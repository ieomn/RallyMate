from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from rallymate_training.cli import build_parser, main as cli_main
from rallymate_training.sam3d_lora import (
    MHR70_ANKLE_INDICES,
    MHR70_RIGHT_WRIST_INDEX,
    LoRALinear,
    MiniSam3dDecoderProxy,
    Sam3dLoraError,
    TennisHardCaseAugmenter,
    count_trainable_parameters,
    extract_lora_state_dict,
    freeze_non_lora_parameters,
    ground_plane_contact_prior_loss,
    inject_lora_adapters,
    load_lora_checkpoint,
    load_sam3d_lora_config,
    merge_lora_weights,
    reprojection_2d_consistency_loss,
    run_sam3d_lora_training,
    save_lora_checkpoint,
    temporal_kinematic_smoothness_loss,
)


class Sam3dLoraTests(unittest.TestCase):
    def test_lora_injection_freezing_gradient_flow_and_exact_merge(self) -> None:
        torch.manual_seed(7)
        model = MiniSam3dDecoderProxy(embed_dim=32, num_joints=70, depth=2)
        base_before = {k: v.detach().clone() for k, v in model.state_dict().items()}

        adapted = inject_lora_adapters(
            model,
            ["decoder.layers.*.self_attn.*", "decoder.layers.*.cross_attn.*", "head_pose.proj.*"],
            rank=4,
            alpha=8.0,
            dropout=0.0,
        )
        self.assertGreater(len(adapted), 0)
        self.assertTrue(any(isinstance(m, LoRALinear) for m in model.modules()))

        counts = freeze_non_lora_parameters(model)
        self.assertGreater(counts["trainable_params"], 0)
        self.assertLess(counts["trainable_params"], counts["total_params"])
        for name, param in model.named_parameters():
            if name.endswith("lora_A") or name.endswith("lora_B"):
                self.assertTrue(param.requires_grad)
            else:
                self.assertFalse(param.requires_grad)

        x = torch.randn(2, 3, 3, 64, 64)
        out = model(x)["joints_3d"]
        loss = (out ** 2).mean()
        loss.backward()

        # Verify gradients flow strictly to LoRA parameters and step updates them while base stays frozen.
        optimizer = torch.optim.SGD([p for p in model.parameters() if p.requires_grad], lr=0.1)
        optimizer.step()

        lora_sd = extract_lora_state_dict(model)
        self.assertEqual(len(lora_sd), len(adapted) * 2)
        self.assertTrue(any(v.abs().max().item() > 0.0 for k, v in lora_sd.items() if k.endswith("lora_B")))

        # Verify base_layer weights remained unchanged before merge.
        for name, mod in model.named_modules():
            if isinstance(mod, LoRALinear):
                self.assertTrue(torch.equal(mod.base_layer.weight.detach(), base_before[f"{name}.weight"]))

        model.eval()
        with torch.no_grad():
            unmerged_out = model(x)["joints_3d"].clone()

        merge_lora_weights(model)
        self.assertFalse(any(isinstance(m, LoRALinear) for m in model.modules()))
        with torch.no_grad():
            merged_out = model(x)["joints_3d"]
        max_diff = float((unmerged_out - merged_out).abs().max().item())
        self.assertLess(max_diff, 1e-5)

    def test_temporal_kinematic_smoothness_loss_is_vfr_aware_and_whip_safe(self) -> None:
        # Constant velocity motion on non-uniform VFR timestamps must yield zero acceleration loss.
        dt_s = torch.tensor([0.02, 0.05, 0.03, 0.07], dtype=torch.float32)
        t = torch.cat([torch.zeros(1), torch.cumsum(dt_s, dim=0)])
        vel = torch.tensor([1.5, -0.8, 0.4], dtype=torch.float32)
        linear_traj = t.view(1, 5, 1, 1) * vel.view(1, 1, 1, 3)
        linear_traj = linear_traj.expand(1, 5, 70, 3).clone().requires_grad_(True)

        zero_loss = temporal_kinematic_smoothness_loss(linear_traj, dt_s)
        self.assertAlmostEqual(float(zero_loss.item()), 0.0, places=6)

        # Alternating jitter produces strong loss and clean gradients.
        jitter = linear_traj.detach().clone()
        jitter[:, 1::2, :, :] += 0.25
        jitter.requires_grad_(True)
        jitter_loss = temporal_kinematic_smoothness_loss(jitter, dt_s)
        self.assertGreater(float(jitter_loss.item()), 1.0)
        jitter_loss.backward()
        self.assertIsNotNone(jitter.grad)
        self.assertTrue(torch.isfinite(jitter.grad).all())

        # Distal wrist whip incurs lower penalty than identical acceleration on a core joint.
        core_spike = torch.zeros(1, 5, 70, 3)
        wrist_spike = torch.zeros(1, 5, 70, 3)
        core_spike[:, 2, 5, 0] = 0.5  # left_shoulder
        wrist_spike[:, 2, MHR70_RIGHT_WRIST_INDEX, 0] = 0.5
        self.assertLess(
            float(temporal_kinematic_smoothness_loss(wrist_spike, dt_s).item()),
            float(temporal_kinematic_smoothness_loss(core_spike, dt_s).item()),
        )

    def test_reprojection_2d_consistency_loss_and_ground_plane_prior(self) -> None:
        joints_3d = torch.tensor(
            [[[0.2, -0.5, 0.0], [0.0, 0.8, 0.1], [-0.3, 0.8, -0.1]]],
            dtype=torch.float32,
            requires_grad=True,
        )
        camera_t = torch.tensor([[0.0, 0.0, 4.0]], dtype=torch.float32)
        focal = torch.tensor([1000.0], dtype=torch.float32)
        j3d_cam = joints_3d.detach() + camera_t.unsqueeze(1)
        target_2d = focal.view(1, 1, 1) * (j3d_cam[..., :2] / j3d_cam[..., 2:3])
        vis = torch.tensor([[1.0, 1.0, 0.0]], dtype=torch.float32)

        # Exact projection matches -> 0 loss.
        loss_exact = reprojection_2d_consistency_loss(joints_3d, camera_t, focal, target_2d, vis)
        self.assertAlmostEqual(float(loss_exact.item()), 0.0, places=6)

        # Corrupting ONLY the invisible joint (vis == 0) must still give 0 loss!
        target_masked = target_2d.clone()
        target_masked[0, 2, :] += 500.0
        loss_masked = reprojection_2d_consistency_loss(joints_3d, camera_t, focal, target_masked, vis)
        self.assertAlmostEqual(float(loss_masked.item()), 0.0, places=6)

        # Corrupting a visible joint gives positive loss + gradients.
        target_shifted = target_2d.clone()
        target_shifted[0, 0, 0] += 15.0
        loss_err = reprojection_2d_consistency_loss(joints_3d, camera_t, focal, target_shifted, vis)
        self.assertGreater(float(loss_err.item()), 0.0)
        loss_err.backward()
        self.assertIsNotNone(joints_3d.grad)

        # Ground plane contact prior: plane y = 1.2 => normal [0, 1, 0], offset -1.2.
        ankles = torch.tensor([[[0.1, 1.2, 4.0], [-0.2, 0.7, 4.1]]], dtype=torch.float32, requires_grad=True)
        contact_mask = torch.tensor([[1.0, 0.0]], dtype=torch.float32)
        normal = torch.tensor([0.0, 1.0, 0.0], dtype=torch.float32)
        # First ankle is planted on plane (y=1.2); second is airborne (mask=0) -> loss == 0.
        self.assertAlmostEqual(
            float(ground_plane_contact_prior_loss(ankles, contact_mask, normal, -1.2).item()),
            0.0,
            places=6,
        )
        # Depth/height drift on planted foot gives positive loss and gradients.
        drifted = torch.tensor([[[0.1, 1.45, 4.0], [-0.2, 0.7, 4.1]]], dtype=torch.float32, requires_grad=True)
        g_loss = ground_plane_contact_prior_loss(drifted, contact_mask, normal, -1.2)
        self.assertGreater(float(g_loss.item()), 0.0)
        g_loss.backward()
        self.assertGreater(float(drifted.grad[0, 0, 1].abs().item()), 0.0)
        self.assertEqual(float(drifted.grad[0, 1].abs().sum().item()), 0.0)

        # Batched ground_offset tensor (B,) across (B, T, K, 3) sequence.
        batched_ankles = torch.zeros(2, 3, 2, 3, dtype=torch.float32)
        batched_ankles[0, :, :, 1] = 1.0
        batched_ankles[1, :, :, 1] = 2.0
        batched_offsets = torch.tensor([-1.0, -2.0], dtype=torch.float32)
        self.assertAlmostEqual(
            float(
                ground_plane_contact_prior_loss(
                    batched_ankles,
                    torch.ones(2, 3, 2, dtype=torch.float32),
                    normal,
                    batched_offsets,
                ).item()
            ),
            0.0,
            places=6,
        )

    def test_tennis_hard_case_augmenter_numpy_and_torch(self) -> None:
        aug = TennisHardCaseAugmenter(
            small_roi_prob=1.0,
            min_roi_height=64,
            max_roi_height=80,
            motion_blur_prob=1.0,
            occlusion_prob=1.0,
            seed=123,
        )
        np_img = np.random.default_rng(1).integers(0, 255, size=(256, 192, 3), dtype=np.uint8)
        out_np = aug(np_img, arm_anchors_xy=[(0.6, 0.4)])
        self.assertIsInstance(out_np, np.ndarray)
        self.assertEqual(out_np.shape, np_img.shape)
        self.assertEqual(out_np.dtype, np_img.dtype)
        self.assertEqual(set(aug.last_applied), {"small_roi_downscale", "swing_motion_blur", "racket_arm_occlusion"})
        self.assertFalse(np.array_equal(out_np, np_img))

        tensor_batch = torch.rand(2, 3, 192, 160, dtype=torch.float32)
        out_t = aug(tensor_batch)
        self.assertIsInstance(out_t, torch.Tensor)
        self.assertEqual(out_t.shape, tensor_batch.shape)
        self.assertEqual(out_t.dtype, tensor_batch.dtype)
        self.assertTrue(torch.isfinite(out_t).all())

    def test_config_smoke_synthetic_training_and_provenance_round_trip(self) -> None:
        cfg = load_sam3d_lora_config("training/configs/sam3d_lora_tennis.yaml")
        self.assertEqual(cfg.rank, 16)
        self.assertEqual(cfg.alpha, 32.0)

        with tempfile.TemporaryDirectory() as tmp:
            result = run_sam3d_lora_training(
                cfg,
                smoke_synthetic=True,
                output_dir=tmp,
                epochs=4,
                merge_weights=True,
            )
            self.assertEqual(result["mode"], "smoke_synthetic")
            self.assertLess(result["merge_max_abs_diff"], 1e-5)
            self.assertLessEqual(result["final_loss"], result["initial_loss"] + 1e-4)

            ckpt_dir = Path(result["checkpoint_dir"])
            fresh_proxy = MiniSam3dDecoderProxy(embed_dim=64, num_joints=70, depth=2)
            loaded = load_lora_checkpoint(
                ckpt_dir,
                model=fresh_proxy,
                verify_provenance=True,
                expected_base_checkpoint_sha256=result["base_checkpoint_sha256"],
                expected_config_sha256=result["config_sha256"],
                expected_dataset_digest=result["dataset_digest"],
            )
            self.assertEqual(loaded["manifest"]["rank"], cfg.rank)
            self.assertEqual(loaded["manifest"]["alpha"], cfg.alpha)
            self.assertGreater(len(loaded["loaded_keys"]), 0)

            # Tampering weights file fails closed on SHA-256 verification.
            (ckpt_dir / "lora_weights.pt").write_bytes(b"corrupted")
            with self.assertRaises(Sam3dLoraError):
                load_lora_checkpoint(ckpt_dir, verify_provenance=True)

            # Direct save/load with wrong expected base SHA also fails closed.
            m = MiniSam3dDecoderProxy(embed_dim=32, num_joints=70, depth=1)
            inject_lora_adapters(m, ["head_pose.proj.*"], rank=4, alpha=8.0)
            save_lora_checkpoint(
                m,
                Path(tmp) / "direct_ckpt",
                base_checkpoint_sha256="a" * 64,
                config_sha256="b" * 64,
                dataset_digest="c" * 64,
            )
            with self.assertRaises(Sam3dLoraError):
                load_lora_checkpoint(
                    Path(tmp) / "direct_ckpt",
                    verify_provenance=True,
                    expected_base_checkpoint_sha256="d" * 64,
                )

    def test_cli_parser_preserves_existing_commands_and_runs_sam3d_lora_smoke(self) -> None:
        parser = build_parser()
        for argv in (
            ["prepare", "--manifest", "m.csv", "--output", "out", "--task", "detect", "--classes", "a,b"],
            ["train", "--config", "training/configs/detect.yaml"],
            ["evaluate", "--config", "training/configs/detect.yaml", "--weights", "best.pt"],
            ["export", "--config", "training/configs/detect.yaml", "--weights", "best.pt", "--format", "onnx"],
            ["register", "--config", "training/configs/detect.yaml", "--artifact", "best.pt", "--metrics", "m.json", "--version", "1.0.0"],
            ["sam3d-lora", "--smoke-synthetic", "--epochs", "2"],
        ):
            parsed = parser.parse_args(argv)
            self.assertEqual(parsed.command, argv[0])

        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                cli_main(
                    [
                        "sam3d-lora",
                        "--config",
                        "training/configs/sam3d_lora_tennis.yaml",
                        "--smoke-synthetic",
                        "--epochs",
                        "2",
                        "--device",
                        "cpu",
                        "--output-dir",
                        tmp,
                    ]
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["result"]["mode"], "smoke_synthetic")
            self.assertLess(payload["result"]["merge_max_abs_diff"], 1e-5)


if __name__ == "__main__":
    unittest.main()

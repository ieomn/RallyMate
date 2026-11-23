from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Sequence

import yaml

from rallymate_training.config import TrainingConfigError, load_train_config
from rallymate_training.dataset import DatasetError, prepare_dataset
from rallymate_training.engine import evaluate, export_model, train
from rallymate_training.registry import RegistryError, register_model
from rallymate_training.sam3d_lora import (
    Sam3dLoraError,
    load_sam3d_lora_config,
    run_sam3d_lora_training,
)
from rallymate_training.tennis_ball_racket import (
    TennisBallRacketError,
    bootstrap_pseudo_labels_from_frames,
    load_tennis_ball_racket_config,
    run_tennis_ball_racket_training,
)


def _class_names(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise argparse.ArgumentTypeError("at least one class name is required")
    return names


def _fingerprint_dataset(path: Path) -> str:
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw) or {}
        referenced = [
            Path(value)
            for key, value in data.items()
            if key in {"train", "val", "test"} and isinstance(value, str)
        ]
    except yaml.YAMLError:
        referenced = []
    digest = hashlib.sha256(raw)
    for file_path in sorted(referenced):
        if file_path.exists():
            digest.update(file_path.read_bytes())
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RallyMate training lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="audit and split a governed manifest")
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--task", choices=("detect", "pose"), required=True)
    prepare.add_argument("--classes", type=_class_names, required=True)
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument("--keypoints", type=int, default=17)
    prepare.add_argument("--keypoint-dims", type=int, choices=(2, 3), default=3)

    train_parser = sub.add_parser("train", help="fine-tune from a YAML config")
    train_parser.add_argument("--config", required=True)

    evaluate_parser = sub.add_parser("evaluate", help="evaluate on val/test split")
    evaluate_parser.add_argument("--config", required=True)
    evaluate_parser.add_argument("--weights", required=True)
    evaluate_parser.add_argument("--split", choices=("val", "test"), default="test")

    export_parser = sub.add_parser("export", help="export ONNX or TensorRT")
    export_parser.add_argument("--config", required=True)
    export_parser.add_argument("--weights", required=True)
    export_parser.add_argument("--format", choices=("onnx", "engine"), required=True)

    register_parser = sub.add_parser(
        "register", help="create an immutable model version"
    )
    register_parser.add_argument("--config", required=True)
    register_parser.add_argument("--artifact", required=True)
    register_parser.add_argument("--metrics", required=True)
    register_parser.add_argument("--version", required=True)
    register_parser.add_argument("--promote", action="store_true")

    sam3d_lora_parser = sub.add_parser(
        "sam3d-lora", help="fine-tune SAM 3D Body with tennis-domain LoRA adapters"
    )
    sam3d_lora_parser.add_argument(
        "--config", default="training/configs/sam3d_lora_tennis.yaml"
    )
    sam3d_lora_parser.add_argument(
        "--smoke-synthetic",
        action="store_true",
        help="run deterministic LoRA training/merge verification on a lightweight proxy",
    )
    sam3d_lora_parser.add_argument("--output-dir", default=None)
    sam3d_lora_parser.add_argument("--epochs", type=int, default=None)
    sam3d_lora_parser.add_argument("--rank", type=int, default=None)
    sam3d_lora_parser.add_argument("--alpha", type=float, default=None)
    sam3d_lora_parser.add_argument("--device", default=None)
    sam3d_lora_parser.add_argument("--seed", type=int, default=None)
    sam3d_lora_parser.add_argument("--no-merge", action="store_true")

    ball_racket_parser = sub.add_parser(
        "tennis-ball-racket",
        help="fine-tune 3-class ball+racket detector or bootstrap pseudo-labels from frames.jsonl",
    )
    ball_racket_parser.add_argument(
        "--config", default="training/configs/tennis_ball_racket.yaml"
    )
    ball_racket_parser.add_argument("--bootstrap-frames", default=None)
    ball_racket_parser.add_argument("--output-dir", default=None)
    ball_racket_parser.add_argument("--validate-manifest", default=None)
    ball_racket_parser.add_argument("--dry-run", action="store_true")
    ball_racket_parser.add_argument("--min-ball-confidence", type=float, default=0.35)
    ball_racket_parser.add_argument("--min-racket-confidence", type=float, default=0.35)
    ball_racket_parser.add_argument("--min-track-length", type=int, default=3)
    ball_racket_parser.add_argument("--max-coast-frames", type=int, default=4)
    ball_racket_parser.add_argument("--device", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_dataset(
                args.manifest,
                args.output,
                args.task,
                args.classes,
                seed=args.seed,
                keypoint_count=args.keypoints,
                keypoint_dims=args.keypoint_dims,
            )
        elif args.command == "sam3d-lora":
            lora_config = load_sam3d_lora_config(args.config)
            overrides: dict = {}
            if args.rank is not None:
                overrides["rank"] = args.rank
            if args.alpha is not None:
                overrides["alpha"] = args.alpha
            if args.device is not None:
                overrides["device"] = args.device
            if args.seed is not None:
                overrides["seed"] = args.seed
            if overrides:
                lora_config = dataclasses.replace(lora_config, **overrides)
            result = run_sam3d_lora_training(
                lora_config,
                smoke_synthetic=args.smoke_synthetic,
                output_dir=args.output_dir,
                epochs=args.epochs,
                merge_weights=False if args.no_merge else None,
            )
        elif args.command == "tennis-ball-racket":
            if args.bootstrap_frames is not None:
                out_dir = (
                    args.output_dir
                    or str(load_tennis_ball_racket_config(args.config).output_dir / "bootstrap")
                )
                result = bootstrap_pseudo_labels_from_frames(
                    args.bootstrap_frames,
                    out_dir,
                    min_ball_confidence=args.min_ball_confidence,
                    min_racket_confidence=args.min_racket_confidence,
                    min_track_length=args.min_track_length,
                    max_coast_frames=args.max_coast_frames,
                )
            else:
                tbr_config = load_tennis_ball_racket_config(args.config)
                tbr_overrides: dict = {}
                if args.output_dir is not None:
                    tbr_overrides["output_dir"] = Path(args.output_dir).resolve()
                if args.device is not None:
                    tbr_overrides["device"] = args.device
                if tbr_overrides:
                    tbr_config = dataclasses.replace(tbr_config, **tbr_overrides)
                result = run_tennis_ball_racket_training(
                    tbr_config,
                    dry_run=args.dry_run,
                    validate_manifest_path=args.validate_manifest,
                )
        else:
            config = load_train_config(args.config)
            if args.command == "train":
                result = {"best_weights": str(train(config))}
            elif args.command == "evaluate":
                result = evaluate(config, args.weights, split=args.split)
            elif args.command == "export":
                result = {
                    "artifact": str(
                        export_model(
                            args.weights,
                            args.format,
                            config.image_size,
                            config.device,
                        )
                    )
                }
            else:
                metrics = json.loads(
                    Path(args.metrics).read_text(encoding="utf-8")
                )
                result = register_model(
                    args.artifact,
                    args.version,
                    config,
                    metrics,
                    _fingerprint_dataset(config.data),
                    promote=args.promote,
                )
    except (
        DatasetError,
        TrainingConfigError,
        RegistryError,
        Sam3dLoraError,
        TennisBallRacketError,
        FileNotFoundError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
    print(json.dumps({"status": "completed", "result": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

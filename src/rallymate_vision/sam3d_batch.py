"""Bounded multi-image BODY inference for the pinned official SAM model.

Each request is one source image and one tracked person's box. Official crop,
camera, affine transform and model code remain in use. We construct B=N, P=1
and perform one model call, then extract every official output in request order.
No optional numerical/model dependencies are imported at module import time.
"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import threading


MAX_SUBJECTS = 8
SOURCE_HASHES = {
    "estimator": "9690d55273db4a8e56660407a2366cc398b09b98a7f9d4fcf7772d13497c04d8",
    "prepare_batch": "993d62352ff67d42b2c6d6a707a45818992422cfe863e584702a2deb7fe09257",
    "model": "d123798e9519fe1ea9cfbfcc07982d33ca9f4d9dfe796d10f234cc314f3e3de9",
    "base_model": "ebc9174c5fc7ca9286cbe9d04d103ce5868bb40e414b172295f4f2e5b5d61b68",
    "recursive_to": "f991fb070b1f5d653e5fec8d048f20ccaa37e2cda1a43deea8b83dd3c0499b77",
}
# Preserve the complete BODY output selection of process_one_image. In
# particular, vertices and joint rotations must not be omitted for speed.
OUTPUT_FIELDS = {
    "focal_length": "focal_length", "pred_keypoints_3d": "pred_keypoints_3d",
    "pred_keypoints_2d": "pred_keypoints_2d", "pred_vertices": "pred_vertices",
    "pred_cam_t": "pred_cam_t", "pred_pose_raw": "pred_pose_raw",
    "global_rot": "global_rot", "body_pose_params": "body_pose",
    "hand_pose_params": "hand", "scale_params": "scale", "shape_params": "shape",
    "expr_params": "face", "pred_joint_coords": "pred_joint_coords",
    "pred_global_rots": "joint_global_rots", "mhr_model_params": "mhr_model_params",
}


def _source_hash(target):
    return hashlib.sha256(Path(inspect.getsourcefile(target)).read_bytes()).hexdigest()


def _verified_runtime(estimator):
    import torch

    module = inspect.getmodule(type(estimator))
    if module is None:
        raise ValueError("SAM estimator module is unavailable")
    prepare = getattr(module, "prepare_batch", None)
    function = inspect.unwrap(estimator.process_one_image)
    recursive = function.__globals__.get("recursive_to")
    # The installed, instance-local D2H converter must also be honored by the
    # batch path. Its original utility is still checked against pinned source.
    recursive_source = recursive
    if (type(recursive).__module__ == "rallymate_vision.sam3d_output_transfer"
            and type(recursive).__name__ == "_BatchedCPUTransfer"):
        recursive_source = recursive.original
    targets = {"estimator": type(estimator), "prepare_batch": prepare,
               "model": estimator.model.run_inference,
               "base_model": estimator.model._initialize_batch,
               "recursive_to": recursive_source}
    for label, target in targets.items():
        if target is None or _source_hash(target) != SOURCE_HASHES[label]:
            raise ValueError(f"SAM batch requires pinned official {label} source")
    if estimator.model.training:
        raise ValueError("SAM batch requires an evaluation model")
    if torch.device(estimator.device).type != "cuda":
        raise ValueError("SAM body batching requires CUDA")
    if estimator.fov_estimator is not None:
        raise ValueError("SAM body batching currently requires the default per-image FOV")
    return torch, prepare, recursive


def _merge_prepared_batches(batches, torch):
    """Merge official single-image/single-person batches along image axis."""
    if not batches:
        raise ValueError("SAM batch needs at least one prepared subject")
    keys = set(batches[0])
    if any(set(batch) != keys for batch in batches):
        raise ValueError("Prepared SAM subjects have different fields")
    for batch in batches:
        if (batch["img"].ndim != 5 or tuple(batch["img"].shape[:2]) != (1, 1)
                or tuple(batch["cam_int"].shape) != (1, 3, 3)
                or tuple(batch["person_valid"].shape) != (1, 1)):
            raise ValueError("Prepared SAM subjects must have B=1,P=1 and their own camera")
    merged = {}
    for key in batches[0]:
        values = [batch[key] for batch in batches]
        if all(isinstance(value, torch.Tensor) for value in values):
            first = values[0]
            if any(value.ndim < 1 or value.shape[0] != 1 or
                   value.shape[1:] != first.shape[1:] or value.dtype != first.dtype or
                   value.device != first.device for value in values):
                raise ValueError(f"Prepared SAM tensor {key} has incompatible subject axes")
            merged[key] = torch.cat(values, dim=0)
        elif all(isinstance(value, list) and len(value) == 1 for value in values):
            # img_ori remains a list of official NoCollate objects. bbox_format
            # is also a list. Neither is broadcast from the first source image.
            merged[key] = [value[0] for value in values]
        else:
            raise ValueError(f"Unsupported prepared SAM field: {key}")
    return merged


class BodyBatchInference:
    """Serial owner of one estimator; singleton graph handles may eager-fallback.

    Graph wrappers preserve their singleton captures. Regions with configured
    batch signatures may capture those independently; remaining regions execute
    all N subjects eagerly. A later singleton can use its original graph again.
    The owning engine must serialize this executor and its ordinary infer path.
    """

    def __init__(self, estimator, *, batch_preprocess=False):
        self.torch, self.prepare_batch, self.recursive_to = _verified_runtime(estimator)
        self.estimator = estimator
        self.lock = threading.Lock()
        self.preprocessor = None
        if batch_preprocess:
            from rallymate_vision.sam3d_batch_preprocess import BatchCropPreprocessor
            self.preprocessor = BatchCropPreprocessor(estimator)
        self.receipt = {"candidate": "official_body_multi_image_batch_v1",
                        "layout": "B=subjects,P=1", "maximum_subjects": MAX_SUBJECTS,
                        "forward_calls": 0, "subjects": 0, "last_batch_subjects": 0,
                        "source_hashes": dict(SOURCE_HASHES),
                        "full_official_body_outputs_retained": True,
                        "per_source_camera_and_affine_preserved": True,
                        "parameters_precision_resolution_unchanged": True,
                        "graph_policy": "configured_batch_graphs_or_eager; singleton_capture_preserved"}
        if self.preprocessor is not None:
            self.receipt["batch_preprocess"] = self.preprocessor.receipt

    def infer(self, requests, *, clear_cuda_cache: bool = True):
        """Return official output dictionaries for [(uint8 BGR image, xyxy)].

        A failed call raises; the engine decides how to report/retry that batch.
        No request is dropped, padded, resampled, or silently run as a loop of
        forwards. No hand/full/mask/FOV-estimator mode is accepted by this API.
        """
        import numpy as np

        if not isinstance(requests, (list, tuple)) or not 1 <= len(requests) <= MAX_SUBJECTS:
            raise ValueError("SAM body batch requires between 1 and 8 subject requests")
        checked = []
        for request in requests:
            if not isinstance(request, (list, tuple)) or len(request) != 2:
                raise ValueError("SAM body request must contain one source image and one bbox")
            frame, box = request
            if (not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3
                    or frame.dtype != np.uint8 or min(frame.shape[:2]) < 1):
                raise ValueError("SAM body batch requires nonempty uint8 source BGR pixels")
            box = np.asarray(box, dtype=np.float32)
            height, width = frame.shape[:2]
            if (box.shape != (4,) or not np.isfinite(box).all() or not
                    (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
                raise ValueError("SAM body batch requires a finite bbox inside its source image")
            checked.append((frame, box))
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Concurrent SAM body batch inference is unsupported")
        try:
            estimator, torch = self.estimator, self.torch
            if estimator.model.training or estimator.fov_estimator is not None:
                raise ValueError("SAM body batch runtime contract changed")
            with torch.no_grad():
                # Same ephemeral cache reset as the official estimator, once
                # per true batch rather than once per requested person.
                estimator.batch = estimator.image_embeddings = estimator.output = None
                estimator.prev_prompt = []
                estimator.is_crop = True
                if clear_cuda_cache:
                    torch.cuda.empty_cache()
                if self.preprocessor is not None:
                    first_rgb, batch = self.preprocessor.prepare(checked, self.prepare_batch, _merge_prepared_batches)
                else:
                    prepared = []
                    first_rgb = None
                    for frame, box in checked:
                        rgb = np.ascontiguousarray(frame[:, :, ::-1])
                        if first_rgb is None:
                            first_rgb = rgb
                        prepared.append(self.prepare_batch(rgb, estimator.transform, box.reshape(1, 4), None, None))
                    batch = _merge_prepared_batches(prepared, torch)
                    del prepared
                batch = self.recursive_to(batch, str(estimator.device))
                estimator.model._initialize_batch(batch)
                # In the pinned BODY branch only img.shape is inspected before
                # early return; every model input/camera lives in the batch.
                output = estimator.model.run_inference(
                    first_rgb, batch, inference_type="body", transform_hand=estimator.transform_hand,
                    thresh_wrist_angle=estimator.thresh_wrist_angle,
                )
                self.receipt["forward_calls"] += 1
                self.receipt["subjects"] += len(checked)
                self.receipt["last_batch_subjects"] = len(checked)
                native = self.recursive_to(output["mhr"], "cpu")
                native = self.recursive_to(native, "numpy")
                # Fail before publishing ANY slot if the result count is wrong.
                for field in OUTPUT_FIELDS.values():
                    value = native[field]
                    if not isinstance(value, np.ndarray) or value.ndim < 1 or value.shape[0] != len(checked):
                        raise ValueError(f"SAM batch output {field} does not match requested subject count")
                boxes = batch["bbox"].detach().cpu().numpy()
                result = []
                for index in range(len(checked)):
                    row = {target: np.array(native[source][index], copy=True)
                           for target, source in OUTPUT_FIELDS.items()}
                    row.update(bbox=np.array(boxes[index, 0], copy=True), mask=None)
                    result.append(row)
                return result
        finally:
            self.lock.release()

    def close(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Cannot close SAM batch staging during inference")
        try:
            if self.preprocessor is not None:
                self.preprocessor.close()
        finally:
            self.lock.release()


def infer_body_batch(estimator, requests, **kwargs):
    """Convenience entry point; reuse BodyBatchInference for repeated calls."""
    return BodyBatchInference(estimator).infer(requests, **kwargs)

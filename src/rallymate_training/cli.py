from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from rallymate_training.config import TrainingConfigError, load_train_config
from rallymate_training.dataset import DatasetError, prepare_dataset
from rallymate_training.engine import evaluate, export_model, train
from rallymate_training.registry import RegistryError, register_model


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
    return parser


def main() -> None:
    args = build_parser().parse_args()
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

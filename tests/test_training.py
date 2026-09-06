from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_training.config import (
    QualityGates,
    TrainConfig,
    TrainingConfigError,
    load_train_config,
)
from rallymate_training.dataset import DatasetError, prepare_dataset
from rallymate_training.registry import RegistryError, register_model


class TrainingConfigTests(unittest.TestCase):
    def test_detect_config_loads_and_resolves_paths(self) -> None:
        config = load_train_config("training/configs/detect.yaml")
        self.assertEqual(config.task, "detect")
        self.assertTrue(config.base_model.is_absolute())
        self.assertEqual(config.quality_gates.precision, 0.80)

    def test_invalid_quality_gate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text(
                "\n".join(
                    (
                        "schema_version: 1",
                        "name: test",
                        "task: detect",
                        "base_model: model.pt",
                        "data: data.yaml",
                        "output_dir: runs",
                        "registry_dir: registry",
                        "quality_gates:",
                        "  recall: 1.2",
                    )
                ),
                encoding="utf-8",
            )
            with self.assertRaises(TrainingConfigError):
                load_train_config(path)


class DatasetPreparationTests(unittest.TestCase):
    def _make_manifest(self, root: Path, count: int = 60) -> Path:
        manifest = root / "manifest.csv"
        fields = [
            "image_path",
            "label_path",
            "session_id",
            "subject_id",
            "camera_id",
            "consent",
            "license",
            "split",
        ]
        rows = []
        for index in range(count):
            session = f"session-{index:03d}"
            image = root / "dataset" / "images" / session / "frame.jpg"
            label = root / "dataset" / "labels" / session / "frame.txt"
            image.parent.mkdir(parents=True, exist_ok=True)
            label.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b"not-needed-by-label-audit")
            label.write_text("0 0.5 0.5 0.2 0.3\n", encoding="utf-8")
            rows.append(
                {
                    "image_path": str(image),
                    "label_path": str(label),
                    "session_id": session,
                    "subject_id": f"subject-{index:03d}",
                    "camera_id": "fixed-01",
                    "consent": "yes",
                    "license": "owned",
                    "split": "",
                }
            )
        with manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return manifest

    def test_prepare_writes_split_lists_without_session_or_subject_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = prepare_dataset(
                self._make_manifest(root),
                root / "generated",
                "detect",
                ["player", "ball", "racket"],
            )
            self.assertEqual(result["leakage_check"], "session_and_subject_passed")
            self.assertEqual(sum(result["sample_counts"].values()), 60)
            self.assertGreater(min(result["sample_counts"].values()), 0)
            self.assertTrue(Path(result["dataset_yaml"]).exists())

    def test_non_normalized_label_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._make_manifest(root)
            bad_label = next((root / "dataset" / "labels").rglob("*.txt"))
            bad_label.write_text("0 500 0.5 0.2 0.3\n", encoding="utf-8")
            with self.assertRaises(DatasetError):
                prepare_dataset(
                    manifest,
                    root / "generated",
                    "detect",
                    ["player", "ball", "racket"],
                )


class RegistryTests(unittest.TestCase):
    def _config(self, root: Path) -> TrainConfig:
        return TrainConfig(
            name="test",
            task="detect",
            base_model=root / "base.pt",
            data=root / "dataset.yaml",
            output_dir=root / "runs",
            registry_dir=root / "registry",
            model_name="rallymate-detect",
            quality_gates=QualityGates(
                map50_95=0.4, precision=0.8, recall=0.7
            ),
        )

    def test_promotion_requires_gates_and_version_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "best.pt"
            artifact.write_bytes(b"weights")
            config = self._config(root)
            with self.assertRaises(RegistryError):
                register_model(
                    artifact,
                    "1.0.0",
                    config,
                    {"map50_95": 0.1, "precision": 0.9, "recall": 0.9},
                    "dataset-sha",
                    promote=True,
                )
            manifest = register_model(
                artifact,
                "1.0.0",
                config,
                {"map50_95": 0.5, "precision": 0.9, "recall": 0.8},
                "dataset-sha",
                promote=True,
            )
            self.assertEqual(manifest["deployment_status"], "production")
            alias = (
                root / "registry" / "aliases" / "rallymate-detect-production.json"
            )
            self.assertEqual(json.loads(alias.read_text())["version"], "1.0.0")
            with self.assertRaises(RegistryError):
                register_model(
                    artifact,
                    "1.0.0",
                    config,
                    manifest["metrics"],
                    "dataset-sha",
                )


if __name__ == "__main__":
    unittest.main()

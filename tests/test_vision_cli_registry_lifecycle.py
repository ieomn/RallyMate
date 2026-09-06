from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rallymate_scoring.registry_lifecycle import DEFAULT_REGISTRY_LIFECYCLE_PATH
from rallymate_vision import cli


class VisionCliRegistryLifecycleTests(unittest.TestCase):
    def _request(self) -> SimpleNamespace:
        return SimpleNamespace(
            job_id="job",
            output_dir=Path("out"),
            processing=SimpleNamespace(
                max_frames=None,
                write_annotated_video=True,
            ),
            models=SimpleNamespace(device="auto"),
        )

    def _summary(self) -> dict:
        return {
            "status": "completed",
            "job_id": "job",
            "processing": {"processed_frames": 1, "elapsed_seconds": 0.1},
            "next_stage": {"ready": True},
        }

    def test_default_operator_manifest_is_forwarded_to_pipeline(self) -> None:
        request = self._request()
        with (
            patch("sys.argv", ["rallymate-vision", "--request", "request.json"]),
            patch.object(cli, "load_request", return_value=request),
            patch.object(cli, "run_pipeline", return_value=self._summary()) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.main()
        self.assertEqual(
            run.call_args.kwargs["registry_lifecycle_manifest_path"],
            DEFAULT_REGISTRY_LIFECYCLE_PATH,
        )

    def test_explicit_operator_manifest_overrides_the_default(self) -> None:
        request = self._request()
        manifest = Path("operator") / "registry-lifecycle.json"
        with (
            patch(
                "sys.argv",
                [
                    "rallymate-vision",
                    "--request",
                    "request.json",
                    "--registry-lifecycle-manifest",
                    str(manifest),
                ],
            ),
            patch.object(cli, "load_request", return_value=request),
            patch.object(cli, "run_pipeline", return_value=self._summary()) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.main()
        self.assertEqual(
            run.call_args.kwargs["registry_lifecycle_manifest_path"],
            manifest,
        )


if __name__ == "__main__":
    unittest.main()

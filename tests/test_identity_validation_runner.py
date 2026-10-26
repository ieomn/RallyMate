"""Safety checks execute before loading any detector or GPU model."""
import tempfile
from pathlib import Path
import unittest

from scripts.validate_tennis_identity_pipeline import main


class IdentityValidationRunnerTests(unittest.TestCase):
    def arguments(self, root):
        request = root / "request.json"
        request.write_text("{}", encoding="utf-8")
        runtime = root / "sam"
        runtime.mkdir()
        python = root / "python.exe"
        python.write_bytes(b"not executed")
        return ["--request", str(request), "--output-root", str(root / "output"),
                "--run-id", "identity-test", "--sam-python", str(python), "--sam-runtime", str(runtime)]

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.arguments(root)
            output = root / "output"
            output.mkdir()
            marker = output / "native.jsonl"
            marker.write_bytes(b"original evidence")
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                main(args)
            self.assertEqual(marker.read_bytes(), b"original evidence")

    def test_colliding_request_names_fail_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.arguments(root)
            other = root / "other"
            other.mkdir()
            (other / "request.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stems must be unique"):
                main(args + ["--request", str(other / "request.json")])
            self.assertFalse((root / "output").exists())

    def test_run_id_cannot_be_a_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.arguments(root)
            args[args.index("--run-id") + 1] = "../other"
            with self.assertRaisesRegex(ValueError, "run-id must contain"):
                main(args)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()

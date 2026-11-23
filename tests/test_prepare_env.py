import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("prepare_deploy_env", Path(__file__).resolve().parents[1] / "deploy/prepare_env.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrepareEnvTests(unittest.TestCase):
    """Plain unittest so it runs under both pytest and `python -m unittest`."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name)

    def test_generated_credentials_match_and_repeat_refuses_overwrite(self):
        paths = module.prepare(self.tmp_path, "rallymate.example.com", "admin@example.com")
        values = dict(line.split("=", 1) for line in paths[0].read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
        self.assertEqual(values["DATABASE_URL"], f"postgresql+psycopg://rallymate:{values['POSTGRES_PASSWORD']}@postgres:5432/rallymate")
        for key in ("JWT_SECRET", "RALLYMATE_API_KEY", "POSTGRES_PASSWORD", "ADMIN_PASSWORD"):
            self.assertTrue(len(values[key]) >= 32 and len(set(values[key])) >= 12, key)
        self.assertTrue(any(c.isdigit() for c in values["ADMIN_PASSWORD"]))
        self.assertTrue(any(c.isalpha() for c in values["ADMIN_PASSWORD"]))
        self.assertEqual(len(set(path.read_text() for path in paths[1:])), 2)
        before = {path: path.read_bytes() for path in paths}
        with self.assertRaisesRegex(ValueError, "no files were overwritten"):
            module.prepare(self.tmp_path, "changed.example.com", "new@example.com")
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_environment_input_injection_refused_without_files(self):
        for host, email in [("https://app.example.com", "a@example.com"),
                            ("app.example.com\nJWT_SECRET=injected", "a@example.com"),
                            ("app.example.com", "a@example.com\nPASSWORD=oops"),
                            ("app.example.com", "a&evil@example.com")]:
            with self.subTest(host=host, email=email):
                with self.assertRaises(ValueError):
                    module.prepare(self.tmp_path, host, email)
                self.assertEqual(list(self.tmp_path.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

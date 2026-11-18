import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("prepare_deploy_env", Path(__file__).resolve().parents[1] / "deploy/prepare_env.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_generated_credentials_match_and_repeat_refuses_overwrite(tmp_path):
    paths = module.prepare(tmp_path, "rallymate.example.com", "admin@example.com")
    values = dict(line.split("=", 1) for line in paths[0].read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
    assert values["DATABASE_URL"] == f"postgresql+psycopg://rallymate:{values['POSTGRES_PASSWORD']}@postgres:5432/rallymate"
    for key in ("JWT_SECRET", "RALLYMATE_API_KEY", "POSTGRES_PASSWORD", "ADMIN_PASSWORD"):
        assert len(values[key]) >= 32 and len(set(values[key])) >= 12
    assert any(c.isdigit() for c in values["ADMIN_PASSWORD"])
    assert any(c.isalpha() for c in values["ADMIN_PASSWORD"])
    assert len(set(path.read_text() for path in paths[1:])) == 2
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError, match="no files were overwritten"):
        module.prepare(tmp_path, "changed.example.com", "new@example.com")
    assert before == {path: path.read_bytes() for path in paths}


@pytest.mark.parametrize("host,email", [("https://app.example.com", "a@example.com"),
    ("app.example.com\nJWT_SECRET=injected", "a@example.com"), ("app.example.com", "a@example.com\nPASSWORD=oops"),
    ("app.example.com", "a&evil@example.com")])
def test_environment_input_injection_refused_without_files(tmp_path, host, email):
    with pytest.raises(ValueError):
        module.prepare(tmp_path, host, email)
    assert list(tmp_path.iterdir()) == []

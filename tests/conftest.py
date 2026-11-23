"""Isolate pytest collection from deployment credentials and service storage."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESEARCH_DIR = _REPO_ROOT / "research"
if _RESEARCH_DIR.is_dir() and str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))


_IMPORT_ROOT = tempfile.TemporaryDirectory(prefix="rallymate-pytest-import-")
_ISOLATED_ENVIRONMENT = {
    "JWT_SECRET": "test-only-session-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "DATABASE_URL": "", "RALLYMATE_DATABASE_URL": "",
    "ADMIN_EMAIL": "", "ADMIN_PASSWORD": "", "RALLYMATE_API_KEY": "",
    "SENTRY_DSN": "", "METRICS_TOKEN": "", "METRICS_TOKEN_FILE": "",
    "RALLYMATE_WORKER_METRICS_PORT": "0",
    "AUTH_COOKIE_SECURE": "false", "PUBLIC_ORIGIN": "http://testserver",
    "RALLYMATE_ENVIRONMENT": "development",
    "RALLYMATE_DATA_ROOT": _IMPORT_ROOT.name,
    "RALLYMATE_DATABASE_PATH": str(Path(_IMPORT_ROOT.name) / "jobs.sqlite3"),
}
_PREVIOUS_ENVIRONMENT = {name: os.environ.get(name) for name in _ISOLATED_ENVIRONMENT}
os.environ.update(_ISOLATED_ENVIRONMENT)


def pytest_unconfigure(config):
    for name, value in _PREVIOUS_ENVIRONMENT.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _IMPORT_ROOT.cleanup()

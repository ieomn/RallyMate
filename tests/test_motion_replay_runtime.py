from pathlib import Path
import os

import pytest
from rallymate_service.database import JobDatabase
from rallymate_service.local_secrets import load_minimax_key, save_minimax_key
from rallymate_service.motion_replay_runtime import CompletedMotionSourceScanner
from rallymate_service.motion_replay_api import MotionReplayInputError


def completed_job(database, job_id, completed_at):
    database.create_job(job_id, "example.mp4", Path("source.mp4"), Path("request.json"), Path("run"))
    database.claim_next("test-worker", 60, 3)
    database.mark_succeeded(job_id, {})
    with database.connect() as connection:
        connection.execute("UPDATE jobs SET completed_at=? WHERE id=?", (completed_at, job_id))
        connection.commit()


def test_scheduler_ignores_history_and_recovers_completions_after_restart(tmp_path):
    jobs = JobDatabase(tmp_path / "jobs.sqlite3")
    jobs.initialize()
    completed_job(jobs, "old", "2026-01-01T00:00:00+00:00")
    called = []
    scanner = CompletedMotionSourceScanner(tmp_path / "scheduler.sqlite3", jobs, called.append)
    scanner.initialize(activated_at="2026-01-02T00:00:00+00:00")
    completed_job(jobs, "first", "2026-01-02T00:00:01+00:00")
    assert scanner.scan_once() == 1
    assert scanner.scan_once() == 0
    completed_job(jobs, "offline", "2026-01-02T00:00:02+00:00")
    restarted = CompletedMotionSourceScanner(tmp_path / "scheduler.sqlite3", jobs, called.append)
    restarted.initialize(activated_at="2026-01-03T00:00:00+00:00")
    assert restarted.scan_once() == 1
    assert called == ["first", "offline"]


def test_scheduler_does_not_skip_transient_enqueue_failure(tmp_path):
    jobs = JobDatabase(tmp_path / "jobs.sqlite3")
    jobs.initialize()
    attempts = []

    def enqueue(job_id):
        attempts.append(job_id)
        if len(attempts) == 1:
            raise OSError("temporarily unavailable")

    scanner = CompletedMotionSourceScanner(tmp_path / "scheduler.sqlite3", jobs, enqueue)
    scanner.initialize(activated_at="2026-01-01T00:00:00+00:00")
    completed_job(jobs, "one", "2026-01-02T00:00:00+00:00")
    with pytest.raises(OSError):
        scanner.scan_once()
    assert scanner.scan_once() == 1
    assert attempts == ["one", "one"]


def test_scheduler_skips_explicit_ineligible_source_without_blocking_next(tmp_path):
    jobs = JobDatabase(tmp_path / "jobs.sqlite3")
    jobs.initialize()
    received = []

    def enqueue(job_id):
        if job_id == "a":
            raise MotionReplayInputError("Insufficient observed motion", 409)
        received.append(job_id)

    scanner = CompletedMotionSourceScanner(tmp_path / "scheduler.sqlite3", jobs, enqueue)
    scanner.initialize(activated_at="2026-01-01T00:00:00+00:00")
    completed_job(jobs, "a", "2026-01-02T00:00:00+00:00")
    completed_job(jobs, "b", "2026-01-02T00:00:00+00:00")
    assert scanner.scan_once() == 2
    assert received == ["b"]


def test_environment_credential_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", " placeholder-test-credential ")
    assert load_minimax_key(tmp_path) == "placeholder-test-credential"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI credential storage")
def test_local_credential_is_encrypted_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    path = save_minimax_key(tmp_path, "placeholder-test-credential")
    assert b"placeholder-test-credential" not in path.read_bytes()
    assert load_minimax_key(tmp_path) == "placeholder-test-credential"

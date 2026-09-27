"""No-network verification of the local SSH heartbeat and event deduplication."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("rallymate_remote_check", Path(__file__).with_name("check_remote.py"))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def healthy():
    return checker.compact_snapshot({"schema_version": "rallymate-health/1", "overall_status": "healthy", "generated_at": "2026-09-24T12:00:00+00:00", "snapshot_age_seconds": 3, "stale": False, "public_url": "https://current-route.trycloudflare.com", "alerts": [], "recent_events": [], "http": {"api_ready": {"ok": True, "http_status": 200}}, "jobs": {"counts": {"succeeded": 4}}})


class RemoteCheckTests(unittest.TestCase):
    code = {"ok": True, "hashes": {"src/example.py": "a" * 64}, "skipped_files": 0}
    now = "2026-09-24T12:00:00+00:00"

    def test_initial_healthy_baseline_is_quiet_and_unchanged_poll_stays_quiet(self):
        first, state = checker.plan_result({}, healthy(), None, self.code, self.now)
        self.assertFalse(first["notify"])
        self.assertTrue(first["code_changes"]["baseline_created"])
        second, _ = checker.plan_result(state, healthy(), None, self.code, self.now)
        self.assertFalse(second["notify"])

    def test_offline_deduplication_preserves_last_good_state_and_announces_recovery(self):
        _, state = checker.plan_result({}, healthy(), None, self.code, self.now)
        good = copy.deepcopy(state["last_good_snapshot"])
        first, offline = checker.plan_result(state, None, "ssh_timeout", self.code, self.now)
        self.assertTrue(first["notify"])
        self.assertEqual(offline["last_good_snapshot"], good)
        second, still_offline = checker.plan_result(offline, None, "ssh_timeout", self.code, self.now)
        self.assertFalse(second["notify"])
        self.assertEqual(still_offline["last_good_snapshot"], good)
        restored, _ = checker.plan_result(still_offline, healthy(), None, self.code, self.now)
        self.assertIn("remote_connection_restored", restored["notify_reasons"])

    def test_recent_failure_is_delivered_once_and_recovery_alert_is_meaningful(self):
        _, state = checker.plan_result({}, healthy(), None, self.code, self.now)
        snapshot = healthy()
        snapshot["recent_events"] = [{"type": "job_failed", "job_id": "job-12345678", "occurred_at": self.now}]
        result, state = checker.plan_result(state, snapshot, None, self.code, self.now)
        self.assertEqual(result["new_events"][0]["job_id"], "job-12345678")
        repeated, _ = checker.plan_result(state, snapshot, None, self.code, self.now)
        self.assertFalse(repeated["notify"])
        snapshot["status"] = "attention"
        snapshot["alerts"] = [{"code": "health_check_failed", "component": "api_ready"}]
        _, state = checker.plan_result(state, snapshot, None, self.code, self.now)
        restored, _ = checker.plan_result(state, healthy(), None, self.code, self.now)
        self.assertIn("health_state_changed", restored["notify_reasons"])

    def test_last_url_survives_a_reachable_snapshot_with_tunnel_down(self):
        _, state = checker.plan_result({}, healthy(), None, self.code, self.now)
        snapshot = healthy()
        snapshot.update(status="attention", public_url=None)
        output, state = checker.plan_result(state, snapshot, None, self.code, self.now)
        self.assertEqual(output["last_public_url"], "https://current-route.trycloudflare.com")
        output, _ = checker.plan_result(state, None, "ssh_timeout", self.code, self.now)
        self.assertEqual(output["last_public_url"], "https://current-route.trycloudflare.com")

    def test_success_event_keeps_completion_time_and_is_not_repeated(self):
        _, state = checker.plan_result({}, healthy(), None, self.code, self.now)
        snapshot = healthy()
        event = checker.compact_event({"type": "job_succeeded", "job_id": "job-12345678", "occurred_at": self.now, "completed_at": self.now, "filename": "PRIVATE"})
        snapshot["recent_events"] = [event]
        first, state = checker.plan_result(state, snapshot, None, self.code, self.now)
        self.assertEqual(first["new_events"][0]["completed_at"], self.now)
        self.assertNotIn("PRIVATE", json.dumps(first))
        repeated, _ = checker.plan_result(state, snapshot, None, self.code, self.now)
        self.assertEqual(repeated["new_events"], [])
        self.assertFalse(repeated["notify"])

    def test_code_hash_changes_only_report_allowed_relative_paths(self):
        for path in [".codex_tmp/autodl/monitor_key", "deploy/autodl/runtime.env", "scoring-demo-web/.env.example", "src/secret.json", "scoring-demo-web/node_modules/lib/index.js", "src/models.pt", "src/../private.py", "/root/token.py"]:
            self.assertFalse(checker.allowed_code_path(path), path)
        _, state = checker.plan_result({}, healthy(), None, self.code, self.now)
        changed = {"ok": True, "hashes": {"src/example.py": "b" * 64, "deploy/autodl/check_remote.py": "c" * 64}, "skipped_files": 0}
        result, _ = checker.plan_result(state, healthy(), None, changed, self.now)
        self.assertEqual(result["code_changes"]["counts"], {"added": 1, "modified": 1, "deleted": 0})
        self.assertEqual(result["code_changes"]["paths"]["modified"], ["src/example.py"])
        self.assertFalse(result["code_changes"]["deployment_performed"])

    def test_compact_snapshot_drops_private_fields_and_nonpublic_urls(self):
        result = checker.compact_snapshot({"schema_version": "rallymate-health/1", "overall_status": "attention", "generated_at": self.now, "private_path": "/root/PRIVATE", "api_key": "SECRET", "public_url": "http://127.0.0.1/private", "alerts": [{"code": "job_progress_stalled", "job_id": "job-12345678", "error": "/root/SECRET"}], "recent_events": [{"type": "job_failed", "job_id": "job-12345678", "occurred_at": self.now, "filename": "PRIVATE_VIDEO"}]})
        encoded = json.dumps(result)
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn("SECRET", encoded)
        self.assertNotIn("127.0.0.1", encoded)

    def test_ssh_rejects_unknown_hosts_disables_extra_keys_and_uses_only_snapshot_command(self):
        class SSHException(Exception): pass
        class AuthenticationException(SSHException): pass
        class BadHostKeyException(SSHException): pass
        class RejectPolicy: pass
        payload = json.dumps({"schema_version": "rallymate-health/1", "overall_status": "healthy", "generated_at": self.now, "stale": False}).encode()
        class Channel:
            def __init__(self): self.data = payload
            def settimeout(self, _): pass
            def recv_ready(self): return bool(self.data)
            def recv(self, size): chunk, self.data = self.data[:size], self.data[size:]; return chunk
            def recv_stderr_ready(self): return False
            def exit_status_ready(self): return True
            def recv_exit_status(self): return 0
        client = MagicMock()
        client.exec_command.return_value = (MagicMock(), SimpleNamespace(channel=Channel()), MagicMock())
        paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=RejectPolicy, SSHException=SSHException, AuthenticationException=AuthenticationException, BadHostKeyException=BadHostKeyException)
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "monitor_key"
            key.write_text("unit-test-only")
            with patch.dict("sys.modules", {"paramiko": paramiko}):
                self.assertEqual(checker.fetch_snapshot(key)["status"], "healthy")
        client.load_system_host_keys.assert_called_once_with()
        self.assertIsInstance(client.set_missing_host_key_policy.call_args.args[0], RejectPolicy)
        self.assertFalse(client.connect.call_args.kwargs["look_for_keys"])
        self.assertFalse(client.connect.call_args.kwargs["allow_agent"])
        self.assertNotIn("password", client.connect.call_args.kwargs)
        self.assertEqual(client.exec_command.call_args.args, ("snapshot",))
        self.assertFalse(client.exec_command.call_args.kwargs["get_pty"])
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

"""Single-flight preparation of bounded report sections from verified artifacts.

One browser disconnect does not own shared CPU work. Job cancellation, source
changes, service shutdown and the existing 60-second budget still stop it.
Only encoded bytes are retained, never mutable summaries or proof objects.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from threading import BoundedSemaphore, Event

from rallymate_service.report_delivery_cache import DeliveryError, DeliveryResult, ReportDeliveryCache, artifact_signature
from rallymate_service.segmented_report import SegmentedReportError
from rallymate_vision.postprocess_resources import PostprocessBudget, PostprocessResourceLimit, PostprocessOutputLimit
from rallymate_service.report_evidence_transport import ReportEvidenceLimitError


class SegmentedDelivery:
    def __init__(self, settings, database, rules_identity):
        self.settings, self.db, self.rules_identity = settings, database, rules_identity
        self.closed = Event()
        self.identity_slots = BoundedSemaphore(2)
        self.cache = ReportDeliveryCache(max_queued=2, max_entries=16, max_bytes=32 * 1024 * 1024,
                                         preparation_timeout_seconds=60, queue_timeout_seconds=120)

    def _state(self, job_id):
        state = self.db.execution_state(job_id)
        if state is None:
            raise DeliveryError(404, "report_job_not_found", "分析任务不存在。")
        if state["status"] != "succeeded" or state.get("cancel_requested"):
            raise DeliveryError(409, "report_source_changed", "分析任务尚未完成或已取消。")
        return state

    def _snapshot(self, job_id, kind):
        with self.db.connect() as connection:
            row = connection.execute("SELECT updated_at,summary_json,output_dir,video_path FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise DeliveryError(404, "report_job_not_found", "分析任务不存在。")
        root = Path(row["output_dir"]).resolve()
        if root != (self.settings.runs_dir / job_id).resolve():
            raise DeliveryError(409, "report_evidence_invalid", "动作证据来源校验未通过。")
        summary_digest = hashlib.sha256((row["summary_json"] or "").encode("utf8")).hexdigest()
        # Hash the actual dependencies, not only mtimes or declared hashes.
        # Same-size edits with restored timestamps must invalidate ready bytes.
        names = [] if kind == "motion" else ["events.jsonl", "indicator-features.jsonl", "scoring-evidence.sqlite3"]
        if kind in {"overview", "detail"}:
            names.append("frames.jsonl")
        if kind == "detail":
            names.extend(("primary-player.jsonl", "source-aligned-measurements.json"))
        dependencies = tuple(artifact_signature(root / name, content_hash=True) for name in names)
        video = artifact_signature(Path(row["video_path"]), content_hash=True) if kind == "overview" else None
        return (row["updated_at"], summary_digest, str(root), dependencies, video, self.rules_identity())

    def request(self, job_id, kind, offset, limit, event_id, build):
        initial_state = self._state(job_id)
        scope = ("segmented-v2", job_id, kind, offset, limit, event_id)
        pending = self.cache.pending_result(scope)
        if pending is not None:
            return pending
        if not self.identity_slots.acquire(blocking=False):
            return DeliveryResult("error", error=DeliveryError(429, "report_segments_busy", "动作证据正在准备，请稍后重试。"))
        try:
            try:
                snapshot = self._snapshot(job_id, kind)
            except (OSError, ValueError, TypeError) as exc:
                raise DeliveryError(409, "report_evidence_invalid", "动作证据来源校验未通过。") from exc
            key = (scope, snapshot)

            def current():
                if self.closed.is_set():
                    raise DeliveryError(503, "report_delivery_unavailable", "报告准备正在停止。")
                if self.db.execution_state(job_id) != initial_state:
                    raise DeliveryError(409, "report_source_changed", "分析任务已变化，请重新读取报告。")

            def source_is_current():
                current()
                try:
                    matches = self._snapshot(job_id, kind) == snapshot
                except (OSError, ValueError, TypeError):
                    matches = False
                if not matches:
                    raise DeliveryError(409, "report_source_changed", "分析摘要或来源产物已变化，请重新读取报告。")
                return True

            def prepare():
                budget = PostprocessBudget(max_seconds=60, cancellation_callback=current)
                budget.check(force=True)
                job = self.db.get_job(job_id)
                if job is None:
                    raise DeliveryError(404, "report_job_not_found", "分析任务不存在。")
                try:
                    body = build(job, budget.check)
                    budget.check(force=True)
                    return body
                except SegmentedReportError as exc:
                    raise DeliveryError(exc.status, exc.code, exc.message) from exc
                except (PostprocessOutputLimit, ReportEvidenceLimitError) as exc:
                    raise DeliveryError(413, "report_segment_limit", "本页证据超过读取上限；原始产物已保留。") from exc
                except PostprocessResourceLimit as exc:
                    raise DeliveryError(503, "report_segment_resource_limit", str(exc)) from exc

            return self.cache.request(key, prepare, source_is_current, scope=scope)
        finally:
            self.identity_slots.release()

    def close(self):
        self.closed.set()
        self.cache.close()

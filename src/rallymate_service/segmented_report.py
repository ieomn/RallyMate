"""Bounded, source-bound report delivery over retained analysis artifacts.

The summary is an explicit projection of persisted aggregates. Event pages and
evidence pages do not build the monolithic report or rerun inference. Offsets
index bytes, not decoded proof; every delivered proof is resolved and checked.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from threading import Lock

from rallymate_scoring.artifact_evidence import (
    ArtifactEvidenceReader, ArtifactEvidenceError, verify_evidence_manifest,
)
from rallymate_scoring.model_measurement_contract import (
    SOURCE_BINDING_FIELDS, validated_model_window, validated_geometry_mask,
)
from rallymate_service.indicator_records import _parse
from rallymate_service.report_evidence_transport import pack_report_evidence

VERSION = "segmented-report-v1"
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_INDEX_ROWS = 65_000
MAX_FRAME_ROWS = 200_000
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_SUMMARY_BYTES = 256 * 1024
MAX_OVERVIEW_BYTES = 2 * 1024 * 1024
MAX_OVERVIEW_WORKING_BYTES = 64 * 1024 * 1024
MAX_DETAIL_BYTES = 8 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")


class SegmentedReportError(ValueError):
    def __init__(self, code="report_evidence_invalid", message="动作证据缺失、来源不一致或校验未通过。", status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _require(value):
    if not value:
        raise SegmentedReportError()


def source_identity(job):
    summary = job.get("summary") or {}
    loop = summary.get("minimum_scoring_loop") or {}
    provenance = loop.get("provenance") or {}
    _require(summary.get("job_id") == job["id"])
    source = {"source_job_id": provenance.get("pipeline_job_id"), "video_id": loop.get("video_id"),
              "video_sha256": provenance.get("video_sha256"), "frames_sha256": provenance.get("frames_sha256")}
    _require(all(isinstance(source[k], str) and _SHA.fullmatch(source[k]) for k in ("video_sha256", "frames_sha256")))
    _require(all(isinstance(source[k], str) and _ID.fullmatch(source[k]) for k in ("source_job_id", "video_id")))
    hashes, manifest = loop.get("artifact_sha256") or {}, loop.get("evidence_storage") or {}
    evidence_hashes = [hashes.get("events_jsonl"), hashes.get("indicator_features_jsonl"), manifest.get("sha256")]
    _require(all(isinstance(value, str) and _SHA.fullmatch(value) for value in evidence_hashes))
    # Motion measurements live in the persisted summary rather than the event
    # pool. Include their revision too so independently loaded episodes cannot
    # be mixed with an older overview after a same-job summary replacement.
    motion_revision = hashlib.sha256(json.dumps(summary.get("action_recognition"), sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    source["evidence_revision"] = hashlib.sha256(json.dumps([VERSION, *(value.lower() for value in evidence_hashes), motion_revision],
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return source


def build_summary(job, original_video):
    source = source_identity(job)
    _require(original_video is not None and original_video["source_job_id"] == source["source_job_id"]
             and original_video["sha256"].lower() == source["video_sha256"].lower())
    summary = job["summary"]
    loop = summary.get("minimum_scoring_loop") or {}
    state = summary.get("scoring_state") or loop.get("result_state") or {}
    recognition = summary.get("action_recognition") or {}
    base = f"/v1/jobs/{job['id']}"
    video = (summary.get("input") or {}).get("video") or {}
    processing = summary.get("processing") or {}
    return {"schema_version": VERSION, "job_id": job["id"], "status": "ready", "source": source,
        "video": {k: video.get(k) for k in ("width", "height", "fps", "frame_count", "duration_ms")},
        "processing": {k: processing.get(k) for k in ("processed_frames", "elapsed_seconds", "effective_processed_fps")},
        "scoring": {"status": state.get("scoring_status", "unavailable"),
            "measurement_status": state.get("measurement_status", "unavailable"), "grade": state.get("grade"),
            "message": state.get("ui_message_zh"), "event_counts": loop.get("event_counts", {}),
            "score_status_counts": loop.get("score_status_counts", {})},
        "action_candidates": {"status": recognition.get("status", "unavailable"),
            "count": recognition.get("candidate_count"), "review_count": recognition.get("review_candidate_count"),
            "semantics": recognition.get("count_semantics")},
        "original_video": original_video,
        "artifact_urls": {name: f"{base}/artifacts/{name}" for name in
            ("analysis-report.html", "annotated.mp4", "preview.jpg") if (Path(job["output_dir"]) / name).is_file()},
        "actions_url": f"{base}/report-actions", "mesh_url": f"{base}/mesh-preview",
        "delivery": {"summary": "complete", "actions": "not_loaded", "evidence": "on_demand", "mesh": "independent"}}


def _signature(path):
    _require(not path.is_symlink() and path.is_file())
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _scan(path, check, *, parse=True):
    with path.open("rb") as stream:
        while True:
            check()
            offset = stream.tell()
            raw = stream.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                break
            _require(len(raw) <= MAX_RECORD_BYTES)
            if raw.strip():
                value = _parse(raw) if parse else None
                _require(not parse or isinstance(value, dict))
                yield offset, len(raw), value


def _sha(path, check):
    digest = hashlib.sha256()
    before = _signature(path)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            check()
            digest.update(chunk)
    _require(_signature(path) == before)
    return digest.hexdigest()


def _event_metadata(event, job_id):
    event_id = event.get("event_id")
    _require(isinstance(event_id, str) and _ID.fullmatch(event_id))
    _require(type(event.get("person_track_id")) is int and event["person_track_id"] >= 0)
    _require(type(event.get("start_ms")) is int and type(event.get("end_ms")) is int
             and 0 <= event["start_ms"] < event["end_ms"])
    code = event.get("event_code")
    _require(isinstance(code, str) and _ID.fullmatch(code))
    return {"action_id": event_id, "event_id": event_id, "event_code": code,
        "label": {"FS01": "分腿垫步动作候选", "FS02": "启动步动作候选", "FS09": "步法调整动作候选"}.get(code, code),
        "start_ms": event["start_ms"], "end_ms": event["end_ms"], "person_track_id": event["person_track_id"],
        "source_track_id": (event.get("source_window") or {}).get("person_track_id"),
        "indicator_count": 0, "evidence_status": "available",
        "detail_url": f"/v1/jobs/{job_id}/report-actions/{event_id}"}


def _bound_row(row, source, event=None):
    _require(row.get("video_id") == source["video_id"])
    provenance = row.get("provenance") or {}
    _require(provenance.get("pipeline_job_id", provenance.get("source_id")) == source["source_job_id"])
    if provenance.get("video_sha256") is not None:
        _require(provenance["video_sha256"].lower() == source["video_sha256"].lower())
    if event is not None:
        _require(all(row.get(k) == event[k] for k in ("event_id", "event_code", "person_track_id")))


def _proof(row, source, *, parent=None):
    # person_track_id is the scoring timeline's primary identity; source_track_id
    # and source_window preserve the native detector track (which can change).
    # Never equate or overwrite these distinct identities.
    native_track = row.get("source_track_id", (row.get("source_window") or {}).get("person_track_id"))
    window = validated_model_window(row, person_track_id=native_track, parent_window=parent)
    _require(window is not None and window["video_sha256"].lower() == source["video_sha256"].lower())
    _require(validated_geometry_mask(row.get("geometry_mask"), window) is not None)
    return window


@dataclass
class ActionIndex:
    actions: list
    events: dict
    records: dict
    signatures: tuple
    charge: int


class SegmentedReportStore:
    def __init__(self):
        self._lock = Lock()
        self._indexes = OrderedDict()
        self._bytes = 0

    def _paths(self, job):
        root = Path(job["output_dir"]).resolve()
        return root, tuple(root / name for name in ("events.jsonl", "indicator-features.jsonl", "scoring-evidence.sqlite3"))

    def _snapshot(self, job, check):
        source = source_identity(job)
        loop = job["summary"]["minimum_scoring_loop"]
        root, paths = self._paths(job)
        signatures = tuple(_signature(p) for p in paths)
        hashes = loop.get("artifact_sha256") or {}
        for path, name in zip(paths[:2], ("events_jsonl", "indicator_features_jsonl")):
            expected = hashes.get(name)
            _require(isinstance(expected, str) and _SHA.fullmatch(expected) and _sha(path, check) == expected.lower())
        manifest = loop.get("evidence_storage")
        verify_evidence_manifest(root, manifest, expected_video_id=source["video_id"],
                                 expected_source_id=source["source_job_id"], cancellation_callback=check)
        _require(manifest["video_sha256"].lower() == source["video_sha256"].lower())
        _require(tuple(_signature(p) for p in paths) == signatures)
        return source, signatures

    def index(self, job, check):
        source, signatures = self._snapshot(job, check)
        root, paths = self._paths(job)
        key = (job["id"], str(root), json.dumps(source, sort_keys=True), signatures)
        with self._lock:
            index = self._indexes.get(key)
            if index is not None:
                self._indexes.move_to_end(key)
                return index, source
        events, records, actions = {}, {}, []
        charge = 0
        with ArtifactEvidenceReader(root, expected_video_id=source["video_id"],
                expected_source_id=source["source_job_id"], cancellation_callback=check,
                cache_bytes=4 * 1024 * 1024) as reader:
            for offset, size, raw in _scan(paths[0], check):
                _require(len(events) < MAX_INDEX_ROWS)
                row = reader.resolve_record(raw)
                _bound_row(row, source)
                metadata = _event_metadata(row, job["id"])
                try:
                    window = _proof(row, source)
                    _require(window["start_ms"] == row.get("start_ms") and window["end_ms"] == row.get("end_ms"))
                except SegmentedReportError:
                    # Keep a failed candidate visible. It must never become
                    # transportable proof or silently disappear from counts.
                    metadata["evidence_status"] = "failed"
                    metadata["reason_code"] = "event_proof_validation_failed"
                identifier = metadata["event_id"]
                _require(identifier not in events)
                events[identifier] = (offset, size, metadata)
                records[identifier] = []
                actions.append(metadata)
                charge += 2048 + len(identifier) * 4
                _require(charge <= MAX_INDEX_BYTES)
            count = 0
            for offset, size, row in _scan(paths[1], check):
                count += 1
                _require(count <= MAX_INDEX_ROWS)
                event = events.get(row.get("event_id"))
                _require(event is not None)
                _bound_row(row, source, event[2])
                records[row["event_id"]].append((offset, size))
                event[2]["indicator_count"] += 1
                charge += 160
                _require(charge <= MAX_INDEX_BYTES)
        _require(tuple(_signature(p) for p in paths) == signatures)
        # Rehash after scanning, including same-size rewrites with restored mtime.
        _require(self._snapshot(job, check)[1] == signatures)
        actions.sort(key=lambda row: (row["start_ms"], row["event_id"]))
        index = ActionIndex(actions, events, records, signatures, charge)
        with self._lock:
            previous = self._indexes.pop(key, None)
            if previous:
                self._bytes -= previous.charge
            while self._indexes and (len(self._indexes) >= 4 or self._bytes + charge > MAX_INDEX_BYTES):
                _, old = self._indexes.popitem(last=False)
                self._bytes -= old.charge
            self._indexes[key] = index
            self._bytes += charge
        return index, source

    def actions(self, job, offset, limit, check):
        index, source = self.index(job, check)
        page = index.actions[offset:offset + limit]
        return {"schema_version": VERSION, "job_id": job["id"], "source": source,
            "status": "complete" if offset + len(page) >= len(index.actions) else "partial", "actions": page,
            "pagination": _pagination(offset, limit, len(index.actions), len(page))}

    def overview(self, job, original_video, check, *, registry_path=None):
        # The reducers consume bounded scalar observations. Every complete
        # source record, including unknown nested proof, is resolved and checked
        # first. No proof-bearing legacy report is built and then truncated.
        from rallymate_service.report_overview import build_overview
        index, source = self.index(job, check)
        root, paths = self._paths(job)
        observations, retained_bytes = [], 0
        with ArtifactEvidenceReader(root, expected_video_id=source["video_id"],
                expected_source_id=source["source_job_id"], cancellation_callback=check,
                cache_bytes=4 * 1024 * 1024) as reader:
            for _, _, raw in _scan(paths[1], check):
                row = reader.resolve_record(raw)
                event = index.events.get(row.get("event_id"))
                _require(event is not None)
                _bound_row(row, source, event[2])
                parent = _proof(row, source)
                _require(event[2]["start_ms"] <= parent["start_ms"] < parent["end_ms"] <= event[2]["end_ms"])
                _validate_nested_proofs(row, source, parent, check)
                observation = _aggregate_observation(row, raw)
                retained_bytes += len(json.dumps(observation, ensure_ascii=False, allow_nan=False).encode("utf-8")) * 8
                _require(retained_bytes <= MAX_OVERVIEW_WORKING_BYTES)
                observations.append(observation)
        check()
        result = build_overview(job, source, original_video, index.actions, observations,
                                registry_path=registry_path)
        _require(self._snapshot(job, check)[1] == index.signatures)
        check()
        return result

    def motion(self, job, episode_id, check):
        from rallymate_service.report_overview import build_motion_detail
        check()
        result = build_motion_detail(job, source_identity(job), episode_id)
        check()
        return result

    def detail(self, job, event_id, offset, limit, check):
        from rallymate_service.footwork_review import build_footwork_review
        from rallymate_service.process_evaluation import build_process_evaluation
        from rallymate_service.source_assessment import load_source_assessment_event
        index, source = self.index(job, check)
        entry = index.events.get(event_id)
        if entry is None:
            raise SegmentedReportError("report_action_not_found", "动作片段不存在。", 404)
        if entry[2]["evidence_status"] != "available":
            raise SegmentedReportError("report_action_evidence_failed", "这个动作片段的证据校验未通过；候选记录和原始产物已保留。")
        root, paths = self._paths(job)
        with ArtifactEvidenceReader(root, expected_video_id=source["video_id"],
                expected_source_id=source["source_job_id"], cancellation_callback=check,
                cache_bytes=4 * 1024 * 1024) as reader:
            event = _read_row(paths[0], entry[:2], reader, check)
            _bound_row(event, source)
            parent = _proof(event, source)
            _validate_nested_proofs(event, source, parent, check)
            selected = index.records[event_id][offset:offset + limit]
            rows = []
            for location in selected:
                row = _read_row(paths[1], location, reader, check)
                _bound_row(row, source, event)
                _proof(row, source, parent=parent)
                _validate_nested_proofs(row, source, parent, check)
                rows.append(row)
        _verify_source_frames(root / "frames.jsonl", source, parent, check)
        _require(self._snapshot(job, check)[1] == index.signatures)
        total = len(index.records[event_id])
        payload = {"schema_version": VERSION, "job_id": job["id"], "source": source,
            "status": "complete" if offset + len(rows) >= total else "partial", "action": entry[2],
            "event": event, "records": rows, "pagination": _pagination(offset, limit, total, len(rows)),
            "evidence_validation": "validated", "scope": "event_indicator_records"}
        review = build_footwork_review([event], rows, video_id=source["video_id"])
        selected_ids = {row["indicator_id"] for row in rows}
        for episode in review["episodes"]:
            episode["indicators"] = [item for item in episode["indicators"] if item["indicator_id"] in selected_ids]
        scope = {"kind": "event_indicator_page", "event_id": event_id, "whole_video": False,
                 "pagination": payload["pagination"]}
        review["detail_scope"] = scope
        review["detail_status"] = "loaded"
        payload["footwork_review"] = review
        check()
        one_summary = {**job["summary"], "action_recognition": {"motion_analysis": {"families": {}}}}
        process = build_process_evaluation(one_summary, rows, footwork_review=review)
        process["detail_scope"] = scope
        payload["process_evaluation"] = process
        payload["source_aligned_assessment"] = load_source_assessment_event(root, event_id=event_id,
            video_id=source["video_id"], duration_ms=(job["summary"].get("input") or {}).get("video", {}).get("duration_ms"),
            video_sha256=source["video_sha256"], frames_sha256=source["frames_sha256"], cancellation_callback=check)
        check()
        return pack_report_evidence(payload, cancellation_callback=check)


def _pagination(offset, limit, total, count):
    return {"offset": offset, "limit": limit, "total": total,
            "next_offset": offset + count if offset + count < total else None}


def _aggregate_observation(row, raw):
    """Internal reducer input, never a replacement for a source evidence row."""
    keys = ("video_id", "event_id", "indicator_id", "event_code", "person_track_id",
        "feature_status", "scoring_feature_status", "scoring_status", "status", "candidate_type",
        "contact_confirmed", "technique_id", "action_type", "event", "action", "technique",
        "phase_key", "phase", "stage", "stage_code")
    result = {key: row[key] for key in keys if key in row and (row[key] is None or type(row[key]) in (str, int, float, bool))}
    result["reason_codes"] = [item for item in row.get("reason_codes", []) if isinstance(item, str)]
    gate = row.get("quality_gate") or {}
    result["quality_gate"] = {key: gate[key] for key in ("measurement_allowed", "scoring_allowed",
        "hard_fail_flags", "scoring_block_flags", "advisory_flags") if key in gate}
    for key in ("features", "scoring_features"):
        result[key] = [{name: feature[name] for name in ("feature_name", "value", "confidence", "unit", "valid")
            if name in feature} for feature in row.get(key, []) if isinstance(feature, dict)]
    # Conflicting duplicate records must still conflict if the difference was
    # in proof or a field not consumed by the reducer.
    result["_source_record_sha256"] = hashlib.sha256(json.dumps(raw, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return result


def _read_row(path, location, reader, check):
    check()
    with path.open("rb") as stream:
        stream.seek(location[0])
        raw = stream.read(location[1])
    _require(len(raw) == location[1])
    return reader.resolve_record(_parse(raw))


def _validate_nested_proofs(row, source, parent, check):
    pending = [row]
    while pending:
        check()
        item = pending.pop()
        if isinstance(item, dict):
            if "source_window" in item and "measurement_provenance" in item:
                _proof(item, source, parent=parent)
            pending.extend(v for v in item.values() if isinstance(v, (dict, list)))
        elif isinstance(item, list):
            pending.extend(v for v in item if isinstance(v, (dict, list)))


def _verify_source_frames(path, source, window, check):
    """Stream bytes for the whole digest; decode only the requested PTS interval."""
    before = _signature(path)
    digest = hashlib.sha256()
    wanted = {binding["processed_index"]: binding for binding in window["source_bindings"]}
    found = set()
    ordinal = 0
    with path.open("rb") as stream:
        while True:
            check()
            raw = stream.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                break
            _require(len(raw) <= MAX_RECORD_BYTES)
            digest.update(raw)
            if not raw.strip():
                continue
            _require(ordinal < MAX_FRAME_ROWS)
            binding = wanted.get(ordinal)
            if binding is not None:
                row = _parse(raw)
                frame = row.get("frame") or {}
                _require(row.get("job_id") == source["source_job_id"])
                _require(frame.get("video_sha256", "").lower() == source["video_sha256"].lower())
                _require(all(frame.get(k) == binding[b] for k, b in
                    (("index", "frame_index"), ("processed_index", "processed_index"), ("pts", "pts"),
                     ("time_base_num", "time_base_num"), ("time_base_den", "time_base_den"),
                     ("source_timestamp_ms", "timestamp_ms"))))
                _require(frame.get("timestamp_ms") == window["video_timestamps_ms"][len(found)])
                poses = [pose for pose in row.get("poses", []) if pose.get("person_track_id") == binding["person_track_id"]]
                _require(len(poses) == 1)
                native = poses[0].get("source_binding") or {}
                _require(all(native.get(key) == binding[key] for key in SOURCE_BINDING_FIELDS))
                found.add(ordinal)
            ordinal += 1
    _require(found == set(wanted) and digest.hexdigest() == source["frames_sha256"].lower()
             and _signature(path) == before)

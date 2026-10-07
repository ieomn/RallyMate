"""Human source-rule reviews, deliberately separate from runtime scores/calibration.

The shared service credential grants access; reviewer identities are self-reported.
This store is an append-only audit trail, not an identity signature or an approved
truth-pack intake. Exported label candidates still require independent adjudication.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rallymate_scoring.calibration import validate_coach_label


VERSION = "technical-review-v1.0.0"
CALIBRATION_STATUS = "requires_independent_adjudication_and_split"
MAX_BODY_BYTES = 32768
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class TechnicalReviewError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message}


def _require(condition: bool, code: str, message: str, status: int = 422) -> None:
    if not condition:
        raise TechnicalReviewError(code, message, status)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _load_json(text: str | bytes) -> Any:
    def invalid(_: str):
        raise ValueError("non-finite JSON number")
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid)


class TechnicalReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mutation_id: str
    expected_revision: int = Field(ge=0)
    source_sha256: str
    source_reference_version: str
    source_reference_sha256: str
    artifact_context_sha256: str
    review_id: str | None = None
    target_kind: Literal["indicator", "visual_rule"] = "indicator"
    indicator_id: str | None = Field(default=None, min_length=1, max_length=64)
    visual_rule_id: str | None = Field(default=None, min_length=1, max_length=128)
    player_id: int = Field(ge=1)
    event_id: str | None = Field(default=None, max_length=128)
    event_source: Literal["system_event", "manual_interval"]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    reviewer_id: str = Field(min_length=1, max_length=128)
    reviewer_name: str = Field(min_length=1, max_length=128)
    reviewer_role: Literal["coach", "reviewer"]
    observability: Literal["observable", "partial", "unobservable"]
    status: Literal["graded", "observed", "not_observed", "unassessable"]
    grade: Literal["A", "B", "C", "D", "E"] | None
    reason_zh: str = Field(min_length=2, max_length=4000)
    next_step_zh: str = Field(max_length=4000)

    @field_validator("mutation_id", "review_id")
    @classmethod
    def validate_uuid(cls, value):
        if value is not None and not _UUID.fullmatch(value):
            raise ValueError("expected lowercase UUID")
        return value

    @field_validator("source_sha256", "source_reference_sha256", "artifact_context_sha256")
    @classmethod
    def validate_sha(cls, value):
        if not _SHA.fullmatch(value):
            raise ValueError("expected lowercase SHA256")
        return value

    @field_validator("reviewer_id", "reviewer_name", "reason_zh", "next_step_zh")
    @classmethod
    def validate_text(cls, value):
        if value != value.strip() or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
            raise ValueError("text must be trimmed and contain no control characters")
        return value


def parse_review_input(body: bytes) -> TechnicalReviewInput:
    _require(len(body) <= MAX_BODY_BYTES, "review_too_large", "评审内容过长。", 413)
    try:
        return TechnicalReviewInput.model_validate(_load_json(body))
    except (ValueError, UnicodeError, ValidationError) as exc:
        # Never reflect arbitrary request text, paths or headers in API errors.
        raise TechnicalReviewError("invalid_review", "评审字段、类型或 JSON 格式无效。") from exc


def _signature(path: Path) -> tuple:
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class TechnicalReviewCatalog:
    def __init__(self, raw: bytes):
        package = _load_json(raw)
        _require(package.get("schema_version") == "technical-review-rules-v1"
                 and package.get("source_reference_version") == "scoring-reference-20261004",
                 "source_reference_unavailable", "来源规则版本不可用。", 503)
        self.version, self.sha256 = package["source_reference_version"], package["source_reference_sha256"]
        _require(bool(_SHA.fullmatch(self.sha256)), "source_reference_unavailable", "来源规则指纹无效。", 503)
        self.rules = {card["indicator_id"]: card for card in package["rules"]}
        self.visual_rules = {rule["visual_rule_id"]: rule for rule in package["visual_rules"]}
        _require(len(self.rules) == len(package["rules"]) == 298
                 and len(self.visual_rules) == len(package["visual_rules"]) == 246,
                 "source_reference_unavailable", "来源规则目录不完整。", 503)
        for rule in self.rules.values():
            _require(type(rule["manual_grading_allowed"]) is bool
                     and rule["manual_grading_allowed"] == (not rule["blockers"])
                     and set(rule["grades"]) == set("ABCDE")
                     and all(isinstance(text, str) and bool(text) for text in rule["grades"].values()),
                     "source_reference_unavailable", "来源分级定义无效。", 503)

    def public_rules(self) -> list[dict]:
        keys = ("indicator_id", "event_code", "manual_grading_allowed", "blockers", "warnings",
                "source_document_id", "source_document_sha256", "source_table", "rubric_sha256")
        return [{key: rule[key] for key in keys} for rule in self.rules.values()]


class TechnicalReviewContext:
    """Small artifact-backed context; never exposes worker paths or guesses IDs."""
    def __init__(self):
        self._cache: OrderedDict[tuple, dict] = OrderedDict()
        self._lock = RLock()

    def load(self, job: dict) -> dict:
        _require(job.get("status") == "succeeded", "job_not_completed", "视频分析完成后才能进行评审。", 409)
        source = Path(job["video_path"])
        output = Path(job["output_dir"])
        primary, events = output / "primary-player.jsonl", output / "events.jsonl"
        try:
            signatures = (_signature(source), _signature(primary), _signature(events))
            key = (job["id"], *signatures, _sha(job.get("summary")))
            with self._lock:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
            digest = hashlib.sha256()
            with source.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            summary = job.get("summary") or {}
            video = summary.get("input", {}).get("video", {})
            duration, fps = video.get("duration_ms"), video.get("fps")
            _require(type(duration) is int and duration > 0 and type(fps) in (int, float)
                     and math.isfinite(fps) and fps > 0,
                     "review_context_unavailable", "视频时长和帧率信息缺失，暂不能评审。", 409)
            processing = summary.get("processing", {})
            first = processing.get("source_start_frame")
            last = processing.get("source_end_frame_exclusive")
            _require(type(first) is int and type(last) is int and 0 <= first < last,
                     "review_context_unavailable", "分析时段信息缺失，暂不能评审。", 409)
            review_start = max(0, math.floor(first * 1000 / fps))
            review_end = min(duration, math.ceil(last * 1000 / fps))
            players: dict[int, dict] = {}
            primary_digest, events_digest = hashlib.sha256(), hashlib.sha256()
            with primary.open("rb") as handle:
                for line in handle:
                    primary_digest.update(line)
                    if not line.strip():
                        continue
                    row = _load_json(line)
                    player_id, timestamp = row.get("primary_player_id"), row.get("timestamp_ms")
                    if player_id is None:
                        continue
                    _require(type(player_id) is int and player_id >= 1 and type(timestamp) is int
                             and review_start <= timestamp <= review_end,
                             "invalid_review_artifact", "人物时间线数据无效。", 409)
                    end = min(review_end, math.ceil(timestamp + 1000 / fps))
                    prior = players.get(player_id)
                    players[player_id] = {"player_id": player_id,
                        "start_ms": min(timestamp, prior["start_ms"]) if prior else timestamp,
                        "end_ms": max(end, prior["end_ms"]) if prior else end}
            event_map = {}
            with events.open("rb") as handle:
                for line in handle:
                    events_digest.update(line)
                    if not line.strip():
                        continue
                    row = _load_json(line)
                    player_id = row.get("provenance", {}).get("primary_player_id", row.get("person_track_id"))
                    event_id, start, end = row.get("event_id"), row.get("start_ms"), row.get("end_ms")
                    _require(isinstance(event_id, str) and bool(event_id) and len(event_id) <= 128
                             and event_id not in event_map and type(start) is int and type(end) is int
                             and review_start <= start < end <= review_end and type(player_id) is int
                             and player_id in players and isinstance(row.get("event_code"), str),
                             "invalid_review_artifact", "动作事件数据无效。", 409)
                    event_map[event_id] = {"event_id": event_id, "event_code": row["event_code"],
                        "player_id": player_id, "start_ms": start, "end_ms": end}
            _require(signatures == (_signature(source), _signature(primary), _signature(events)),
                     "review_artifacts_changed", "视频产物已变化，请重新加载后评审。", 409)
            result = {"source_sha256": digest.hexdigest(),
                "video": {"duration_ms": duration, "review_start_ms": review_start, "review_end_ms": review_end},
                "players": sorted(players.values(), key=lambda row: row["player_id"]),
                "events": list(event_map.values()), "event_map": event_map}
            result["artifact_context_sha256"] = _sha({"source_sha256": result["source_sha256"], "video": result["video"],
                                                      "primary_sha256": primary_digest.hexdigest(), "events_sha256": events_digest.hexdigest()})
            with self._lock:
                self._cache[key] = result
                while len(self._cache) > 16:
                    self._cache.popitem(last=False)
            return result
        except TechnicalReviewError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TechnicalReviewError("review_context_unavailable", "原视频或评审所需产物不可用。", 409) from exc


_BINDING_FIELDS = ("source_sha256", "source_reference_version", "source_reference_sha256", "artifact_context_sha256", "target_kind", "indicator_id", "visual_rule_id",
                   "player_id", "event_id", "event_source", "start_ms", "end_ms",
                   "reviewer_id", "reviewer_name", "reviewer_role")
_SEMANTICS = {"semantics": "human_source_rule_review", "formal_grade": None, "formal_score": None,
              "calibration_eligible": False, "calibration_status": CALIBRATION_STATUS,
              "identity_verification": "self_reported_local", "observability_source": "manual_visual_review",
              "automatic_joint_gate_passed": None, "production_scoring_enabled": False}


class TechnicalReviewStore:
    def __init__(self, path: Path, reference_path: Path | None = None):
        self.path, self.reference_path = Path(path), Path(reference_path) if reference_path else None
        self.context = TechnicalReviewContext()
        self._catalog_signature: str | None = None
        self._catalog: TechnicalReviewCatalog | None = None
        self._lock = RLock()

    def catalog(self) -> TechnicalReviewCatalog:
        try:
            raw = (self.reference_path.read_bytes() if self.reference_path else
                   files("rallymate_scoring").joinpath("data/technical_review_rules.json").read_bytes())
            signature = hashlib.sha256(raw).hexdigest()
            with self._lock:
                if self._catalog is None or signature != self._catalog_signature:
                    self._catalog = TechnicalReviewCatalog(raw)
                    self._catalog_signature = signature
                return self._catalog
        except (OSError, KeyError, ValueError, TypeError) as exc:
            if isinstance(exc, TechnicalReviewError):
                raise
            raise TechnicalReviewError("source_reference_unavailable", "来源规则目录暂不可用。", 503) from exc

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS technical_review_revisions (
                    job_id TEXT NOT NULL, job_revision INTEGER NOT NULL,
                    review_id TEXT NOT NULL, entry_revision INTEGER NOT NULL,
                    mutation_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
                    PRIMARY KEY(job_id, job_revision), UNIQUE(job_id, mutation_id),
                    UNIQUE(job_id, review_id, entry_revision)
                );
                CREATE TRIGGER IF NOT EXISTS technical_reviews_no_update BEFORE UPDATE ON technical_review_revisions
                    BEGIN SELECT RAISE(ABORT, 'review history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS technical_reviews_no_delete BEFORE DELETE ON technical_review_revisions
                    BEGIN SELECT RAISE(ABORT, 'review history is append-only'); END;
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _history(self, conn: sqlite3.Connection, job_id: str) -> list[dict]:
        rows = conn.execute("SELECT * FROM technical_review_revisions WHERE job_id=? ORDER BY job_revision", (job_id,)).fetchall()
        history, prior = [], {}
        for index, row in enumerate(rows, 1):
            record = _load_json(row["record_json"])
            predecessor = prior.get(row["review_id"])
            content = {key: value for key, value in record.items() if key != "record_sha256"}
            _require(row["job_revision"] == index and record.get("job_revision") == index
                     and record.get("job_id") == job_id and record.get("review_id") == row["review_id"]
                     and record.get("entry_revision") == row["entry_revision"]
                     and record.get("mutation_id") == row["mutation_id"]
                     and record.get("record_sha256") == _sha(content)
                     and record.get("entry_revision") == (predecessor["entry_revision"] + 1 if predecessor else 1)
                     and record.get("previous_record_sha256") == (predecessor["record_sha256"] if predecessor else None),
                     "review_history_invalid", "评审历史校验失败，已停止写入。", 409)
            prior[row["review_id"]] = record
            history.append(record)
        return history

    @staticmethod
    def _latest(history: list[dict]) -> list[dict]:
        latest = {record["review_id"]: record for record in history}
        return sorted(latest.values(), key=lambda record: record["job_revision"])

    def _response(self, job_id: str, catalog: TechnicalReviewCatalog, context: dict, history: list[dict]) -> dict:
        entries = [{**entry, "source_binding_current": (
            entry["source_sha256"] == context["source_sha256"]
            and entry["source_reference_version"] == catalog.version
            and entry["source_reference_sha256"] == catalog.sha256
            and entry["artifact_context_sha256"] == context["artifact_context_sha256"]
        )} for entry in self._latest(history)]
        return {"schema_version": VERSION, "job_id": job_id, "revision": len(history),
                "source_sha256": context["source_sha256"], "source_reference_version": catalog.version,
                "artifact_context_sha256": context["artifact_context_sha256"],
                "source_reference_sha256": catalog.sha256, "video": context["video"],
                "players": context["players"], "events": context["events"], "rules": catalog.public_rules(),
                "visual_rules": list(catalog.visual_rules.values()),
                "entries": entries, **_SEMANTICS}

    def get(self, job: dict, *, export: bool = False) -> dict:
        catalog, context = self.catalog(), self.context.load(job)
        with self._connect() as conn:
            history = self._history(conn, job["id"])
        result = self._response(job["id"], catalog, context, history)
        if export:
            result["history"] = history
            result["exported_at"] = datetime.now(timezone.utc).isoformat()
            result["hash_is_identity_signature_or_trusted_timestamp"] = False
            result["coach_label_candidates"] = self._label_candidates(result["entries"])
            result["intake_requirements"] = [
                "确认原视频、人物、事件及阶段边界；系统候选事件不等于已接受的人工事件。",
                "独立教练盲评、争议裁定，并按视频来源划分标定集与独立测试集。",
                "现有 truth_pack 仅覆盖 FS01、FS02、FS09 的 13 项；其他来源指标需要扩展合同及验证。",
                "提交原始 CSV、来源包与必要的事件/关键点/语义审查；导出候选不等于已通过 intake。",
                "完成独立验证及可信授权后，才能考虑正式校准或上线；人工等级不映射为百分制。",
            ]
        return result

    @staticmethod
    def _label_candidates(entries: list[dict]) -> list[dict]:
        candidates = []
        for record in entries:
            if record["status"] != "graded" or not record["source_binding_current"]:
                continue
            binding = {key: record[key] for key in ("source_sha256", "event_code", "player_id", "start_ms", "end_ms")}
            event_id = record["event_id"] or f"manual-interval-{_sha(binding)[:24]}"
            label = {"schema_version": "1.0.0", "annotation_id": f"{record['review_id']}:r{record['entry_revision']}",
                     "video_id": record["source_sha256"], "event_id": event_id,
                     "indicator_id": record["indicator_id"], "annotator_id": record["reviewer_id"],
                     "label_type": "grade", "grade": record["grade"]}
            validate_coach_label(label)
            candidates.append({"coach_label": label, "review_id": record["review_id"],
                               "review_record_sha256": record["record_sha256"],
                               "event_source": record["event_source"], "event_binding": binding,
                               "accepted_manual_event": False, "human_truth_ready": False,
                               "calibration_eligible": False, "calibration_status": CALIBRATION_STATUS})
        return candidates

    def save(self, job: dict, submission: TechnicalReviewInput) -> dict:
        catalog, context = self.catalog(), self.context.load(job)
        payload = submission.model_dump()
        _require(submission.source_sha256 == context["source_sha256"], "source_changed", "原视频已变化，请重新加载。", 409)
        _require(submission.source_reference_version == catalog.version and submission.source_reference_sha256 == catalog.sha256,
                 "source_reference_changed", "来源规则版本已变化，请重新加载。", 409)
        _require(submission.artifact_context_sha256 == context["artifact_context_sha256"],
                 "artifact_context_changed", "人物或动作产物已变化，请重新加载后再次核对评审。", 409)
        if submission.target_kind == "indicator":
            rule = catalog.rules.get(submission.indicator_id)
            _require(rule is not None and submission.visual_rule_id is None,
                     "unknown_indicator", "指标不在已核验的 298 项来源目录中。")
            _require(submission.status in {"graded", "unassessable"}, "invalid_indicator_status", "指标必须选择人工等级或无法评价。")
        else:
            rule = catalog.visual_rules.get(submission.visual_rule_id)
            _require(rule is not None and submission.indicator_id is None,
                     "unknown_visual_rule", "视觉条目不在已核验的 246 条原文中。")
            _require(submission.status in {"observed", "not_observed", "unassessable"} and submission.grade is None
                     and submission.event_source == "manual_interval", "invalid_visual_review",
                     "视觉原文仅可记录人工时段中的观察情况，不能填写 A～E 等级。")
            _require(submission.status == "unassessable" or submission.observability == "observable",
                     "visual_without_observation", "确认出现或未出现前，需人工确认画面具备充分观察条件。")
        bounds = context["video"]
        _require(bounds["review_start_ms"] <= submission.start_ms < submission.end_ms <= bounds["review_end_ms"],
                 "invalid_interval", "评审时段必须完整位于已分析的视频范围内。")
        player = next((item for item in context["players"] if item["player_id"] == submission.player_id), None)
        _require(player is not None, "unknown_player", "人物编号不在该视频已记录的人物中。")
        _require(player["start_ms"] <= submission.start_ms < submission.end_ms <= player["end_ms"],
                 "invalid_player_interval", "评审时段超出该人物的已记录范围。")
        if submission.event_source == "system_event":
            event = context["event_map"].get(submission.event_id)
            _require(event is not None and event["event_code"] == rule["event_code"]
                     and event["player_id"] == submission.player_id
                     and event["start_ms"] <= submission.start_ms < submission.end_ms <= event["end_ms"],
                     "event_binding_mismatch", "事件、人物、指标类别或时段不匹配。")
        else:
            _require(submission.event_id is None, "manual_interval_has_event", "人工时段不能冒用系统事件编号。")
        if submission.status == "graded":
            _require(rule["manual_grading_allowed"], "source_rule_blocked", "该指标原文存在缺失或错误，只能记录无法评价。")
            _require(submission.grade is not None and submission.observability == "observable",
                     "grade_without_observation", "只有人工确认充分可观察时才能填写 A～E 等级。")
        else:
            _require(submission.grade is None, "unassessable_has_grade", "无法评价时等级必须留空。")
        payload_hash = _sha(payload)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            history = self._history(conn, job["id"])
            retry = conn.execute("SELECT payload_sha256 FROM technical_review_revisions WHERE job_id=? AND mutation_id=?",
                                 (job["id"], submission.mutation_id)).fetchone()
            if retry:
                _require(retry["payload_sha256"] == payload_hash, "mutation_conflict", "同一保存编号不能用于不同内容。", 409)
                return self._response(job["id"], catalog, context, history)
            _require(submission.expected_revision == len(history), "revision_conflict", "评审已有新版本，请重新加载后再保存。", 409)
            entries = self._latest(history)
            previous = next((record for record in entries if record["review_id"] == submission.review_id), None)
            if submission.review_id:
                _require(previous is not None, "review_not_found", "待修订的评审不存在。", 404)
                _require(all(previous[key] == payload[key] for key in _BINDING_FIELDS),
                         "immutable_binding", "修订不能更换来源、人物、时段、指标或评审人；请创建新评审。", 409)
            else:
                identity_fields = tuple(key for key in _BINDING_FIELDS if key not in {"reviewer_name", "reviewer_role"})
                _require(not any(all(record[key] == payload[key] for key in identity_fields) for record in entries),
                         "review_already_exists", "该评审人已保存这一指标和时段，请修订已有记录。", 409)
            now = datetime.now(timezone.utc).isoformat()
            record = {key: value for key, value in payload.items() if key not in {"expected_revision", "review_id"}}
            record.update({"schema_version": VERSION, "job_id": job["id"],
                "review_id": previous["review_id"] if previous else str(uuid.uuid4()),
                "job_revision": len(history) + 1, "entry_revision": previous["entry_revision"] + 1 if previous else 1,
                "created_at": previous["created_at"] if previous else now, "saved_at": now,
                "previous_record_sha256": previous["record_sha256"] if previous else None,
                "event_code": rule.get("event_code"), "source_document_id": rule["source_document_id"],
                "artifact_context_sha256": context["artifact_context_sha256"],
                "source_document_sha256": rule["source_document_sha256"], "source_table": rule.get("source_table"),
                "source_locator": rule.get("source_locator"), "stage_id": rule.get("stage_id"),
                "optional_in_source": rule.get("optional_in_source"), "source_text": rule.get("source_text"),
                "rubric_sha256": rule["rubric_sha256"], "selected_grade_definition": rule.get("grades", {}).get(submission.grade),
                "source_blockers": rule.get("blockers", []), "source_warnings": rule.get("warnings", []), **_SEMANTICS})
            record["record_sha256"] = _sha(record)
            conn.execute("INSERT INTO technical_review_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (job["id"], record["job_revision"], record["review_id"], record["entry_revision"],
                          submission.mutation_id, payload_hash, _json(record)))
            history.append(record)
        return self._response(job["id"], catalog, context, history)

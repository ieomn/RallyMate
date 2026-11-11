"""Small report navigation/aggregates, with full motion evidence read separately.

This is a presentation projection, not a proof codec. Source records are checked
by SegmentedReportStore before the scalar reducers are called. Raw event proof
remains available from report-actions; motion evidence is never truncated.
"""
from __future__ import annotations

from rallymate_scoring.technique_assessment import build_technique_assessment
from rallymate_service.training_evaluation import build_training_evaluation_with_records
from rallymate_service.analysis_report import build_analysis_report
from rallymate_service.process_evaluation import build_process_evaluation
from rallymate_service.user_demo import _post_analysis_capabilities, _ACTION_SPECS, _friendly_amplitudes, _formation_assessment


def _pick(value, keys):
    return {key: value[key] for key in keys if key in value}


def motion_navigation(raw):
    """Keep all episode identities and phases; measurements stay on demand."""
    raw = raw if isinstance(raw, dict) else {}
    result = _pick(raw, ("schema_version", "analysis_version", "method", "status", "contact_confirmed",
        "limitations_zh", "temporal_backend", "count_semantics"))
    families = {}
    for family, item in (raw.get("families") or {}).items():
        episodes = []
        for episode in item.get("episodes", []):
            navigation = _pick(episode, ("episode_id", "family", "method", "person_track_id",
                "start_ms", "peak_ms", "end_ms", "start_frame", "peak_frame", "end_frame",
                "contact_confirmed", "classification", "analysis_status", "limitations_zh"))
            navigation["phases"] = [_pick(phase, ("phase", "label_zh", "start_ms", "end_ms", "status"))
                for phase in episode.get("phases", [])]
            navigation.update(metrics={}, evidence={}, detail_status="on_demand")
            episodes.append(navigation)
        families[family] = {**_pick(item, ("status", "reason_zh", "summary")), "episodes": episodes,
                            "detail_status": "on_demand"}
    result["families"] = families
    return result


def build_overview(job, source, original_video, actions, observations, *, registry_path=None):
    from rallymate_service.segmented_report import VERSION, _require
    _require(original_video is not None and original_video["source_job_id"] == source["source_job_id"]
        and original_video["sha256"].lower() == source["video_sha256"].lower())
    summary = job["summary"]
    raw_recognition = summary.get("action_recognition") or {}
    recognition = _pick(raw_recognition, ("schema_version", "detector_version", "status", "scope",
        "candidate_count", "by_family", "supporting_motion_candidate_count", "confirmed_contact_count",
        "candidates", "review_candidates", "review_candidate_count", "review_count_semantics",
        "count_semantics", "limitations_zh"))
    recognition["motion_analysis"] = motion_navigation(raw_recognition.get("motion_analysis"))
    compact_summary = {**summary, "action_recognition": recognition}
    assessment = build_technique_assessment(compact_summary, observations, registry_path=registry_path)
    recognition = assessment["action_recognition"]
    training, validated_observations = build_training_evaluation_with_records(observations)
    names = {"FS01": "准备与分腿垫步", "FS02": "第一步启动", "FS09": "制动与重新稳定"}
    episodes = [{**_pick(action, ("event_id", "event_code", "start_ms", "end_ms", "person_track_id")),
        "video_id": source["video_id"], "source_id": source["source_job_id"],
        "name_zh": names[action["event_code"]], "indicators": [], "detail_status": "on_demand",
        "evidence_status": action["evidence_status"], "report_action": action}
        for action in actions if action["event_code"] in names]
    footwork = {"schema_version": "1.1.0", "review_version": "footwork-navigation-v1",
        "status": "available" if episodes else "unavailable", "episodes": episodes,
        "episode_count": len(episodes), "returned_episode_count": len(episodes), "is_truncated": False,
        "detail_status": "on_demand", "reason_zh": "选择动作后按页读取完整指标与来源证据。"}
    loop = summary.get("minimum_scoring_loop") or {}
    counts = loop.get("event_counts") or {}
    action_summaries = [{"event_code": code, "name_zh": name,
        "event_count": counts.get(code, 0), "detected_segments": counts.get(code, 0), "observed": counts.get(code, 0) > 0,
        "status": "not_observed" if not counts.get(code, 0) else "measured"
            if training["action_evaluations"][code]["evaluated_indicator_count"] == training["action_evaluations"][code]["total_indicator_count"]
            else "partially_measured",
        "registered_indicator_count": training["action_evaluations"][code]["total_indicator_count"],
        "measured_indicator_count": training["action_evaluations"][code]["evaluated_indicator_count"],
        "measurement_coverage_percent": round(100 * training["action_evaluations"][code]["evaluated_indicator_count"]
            / training["action_evaluations"][code]["total_indicator_count"]),
        "amplitudes": [], "summary_zh": training["action_evaluations"][code]["summary_zh"],
        "feedback_zh": "选择动作片段，逐页复核测量与来源证据。",
        "indicator_evaluations": training["action_evaluations"][code]["indicator_evaluations"],
        "performance_assessment": training["action_evaluations"][code],
        "measurement_status": "measured" if training["action_evaluations"][code]["available"] else "unavailable"}
        for code, name in names.items()]
    for action, spec in zip(action_summaries, _ACTION_SPECS):
        validity = loop.get("indicator_feature_validity") or {}
        items = [validity.get(identifier) or {} for identifier in spec["indicator_ids"]]
        total = sum(item.get("total", 0) for item in items)
        if total:
            action["measurement_coverage_percent"] = round(100 * sum(item.get("valid", 0) for item in items) / total)
        action["amplitudes"] = _friendly_amplitudes(validated_observations, spec["event_code"],
            frozenset(spec["indicator_ids"]), spec["features"])
        action["formation_assessment"] = _formation_assessment(
            measurement_available=action["performance_assessment"]["available"],
            detected_segments=action["detected_segments"], measured_indicator_count=action["measured_indicator_count"],
            expected_indicator_count=action["registered_indicator_count"],
            measurement_coverage_percent=action["measurement_coverage_percent"],
            amplitude_count=len(action["amplitudes"]), expected_amplitude_count=len(spec["features"]))
    hit_statistics, trajectory_analysis = _post_analysis_capabilities(summary, recognition)
    video = {**(summary.get("input") or {}).get("video", {}), "sha256": source["video_sha256"]}
    result = {"schema_version": "1.2.0", "result_version": "segmented-overview-v1", "job_id": job["id"],
        "video_id": source["video_id"], "status": "ready", "input": {"video": video},
        "original_video": original_video, "sam3d_mesh_status": "pending",
        "training_evaluation": training, "technique_assessment": assessment,
        "action_recognition": recognition, "footwork_review": footwork, "actions": action_summaries,
        "hit_statistics": hit_statistics, "trajectory_analysis": trajectory_analysis,
        "summary": {"job_id": job["id"], "video": video,
            "processing": _pick(summary.get("processing") or {}, ("processed_frames", "elapsed_seconds", "effective_processed_fps")),
            "scoring_state": summary.get("scoring_state"), "measurement_portfolio": summary.get("measurement_portfolio")},
        "report_delivery": {"mode": "segmented", "overview": "complete", "evidence": "on_demand",
            "motion": "on_demand", "footwork": "on_demand", "is_full_evidence_export": False},
        "scoring_state": summary.get("scoring_state"), "measurement_portfolio": summary.get("measurement_portfolio")}
    # Overview messages must not interpret deferred evidence as absent evidence.
    result["analysis_report"] = build_analysis_report(
        {**result, "action_recognition": raw_recognition, "footwork_review": {}}, summary=summary)
    result["analysis_report"]["headline_zh"] = "训练报告已生成，详细测量按动作读取"
    result["analysis_report"]["detail_status"] = "footwork_on_demand"
    return {"schema_version": VERSION, "job_id": job["id"], "source": source, "status": "complete",
        "result": result, "assessment": assessment}


def build_motion_detail(job, source, episode_id):
    from rallymate_service.segmented_report import VERSION, SegmentedReportError, _require
    summary = job["summary"]
    recognition = summary.get("action_recognition") or {}
    motion = recognition.get("motion_analysis") or {}
    matches = [(family, episode) for family, group in (motion.get("families") or {}).items()
        for episode in group.get("episodes", []) if episode.get("episode_id") == episode_id]
    if not matches:
        raise SegmentedReportError("report_motion_not_found", "运动片段不存在。", 404)
    _require(len(matches) == 1)
    family, episode = matches[0]
    _require(episode.get("family") == family and episode.get("contact_confirmed") is False
        and 0 <= episode["start_ms"] <= episode["peak_ms"] <= episode["end_ms"])
    one_motion = {**motion, "families": {family: {**motion["families"][family], "episodes": [episode]}}}
    one_summary = {**summary, "action_recognition": {**recognition, "motion_analysis": one_motion}}
    # This is explicitly one episode. Full source observations and all unknown
    # fields are delivered intact; callers retain the existing motion validators.
    process = build_process_evaluation(one_summary)
    process["detail_scope"] = {"kind": "motion_episode", "episode_id": episode_id,
        "footwork": "not_loaded", "whole_video": False}
    return {"schema_version": VERSION, "job_id": job["id"], "source": source, "status": "complete",
        "episode": {**episode, "detail_status": "loaded"}, "process_evaluation": process}

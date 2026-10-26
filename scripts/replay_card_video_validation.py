"""Re-run real source artifacts and report one-to-one action-window evidence.

Public examples can be fetched/inferred explicitly. Raw media and full outputs
stay in .codex_tmp. No web link is counted as a processed video. Annotations in
the case manifest are reviewer estimates, not certified contact ground truth.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
from typing import Any
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def normalized_sha256(path: Path) -> str:
    """Platform-independent UTF-8 text digest; raw evidence hashes stay intact."""
    return hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def overlap_fraction(candidate: dict, annotation: dict) -> float:
    duration = annotation["end_ms"] - annotation["start_ms"]
    if duration <= 0:
        raise ValueError("Annotation window must have positive duration")
    return max(0, min(candidate["end_ms"], annotation["end_ms"])
               - max(candidate["start_ms"], annotation["start_ms"])) / duration


def match_action_windows(candidates: list[dict], annotations: list[dict], *,
                         min_reference_overlap: float = .2) -> dict[str, Any]:
    """Maximum one-to-one matches; duplicate windows remain false candidates.

    This is motion-window recall, not contact recall or phase correctness.
    Absent class/phase labels are unknown, never counted as correct labels.
    """
    # Minimum-cost maximum bipartite matching. Cardinality comes first; among
    # equally sized matchings, prefer overlap and a nearby forward-swing anchor.
    # A broad preparation window must not steal a reference from its precise
    # measured swing merely because it appeared later in the input list.
    n, m = len(candidates), len(annotations)
    source, sink = n + m, n + m + 1
    graph = [[] for _ in range(n + m + 2)]

    def edge(a, b, cost):
        forward = [b, len(graph[b]), 1, cost]
        reverse = [a, len(graph[a]), 0, -cost]
        graph[a].append(forward)
        graph[b].append(reverse)
        return forward

    links = {}
    for i, candidate in enumerate(candidates):
        edge(source, i, 0)
        for j, truth in enumerate(annotations):
            overlap = overlap_fraction(candidate, truth)
            if overlap >= min_reference_overlap:
                cost = round(abs(candidate["peak_ms"] - truth["anchor_ms"]) + (1 - min(1, overlap)) * 1000)
                links[i, j] = edge(i, n + j, cost)
    for j in range(m):
        edge(n + j, sink, 0)
    while True:
        distance, previous = [float("inf")] * len(graph), [None] * len(graph)
        distance[source] = 0
        for _ in range(len(graph) - 1):
            changed = False
            for a, adjacency in enumerate(graph):
                for k, (b, _, capacity, cost) in enumerate(adjacency):
                    if capacity and distance[a] + cost < distance[b]:
                        distance[b], previous[b] = distance[a] + cost, (a, k)
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        node = sink
        while node != source:
            a, k = previous[node]
            item = graph[a][k]
            item[2] -= 1
            graph[node][item[1]][2] += 1
            node = a
    owner = {j: i for (i, j), item in links.items() if item[2] == 0}
    matches = []
    for j, i in sorted(owner.items()):
        candidate, truth = candidates[i], annotations[j]
        label = candidate.get("classification", {}).get("label")
        unknown = label in (None, "unclassified", "unclassified_swing_review")
        hand = candidate.get("racket_hand", candidate.get("racket_hand_candidate", candidate.get("wrist_side")))
        expected_hands = truth.get("eligible_wrist_sides", [])
        matches.append({"annotation_id": truth["id"], "candidate_id": candidate.get("candidate_id", candidate.get("episode_id")),
                        "action_type": truth["action_type"],
                        "overlap_fraction": round(overlap_fraction(candidate, truth), 4),
                        "anchor_error_ms": round(candidate["peak_ms"] - truth["anchor_ms"], 3),
                        "classification_status": "unknown" if unknown else "correct" if label == truth["action_type"] else "incorrect",
                        "wrist_side_status": "unknown" if hand is None or not expected_hands else "compatible" if hand in expected_hands else "incompatible",
                        "route": candidate.get("route", "primary")})
    false_indexes = sorted(set(range(len(candidates))) - set(owner.values()))
    missing_indexes = sorted(set(range(len(annotations))) - set(owner))
    count = len(matches)
    by_type = {}
    for action_type in sorted({truth["action_type"] for truth in annotations}):
        total = sum(truth["action_type"] == action_type for truth in annotations)
        hit = sum(m["action_type"] == action_type for m in matches)
        by_type[action_type] = {"annotated": total, "matched": hit, "missed": total - hit, "recall": hit / total}
    return {"annotation_count": len(annotations), "candidate_count": len(candidates), "matched_count": count,
            "missed_count": len(missing_indexes), "false_candidate_count": len(false_indexes),
            "recall": count / len(annotations) if annotations else None,
            "precision": count / len(candidates) if candidates else None,
            "by_action_type": by_type, "matches": matches,
            "unknown_classification_count": sum(m["classification_status"] == "unknown" for m in matches),
            "incompatible_wrist_side_count": sum(m["wrist_side_status"] == "incompatible" for m in matches),
            "median_absolute_anchor_error_ms": statistics.median(abs(m["anchor_error_ms"]) for m in matches) if matches else None,
            "missed_annotation_ids": [annotations[j]["id"] for j in missing_indexes],
            "false_candidates": [{key: candidates[i].get(key) for key in ("candidate_id", "start_ms", "peak_ms", "end_ms", "route")} for i in false_indexes]}


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def decode_source_timeline(video_path: Path, expected_sha256: str) -> list[dict]:
    """Read actual decoder PTS; never synthesize timestamps from a frame rate."""
    import imageio_ffmpeg
    if sha256(video_path) != expected_sha256:
        raise ValueError("Source video changed before decoder time validation")
    completed = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(video_path),
        "-map", "0:v:0", "-vf", "showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if completed.returncode:
        raise ValueError("Original video PTS decoding failed")
    timebase = re.search(r"config in time_base:\s*(\d+)/(\d+)", completed.stderr)
    if not timebase:
        raise ValueError("Decoder did not expose the source timebase")
    numerator, denominator = map(int, timebase.groups())
    if numerator <= 0 or denominator <= 0:
        raise ValueError("Decoder returned an invalid source timebase")
    rows = [{"frame_index": int(index), "pts": int(pts), "time_base_num": numerator,
             "time_base_den": denominator, "video_sha256": expected_sha256,
             "time_binding_source": "ffmpeg_showinfo_actual_decoded_frame"}
            for index, pts in re.findall(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:", completed.stderr)]
    if not rows or any(row["frame_index"] != i for i, row in enumerate(rows)):
        raise ValueError("Decoder source frame index is incomplete")
    return rows


def selected_samples(run_dir: Path):
    """Reconstruct observed samples for measurement replay, retaining gaps.

    Uses the production point and camera validators. The returned decoder
    timeline includes frames with no selected pose; callers must not replace
    this denominator with the subset that happened to yield a detection.
    """
    from collections import defaultdict
    from rallymate_features.coordinates import validated_camera_transform
    from rallymate_scoring.stroke_candidates import _sample, _associate_rackets, _stabilize_partial_torso_windows
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    provenance = summary.get("minimum_scoring_loop", {}).get("provenance", {})
    source_digest = provenance.get("video_sha256", "").lower()
    frame_digest = provenance.get("frames_sha256", "").lower()
    if (len(source_digest) != 64 or len(frame_digest) != 64
            or sha256(run_dir / "frames.jsonl") != frame_digest
            or sha256(Path(summary["input"]["video_path"])) != source_digest):
        raise ValueError("Video/frame artifact digest binding is missing or changed")
    decoded = decode_source_timeline(Path(summary["input"]["video_path"]), source_digest)
    selected = {row["processed_index"]: row.get("source_track_id")
                for row in _read_jsonl(run_dir / "primary-player.jsonl")
                if row.get("selection_status") == "selected" and row.get("identity_ambiguous") is not True}
    by_track, all_tracks, timeline = defaultdict(list), defaultdict(list), []
    epoch, previous_selected, previous_camera_epoch = 0, None, None
    for record in _read_jsonl(run_dir / "frames.jsonl"):
        frame, camera = dict(record["frame"]), record.get("camera_motion")
        source_frame = decoded[frame["index"]]
        actual_ms = source_frame["pts"] * source_frame["time_base_num"] * 1000 / source_frame["time_base_den"]
        if abs(actual_ms - frame["timestamp_ms"]) > .51:
            raise ValueError("Recorded frame time disagrees with decoded source PTS")
        timeline.append({**source_frame, "timestamp_ms": frame["timestamp_ms"]})
        if isinstance(camera, dict):
            camera_epoch, status = camera.get("reference_epoch"), camera.get("status", "unavailable")
            transform = validated_camera_transform(camera)
            if (("compensation_valid" in camera or "matrix_to_reference" in camera)
                    and (transform is None or type(camera_epoch) is not int or camera_epoch < 0
                         or status not in {"reference", "stationary", "moving"})):
                status = "unavailable"
            elif status == "moving" and transform is not None:
                status = "compensated"
            if status not in {"reference", "stationary", "compensated", "moving", "unavailable"}:
                status = "unavailable"
            frame.update(camera_motion_status=status, camera_motion=camera)
            if previous_camera_epoch is not None and camera_epoch != previous_camera_epoch:
                epoch += 1
            previous_camera_epoch = camera_epoch
        current = selected.get(frame.get("processed_index"))
        if current != previous_selected:
            epoch += 1
        previous_selected = current
        if frame.get("timestamp_source") == "fps_fallback" or frame.get("camera_motion_status") in {"moving", "unavailable"}:
            epoch += 1
        samples = [sample for pose in record.get("poses", []) if (sample := _sample(pose, frame)) is not None]
        _associate_rackets(samples, record.get("detections", []))
        for sample in samples:
            sample.selection_epoch = epoch
            if not sample.source_binding:
                sample.source_binding = {"video_sha256": source_digest,
                                         "frame_index": sample.frame_index,
                                         "person_track_id": sample.track,
                                         "selection_epoch": epoch,
                                         "camera_reference_epoch": sample.camera_reference_epoch,
                                         "pts": source_frame["pts"], "time_base_num": source_frame["time_base_num"],
                                         "time_base_den": source_frame["time_base_den"], "timestamp_ms": actual_ms,
                                         "binding_source": "verified_run_video_and_frames_sha256"}
            all_tracks[sample.track].append(sample)
            if sample.track == current:
                by_track[sample.track].append(sample)
    _stabilize_partial_torso_windows(all_tracks)
    return by_track, timeline


def measure_annotated_windows(case: dict, run_dir: Path) -> list[dict]:
    from rallymate_scoring.card_kinematics import measure_card_window
    from rallymate_scoring.card_evaluation import evaluate_card_window
    from rallymate_scoring.card_rule_catalog import load_card_rules
    rules = load_card_rules()["cards"]
    by_track, timeline = selected_samples(run_dir)
    output = []
    for annotation in case.get("stage_windows", []):
        rows = [row for row in timeline if annotation["start_ms"] <= row["timestamp_ms"] <= annotation["end_ms"]]
        samples = [sample for group in by_track.values() for sample in group
                   if annotation["start_ms"] <= sample.time <= annotation["end_ms"]]
        if not samples or len(rows) < 3:
            output.append({"stage_id": annotation["stage_id"], "status": "unavailable_no_observations"})
            continue
        sample = samples[0]
        window = {"start_ms": rows[0]["timestamp_ms"], "end_ms": rows[-1]["timestamp_ms"],
                  "person_track_id": sample.track, "selection_epoch": sample.selection_epoch,
                  "camera_reference_epoch": sample.camera_reference_epoch, "camera_compensated": sample.camera_compensated,
                  "video_sha256": case["video_sha256"], "stage_id": annotation["stage_id"],
                  "event_code": annotation["stage_id"].split("-")[0], "stage_code": annotation["stage_id"].split("-")[1],
                  "event_id": annotation["id"], "video_id": case["id"],
                  "stage_binding": {"method": "manual_anchor", "version": case["annotation_version"],
                                    "source": case["annotation_origin"], "uncertainty_ms": 150}}
        measured = measure_card_window(samples, window, rows)
        measured["annotation_id"] = annotation["id"]
        measured["annotation_semantics"] = "reviewer_estimated_stage_not_expert_certified_source_card_phase"
        measured["card_evaluations"] = [evaluate_card_window(rule, samples, window, rows) for rule in rules
                                         if rule["stage_code"] == annotation["stage_id"]]
        output.append(measured)
    return output


def replay_case(case: dict) -> dict:
    from rallymate_scoring.stroke_candidates import recognize_strokes_from_artifacts
    import cv2
    video_path, run_dir = ROOT / case["local_video"], ROOT / case["run_dir"]
    if not video_path.is_file() or not (run_dir / "frames.jsonl").is_file():
        return {"case_id": case["id"], "status": "not_run", "reason": "video_or_frame_artifact_missing"}
    digest = sha256(video_path)
    if case.get("video_sha256") and case["video_sha256"].lower() != digest:
        raise ValueError(f"Source hash mismatch for {case['id']}")
    run_summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    provenance = run_summary.get("minimum_scoring_loop", {}).get("provenance", {})
    if (provenance.get("video_sha256", "").lower() != digest
            or provenance.get("frames_sha256", "").lower() != sha256(run_dir / "frames.jsonl")):
        raise ValueError(f"Video/frame provenance mismatch for {case['id']}")
    cap = cv2.VideoCapture(str(video_path))
    meta = {"width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": cap.get(cv2.CAP_PROP_FPS), "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT))}
    ok, _ = cap.read()
    cap.release()
    if not ok:
        raise ValueError(f"Cannot decode source for {case['id']}")
    recognition = recognize_strokes_from_artifacts(run_dir / "frames.jsonl", run_dir / "primary-player.jsonl")
    fallback = recognition.get("review_candidates", [])
    annotations = case.get("annotations", [])
    primary = recognition.get("candidates", [])
    result = {"case_id": case["id"], "status": "replayed_real_observations", "split": case["split"],
              "source_video_sha256": digest, "source_metadata": meta,
              "input_artifact_sha256": {name: sha256(run_dir / name) for name in ("frames.jsonl", "primary-player.jsonl", "summary.json")},
              "pose_model_sha256": run_summary.get("models", {}).get("pose_backend", {}).get("model_sha256"),
              "recognition_status": recognition["status"], "primary_candidate_count": len(primary),
              "fallback_review_candidate_count": len(fallback),
              "diagnostics": recognition.get("diagnostics"),
              "fallback_semantics": "manual_review_windows_not_additional_confirmed_strokes_or_scores",
              "annotations": {"origin": case.get("annotation_origin"), "status": "reviewer_estimates_not_certified_ground_truth"}}
    if annotations:
        result["primary_window_metrics"] = match_action_windows(primary, annotations)
        result["with_review_fallback_metrics"] = match_action_windows(primary + fallback, annotations)
        episodes = [e for family in recognition["motion_analysis"]["families"].values() for e in family["episodes"]]
        result["estimated_phase_anchor_metrics"] = match_action_windows(episodes, annotations)
    else:
        result["window_metrics_status"] = "not_estimated_no_exhaustive_annotation"
    features_path = run_dir / "indicator-features.jsonl"
    if features_path.is_file():
        count = Counter()
        for record in _read_jsonl(features_path):
            count[f"{record.get('event_code')}:{record.get('feature_status')}"] += 1
        result["legacy_feature_status_counts"] = dict(count)
    if case.get("stage_windows"):
        result["stage_measurements"] = measure_annotated_windows(case, run_dir)
    return result


def download_public(cases: list[dict]) -> None:
    for case in cases:
        url = case.get("download_url")
        if not url:
            continue
        # Source catalog is repository-reviewed; restrict downloads to its
        # HTTPS archive.org media links and reject oversized responses.
        if not url.startswith("https://archive.org/download/MITPE.710IAP06/"):
            raise ValueError("Only the catalogued public MIT demonstration download is supported")
        path = ROOT / case["local_video"]
        if path.exists():
            if sha256(path) != case["video_sha256"]:
                raise ValueError("Existing public video has the wrong source hash")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(url, timeout=45) as response:
            data = response.read(20_000_001)
        if len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != case["video_sha256"]:
            raise ValueError("Public video size/hash validation failed")
        path.write_bytes(data)


def infer_public(cases: list[dict]) -> None:
    from rallymate_vision.contracts import PipelineRequest, ModelConfig, ProcessingConfig
    from rallymate_vision.pipeline import run_pipeline
    import torch
    torch.set_num_threads(4)
    for case in cases:
        if not case.get("download_url"):
            continue
        request = PipelineRequest("card-" + case["id"], ROOT / case["local_video"], ROOT / case["run_dir"],
            ModelConfig(ROOT / "models/yolo26n.pt", ROOT / "models/yolo26n-pose.pt", detect_imgsz=640, pose_imgsz=640, device="cpu"),
            ProcessingConfig(max_players=1, write_annotated_video=False),
            upstream_metadata={"source_url": case["source_url"], "purpose": "real_video_validation_not_grade_calibration"})
        run_pipeline(request)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/fixtures/card-video-cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".codex_tmp/card-video-validation-20261022/replay-results.json")
    parser.add_argument("--download-public", action="store_true")
    parser.add_argument("--infer-public", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(args.manifest.read_text(encoding="utf-8"))
    cases = [case for case in catalog["cases"] if case.get("local_video")]
    if args.download_public:
        download_public(cases)
    if args.infer_public:
        infer_public(cases)
    module_names = ("stroke_candidates.py", "stroke_review_fallback.py", "temporal_recognition.py",
                    "card_rule_catalog.py", "card_kinematics.py", "card_evaluation.py", "data/card_rules_v1.json")
    result = {"schema_version": "card-video-validation-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "case_manifest_sha256": sha256(args.manifest), "replay_script_sha256": sha256(Path(__file__)),
              "case_manifest_normalized_sha256": normalized_sha256(args.manifest),
              "replay_script_normalized_sha256": normalized_sha256(Path(__file__)),
              "catalogue_digest_kind": "raw_file_sha256", "module_digest_kind": "raw_file_sha256",
              "normalized_digest_kind": "utf8_lf_sha256",
              "annotation_policy": catalog["annotation_policy"],
              "matching_policy": {"method": "maximum_one_to_one", "minimum_reference_window_overlap": .2,
                                  "anchor_semantics": "estimated_forward_swing_anchor_not_confirmed_contact"},
              "module_sha256": {name: sha256(ROOT / "src/rallymate_scoring" / name) for name in module_names},
              "module_normalized_sha256": {name: normalized_sha256(ROOT / "src/rallymate_scoring" / name) for name in module_names},
              "cases": [replay_case(case) for case in cases]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for case in result["cases"]:
        print(json.dumps({key: case.get(key) for key in ("case_id", "status", "primary_candidate_count", "fallback_review_candidate_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Subject-scoped aggregation; a legacy primary_player_id is not reidentification."""
from collections import defaultdict
from collections.abc import Mapping


def subject_reference(selected, native):
    proof = selected.get("subject_identity")
    if isinstance(proof, Mapping):
        identifier = proof.get("subject_id")
        if (proof.get("version") != "subject-lock-v1" or not isinstance(identifier, str)
                or not identifier or proof.get("status") not in {"locked", "reidentified"}
                or proof.get("source_track_id") != native.get("person_track_id")
                or proof.get("source_track_id") != selected.get("source_track_id")
                or not isinstance(proof.get("evidence"), list) or not proof["evidence"]):
            return None
        return {"subject_id": identifier, "identity_basis": "target_lock",
                "identity_status": proof["status"]}
    # Old artifacts used primary_player_id=1 for every selected person. Neither
    # that ID nor two nonoverlapping tracks establishes that they are one person.
    track, epoch = native.get("person_track_id"), native.get("selection_epoch")
    if type(track) is not int or type(epoch) is not int:
        return None
    return {"subject_id": f"legacy-primary-{selected.get('primary_player_id')}-track-{track}-epoch-{epoch}",
            "identity_basis": "unlinked_source_track", "identity_status": "track_local"}


def select_report_subject(segments):
    """Keep the first independently selected target, never the highest score."""
    observed = defaultdict(list)
    for segment in segments:
        for row in segment:
            observed[row["subject_id"]].append(row)
    summaries = []
    for identifier, rows in observed.items():
        summaries.append({"subject_id": identifier, "identity_basis": rows[0]["identity_basis"],
                          "start_ms": round(min(r["time"] for r in rows), 3),
                          "end_ms": round(max(r["time"] for r in rows), 3),
                          "observed_frames": len(rows),
                          "source_track_ids": sorted({r["track"] for r in rows})})
    summaries.sort(key=lambda s: (s["start_ms"], s["subject_id"]))
    locked = [s for s in summaries if s["identity_basis"] == "target_lock"]
    candidates = locked or summaries
    selected = candidates[0]["subject_id"] if candidates else None
    return {"version": "subject-scoped-assessment-v1", "selected_subject_id": selected,
            "selection_basis": "first_locked_target" if locked else "first_unlinked_source_track",
            "cross_subject_aggregation": False, "subjects": summaries}


def reviewed_subject_timeline(document, *, video_sha256, native_sha256, frames_sha256):
    """A separate, content-bound re-selection never rewrites historical poses.

    This optional sidecar applies only to the current tennis assessment. Legacy
    formal artifacts keep their original primary timeline and evidence hashes.
    """
    if (not isinstance(document, Mapping) or document.get("version") != "tennis-subject-selection-v1"
            or document.get("source_video_sha256") != video_sha256
            or document.get("source_artifact_sha256") != {"sam3d_frames.jsonl": native_sha256, "frames.jsonl": frames_sha256}
            or not isinstance(document.get("timeline"), list)
            or not all(isinstance(row, Mapping) for row in document["timeline"])):
        raise ValueError("reviewed_subject_selection_source_mismatch")
    return document["timeline"]

"""CPU-only, semantic evidence-frame variation across retained pipeline trials.

This audits already written score artifacts. It is not proof validation,
independent accuracy validation, or authorization to enable batch inference.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import itertools
import json
import math
from pathlib import Path


def frame_distance(left, right):
    """Symmetric nearest-frame distance; list insertion is not index shift."""
    if any(type(value) is not int or value < 0 for value in (*left, *right)):
        raise ValueError("Evidence frames must be nonnegative integers")
    a, b = set(left), set(right)
    if not a and not b:
        return 0
    if not a or not b:
        return None
    return max(max(min(abs(x - y) for y in b) for x in a),
               max(min(abs(y - x) for x in a) for y in b))


def scores(path):
    result = {}
    with path.open(encoding="utf8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["event_id"], row["person_track_id"], row["indicator_id"])
            if key in result:
                raise ValueError("Duplicate score identity")
            result[key] = row
    return result


def feature_map(row):
    result, occurrences = {}, Counter()
    for item in row.get("feature", {}).get("items", []):
        name = item.get("feature_name")
        occurrence = occurrences[name]
        occurrences[name] += 1
        result[(name, occurrence)] = item
    return result


def compare_scores(reference, candidate):
    if reference.keys() != candidate.keys():
        raise ValueError("Score identity set changed")
    changed, score_evidence, value_changes = [], [], defaultdict(list)
    total_features = 0
    for key, old in reference.items():
        new = candidate[key]
        a, b = feature_map(old), feature_map(new)
        if a.keys() != b.keys():
            raise ValueError("Feature identity set changed")
        total_features += len(a)
        for feature_key, before in a.items():
            after = b[feature_key]
            af, bf = before.get("source_frames", []), after.get("source_frames", [])
            if af != bf:
                changed.append({"event_id": key[0], "person_track_id": key[1], "indicator_id": key[2],
                    "feature_name": feature_key[0], "occurrence": feature_key[1],
                    "old_frames": af, "new_frames": bf, "set_changed": set(af) != set(bf),
                    "removed_frames": sorted(set(af) - set(bf)), "added_frames": sorted(set(bf) - set(af)),
                    "symmetric_nearest_frame_distance": frame_distance(af, bf)})
            av, bv = before.get("value"), after.get("value")
            if type(av) in (int, float) and type(bv) in (int, float) and av != bv:
                if not math.isfinite(av) or not math.isfinite(bv) or before.get("unit") != after.get("unit"):
                    raise ValueError("Invalid comparable feature values")
                value_changes[before.get("unit")].append({"event_id": key[0], "indicator_id": key[2],
                    "feature_name": feature_key[0], "before": av, "after": bv, "abs_delta": abs(av - bv)})
        if old.get("evidence") != new.get("evidence"):
            score_evidence.append({"event_id": key[0], "person_track_id": key[1], "indicator_id": key[2]})
    return {"score_rows": len(reference), "feature_items": total_features,
            "feature_frame_list_changes": len(changed), "feature_frame_set_changes": sum(row["set_changed"] for row in changed),
            "feature_frame_distance_counts": dict(Counter(str(row["symmetric_nearest_frame_distance"]) for row in changed)),
            "changed_score_evidence_rows": score_evidence,
            "feature_frame_changes": changed,
            "value_changes_by_unit": {unit: {"changed_values": len(values),
                "largest": max(values, key=lambda row: row["abs_delta"])} for unit, values in value_changes.items()}}


def audit(root):
    probe = json.loads((root / "probe.json").read_text(encoding="utf8"))
    trials = probe["trials"]
    data = {trial["trial"]: scores(Path(trial["output_dir"]) / "scores.jsonl") for trial in trials}
    pairs = []
    for old, new in itertools.combinations(trials, 2):
        pairs.append({"reference_trial": old["trial"], "candidate_trial": new["trial"],
            "reference_batch_frames": old["batch_frames"], "candidate_batch_frames": new["batch_frames"],
            "kind": "repeat_same_batch" if old["batch_frames"] == new["batch_frames"] else "cross_batch",
            **compare_scores(data[old["trial"]], data[new["trial"]])})
    return {"schema_version": "sam3d-evidence-variation-audit-v1", "source_probe": str(root / "probe.json"),
            "scoring_accuracy_validated": False, "proof_transport_validated": False,
            "semantics": "raw artifact comparison by event/person/indicator/feature; frame distance compares sets, not list positions",
            "pairs": pairs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = audit(args.input_root.resolve())
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    for pair in value["pairs"]:
        print(json.dumps({key: val for key, val in pair.items() if key not in
                          ("feature_frame_changes", "changed_score_evidence_rows")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

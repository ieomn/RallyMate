import copy
import unittest

from rallymate_scoring.model_measurement_contract import (
    MODEL_MEASUREMENT_PROVENANCE, model_measurement_provenance,
    validated_model_window, validated_geometry_mask, has_model_measurement_context,
)


def model_window_fixture():
    frames = [9, 10, 11, 12]
    local = [300, 333, 367, 400]
    source = [75300, 75333, 75367, 75400]
    return {"measurement_provenance": dict(MODEL_MEASUREMENT_PROVENANCE),
        "source_window": {"start_ms": 300, "end_ms": 400, "source_frames": frames,
            "video_timestamps_ms": local, "source_timestamps_ms": source,
            "person_track_id": 1, "selection_epoch": 2, "camera_reference_epoch": 3, "video_sha256": "a"*64,
            "source_bindings": [{"video_sha256": "a"*64, "frame_index": frame, "processed_index": index,
                "pts": source[index], "time_base_num": 1, "time_base_den": 1000, "timestamp_ms": source[index],
                "person_track_id": 1, "selection_epoch": 2, "camera_reference_epoch": 3}
                for index, frame in enumerate(frames)]},
        "geometry_mask": {"left_hip": [True, True, False, True], "right_hip": [True]*4}}


class ModelMeasurementContractTests(unittest.TestCase):
    def test_nonzero_source_clock_and_subset_preserve_original_proof(self):
        payload = model_window_fixture()
        before = copy.deepcopy(payload)
        parent = validated_model_window(payload, person_track_id=1)
        self.assertEqual(parent, payload["source_window"])
        child = copy.deepcopy(payload)
        for key in ("source_frames", "video_timestamps_ms", "source_timestamps_ms", "source_bindings"):
            child["source_window"][key] = child["source_window"][key][1:]
        child["source_window"]["start_ms"] = 333
        self.assertIsNotNone(validated_model_window(child, parent_window=parent))
        child["source_window"]["source_bindings"][1]["processed_index"] += 100
        self.assertIsNone(validated_model_window(child, parent_window=parent))
        self.assertEqual(payload, before)

    def test_each_decoder_and_selection_binding_is_required(self):
        for field, value in (("video_sha256", "b"*64), ("person_track_id", 2), ("person_track_id", True),
                             ("selection_epoch", 5), ("camera_reference_epoch", 8), ("pts", 0),
                             ("timestamp_ms", 123), ("frame_index", 999), ("time_base_den", 0), ("processed_index", 0)):
            changed = model_window_fixture()
            changed["source_window"]["source_bindings"][2][field] = value
            self.assertIsNone(validated_model_window(changed), field)
        changed = model_window_fixture()
        del changed["source_window"]["source_bindings"][2]["processed_index"]
        self.assertIsNone(validated_model_window(changed))

    def test_geometry_coverage_does_not_become_confidence_or_visibility(self):
        payload = model_window_fixture()
        window = validated_model_window(payload)
        self.assertEqual(validated_geometry_mask(payload["geometry_mask"], window, required_joints=["left_hip"]), payload["geometry_mask"])
        self.assertIsNone(validated_geometry_mask({"left_hip": [1, 1, 0, 1]}, window))
        self.assertIsNone(validated_geometry_mask(payload["geometry_mask"], window, required_joints=["left_heel"]))
        for key in ("scoring_allowed", "confidence_available", "visibility_verified"):
            changed = copy.deepcopy(payload)
            changed["measurement_provenance"][key] = True
            self.assertIsNone(validated_model_window(changed))
        self.assertFalse(model_measurement_provenance({**MODEL_MEASUREMENT_PROVENANCE, "scoring_allowed": 0}))

    def test_source_clock_cannot_jump_or_diverge_from_playback(self):
        for amount in (1.1, 1000):
            changed = model_window_fixture()
            changed["source_window"]["source_timestamps_ms"][2] += amount
            changed["source_window"]["source_bindings"][2]["timestamp_ms"] += amount
            changed["source_window"]["source_bindings"][2]["pts"] += amount
            self.assertIsNone(validated_model_window(changed))

    def test_continuity_and_output_allowlist(self):
        for defect in ("frame_jump", "processed_jump", "timebase_change"):
            payload = model_window_fixture()
            for i in (2, 3):
                if defect == "frame_jump":
                    payload["source_window"]["source_frames"][i] += 1
                    payload["source_window"]["source_bindings"][i]["frame_index"] += 1
                elif defect == "processed_jump": payload["source_window"]["source_bindings"][i]["processed_index"] += 1
                else:
                    payload["source_window"]["source_bindings"][i]["time_base_num"] = 2
                    payload["source_window"]["source_bindings"][i]["time_base_den"] = 2000
            self.assertIsNone(validated_model_window(payload), defect)
        payload = model_window_fixture()
        payload["source_window"]["untrusted_extra"] = "discard"
        payload["source_window"]["source_bindings"][0]["untrusted_extra"] = "discard"
        normalized = validated_model_window(payload)
        self.assertNotIn("untrusted_extra", normalized)
        self.assertNotIn("untrusted_extra", normalized["source_bindings"][0])
        self.assertFalse(has_model_measurement_context({"measurement_provenance": {"observation_semantics": []}}))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import unittest

from rallymate_scoring.indicator_assessment import build_indicator_assessment
from rallymate_scoring.source_assessment import ARTIFACT_VERSION, source_binding
from rallymate_service.source_assessment import project_source_assessment, SEMANTICS
from rallymate_features.source_alignment import JOINTS


PROVENANCE = {"pose_source": "sam3d", "observation_semantics": "monocular_model_estimate",
              "assessment_status": "measurement_only", "scoring_allowed": False,
              "confidence_available": False, "visibility_verified": False}
HEAD = ["nose", "left_ear", "right_ear"]


def payload(count=4):
    video_times = [i * 40 for i in range(count)]
    source_times = [1000 + t for t in video_times]
    bindings = [{"video_sha256": "a" * 64, "frame_index": 10 + i, "processed_index": i,
                 "pts": 1000 + t, "time_base_num": 1, "time_base_den": 1000, "timestamp_ms": 1000 + t,
                 "person_track_id": 217, "selection_epoch": 2, "camera_reference_epoch": 3}
                for i, t in enumerate(video_times)]
    return {"measurement_provenance": copy.deepcopy(PROVENANCE),
            "source_window": {"start_ms": 0, "end_ms": video_times[-1], "source_frames": list(range(10, 10 + count)),
                              "video_timestamps_ms": video_times, "source_timestamps_ms": source_times,
                              "source_bindings": bindings, "person_track_id": 217, "selection_epoch": 2,
                              "camera_reference_epoch": 3, "video_sha256": "a" * 64},
            "geometry_mask": {name: [True] * count for name in HEAD}}


def fixture():
    event = {"event_id": "fs01-model", "event_code": "FS01", "video_id": "model-video", "person_track_id": 1,
             "start_ms": 0, "end_ms": 120, "key_phases_ms": {"takeoff_proxy_ms": 80},
             "track_diagnostics": {"source_track_ids": [217]}, **payload()}
    geometry = {"scope": "inclusive_window_all_sampled_source_frames", "required_joint_names": HEAD,
                "valid_frame_count": 3, "total_frame_count": 3, "fraction": 1., "passes": True,
                "threshold": .7, "operator": ">=", "semantics": "model_estimate_geometry_not_verified_visibility",
                "visibility_verified": False, "source_visibility_70_percent_verified": None}
    measurement = {"feature_name": "head_center_mean_speed_frame_long_edge_s", "name_zh": "候选头部速度",
                   "value": 0., "unit": "frame_long_edge/s", "status": "measured", "valid": True,
                   "source_ref": "CARD-FS:word/document.xml:P0080", "window_ms": {"start_ms": 0, "end_ms": 80},
                   "source_frames": [10, 11, 12], "definition": "adjacent valid model geometry only",
                   "coverage": geometry, "reason": "observed_measurement", **payload(3),
                   "evidence": {"gaze_evaluated": False, "incoming_ball_alignment_evaluated": False,
                                "technical_stability_evaluated": False,
                                "window_scope": "FS01_candidate_start_to_takeoff_proxy_not_confirmed_pre_split_stage"}}
    core = {**geometry, "valid_frame_count": 4, "total_frame_count": 4}
    item = {"indicator_id": "FS01-M01", "source_rule_gate": {"pose_coverage": core, "complete_source_rule_verified": False},
            "source_measurements": [measurement], "limitations_zh": []}
    record = {"event_id": event["event_id"], "event_code": "FS01", "video_id": "model-video", "person_track_id": 1,
              "indicators": [item], **payload()}
    artifact = {"version": ARTIFACT_VERSION, "video_id": "model-video", "video_sha256": "a" * 64,
                "source_binding": source_binding(), "score_semantics": SEMANTICS,
                "technical_grade": None, "technical_score_0_to_100": None, "records": [record]}
    return artifact, [event]


def project(artifact, events):
    return project_source_assessment(artifact, events, video_id="model-video", duration_ms=200, video_sha256="a" * 64)


def head_window(report):
    return next(row for row in report["indicators"] if row["indicator_id"] == "FS01-M01")["windows"][0]


class SourceModelMeasurementTests(unittest.TestCase):
    def test_zero_model_geometry_survives_without_forging_original_visibility_or_grade(self):
        artifact, events = fixture()
        report = project(artifact, events)
        window = head_window(report)
        metric = window["measurements"][0]
        self.assertEqual(metric["status"], "measured")
        self.assertEqual(metric["value"], 0.)
        self.assertEqual(metric["measurement_provenance"], PROVENANCE)
        self.assertIsNone(window["visibility"]["valid_frame_ratio"])
        self.assertEqual(window["visibility"]["status"], "unavailable")
        self.assertFalse(window["visibility"]["visibility_verified"])
        self.assertEqual(metric["model_geometry_coverage"]["fraction"], 1.)
        self.assertIsNone(metric["model_geometry_coverage"]["source_visibility_70_percent_verified"])
        assessment = build_indicator_assessment({"job_id": "model-video"}, source_assessment=report)
        self.assertEqual(assessment["counts"]["formal_graded"], 0)
        observation = next(row for row in assessment["observations"] if row.get("indicator_id") == "FS01-M01")
        self.assertEqual(observation["measurement_provenance"], PROVENANCE)
        self.assertFalse(observation["full_source_rule_satisfied"])
        self.assertEqual(observation["source_window"], metric["source_window"])

    def test_bad_source_binding_or_model_promotion_never_falls_back_to_confidence_path(self):
        for defect in ("scoring", "event_provenance", "pts", "track", "video", "mask", "geometry_low", "window", "source_tracks", "frames", "record", "visible"):
            artifact, events = fixture()
            measurement = artifact["records"][0]["indicators"][0]["source_measurements"][0]
            if defect == "scoring": measurement["measurement_provenance"]["scoring_allowed"] = True
            elif defect == "event_provenance": events[0].pop("measurement_provenance")
            elif defect == "pts": measurement["source_window"]["source_bindings"][1]["pts"] += 40
            elif defect == "track": measurement["source_window"]["source_bindings"][1]["person_track_id"] = 2
            elif defect == "video": measurement["source_window"]["video_sha256"] = "b" * 64
            elif defect == "mask": measurement["geometry_mask"]["nose"] = [True]
            elif defect == "geometry_low": measurement["geometry_mask"]["nose"] = [False, False, True]
            elif defect == "window": measurement["window_ms"]["end_ms"] = 120
            elif defect == "source_tracks": events[0]['track_diagnostics']['source_track_ids'] = [1, 217]
            elif defect == "frames": measurement['source_frames'] = [10, 11, 99]
            elif defect == "record": artifact['records'][0]['source_window']['source_bindings'][0]['processed_index'] = 7
            elif defect == "visible": measurement['coverage']['source_visibility_70_percent_verified'] = True
            report = project(artifact, events)
            indicator = next(row for row in report["indicators"] if row["indicator_id"] == "FS01-M01")
            self.assertFalse(any(m["status"] == "measured" for w in indicator["windows"] for m in w["measurements"]), defect)

    def test_indicator_consumer_rechecks_model_contract_and_retains_geometry_semantics(self):
        artifact, events = fixture()
        for defect in ("confidence", "binding", "source_visibility"):
            report = project(artifact, events)
            metric = head_window(report)["measurements"][0]
            if defect == "confidence": metric["measurement_provenance"]["confidence_available"] = True
            elif defect == "binding": metric["source_window"]["source_bindings"][0]["selection_epoch"] = 8
            else: metric["model_geometry_coverage"]["source_visibility_70_percent_verified"] = True
            result = build_indicator_assessment({"job_id": "model-video"}, source_assessment=report)
            self.assertFalse(any(row.get("indicator_id") == "FS01-M01" for row in result["observations"]), defect)

    def test_compact_features_preserve_unknown_confidence_and_source_identity(self):
        record = {'event_id': 'fs01-model', 'event_code': 'FS01', 'video_id': 'model-video', 'person_track_id': 1,
                  'indicator_id': 'FS01-M01', 'track_diagnostics': {'source_track_ids': [217]}, **payload(),
                  'quality_gate': {'scoring_allowed': False, 'measurement_allowed': True}, 'provenance': {'video_sha256': 'a' * 64},
                  'features': [{'feature_name': 'body_center_speed_body_s', 'value': 0., 'valid': True, 'confidence': None,
                                'unit': 'body/s', 'source_frames': [10, 11, 12], **payload(3)}]}
        record['geometry_mask'].update({name: [True] * 4 for name in JOINTS})
        record['features'][0]['geometry_mask'].update({name: [True] * 3 for name in JOINTS})
        result = build_indicator_assessment({'job_id': 'model-video'}, [record])
        self.assertEqual(len(result['observations']), 1)
        observation = result['observations'][0]
        self.assertEqual(observation['value'], 0.)
        self.assertEqual(observation['person_track_id'], 1)
        self.assertEqual(observation['source_window']['person_track_id'], 217)
        self.assertFalse(observation['scoring_allowed'])
        self.assertIsNone(observation['source_visibility_70_percent_verified'])
        for defect in ('confidence', 'parent', 'outside', 'mask', 'missing_marker', 'source_track', 'quality_gate'):
            bad = copy.deepcopy(record)
            feature = bad['features'][0]
            if defect == 'confidence': feature['confidence'] = 1.
            elif defect == 'parent': bad['source_window']['selection_epoch'] = 9
            elif defect == 'outside': feature['source_frames'] = [10, 99]
            elif defect == 'mask': feature['geometry_mask']['nose'][0] = 1
            elif defect == 'missing_marker': feature.pop('measurement_provenance')
            elif defect == 'source_track': bad['track_diagnostics']['source_track_ids'] = [1]
            elif defect == 'quality_gate': bad['quality_gate']['measurement_allowed'] = False
            self.assertEqual(build_indicator_assessment({'job_id': 'model-video'}, [bad])['observations'], [], defect)

    def test_source_transport_exports_only_known_fields(self):
        artifact, events = fixture()
        measurement = artifact['records'][0]['indicators'][0]['source_measurements'][0]
        measurement['measurement_provenance']['private_token'] = 'never-export'
        measurement['source_window']['private_path'] = 'never-export'
        measurement['source_window']['source_bindings'][0]['private_token'] = 'never-export'
        report = project(artifact, events)
        metric = head_window(report)['measurements'][0]
        self.assertEqual(metric['status'], 'measured')
        self.assertNotIn('private_token', metric['measurement_provenance'])
        self.assertNotIn('private_path', metric['source_window'])
        self.assertNotIn('private_token', metric['source_window']['source_bindings'][0])

    def test_only_whitelisted_single_side_proxy_can_supply_actual_required_joints(self):
        record = {'event_id': 'fs02-model', 'event_code': 'FS02', 'video_id': 'model-video', 'person_track_id': 1,
            'indicator_id': 'FS02-M02', 'track_diagnostics': {'source_track_ids': [217]}, **payload(),
            'quality_gate': {'scoring_allowed': False, 'measurement_allowed': True}, 'provenance': {'video_sha256': 'a' * 64},
            'features': [{'feature_name': 'first_step_displacement_body', 'value': 0., 'valid': True,
                'confidence': None, 'unit': 'body', 'source_frames': [10, 11, 12], 'selected_side': 'left',
                'required_joints_used': ['left_hip', 'right_hip', 'left_ankle'], **payload(3)}]}
        record['geometry_mask'].update({name: [name != 'right_ankle'] * 4 for name in JOINTS})
        feature = record['features'][0]
        feature['geometry_mask'].update({name: [name != 'right_ankle'] * 3 for name in JOINTS})
        result = build_indicator_assessment({'job_id': 'model-video'}, [record])
        self.assertEqual(result['observations'][0]['selected_side'], 'left')
        self.assertEqual(result['observations'][0]['required_joints_used'], feature['required_joints_used'])
        for change in ({'selected_side': 'right'}, {'required_joints_used': ['left_hip', 'left_ankle']},
                       {'feature_name': 'stance_width_body'}, {'confidence': 1.}):
            bad = copy.deepcopy(record); bad['features'][0].update(change)
            self.assertEqual(build_indicator_assessment({'job_id': 'model-video'}, [bad])['observations'], [])

    def test_compact_model_features_require_joint_intersection_at_existing_half_window_gate(self):
        for selected in (False, True):
            for case, expected in (('disjoint', 0), ('below_half', 0), ('exact_half', 1)):
                with self.subTest(selected_side=selected, case=case):
                    required = ['left_hip', 'right_hip'] + (['left_ankle'] if selected else [])
                    feature = {'feature_name': 'first_step_displacement_body' if selected else 'hip_center_speed_body_s',
                               'value': 0., 'valid': True, 'confidence': None,
                               'unit': 'body' if selected else 'body/s', 'source_frames': [10, 13], **payload()}
                    if selected:
                        feature.update(selected_side='left', required_joints_used=required)
                    masks = {name: [True] * 4 for name in JOINTS}
                    masks['right_ankle'] = [False] * 4
                    for name in required:
                        masks[name] = [True, case != 'below_half', False, False]
                    if case == 'disjoint':
                        masks['right_hip'] = [False, False, True, True]
                    feature['geometry_mask'] = copy.deepcopy(masks)
                    record = {'event_id': 'model-coverage', 'event_code': 'FS02' if selected else 'FS09',
                              'indicator_id': 'FS02-M02' if selected else 'FS09-M01', 'video_id': 'model-video',
                              'person_track_id': 1, 'track_diagnostics': {'source_track_ids': [217]}, **payload(),
                              'geometry_mask': masks, 'quality_gate': {'scoring_allowed': False, 'measurement_allowed': True},
                              'provenance': {'video_sha256': 'a' * 64}, 'features': [feature]}
                    result = build_indicator_assessment({'job_id': 'model-video'}, [record])
                    self.assertEqual(len(result['observations']), expected)
                    if expected:
                        self.assertEqual(result['observations'][0]['measurement_provenance'], PROVENANCE)
                        self.assertFalse(result['observations'][0]['scoring_allowed'])
                        self.assertIsNone(result['observations'][0]['source_visibility_70_percent_verified'])

    def test_transition_keeps_complete_cross_event_proof_and_checks_successor(self):
        artifact, events = fixture()
        full = payload(8)
        full['geometry_mask'] = {name: [True] * 8 for name in JOINTS}
        def section(first, last):
            value = copy.deepcopy(full)
            window = value['source_window']
            for key in ('source_frames', 'source_timestamps_ms', 'video_timestamps_ms', 'source_bindings'):
                window[key] = window[key][first:last + 1]
            window['start_ms'], window['end_ms'] = first * 40, last * 40
            value['geometry_mask'] = {name: values[first:last + 1] for name, values in value['geometry_mask'].items()}
            return value
        event = events[0]
        event.update(section(0, 3)); event['key_phases_ms'] = {'landing_proxy_ms': 80}
        successor = {**copy.deepcopy(event), 'event_id': 'fs02-next', 'event_code': 'FS02', 'start_ms': 160, 'end_ms': 280, **section(4, 7)}
        events.append(successor)
        record = artifact['records'][0]; record.update(section(0, 3))
        item = record['indicators'][0]; item['indicator_id'] = 'FS01-M05'
        item['event_association'] = {'source_event_id': event['event_id'], 'successor_event_id': successor['event_id'],
            'anchor_phase_key': 'landing_proxy_ms', 'anchor_ms': 80, 'successor_start_ms': 160,
            'successor_event_code': 'FS02', 'link_measurement_status': 'measured'}
        metric = item['source_measurements'][0]; metric.update(section(2, 4))
        metric.update(feature_name='landing_proxy_to_next_fs02_ms', value=80, unit='ms', source_ref='CARD-FS:word/document.xml:P0220',
                      source_frames=[12, 13, 14], window_ms={'start_ms': 80, 'end_ms': 160})
        metric['coverage']['required_joint_names'] = list(JOINTS)
        def actual():
            report = project_source_assessment(artifact, events, video_id='model-video', duration_ms=300, video_sha256='a' * 64)
            window = next(row for row in report['indicators'] if row['indicator_id'] == 'FS01-M05')['windows'][0]
            return report, window['measurements'][0]
        report, measured = actual()
        self.assertEqual(measured['status'], 'measured')
        self.assertEqual(measured['source_window']['end_ms'], 160)
        self.assertEqual(build_indicator_assessment({'job_id': 'model-video'}, source_assessment=report)['counts']['source_aligned_measured'], 1)
        successor['source_window']['source_bindings'][0]['pts'] += 1
        self.assertEqual(actual()[1]['status'], 'unavailable')


if __name__ == "__main__":
    unittest.main()

"""Box proximity cannot identify the grip when a head or both arms conflict."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from rallymate_scoring.racket_geometry import box_limb_evidence, forward_ray_intersects_box
from rallymate_scoring.stroke_candidates import Sample, _associate_rackets, _head_conflict_directional_support


def sample(points):
    return Sample(100, 3, 1, (0, 0), 100,
                  {k: (p[0] / 100, p[1] / 100) for k, p in points.items()}, set(),
                  raw_points_px=points, raw_wrists_px={s: points[s + "_wrist"] for s in ("left", "right")},
                  raw_scale=100, timestamp_source="decoder_pts")


def associate(points, box):
    row = sample(points)
    _associate_rackets([row], [{"class_name": "racket", "confidence": .9,
                                "track_id": 7, "bbox_px": box}])
    return row


class RacketGeometryTests(unittest.TestCase):
    def test_forward_ray_uses_beyond_wrist_not_unbounded_line(self):
        self.assertTrue(forward_ray_intersects_box((0, 0), (10, 0), [15, -2, 20, 2]))
        self.assertFalse(forward_ray_intersects_box((0, 0), (10, 0), [-20, -2, -15, 2]))
        self.assertTrue(forward_ray_intersects_box((0, 10), (0, 0), [-2, -20, 2, -15]))
        self.assertFalse(forward_ray_intersects_box((0, 10), (0, 0), [2, -20, 4, -15]))
        self.assertIsNone(forward_ray_intersects_box((0, 0), (0, 0), [1, 1, 2, 2]))

    def test_unique_forearm_support_is_separate_from_legacy_nearest_proximity(self):
        points = {"left_wrist": (20, 0), "left_elbow": (40, 0),
                  "right_wrist": (15, 8), "right_elbow": (0, 8)}
        row = associate(points, [30, -2, 50, 12])
        self.assertEqual(row.racket_sides, {"left"})
        proof = row.racket_observations[0]["limb_geometry"]
        self.assertEqual(proof["forearm_ray_box_intersection"], {"left": False, "right": True})
        self.assertLess(row.racket_observations[0]["wrist_box_distance_torso"]["left"],
                        row.racket_observations[0]["wrist_box_distance_torso"]["right"])
        # Mirror coordinates and names: there is no preference for anatomical
        # right, screen-right, a fast arm, a reviewed type, or a higher score.
        swap = lambda k: k.replace("left", "tmp").replace("right", "left").replace("tmp", "right")
        mirrored = {swap(k): (-p[0], p[1]) for k, p in points.items()}
        mirrored_row = associate(mirrored, [-50, -2, -30, 12])
        self.assertEqual(mirrored_row.racket_sides, {"right"})
        self.assertEqual(mirrored_row.racket_observations[0]["limb_geometry"]["supported_motion_wrists"], ["left"])

    def test_flexed_wrist_conflict_stays_unavailable_instead_of_inventing_a_grip(self):
        points = {"left_wrist": (20, 0), "left_elbow": (40, 0),
                  "right_wrist": (15, 8), "right_elbow": (40, 8)}
        row = associate(points, [30, -2, 50, 12])
        self.assertEqual(row.racket_sides, {"left"})  # legacy weak proximity remains reviewable
        self.assertEqual(row.racket_observations[0]["limb_geometry"]["supported_motion_wrists"], [])
        self.assertEqual(len(row.racket_observations), 1)  # retain the actual object for review

    def test_two_handed_nearby_wrists_remain_shared_not_primary_grip_order(self):
        points = {"left_wrist": (10, 0), "left_elbow": (0, 0),
                  "right_wrist": (15, 8), "right_elbow": (0, 8)}
        row = associate(points, [20, -2, 40, 12])
        from rallymate_scoring.stroke_analysis import racket_hand_evidence
        self.assertIsNone(racket_hand_evidence([row])["hand"])
        self.assertEqual(row.racket_observations[0]["association"], "paired_wrist_racket_proximity")
        self.assertEqual(row.racket_observations[0]["wrist_sides"], ["left", "right"])

    def test_profile_head_cluster_is_ambiguous_without_padding_or_one_nose_shortcut(self):
        points = {"nose": (-1, 5), "left_eye": (-2, 0), "right_eye": (3, 0),
                  "left_ear": (5, 1), "right_ear": (12, 1),
                  "left_wrist": (20, 30), "left_elbow": (0, 40),
                  "right_wrist": (50, 30), "right_elbow": (40, 40)}
        row = associate(points, [0, -10, 35, 20])
        self.assertEqual(row.racket_sides, {"left"})
        self.assertEqual(row.racket_observations[0]["semantic_status"], "head_box_ambiguous")
        self.assertEqual(row.racket_observations[0]["limb_geometry"]["contained_head_landmarks"],
                         ["right_eye", "left_ear", "right_ear"])
        sparse = {k: v for k, v in points.items() if "eye" not in k and "ear" not in k}
        proof = box_limb_evidence(sparse, [0, -10, 35, 20], ["left"])
        self.assertFalse(proof["head_geometry_available"])
        self.assertFalse(proof["head_cluster_overlap_without_wrist"])

        # A complete profile head can place both ears inside while its eyes
        # and nose lie outside. No image-specific pixel padding is introduced.
        profile = deepcopy(points)
        profile['right_eye'] = (-1, 0)
        proof = box_limb_evidence(profile, [0, -10, 35, 20], ["left", "right"])
        self.assertTrue(proof["head_cluster_overlap_without_wrist"])
        del profile['nose']
        self.assertFalse(box_limb_evidence(profile, [0, -10, 35, 20], ["left", "right"])["head_cluster_overlap_without_wrist"])

    def test_real_overhead_racket_near_head_is_retained(self):
        points = {"nose": (0, 0), "left_eye": (-3, -5), "right_eye": (3, -5),
                  "left_ear": (-7, -2), "right_ear": (7, -2),
                  "left_wrist": (-50, 20), "left_elbow": (-30, 30),
                  "right_wrist": (15, -20), "right_elbow": (15, 10)}
        row = associate(points, [10, -45, 30, -15])
        self.assertEqual(row.racket_sides, {"right"})
        # A broad true follow-through box may overlap head landmarks but also
        # contains the wrist: the head-only ambiguity rule must not reject it.
        row = associate(points, [-10, -25, 30, 5])
        self.assertEqual(row.racket_sides, {"right"})
        self.assertFalse(row.racket_observations[0]["limb_geometry"]["head_cluster_overlap_without_wrist"])

    def test_missing_or_wrong_coordinate_frame_does_not_create_geometry_evidence(self):
        points = {"left_wrist": (20, 0), "right_wrist": (15, 8)}
        row = associate(points, [30, -2, 50, 12])
        self.assertIsNone(row.racket_observations[0]["limb_geometry"]["supported_motion_wrists"])
        row = sample(points); row.camera_compensated = True; row.raw_points_px = {}
        _associate_rackets([row], [{"class_name": "racket", "confidence": .9, "bbox_px": [30, -2, 50, 12]}])
        self.assertIsNone(row.racket_observations[0]["limb_geometry"]["supported_motion_wrists"])

    def test_other_player_ambiguity_is_not_resolved_by_preferred_forearm(self):
        points = {"left_wrist": (20, 0), "left_elbow": (40, 0),
                  "right_wrist": (15, 8), "right_elbow": (0, 8)}
        a = sample(points); b = deepcopy(a); b.track = 2
        _associate_rackets([a, b], [{"class_name": "racket", "confidence": .9, "bbox_px": [30, -2, 50, 12]}])
        self.assertFalse(a.racket_observations)
        self.assertFalse(b.racket_observations)


def conflict_sequence():
    points = {"left_wrist": (20, 0), "left_elbow": (40, 0),
              "right_wrist": (15, 8), "right_elbow": (0, 8)}
    rows = [sample(deepcopy(points)) for _ in range(9)]
    for i, row in enumerate(rows):
        row.time = i * 100
        row.frame_index = i
    head = {"nose": (-1, 5), "left_eye": (-2, 0), "right_eye": (3, 0),
            "left_ear": (5, 1), "right_ear": (12, 1),
            "left_wrist": (20, 30), "left_elbow": (0, 40),
            "right_wrist": (50, 30), "right_elbow": (40, 40)}
    rows[1] = associate(head, [0, -10, 35, 20]); rows[1].time = 100; rows[1].frame_index = 1
    for obs in rows[1].racket_observations:
        obs.update(timestamp_ms=100, source_frame_index=1)
    for i in (5, 6):
        _associate_rackets([rows[i]], [{"class_name": "racket", "confidence": .9,
                                      "track_id": 8, "bbox_px": [30, -2, 50, 12]}])
    return rows


class HeadConflictAlternativeTests(unittest.TestCase):
    def test_two_later_frames_support_only_a_bound_alternative_not_primary_grip(self):
        proof = _head_conflict_directional_support(conflict_sequence(), "right", 2, 4, 7)
        self.assertEqual(proof['distinct_directional_support_frames'], 2)
        self.assertEqual(proof['geometry_window_ms'], [200, 700])
        self.assertEqual([o['timestamp_ms'] for o in proof['directional_support_observations']], [500, 600])
        self.assertEqual(proof['motion_wrist'], 'right')
        self.assertIn('not_handle_primary_grip', proof['semantics'])

    def test_no_head_conflict_no_replacement_despite_unique_forearm_geometry(self):
        rows = conflict_sequence(); rows[1].racket_observations = []
        self.assertIsNone(_head_conflict_directional_support(rows, "right", 2, 4, 7))

    def test_one_frame_or_duplicate_boxes_are_not_two_independent_observations(self):
        rows = conflict_sequence(); rows[6].racket_observations = []
        rows[5].racket_observations *= 2
        self.assertIsNone(_head_conflict_directional_support(rows, "right", 2, 4, 7))

    def test_evidence_before_head_conflict_or_outside_geometry_window_cannot_be_borrowed(self):
        rows = conflict_sequence()
        for i in (5, 6):
            rows[i - 3].racket_observations = rows[i].racket_observations
            for obs in rows[i - 3].racket_observations:
                obs.update(timestamp_ms=rows[i - 3].time, source_frame_index=i - 3)
            rows[i].racket_observations = []
        rows[4].racket_observations = rows[1].racket_observations
        rows[1].racket_observations = []
        self.assertIsNone(_head_conflict_directional_support(rows, "right", 2, 4, 7))
        self.assertIsNone(_head_conflict_directional_support(conflict_sequence(), "right", 2, 4, 5))
        rows = conflict_sequence(); rows[1].time = -400
        self.assertIsNone(_head_conflict_directional_support(rows, "right", 2, 4, 7))

    def test_missing_elbows_shared_arms_or_competing_rackets_do_not_identify_alternative(self):
        for kind in ('missing_elbow', 'shared', 'opposite'):
            rows = conflict_sequence()
            for i in (5, 6):
                if kind == 'missing_elbow':
                    rows[i].racket_observations[0]['limb_geometry']['supported_motion_wrists'] = None
                elif kind == 'shared':
                    rows[i].racket_observations[0]['limb_geometry']['supported_motion_wrists'] = ['left', 'right']
                else:
                    other = deepcopy(rows[i].racket_observations[0])
                    other['limb_geometry']['supported_motion_wrists'] = ['left']
                    rows[i].racket_observations.append(other)
            with self.subTest(kind=kind):
                self.assertIsNone(_head_conflict_directional_support(rows, "right", 2, 4, 7))

    def test_person_camera_epoch_and_missing_time_break_the_evidence_chain(self):
        for field, value in [('track', 2), ('selection_epoch', 1), ('camera_reference_epoch', 1), ('time', 1500)]:
            rows = conflict_sequence(); setattr(rows[3], field, value)
            with self.subTest(field=field):
                self.assertIsNone(_head_conflict_directional_support(rows, "right", 2, 4, 7))

    def test_head_box_cannot_confirm_primary_grip_but_legacy_proposal_order_remains_separate(self):
        from rallymate_scoring.stroke_analysis import racket_hand_evidence
        rows = [deepcopy(conflict_sequence()[1]) for _ in range(8)]
        for row in rows:
            row.points['right_wrist'] = (1.5, .3)
        self.assertIsNone(racket_hand_evidence(rows)['hand'])
        self.assertEqual(racket_hand_evidence(rows, exclude_head_ambiguity=False)['hand'], 'left')

    def test_only_complete_locally_bound_alternative_can_replace_ordinary_proposal(self):
        from rallymate_scoring.stroke_candidates import _detect_tracks
        ordinary = {'candidate_id': 'ordinary', 'family': 'baseline', 'person_track_id': 1,
                    'racket_hand_candidate': 'right', 'start_ms': 200, 'peak_ms': 400, 'end_ms': 700,
                    'evidence': {'racket_associated_frames': 20, 'racket_geometry_alternative': None}}
        alternative = deepcopy(ordinary)
        alternative.update(candidate_id='alternative')
        alternative['evidence'].update(racket_associated_frames=2,
                                      racket_geometry_alternative={'status': 'supported'})
        for status, peak, expected in [('measured', 400, 'alternative'),
                                       ('unavailable', 400, 'ordinary'),
                                       ('measured', 800, 'ordinary')]:
            prediction = {'start_ms': 200, 'peak_ms': peak, 'end_ms': 700,
                          'phases': [{'status': 'measured'}, {'status': 'measured'}, {'status': status}]}
            rows = conflict_sequence()
            with self.subTest(status=status, peak=peak), \
                 patch('rallymate_scoring.stroke_candidates._detect_segment',
                       side_effect=lambda _, side: deepcopy([ordinary, alternative]) if side == 'right' else []), \
                 patch('rallymate_scoring.stroke_analysis.candidate_hand_evidence',
                       return_value={'status': 'unresolved', 'hand_evidence': {'hand': None}}), \
                 patch('rallymate_scoring.stroke_analysis._episode_context', return_value=rows), \
                 patch('rallymate_scoring.temporal_recognition.build_temporal_window', return_value=None), \
                 patch('rallymate_scoring.temporal_recognition.RuleTemporalBackend.recognize', return_value=prediction), \
                 patch('rallymate_scoring.stroke_candidates._group_supporting_sweeps', side_effect=lambda cs, _: cs):
                selected = _detect_tracks({1: rows})
                self.assertEqual([c['candidate_id'] for c in selected], [expected])

    def test_racket_confidence_and_person_ownership_gates_still_precede_geometry(self):
        row = conflict_sequence()[5]
        row.racket_observations = []
        row.racket_sides = set()
        _associate_rackets([row], [{'class_name': 'racket', 'confidence': .249,
                                   'bbox_px': [30, -2, 50, 12]}])
        self.assertFalse(row.racket_observations)


if __name__ == "__main__":
    unittest.main()

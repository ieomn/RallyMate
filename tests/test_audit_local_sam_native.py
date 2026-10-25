import copy
import unittest

from scripts.audit_local_sam_native import MHR70_NAMES, native_geometry_errors


class NativeAuditTests(unittest.TestCase):
    def setUp(self):
        self.row = {"keypoints_2d": {name: [0., 1.] for name in MHR70_NAMES},
                    "keypoints_3d": {name: [0., 1., 2.] for name in MHR70_NAMES}}

    def test_named_native_points_are_valid(self):
        self.assertEqual(native_geometry_errors(self.row), [])

    def test_anonymous_arrays_are_not_the_saved_native_contract(self):
        self.row["keypoints_2d"] = list(self.row["keypoints_2d"].values())
        self.assertIn("keypoints_2d:expected_joint_name_mapping", native_geometry_errors(self.row))

    def test_exact_names_dimensions_finiteness_and_numeric_type_required(self):
        for value in ([1.], [1., 2., 3.], [float("nan"), 1.], [float("inf"), 1.], [True, 1.], ["1", 1.]):
            row = copy.deepcopy(self.row)
            row["keypoints_2d"]["nose"] = value
            with self.subTest(value=value):
                self.assertTrue(native_geometry_errors(row))
        self.row["keypoints_2d"]["renamed_nose"] = self.row["keypoints_2d"].pop("nose")
        self.assertIn("keypoints_2d:joint_names_mismatch", native_geometry_errors(self.row))

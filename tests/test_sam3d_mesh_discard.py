"""Cancellation only discards exact captures whose ownership was not transferred."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np

from rallymate_service.worker import JobCancelled
from rallymate_vision.sam3d_mesh import (
    archive_mesh_records, capture_mesh_output, discard_unarchived_mesh_records,
)
from rallymate_vision.sam3d_mesh_archive import BoundedMeshArchiver
from rallymate_vision.sam3d_pipeline_batch import sam_pose_observations
from tests.test_sam3d_pipeline_batch import Perception, observation


class MeshDiscardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime, self.output = self.root / "runtime", self.root / "job"
        self.output.mkdir()

    def capture(self):
        artifact = capture_mesh_output({
            "pred_vertices": np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], np.float32),
            "pred_cam_t": np.array([0., 0., 3.], np.float32), "focal_length": np.array(100., np.float32),
        }, np.array([[0, 1, 2]], np.int32), self.runtime)
        return {"status": "ok", "mesh_artifact": artifact}

    def iterator(self, records, *, check=lambda: None, after=lambda: None):
        perception = Perception()
        def infer(requests):
            after()
            return [{"poses": [], "records": [record]} for record in records]
        perception.estimate_pose_batch = infer
        return sam_pose_observations([observation(i) for i in range(len(records))], perception,
            max_players=2, video_sha256="a" * 64, batch_frames=len(records),
            timings={}, statistics={}, check_cancelled=check,
            discard_records=lambda rows: discard_unarchived_mesh_records(rows, self.runtime))

    def test_only_named_verified_capture_is_removed_and_topology_is_retained(self):
        record, other = self.capture(), self.capture()
        record["status"] = "failed"
        self.assertEqual(discard_unarchived_mesh_records([record], self.runtime), 1)
        self.assertFalse(Path(record["mesh_artifact"]["path"]).exists())
        self.assertTrue(Path(record["mesh_artifact"]["faces_path"]).exists())
        self.assertTrue(Path(other["mesh_artifact"]["path"]).exists())
        self.assertEqual(discard_unarchived_mesh_records([record], self.runtime), 0)

    def test_invalid_binding_or_changed_capture_is_never_deleted(self):
        record = self.capture()
        artifact = record["mesh_artifact"]
        source = Path(artifact["path"])
        outside = self.root / "unrelated.mesh.npz"
        outside.write_bytes(source.read_bytes())
        other_suffix = source.with_name("unrelated.npz")
        other_suffix.write_bytes(source.read_bytes())
        nested = source.parent / "other" / source.name
        nested.parent.mkdir()
        nested.write_bytes(source.read_bytes())
        variants = [None, {}, {"mesh_artifact": None}]
        variants += [{"mesh_artifact": {**artifact, **changes}} for changes in (
            {"version": "unknown"}, {"sha256": "f" * 64}, {"path": str(outside)},
            {"path": str(source.relative_to(self.root))}, {"path": str(other_suffix)},
            {"path": str(nested)}, {"sha256": None},
        )]
        self.assertEqual(discard_unarchived_mesh_records(variants, self.runtime), 0)
        for path in (source, outside, other_suffix, nested):
            self.assertTrue(path.is_file())
        source.write_bytes(source.read_bytes() + b"changed")
        self.assertEqual(discard_unarchived_mesh_records([record], self.runtime), 0)
        self.assertTrue(source.exists())

    def test_archived_capture_survives_stale_and_updated_records(self):
        record = self.capture()
        stale = deepcopy(record)
        archive_mesh_records([record], self.output, self.runtime)
        archived = self.output / record["mesh_artifact"]["path"]
        self.assertEqual(discard_unarchived_mesh_records([stale, record], self.runtime), 0)
        self.assertTrue(archived.is_file())

    def test_cancel_immediately_after_forward_discards_entire_unpublished_batch(self):
        records = [self.capture(), self.capture()]
        state = {"cancel": False}
        def check():
            if state["cancel"]:
                raise JobCancelled("cancel after forward")
        iterator = self.iterator(records, check=check, after=lambda: state.update(cancel=True))
        with self.assertRaises(JobCancelled):
            next(iterator)
        self.assertTrue(all(not Path(row["mesh_artifact"]["path"]).exists() for row in records))

    def test_close_discards_unyielded_slots_but_preserves_async_archive_ownership(self):
        records = [self.capture(), self.capture(), self.capture()]
        iterator = self.iterator(records)
        transferred = next(iterator)
        started, release = threading.Event(), threading.Event()
        def archive(rows, output_dir, runtime_dir):
            started.set()
            if not release.wait(5):
                raise TimeoutError("test archive was not released")
            return archive_mesh_records(rows, output_dir, runtime_dir)
        archiver = BoundedMeshArchiver(self.output, self.runtime, _archive=archive)
        try:
            archiver.submit(transferred["sam_records"])
            self.assertTrue(started.wait(2))
            iterator.close()
            self.assertTrue(Path(records[0]["mesh_artifact"]["path"]).exists())
            self.assertTrue(all(not Path(row["mesh_artifact"]["path"]).exists() for row in records[1:]))
            release.set()
            result = archiver.finish()
            self.assertEqual(len(result), 1)
            self.assertTrue((self.output / result[0].records[0]["mesh_artifact"]["path"]).exists())
        finally:
            release.set()
            iterator.close()
            archiver.close()

    def test_normal_completion_transfers_every_slot_without_discarding(self):
        records = [self.capture(), self.capture()]
        rows = list(self.iterator(records))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(Path(row["mesh_artifact"]["path"]).exists() for row in records))


if __name__ == "__main__":
    unittest.main()

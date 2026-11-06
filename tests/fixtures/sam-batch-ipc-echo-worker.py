"""CPU-only batch echo engine behind production _serve and shared pixel slots."""
import hashlib
import sys
import time
from types import SimpleNamespace

from rallymate_vision import sam3d_runner as runner


class BatchPixelEcho:
    def __init__(self, *args, **kwargs):
        self.metadata = {"cpu_threads": {}}

    def infer(self, frame, boxes):
        marker = int(frame[0, 0, 0])
        if marker == 253:
            time.sleep(30)
        if marker == 254:
            raise ValueError("intentional fixture engine failure")
        checksum = hashlib.sha256(frame.tobytes()).hexdigest()
        return [{"sha256": checksum, "shape": list(frame.shape), "dtype": str(frame.dtype),
                 "writeable": frame.flags.writeable, "box": box, "subject_index": index}
                for index, box in enumerate(boxes)]

    def infer_batch(self, requests):
        # Re-read every source after processing the others. If the parent reused
        # one shared slot for all frames, the checksums/order assertions fail.
        snapshots = [hashlib.sha256(item["frame"].tobytes()).hexdigest() for item in requests]
        outputs = [self.infer(item["frame"], item["boxes"]) for item in requests]
        if snapshots != [hashlib.sha256(item["frame"].tobytes()).hexdigest() for item in requests]:
            raise RuntimeError("source pixels were overwritten before acknowledgement")
        marker = int(requests[0]["frame"][0, 0, 0]) if requests else 0
        if marker == 250:
            return outputs[:-1]
        if marker == 249:
            outputs[0] = outputs[0][:-1]
        if marker == 248:
            outputs[0] = [None]
        return outputs

    def close(self):
        pass


sys.modules["torch"] = SimpleNamespace(get_num_threads=lambda: 1, get_num_interop_threads=lambda: 1)
sys.modules["cv2"] = SimpleNamespace(getNumThreads=lambda: 1)
runner.configure_sam_cpu_pools = lambda *args: {"requested_intraop": 1, "requested_interop": 1}
runner.Sam3DEngine = BatchPixelEcho
raise SystemExit(runner.main())

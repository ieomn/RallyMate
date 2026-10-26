"""CPU-only child using the production SAM wire protocol, never a model."""
import hashlib
import sys
import time
from types import SimpleNamespace

from rallymate_vision import sam3d_runner as runner


class PixelEcho:
    def __init__(self, *args, **kwargs):
        self.metadata = {"cpu_threads": {}}

    def infer(self, frame, boxes):
        if boxes and boxes[0] == [-1]:
            raise ValueError("intentional test error")
        if boxes and boxes[0] == [-2]:
            time.sleep(30)
        return [{"sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                 "shape": list(frame.shape), "dtype": str(frame.dtype),
                 "writeable": frame.flags.writeable, "boxes": boxes}]

    def close(self):
        pass


sys.modules["torch"] = SimpleNamespace(get_num_threads=lambda: 1, get_num_interop_threads=lambda: 1)
sys.modules["cv2"] = SimpleNamespace(getNumThreads=lambda: 1)
runner.configure_sam_cpu_pools = lambda *args: {"requested_intraop": 1, "requested_interop": 1}
runner.Sam3DEngine = PixelEcho
raise SystemExit(runner.main())

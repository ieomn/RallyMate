"""CPU-only, source-verified side-by-side native pose review video.

Common anatomical landmarks are displayed on identical source frames and crops.
This renders observed model output only: no smoothing, synthetic skeleton points,
mesh rendering, accuracy truth, or model ranking is introduced.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import cv2
import numpy as np

from scripts.audit_sam_benchmark_evidence import BINDING, raw_points


EDGES = (("left_shoulder", "right_shoulder"), ("left_shoulder", "left_elbow"),
         ("left_elbow", "left_wrist"), ("right_shoulder", "right_elbow"),
         ("right_elbow", "right_wrist"), ("left_shoulder", "left_hip"),
         ("right_shoulder", "right_hip"), ("left_hip", "right_hip"),
         ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
         ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
         ("left_eye", "right_eye"), ("left_eye", "nose"), ("right_eye", "nose"),
         ("left_eye", "left_ear"), ("right_eye", "right_ear"))


def load_ledger(path, requests):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != len(requests):
        raise ValueError("overlay requires one ledger record per manifest request")
    for row, request in zip(rows, requests):
        if any(key not in row or row[key] != request.get(key) for key in BINDING):
            raise ValueError("overlay ledger does not exactly match the source/box manifest")
    return rows


def fit_image(image, width=640, height=360):
    canvas = np.full((height, width, 3), 22, dtype=np.uint8)
    scale = min(width / image.shape[1], height / image.shape[0])
    size = (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale)))
    resized = cv2.resize(image, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    x, y = (width-size[0]) // 2, (height-size[1]) // 2
    canvas[y:y+size[1], x:x+size[0]] = resized
    return canvas


def draw_pose(frame, row, color):
    output = frame.copy()
    if row.get("status") != "ok":
        return output
    points, _ = raw_points(row)
    xy = {name: (round(point[0]), round(point[1])) for name, point in points.items()}
    for first, second in EDGES:
        if first in xy and second in xy:
            cv2.line(output, xy[first], xy[second], color, 2, cv2.LINE_AA)
    for point in xy.values():
        cv2.circle(output, point, 3, color, -1, cv2.LINE_AA)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("video", "manifest", "first", "second", "out", "ffmpeg"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--first-label", default="RTMPose-M (common 17 joints)")
    parser.add_argument("--second-label", default="SAM DINOv3 (common 17 joints)")
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("choose a fresh review output directory")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    requests = manifest["requests"]
    first, second = load_ledger(args.first, requests), load_ledger(args.second, requests)
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("native_overlay_source", root/"runtime/sam3d/benchmark_official.py")
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    if benchmark.sha256(args.video) != manifest["video"]["sha256"]:
        raise ValueError("source video SHA mismatch")
    ready = [row for row in requests if row.get("request_status") == "ready"]
    if len(ready) != len(requests) or len(ready) < 2:
        raise ValueError("continuous review requires every manifest frame to be available")
    intervals = [(b["pts"] * b["time_base_num"] / b["time_base_den"] - a["pts"] * a["time_base_num"] / a["time_base_den"])
                 for a, b in zip(ready, ready[1:])]
    if min(intervals) <= 0 or max(intervals)-min(intervals) > 1e-8:
        raise ValueError("review encoder needs a uniform PTS interval; refusing to silently change variable timing")
    fps = 1 / intervals[0]
    args.out.mkdir(parents=True)
    video_path = args.out / "common-joint-source-review.mp4"
    command = [str(args.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
               "-pixel_format", "bgr24", "-video_size", "1920x810", "-framerate", str(fps), "-i", "pipe:0",
               "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-threads", "2",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video_path)]
    rows_rendered = []
    with (args.out/"encoder.stderr.log").open("wb") as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            for index, (request, rgb, error) in enumerate(benchmark.decoded_requests(args.video, requests)):
                if error or rgb is None:
                    raise ValueError("source RGB or PTS binding failed during review export")
                source = np.ascontiguousarray(rgb[:, :, ::-1])
                box = request["bbox_px"]
                pad = max(25, (box[2]-box[0]) * .35)
                left, top = max(0, int(box[0]-pad)), max(0, int(box[1]-pad))
                right, bottom = min(source.shape[1], int(box[2]+pad)), min(source.shape[0], int(box[3]+pad))
                panels = []
                for frame, label in ((source, "Original source"),
                                     (draw_pose(source, first[index], (40,230,100)), args.first_label),
                                     (draw_pose(source, second[index], (240,210,50)), args.second_label)):
                    panel = np.full((810,640,3),22,dtype=np.uint8)
                    cv2.putText(panel,label,(16,25),cv2.FONT_HERSHEY_SIMPLEX,.62,(240,240,240),1,cv2.LINE_AA)
                    panel[40:400] = fit_image(frame)
                    panel[420:780] = fit_image(frame[top:bottom,left:right])
                    cv2.putText(panel,"Identical selected-person crop; no smoothing",(12,803),cv2.FONT_HERSHEY_SIMPLEX,.48,(210,210,210),1,cv2.LINE_AA)
                    panels.append(panel)
                combined = np.ascontiguousarray(np.hstack(panels))
                caption = f"source frame {request['frame_index']}   {request['timestamp_ms']/1000:.3f}s   model estimates, not accuracy truth"
                cv2.putText(combined,caption,(12,416),cv2.FONT_HERSHEY_SIMPLEX,.48,(230,230,230),1,cv2.LINE_AA)
                process.stdin.write(combined.tobytes())
                if index == len(requests)//2:
                    cv2.imwrite(str(args.out/"review-poster.jpg"),combined)
                rows_rendered.append({"frame_index":request["frame_index"],"pts":request["pts"],
                                      "rgb_sha256":request["rgb_sha256"],"common_crop_px":[left,top,right,bottom]})
        finally:
            process.stdin.close()
            code = process.wait(timeout=60)
        if code:
            raise RuntimeError(f"review encoder failed with exit code {code}")
    receipt = {"schema":"source-bound-model-overlay-v1","frames":len(rows_rendered),"fps":fps,
               "video":str(video_path.resolve()),"video_sha256":benchmark.sha256(video_path),
               "source_sha256":manifest["video"]["sha256"],"manifest_sha256":benchmark.sha256(args.manifest),
               "first_ledger_sha256":benchmark.sha256(args.first),"second_ledger_sha256":benchmark.sha256(args.second),
               "rows":rows_rendered,"scope":"common17; RTMPose normalized native score>=0.35, SAM unknown confidence; full source and identical crop; no interpolation, smoothing, mesh or accuracy claim"}
    (args.out/"receipt.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    print(json.dumps({"video":str(video_path),"frames":len(rows_rendered)}))


if __name__ == "__main__":
    main()

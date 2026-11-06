"""Bounded, ordered SAM frame requests after subject/camera state is resolved."""
from __future__ import annotations

import time

from rallymate_tracking.secondary_subjects import SecondarySubjectSelector


def sam_pose_observations(observations, perception, *, max_players, video_sha256,
                          batch_frames, timings, statistics, check_cancelled,
                          max_buffer_bytes=64 * 1024 * 1024, discard_records=None):
    if type(batch_frames) is not int or not 1 <= batch_frames <= 8:
        raise ValueError("SAM batch frames must be between 1 and 8")
    if type(max_buffer_bytes) is not int or max_buffer_bytes < 1:
        raise ValueError("SAM source buffer bytes must be positive")
    statistics.update(requested_batch_frames=batch_frames, max_buffer_bytes=max_buffer_bytes,
                      calls=0, peak_frames=0, peak_source_bytes=0)

    def infer(batch):
        check_cancelled()
        started = time.perf_counter()
        requests = [item[1] for item in batch]
        results, transferred = [], 0
        try:
            if len(batch) == 1:
                request = requests[0]
                poses = perception.estimate_poses(request["frame"], request["player_detections"],
                    request["max_players"], source_binding=request["source_binding"])
                results = [{"poses": poses, "records": perception.pose_frame_records()}]
            else:
                results = perception.estimate_pose_batch(requests)
            timings["pose_seconds"] = timings.get("pose_seconds", 0) + time.perf_counter() - started
            check_cancelled()
            if (not isinstance(results, list) or len(results) != len(batch)
                    or any(not isinstance(item, dict) or not isinstance(item.get("poses"), list)
                           or not isinstance(item.get("records"), list) for item in results)):
                raise ValueError("SAM adapter returned mismatched frame slots")
            statistics["calls"] += 1
            statistics["peak_frames"] = max(statistics["peak_frames"], len(batch))
            statistics["peak_source_bytes"] = max(statistics["peak_source_bytes"],
                                                 sum(item[0]["image"].nbytes for item in batch))
            for (observation, _), result in zip(batch, results):
                check_cancelled()
                # The consumer owns this slot before control leaves the
                # generator. It may submit its files to an async archive worker.
                transferred += 1
                yield {**observation, "sam_poses": result["poses"], "sam_records": result["records"]}
        finally:
            if discard_records is not None and isinstance(results, list):
                for result in results[transferred:]:
                    if isinstance(result, dict) and isinstance(result.get("records"), list):
                        discard_records(result["records"])

    pending, size = [], 0
    selector = SecondarySubjectSelector(max_secondaries=max_players - 1)
    for index, observation in enumerate(observations):
        check_cancelled()
        frame = observation["image"]
        players = observation["players"]
        target = observation["subject_identity"].get("source_track_id")
        binding = observation["decoded_binding"]
        selected, selection = selector.update(players, primary_track_id=target,
            timestamp_ms=binding["timestamp_ms"], width=frame.shape[1], height=frame.shape[0],
            image=frame, camera_epoch=observation["camera_motion"]["reference_epoch"])
        observation = {**observation, "sam_subject_selection": selection}
        request = {"frame": frame, "player_detections": selected, "max_players": max_players,
            "source_binding": {**binding, "video_sha256": video_sha256,
                "timestamp_ms": round(binding["timestamp_ms"]) if binding.get("timestamp_ms") is not None else None,
                "frame_index": observation["source_frame_index"], "processed_index": index,
                "camera_reference_epoch": observation["camera_motion"]["reference_epoch"],
                "subject_selection": selection,
                "relative_timestamp_ms": observation["timestamp_ms"],
                "timestamp_source": observation["timestamp_source"]}}
        if pending and size + frame.nbytes > max_buffer_bytes:
            yield from infer(pending)
            pending, size = [], 0
        pending.append((observation, request))
        size += frame.nbytes
        if len(pending) >= batch_frames or size >= max_buffer_bytes:
            yield from infer(pending)
            pending, size = [], 0
    if pending:
        yield from infer(pending)

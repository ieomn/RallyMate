"""Read-only local HTTP acceptance for segmented reports, without model work.

Run only after isolated timed trials finish. This checks the browser gateway,
not direct API credentials, and never requests the monolithic demo result.
The receipt proves HTTP delivery and strict proof decoding, not browser layout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rallymate_service.report_evidence_transport import unpack_report_evidence


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_matches(actual, expected):
    if not isinstance(actual, dict):
        return False
    return all(actual.get(key) == expected.get(key) for key in ("source_job_id", "video_id")) and all(
        isinstance(actual.get(key), str) and actual[key].lower() == expected[key].lower()
        for key in ("video_sha256", "frames_sha256", "evidence_revision"))


def check_page(value, rows, *, offset, limit, expected_total=None):
    pagination = value["pagination"]
    total = pagination.get("total")
    require(type(total) is int and total >= 0, "Invalid pagination total")
    require(expected_total is None or total == expected_total, "Pagination total changed")
    require(pagination.get("offset") == offset and pagination.get("limit") == limit,
            "Pagination request differs")
    count = min(limit, max(0, total - offset))
    require(len(rows) == count, "Page silently omitted or added records")
    next_offset = offset + count if offset + count < total else None
    require(pagination.get("next_offset") == next_offset, "Pagination continuation differs")
    require(value.get("status") == ("partial" if next_offset is not None else "complete"),
            "Pagination completion status differs")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--job-id", default="afd950a6-0967-49dc-8e74-fd1b4c76a9e6")
    parser.add_argument("--expect-mode", choices=("maintenance", "restored"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; previous receipts are never overwritten")
    args = parser.parse_args(argv)
    base = urlsplit(args.base_url)
    require(base.scheme == "http" and base.hostname in ("127.0.0.1", "localhost", "::1")
            and base.path in ("", "/") and not (base.username or base.password or base.query or base.fragment),
            "Only an explicit local HTTP browser gateway is accepted")
    require(re.fullmatch(r"[A-Za-z0-9_-]{8,80}", args.job_id), "Invalid job ID")
    origin = args.base_url.rstrip("/")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    opener = build_opener(NoRedirect())
    route = f"/v1/jobs/{args.job_id}"
    receipt = {"schema_version": "segmented-local-http-acceptance-v1", "job_id": args.job_id,
        "expected_mode": args.expect_mode, "frontend_url": origin + "/?" + urlencode({"view": "analysis", "job": args.job_id}),
        "coverage": "first_and_last_action_pages_selected_detail_pages_and_1024_byte_media_ranges",
        "requests": [], "qualification": "HTTP_gateway_and_proof_decode_not_browser_visual_or_multi_GPU_acceptance"}

    def read(name, path, *, limit=256 * 1024, expected=200, headers=None, binary=False):
        started = time.perf_counter()
        request = Request(origin + path, headers=headers or {}, method="GET")
        with opener.open(request, timeout=25) as response:
            require(response.status == expected, f"Unexpected HTTP status for {name}")
            length = response.headers.get("Content-Length")
            require(length is None or length.isdecimal() and int(length) <= limit, f"Oversized {name} Content-Length")
            body = response.read(limit + 1)
            require(len(body) <= limit, f"Oversized streamed {name} response")
            require(length is None or len(body) == int(length), f"Incomplete {name} body")
            content_range = response.headers.get("Content-Range")
            if expected == 206:
                require(content_range is not None and re.fullmatch(r"bytes 0-1023/[1-9][0-9]*", content_range)
                        and len(body) == 1024, f"Invalid byte-range response for {name}")
            receipt["requests"].append({"name": name, "path": path, "http_status": response.status,
                "bytes": len(body), "elapsed_seconds": round(time.perf_counter() - started, 6),
                "sha256": hashlib.sha256(body).hexdigest(), "content_range": content_range})
        (out / (name + (".bin" if binary else ".json"))).write_bytes(body)
        return body if binary else json.loads(body)

    def page(name, offset, expected_total=None):
        value = read(name, route + f"/report-actions?offset={offset}&limit=20")
        require(value.get("schema_version") == "segmented-report-v1", "Unknown action schema")
        require(value.get("job_id") == args.job_id and source_matches(value.get("source"), summary["source"]), "Action source revision differs")
        check_page(value, value["actions"], offset=offset, limit=20, expected_total=expected_total)
        identifiers = [row["action_id"] for row in value["actions"]]
        require(len(identifiers) == len(set(identifiers)), "Repeated action within one page")
        receipt["requests"][-1].update(actions=len(value["actions"]), total=value["pagination"]["total"],
            detail_status=value["status"], next_offset=value["pagination"]["next_offset"])
        return value

    def detail(name, action, offset=0):
        require(isinstance(action.get("action_id"), str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", action["action_id"]), "Invalid action route identifier")
        value = read(name, route + f"/report-actions/{action['action_id']}?offset={offset}&limit=4", limit=8 * 1024 * 1024)
        decoded = unpack_report_evidence(value)
        require(decoded.get("schema_version") == "segmented-report-v1", "Unknown detail schema")
        require(decoded.get("job_id") == args.job_id and source_matches(decoded.get("source"), summary["source"]), "Detail source revision differs")
        require(decoded.get("evidence_validation") == "validated" and decoded.get("scope") == "event_indicator_records", "Detail contract differs")
        require(decoded["action"] == action and decoded["event"]["event_id"] == action["event_id"], "Detail event differs")
        require(decoded["event"].get("person_track_id") == action["person_track_id"], "Event logical subject differs")
        window = decoded["event"]["source_window"]
        require(window.get("person_track_id") == action["source_track_id"]
                and window.get("video_sha256", "").lower() == summary["source"]["video_sha256"].lower(),
                "Event native subject or video source differs")
        check_page(decoded, decoded["records"], offset=offset, limit=4, expected_total=action["indicator_count"])
        require(all(row.get("event_id") == action["event_id"] and row.get("person_track_id") == action["person_track_id"]
                    for row in decoded["records"]), "Detail logical subject differs")
        receipt["requests"][-1].update(strict_proof_decode=True, records=len(decoded["records"]),
            logical_subject=action["person_track_id"], native_subject=action["source_track_id"],
            detail_status=decoded["status"], next_offset=decoded["pagination"]["next_offset"])
        return decoded

    try:
        health = read("health", "/health/live", limit=64 * 1024)
        require(health["admission"]["accept_new_jobs"] is (args.expect_mode == "restored"), "Admission mode differs from expected maintenance/restored mode")
        status = read("status", route + "/status", limit=64 * 1024)
        require(status.get("id") == args.job_id and status.get("status") == "succeeded", "Completed task status is unavailable")
        require(not status.get("cancel_requested") and "summary" not in status and "summary_json" not in status,
                "Lightweight status is cancelled or unexpectedly contains the full summary")
        summary = read("summary", route + "/report-summary")
        require(summary.get("schema_version") == "segmented-report-v1", "Unknown summary schema")
        require(summary.get("job_id") == args.job_id and summary.get("status") == "ready", "Summary is not ready for this job")
        require(summary.get("delivery", {}).get("summary") == "complete", "Summary delivery is incomplete")
        for key in ("video_sha256", "frames_sha256", "evidence_revision"):
            require(isinstance(summary["source"].get(key), str) and re.fullmatch(r"[0-9a-fA-F]{64}", summary["source"][key]), "Invalid source identity")
        receipt["evidence_revision"] = summary["source"]["evidence_revision"]
        original = summary["original_video"]
        require(original["source_job_id"] == summary["source"]["source_job_id"]
                and original["sha256"].lower() == summary["source"]["video_sha256"].lower()
                and original["url"] == route + "/original-video", "Original-video descriptor source differs")
        first = page("actions", 0)
        available = [action for action in first["actions"] if action["evidence_status"] == "available"]
        require(available, "No readable action on the first page")
        detail("detail", available[0])
        last_offset = max(0, ((first["pagination"]["total"] - 1) // 20) * 20)
        last = page("actions-last", last_offset, first["pagination"]["total"])
        last_available = [action for action in last["actions"] if action["evidence_status"] == "available"]
        require(last_available, "No readable action on the last page")
        final = detail("detail-last", last_available[-1])
        if final["pagination"]["next_offset"] is not None:
            detail("detail-last-next", last_available[-1], final["pagination"]["next_offset"])
        mesh = read("mesh", route + "/mesh-preview", limit=256 * 1024)
        require(mesh.get("job_id") == args.job_id and mesh.get("status") in ("ready", "pending", "failed", "unavailable"), "Unknown independent mesh state")
        receipt["mesh_status"] = mesh["status"]
        read("original-range", route + "/original-video", limit=1024, expected=206, headers={"Range": "bytes=0-1023"}, binary=True)
        if mesh["status"] == "ready":
            require(mesh["sam3d_mesh"]["source_video_sha256"].lower() == summary["source"]["video_sha256"].lower(), "Mesh video source differs")
            for name in ("sam3d-mesh", "sam3d-mesh-side"):
                read(name + "-range", route + f"/artifacts/{name}.mp4", limit=1024, expected=206, headers={"Range": "bytes=0-1023"}, binary=True)
        read("frontend", "/?" + urlencode({"view": "analysis", "job": args.job_id}), limit=2 * 1024 * 1024, binary=True)
        receipt["status"] = "passed"
    except Exception as exc:
        receipt.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "output": str(out), "request_count": len(receipt["requests"]),
        "frontend_url": receipt["frontend_url"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

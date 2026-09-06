#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from rallymate_evaluation.m96_pose_pilot import M96PosePilotError, role_service_contract
from range_http_server import RangeRequestHandler


def main(argv: list[str] | None = None) -> int:
    root = _ROOT
    parser = argparse.ArgumentParser(
        description="Validate and serve exactly one M96 role package plus its three bound videos"
    )
    parser.add_argument("--workspace", type=Path, default=root)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--role", choices=("A", "B", "C"), required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    if args.bind != "127.0.0.1":
        parser.error("--bind must be exactly 127.0.0.1")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        contract = role_service_contract(args.workspace, args.bundle, args.role)
        workspace = Path(contract["workspace"])
        allowed_entries = []
        for relative in contract["allowed_relative_paths"]:
            path = (workspace / Path(*relative.split("/"))).resolve(strict=True)
            path.relative_to(workspace)
            if path.is_symlink() or not path.is_file():
                raise M96PosePilotError(f"allowlisted path is not a regular file: {relative}")
            allowed_entries.append((path, False))
        result = {
            "ok": True,
            "status": "valid" if args.validate_only else "serving",
            "role_slot": contract["role_slot"],
            "bundle_id": contract["bundle_id"],
            "allowlisted_file_count": len(allowed_entries),
        }
        if args.validate_only:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        handler = functools.partial(
            RangeRequestHandler,
            directory=str(workspace),
            allowed_entries=tuple(allowed_entries),
        )
        server = ThreadingHTTPServer((args.bind, args.port), handler)
        actual_port = int(server.server_address[1])
        result["url"] = f"http://127.0.0.1:{actual_port}{contract['entrypoint']}"
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (M96PosePilotError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

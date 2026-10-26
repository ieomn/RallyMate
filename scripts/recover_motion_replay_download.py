"""Recover one existing remote result; never render, upload or submit a task."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_service.local_secrets import load_minimax_key
from rallymate_service.motion_replay_provider import MiniMaxVideoProvider, ProviderError
from rallymate_service.motion_replay_recovery import recover_existing_download
from rallymate_service.motion_replay_store import MotionReplaySettings, MotionReplayStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("service_data"))
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--allow-proxy-dns", action="store_true",
                        help="Use the existing exact official CDN + benchmark-IP compatibility for this process only")
    args = parser.parse_args()
    try:
        settings = MotionReplaySettings(args.data_root.resolve())
        provider = MiniMaxVideoProvider(api_key=load_minimax_key(settings.data_root), allow_proxy_dns=args.allow_proxy_dns)
        result = recover_existing_download(MotionReplayStore(settings.database_path), settings,
            replay_id=args.replay_id, job_id=args.job_id, provider=provider)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_code": exc.code if isinstance(exc, ProviderError) else type(exc).__name__,
                          "new_generation_created": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

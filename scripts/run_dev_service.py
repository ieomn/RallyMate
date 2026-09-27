from __future__ import annotations

import argparse
import sys

from rallymate_service.cli import dev_main


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    args = parser.parse_args()
    sys.argv = ["rallymate-dev", "--host", args.host, "--port", str(args.port), "--poll-seconds", str(args.poll_seconds)]
    dev_main()


if __name__ == "__main__":
    main()

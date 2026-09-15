"""Fail without printing secret values when tracked/staged files contain keys."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

PATTERNS = (
    re.compile(rb"\btp-[A-Za-z0-9_-]{24,}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.directory:
        files = [(str(path), path.read_bytes()) for path in args.directory.rglob("*") if path.is_file()]
    else:
        paths = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
        files = []
        for raw in paths:
            if not raw:
                continue
            name = raw.decode("utf-8")
            if args.staged:
                data = subprocess.check_output(["git", "show", f":{name}"])
            else:
                path = Path(name)
                if not path.is_file():
                    continue
                data = path.read_bytes()
            files.append((name, data))
    matches = [name for name, data in files if any(pattern.search(data) for pattern in PATTERNS)]
    if matches:
        print("Credential-like content found; values withheld:")
        for name in matches:
            print(name)
        return 1
    print(f"PASS: {len(files)} files scanned; no configured credential patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

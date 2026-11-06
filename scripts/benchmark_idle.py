"""Fresh, read-only benchmark admission checks, including WSL/Windows bridging.

The --serve worker uses only the standard library and opens the live database
on its native OS. It never creates a database, takes a snapshot, changes rows,
or keeps a transaction across checks. EOF terminates the persistent worker.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading


def _active_jobs(database: Path) -> int:
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=1)) as connection:
        return connection.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')").fetchone()[0]


def require_idle(database: Path) -> None:
    if _active_jobs(database):
        raise RuntimeError("Benchmark requires an idle jobs database; queued/running production work exists")


def _bridge_path(path: Path, python: Path) -> str:
    path = Path(path).resolve()
    if os.name != "nt" and Path(python).suffix.lower() == ".exe":
        # Arguments are passed directly, never interpolated into a shell.
        result = subprocess.run(["wslpath", "-w", str(path)], check=True,
                                capture_output=True, text=True, timeout=5)
        converted = result.stdout.strip()
        if not converted or "\n" in converted or "\r" in converted:
            raise RuntimeError("Invalid Windows path returned by wslpath")
        return converted
    return str(path)


class IdleJobsGuard:
    def __init__(self, database: Path, python: Path | None = None, timeout: float = 5):
        if not 0 < timeout <= 60:
            raise ValueError("Idle check timeout must be positive and at most 60 seconds")
        self.database = Path(database).resolve()
        self.python = Path(python).resolve() if python is not None else None
        self.timeout = timeout
        self.owner = self.process = None
        self.messages = queue.Queue(maxsize=2)
        self.request_id, self.closed = 0, False
        self.lock = threading.RLock()
        self.receipt = {"mode": "persistent_stdio_bridge" if python is not None else "native_sqlite",
                        "database": str(self.database),
                        "python": str(self.python) if self.python is not None else None,
                        "fresh_connection_per_check": True, "read_only": True}

    def _start_bridge(self):
        # Only the parent imports this repository's stdlib process owner. The
        # worker can run as an independent Python script in either OS.
        from rallymate_vision.owned_subprocess import OwnedProcess

        command = [str(self.python), "-u", _bridge_path(Path(__file__), self.python),
                   "--serve", "--database", _bridge_path(self.database, self.python)]
        self.owner = OwnedProcess(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.process = self.owner.process
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(4097)
                if not line:
                    message = None
                else:
                    try:
                        if len(line) > 4096 or not line.endswith("\n"):
                            raise ValueError("oversized/incomplete response")
                        message = json.loads(line)
                        if not isinstance(message, dict):
                            raise ValueError("response is not an object")
                    except (ValueError, TypeError):
                        message = {"protocol_error": True}
                self.messages.put_nowait(message)
                if message is None or message.get("protocol_error"):
                    return
        except (OSError, ValueError, queue.Full):
            # A full queue already contains an unsolicited response. Its ID
            # will fail validation; never drop that evidence to admit work.
            try:
                self.messages.put_nowait({"protocol_error": True})
            except queue.Full:
                pass

    def check(self) -> None:
        with self.lock:
            if self.closed:
                raise RuntimeError("Idle jobs guard is closed")
            if self.python is None:
                require_idle(self.database)
                return
            try:
                if self.process is None:
                    self._start_bridge()
                if self.process.poll() is not None:
                    raise RuntimeError("Idle jobs bridge exited")
                self.request_id += 1
                self.process.stdin.write(json.dumps({"id": self.request_id, "command": "check"}) + "\n")
                self.process.stdin.flush()
                try:
                    response = self.messages.get(timeout=self.timeout)
                except queue.Empty as error:
                    raise RuntimeError("Idle jobs bridge timeout; benchmark refused") from error
                if response is None:
                    raise RuntimeError("Idle jobs bridge EOF; benchmark refused")
                if (not isinstance(response, dict) or response.get("id") != self.request_id
                        or type(response.get("id")) is not int):
                    raise RuntimeError("Idle jobs bridge protocol error; benchmark refused")
                if "error" in response:
                    raise RuntimeError("Idle jobs bridge could not read the live database: " + str(response["error"]))
                count = response.get("active_jobs")
                if type(count) is not int or count < 0 or set(response) != {"id", "active_jobs"}:
                    raise RuntimeError("Idle jobs bridge protocol error; invalid active-job count")
                if count:
                    raise RuntimeError("Benchmark requires an idle jobs database; queued/running production work exists")
            except BaseException:
                self.close()
                raise

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.process is None:
                return
            # In WSL the Windows Python is reached through interop. Closing
            # stdin gives the native worker EOF, including when its Linux
            # launcher cannot terminate the Windows process itself.
            try:
                self.process.stdin.close()
                self.process.wait(timeout=3)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    self.owner.close()
                finally:
                    self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args(argv)
    last_id = 0
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if (not isinstance(request, dict) or set(request) != {"id", "command"}
                    or type(request["id"]) is not int or request["id"] <= last_id
                    or request["command"] != "check"):
                return 2
            last_id = request["id"]
            try:
                response = {"id": last_id, "active_jobs": _active_jobs(args.database)}
            except (sqlite3.Error, OSError) as error:
                # Error class is sufficient; do not echo environment or data.
                response = {"id": last_id, "error": type(error).__name__}
            print(json.dumps(response), flush=True)
        except (ValueError, TypeError, BrokenPipeError):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

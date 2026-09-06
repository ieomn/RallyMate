#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import os
import re
import stat
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import unquote, urlsplit


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_LOOPBACK_BINDS = {"127.0.0.1", "localhost"}
_PRIVATE_SEGMENTS = {
    ".codex_tmp",
    ".git",
    "blind-handoff",
    "compiled",
    "fs09-phase-truth-m77-v1",
    "raw-exports",
}
_PRIVATE_FILENAMES = {
    "adjudications.csv",
    "annotations.csv",
    "intake-manifest.json",
    "manual-fs09-events.jsonl",
    "sealed-candidates.json",
    "sealed-event-candidates.jsonl",
}


def _is_private_path(parts: tuple[str, ...]) -> bool:
    folded = tuple(part.casefold() for part in parts)
    if any(part.startswith(".") or part in _PRIVATE_SEGMENTS for part in folded):
        return True
    if not folded:
        return True
    filename = folded[-1]
    if filename in _PRIVATE_FILENAMES:
        return True
    return "sealed" in filename and (
        "candidate" in filename or "prediction" in filename
    )


def _public_request_path(target: str) -> str | None:
    parsed = urlsplit(target)
    if parsed.query or parsed.fragment:
        return None
    if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path):
        return None
    try:
        decoded = unquote(parsed.path, errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    # Reject a residual percent sign so a downstream component can never decode
    # the request a second time (for example, %252d -> %2d -> '-').
    if (
        not decoded.startswith("/")
        or "\\" in decoded
        or "\x00" in decoded
        or "%" in decoded
    ):
        return None
    raw_parts = decoded.split("/")[1:]
    if any(part in {"", ".", ".."} for part in raw_parts):
        return None
    path = PurePosixPath(*raw_parts)
    if path.as_posix() != "/".join(raw_parts):
        return None
    if _is_private_path(path.parts):
        return None
    return "/" + path.as_posix()


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _contains_reparse_component(root: Path, candidate: Path) -> bool:
    if _is_reparse_point(root):
        return True
    relative = candidate.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if _is_reparse_point(current):
            return True
    return False


def _resolve_request_target(
    root: Path,
    public_path: str,
    allowed_entries: tuple[tuple[Path, bool], ...],
) -> Path | None:
    relative = PurePosixPath(public_path.removeprefix("/"))
    candidate = root.joinpath(*relative.parts)
    if _contains_reparse_component(root, candidate):
        return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    permitted = False
    for allowed_path, is_directory in allowed_entries:
        if is_directory:
            try:
                resolved.relative_to(allowed_path)
            except ValueError:
                continue
            permitted = True
            break
        if resolved == allowed_path:
            permitted = True
            break
    return resolved if permitted else None


def _resolve_allow_entries(
    root: Path, values: list[str], parser: argparse.ArgumentParser
) -> tuple[tuple[Path, bool], ...]:
    resolved_entries: list[tuple[Path, bool]] = []
    seen: set[Path] = set()
    for value in values:
        if not isinstance(value, str) or value != value.strip() or not value:
            parser.error("--allow must be a non-empty canonical relative path")
        if value == ".":
            parts: tuple[str, ...] = ()
            candidate = root
        else:
            if "\\" in value or value.startswith("/") or "%" in value:
                parser.error("--allow must use a canonical POSIX relative path")
            raw_parts = tuple(value.split("/"))
            if any(part in {"", ".", ".."} for part in raw_parts):
                parser.error("--allow must not contain empty or dot segments")
            path = PurePosixPath(*raw_parts)
            if path.as_posix() != value:
                parser.error("--allow must use a canonical POSIX relative path")
            parts = path.parts
            if _is_private_path(parts):
                parser.error("--allow cannot authorize a private artifact path")
            candidate = root.joinpath(*parts)
        if _contains_reparse_component(root, candidate):
            parser.error("--allow cannot contain symlinks or filesystem reparse points")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            parser.error(f"--allow path is missing or outside --directory: {value}")
        if not (resolved.is_file() or resolved.is_dir()):
            parser.error(f"--allow path must be a regular file or directory: {value}")
        if resolved in seen:
            parser.error(f"duplicate --allow path: {value}")
        seen.add(resolved)
        resolved_entries.append((resolved, resolved.is_dir()))
    return tuple(resolved_entries)


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """Static file handler with single-range byte serving for browser video seek."""

    range_start: int | None = None
    range_end: int | None = None

    def __init__(
        self,
        *args,  # type: ignore[no-untyped-def]
        directory: str,
        allowed_entries: tuple[tuple[Path, bool], ...],
        **kwargs,  # type: ignore[no-untyped-def]
    ) -> None:
        self.document_root = Path(directory).resolve()
        self.allowed_entries = allowed_entries
        super().__init__(*args, directory=directory, **kwargs)

    def _loopback_host_allowed(self) -> bool:
        host = self.headers.get("Host")
        if not isinstance(host, str):
            return False
        port = int(self.server.server_address[1])
        allowed = {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        }
        return host.casefold() in allowed

    def end_headers(self) -> None:
        self.send_header("X-RallyMate-Range-Server", "allowlist-v1")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def list_directory(self, path: str):  # type: ignore[no-untyped-def]
        self.send_error(HTTPStatus.NOT_FOUND, "Directory listing disabled")
        return None

    def send_head(self) -> BinaryIO | None:
        if not self._loopback_host_allowed():
            self.send_error(HTTPStatus.FORBIDDEN, "Loopback Host required")
            return None
        public_path = _public_request_path(self.path)
        if public_path is None:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        path = _resolve_request_target(
            self.document_root, public_path, self.allowed_entries
        )
        if path is None or path.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND, "Directory listing disabled")
            return None
        try:
            source = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        try:
            size = os.fstat(source.fileno()).st_size
            content_type = self.guess_type(str(path))
            requested = self.headers.get("Range")
            self.range_start = None
            self.range_end = None
            if requested:
                match = _RANGE_RE.fullmatch(requested.strip())
                if match is None or size == 0:
                    self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    source.close()
                    return None
                start_text, end_text = match.groups()
                if not start_text:
                    suffix = int(end_text or "0")
                    if suffix <= 0:
                        raise ValueError("invalid suffix range")
                    start = max(0, size - suffix)
                    end = size - 1
                else:
                    start = int(start_text)
                    end = min(size - 1, int(end_text) if end_text else size - 1)
                if start >= size or end < start:
                    raise ValueError("unsatisfiable range")
                self.range_start = start
                self.range_end = end
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                source.seek(start)
            else:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Length", str(size))
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header(
                "Last-Modified", self.date_time_string(os.path.getmtime(path))
            )
            self.end_headers()
            return source
        except ValueError:
            source.close()
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return None
        except Exception:
            source.close()
            raise

    def copyfile(self, source: BinaryIO, outputfile: BinaryIO) -> None:
        if self.range_start is None or self.range_end is None:
            super().copyfile(source, outputfile)
            return
        remaining = self.range_end - self.range_start + 1
        while remaining > 0:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serve RallyMate reports with HTTP byte-range video support"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--allow",
        action="append",
        required=True,
        help=(
            "Canonical path relative to --directory to expose; repeat for each "
            "file or directory. Use '.' only to expose the selected directory tree."
        ),
    )
    args = parser.parse_args()
    if not isinstance(args.bind, str) or args.bind.casefold() not in _LOOPBACK_BINDS:
        parser.error("--bind must be IPv4 loopback: 127.0.0.1 or localhost")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    requested_directory = args.directory.absolute()
    if _is_reparse_point(requested_directory):
        parser.error("--directory cannot be a symlink or filesystem reparse point")
    directory = requested_directory.resolve()
    if not directory.is_dir():
        parser.error(f"directory does not exist: {directory}")
    allowed_entries = _resolve_allow_entries(directory, args.allow, parser)
    handler = functools.partial(
        RangeRequestHandler,
        directory=str(directory),
        allowed_entries=allowed_entries,
    )
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    allowed_display = ", ".join(args.allow)
    print(
        f"Serving {directory} at http://{args.bind}:{args.port} with byte ranges; "
        f"allowlist={allowed_display}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fixed-screen terminal output for the read-only service monitor (stdlib only)."""
from __future__ import annotations

import os
import re
import sys
import unicodedata


_ESCAPE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")
_UNSET = object()


def _plain(text: str) -> str:
    # Status/log text must never be interpreted as terminal control sequences.
    return "".join(" " if unicodedata.category(char).startswith("C") else char
                   for char in _ESCAPE.sub("", text))


def display_width(text: str) -> int:
    """Conservative cell width: CJK/full-width characters occupy two columns."""
    return sum(0 if unicodedata.category(char) in {"Mn", "Me"}
               else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
               for char in _plain(text))


def clip_text(text: str, columns: int) -> str:
    """Clip by terminal cells, without splitting a wide character at the edge."""
    result, used = [], 0
    for char in _plain(str(text)):
        width = (0 if unicodedata.category(char) in {"Mn", "Me"}
                 else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1)
        if used + width > max(0, columns):
            break
        if width or result:
            result.append(char)
        used += width
    return "".join(result)


class _WindowsConsole:
    """VT when available; an isolated native console buffer on older Windows."""

    def __init__(self, stream):
        import ctypes
        from ctypes import wintypes
        import msvcrt

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class RECT(ctypes.Structure):
            _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT),
                        ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]

        class INFO(ctypes.Structure):
            _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD),
                        ("wAttributes", wintypes.WORD), ("srWindow", RECT),
                        ("dwMaximumWindowSize", COORD)]

        class CURSOR(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("bVisible", wintypes.BOOL)]

        self.ctypes, self.types = ctypes, wintypes
        self.COORD, self.INFO, self.CURSOR = COORD, INFO, CURSOR
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "GetConsoleMode": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "SetConsoleMode": ([wintypes.HANDLE, wintypes.DWORD], wintypes.BOOL),
            "GetConsoleScreenBufferInfo": ([wintypes.HANDLE, ctypes.POINTER(INFO)], wintypes.BOOL),
            "GetConsoleCursorInfo": ([wintypes.HANDLE, ctypes.POINTER(CURSOR)], wintypes.BOOL),
            "SetConsoleCursorInfo": ([wintypes.HANDLE, ctypes.POINTER(CURSOR)], wintypes.BOOL),
            "CreateConsoleScreenBuffer": ([wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.c_void_p], wintypes.HANDLE),
            "SetConsoleActiveScreenBuffer": ([wintypes.HANDLE], wintypes.BOOL),
            "SetConsoleCursorPosition": ([wintypes.HANDLE, COORD], wintypes.BOOL),
            "FillConsoleOutputCharacterW": ([wintypes.HANDLE, wintypes.WCHAR, wintypes.DWORD,
                                              COORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "FillConsoleOutputAttribute": ([wintypes.HANDLE, wintypes.WORD, wintypes.DWORD,
                                             COORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "WriteConsoleW": ([wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.original = msvcrt.get_osfhandle(stream.fileno())
        self.handle, self.native = self.original, None
        self.mode = wintypes.DWORD()
        if not self.kernel.GetConsoleMode(self.original, ctypes.byref(self.mode)):
            raise OSError("not a Windows console")
        self.cursor = CURSOR()
        self.has_cursor = bool(self.kernel.GetConsoleCursorInfo(self.original, ctypes.byref(self.cursor)))

    def enable_vt(self) -> bool:
        # ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING.
        return bool(self.kernel.SetConsoleMode(self.original, self.mode.value | 0x0001 | 0x0004))

    def enable_native(self) -> bool:
        handle = self.kernel.CreateConsoleScreenBuffer(0xC0000000, 3, None, 1, None)
        if handle in (None, self.ctypes.c_void_p(-1).value):
            return False
        if not self.kernel.SetConsoleActiveScreenBuffer(handle):
            self.kernel.CloseHandle(handle)
            return False
        self.native = self.handle = handle
        hidden = self.CURSOR(25, False)
        self.kernel.SetConsoleCursorInfo(handle, self.ctypes.byref(hidden))
        return True

    def info(self):
        info = self.INFO()
        if not self.kernel.GetConsoleScreenBufferInfo(self.handle, self.ctypes.byref(info)):
            raise OSError("cannot read terminal dimensions")
        return info

    def size(self) -> tuple[int, int]:
        window = self.info().srWindow
        return window.Right - window.Left + 1, window.Bottom - window.Top + 1

    def draw(self, rows: list[str], previous: list[str], resized: bool) -> None:
        info = self.info()
        width = max(0, info.srWindow.Right - info.srWindow.Left)
        height = max(0, info.srWindow.Bottom - info.srWindow.Top)
        written = self.types.DWORD()
        # A resize can expose cells outside the previous viewport. Clear those
        # with native fill (which cannot scroll), including the reserved edge.
        count = height + 1 if resized else min(height, max(len(rows), len(previous)))
        for index in range(count):
            text = rows[index] if index < len(rows) else ""
            if not resized and index < len(previous) and previous[index] == text:
                continue
            origin = self.COORD(info.srWindow.Left, info.srWindow.Top + index)
            for success in (
                self.kernel.FillConsoleOutputCharacterW(self.handle, " ", width + 1 if resized else width, origin, self.ctypes.byref(written)),
                self.kernel.FillConsoleOutputAttribute(self.handle, info.wAttributes, width + 1 if resized else width, origin, self.ctypes.byref(written)),
                self.kernel.SetConsoleCursorPosition(self.handle, origin),
            ):
                if not success:
                    raise OSError("cannot update console")
            if text and not self.kernel.WriteConsoleW(self.handle, text, len(text.encode("utf-16-le")) // 2,
                                                      self.ctypes.byref(written), None):
                raise OSError("cannot write console")
        self.kernel.SetConsoleCursorPosition(self.handle, self.COORD(info.srWindow.Left, info.srWindow.Top))

    def close(self) -> None:
        if self.native is not None:
            self.kernel.SetConsoleActiveScreenBuffer(self.original)
            self.kernel.CloseHandle(self.native)
            self.native = None
        self.kernel.SetConsoleMode(self.original, self.mode.value)
        if self.has_cursor:
            self.kernel.SetConsoleCursorInfo(self.original, self.ctypes.byref(self.cursor))


class TerminalRenderer:
    """Draw in place; noninteractive outputs receive a single ordinary snapshot.

    ``size()`` reports the full terminal dimensions. Rendering reserves its last
    row and column, preventing both wrapping and terminal scrollback growth.
    ``change_key`` optionally excludes timestamps from duplicate detection.
    The caller should exit after one draw when ``interactive`` is false.
    """

    def __init__(self, stream=None, once: bool = False):
        self.stream = sys.stdout if stream is None else stream
        self.once, self.interactive = once, False
        self._console = None
        self._vt = False
        self._previous: list[str] = []
        self._dimensions = None
        self._last_key = _UNSET
        self._printed = False

    def __enter__(self):
        try:
            tty = bool(self.stream.isatty())
        except (AttributeError, OSError):
            tty = False
        if self.once or not tty:
            return self
        if os.name == "nt":
            try:
                self._console = _WindowsConsole(self.stream)
                self._vt = self._console.enable_vt()
                self.interactive = self._vt or self._console.enable_native()
            except (OSError, ValueError, AttributeError):
                # Some Windows terminal emulators expose a TTY pipe with VT but
                # no Win32 console handle. Only known VT environments qualify.
                self._vt = bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON")
                                or os.environ.get("TERM", "") not in {"", "dumb"})
                self.interactive = self._vt
        else:
            self._vt = os.environ.get("TERM", "") != "dumb"
            self.interactive = self._vt
        if self._vt:
            try:
                self.stream.write("\x1b[?1049h\x1b[?25l\x1b[H\x1b[2J")
                self.stream.flush()
            except BaseException:
                self.__exit__(*sys.exc_info())
                raise
        return self

    def size(self) -> tuple[int, int]:
        if self._console is not None:
            try:
                return self._console.size()
            except OSError:
                pass
        try:
            size = os.get_terminal_size(self.stream.fileno())
            return max(1, size.columns), max(1, size.lines)
        except (AttributeError, OSError, ValueError):
            return 120, 40

    def draw(self, content: str, change_key=None) -> None:
        if not self.interactive:
            if self._printed:
                return
            self.stream.write("\n".join(_plain(line) for line in content.splitlines()) + "\n")
            if not self.once:
                self.stream.write("当前输出不支持固定屏幕，已显示一次快照。请在交互终端运行，或使用 -Once。\n")
            self.stream.flush()
            self._printed = True
            return
        dimensions = self.size()
        columns, lines = dimensions
        rows = [clip_text(line, max(0, columns - 1)) for line in content.splitlines()[:max(0, lines - 1)]]
        key = rows if change_key is None else change_key
        resized = dimensions != self._dimensions
        if not resized and key == self._last_key:
            return
        if self._vt:
            fragments = ["\x1b[2J"] if resized else []
            for index in range(min(max(0, lines - 1), max(len(rows), len(self._previous)))):
                line = rows[index] if index < len(rows) else ""
                if resized or index >= len(self._previous) or self._previous[index] != line:
                    fragments.append(f"\x1b[{index + 1};1H\x1b[2K{line}")
            if fragments:
                self.stream.write("".join(fragments) + "\x1b[H")
                self.stream.flush()
        else:
            self._console.draw(rows, self._previous, resized)
        self._previous, self._dimensions, self._last_key = rows, dimensions, key

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self._vt:
                self.stream.write("\x1b[?25h\x1b[?1049l")
                self.stream.flush()
        finally:
            if self._console is not None:
                self._console.close()
            self.interactive, self._vt = False, False
        return False

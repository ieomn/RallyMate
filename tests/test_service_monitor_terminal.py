from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "service_monitor_terminal", Path(__file__).resolve().parents[1] / "scripts/service_monitor_terminal.py")
terminal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(terminal)


class TtyStream(io.StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 1


class FakeConsole:
    def __init__(self, stream, vt=False, native=True):
        self.vt, self.native = vt, native
        self.closed = False
        self.frames = []

    def enable_vt(self):
        return self.vt

    def enable_native(self):
        return self.native

    def size(self):
        return 9, 4

    def draw(self, rows, previous, resized):
        self.frames.append((rows, previous, resized))

    def close(self):
        self.closed = True


class TerminalRendererTests(unittest.TestCase):
    def test_chinese_combining_and_escape_text_are_bounded_by_cells(self):
        self.assertEqual(terminal.display_width("上传 A"), 6)
        self.assertEqual(terminal.clip_text("上传完成ABC", 5), "上传")
        self.assertEqual(terminal.clip_text("Ae\u0301中B", 4), "Ae\u0301中")
        self.assertEqual(terminal.display_width("Ae\u0301中"), 4)
        self.assertEqual(terminal.clip_text("中", 1), "")
        self.assertEqual(terminal.clip_text("abc", 0), "")
        self.assertEqual(terminal.clip_text("\x1b[2J任务\x1b]0;evil\x07\r", 10), "任务 ")

    def test_vt_frames_never_write_newline_or_touch_last_row_and_column(self):
        stream = TtyStream()
        with patch.object(terminal.os, "name", "posix"), patch.dict(os.environ, {"TERM": "xterm"}), \
                patch.object(terminal.os, "get_terminal_size", return_value=os.terminal_size((9, 4))):
            with terminal.TerminalRenderer(stream) as renderer:
                self.assertTrue(renderer.interactive)
                self.assertEqual(renderer.size(), (9, 4))
                renderer.draw("上传中ABCDEF\n第二行abcdef\nthird row long\nMUST NOT APPEAR")
                output = stream.getvalue()
                self.assertNotIn("\n", output)
                self.assertNotIn("MUST NOT APPEAR", output)
                self.assertNotIn("\x1b[4;", output)
                self.assertIn("上传中AB", output)
                self.assertNotIn("上传中ABC", output)
                renderer.draw("short")
                delta = stream.getvalue()[len(output):]
                self.assertIn("\x1b[2;1H\x1b[2K", delta)
                self.assertIn("\x1b[3;1H\x1b[2K", delta)
            self.assertTrue(stream.getvalue().endswith("\x1b[?25h\x1b[?1049l"))

    def test_change_key_suppresses_clock_only_updates_but_not_resize(self):
        stream = TtyStream()
        with patch.object(terminal.os, "name", "posix"), patch.dict(os.environ, {"TERM": "xterm"}), \
                patch.object(terminal.os, "get_terminal_size", return_value=os.terminal_size((20, 5))) as size:
            with terminal.TerminalRenderer(stream) as renderer:
                renderer.draw("clock 1\nsame status", change_key="status")
                first = stream.getvalue()
                renderer.draw("clock 2\nsame status", change_key="status")
                self.assertEqual(stream.getvalue(), first)
                size.return_value = os.terminal_size((10, 3))
                renderer.draw("clock 3\nsame status", change_key="status")
                self.assertGreater(len(stream.getvalue()), len(first))

    def test_one_cell_window_does_not_scroll_and_duplicate_content_is_skipped(self):
        stream = TtyStream()
        with patch.object(terminal.os, "name", "posix"), patch.dict(os.environ, {"TERM": "xterm"}), \
                patch.object(terminal.os, "get_terminal_size", return_value=os.terminal_size((1, 1))):
            with terminal.TerminalRenderer(stream) as renderer:
                renderer.draw("cannot fit\nmore")
                first = stream.getvalue()
                renderer.draw("cannot fit\nmore")
                self.assertEqual(stream.getvalue(), first)
                self.assertNotIn("cannot fit", first)
                self.assertNotIn("\n", first)

    def test_redirected_output_gets_one_snapshot_and_actionable_hint(self):
        stream = io.StringIO()
        with terminal.TerminalRenderer(stream) as renderer:
            self.assertFalse(renderer.interactive)
            renderer.draw("first\n上传")
            renderer.draw("first\n上传")
            renderer.draw("changed")
        self.assertEqual(stream.getvalue().count("first"), 1)
        self.assertNotIn("changed", stream.getvalue())
        self.assertIn("-Once", stream.getvalue())
        self.assertNotIn("\x1b", stream.getvalue())

    def test_once_is_plain_and_does_not_activate_terminal(self):
        stream = TtyStream()
        with patch.object(terminal, "_WindowsConsole") as console:
            with terminal.TerminalRenderer(stream, once=True) as renderer:
                renderer.draw("snapshot")
            console.assert_not_called()
        self.assertEqual(stream.getvalue(), "snapshot\n")

    def test_dumb_terminal_falls_back_to_one_snapshot(self):
        stream = TtyStream()
        with patch.object(terminal.os, "name", "posix"), patch.dict(os.environ, {"TERM": "dumb"}):
            with terminal.TerminalRenderer(stream) as renderer:
                self.assertFalse(renderer.interactive)
                renderer.draw("status")
        self.assertNotIn("\x1b", stream.getvalue())

    def test_windows_native_fallback_uses_clipped_rows_and_restores_after_interrupt(self):
        stream = TtyStream()
        console = FakeConsole(stream)
        with patch.object(terminal.os, "name", "nt"), patch.object(terminal, "_WindowsConsole", return_value=console):
            with self.assertRaises(KeyboardInterrupt):
                with terminal.TerminalRenderer(stream) as renderer:
                    self.assertTrue(renderer.interactive)
                    renderer.draw("上传中ABCDEF\n2\n3\n4")
                    raise KeyboardInterrupt()
        self.assertEqual(console.frames[0][0], ["上传中AB", "2", "3"])
        self.assertTrue(console.closed)
        self.assertEqual(stream.getvalue(), "")

    def test_windows_vt_restores_console_mode_after_render_error(self):
        stream = TtyStream()
        console = FakeConsole(stream, vt=True)
        with patch.object(terminal.os, "name", "nt"), patch.object(terminal, "_WindowsConsole", return_value=console):
            with self.assertRaises(ValueError):
                with terminal.TerminalRenderer(stream):
                    raise ValueError("status read failed")
        self.assertTrue(console.closed)
        self.assertTrue(stream.getvalue().endswith("\x1b[?25h\x1b[?1049l"))

    def test_windows_without_vt_or_native_console_does_not_print_control_codes(self):
        stream = TtyStream()
        console = FakeConsole(stream, native=False)
        with patch.object(terminal.os, "name", "nt"), patch.object(terminal, "_WindowsConsole", return_value=console):
            with terminal.TerminalRenderer(stream) as renderer:
                self.assertFalse(renderer.interactive)
                renderer.draw("status")
        self.assertNotIn("\x1b", stream.getvalue())
        self.assertTrue(console.closed)


if __name__ == "__main__":
    unittest.main()

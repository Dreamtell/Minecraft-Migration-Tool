"""Isolated live-log, retention, persistence and user-override regressions."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_temp = tempfile.TemporaryDirectory(prefix="mctool-live-log-")
os.environ["MCTOOL_CONFIG"] = str(Path(_temp.name) / "config.json")
os.environ["MCTOOL_LANG"] = "zh"
Path(os.environ["MCTOOL_CONFIG"]).write_text('{"language":"zh"}', encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from _testcfg import use_config as _use_config   # noqa: E402
_CFG_PATH = os.environ["MCTOOL_CONFIG"]


def setUpModule():                     # 全量跑时各模块各用各的临时配置
    _use_config(_CFG_PATH)
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from ui.live_log import LiveLogView
from ui.mw_log import LogMixin
from utils import anim, secrets
from utils.session_log import SessionLog
from utils.theme import LIGHT_THEME


class SessionLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_temp.name)
        self.store = SessionLog(self.temp.name)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_full_file_survives_tail_limit_clear_and_two_megabytes(self):
        for n in range(12000):
            self.store.append("row %05d %s" % (n, "x" * 190))
        self.assertEqual(len(self.store.lines), 5000)
        text = self.store.path.read_text(encoding="utf-8")
        self.assertGreater(self.store.path.stat().st_size, 2 * 1024 * 1024)
        self.assertEqual(len(text.splitlines()), 12000)
        self.assertTrue(text.startswith("row 00000"))
        self.assertIn("row 11999", text)
        self.store.clear()
        self.store.append("after clear")
        text = self.store.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("row 00000"))
        self.assertTrue(text.endswith("after clear\n"))

    def test_workers_do_not_drop_or_interleave_lines(self):
        def writer(worker):
            for n in range(400):
                self.store.append("%d:%d" % (worker, n))
        workers = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        lines = self.store.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1600)
        self.assertEqual(set(lines), {"%d:%d" % (i, n) for i in range(4) for n in range(400)})

    def test_sessions_use_separate_files_and_save_false_stays_in_view(self):
        self.store.append("ui only", save=False)
        self.assertIsNone(self.store.path)
        self.store.append("first")
        other = SessionLog(self.temp.name)
        try:
            other.append("second")
            self.assertNotEqual(self.store.path, other.path)
            self.assertNotIn("ui only", self.store.path.read_text(encoding="utf-8"))
        finally:
            other.close()

    def test_disk_failure_is_exposed_without_breaking_live_output(self):
        with mock.patch.object(Path, "open", side_effect=OSError("disk full")):
            self.store.append("error details", "ERROR")
        self.assertEqual(self.store.error, "disk full")
        self.assertIn("error details", self.store.snapshot()[2][-1][2])

    def test_log_entry_redacts_before_persisting(self):
        host = LogMixin()
        host._init_execution_log()
        host._session_log = self.store
        host._persistable = lambda *_: True
        with mock.patch.object(secrets, "redact", return_value="token=[REDACTED]"):
            host.log("token=example-private-token")
        text = self.store.path.read_text(encoding="utf-8")
        self.assertNotIn("example-private-token", text)
        self.assertIn("[REDACTED]", text)

    def test_repeated_errors_are_preserved(self):
        host = LogMixin()
        host._init_execution_log()
        host._session_log = self.store
        host._persistable = lambda *_: True
        host.log("same error", "ERROR")
        host.log("same error", "ERROR")
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), "same error\nsame error\n")


class LiveLogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("640x300+60+60")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(repr(args))
        self.holds = anim._hr_holders
        self.store = SessionLog(Path(_temp.name) / "views")
        self.text = ScrolledText(self.root, wrap="word", state="disabled", undo=False)
        self.text.pack(fill="both", expand=True)
        self.view = LiveLogView(self.text, self.root, self.store, LIGHT_THEME)
        self.root.update_idletasks()
        self.wait(30)  # Map the native Text before checking its floating child.

    def tearDown(self):
        self.root.destroy()
        self.store.close()
        self.assertEqual(anim._hr_holders, self.holds)
        self.assertFalse(self.errors, self.errors)

    def append(self, start, count, prefix="line"):
        for n in range(start, start + count):
            self.store.append("%s %05d" % (prefix, n), "ERROR" if n % 7 == 0 else "INFO")
        self.view.refresh()
        self.root.update_idletasks()

    def wait(self, ms):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def test_bottom_follows_and_only_paused_view_shows_button(self):
        self.append(0, 200)
        self.assertTrue(self.view.at_bottom())
        self.assertFalse(self.view.button.winfo_ismapped())
        self.view._scrollbar("moveto", .2)
        self.root.update_idletasks()
        self.assertFalse(self.view.following)
        self.assertTrue(self.view.button.winfo_ismapped())
        before = self.text.get("1.0", "end-1c")
        anchor = self.text.index("@0,0")
        self.append(200, 12)
        self.assertEqual(self.text.get("1.0", "end-1c"), before)
        self.assertEqual(self.text.index("@0,0"), anchor)
        self.assertEqual(self.view.unread, 12)
        self.assertIn("12", self.view.button.get_text())
        self.view.button.invoke()
        self.root.update_idletasks()
        self.assertTrue(self.view.at_bottom())
        self.assertTrue(self.view.following)
        self.assertEqual(self.view.unread, 0)
        self.assertFalse(self.view.button.winfo_ismapped())
        self.assertIn("line 00211", self.text.get("1.0", "end-1c"))

    def test_wheel_override_wins_when_output_arrives_before_first_frame(self):
        self.append(0, 200)
        self.view.scroller.wheel(SimpleNamespace(delta=120))
        self.append(200, 20)
        self.wait(200)
        self.assertFalse(self.view.following)
        self.assertFalse(self.view.at_bottom())
        self.assertEqual(self.view.unread, 20)
        self.assertNotIn("line 00200", self.text.get("1.0", "end-1c"))

    def test_manual_scroll_to_bottom_resumes_following(self):
        self.append(0, 200)
        self.text.yview_moveto(.3)
        self.root.update_idletasks()
        self.append(200, 3)
        self.assertFalse(self.view.following)
        self.text.yview_moveto(1)
        self.root.update_idletasks()
        self.assertTrue(self.view.following)
        self.assertEqual(self.view.unread, 0)
        self.assertIn("line 00202", self.text.get("1.0", "end-1c"))

    def test_keyboard_page_up_pauses_and_control_end_resumes(self):
        self.append(0, 200)
        self.text.focus_force()
        self.wait(20)
        self.text.event_generate("<KeyPress-Prior>")
        self.wait(20)
        self.assertFalse(self.view.following)
        self.append(200, 5)
        self.assertEqual(self.view.unread, 5)
        self.text.event_generate("<Control-End>")
        self.wait(20)
        self.assertTrue(self.view.following)

    def test_paused_reading_survives_more_than_entire_cache(self):
        self.append(0, 5000)
        self.view._scrollbar("moveto", .01)
        self.root.update_idletasks()
        anchor = self.text.get("@0,0 linestart", "@0,0 lineend")
        self.append(5000, 9000)
        self.assertEqual(self.text.get("@0,0 linestart", "@0,0 lineend"), anchor)
        self.assertEqual(self.view.unread, 9000)
        self.assertEqual(int(self.text.index("end-1c").split(".")[0]) - 1, 5000)
        self.view.jump_to_bottom()
        self.root.update_idletasks()
        contents = self.text.get("1.0", "end-1c")
        self.assertEqual(len(contents.splitlines()), 5000)
        self.assertTrue(contents.startswith("line 09000\n"))
        self.assertTrue(contents.endswith("line 13999\n"))
        self.assertEqual(len(self.store.path.read_text(encoding="utf-8").splitlines()), 14000)

    def test_clear_resets_paused_view_without_resurrecting_queued_lines(self):
        self.append(0, 200)
        self.view._scrollbar("moveto", .2)
        self.append(200, 10)
        self.store.clear()
        self.view.refresh()
        self.root.update_idletasks()
        self.assertTrue(self.view.following)
        self.assertEqual(self.view.unread, 0)
        self.assertEqual(self.text.get("1.0", "end-1c"), "")
        self.append(210, 1)
        self.assertEqual(self.text.get("1.0", "end-1c"), "line 00210\n")

    def test_two_views_have_independent_follow_states_and_colors(self):
        top = tk.Toplevel(self.root)
        other_text = ScrolledText(top, state="disabled")
        other_text.pack(fill="both", expand=True)
        other = LiveLogView(other_text, top, self.store, LIGHT_THEME)
        self.append(0, 200)
        other.refresh()
        self.root.update_idletasks()
        other._scrollbar("moveto", .1)
        self.append(200, 10)
        other.refresh()
        self.root.update_idletasks()
        self.assertTrue(self.view.following)
        self.assertFalse(other.following)
        self.assertEqual(other.unread, 10)
        self.assertIn("ERROR", self.text.tag_names("1.0"))
        self.assertNotIn("line 00209", other_text.get("1.0", "end-1c"))
        top.destroy()

    def test_wrapped_multiline_text_stays_visible_and_counts_logical_lines(self):
        self.store.append(("very long wrapped text " * 40 + "\n") * 3 + "last line", "ERROR")
        self.view.refresh()
        self.root.update_idletasks()
        self.assertTrue(self.view.at_bottom())
        self.view._scrollbar("moveto", 0)
        self.store.append("new\nsecond\nthird", "WARNING")
        self.view.refresh()
        self.assertEqual(self.view.unread, 3)
        self.view.jump_to_bottom()
        self.root.update_idletasks()
        self.assertTrue(self.view.at_bottom())
        self.assertIn("WARNING", self.text.tag_names("5.0"))

    def test_completion_while_paused_does_not_jump(self):
        self.append(0, 200)
        self.view._scrollbar("moveto", .2)
        host = LogMixin()
        host.root = self.root
        host._session_log = self.store
        host._log_view = self.view
        events = []
        host.when_log_bottom(lambda: events.append("finished"))
        self.wait(30)
        self.assertEqual(events, ["finished"])
        self.assertFalse(self.view.following)
        self.assertFalse(self.view.at_bottom())


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _temp.cleanup()

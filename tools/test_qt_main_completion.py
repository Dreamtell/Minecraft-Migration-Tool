"""Regression coverage for the remaining Qt main-window parity work."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-completion-")
CFG = Path(_tmp.name) / "config.json"
CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")
os.environ["MCTOOL_CONFIG"] = str(CFG)
os.environ["MCTOOL_LANG"] = "zh"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from _testcfg import use_config as _use_config  # noqa: E402

_CFG_PATH = os.environ["MCTOOL_CONFIG"]


def setUpModule():
    _use_config(_CFG_PATH)


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from utils import config as _cfg  # noqa: E402
from utils.theme import DARK_THEME  # noqa: E402

_cfg.CONFIG_FILE = CFG
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms=30):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


class MainWindowCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.Window = QtMainWindow

    def setUp(self):
        self.window = self.Window()
        self.window.show()
        pump()

    def tearDown(self):
        self.window._stop_countdown_timer()
        self.window.hide()
        self.window._close_session_log()
        self.window.deleteLater()
        pump()

    def test_empty_hints_initialize_before_list_pages_and_track_content(self):
        hints = self.window._empty_hints
        self.assertEqual(len(hints), 3)
        self.assertTrue(all(not hint.isHidden() for hint in hints))

        self.window.mod_text.setPlainText("example.jar")
        pump()
        self.assertTrue(hints[0].isHidden())
        self.window.mod_text.clear()
        pump()
        self.assertFalse(hints[0].isHidden())

    def test_log_double_click_dispatches_the_clicked_block(self):
        from ui.qt_main.window import LogEdit

        seen = []
        editor = LogEdit(on_double_click=seen.append)
        editor.setPlainText("first\nsecond")
        editor.resize(360, 160)
        editor.show()
        pump()
        cursor = QtGui.QTextCursor(editor.document().findBlockByNumber(1))
        point = editor.cursorRect(cursor).center()

        from PySide6.QtTest import QTest
        QTest.mouseDClick(editor, QtCore.Qt.LeftButton, pos=point)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].blockNumber(), 1)
        editor.deleteLater()

    def test_log_levels_match_overview_markers_and_plain_summary_color(self):
        from ui.qt_main.window import QtMainWindow
        from ui.qt_main.log_overview import _LEVELS

        codes = QtMainWindow._LOG_LEVEL_CODES
        self.assertEqual(codes["WARNING"], 2)
        self.assertEqual(codes["ERROR"], 3)
        self.assertEqual(codes["SUCCESS"], 4)
        self.assertEqual(codes["SIMULATE"], 5)
        self.assertEqual(codes["PLAIN"], 6)

        # 总览条自己写了一份级别表：两张表脱钩时总览条会静默少画点，这里必须对账。
        for code, (name, _color_key, _order) in _LEVELS.items():
            self.assertEqual(codes[name], code, name)           # window → 总览条
        for name in ("WARNING", "ERROR", "SUCCESS", "SIMULATE"):
            self.assertIn(codes[name], _LEVELS, name)           # 总览条 → window（双向）

        self.window.log("missing summary", "PLAIN")
        block = self.window.log_text.document().findBlockByNumber(
            self.window.log_text.document().blockCount() - 2)
        self.assertEqual(block.userState(), 6)
        cursor = QtGui.QTextCursor(block)
        cursor.movePosition(QtGui.QTextCursor.NextCharacter)
        self.assertEqual(cursor.charFormat().foreground().color().name(),
                         self.window.theme["log_error_fg"])

    def test_log_height_tracks_large_windows_and_caps_growth(self):
        self.window.resize(1000, 1200)
        self.window._resize_log_area()
        self.assertEqual(self.window.log_text.height(), 214)
        self.window.resize(1000, 1600)
        self.window._resize_log_area()
        self.assertEqual(self.window.log_text.height(), 340)

    def test_countdown_flips_back_and_runs_a_visible_zero_state(self):
        now = [100.0]
        prepared = mock.Mock()
        with mock.patch("ui.qt_main.window.time.perf_counter", side_effect=lambda: now[0]), \
                mock.patch.object(self.window, "_begin_prepare", prepared):
            self.window._begin_countdown(2, {"dry_run": False})
            self.assertTrue(self.window.start_btn.is_back())
            self.assertEqual(self.window.start_btn._progress, 1.0)

            now[0] += 0.5
            self.window._countdown_tick()
            self.assertAlmostEqual(self.window.start_btn._progress, 0.75, places=2)

            now[0] = self.window._countdown_end + 0.01
            self.window._countdown_tick()
            self.assertIn("0 秒后开始", self.window.start_btn._labels["back"])
            self.assertEqual(self.window.start_btn._progress, 0.0)
            prepared.assert_not_called()

            now[0] += 0.91
            self.window._countdown_tick()
            prepared.assert_called_once_with({"dry_run": False})
            self.assertIsNone(self.window._countdown_timer)

    def test_cancel_countdown_returns_button_to_front(self):
        self.window._begin_countdown(5, {})
        self.window._cancel_countdown()
        self.assertFalse(self.window.start_btn.is_back())
        self.assertIsNone(self.window.start_btn._progress)

    def test_main_qss_styles_only_named_list_and_log_scrollbars(self):
        from ui.qt_main import look
        import re

        qss = look.build_qss(DARK_THEME)
        for name in ("modListEdit", "configListEdit", "extraListEdit", "logEdit"):
            self.assertIn(f"QTextEdit#{name} QScrollBar:vertical", qss)
        # 反向:不能出现「不带 objectName 限定」的 QTextEdit 滚动条规则 ——
        # 那会把差异窗、放大查看里的 QTextEdit 一起改掉,而它们的滚动条要保持原生。
        self.assertIsNone(
            re.search(r"QTextEdit\s+QScrollBar", qss),
            "出现了未限定 objectName 的 QTextEdit 滚动条规则")
        self.assertIsNone(
            re.search(r"QTextEdit\s*\{[^}]*QScrollBar", qss, re.S),
            "滚动条样式混进了全局 QTextEdit 规则")


if __name__ == "__main__":
    unittest.main()

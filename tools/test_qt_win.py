# -*- coding: utf-8 -*-
"""ui/qt_win.fit_window 的尺寸钳制测试（离屏跑，不依赖真实显示器）。

跑法：python tools/test_qt_win.py
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Minecraft迁移工具"))

from PySide6 import QtCore, QtWidgets      # noqa: E402

from ui import qt_win                     # noqa: E402


def _screen(w, h):
    return QtCore.QRect(0, 0, w, h)


class FitWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self._orig = qt_win._available
        self.addCleanup(lambda: setattr(qt_win, "_available", self._orig))

    def _dlg(self):
        d = QtWidgets.QDialog()
        self.addCleanup(d.deleteLater)
        return d

    def test_normal_screen_uses_wanted_size(self):
        qt_win._available = lambda screen=None: _screen(1920, 1040)
        d = self._dlg()
        got = qt_win.fit_window(d, 720, 520, 940, 700)
        self.assertEqual(got, (940, 700))
        self.assertEqual((d.width(), d.height()), (940, 700))
        self.assertEqual((d.minimumWidth(), d.minimumHeight()), (720, 520))

    def test_small_screen_clamps_want_but_keeps_min(self):
        # 800x600 → 可用上限 720x540
        qt_win._available = lambda screen=None: _screen(800, 600)
        d = self._dlg()
        got = qt_win.fit_window(d, 400, 300, 1000, 800)
        self.assertEqual(got, (720, 540))
        self.assertEqual((d.width(), d.height()), (720, 540))
        self.assertEqual((d.minimumWidth(), d.minimumHeight()), (400, 300))

    def test_min_bigger_than_screen_is_capped_too(self):
        """最小尺寸也不能大过屏幕：否则窗口一出屏就拉不回来。"""
        qt_win._available = lambda screen=None: _screen(800, 600)
        d = self._dlg()
        got = qt_win.fit_window(d, 1200, 900, 1200, 900)
        self.assertEqual(got, (720, 540))
        self.assertEqual((d.minimumWidth(), d.minimumHeight()), (720, 540))

    def test_want_smaller_than_min_is_raised(self):
        qt_win._available = lambda screen=None: _screen(1920, 1040)
        d = self._dlg()
        got = qt_win.fit_window(d, 720, 520, 300, 200)
        self.assertEqual(got, (720, 520))

    def test_no_screen_uses_raw_values(self):
        qt_win._available = lambda screen=None: None
        d = self._dlg()
        got = qt_win.fit_window(d, 500, 400, 900, 700)
        self.assertEqual(got, (900, 700))
        self.assertEqual((d.minimumWidth(), d.minimumHeight()), (500, 400))

    def test_min_floor_is_sane(self):
        """接线错误（传 0 或负数）也别给个 0x0 的窗口。"""
        qt_win._available = lambda screen=None: _screen(1920, 1040)
        d = self._dlg()
        qt_win.fit_window(d, 0, -5, 10, 10)
        self.assertGreaterEqual(d.minimumWidth(), 240)
        self.assertGreaterEqual(d.minimumHeight(), 160)


if __name__ == "__main__":
    unittest.main(verbosity=2)

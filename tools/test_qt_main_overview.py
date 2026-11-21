"""Focused interaction tests for the Qt log overview bar."""

import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from ui.qt_main.log_overview import LogOverview  # noqa: E402


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
THEME = {
    "bg": "#2e2e2e",
    "fg": "#ffffff",
    "accent_fg": "#4a90d9",
    "log_warning_fg": "#ffb74d",
    "log_error_fg": "#ef5350",
    "log_success_fg": "#66bb6a",
    "log_simulate_fg": "#64b5f6",
}


def pump(ms=120):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


class LogOverviewTests(unittest.TestCase):
    def make(self, lines, states, width=360, height=180):
        host = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        editor = QtWidgets.QTextEdit()
        editor.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        editor.setPlainText("\n".join(lines) + "\n")
        block = editor.document().begin()
        index = 0
        while block.isValid():
            if index in states:
                block.setUserState(states[index])
            index += 1
            block = block.next()
        overview = LogOverview(editor, THEME, host)
        layout.addWidget(editor, 1)
        layout.addWidget(overview)
        host.resize(width, height)
        host.show()
        pump(140)
        overview._rebuild()
        self.addCleanup(host.close)
        self.addCleanup(host.deleteLater)
        return host, editor, overview

    def marker_for(self, overview, block_number):
        return next(item for item in overview._markers if item[1] == block_number)

    def test_markers_use_paragraph_start_even_when_wrapped(self):
        long_line = "wrapped message " * 120
        _host, editor, overview = self.make([long_line, "ordinary"], {0: 3})
        # Qt 6.11 keeps QTextBlock.lineCount() at one for wrapped paragraphs;
        # the layout owns the actual visual line count.
        self.assertGreater(editor.document().findBlockByNumber(0).layout().lineCount(), 1)
        rect = editor.document().documentLayout().blockBoundingRect(
            editor.document().findBlockByNumber(0))
        expected_y = round(rect.top() * overview.height() /
                           editor.document().documentLayout().documentSize().height())
        self.assertEqual(self.marker_for(overview, 0)[0], expected_y)

    def test_error_wins_when_levels_share_a_pixel(self):
        _host, _editor, overview = self.make(
            ["warning", "error", "success", "simulate"], {0: 2, 1: 3, 2: 4, 3: 5})
        overview.setFixedHeight(1)
        pump(20)
        overview._rebuild()
        self.assertEqual(len(overview._segments), 1)
        self.assertEqual(overview._segments[0][2], 3)

    def test_click_hidden_marker_inside_minimum_viewport_box_jumps_to_block(self):
        lines = ["ordinary %d" % i for i in range(240)]
        lines[80] = "failure that should be visible after jump"
        _host, editor, overview = self.make(lines, {80: 3}, height=180)
        block = editor.document().findBlockByNumber(82)
        editor.verticalScrollBar().setValue(round(
            editor.document().documentLayout().blockBoundingRect(block).top()))
        pump(20)
        marker_y, block_number, _state = self.marker_for(overview, 80)
        box = overview._viewport_box()
        self.assertIsNotNone(box)
        self.assertTrue(box[0] <= marker_y <= box[1])
        self.assertFalse(overview._block_start_visible(block_number))

        from PySide6.QtTest import QTest
        QTest.mouseClick(overview, QtCore.Qt.LeftButton, pos=QtCore.QPoint(6, marker_y))
        pump(30)

        self.assertTrue(overview._block_start_visible(block_number))

    def test_click_visible_marker_inside_viewport_box_grabs_without_jumping(self):
        _host, editor, overview = self.make(["error", "ordinary"] +
                                            ["line %d" % i for i in range(80)], {0: 3})
        marker_y, block_number, _state = self.marker_for(overview, 0)
        before = editor.verticalScrollBar().value()
        box = overview._viewport_box()
        self.assertTrue(box[0] <= marker_y <= box[1])
        self.assertTrue(overview._block_start_visible(block_number))

        from PySide6.QtTest import QTest
        QTest.mousePress(overview, QtCore.Qt.LeftButton, pos=QtCore.QPoint(6, marker_y))
        pump(20)

        self.assertTrue(overview._dragging)
        self.assertEqual(editor.verticalScrollBar().value(), before)
        QTest.mouseRelease(overview, QtCore.Qt.LeftButton, pos=QtCore.QPoint(6, marker_y))

    def test_scroll_only_moves_viewport_overlay(self):
        _host, editor, overview = self.make(["line %d" % i for i in range(200)], {30: 2})
        cached = overview._pixmap
        bar = editor.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        pump(10)
        self.assertIs(overview._pixmap, cached)
        self.assertFalse(overview._refresh_timer.isActive())

    def test_dragging_viewport_scrolls_without_rebuilding_map(self):
        _host, editor, overview = self.make(["line %d" % i for i in range(200)], {30: 2})
        box = overview._viewport_box()
        before = editor.verticalScrollBar().value()
        cached = overview._pixmap
        from PySide6.QtTest import QTest
        start = QtCore.QPoint(6, round((box[0] + box[1]) / 2))
        finish = QtCore.QPoint(6, min(overview.height() - 1, start.y() + 30))
        QTest.mousePress(overview, QtCore.Qt.LeftButton, pos=start)
        QTest.mouseMove(overview, finish, delay=10)
        QTest.mouseRelease(overview, QtCore.Qt.LeftButton, pos=finish)
        pump(20)
        self.assertGreater(editor.verticalScrollBar().value(), before)
        self.assertIs(overview._pixmap, cached)

    def test_replacement_and_truncation_rebuild_marker_block_numbers(self):
        _host, editor, overview = self.make(["old %d" % i for i in range(80)], {60: 3})
        self.assertIn(60, {item[1] for item in overview._markers})

        editor.setPlainText("\n".join("new %d" % i for i in range(80)) + "\n")
        block = editor.document().findBlockByNumber(24)
        block.setUserState(5)
        overview._rebuild()
        self.assertNotIn(60, {item[1] for item in overview._markers})
        self.assertEqual(self.marker_for(overview, 24)[2], 5)

        cursor = QtGui.QTextCursor(editor.document())
        cursor.movePosition(QtGui.QTextCursor.Start)
        cursor.movePosition(QtGui.QTextCursor.Down, QtGui.QTextCursor.KeepAnchor, 10)
        cursor.removeSelectedText()
        pump(120)
        overview._rebuild()
        self.assertEqual(self.marker_for(overview, 14)[2], 5)
        self.assertTrue(all(block_no < 70 for _y, block_no, _state in overview._markers))


if __name__ == "__main__":
    unittest.main()

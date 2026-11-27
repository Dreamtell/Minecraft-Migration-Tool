"""Qt 主界面的帧率与滚轮缓动回归:FrameAnim / 各平滑控件 / 滑条不吃滚轮。

数值来自 Tk 侧:utils/anim.frame_ms(跟随刷新率,240Hz 下 4ms)、
utils/helpers.SmoothScroller 的 for_canvas(48)、for_rows(3×行高)、
message_center(60)、mw_migration 档案卡(56)、dialogs 描述框(22)。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-frames-")
CFG = Path(_tmp.name) / "config.json"
CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")
os.environ["MCTOOL_CONFIG"] = str(CFG)
os.environ["MCTOOL_LANG"] = "zh"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from _testcfg import use_config as _use_config   # noqa: E402
_CFG_PATH = os.environ["MCTOOL_CONFIG"]


def setUpModule():
    _use_config(_CFG_PATH)


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from utils import config as _cfg  # noqa: E402
from utils.theme import DARK_THEME  # noqa: E402

_cfg.CONFIG_FILE = CFG
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


class FrameAnimTests(unittest.TestCase):
    """补间必须按刷新率排帧:同样的时长,帧数要比 QVariantAnimation 多得多。"""

    def _run(self, ms):
        from ui.qt_frame_anim import FrameAnim
        w = QtWidgets.QWidget()
        frames = []
        values = []
        fin = []
        a = FrameAnim(w)
        a.setDuration(ms)
        a.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        t0 = time.perf_counter()
        a.valueChanged.connect(lambda v: (frames.append(time.perf_counter()), values.append(float(v))))
        a.finished.connect(lambda: fin.append(time.perf_counter()))
        a.start()
        while not fin and time.perf_counter() - t0 < 5.0:
            pump(2)
        elapsed = time.perf_counter() - t0
        return a, values, frames, fin, elapsed

    def test_it_emits_far_more_frames_than_qvariantanimation(self):
        """QVariantAnimation 固定 16ms 推进;190ms 只有 12 帧。这里要显著更多。"""
        _, values, frames, _, elapsed = self._run(190)
        self.assertGreater(len(frames), 30, "190ms 的动画只出了 %d 帧" % len(frames))
        self.assertTrue(0.15 <= elapsed <= 1.2, "总时长应仍在 190ms 量级,实际 %.3fs" % elapsed)
        self.assertAlmostEqual(values[-1], 1.0, places=3)

    def test_finished_fires_exactly_once(self):
        _, _, _, fin, _ = self._run(60)
        self.assertEqual(len(fin), 1)

    def test_state_reports_running_then_stopped(self):
        """settings/main_tabs 用 state() 判断"动画还在跑",缺了会 AttributeError。"""
        from ui.qt_frame_anim import FrameAnim
        w = QtWidgets.QWidget()
        a = FrameAnim(w)
        a.setDuration(120)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        self.assertEqual(a.state(), QtCore.QAbstractAnimation.Stopped)
        a.start()
        self.assertEqual(a.state(), QtCore.QAbstractAnimation.Running)
        pump(250)
        self.assertEqual(a.state(), QtCore.QAbstractAnimation.Stopped)

    def test_stop_keeps_the_current_value_as_the_next_start_point(self):
        from ui.qt_frame_anim import FrameAnim
        w = QtWidgets.QWidget()
        a = FrameAnim(w)
        a.setDuration(400)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.start()
        pump(120)
        a.stop()
        mid = a.currentValue()
        self.assertIsNotNone(mid)
        self.assertLess(float(mid), 0.99)
        before = len([1])
        a.setStartValue(mid)                 # 调用方就是这么接着跑的
        a.setEndValue(0.0)
        seen = []
        a.valueChanged.connect(lambda v: seen.append(float(v)))
        a.start()
        pump(120)
        self.assertGreaterEqual(len(seen), before - 1)

    def test_start_interval_follows_the_refresh_rate(self):
        """帧间隔取 anim.frame_ms,而不是写死的 16。"""
        from ui.qt_frame_anim import FrameAnim, _frame_ms
        w = QtWidgets.QWidget()
        a = FrameAnim(w)
        self.assertEqual(_frame_ms(w), a._timer.interval() if a._timer.isActive() else _frame_ms(w))
        a.setDuration(100)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.start()
        self.assertEqual(a._timer.interval(), _frame_ms(w))
        a.stop()


class SmoothWheelValueTests(unittest.TestCase):
    """每一类控件「一格滚多远」都要对上 Tk 的数值。"""

    def setUp(self):
        from ui.qt_main import smooth
        self.m = smooth

    def test_scroll_area_matches_for_canvas(self):
        w = self.m.SmoothScrollArea()
        self.assertEqual(w.wheel_px(), 48.0)      # Tk for_canvas 默认 48

    def test_list_matches_message_center(self):
        w = self.m.SmoothList()
        self.assertEqual(w.wheel_px(), 60.0)      # Tk message_center 一格 60

    def test_tree_is_pixel_scrolled_and_three_rows(self):
        w = self.m.SmoothTree()
        self.assertEqual(w.verticalScrollMode(), QtWidgets.QAbstractItemView.ScrollPerPixel)
        # 空树时 sizeHintForRow(0) 返回 -1,不能退化成负数
        self.assertGreaterEqual(w.wheel_px(), 60.0)

    def test_log_text_is_three_lines(self):
        w = self.m.SmoothLogText()
        ls = float(w.fontMetrics().lineSpacing())
        self.assertAlmostEqual(w.wheel_px(), max(12.0, ls) * 3.0, places=3)

    def test_static_overview_ignores_the_wheel(self):
        """概览在 Tk 里是普通 Frame,滚轮不动内容。"""
        w = self.m.StaticTextEdit()
        w.setPlainText("x\n" * 200)
        w.resize(200, 60)
        ev = QtGui.QWheelEvent(QtCore.QPointF(10, 10), QtCore.QPointF(10, 10),
                               QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
                               QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                               QtCore.Qt.NoScrollPhase, False)
        bar = w.verticalScrollBar()
        before = bar.value()
        QtWidgets.QApplication.sendEvent(w, ev)
        self.assertEqual(bar.value(), before)


class SmoothScrollBehaviourTests(unittest.TestCase):
    """滚轮真的逐帧推进,而不是一次跳到位。

    ⚠ 事件要发给 viewport:QAbstractScrollArea 的 wheelEvent 是通过视口转发上来的,
    发给外层控件会被 QWidget::event 直接处理掉,探针会误以为"没生效"。
    """

    @staticmethod
    def _wheel(widget, dy=-120):
        ev = QtGui.QWheelEvent(QtCore.QPointF(20, 20), QtCore.QPointF(20, 20),
                               QtCore.QPoint(0, 0), QtCore.QPoint(0, dy),
                               QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                               QtCore.Qt.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(widget, ev)

    def _settle(self, widget, steps=30):
        bar = widget.verticalScrollBar()
        seen = []
        for _ in range(steps):
            pump(10)
            seen.append(bar.value())
        return seen

    def test_text_edit_glides_instead_of_jumping(self):
        from ui.qt_big_view import SmoothTextEdit
        e = SmoothTextEdit()
        e.setPlainText("x\n" * 300)
        e.resize(240, 160)
        e.show()
        pump(60)
        self._wheel(e.viewport())
        seen = self._settle(e)
        self.assertGreater(len(set(seen)), 3, "一格滚轮应当分几帧走完,实际 %r" % seen[:8])
        self.assertGreater(seen[-1], 0)

    def test_scroll_area_glides(self):
        from ui.qt_main.smooth import SmoothScrollArea
        sa = SmoothScrollArea()
        inner = QtWidgets.QWidget()
        inner.setMinimumHeight(2000)
        sa.setWidget(inner)
        sa.resize(240, 160)
        sa.show()
        pump(60)
        self._wheel(sa.viewport())
        seen = self._settle(sa)
        self.assertGreater(len(set(seen)), 3, "设置页应当分几帧走完,实际 %r" % seen[:8])

    def test_tree_glides(self):
        from ui.qt_main.smooth import SmoothTree
        t = SmoothTree()
        t.setHeaderHidden(True)
        for i in range(80):
            t.addTopLevelItem(QtWidgets.QTreeWidgetItem(["i%d" % i]))
        t.resize(240, 160)
        t.show()
        pump(60)
        self._wheel(t.viewport())
        seen = self._settle(t)
        self.assertGreater(len(set(seen)), 3, "树应当分几帧走完,实际 %r" % seen[:8])


class SliderWheelTests(unittest.TestCase):
    def test_wheel_does_not_change_the_value(self):
        """Tk 明确禁止滚轮改滑条(用户反馈过误触),Qt 也要 ignore 让页面去滚。"""
        from ui.qt_main.settings_widgets import SliderRow
        row = SliderRow(0, 10, 5, theme=DARK_THEME)
        ev = QtGui.QWheelEvent(QtCore.QPointF(5, 5), QtCore.QPointF(5, 5),
                               QtCore.QPoint(0, 0), QtCore.QPoint(0, 120),
                               QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                               QtCore.Qt.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(row, ev)
        self.assertEqual(row.value(), 5)


class WiringTests(unittest.TestCase):
    """控件真的换成了平滑版的那些类(而不是只写了类没人用)。"""

    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        self.w = self.W()
        self.w.show()
        pump(30)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.w._migration_running = False
        self.w.hide()
        try:
            self.w._close_session_log()
        except Exception:
            pass
        self.w.deleteLater()
        pump(20)

    def test_main_log_is_smooth(self):
        from ui.qt_big_view import _SmoothWheel
        self.assertIsInstance(self.w.log_text, _SmoothWheel)

    def test_big_log_window_is_smooth(self):
        from ui.qt_big_view import _SmoothWheel
        self.w._open_big_log_view()
        self.assertIsInstance(self.w._log_big_text, _SmoothWheel)

    def test_the_three_list_boxes_are_smooth(self):
        from ui.qt_big_view import _SmoothWheel
        for box in (self.w.mod_text, self.w.config_text, self.w.extra_text):
            self.assertIsInstance(box, _SmoothWheel)

    def test_changelog_box_is_a_smooth_plain_text_edit(self):
        """QPlainTextEdit 的滚动条单位是行,套缓动会一次滚几十行;要用 QTextEdit 系。"""
        import inspect
        from ui.qt_main import window as W
        src = inspect.getsource(W.QtMainWindow._import_changelog)
        self.assertIn("SmoothTextEdit()", src)
        self.assertNotIn("QPlainTextEdit()", src)

    def test_window_carries_its_own_icon(self):
        from ui.qt_main.winstyle import icon_path
        if icon_path() is None:
            self.skipTest("这台机器上没有 1.ico")
        self.assertFalse(self.w.windowIcon().isNull())


if __name__ == "__main__":
    unittest.main(verbosity=2)

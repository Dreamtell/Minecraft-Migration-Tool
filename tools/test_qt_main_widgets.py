"""Qt main window widgets (toast overlay, flip start button), rendered offscreen."""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ui.qt_main import flip_button as fb  # noqa: E402
from ui.qt_main import toast as qt_toast  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def grab(widget):
    img = widget.grab().toImage()
    return img.convertToFormat(QtGui.QImage.Format_ARGB32)


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


class FlipButtonTests(unittest.TestCase):
    def setUp(self):
        self.host = QtWidgets.QWidget()
        self.host.setStyleSheet("background: #1e1e1e;")
        self.btn = fb.FlipButton("🚀 开始迁移", self.host, dark=True)
        self.btn.resize(160, 38)
        self.host.resize(200, 60)
        self.host.show()
        pump(20)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()

    def test_front_face_matches_tk_colors(self):
        img = grab(self.btn)
        # 竖直渐变：上面比下面亮，取色接近 Tk 版 face_top / face_bot
        top = QtGui.QColor(img.pixel(80, 3))
        bot = QtGui.QColor(img.pixel(80, 34))
        self.assertGreater(top.green(), bot.green())
        self.assertLess(abs(top.green() - 0x2a), 12)
        # 左侧 accent 竖条
        acc = QtGui.QColor(img.pixel(5, 19))
        self.assertGreater(acc.green(), 150)

    def test_knife_frame_at_half(self):
        self.btn._anim_t0 = time.perf_counter()
        self.btn._f = 0.5
        img = grab(self.btn)
        mid = QtGui.QColor(img.pixel(80, 19))
        off = QtGui.QColor(img.pixel(80, 5))
        self.assertGreater(mid.green(), 150)          # 2px accent 亮线
        self.assertLess(off.green(), 60)               # 其余透出底色
        self.btn._anim_t0 = None
        self.btn._f = 0.0

    def test_flip_round_trip_runs_on_real_time_and_calls_done(self):
        self.btn.set_feel("crisp")
        self.btn.flip_to_back("⏳ 迁移中...")
        self.assertTrue(self.btn.is_back())
        self.assertEqual(self.btn.text(), "⏳ 迁移中...")
        pump(PRESETS_MS("crisp") + 120)
        self.assertEqual(self.btn._f, 1.0)
        self.assertIsNone(self.btn._anim_t0)
        back = QtGui.QColor(grab(self.btn).pixel(5, 19))
        self.assertGreater(back.red(), 180)            # 背面红色竖条
        done = []
        self.btn.flip_to_front("🚀 开始迁移", done=lambda: done.append(1))
        pump(PRESETS_MS("crisp") + 120)
        self.assertEqual(done, [1])
        self.assertEqual(self.btn._f, 0.0)
        self.assertFalse(self.btn._timer.isActive())   # 正面静止不空转

    def test_dry_run_turns_front_yellow(self):
        self.btn.set_dry_run(True)
        acc = QtGui.QColor(grab(self.btn).pixel(5, 19))
        self.assertGreater(acc.red(), 200)
        self.assertGreater(acc.green(), 160)
        self.assertLess(acc.blue(), 120)

    def test_disabled_front_is_grey_but_back_stays_red(self):
        self.btn.setEnabled(False)
        acc = QtGui.QColor(grab(self.btn).pixel(5, 19))
        self.assertLess(abs(acc.red() - acc.green()), 10)
        self.btn._target, self.btn._f = "back", 1.0
        back = QtGui.QColor(grab(self.btn).pixel(5, 19))
        self.assertGreater(back.red(), 180)

    def test_clicked_signal_still_works(self):
        hits = []
        self.btn.clicked.connect(lambda: hits.append(1))
        self.btn.click()
        self.assertEqual(hits, [1])


def PRESETS_MS(feel):
    return fb.PRESETS[feel]["ms"]


class ToastTests(unittest.TestCase):
    def setUp(self):
        self.win = QtWidgets.QMainWindow()
        self.win.setCentralWidget(QtWidgets.QWidget())
        self.win.resize(800, 600)
        self.win.show()
        self.host = qt_toast.ToastHost(self.win, {"bg": "#1e1e1e", "fg": "#e8e8e8"})
        pump(20)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()

    def test_stack_caps_at_four_and_sits_bottom_right(self):
        for i in range(6):
            self.host.show("消息 %d" % i, "info")
        self.assertEqual(self.host.count(), 4)
        pump(300)
        live = [c for c in self.host._items if not c.leaving]
        newest = live[-1]
        self.assertEqual(newest.text, "消息 5")
        self.assertLessEqual(newest.geometry().right(), self.win.width() - qt_toast._MARGIN)
        ys = [c.y() for c in live]
        self.assertEqual(ys, sorted(ys))               # 最新的在最下面

    def test_same_key_updates_in_place(self):
        a = self.host.show("模拟运行 开", "info", key="dry")
        b = self.host.show("模拟运行 关", "info", key="dry")
        self.assertIs(a, b)
        self.assertEqual(self.host.count(), 1)
        self.assertEqual(b.text, "模拟运行 关")

    def test_leading_level_symbol_is_stripped(self):
        c = self.host.show("⚠️ 磁盘快满了", "warn")
        self.assertEqual(c.text, "磁盘快满了")

    def test_error_is_sticky_and_info_expires(self):
        e = self.host.show("出错了", "error")
        i = self.host.show("好了", "ok", ms=60)
        pump(450)
        self.assertNotIn(i, self.host._items)
        self.assertIn(e, self.host._items)
        self.host.dismiss(e)
        pump(300)
        self.assertEqual(self.host.count(), 0)
        self.assertFalse(self.host._timer.isActive())

    def test_hover_pauses_countdown(self):
        c = self.host.show("悬停暂停", "info", ms=80)
        c.hover = True
        pump(250)
        self.assertIn(c, self.host._items)
        self.assertFalse(c.leaving)

    def test_duration_scales_with_length(self):
        self.assertEqual(qt_toast.停留时长("短"), 3000)
        self.assertEqual(qt_toast.停留时长("字" * 200), 8000)
        self.assertEqual(qt_toast.停留时长("x", 1200), 1200)


class NetFloatDockTests(unittest.TestCase):
    def setUp(self):
        from ui.qt_main import net_float as nf
        self.nf = nf
        self.win = QtWidgets.QMainWindow()
        self.win.setCentralWidget(QtWidgets.QWidget())
        self.win.resize(800, 600)
        self.win.move(100, 100)
        self.win.show()
        pump(30)
        self.inside = True
        self.f = nf.NetFloat(self.win, size=56, rel=(200, 200))
        self.f._cursor_inside = lambda: self.inside
        pump(20)

    def tearDown(self):
        self.f.close()
        self.win.close()

    def _drop_at_right_edge(self):
        g = self.win.geometry()
        F = self.f._full()
        self.f._rel = (g.width() - F - 3, 200)
        self.assertEqual(self.f._detect_edge(), "right")
        self.f._hint = "right"
        self.f._dragging = True
        self.f._press = (QtCore.QPoint(0, 0), QtCore.QPoint(5, 5))
        ev = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(5, 5),
                               QtCore.QPointF(5, 5), QtCore.Qt.LeftButton,
                               QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
        self.f.mouseReleaseEvent(ev)

    def test_edge_detection_matches_tk(self):
        self.assertEqual(self.f._detect_edge((0, 0)), "left")      # Tk: 左右优先
        self.assertEqual(self.f._detect_edge((0, 200)), "left")
        self.assertEqual(self.f._detect_edge((200, 0)), "top")
        self.assertIsNone(self.f._detect_edge((200, 200)))

    def test_drop_near_edge_docks_then_collapses_to_tab(self):
        self._drop_at_right_edge()
        self.assertEqual(self.f._docked, "right")
        self.assertTrue(self.f._peek)               # 光标还在上面:先停在展开态
        self.inside = False
        self.f._poll_hover()
        self.assertTrue(self.f._peek)               # 一次离开不收(防擦边)
        self.f._poll_hover()
        self.assertFalse(self.f._peek)
        pump(self.nf._MORPH_MS + 80)
        self.assertEqual(self.f.width(), self.nf._TAB_W)
        g = self.win.geometry()
        self.assertEqual(self.f.geometry().right(), g.right())   # 小标签紧贴右边

    def test_hover_expands_and_drag_away_undocks(self):
        self._drop_at_right_edge()
        self.inside = False
        self.f._poll_hover()
        self.f._poll_hover()
        pump(self.nf._MORPH_MS + 80)
        self.inside = True
        self.f._poll_hover()
        self.assertTrue(self.f._peek)
        pump(self.nf._MORPH_MS + 80)
        self.assertEqual(self.f.width(), self.f._full())
        self.f._undock()
        self.f._place()
        self.assertIsNone(self.f._docked)
        self.assertFalse(self.f._poll.isActive())

    def test_saved_edge_position_restores_docked(self):
        g = self.win.geometry()
        F = self.f._full()
        g2 = self.nf.NetFloat(self.win, size=56, rel=(g.width() - F, 300))
        try:
            pump(20)
            self.assertEqual(g2._docked, "right")
            self.assertEqual(g2.width(), self.nf._TAB_W)
            off = self.nf.NetFloat(self.win, size=56, rel=(g.width() - F, 300), dock=False)
            pump(20)
            self.assertIsNone(off._docked)
            off.close()
        finally:
            g2.close()


class ListUndoTests(unittest.TestCase):
    def setUp(self):
        from ui.qt_main.window import ListEdit
        self.box = ListEdit()
        self.box.setPlainText("a.jar\nb.jar\nc.jar")
        self.box.reset_undo()

    def _delete_last_line(self):
        cur = self.box.textCursor()
        cur.movePosition(QtGui.QTextCursor.End)
        cur.select(QtGui.QTextCursor.BlockUnderCursor)
        cur.removeSelectedText()

    def test_each_delete_is_its_own_undo_step(self):
        self._delete_last_line()
        self._delete_last_line()
        self.assertEqual(self.box.toPlainText(), "a.jar")
        self.box.undo()
        self.assertEqual(self.box.toPlainText(), "a.jar\nb.jar")      # 一次只还原一步
        self.box.undo()
        self.assertEqual(self.box.toPlainText(), "a.jar\nb.jar\nc.jar")
        self.box.undo()                                               # 载入的内容是起点
        self.assertEqual(self.box.toPlainText(), "a.jar\nb.jar\nc.jar")
        self.box.redo()
        self.assertEqual(self.box.toPlainText(), "a.jar\nb.jar")

    def test_ctrl_z_and_ctrl_y_keys(self):
        self._delete_last_line()
        z = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
        y = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Y, QtCore.Qt.ControlModifier)
        self.box.keyPressEvent(z)
        self.assertEqual(self.box.toPlainText(), "a.jar\nb.jar\nc.jar")
        self.box.keyPressEvent(y)
        self.assertEqual(self.box.toPlainText(), "a.jar\nb.jar")

    def test_program_replace_is_one_undoable_step(self):
        cur = QtGui.QTextCursor(self.box.document())
        cur.beginEditBlock()
        cur.select(QtGui.QTextCursor.Document)
        cur.insertText("")
        cur.endEditBlock()
        self.box.undo()
        self.assertEqual(self.box.toPlainText(), "a.jar\nb.jar\nc.jar")


class MainWindowSmokeTests(unittest.TestCase):
    """整窗起一次:翻转按钮/吐司接进去之后,原来的主流程入口还对得上。"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory(prefix="mctool-qtmain-")
        cfg = Path(cls._tmp.name) / "config.json"
        cfg.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")
        os.environ["MCTOOL_CONFIG"] = str(cfg)
        from utils import config as _c
        _c.CONFIG_FILE = cfg
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_start_button_lifecycle_and_toast(self):
        w = self.W()
        try:
            w.show()
            pump(30)
            self.assertIsInstance(w.start_btn, fb.FlipButton)
            w.dry_run_check.setChecked(not w.dry_run_check.isChecked())
            self.assertEqual(w.start_btn._dry, w.dry_run_check.isChecked())
            self.assertEqual(w._toast_host.count(), 1)          # 开关提示走浮层卡片
            w.dry_run_check.setChecked(not w.dry_run_check.isChecked())
            self.assertEqual(w._toast_host.count(), 1)          # 同 key 原地更新
            w.start_btn.set_feel("crisp")
            w._migration_running = True
            w.start_btn.setEnabled(False)
            w.start_btn.flip_to_back("⏳ 准备中...")
            pump(fb.PRESETS["crisp"]["ms"] + 100)
            self.assertTrue(w.start_btn.is_back())
            w._unlock_after_prepare()
            pump(fb.PRESETS["crisp"]["ms"] + 100)
            self.assertFalse(w.start_btn.is_back())
            self.assertEqual(w.start_btn.text(), "🚀 开始迁移")
            self.assertTrue(w.start_btn.isEnabled())
            w.config["toast_on"] = False
            w.toast("关掉之后不弹", "info")
            self.assertEqual(w._toast_host.count(), 1)
        finally:
            w._migration_running = False
            w.hide()
            w.deleteLater()
            pump(30)


if __name__ == "__main__":
    unittest.main()

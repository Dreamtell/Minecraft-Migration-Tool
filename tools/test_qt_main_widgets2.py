"""Qt 主界面自绘控件的回归测试:SwitchRow / MainTabs / BottomBar,以及和 Tk 对齐的文案与顺序。

数值来自 Tk 的 utils/helpers.SwitchRow、ui/rounded_tabs.RoundedTabs、ui/mw_pages 底栏(逐项对过源码)。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-widgets2-")
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
from utils.theme import DARK_THEME, LIGHT_THEME  # noqa: E402

_cfg.CONFIG_FILE = CFG
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


def grab(widget, dpr=1.0):
    img = QtGui.QImage(int(widget.width() * dpr), int(widget.height() * dpr),
                       QtGui.QImage.Format_RGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(QtGui.QColor("#000000"))
    widget.render(img)
    return img


def px(img, x, y):
    return "#%06x" % (img.pixel(x, y) & 0xffffff)


# ====================================================================
# SwitchRow
# ====================================================================
class SwitchRowTests(unittest.TestCase):
    def make(self, **kw):
        from ui.qt_main.switch_row import SwitchRow
        w = SwitchRow(DARK_THEME, kw.pop("title", "标题"), kw.pop("desc", ""), **kw)
        w.resize(w.sizeHint().width() if kw.get("compact") else 400, w._h)
        w.show()
        pump(20)
        self.addCleanup(w.deleteLater)
        return w

    def test_sizes_follow_tk(self):
        card = self.make(desc="说明")
        self.assertEqual((card._sw, card._sh, card._knob, card._h), (44, 22, 16, 46))
        self.assertEqual((card._pad_l, card._gap), (12, 12))
        small = self.make(compact=True)
        self.assertEqual((small._sw, small._sh, small._knob, small._h), (36, 18, 14, 26))
        self.assertEqual((small._pad_l, small._gap), (0, 8))

    def test_compact_width_fits_text(self):
        a = self.make(title="短", compact=True)
        b = self.make(title="一个比较长的开关标题", compact=True)
        self.assertGreater(b.width(), a.width())

    def test_toggle_emits_and_moves_knob(self):
        w = self.make(compact=True)
        seen = []
        w.toggled.connect(seen.append)
        self.assertFalse(w.isChecked())
        w.toggle()
        pump(250)
        self.assertEqual(seen, [True])
        self.assertTrue(w.isChecked())
        self.assertAlmostEqual(w._t, 1.0, places=2)
        w.toggle()
        pump(250)
        self.assertEqual(seen, [True, False])
        self.assertAlmostEqual(w._t, 0.0, places=2)

    def test_set_checked_emits_like_qcheckbox_and_jumps(self):
        """主窗口里很多路径(载入配置、锁定/解锁)靠这个信号同步状态,所以保持 QCheckBox 语义。"""
        w = self.make(compact=True)
        seen = []
        w.toggled.connect(seen.append)
        w.setChecked(True)
        self.assertEqual(seen, [True])
        self.assertEqual(w._t, 1.0)                       # 程序设值不带动画
        w.setChecked(True)
        self.assertEqual(seen, [True])                    # 值没变不重复发
        w.blockSignals(True)
        w.setChecked(False)
        w.blockSignals(False)
        self.assertEqual(seen, [True])

    def test_disabled_ignores_clicks(self):
        w = self.make(compact=True)
        w.setEnabled(False)
        seen = []
        w.toggled.connect(seen.append)
        w.toggle()
        QtCore.QMetaObject.invokeMethod(w, "update")
        self.assertEqual(seen, [])
        self.assertFalse(w.isChecked())
        self.assertEqual(w.cursor().shape(), QtCore.Qt.ArrowCursor)

    def test_click_inside_toggles_click_outside_does_not(self):
        w = self.make(compact=True)
        seen = []
        w.toggled.connect(seen.append)
        QtTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
        # 控件外按下:不切换
        QtTest.mouseClick(w, QtCore.Qt.LeftButton, pos=QtCore.QPoint(w.width() + 8, 5))
        self.assertEqual(seen, [])
        # 里面按下、拖到外面松手:不切换(标准按钮语义,靠 rect().contains 守卫)
        QtTest.mousePress(w, QtCore.Qt.LeftButton, pos=QtCore.QPoint(10, 10))
        QtTest.mouseRelease(w, QtCore.Qt.LeftButton, pos=QtCore.QPoint(w.width() + 8, 5))
        self.assertEqual(seen, [])
        # 按下和松手都在里面:切换
        QtTest.mouseClick(w, QtCore.Qt.LeftButton, pos=QtCore.QPoint(10, 10))
        self.assertEqual(seen, [True])

    def test_pixels_track_and_knob_match_tk(self):
        """开着:轨道是强调色 switch_on #4da3f0,圆钮白色在右;关着:轨道 muted_fg,圆钮在左。"""
        w = self.make(compact=True, accent="switch_on")
        off = grab(w)
        self.assertEqual(px(off, 18, 13), DARK_THEME["muted_fg"])            # 轨道中央(圆钮在左,避开)
        w.setChecked(True)
        on = grab(w)
        self.assertEqual(px(on, 8, 13), DARK_THEME["switch_on"])             # 轨道左半(圆钮已到右)
        self.assertEqual(px(on, 36 - 3 - 7, 13), "#ffffff")                  # 圆钮中心

    def test_keep_state_when_disabled(self):
        a = self.make(compact=True, accent="switch_on", keep_state_when_disabled=True)
        a.setChecked(True)
        a.setEnabled(False)
        self.assertEqual(px(grab(a), 8, 13), DARK_THEME["switch_on"])        # 禁用后颜色不变
        b = self.make(compact=True, accent="switch_on")
        b.setChecked(True)
        b.setEnabled(False)
        self.assertNotEqual(px(grab(b), 8, 13), DARK_THEME["switch_on"])     # 普通开关灰掉

    def test_card_warn_text_when_on(self):
        w = self.make(title="主界面编辑", desc="直接改动清单文字 · 谨慎使用", warn_desc="⚠ 已开启")
        self.assertEqual(w.description(), "直接改动清单文字 · 谨慎使用")
        # 关着是说明色,开着(t>0.5)换成警示红:取说明那块的像素颜色比
        def desc_colors(sw):
            img = grab(sw)
            seen = set()
            for x in range(190, 330):
                for y in range(14, 32):
                    seen.add(px(img, x, y))
            return seen
        self.assertNotIn("#ff6b6b", desc_colors(w))
        w.setChecked(True)
        self.assertIn("#ff6b6b", desc_colors(w))                              # 深色主题的警示色

    def test_anim_scale_zero_jumps_without_animation(self):
        w = self.make(compact=True)
        with mock.patch("ui.qt_main.switch_row._anim_scale", return_value=0.0):
            w.toggle()
        self.assertEqual(w._t, 1.0)                       # 「特效强度」关闭:一步到位
        self.assertFalse(w._anim.state() == QtCore.QAbstractAnimation.Running)

    def test_theme_switch_repaints(self):
        w = self.make(compact=True, accent="switch_on")
        w.setChecked(True)
        before = px(grab(w), 8, 13)
        w.set_theme(dict(DARK_THEME, switch_on="#ff0000"))
        self.assertNotEqual(px(grab(w), 8, 13), before)


# ====================================================================
# MainTabs
# ====================================================================
class MainTabsTests(unittest.TestCase):
    def make(self, labels=("🧩 模组清单 3", "⚙️ config 清单", "📦 其它文件 12")):
        from ui.qt_main.main_tabs import MainTabs
        t = MainTabs(DARK_THEME)
        for i, lb in enumerate(labels):
            page = QtWidgets.QLabel("page%d" % i)
            t.addTab(page, lb)
        t.resize(900, 300)
        t.show()
        pump(30)
        self.addCleanup(t.deleteLater)
        return t

    def test_geometry_constants_match_tk(self):
        from ui.qt_main import main_tabs as m
        self.assertEqual((m.BAR_H, m.PILL_H, m.PILL_Y, m.PAD_X, m.GAP, m.RADIUS),
                         (44, 34, 5, 22, 4, 12))
        self.assertEqual((m.SLIDE_MS, m.PAGE_MS, m.LEFT, m.LONG_PRESS_S), (170, 150, 6, 1.0))
        # 同一份数在 Tk 源码里
        tk = (Path(__file__).resolve().parents[1] / "Minecraft迁移工具" / "ui" / "rounded_tabs.py"
              ).read_text(encoding="utf-8")
        for name, val in (("PAD_X", 22), ("GAP", 4), ("BAR_H", 44), ("PILL_H", 34),
                          ("PILL_Y", 5), ("RADIUS", 12), ("SLIDE_MS", 170), ("PAGE_MS", 150)):
            self.assertRegex(tk, r"(?m)^%s\s*=\s*%d\b" % (name, val), name)

    def test_qtabwidget_like_interface(self):
        t = self.make()
        self.assertEqual(t.count(), 3)
        self.assertEqual(t.currentIndex(), 0)
        self.assertEqual(t.tabText(2), "📦 其它文件 12")
        self.assertIsNotNone(t.widget(1))
        self.assertIs(t.tabBar(), t.bar)
        seen = []
        t.currentChanged.connect(seen.append)
        t.setCurrentIndex(2)
        self.assertEqual(seen, [2])
        t.setCurrentIndex(2)
        self.assertEqual(seen, [2])                       # 同一页不重复发
        self.assertTrue(t.widget(2).isVisible())
        self.assertFalse(t.widget(0).isVisible())

    def test_selected_slides_and_snaps_when_text_changes(self):
        t = self.make()
        t.setCurrentIndex(2, animate=False)
        tabs, _ = t.bar.layout_items()
        self.assertAlmostEqual(t.bar._sel_x, tabs[2][0], places=1)
        t.setTabText(2, "📦 其它文件 1234567")
        pump(20)
        tabs, _ = t.bar.layout_items()
        self.assertAlmostEqual(t.bar._sel_x, tabs[2][0], places=1)    # 改文字后选中块立刻贴回,不做动画
        self.assertAlmostEqual(t.bar._sel_w, tabs[2][1], places=1)

    def test_slide_goes_through_intermediate_positions(self):
        t = self.make()
        xs = []
        t.bar._anim.valueChanged.connect(lambda v: xs.append(v.x()))
        t.setCurrentIndex(2)
        pump(250)
        self.assertGreater(len(xs), 2)                    # 真的在动,不是瞬移
        tabs, _ = t.bar.layout_items()
        self.assertAlmostEqual(t.bar._sel_x, tabs[2][0], places=1)

    def test_anim_scale_zero_snaps(self):
        t = self.make()
        with mock.patch("ui.qt_main.main_tabs._anim_scale", return_value=0.0):
            t.setCurrentIndex(1)
        tabs, _ = t.bar.layout_items()
        self.assertAlmostEqual(t.bar._sel_x, tabs[1][0], places=1)
        self.assertEqual(t.pages._dy, 0)

    def test_page_slides_in_from_below(self):
        t = self.make()
        t.setCurrentIndex(1)
        self.assertEqual(t.pages._dy, 10)
        self.assertEqual(t.widget(1).y(), 10)                  # 一开始比终点低 10px
        pump(60)
        self.assertTrue(0 < t.widget(1).y() < 10, t.widget(1).y())   # 真的在往上滑,不是瞬移
        pump(220)
        self.assertEqual(t.pages._dy, 0)
        self.assertEqual(t.widget(1).y(), 0)

    def test_locked_ignores_clicks(self):
        t = self.make()
        t.set_locked(True)
        t.bar.clicked.emit(2)
        self.assertEqual(t.currentIndex(), 0)
        QtTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
        tabs, _ = t.bar.layout_items()
        QtTest.mouseClick(t.bar, QtCore.Qt.LeftButton, pos=QtCore.QPoint(int(tabs[1][0] + 20), 20))
        self.assertEqual(t.currentIndex(), 0)
        t.set_locked(False)
        QtTest.mouseClick(t.bar, QtCore.Qt.LeftButton, pos=QtCore.QPoint(int(tabs[1][0] + 20), 20))
        self.assertEqual(t.currentIndex(), 1)

    def test_tail_buttons_sit_at_right_end(self):
        t = self.make()
        h1 = t.add_tail("a", "warn", "⚠ 更新提醒", lambda: None)
        h2 = t.add_tail("b", "apply", "🔁 应用上次记录", lambda: None)
        _, tails = t.bar.layout_items()
        self.assertEqual(len(tails), 2)
        self.assertLess(tails[0][0], tails[1][0])
        self.assertEqual(round(tails[1][0] + tails[1][1]), t.bar.width() - 6)   # 贴右端留 6
        h1.setVisible(False)
        _, tails = t.bar.layout_items()
        self.assertEqual(len(tails), 1)
        self.assertEqual(h2.text(), "🔁 应用上次记录")

    def test_tail_needs_a_full_second_of_holding(self):
        """防误点:短按不触发,按满 1 秒才触发(同 Tk)。"""
        t = self.make()
        fired = []
        t.add_tail("b", "apply", "🔁 应用", lambda: fired.append(1))
        QtTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
        _, tails = t.bar.layout_items()
        pos = QtCore.QPoint(int(tails[0][0] + 10), 20)
        QtTest.mouseClick(t.bar, QtCore.Qt.LeftButton, pos=pos)       # 立刻松手
        pump(60)
        self.assertEqual(fired, [])
        QtTest.mousePress(t.bar, QtCore.Qt.LeftButton, pos=pos)
        pump(300)
        self.assertEqual(fired, [])
        self.assertGreater(t.bar.press_progress(), 0.1)
        pump(900)                                                      # 不松手,过 1 秒
        self.assertEqual(fired, [1])
        QtTest.mouseRelease(t.bar, QtCore.Qt.LeftButton, pos=pos)
        pump(40)
        self.assertEqual(fired, [1])                                   # 松手不会再触发一次

    def test_leaving_the_bar_cancels_long_press(self):
        t = self.make()
        fired = []
        t.add_tail("b", "apply", "🔁 应用", lambda: fired.append(1))
        QtTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
        _, tails = t.bar.layout_items()
        pos = QtCore.QPoint(int(tails[0][0] + 10), 20)
        QtTest.mousePress(t.bar, QtCore.Qt.LeftButton, pos=pos)
        pump(200)
        QtCore.QCoreApplication.sendEvent(t.bar, QtCore.QEvent(QtCore.QEvent.Leave))
        pump(1000)
        self.assertEqual(fired, [])

    def test_releasing_halfway_cancels_long_press(self):
        """按到一半松手:进度清零、之后不会自己数到 1 秒触发(上一条是移出,这一条是松手)。"""
        t = self.make()
        fired = []
        t.add_tail("b", "apply", "🔁 应用", lambda: fired.append(1))
        QtTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
        _, tails = t.bar.layout_items()
        pos = QtCore.QPoint(int(tails[0][0] + 10), 20)
        QtTest.mousePress(t.bar, QtCore.Qt.LeftButton, pos=pos)
        pump(400)
        self.assertGreater(t.bar.press_progress(), 0.2)
        QtTest.mouseRelease(t.bar, QtCore.Qt.LeftButton, pos=pos)
        self.assertEqual(t.bar.press_progress(), 0.0)
        self.assertFalse(t.bar._press_timer.isActive())
        pump(1200)
        self.assertEqual(fired, [])

    def test_disabled_tail_does_not_fire(self):
        t = self.make()
        fired = []
        h = t.add_tail("b", "apply", "🔁 应用", lambda: fired.append(1))
        h.setEnabled(False)
        QtTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
        _, tails = t.bar.layout_items()
        pos = QtCore.QPoint(int(tails[0][0] + 10), 20)
        # 第二道保险:就算按下被放过(状态在按住期间才变成禁用),走满 1 秒也不能触发
        h.setEnabled(True)
        QtTest.mousePress(t.bar, QtCore.Qt.LeftButton, pos=pos)
        pump(300)
        h.setEnabled(False)
        pump(1200)
        QtTest.mouseRelease(t.bar, QtCore.Qt.LeftButton, pos=pos)
        self.assertEqual(fired, [])
        QtTest.mousePress(t.bar, QtCore.Qt.LeftButton, pos=pos)
        pump(1200)
        QtTest.mouseRelease(t.bar, QtCore.Qt.LeftButton, pos=pos)
        self.assertEqual(fired, [])
        h.click()
        self.assertEqual(fired, [])                                    # 句柄的 click 也尊重禁用
        h.setEnabled(True)
        h.click()
        self.assertEqual(fired, [1])

    def test_pills_colors_match_tk(self):
        t = self.make()
        t.add_tail("a", "warn", "x", None)
        t.add_tail("b", "apply", "y", None)
        bar = t.bar
        self.assertEqual(bar._tail_color("normal", "warn"), "#4d1517")
        self.assertEqual(bar._tail_color("hover", "warn"), "#5f1b1e")
        self.assertEqual(bar._tail_color("normal", "apply"), "#12496e")
        self.assertEqual(bar._tail_color("hover", "apply"), "#155a86")
        self.assertEqual(bar._tail_fg("warn"), "#ff6b70")
        self.assertEqual(bar._tail_fg("apply"), "#66c6ff")
        t.set_theme(LIGHT_THEME)
        self.assertEqual(bar._tail_color("normal", "warn"), "#fde5e1")
        self.assertEqual(bar._tail_color("normal", "apply"), "#d6ecfb")
        self.assertEqual(bar._tail_fg("apply"), "#1565a8")

    def test_selected_pill_and_number_colors(self):
        t = self.make()
        img = grab(t.bar)
        tabs, _ = t.bar.layout_items()
        sel = QtGui.QColor(DARK_THEME["card_sel_bar"]).name()
        x, w = tabs[0]
        self.assertEqual(px(img, int(x + 6), 22), sel)                # 选中药丸的底
        x2, w2 = tabs[1]
        self.assertEqual(px(img, int(x2 + 6), 22), DARK_THEME["button_bg"])   # 未选中的底

    def test_number_gets_data_color(self):
        """条数那段字用 tab_num_fg，**名字那段不能**也用它（否则整块同色，看不出哪个是数字）。"""
        t = self.make(labels=("名字 7", "名字 8"))
        img = grab(t.bar)
        tabs, _ = t.bar.layout_items()
        want_num = QtGui.QColor(DARK_THEME["tab_num_fg"]).name()
        want_name = QtGui.QColor(DARK_THEME["fg"]).name()
        fm = QtGui.QFontMetrics(t.bar.font_())
        x, w = tabs[1]
        main, num = "名字", "8"
        total = max(fm.horizontalAdvance(main) + fm.horizontalAdvance(num),
                    fm.horizontalAdvance("名字 8"))
        x0 = int(x + max(0, (w - total) // 2))
        name_end = x0 + fm.horizontalAdvance(main)
        num_start = name_end + 2
        name_zone = {px(img, xx, yy) for xx in range(x0, name_end) for yy in range(18, 30)}
        num_zone = {px(img, xx, yy)
                    for xx in range(num_start, num_start + fm.horizontalAdvance(num) + 2)
                    for yy in range(18, 30)}
        self.assertIn(want_num, num_zone)                  # 数字是数据色
        self.assertNotIn(want_num, name_zone)              # 名字不是数据色
        self.assertIn(want_name, name_zone)                # 名字用的是正文色


# ====================================================================
# BottomBar
# ====================================================================
class BottomBarTests(unittest.TestCase):
    def make(self, theme=DARK_THEME):
        from ui.qt_main.bottom_bar import BottomBar
        b = BottomBar(theme, "本工具完全免费，仅供个人学习交流使用。严禁倒卖或用于商业目的。")
        b.resize(1000, b.height())
        b.show()
        pump(30)
        self.addCleanup(b.deleteLater)
        return b

    def test_height_and_background_follow_tk(self):
        b = self.make()
        self.assertEqual(b.height(), 22)
        self.assertEqual(px(grab(b), 500, 1), DARK_THEME["bottom_bg"])
        b.set_theme(LIGHT_THEME)
        self.assertEqual(px(grab(b), 500, 1), LIGHT_THEME["bottom_bg"])

    def test_declaration_is_centered_on_the_whole_bar(self):
        """声明要在整条底栏居中,不是「左边控件之后的剩余空间」里居中(那样会偏右)。"""
        b = self.make()
        c = b.decl.geometry().center().x()
        self.assertLessEqual(abs(c - b.width() / 2), 2)
        b.resize(1400, b.height())
        pump(20)
        self.assertLessEqual(abs(b.decl.geometry().center().x() - b.width() / 2), 2)

    def test_spark_size_and_placeholder(self):
        b = self.make()
        self.assertEqual((b.spark.width(), b.spark.height()), (84, 16))
        with mock.patch.object(b.spark, "_history", return_value=[]):
            img = grab(b.spark)
        self.assertEqual(px(img, 1, 1), DARK_THEME["bottom_bg"])

    def test_spark_draws_green_when_up_red_when_down(self):
        b = self.make()
        now = time.time()
        up = [(now - 20 + i * 2, 40 + i, 2, 2) for i in range(10)]
        down = [(now - 20 + i * 2, 0, 0, 2) for i in range(10)]
        def colors(hist):
            with mock.patch.object(b.spark, "_history", return_value=hist):
                img = grab(b.spark)
            return {px(img, x, y) for x in range(84) for y in range(16)}
        self.assertTrue(any(c.startswith("#81") or c == "#81c784" for c in colors(up)))
        self.assertTrue(any(c in ("#c62828",) or c.startswith("#c6") for c in colors(down)))

    def test_spark_y_max_only_rises_quickly(self):
        """上限只升不骤降:新目标比旧上限小时只往下走 10%,曲线不会忽大忽小。"""
        b = self.make()
        now = time.time()
        big = [(now - 20 + i * 2, 400, 2, 2) for i in range(10)]
        small = [(now - 20 + i * 2, 20, 2, 2) for i in range(10)]
        with mock.patch.object(b.spark, "_history", return_value=big):
            grab(b.spark)
        hi = b.spark._y_max
        with mock.patch.object(b.spark, "_history", return_value=small):
            grab(b.spark)
        self.assertLess(b.spark._y_max, hi)
        self.assertGreater(b.spark._y_max, hi * 0.85)

    def test_double_click_on_spark_emits(self):
        b = self.make()
        seen = []
        b.spark.doubleClicked.connect(lambda: seen.append(1))
        QtTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
        QtTest.mouseDClick(b.spark, QtCore.Qt.LeftButton, pos=QtCore.QPoint(10, 8))
        self.assertEqual(seen, [1])


# ====================================================================
# 主窗口接线:文案 / 顺序 / 底栏 / 开关 / 页签
# ====================================================================
class WindowWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def make(self, **cfg):
        base = {"language": "zh", "theme": "dark", "net_float_on": False}
        base.update(cfg)
        _cfg.save_raw_config(base)
        w = self.W()
        w.resize(1180, 900)
        w.show()
        pump(80)
        self.addCleanup(self._close, w)
        return w

    @staticmethod
    def _close(w):
        w.hide()
        w._migration_running = False
        w._close_session_log()
        w.deleteLater()
        pump(30)

    def test_button_order_and_labels_follow_tk(self):
        """默认顺序 = Tk 的 _DEFAULT_BUTTON_ORDER;标签 = Tk 的 _BUTTON_LABELS。"""
        from ui.qt_main.buttons import DEFAULT_ORDER, LABELS
        src = (Path(__file__).resolve().parents[1] / "Minecraft迁移工具" / "ui" / "mw_common.py"
               ).read_text(encoding="utf-8")
        ns = {}
        a = src.index("_DEFAULT_BUTTON_ORDER = {")
        b = src.index("# 窗口工具栏按钮")
        exec(src[a:b].replace("_DEFAULT_BUTTON_ORDER = {", "TK_ORDER = {", 1), ns)
        a = src.index("_BUTTON_LABELS = {")
        b = src.index("_BUTTON_LABELS.update")
        exec(src[a:b].replace("_BUTTON_LABELS = {", "TK_LABELS = {", 1), ns)
        for g in ("path", "path_target", "mods", "config", "extra", "action"):
            qt = [k for k in DEFAULT_ORDER[g] if k in ns["TK_ORDER"][g]]       # 去掉 Qt 独有的按钮
            tk = [k for k in ns["TK_ORDER"][g] if k in DEFAULT_ORDER[g]]       # 去掉 Tk 独有的(回滚)
            self.assertEqual(qt, tk, g)
        for key, text in ns["TK_LABELS"].items():
            if key in LABELS and key not in ("history", "scan_diff"):
                self.assertEqual(LABELS[key], text, key)
        # 这两个 Tk 本来就有两种叫法(设置窗列表里一个名字、界面上另一个):以设置窗为准
        self.assertEqual(LABELS["history"], "📦 迁移历史")
        self.assertEqual(LABELS["scan_diff"], "🔍 扫描模组差异")

    def test_qt_only_buttons_are_last(self):
        from ui.qt_main.buttons import DEFAULT_ORDER
        self.assertEqual(DEFAULT_ORDER["mods"][-1], "changelog")

    def test_page_button_texts_match_tk(self):
        w = self.make()
        want = {"mod_magnify": "📂 放大查看", "add_mods": "➕ 添加模组", "scan_diff": "🔍 扫描差异",
                "clear_mods": "🗑️ 清空清单", "check_mods": "🔎 检查存在",
                "cfg_magnify": "📂 放大查看", "add_cfg_dir": "📁 添加文件夹",
                "add_cfg_file": "📄 添加文件", "clear_cfg": "🗑️ 清空 config 清单",
                "check_cfg": "🔎 检查存在", "add_extra_dir": "📁 添加文件夹",
                "add_extra_file": "📄 添加文件", "add_extra_options": "＋ options.txt",
                "add_extra_preset": "＋ 常用目录 ▾", "clear_extra": "🗑️ 清空清单",
                "check_extra": "🔎 检查存在", "log_big": "📂 放大查看",
                "log_open": "📂 打开日志文件夹", "fail_locate": "📍 定位错误",
                "log_clear": "🗑️ 清空日志", "history": "📋 查看历史"}
        for key, text in want.items():
            self.assertEqual(w._btn_widgets[key].text(), text, key)

    def test_tab_labels_and_badges_follow_tk(self):
        w = self.make(mod_list="a.jar\nb.jar", config_list="# 注释\nx", extra_list="")
        self.assertEqual(w.tabs.tabText(w._mod_tab_index), "🧩 模组清单 2")
        self.assertEqual(w.tabs.tabText(w._config_tab_index), "⚙️ config 清单 1")     # 注释行不计
        self.assertEqual(w.tabs.tabText(w._extra_tab_index), "📦 其它文件")           # 0 条不带数字
        w2 = self.make(mod_list="", config_list="", extra_list="")                     # 全空:三页都是初始文案
        self.assertEqual([w2.tabs.tabText(i) for i in range(3)],
                         ["🧩 模组清单", "⚙️ config 清单", "📦 其它文件"])

    def test_bottom_bar_is_wired(self):
        w = self.make()
        self.assertIs(w.net_label, w.bottom_bar.net_label)
        self.assertIs(w.msg_bell, w.bottom_bar.bell)
        self.assertEqual(w.bottom_bar.decl.text(), "本工具完全免费，仅供个人学习交流使用。严禁倒卖或用于商业目的。")
        self.assertEqual(w.bottom_bar.height(), 22)
        self.assertFalse(w.statusBar().isVisible())               # 不再另造一条状态栏

    def test_pin_toggles_the_float_and_persists(self):
        w = self.make()
        with mock.patch.object(w, "toast") as toast:
            w.bottom_bar.pin_btn.click()
        self.assertTrue(w.config["net_float_on"])
        self.assertTrue(_cfg.load_raw_config()["net_float_on"])
        self.assertIsNotNone(w._net_float)
        self.assertIn("桌面浮标已显示", toast.call_args.args[0])
        with mock.patch.object(w, "toast") as toast:
            w.bottom_bar.pin_btn.click()
        self.assertFalse(w.config["net_float_on"])
        self.assertIsNone(w._net_float)
        self.assertIn("桌面浮标已关闭", toast.call_args.args[0])

    def test_switches_are_switchrows_and_keep_state_when_locked(self):
        from ui.qt_main.switch_row import SwitchRow
        w = self.make(dry_run=True, overwrite=True)
        for name in ("edit_switch", "dry_run_check", "overwrite_check"):
            self.assertIsInstance(getattr(w, name), SwitchRow, name)
        self.assertFalse(w.edit_switch._compact)
        self.assertTrue(w.dry_run_check._compact and w.overwrite_check._compact)
        self.assertEqual(w.dry_run_check.text(), "模拟运行（仅显示操作）")
        self.assertEqual(w.overwrite_check.text(), "覆盖已存在的模组")
        # 真正要守的是「禁用后还看得清」：_keep_state 决定禁用时要不要灰掉。
        # 断言它驱动的那条行为，而不是断言构造期就定死的值。
        sw = w.dry_run_check
        self.assertTrue(sw.isChecked())
        sw.setEnabled(False)
        self.assertFalse(sw._gray_on_disabled())          # 锁住 ≠ 灰掉
        self.assertTrue(sw.isChecked())                   # 状态仍读得出来
        sw._keep_state = False
        self.assertTrue(sw._gray_on_disabled())           # 关掉这个开关才会灰
        sw._keep_state = True
        sw.setEnabled(True)
        self.assertTrue(sw.isChecked())

    def test_tabs_locked_while_busy(self):
        w = self.make()
        w._scanning = True
        w._refresh_busy_state()
        self.assertTrue(w.tabs.locked)
        w._scanning = False
        w._refresh_busy_state()
        self.assertFalse(w.tabs.locked)

    def test_apply_pill_is_a_tail_handle(self):
        from ui.qt_main.main_tabs import TailHandle
        w = self.make()
        self.assertIsInstance(w.apply_pill, TailHandle)
        self.assertIsInstance(w.pack_pill, TailHandle)
        self.assertEqual(w.apply_pill.kind, "apply")
        self.assertEqual(w.pack_pill.kind, "warn")

    def test_theme_toggle_reaches_new_widgets(self):
        w = self.make()
        w._toggle_theme()
        pump(40)
        self.assertEqual(w.tabs.theme["bg"], w.theme["bg"])
        self.assertEqual(w.bottom_bar._theme["bottom_bg"], w.theme["bottom_bg"])
        self.assertEqual(w.edit_switch._theme["bg"], w.theme["bg"])
        self.assertEqual(w.dry_run_check._theme["bg"], w.theme["bg"])

    def test_no_instance_env_row(self):
        """Tk 主界面没有「实例环境」那一行(环境只给联网搜索用),Qt 也不画。"""
        w = self.make()
        self.assertFalse(hasattr(w, "env_label"))
        texts = [lab.text() for lab in w.findChildren(QtWidgets.QLabel)]
        self.assertNotIn("实例环境:", texts)

    def test_log_card_structure_follows_tk(self):
        """工具栏在上、日志在中、脚注行在下(右边常驻「打开完整日志」)。"""
        from ui.qt_main import look
        w = self.make()
        card = [s for s in w.findChildren(look.SectionFrame) if s.title() == "执行日志"][0]
        lay = card.layout()
        kinds = [lay.itemAt(i).layout() is not None for i in range(lay.count())]
        self.assertEqual(lay.itemAt(0).layout().count() > 0, True)        # 工具栏
        log_row = lay.itemAt(1).widget()
        self.assertIs(log_row.findChild(QtWidgets.QTextEdit), w.log_text) # 日志 + 总览条
        self.assertIs(w.log_overview.parentWidget(), log_row)
        self.assertTrue(kinds[2])                                         # 脚注行
        self.assertEqual(w.log_full_btn.text(), "打开完整日志")
        self.assertEqual(w.log_hint_label.text(), "💡 双击日志行可定位到清单里的那条")
        # 工具栏里的顺序:放大查看 | 💡提示 | 定位错误 …弹簧… 打开日志文件夹 | 清空日志
        tools = lay.itemAt(0).layout()
        order = []
        for i in range(tools.count()):
            it = tools.itemAt(i)
            if it.widget() is not None:
                order.append(it.widget())
            else:
                order.append(None)                                        # 弹簧
        names = {w._btn_widgets["log_big"]: "big", w.log_hint_label: "hint",
                 w._btn_widgets["fail_locate"]: "fail", w._btn_widgets["log_open"]: "open",
                 w._btn_widgets["log_clear"]: "clear", None: "spring"}
        self.assertEqual([names[x] for x in order],
                         ["big", "hint", "fail", "spring", "open", "clear"])

    def test_open_full_log_without_file_says_so(self):
        w = self.make()
        saved = w._session_log.path
        w._session_log.path = None                       # 还没写过任何日志文件
        try:
            with mock.patch.object(w._session_log, "flush"), \
                    mock.patch.object(QtWidgets.QMessageBox, "information") as info:
                w._open_full_log()
        finally:
            w._session_log.path = saved
        info.assert_called_once()
        self.assertEqual(info.call_args.args[1], "提示")

    def test_open_full_log_opens_the_file(self):
        w = self.make()
        w.log("hello", "INFO")
        w._session_log.flush()
        p = w._session_log.path
        self.assertTrue(p is not None and Path(p).is_file())
        with mock.patch("os.startfile", create=True) as start:
            w._open_full_log()
        start.assert_called_once_with(str(p))


if __name__ == "__main__":
    unittest.main()

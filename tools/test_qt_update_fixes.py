"""本轮审查修掉的几处问题的回归测试（Qt 侧）。

覆盖：
  · 清空日志不再连带清掉「定位错误」的条目
  · 日志放大查看与主日志一样有块数上限，清空时一起清
  · 计数动画被中断后会被删除（stop() 不发 finished）
  · 尾部药丸长按：鼠标抓取被收回 / 左键已松开时取消
  · 滑动中改页签文字，选中药丸不会停在旧几何
  · 总览条视口框不再拿 accent_fg 当强调色
  · 底栏折线遍历前先做快照（跨线程 append 不会炸）
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-update-fixes-")
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


class _WinBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        self.w = self.W()
        self.w.show()
        pump(30)

    def tearDown(self):
        self.w._migration_running = False
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)


class ClearLogTests(_WinBase):
    def test_clear_log_keeps_the_locate_error_entries(self):
        """「定位错误」的条目来自三个清单，和日志文字无关；清日志不该把它一起清掉。"""
        self.w._failed_items = [(0, "a.jar"), (1, "b.orig")]
        self.w._refresh_fail_button()
        self.assertTrue(self.w.fail_btn.isEnabled())
        self.w.log("❌ 出错了", "ERROR")
        self.w._clear_log()
        self.assertEqual(self.w._failed_items, [(0, "a.jar"), (1, "b.orig")])
        self.assertTrue(self.w.fail_btn.isEnabled())
        self.assertIn("2", self.w.fail_btn.text())

    def test_clear_log_still_empties_the_display_and_session(self):
        self.w.log("一行日志", "INFO")
        self.assertTrue(self.w.log_text.toPlainText().strip())
        self.w._clear_log()
        self.assertEqual(self.w.log_text.toPlainText().strip(), "")

    def test_clear_log_empties_the_big_log_too(self):
        self.w.log("一行日志", "INFO")
        self.w._open_big_log_view()
        pump(20)
        self.assertTrue(self.w._log_big_text.toPlainText().strip())
        self.w._clear_log()
        self.assertEqual(self.w._log_big_text.toPlainText().strip(), "")
        self.assertIn("0", self.w._log_big_count.text())

    def test_clear_log_stops_pending_fade_animations(self):
        for i in range(10):
            self.w.log("第 %d 行" % i, "INFO")
        self.assertTrue(getattr(self.w, "_log_fade_anims", []))
        self.w._clear_log()
        self.assertEqual(getattr(self.w, "_log_fade_anims", []), [])
        pump(250)                       # 原来排队的动画如果还在，这里会去改已删除的块
        self.assertEqual(self.w.log_text.document().blockCount(), 1)


class BigLogTests(_WinBase):
    def test_big_log_has_the_same_block_cap_as_the_main_log(self):
        self.w._open_big_log_view()
        pump(20)
        self.assertEqual(self.w._log_big_text.document().maximumBlockCount(),
                         self.w.log_text.document().maximumBlockCount())
        self.assertGreater(self.w._log_big_text.document().maximumBlockCount(), 1000)

    def test_interrupted_count_animation_is_deleted(self):
        """stop() 不发 finished，原来只有 finished 里 deleteLater → 每轮漏一个动画。"""
        self.w._open_big_log_view()
        pump(20)
        self.w._log_big_count_display = 0
        self.w._update_big_log_count()            # 起一个 180ms 的计数动画
        anim = self.w._log_big_count_anim
        self.assertIsNotNone(anim)
        self.w._update_big_log_count()            # 中途再来一次：旧动画被 stop + deleteLater
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        with self.assertRaises(RuntimeError):     # 已删除的 QObject 再访问会抛
            anim.objectName()


class PillLongPressTests(unittest.TestCase):
    def make(self):
        from ui.qt_main.main_tabs import MainTabs
        t = MainTabs(DARK_THEME)
        for i in range(3):
            t.addTab(QtWidgets.QLabel("page%d" % i), "标签%d" % i)
        t.resize(900, 300)
        t.show()
        pump(30)
        self.addCleanup(t.deleteLater)
        return t

    def press(self, t):
        QtTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
        _, tails = t.bar.layout_items()
        pos = QtCore.QPoint(int(tails[0][0] + 10), 20)
        QtTest.mousePress(t.bar, QtCore.Qt.LeftButton, pos=pos)
        pump(200)
        self.assertGreater(t.bar.press_progress(), 0.05)
        return QtTest, pos

    def test_ungrab_mouse_cancels_the_long_press(self):
        """系统把鼠标抓取收回时收不到 mouseReleaseEvent，1 秒后不能照样触发。"""
        t = self.make()
        fired = []
        t.add_tail("b", "apply", "🔁 应用", lambda: fired.append(1))
        self.press(t)
        QtCore.QCoreApplication.sendEvent(t.bar, QtCore.QEvent(QtCore.QEvent.UngrabMouse))
        self.assertEqual(t.bar.press_progress(), 0.0)
        pump(1100)
        self.assertEqual(fired, [])

    def test_window_deactivate_cancels_the_long_press(self):
        t = self.make()
        fired = []
        t.add_tail("b", "apply", "🔁 应用", lambda: fired.append(1))
        self.press(t)
        QtCore.QCoreApplication.sendEvent(t.bar, QtCore.QEvent(QtCore.QEvent.WindowDeactivate))
        pump(1100)
        self.assertEqual(fired, [])

    def test_released_button_without_event_cancels_the_long_press(self):
        """按住期间左键已被松开（事件丢了）—— 下一拍必须自己发现并取消。"""
        t = self.make()
        fired = []
        t.add_tail("b", "apply", "🔁 应用", lambda: fired.append(1))
        QtTest, pos = self.press(t)
        with mock.patch.object(QtGui.QGuiApplication, "mouseButtons",
                               return_value=QtCore.Qt.NoButton):
            pump(60)
        self.assertEqual(t.bar.press_progress(), 0.0)
        pump(1100)
        self.assertEqual(fired, [])

    def test_held_button_still_fires_after_one_second(self):
        t = self.make()
        fired = []
        t.add_tail("b", "apply", "🔁 应用", lambda: fired.append(1))
        self.press(t)
        pump(1100)
        self.assertEqual(fired, [1])


class TabGeometryTests(unittest.TestCase):
    def make(self):
        from ui.qt_main.main_tabs import MainTabs
        t = MainTabs(DARK_THEME)
        for i in range(3):
            t.addTab(QtWidgets.QLabel("page%d" % i), "标签%d" % i)
        t.resize(900, 300)
        t.show()
        pump(30)
        self.addCleanup(t.deleteLater)
        return t

    def test_set_tab_text_during_slide_recalibrates_selection(self):
        t = self.make()
        t.setCurrentIndex(1, animate=True)
        pump(20)                                   # 动画跑到一半
        t.setTabText(1, "🧩 模组清单 12345")         # 宽度大变
        tabs, _ = t.bar.layout_items()
        self.assertEqual((t.bar._sel_x, t.bar._sel_w),
                         (float(tabs[1][0]), float(tabs[1][1])))
        self.assertNotEqual(t.bar._anim.state(), QtCore.QAbstractAnimation.Running)

    def test_selection_snaps_to_the_current_tab_after_animation_ends(self):
        t = self.make()
        t.setCurrentIndex(2, animate=True)
        pump(400)                                  # 等动画跑完（170ms）
        tabs, _ = t.bar.layout_items()
        self.assertEqual((t.bar._sel_x, t.bar._sel_w),
                         (float(tabs[2][0]), float(tabs[2][1])))


class OverviewAccentTests(unittest.TestCase):
    def test_accent_is_not_the_text_color_in_either_theme(self):
        from ui.qt_main.log_overview import LogOverview
        editor = QtWidgets.QTextEdit()
        self.addCleanup(editor.deleteLater)
        for theme in (DARK_THEME, LIGHT_THEME):
            ov = LogOverview(editor, theme)
            self.addCleanup(ov.deleteLater)
            self.assertEqual(ov._accent_color().name().lower(), "#4a90d9")
            self.assertNotEqual(ov._accent_color().name().lower(),
                                theme["accent_fg"].lower())

    def test_accent_follows_an_explicit_theme_override(self):
        from ui.qt_main.log_overview import LogOverview
        editor = QtWidgets.QTextEdit()
        self.addCleanup(editor.deleteLater)
        ov = LogOverview(editor, {**DARK_THEME, "accent": "#123456"})
        self.addCleanup(ov.deleteLater)
        self.assertEqual(ov._accent_color().name().lower(), "#123456")


class WindowSizingTests(_WinBase):
    """主窗口尺寸：与 Tk 同一个设计稿（1000×1080）、居中、最小高度不压内容。"""

    def content_need(self):
        c = self.w.centralWidget()
        return max(c.minimumSizeHint().height(), c.layout().minimumSize().height())

    def test_design_size_matches_tk(self):
        """与 Tk 同一个设计稿 1000×1080；离屏平台屏幕只有 800×800，会被 90% 夹小。"""
        from PySide6 import QtWidgets as W
        screen = self.w.screen() or W.QApplication.primaryScreen()
        cap_w = int(screen.availableGeometry().width() * 0.9) if screen else 1000
        cap_h = int(screen.availableGeometry().height() * 0.9) if screen else 1080
        self.assertEqual(self.w.height(), min(1080, cap_h))
        self.assertEqual(self.w.width(), min(1000, cap_w))

    def screen_cap_h(self):
        from PySide6 import QtWidgets as W
        screen = self.w.screen() or W.QApplication.primaryScreen()
        return max(360, round(screen.availableGeometry().height() * 0.9)) if screen else 10 ** 6

    def test_minimum_height_is_not_below_the_content(self):
        """原来最小高度写死 680，内容要 741 —— 拖到最小会把清单区压掉 61px。

        屏幕放不下时按可用区域的 90% 夹住（离屏虚拟屏只有 800 高，所以这里期望值是夹取后的）。
        """
        self.w.resize(self.w.minimumWidth(), 400)
        pump(40)
        self.assertEqual(self.w.minimumHeight(),
                         min(max(self.content_need(), 700), self.screen_cap_h()))

    def test_minimum_height_never_crushes_content_when_the_screen_allows(self):
        cap = self.screen_cap_h()
        self.w.resize(720, 900)
        pump(40)
        need = self.content_need()
        if need <= cap:
            self.assertGreaterEqual(self.w.minimumHeight(), need)
        else:
            self.assertEqual(self.w.minimumHeight(), cap)      # 屏幕真放不下时只能夹到上限

    def test_minimum_height_follows_narrower_widths(self):
        """内容需要的高度随宽度变化（窄窗要更高），最小高度要跟着涨。"""
        self.w.resize(1000, 900)
        pump(40)
        wide_need = self.content_need()
        self.w.resize(720, 900)
        pump(40)
        narrow_need = self.content_need()
        self.assertEqual(self.w.minimumHeight(),
                         min(max(wide_need, narrow_need, 700), self.screen_cap_h()))

    def test_window_is_centered_like_tk(self):
        from PySide6 import QtWidgets as W
        screen = self.w.screen() or W.QApplication.primaryScreen()
        if screen is None:
            self.skipTest("没有屏幕信息")
        av = screen.availableGeometry()
        want_x = av.x() + max(0, (av.width() - self.w.width()) // 2)
        self.assertEqual(self.w.x(), want_x)

    def test_history_returns_a_snapshot_not_the_live_deque(self):
        """探测线程会在后台往同一个 deque 里 append，直接遍历会抛「deque mutated」。"""
        import collections
        from ui.qt_main.bottom_bar import SparkLine
        live = collections.deque([(1.0, 20), (2.0, 30)])
        with mock.patch("ui.mw_net.rtt_history", lambda: live):
            got = SparkLine._history()
        self.assertIsInstance(got, list)
        live.append((3.0, 40))                     # 快照之后的写入不应影响返回值
        self.assertEqual(got, [(1.0, 20), (2.0, 30)])
        self.assertEqual(len(live), 3)


if __name__ == "__main__":
    unittest.main()

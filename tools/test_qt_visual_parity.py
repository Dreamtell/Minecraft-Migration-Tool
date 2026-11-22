"""外观对齐 Tk 的回归测试（本批 high 项）。

覆盖：
  · 主操作行在日志**上方**（同 Tk 的 opt_frame → log_frame）
  · 「开始迁移」「查看历史」都是 Tk 的 btn_h = 44，且字号档位对齐
  · 翻转按钮浅色面用 Tk 的饱和绿/红/琥珀，不是淡彩
  · 进度条填充取 card_sel_bar（不是 accent_fg）
  · INFO 行按 log_kinds 子类上色、阶段加粗；ERROR 整行 danger_bg 红底
  · 主题切换的圆形揭示在特效关闭/无截图时安全跳过
"""
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-visual-")
CFG = os.path.join(_tmp.name, "config.json")
with open(CFG, "w", encoding="utf-8") as fh:
    fh.write('{"language":"zh","theme":"light","net_float_on":false}')
os.environ["MCTOOL_CONFIG"] = CFG
os.environ["MCTOOL_LANG"] = "zh"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Minecraft迁移工具")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _testcfg import use_config as _use_config   # noqa: E402


def setUpModule():
    _use_config(CFG)


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


class _Win(unittest.TestCase):
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


class MainActionRowTests(_Win):
    def test_action_row_sits_above_the_log(self):
        """Tk：先 opt_frame（开关+按钮）、后 log_frame；Qt 必须同序。

        按屏幕纵坐标判断（而不是数布局项）：主操作行里还有嵌套布局，
        数 item 时会把最上面的顶栏布局也算成「行」。
        """
        pump(40)
        row_y = self.w.start_btn.mapTo(self.w, QtCore.QPoint(0, 0)).y()
        log_y = self.w.log_text.mapTo(self.w, QtCore.QPoint(0, 0)).y()
        self.assertLess(row_y, log_y)

    def test_start_and_history_buttons_are_44_high(self):
        """Tk 的 btn_h = 44；全局 QSS 的 min-height:30 不能把它压回去。"""
        pump(20)
        self.assertEqual(self.w.start_btn.height(), 44)
        self.assertEqual(self.w.start_btn.minimumHeight(), 44)
        self.assertEqual(self.w.history_btn.height(), 44)

    def test_button_font_tiers_match_tk(self):
        """主按钮 14pt（≈19px）、辅助按钮 11pt 粗体。"""
        self.assertEqual(self.w.start_btn.font().pixelSize(), 19)
        self.assertTrue(self.w.start_btn.font().bold())
        self.assertAlmostEqual(self.w.history_btn.font().pointSizeF(), 11.0, places=1)
        self.assertTrue(self.w.history_btn.font().bold())

    def test_blurring_a_list_keeps_the_scroll_position(self):
        """点别处（清单失焦）不能把已经滚走的清单拉回光标处。

        setTextCursor 会让 QTextEdit 把光标滚回可见区——光标在末尾、用户滚到顶部时，
        这就是「点一下别处，清单自己乱滚」。
        """
        ed = self.w.mod_text
        ed.setPlainText("\n".join("mod-%03d.jar" % i for i in range(300)))
        pump(40)
        cur = ed.textCursor()
        cur.movePosition(QtGui.QTextCursor.End)
        ed.setTextCursor(cur)
        pump(40)
        bar = ed.verticalScrollBar()
        bar.setValue(0)
        pump(20)
        self.assertEqual(bar.value(), 0)
        self.w._blur_list_texts()
        pump(60)
        self.assertEqual(bar.value(), 0)


class ColorParityTests(unittest.TestCase):
    def test_light_flip_faces_use_the_tk_colors(self):
        from ui.qt_main import flip_button as fb
        self.assertEqual(fb.FACE_IDLE_L["face_top"], "#2fb36a")
        self.assertEqual(fb.FACE_IDLE_L["face_bot"], "#1f8f52")
        self.assertEqual(fb.FACE_SEARCH_L["face_top"], "#e5534b")
        self.assertEqual(fb.FACE_DRY_L["face_top"], "#f6c046")
        self.assertEqual(fb.FACE_IDLE_L["title"], "#ffffff")

    def test_progress_chunk_is_the_selection_blue_not_accent_fg(self):
        from ui.qt_main import look
        for theme in (LIGHT_THEME, DARK_THEME):
            qss = look.build_qss(theme)
            chunk = qss.split("QProgressBar::chunk", 1)[1]
            chunk = chunk.split("}", 1)[0]
            self.assertIn(theme["card_sel_bar"].lower(), chunk.lower())
            self.assertNotIn("accent_fg", chunk)


class LogColorTests(_Win):
    def last_block_format(self, editor):
        blk = editor.document().lastBlock().previous()      # 最后一行（末尾有空行）
        cur = QtGui.QTextCursor(blk)
        cur.movePosition(QtGui.QTextCursor.EndOfBlock)
        return cur.charFormat()

    def test_info_phase_line_uses_the_phase_color_and_bold(self):
        from ui import log_kinds
        info = self.w.log("【复制阶段】开始复制文件", "INFO")
        blk = self.w.log_text.document().lastBlock().previous()
        cur = QtGui.QTextCursor(blk)
        cur.movePosition(QtGui.QTextCursor.EndOfBlock)
        fmt = cur.charFormat()
        want = QtGui.QColor(log_kinds.kind_color(self.w.theme, "phase")).name().lower()
        self.assertEqual(fmt.foreground().color().name().lower(), want)
        self.assertEqual(fmt.fontWeight(), QtGui.QFont.Bold)
        self.assertGreaterEqual(self.w.log_text.document().blockCount(), 2)

    def test_plain_info_stays_grey(self):
        self.w.log("一行认不出子类的普通信息", "INFO")
        blk = self.w.log_text.document().lastBlock().previous()
        cur = QtGui.QTextCursor(blk)
        cur.movePosition(QtGui.QTextCursor.EndOfBlock)
        fmt = cur.charFormat()
        self.assertNotEqual(fmt.fontWeight(), QtGui.QFont.Bold)
        self.assertEqual(fmt.foreground().color().name().lower(),
                         QtGui.QColor(self.w.theme["log_info_fg"]).name().lower())

    def test_error_line_gets_the_danger_background(self):
        self.w.log("❌ 出错了", "ERROR")
        blk = self.w.log_text.document().lastBlock().previous()
        cur = QtGui.QTextCursor(blk)
        cur.movePosition(QtGui.QTextCursor.EndOfBlock)
        fmt = cur.charFormat()
        self.assertEqual(fmt.background().color().name().lower(),
                         QtGui.QColor(self.w.theme["danger_bg"]).name().lower())


class ThemeRevealTests(_Win):
    def test_reveal_is_a_noop_without_a_snapshot(self):
        self.w._play_theme_reveal(None)                 # 不抛

    def test_reveal_is_skipped_when_effects_are_off(self):
        from utils import fx
        shot = self.w.grab()
        old = fx.anim_scale()
        try:
            fx.set_level("off")
            before = [c for c in self.w.findChildren(QtWidgets.QWidget)]
            self.w._play_theme_reveal(shot)
            pump(20)
            self.assertEqual(len([c for c in self.w.findChildren(QtWidgets.QWidget)]), len(before))
        finally:
            fx.set_level(old)
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()

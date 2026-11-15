"""Regression checks for the Tk virtual table hot scroll path.

These tests use a small fake Canvas so they do not create a Tk window.  The
real widget is still exercised by the application's existing UI probes.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))
from ui.virtual_table import VirtualTable


class VirtualTableScrollTests(unittest.TestCase):
    def make_table(self):
        body = SimpleNamespace(position=0.0)
        body.canvasy = lambda _y: body.position
        table = object.__new__(VirtualTable)
        table.body = body
        table.row_height = 26
        table._first = 0
        table.render_count = 0
        table.scroll_notifications = 0
        def render():
            table.render_count += 1
            table._first = max(0, int(body.position // table.row_height))
        table._render = render
        table.on_scroll = lambda: setattr(
            table, "scroll_notifications", table.scroll_notifications + 1)
        return table, body

    def test_sub_row_pixel_scroll_does_not_repaint_rows(self):
        table, body = self.make_table()
        body.position = 13
        VirtualTable._after_scroll(table)
        self.assertEqual(table.render_count, 0)
        self.assertEqual(table.scroll_notifications, 1)

    def test_crossing_row_boundary_repaints_once(self):
        table, body = self.make_table()
        body.position = 26
        VirtualTable._after_scroll(table)
        self.assertEqual(table.render_count, 1)
        self.assertEqual(table.scroll_notifications, 1)
        self.assertEqual(table._first, 1)

    def test_fractional_position_is_clamped_to_the_first_row(self):
        table, body = self.make_table()
        body.position = -4
        VirtualTable._after_scroll(table)
        self.assertEqual(table.render_count, 0)
        self.assertEqual(table.scroll_notifications, 1)


class EmojiWidthTests(unittest.TestCase):
    """emoji 宽度估算（utils/helpers.emoji_and_text_width）与表头预热。

    为什么不直接量：粗体 Font 第一次 measure 某个 emoji 要 5~380 ms（Tk 现场初始化
    字体回退），放大查看的表头标题全以 emoji 开头，一次开窗光量字就上百毫秒。
    """

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            import tkinter.font as tkfont
        except Exception as exc:                       # pragma: no cover
            raise unittest.SkipTest("没有 tkinter: %s" % exc)
        cls.tkfont = tkfont
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as exc:                       # pragma: no cover
            raise unittest.SkipTest("起不了 Tk: %s" % exc)
        cls.bold = tkfont.Font(family="微软雅黑", size=12, weight="bold")
        cls.plain = tkfont.Font(family="微软雅黑", size=12)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def test_estimate_matches_real_width_for_emoji_strings(self):
        from utils.helpers import emoji_and_text_width
        for f in (self.bold, self.plain):
            for s in ("🔵 状态", "📄 文件名", "🧩 类型", "🆔 Mod ID", "💾 大小KB"):
                est = emoji_and_text_width(f, s)
                real = f.measure(s)
                # 估得偏大一点点是安全的（截断只会多留余量），但不能差太多
                self.assertGreaterEqual(est, real - 1, s)
                self.assertLessEqual(est, real + 3, s)

    def test_plain_text_is_measured_exactly(self):
        from utils.helpers import emoji_and_text_width
        for s in ("纯中文", "mod-1.20.1.jar", ""):
            self.assertEqual(emoji_and_text_width(self.plain, s), self.plain.measure(s), s)

    def test_estimate_never_calls_tk_with_emoji(self):
        """量 emoji 串时，Tk 的 measure 只该收到"去掉 emoji 之后"的普通文字。"""
        from utils.helpers import emoji_and_text_width
        收到 = []
        原 = self.tkfont.Font.measure

        def 拦(self_, text, *a, **k):
            收到.append(str(text))
            return 原(self_, text, *a, **k)

        self.tkfont.Font.measure = 拦
        try:
            emoji_and_text_width(self.bold, "🔵 状态")
        finally:
            self.tkfont.Font.measure = 原
        self.assertTrue(收到)
        for t in 收到:
            self.assertFalse(any(ch in t for ch in "🔵📄📁🧩🆔"), 收到)

    def test_fit_uses_the_emoji_aware_measure(self):
        """_fit 不能退回 Font.measure 直接量 emoji 标题（那正是慢的来源）。"""
        table = object.__new__(VirtualTable)
        table._font = self.bold
        table._fit_cache = {}
        收到 = []
        原 = self.tkfont.Font.measure

        def 拦(self_, text, *a, **k):
            收到.append(str(text))
            return 原(self_, text, *a, **k)

        self.tkfont.Font.measure = 拦
        try:
            out = table._fit("📁 完整路径/完整路径", 80)
        finally:
            self.tkfont.Font.measure = 原
        self.assertTrue(out.startswith("📁"))
        self.assertFalse(any("📁" in t for t in 收到), 收到)

    def test_prewarm_is_cheap_the_second_time(self):
        from ui.virtual_table import _PREWARM_CHARS, prewarm_emoji_font
        import time
        self.assertGreater(len(_PREWARM_CHARS), 50)
        prewarm_emoji_font()                 # 无 widget：同步跑完
        t0 = time.perf_counter()
        prewarm_emoji_font()
        self.assertLess((time.perf_counter() - t0) * 1000, 400,
                        "预热过一遍之后再来应该很快（只走缓存）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""本轮审查修掉的两处 Tk 侧问题的回归测试。

· emoji 预热不再被启动构建期的 root.update() 泵出来：窗口真正显示之后才跑。
· emoji 宽度估算要乘 Tk 的 scaling：100% 下与改动前一致，高 DPI 下不低估。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

import tkinter as tk  # noqa: E402

from utils import helpers  # noqa: E402


class _FakeTk:
    def __init__(self, scaling):
        self._scaling = scaling

    def call(self, *_args):
        return self._scaling


class _FakeFont:
    """够 emoji_and_text_width 用：取字号、量普通文字、读 tk scaling。"""

    def __init__(self, size=12, scaling=4.0 / 3.0, per_char=7):
        self._size = size
        self._root = mock.MagicMock(tk=_FakeTk(scaling))
        self._per_char = per_char

    def cget(self, name):
        if name != "size":
            raise tk.TclError(name)
        return self._size

    def measure(self, text):
        return len(text) * self._per_char


class EmojiWidthTests(unittest.TestCase):
    def test_default_scaling_is_windows_100_percent(self):
        self.assertAlmostEqual(helpers._tk_scaling(None), 4.0 / 3.0, places=4)
        self.assertAlmostEqual(helpers._tk_scaling(object()), 4.0 / 3.0, places=4)

    def test_scaling_is_read_from_the_font(self):
        self.assertAlmostEqual(helpers._tk_scaling(_FakeFont(scaling=2.0)), 2.0, places=4)

    def test_width_at_100_percent_matches_the_old_formula(self):
        """改动前固定是 字号 × 1.88；100% 缩放下必须完全一致，不能变成回归。"""
        width = helpers.emoji_and_text_width(_FakeFont(size=12, scaling=4.0 / 3.0), "😀")
        self.assertEqual(width, int(round(12 * 1.88)))

    def test_width_grows_with_scaling(self):
        lo = helpers.emoji_and_text_width(_FakeFont(size=12, scaling=4.0 / 3.0), "😀")
        hi = helpers.emoji_and_text_width(_FakeFont(size=12, scaling=2.0), "😀")
        self.assertGreater(hi, lo)
        self.assertGreaterEqual(hi, round(12 * 1.88 * (2.0 / (4.0 / 3.0))) - 1)

    def test_plain_text_is_still_measured_by_tk(self):
        text = "普通文字"
        self.assertEqual(helpers.emoji_and_text_width(_FakeFont(), text),
                         len(text) * 7)

    def test_mixed_text_adds_both_parts(self):
        f = _FakeFont(size=12, scaling=4.0 / 3.0)
        self.assertEqual(helpers.emoji_and_text_width(f, "😀中文"),
                         int(round(12 * 1.88)) + 2 * 7)


class PrewarmTimingTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self._destroy)

    def _destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_no_prewarm_before_the_window_is_mapped(self):
        """构建期间 app.py 的 on_stage 每步都 root.update()，不能把预热泵出来。"""
        from ui import virtual_table
        with mock.patch.object(virtual_table, "_prewarm_batch") as batch:
            virtual_table.prewarm_emoji_font(self.root, chunk=8, delay=5)
            for _ in range(20):                 # 模拟构建期的多次 update
                self.root.update()
                self.root.update_idletasks()
        batch.assert_not_called()

    def test_prewarm_runs_after_the_window_shows(self):
        from ui import virtual_table
        with mock.patch.object(virtual_table, "_prewarm_batch") as batch:
            virtual_table.prewarm_emoji_font(self.root, chunk=8, delay=5)
            self.root.update()
            batch.assert_not_called()
            self.root.deiconify()
            self.root.update()
            for _ in range(40):                 # 等第一批（delay=5ms）与后续批次
                self.root.update()
                self.root.after(2, lambda: None)
                self.root.update()
        self.assertGreater(batch.call_count, 0)

    def test_existing_widget_keeps_working_when_not_mapped(self):
        """窗口没显示时不做事，也不能抛异常或留下会一直空转的重排。"""
        from ui import virtual_table
        with mock.patch.object(virtual_table, "_prewarm_batch") as batch:
            virtual_table.prewarm_emoji_font(self.root, chunk=8, delay=5)
            for _ in range(3):
                self.root.update()
        batch.assert_not_called()
        self.root.deiconify()
        self.root.update()


if __name__ == "__main__":
    unittest.main()

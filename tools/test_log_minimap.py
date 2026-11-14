# -*- coding: utf-8 -*-
"""日志放大查看右侧缩略图（ui/log_minimap.py）的回归测试。

全是 Tk 原生控件 + PIL，不需要显示器（Linux 上要 xvfb，本机 Windows 直接跑）。
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "Minecraft迁移工具"))

try:
    import tkinter as tk
except Exception as exc:                                   # pragma: no cover
    raise unittest.SkipTest("没有 tkinter: %s" % exc)

from utils.theme import DARK_THEME, LIGHT_THEME            # noqa: E402
from ui.log_minimap import LogMinimap, _cells              # noqa: E402
from ui.log_overview import LogOverview, marks_of          # noqa: E402


def _pump(root, ms=120):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.005)


def _ev(**kw):
    return type("E", (), kw)()


class MinimapTests(unittest.TestCase):
    N = 600

    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("640x420+40+40")
        # 顺序和放大日志窗口一致：缩略图先 pack(side=right)，文本框再占剩下的
        self.text = tk.Text(self.root, height=10, wrap="word")
        self.wheel = []
        self.before = []
        self.mm = LogMinimap(self.root, self.text, DARK_THEME, width=100,
                             on_before=lambda: self.before.append(1),
                             on_wheel=lambda e: self.wheel.append(e.delta))
        self.mm.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        for tag in ("INFO", "WARNING", "ERROR", "SUCCESS", "SIMULATE"):
            self.text.tag_configure(tag)
        self._fill(0, self.N)
        _pump(self.root, 300)
        self.mm._redraw(force=True)

    def tearDown(self):
        try:
            self.mm.destroy()
            self.root.destroy()
        except Exception:
            pass

    def _fill(self, a, b):
        self.text.configure(state="normal")
        for i in range(a, b):
            if i % 50 == 7:
                self.text.insert("end", "失败 mod-%d.jar\n" % i, "ERROR")
            elif i % 31 == 3:
                self.text.insert("end", "跳过 mod-%d.jar\n" % i, "WARNING")
            else:
                self.text.insert("end", "正在处理 mod-%d.jar 共 %d 项\n" % (i, i), "INFO")
        self.text.configure(state="disabled")

    def _box(self):
        self.mm._redraw(force=True)
        return self.mm._slider

    # ---------- 用例 ----------
    def test_draws_an_image_and_a_slider(self):
        self.assertIsNotNone(self.mm._photo)
        self.assertIsNotNone(self._box())

    def test_slider_center_matches_viewport_center(self):
        self.text.yview("300.0")
        _pump(self.root, 80)
        box = self._box()
        g = self.mm._geom()
        中行 = ((box[0] + box[1]) / 2.0 + g["off"]) / self.mm._lh
        视口中 = (g["top"] + g["bot"]) / 2.0
        self.assertLess(abs(中行 - 视口中), 3.0, (中行, 视口中))

    def test_click_puts_that_line_in_the_middle(self):
        for y in (20, 150, 300):
            self.text.yview("300.0")
            _pump(self.root, 60)
            g = self.mm._geom()
            想要 = int((y + g["off"]) // self.mm._lh) + 1
            self.mm._on_press(_ev(y=y))
            self.mm._on_release(None)
            _pump(self.root, 80)
            g2 = self.mm._geom()
            self.assertTrue(g2["top"] <= 想要 <= g2["bot"], (y, 想要, g2))
        self.assertTrue(self.before, "改视口前要先调 on_before（停掉平滑滚动）")

    def test_press_on_slider_does_not_jump(self):
        self.text.yview("300.0")
        _pump(self.root, 80)
        box = self._box()
        g0 = self.mm._geom()
        self.mm._on_press(_ev(y=(box[0] + box[1]) / 2.0 + 2))
        self.mm._on_release(None)
        _pump(self.root, 60)
        g1 = self.mm._geom()
        self.assertEqual((g0["top"], g0["bot"]), (g1["top"], g1["bot"]))

    def test_drag_slider_scrolls_to_both_ends(self):
        self.text.yview("300.0")
        _pump(self.root, 80)
        box = self._box()
        抓 = (box[0] + box[1]) / 2.0
        h = self.mm.winfo_height()
        self.mm._on_press(_ev(y=抓))
        for k in range(1, 11):
            self.mm._on_drag(_ev(y=抓 + (h - 抓) * k / 10.0))
        self.mm._on_release(None)
        _pump(self.root, 80)
        self.assertEqual(self.mm._geom()["bot"], self.mm._geom()["n"])
        box = self._box()
        抓 = (box[0] + box[1]) / 2.0
        self.mm._on_press(_ev(y=抓))
        for k in range(1, 11):
            self.mm._on_drag(_ev(y=抓 * (1 - k / 10.0)))
        self.mm._on_release(None)
        _pump(self.root, 80)
        self.assertEqual(self.mm._geom()["top"], 1)

    def test_wheel_is_forwarded(self):
        self.mm._on_mousewheel(_ev(delta=-120))
        self.assertEqual(self.wheel, [-120])

    def test_short_log_has_no_slider(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "只有一行\n", "INFO")
        self.text.configure(state="disabled")
        _pump(self.root, 100)
        self.mm._redraw(force=True)
        self.assertIsNone(self.mm._slider)

    def test_empty_text_is_safe(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        _pump(self.root, 100)
        self.mm._redraw(force=True)        # 不该抛
        self.assertIsNone(self.mm._slider)

    def test_theme_switch_repaints(self):
        sig = self.mm._sig
        self.mm.set_theme(LIGHT_THEME)
        _pump(self.root, 60)
        self.assertEqual(self.mm.cget("bg"), LIGHT_THEME["log_bg"])
        self.assertNotEqual(self.mm._sig, sig)
        self.assertEqual(self.mm._bg, tuple(int(LIGHT_THEME["log_bg"][i:i + 2], 16) for i in (1, 3, 5)))

    def test_error_line_gets_a_red_band(self):
        """ERROR 行在缩略图上整行垫一条淡红底（颜色和底色不同），普通行没有。"""
        self.text.yview("1.0")
        _pump(self.root, 80)
        self.mm._redraw(force=True)
        img = self.mm._base
        lh = self.mm._lh
        err_ln = 8                          # i=7 → 第 8 行是 ERROR
        y = (err_ln - 1) * lh + 1
        self.assertEqual(img.getpixel((img.width - 3, y))[:3], self.mm._band)
        self.assertEqual(img.getpixel((img.width - 3, 2 * lh + 1))[:3], self.mm._bg)

    def test_appending_lines_repaints(self):
        self.text.yview_moveto(1.0)
        _pump(self.root, 80)
        self.mm._redraw(force=True)
        sig = self.mm._sig
        self._fill(self.N, self.N + 40)
        self.text.yview_moveto(1.0)
        _pump(self.root, 150)
        self.mm._redraw()
        self.assertNotEqual(self.mm._sig, sig)

    def test_wide_chars_take_two_cells(self):
        self.assertEqual(_cells("a"), 1)
        self.assertEqual(_cells("中"), 2)
        self.assertEqual(_cells("\u200d"), 0)
        self.assertEqual(self.mm._spans("ab 中文  x"), [(0, 2), (3, 7), (9, 10)])


class MarksExpandTests(unittest.TestCase):
    """Tk 会把相邻的同 tag 文字合并成一个范围：连着 12 行 ERROR 只返回 1 对。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("400x300+40+40")
        self.text = tk.Text(self.root, height=10)
        self.text.pack(fill="both", expand=True)
        for tag in ("INFO", "WARNING", "ERROR", "SUCCESS", "SIMULATE"):
            self.text.tag_configure(tag)
        for i in range(60):
            self.text.insert("end", "普通 %d\n" % i, "INFO")
        for i in range(12):
            self.text.insert("end", "失败 %d\n" % i, "ERROR")
        for i in range(60):
            self.text.insert("end", "普通 %d\n" % i, "INFO")
        _pump(self.root, 120)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_run_of_error_lines_expands_to_every_line(self):
        err = [ln for ln, tag in marks_of(self.text) if tag == "ERROR"]
        self.assertEqual(err, list(range(61, 73)))

    def test_range_clip(self):
        err = [ln for ln, tag in marks_of(self.text, 65, 70) if tag == "ERROR"]
        self.assertEqual(err, list(range(65, 71)))

    def test_overview_marks_dense_errors_with_a_taller_tick(self):
        ov = LogOverview(self.text.master, self.text, DARK_THEME, width=14, height=1)
        ov.pack(side="right", fill="y")
        _pump(self.root, 150)
        ov._redraw(force=True)
        红 = [s for s in ov._segs if s[2] == "ERROR"]
        self.assertEqual(len(红), 1)
        self.assertEqual(len(红[0][3]), 12)           # 色块里压着 12 条
        ov.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)

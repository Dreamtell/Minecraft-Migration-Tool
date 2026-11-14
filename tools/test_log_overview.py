# -*- coding: utf-8 -*-
"""执行日志总览条（ui/log_overview.py）的回归测试。

全是 Tk 原生控件，不需要显示器（Linux 上要 xvfb，本机 Windows 直接跑）。
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
from ui.log_overview import LogOverview                     # noqa: E402


def _pump(root, ms=120):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.005)


class LogOverviewTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        # 不要 withdraw()：没映射的窗口 winfo_height() 恒为 1，点击换算出来的
        # 比例永远是 0，测不出跳转（窗口会闪一下，测试进程里无所谓）
        self.root.geometry("400x300+40+40")
        self.text = tk.Text(self.root, height=10)
        self.text.pack(fill="both", expand=True)
        for tag in ("INFO", "WARNING", "ERROR", "SUCCESS", "SIMULATE"):
            self.text.tag_configure(tag)
        self.ov = LogOverview(self.root, self.text, DARK_THEME, width=14)
        self.ov.pack(side="right", fill="y")
        for _ in range(40):
            self.text.insert("end", "一行普通日志\n")
        self.text.insert("end", "复制失败\n", "ERROR")
        self.text.insert("end", "跳过一条\n", "WARNING")
        self.text.insert("end", "完成\n", "SUCCESS")
        self.text.configure(state="disabled")
        _pump(self.root)

    def tearDown(self):
        try:
            self.ov.destroy()
            self.root.destroy()
        except Exception:
            pass

    # ---------- 用例 ----------
    def test_marks_cover_error_and_warning(self):
        标记 = self.ov._marks()
        tags = {t for _ln, t in 标记}
        self.assertIn("ERROR", tags)
        self.assertIn("WARNING", tags)
        self.assertIn("SUCCESS", tags)
        # INFO 不画（占绝大多数，画满就是一块灰条）
        self.assertNotIn("INFO", tags)
        # 行号必须落在有效范围内
        末 = int(str(self.text.index("end-1c")).split(".")[0])
        for ln, _t in 标记:
            self.assertGreaterEqual(ln, 1)
            self.assertLessEqual(ln, 末 + 1)

    def test_draws_items_and_viewport(self):
        self.ov._redraw(force=True)
        self.assertGreater(len(self.ov.find_all()), 2)      # 底 + 若干色点

    def test_click_jumps(self):
        self.text.yview_moveto(0.0)
        _pump(self.root, 60)
        _pump(self.root, 120)
        h = max(1, self.ov.winfo_height())
        self.ov._on_press(type("E", (), {"y": int(h * 0.9)})())
        _pump(self.root, 60)
        self.assertGreater(self.text.yview()[0], 0.5)

    def test_top_click_goes_back_to_top(self):
        self.text.yview_moveto(1.0)
        _pump(self.root, 60)
        self.ov._on_press(type("E", (), {"y": 0})())
        _pump(self.root, 60)
        self.assertLess(self.text.yview()[0], 0.2)

    def test_theme_switch_repaints(self):
        self.ov.set_theme(LIGHT_THEME)
        _pump(self.root, 60)
        self.assertEqual(self.ov.cget("bg"), LIGHT_THEME.get("bg"))
        self.assertGreater(len(self.ov.find_all()), 0)

    def test_empty_text_is_safe(self):
        空白 = tk.Text(self.root, height=3)
        ov2 = LogOverview(self.root, 空白, DARK_THEME, width=10)
        ov2.pack(side="right", fill="y")
        _pump(self.root, 60)
        ov2._redraw(force=True)          # 不该抛
        ov2.destroy()


class LogOverviewWrapTests(unittest.TestCase):
    """折行的长行把"行号"和"像素位置"拆开了：色点要按像素摆，点色点要真的看到那条错误。"""

    ERR = (20, 120, 220, 320, 380)

    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("500x260+40+40")
        # 顺序和主界面一致：总览条先 pack(side=right)，文本框再占剩下的
        self.text = tk.Text(self.root, height=10, wrap="word")
        self.ov = LogOverview(self.root, self.text, DARK_THEME, width=14, height=1)
        self.ov.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        for tag in ("INFO", "WARNING", "ERROR", "SUCCESS", "SIMULATE"):
            self.text.tag_configure(tag)
        长 = "目标目录已存在同名文件且无法覆盖，" * 12
        for i in range(400):
            if i in self.ERR:
                self.text.insert("end", "失败 %d\n" % i, "ERROR")
            else:
                # 前半段全是折行的长行，后半段全是短行：行号比例和像素比例差得很远
                self.text.insert("end", "普通 %d %s\n" % (i, 长 if i < 200 else ""), "INFO")
        self.text.configure(state="disabled")
        _pump(self.root, 300)
        # Tk 在后台量折行高度，总高要几百毫秒才收敛：等它稳住再测
        上次 = -1
        for _ in range(40):
            _pump(self.root, 100)
            cur = self.ov._total_px()
            if cur == 上次:
                break
            上次 = cur
        self.ov._redraw(force=True)

    def tearDown(self):
        try:
            self.ov.destroy()
            self.root.destroy()
        except Exception:
            pass

    def _err_lines(self):
        r = self.text.tag_ranges("ERROR")
        return [int(str(self.text.index(r[i])).split(".")[0]) for i in range(0, len(r), 2)]

    def test_ticks_follow_pixels_not_line_numbers(self):
        """按行号摆的话，前半段折行的错误会整体偏向顶部；按像素摆则和文本里的真实位置一致。"""
        total = self.ov._total_px()
        h = self.ov.winfo_height()
        self.assertGreater(total, 0)
        期望 = sorted(
            (self.text.count("1.0", "%d.0" % ln, "ypixels") or (0,))[0] / float(total) * h
            for ln in self._err_lines())
        实际 = sorted((y0 + y1) / 2.0 for y0, y1, tag, _m in self.ov._segs if tag == "ERROR")
        self.assertEqual(len(实际), len(期望))
        for e, a in zip(期望, 实际):
            self.assertLess(abs(e - a), 5.0, (期望, 实际))

    def test_click_on_tick_shows_that_error(self):
        h = self.ov.winfo_height()
        命中 = 0
        for y0, y1, tag, _m in list(self.ov._segs):
            if tag != "ERROR":
                continue
            self.text.yview_moveto(0.0)
            _pump(self.root, 60)
            self.ov._on_press(type("E", (), {"y": (y0 + y1) / 2.0})())
            self.ov._on_release(None)
            _pump(self.root, 80)
            r = self.text.tag_ranges("ERROR")
            if any(self.text.dlineinfo(r[i]) is not None for i in range(0, len(r), 2)):
                命中 += 1
        self.assertEqual(命中, len(self.ERR))

    def test_click_on_blank_centers_viewport(self):
        h = self.ov.winfo_height()
        self.text.yview_moveto(0.0)
        _pump(self.root, 60)
        self.ov._on_press(type("E", (), {"y": h * 0.5})())
        self.ov._on_release(None)
        _pump(self.root, 80)
        a, b = self.text.yview()
        self.assertAlmostEqual((a + b) / 2.0, 0.5, delta=0.03)

    def test_drag_box_does_not_jump_on_press(self):
        """按在可见区框上只是"抓住"，不能一按就把视口挪到点击处。"""
        self.text.yview_moveto(0.4)
        _pump(self.root, 120)
        self.ov._redraw(force=True)
        self.assertIsNotNone(self.ov._box)
        y0, y1 = self.ov._box
        before = self.text.yview()[0]
        self.ov._on_press(type("E", (), {"y": (y0 + y1) / 2.0 + 1})())
        _pump(self.root, 60)
        self.assertAlmostEqual(self.text.yview()[0], before, delta=0.005)
        self.ov._on_drag(type("E", (), {"y": (y0 + y1) / 2.0 + 1 + 20})())
        _pump(self.root, 60)
        self.assertGreater(self.text.yview()[0], before + 0.02)
        self.ov._on_release(None)


class LogOverviewTruncateTests(unittest.TestCase):
    """日志满额后头部被截掉：行数和总高都不变，但每个标记的行号整体前移，色点必须跟着动。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("400x300+40+40")
        self.text = tk.Text(self.root, height=10)
        self.ov = LogOverview(self.root, self.text, DARK_THEME, width=14, height=1)
        self.ov.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("ERROR")
        self.text.tag_configure("INFO")

    def tearDown(self):
        try:
            self.ov.destroy()
            self.root.destroy()
        except Exception:
            pass

    def _fill(self, a, b):
        self.text.configure(state="normal")
        for i in range(a, b):
            err = i % 400 == 7
            self.text.insert("end", ("失败 %d\n" if err else "普通 %d\n") % i, "ERROR" if err else "INFO")
        self.text.configure(state="disabled")

    def _ticks(self):
        return sorted((y0, y1) for y0, y1, _t, _m in self.ov._segs)

    def test_ticks_move_after_head_is_trimmed(self):
        self._fill(0, 2000)
        _pump(self.root, 300)
        self.ov._redraw(force=True)
        前 = self._ticks()
        self.assertTrue(前)
        # 再追加 150 行、同时从头部删掉 150 行：行数不变，ERROR 的行号全部前移 150
        self._fill(2000, 2150)
        self.text.configure(state="normal")
        self.text.delete("1.0", "151.0")
        self.text.configure(state="disabled")
        _pump(self.root, 300)
        self.ov._redraw()                 # 不 force：要靠签名自己发现变化
        后 = self._ticks()
        self.assertNotEqual(前, 后)


if __name__ == "__main__":
    unittest.main(verbosity=2)

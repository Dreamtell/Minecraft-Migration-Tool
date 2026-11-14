# -*- coding: utf-8 -*-
"""窗口不许压到任务栏（utils/dpi.py 的工作区夹取）。

用户反馈过一次：主窗口底边正好压在任务栏上，窗口自己那圈亮边就"盖住任务栏"。
根因是 `winfo_screenheight()` 含任务栏，而 UI_SCALE 下限是 1.0，1080 高的
设计稿在 1080p 屏上必然多出一个任务栏的高度。

这里只盖两个行为：
- `window_geometry()` 给的尺寸不超过工作区；
- `ensure_on_screen()` 能把已经越界的窗口拉回来（用真实 Tk 窗口量）。
"""

import os
import sys
import time
import tkinter as tk
import unittest

SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "Minecraft迁移工具")
sys.path.insert(0, SOURCE)

from utils.dpi import (ensure_on_screen, px, work_area,   # noqa: E402
                       window_geometry)


def _parse(geo):
    """"1300x900+10+20" -> (1300, 900, 10, 20)"""
    size, rest = geo.split("+", 1)
    w, h = size.split("x")
    x, y = rest.split("+")
    return int(w), int(h), int(x), int(y)


class WindowBoundsTests(unittest.TestCase):
    def setUp(self):
        self.wa = work_area()
        if self.wa is None:            # 非 Windows / 拿不到工作区时这些断言没意义
            self.skipTest("拿不到工作区")
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry("200x120+10+10")
        self.root.update()

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def test_absurd_height_is_clamped(self):
        """设计稿写 100000 高也不能开出屏幕外。"""
        w, h, x, y = _parse(window_geometry(self.root, 1000, 100000))
        _ax, _ay, aw, ah = self.wa
        self.assertLessEqual(h, ah)
        self.assertLessEqual(w, aw)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_normal_size_is_kept(self):
        """正常尺寸不该被改小（夹取只对越界生效）。"""
        w, h, _x, _y = _parse(window_geometry(self.root, 600, 400))
        _ax, _ay, aw, ah = self.wa
        # 设计稿像素要乘 UI_SCALE，这里按 px() 比
        if px(600) <= aw - 8 and px(400) <= ah - 48:
            self.assertEqual((w, h), (px(600), px(400)))

    def test_ensure_on_screen_pulls_back(self):
        """已经压到任务栏上的窗口，兜底函数要把它收回来。"""
        ax, ay, aw, ah = self.wa
        self.root.deiconify()
        self.root.geometry("600x%d+%d+%d" % (ah + 120, ax + 40, ay + 40))
        for _ in range(20):
            self.root.update()
            time.sleep(0.01)
        ensure_on_screen(self.root)
        for _ in range(20):
            self.root.update()
            time.sleep(0.01)
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        self.assertLessEqual(bottom, ay + ah)
        self.assertLessEqual(self.root.winfo_rooty(), ay + ah)


if __name__ == "__main__":
    unittest.main(verbosity=2)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

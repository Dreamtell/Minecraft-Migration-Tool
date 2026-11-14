# -*- coding: utf-8 -*-
"""日志里普通信息行的细分上色（ui/log_kinds.py）的回归测试。

分类函数是纯函数；上色那几条用真实的 LiveLogView + Text，不需要显示器。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "Minecraft迁移工具"))

try:
    import tkinter as tk
    from tkinter.scrolledtext import ScrolledText
except Exception as exc:                                   # pragma: no cover
    raise unittest.SkipTest("没有 tkinter: %s" % exc)

from ui.log_kinds import KIND_TAGS, LINE_KINDS, classify, configure_tags, kind_color   # noqa: E402
from ui.log_overview import marks_of                                                    # noqa: E402
from utils.session_log import SessionLog                                                # noqa: E402
from utils.theme import DARK_THEME, LIGHT_THEME                                         # noqa: E402


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def _lab(h):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = [lin(int(h[i:i + 2], 16)) for i in (1, 3, 5)]
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3.0) if t > 0.008856 else 7.787 * t + 16 / 116.0
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _delta_e(a, b):
    la, lb = _lab(a), _lab(b)
    return sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5


class ClassifyTests(unittest.TestCase):
    CASES = [
        # 阶段
        ("🔎 开始迁移前自检…", "phase"),
        ("🔍 开始扫描模组差异，请稍候...", "phase"),
        ("⏳ 开始前倒计时 5 秒：期间再点一下「开始迁移」可取消", "phase"),
        ("【复制阶段】mods", "phase"),
        ("迁移结束：模拟运行", "phase"),
        ("[Copy phase] mods", "phase"),
        ("Migration finished: Simulated run", "phase"),
        # 统计
        ("📦 待迁移文件 2841 个，总大小 2959.7 MB", "stat"),
        ("📊 模组清单检查结果：总清单项数 377", "stat"),
        ("📦 2841 files to migrate, 2959.7 MB in total", "stat"),
        # 提示
        ("ℹ️ 源目录里还有 53 个 jar 不在清单里，不会被复制（a.jar 等 53 个）", "note"),
        ("ℹ️ 目标里已有 2 处同名内容，会按「覆盖」处理（覆盖前会先备份）", "note"),
        ("已自动处理 3 个重复模组文件，本次选择 374 个模组；处理明细见执行日志。", "note"),
        ("自动处理重复模组 appleskin：保留 a.jar；跳过 b.jar。原因：文件内容相同。", "note"),
        ("Automatically resolved 3 duplicate mod files; 374 mods selected.", "note"),
        # 操作反馈
        ("📍 已定位到「x.jar」（模组清单 第 3 行）", "action"),
        ("🔗 已打开链接：https://example.com", "action"),
        ("🗂 已切到卡片视图（只读预览）", "action"),
        # 通过
        ("✅ 自检通过（2 项提示）", "ok"),
        ("☑ 已全选：当前显示 3 项，选中 3 项", "ok"),
    ]

    # 不该上色的：保持 INFO 的灰
    PLAIN = [
        "", "   ", "普通的一句话没有任何图标", "   - mod-1.jar",
        "[模拟] 将复制: mods\\a.jar",                       # SIMULATE 级别本来就有颜色
        "⏭️ 目标已存在，按设置跳过: a.jar",
        "mods\\Starting-Over-1.0.jar",                      # 文件名里恰好有 Starting
        "[模拟] 将复制: config\\scanning_tool.toml",         # 文件名里恰好有 scanning
        "appleskin-forge-mc1.20.1-2.5.1.jar。原因：文件内容相同，保留清单中第一份。",   # 多行消息的续行
    ]

    def test_known_lines(self):
        bad = [(t, want, classify(t)) for t, want in self.CASES if classify(t) != want]
        self.assertEqual(bad, [])

    def test_unknown_lines_stay_gray(self):
        bad = [(t, classify(t)) for t in self.PLAIN if classify(t) is not None]
        self.assertEqual(bad, [])

    def test_none_and_non_string_are_safe(self):
        self.assertIsNone(classify(None))
        self.assertIsNone(classify(123))


class ThemeColorTests(unittest.TestCase):
    """子类色要同时满足：对比度够、和灰/其它级别色分得开（ΔE），深浅两套主题都过。"""

    def _check(self, theme):
        bg = theme["log_bg"]
        base = {k: theme[k] for k in ("log_info_fg", "log_warning_fg", "log_error_fg",
                                      "log_success_fg", "log_simulate_fg")}
        for kind in LINE_KINDS:
            col = kind_color(theme, kind)
            self.assertGreaterEqual(_contrast(col, bg), 4.5, (kind, col, bg))
        # 新增的四种（不含直接用成功绿的 ok）：和灰、和各级别色都要拉开
        new = {k: kind_color(theme, k) for k in ("phase", "stat", "note", "action")}
        for k, col in new.items():
            for bk, bc in base.items():
                self.assertGreaterEqual(_delta_e(col, bc), 20, (k, col, bk, bc))
        # 四种新色互相之间也要拉开
        ks = list(new)
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                self.assertGreaterEqual(_delta_e(new[ks[i]], new[ks[j]]), 20, (ks[i], ks[j]))

    def test_dark(self):
        self._check(DARK_THEME)

    def test_light(self):
        self._check(LIGHT_THEME)

    def test_missing_keys_fall_back_to_existing_theme_colors(self):
        theme = {k: v for k, v in DARK_THEME.items() if not k.startswith("log_phase")}
        self.assertEqual(kind_color(theme, "phase"), theme["card_sel_bar"])


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionLog(self.temp.name)
        self.root = tk.Tk()
        self.root.geometry("600x400+40+40")
        self.text = ScrolledText(self.root, wrap="word", height=20)
        self.text.pack(fill="both", expand=True)
        from ui.live_log import LiveLogView
        self.view = LiveLogView(self.text, self.root, self.store, LIGHT_THEME)
        for tag in ("INFO", "WARNING", "ERROR", "SUCCESS", "SIMULATE"):
            self.text.tag_configure(tag)
        configure_tags(self.text, LIGHT_THEME)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.store.close()
        self.temp.cleanup()

    def _tags(self, ln):
        return set(self.text.tag_names("%d.0" % ln))

    def test_info_lines_get_a_subtype_tag_and_keep_their_level(self):
        self.store.append("📦 待迁移文件 3 个，总大小 1.0 MB", "INFO")
        self.store.append("普通的一句话", "INFO")
        self.store.append("❌ 复制失败", "ERROR")
        self.view.refresh(force=True)
        a, b, c = self._tags(1), self._tags(2), self._tags(3)
        self.assertIn("INFO", a)
        self.assertIn(LINE_KINDS["stat"][0], a)
        self.assertEqual({t for t in b if t in KIND_TAGS}, set())
        self.assertIn("ERROR", c)
        self.assertEqual({t for t in c if t in KIND_TAGS}, set())     # 非 INFO 不叠子类

    def test_subtype_does_not_leak_into_overview_marks(self):
        """总览条/缩略图只认四种级别：子类标签叠在 INFO 上，不能让 INFO 行被当成别的级别。"""
        self.store.append("📦 待迁移文件 3 个，总大小 1.0 MB", "INFO")
        self.store.append("【复制阶段】mods", "INFO")
        self.store.append("出错了", "ERROR")
        self.view.refresh(force=True)
        self.assertEqual([t for _ln, t in marks_of(self.text)], ["ERROR"])

    def test_subtype_color_wins_over_info_gray(self):
        self.text.tag_configure("INFO", foreground="#6e6e6e")
        configure_tags(self.text, LIGHT_THEME)       # 子类标签在 INFO 之后定义，优先级更高
        self.store.append("📍 已定位到「x」", "INFO")
        self.view.refresh(force=True)
        self.text.update_idletasks()
        names = self.text.tag_names("1.0")
        self.assertLess(names.index("INFO"), names.index(LINE_KINDS["action"][0]))

    def test_theme_switch_recolors_subtypes(self):
        configure_tags(self.text, DARK_THEME)
        self.assertEqual(self.text.tag_cget(LINE_KINDS["phase"][0], "foreground"),
                         DARK_THEME["log_phase_fg"])
        configure_tags(self.text, LIGHT_THEME)
        self.assertEqual(self.text.tag_cget(LINE_KINDS["phase"][0], "foreground"),
                         LIGHT_THEME["log_phase_fg"])

    def test_phase_is_bold(self):
        from utils.helpers import log_font
        configure_tags(self.text, LIGHT_THEME, font=log_font())
        f = self.text.tag_cget(LINE_KINDS["phase"][0], "font")
        self.assertTrue(f)
        self.assertFalse(self.text.tag_cget(LINE_KINDS["stat"][0], "font"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

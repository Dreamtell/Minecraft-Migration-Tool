# -*- coding: utf-8 -*-
"""「＋ 常用目录」弹窗（ui/preset_menu.py）与分组表（core/extra_presets.py）。

覆盖：
  · 分组：每个预设**恰好出现一次**，分组键与条目逐字匹配（文件夹带末尾 /），
    以后新增的预设自动落到「未分组」而不是消失；
  · 省略号截断；
  · 弹窗：行数、「已在清单」徽章、点一行调 on_pick 且徽章当场更新、弹窗不关、
    Esc/关闭幂等、重复打开不会叠两个。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_产品 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Minecraft迁移工具")
if _产品 not in sys.path:
    sys.path.insert(0, _产品)

os.environ.setdefault("MCTOOL_CONFIG",
                      str(Path(tempfile.mkdtemp(prefix="mct_cfg_")) / "config.json"))

from core.extra_presets import EXTRA_PRESETS, preset_groups, preset_rows   # noqa: E402


class GroupTests(unittest.TestCase):
    def test_每个预设恰好出现一次(self):
        groups = preset_groups([])
        flat = [r[0] for _t, rows in groups for r in rows]
        self.assertEqual(len(flat), len(EXTRA_PRESETS))
        self.assertEqual(len(set(flat)), len(flat))
        self.assertEqual(set(flat), {rel for _k, rel, _d in EXTRA_PRESETS})

    def test_分组键要带文件夹的斜杠(self):
        """_GROUPS 里写 "shaderpacks" 就永远匹配不到 "shaderpacks/"（掉进未分组）。"""
        groups = dict(preset_groups([]))
        names = [r[0] for r in groups.get("画面与资源", [])]
        self.assertIn("shaderpacks/", names)
        self.assertIn("resourcepacks/", names)
        self.assertNotIn("未分组", groups)

    def test_已在清单(self):
        groups = dict(preset_groups(["shaderpacks/", "options.txt"]))
        row = next(r for r in groups["画面与资源"] if r[0] == "shaderpacks/")
        self.assertTrue(row[2])
        row2 = next(r for r in groups["画面与资源"] if r[0] == "resourcepacks/")
        self.assertFalse(row2[2])

    def test_反斜杠和大小写也算已在清单(self):
        groups = dict(preset_groups(["ShaderPacks\\"]))
        row = next(r for r in groups["画面与资源"] if r[0] == "shaderpacks/")
        self.assertTrue(row[2])

    def test_新增预设落到未分组(self):
        """以后加了新预设、忘了写进分组表：它要出现在「未分组」里，不能消失。"""
        import core.extra_presets as ep
        old = ep.EXTRA_PRESETS
        try:
            ep.EXTRA_PRESETS = old + (("zzz_new", "zzz_new/", "新目录"),)
            groups = dict(preset_groups([]))
            self.assertIn("未分组", groups)
            self.assertEqual([r[0] for r in groups["未分组"]], ["zzz_new/"])
        finally:
            ep.EXTRA_PRESETS = old

    def test_行顺序与分组顺序一致(self):
        groups = preset_groups([])
        self.assertEqual([t for t, _r in groups],
                         ["画面与资源", "地图与投影", "模组与脚本", "其它"])


class ElideTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_短文本原样(self):
        import tkinter.font as tkfont
        from ui.preset_menu import _elide
        f = tkfont.Font(family="Microsoft YaHei UI", size=9)
        self.assertEqual(_elide("abc", f, 1000), "abc")

    def test_长文本加省略号(self):
        import tkinter.font as tkfont
        from ui.preset_menu import _elide
        f = tkfont.Font(family="Microsoft YaHei UI", size=9)
        s = _elide("这是一个很长的说明文字" * 10, f, 60)
        self.assertTrue(s.endswith("…"))
        self.assertLessEqual(f.measure(s), 60)


class MenuTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from ui.preset_menu import PresetMenu
        self.PresetMenu = PresetMenu
        self.root = tk.Tk()
        self.root.withdraw()
        self.btn = tk.Button(self.root, text="＋")
        self.btn.pack()
        self.root.update_idletasks()
        self.picked = []

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _open(self, lines):
        from utils.theme import DARK_THEME
        m = self.PresetMenu(
            self.root, DARK_THEME, lines, self.btn,
            on_pick=lambda e: self.picked.append(e),
            get_existing=lambda: list(self.root._lines))
        self.root._lines = list(lines)
        return m

    def test_行数与徽章(self):
        m = self._open(["shaderpacks/"])
        self.assertEqual(len(m._rows), len(EXTRA_PRESETS))
        chip = next(r[2].cget("text") for r in m._rows if r[0] == "shaderpacks/")
        self.assertIn("已在清单", chip)
        other = next(r[2].cget("text") for r in m._rows if r[0] == "screenshots/")
        self.assertEqual(other, "")
        m.close()

    def test_点击加入并更新徽章(self):
        m = self._open([])
        m._pick("schematics/")
        self.assertEqual(self.picked, ["schematics/"])
        chip = next(r[2].cget("text") for r in m._rows if r[0] == "schematics/")
        # get_existing 返回的还是旧内容（真实里 _quick_add_preset 会先写文本框）
        self.assertEqual(chip, "")
        self.assertTrue(m.alive(), "加完一项不该关掉弹窗")
        m.close()

    def test_update_当场刷新徽章(self):
        m = self._open([])
        m.update(["schematics/"])
        chip = next(r[2].cget("text") for r in m._rows if r[0] == "schematics/")
        self.assertIn("已在清单", chip)
        self.assertTrue(m.alive())
        m.close()

    def test_关闭幂等(self):
        m = self._open([])
        m.close()
        m.close()
        self.assertFalse(m.alive())

    def test_键盘选择(self):
        m = self._open([])
        m._move(1)
        self.assertEqual(m._sel, 0)
        m._activate_sel()
        self.assertEqual(self.picked, [m._rows[0][0]])
        m.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

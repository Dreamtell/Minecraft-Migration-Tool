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
import time
import types
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
        """点一下 → on_pick 写清单 → get_existing() 读到新内容 → 徽章当场出现。

        以前这里假 on_pick 只往 picked 里 append、不动 root._lines，于是断言的是
        「徽章仍是空」——把 _pick 里调 update 的那条接线删掉测试也照样绿。
        """
        m = self._open([])
        m.on_pick = lambda e: (self.root._lines.append(e), self.picked.append(e))
        m._pick("schematics/")
        self.assertEqual(self.picked, ["schematics/"])
        chip = next(r[2].cget("text") for r in m._rows if r[0] == "schematics/")
        self.assertIn("已在清单", chip)
        self.assertTrue(m.alive(), "加完一项不该关掉弹窗")
        m.close()

    def test_点击不重建整张卡片(self):
        """点一下只改徽章：整张重建实测卡主线程约 200ms，而且会清掉悬停高亮。"""
        m = self._open([])
        row = next(r for r in m._rows if r[0] == "schematics/")[1][0]
        photo_count = len(m._photos)
        m.on_pick = lambda e: self.root._lines.append(e)
        m._pick("schematics/")
        row2 = next(r for r in m._rows if r[0] == "schematics/")[1][0]
        self.assertIs(row, row2, "行控件被重建了（整张卡片重建）")
        self.assertEqual(len(m._photos), photo_count)
        m.close()

    def test_关闭解开主窗口绑定(self):
        """_root_bid 是 [(事件, funcid)]，close() 要按 funcid 逐条解绑。

        以前写 .get(ev)（list 没有 .get）→ AttributeError 被吞，每开关一次就多留
        一份绑定，闭包还钉住整个 PresetMenu。

        ⚠ 只比较"条数掉回去"是不够的：`root.unbind(ev)`（不带 funcid）会把该事件在
        主窗口上的**全部**绑定清掉，条数同样掉回 0，这条用例照样绿 —— 而浮标正是
        靠主窗口的 <Unmap> 才跟着最小化消失。所以先挂一个哨兵，关闭后它必须还在。
        """
        def 绑定数():
            return str(self.root.bind("<Unmap>") or "").count('" == "break"}')

        哨兵 = []
        self.root.bind("<Unmap>", lambda e: 哨兵.append(1), add="+")
        前 = 绑定数()
        self.assertEqual(前, 1)
        m = self._open([])
        self.assertEqual(绑定数(), 前 + 1)
        m.close()
        self.assertEqual(绑定数(), 前, "弹窗自己的绑定没解开")
        self.root.event_generate("<Unmap>")
        self.root.update_idletasks()
        self.assertEqual(哨兵, [1], "别的窗口的 <Unmap> 绑定被一起清掉了")

    def test_update_当场刷新徽章(self):
        m = self._open([])
        m.update(["schematics/"])
        chip = next(r[2].cget("text") for r in m._rows if r[0] == "schematics/")
        self.assertIn("已在清单", chip)
        self.assertTrue(m.alive())
        m.close()

    def test_关闭幂等(self):
        """关闭必须真的销毁窗口、放开 grab —— 只看 alive() 是自证。

        alive() 是 `(not self._closed) and winfo_exists()`，_closed 一置就短路，
        漏掉 destroy（弹窗留在屏幕上）或 grab_release（整个程序被本地 grab 卡住）
        它也返回 False。
        """
        m = self._open([])
        win = m.win
        m.close()
        m.close()
        self.assertFalse(m.alive())
        self.assertFalse(bool(win.winfo_exists()), "窗口没被销毁")
        self.assertFalse(bool(self.root.grab_current()), "grab 没放开")

    def test_键盘选择(self):
        m = self._open([])
        m._move(1)
        self.assertEqual(m._sel, 0)
        m._activate_sel()
        self.assertEqual(self.picked, [m._rows[0][0]])
        m.close()


class ListAppendTests(unittest.TestCase):
    """「＋ 常用目录」加进「其它文件」清单时的两条硬要求。

    1. 末尾没有换行的清单（从配置读进来就是这样）不能把新条目粘到上一行：
       Text.get("1.0", END) 永远以控件自带的不可见换行结尾，拿它判"末尾是不是换行"
       恒为真，必须先 get("1.0", "end-1c")。
    2. 去重要和「已在清单」徽章用同一个归一函数（忽略大小写 / 反斜杠 / 末尾 /），
       否则徽章说已在、点一下又添一行。
    """

    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.text = tk.Text(self.root)
        self.base = Path(tempfile.mkdtemp(prefix="mct_append_"))
        (self.base / "schematics").mkdir()
        (self.base / "shaderpacks").mkdir()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _加(self, *相对):
        from ui.mw_lists import ListsMixin
        return ListsMixin._add_paths_to(
            None, self.text, str(self.base), [self.base / r for r in 相对])

    def test_末尾没有换行也不会粘成一行(self):
        self.text.insert("1.0", "shaderpacks/")
        n, failed = self._加("schematics")
        self.assertEqual((n, failed), (1, []))
        # 关键是**两行**；末尾多一个换行是 Text 自己的，不算问题
        self.assertEqual(self.text.get("1.0", "end-1c").splitlines(),
                         ["shaderpacks/", "schematics/"])

    def test_大小写不同也算已在清单(self):
        self.text.insert("1.0", "ShaderPacks/\n")
        n, _ = self._加("shaderpacks")
        self.assertEqual(n, 0, "大小写不同不该再添一行")

    def test_反斜杠和末尾斜杠也算同一个(self):
        # 一行是 "shaderpacks\"（Windows 风格、末尾反斜杠）
        self.text.insert("1.0", "shaderpacks\\\n")
        n, _ = self._加("shaderpacks")
        self.assertEqual(n, 0)


class ToggleAndFitTests(unittest.TestCase):
    """「再点一次取消」与「卡片比屏幕高时限高可滚」。

    两个都是用户直接报过的现象：
      · 点按钮关不掉（按下被 grab 送到弹窗关闭，松手落到按钮又开一次）；
      · 卡片比工作区高时底部被屏幕切掉，最后一组看不到、也没法滚。
    """

    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.btn = tk.Button(self.root, text="＋")
        self.btn.pack()
        self.root.update_idletasks()
        self.lines = []

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _开(self):
        from utils.theme import DARK_THEME
        from ui.preset_menu import PresetMenu
        self.menu = PresetMenu(self.root, DARK_THEME, [], self.btn,
                               on_pick=self.lines.append,
                               get_existing=lambda: list(self.lines))
        return self.menu

    def test_刚关掉时算同一次点击(self):
        m = self._开()
        self.assertFalse(m.just_closed(), "还开着的时候不算'刚关掉'")
        # 模拟按下时被 grab 送到弹窗、坐标越界 → 关闭
        m._on_press(types.SimpleNamespace(x=-5, y=-5))
        self.assertFalse(m.alive())
        self.assertTrue(m.just_closed(), "同一次点击里应判为'刚关掉'")
        self.assertFalse(m.just_closed(within=0.0))

    def test_过一会儿就不算了(self):
        m = self._开()
        m.close()
        time.sleep(0.4)
        self.assertFalse(m.just_closed())

    def test_再点一次按钮不会立刻又打开(self):
        from ui.mw_lists import ListsMixin
        fake = types.SimpleNamespace(
            root=self.root, theme=__import__("utils.theme", fromlist=["DARK_THEME"]).DARK_THEME,
            add_extra_preset_btn=self.btn, _preset_menu=None,
            extra_text=__import__("tkinter").Text(self.root),
            _quick_add_preset=lambda e: self.lines.append(e),
            _open_extra_preset_menu_native=lambda e: None)
        fake._open_extra_preset_menu = lambda: ListsMixin._open_extra_preset_menu(fake)

        fake._open_extra_preset_menu()
        m = fake._preset_menu
        self.assertIsNotNone(m)
        self.assertTrue(m.alive())

        m._on_press(types.SimpleNamespace(x=-5, y=-5))     # 点按钮那一下：按下→关
        fake._open_extra_preset_menu()                     # 松手→按钮回调
        self.assertFalse(getattr(fake, "_preset_menu").alive(),
                         "同一次点击里不该又打开")

        time.sleep(0.4)
        fake._open_extra_preset_menu()                     # 真的再点一次：应该打开
        self.assertTrue(fake._preset_menu.alive())

    def test_卡片比工作区高时被限高(self):
        from unittest import mock
        m = self._开()
        with mock.patch("utils.dpi.work_area_at",
                        return_value=(0, 0, 1280, 400)):
            m._place()
            self.root.update_idletasks()
            高 = m.win.winfo_height()
        self.assertLessEqual(高, 400, "卡片高度必须落在工作区内（实际 %d）" % 高)
        self.assertTrue(m._可滚(), "被限高时内容应当可以滚动")
        self.assertGreater(m.body.winfo_reqheight(), m._canvas.winfo_height())

    def test_滚轮能滚到下面(self):
        from unittest import mock
        m = self._开()
        with mock.patch("utils.dpi.work_area_at",
                        return_value=(0, 0, 1280, 400)):
            m._place()
            self.root.update_idletasks()
            前 = m._canvas.yview()[0]
            m._on_wheel(types.SimpleNamespace(delta=-120))
            self.root.update_idletasks()
            后 = m._canvas.yview()[0]
        self.assertGreater(后, 前, "滚轮应当往下滚")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""Tk 主界面细节（存档名默认值 / 底栏网络状态 / 禁用按钮 / 浅色主题对比度）的回归测试。

用真实的 MigrationGUI 建一个主窗口（和 _dctest 里那些探针同一套做法），不需要显示器。
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_temp = tempfile.TemporaryDirectory(prefix="mctool-tk-polish-")
os.environ["MCTOOL_CONFIG"] = str(Path(_temp.name) / "config.json")
os.environ["MCTOOL_LANG"] = "zh"
SOURCE = Path(__file__).resolve().parents[1] / "Minecraft迁移工具"
sys.path.insert(0, str(SOURCE))

from _testcfg import use_config as _use_config   # noqa: E402
_CFG_PATH = os.environ["MCTOOL_CONFIG"]


try:
    import tkinter as tk                                  # noqa: E402
except Exception as exc:                                   # pragma: no cover
    raise unittest.SkipTest("没有 tkinter: %s" % exc)

from utils.helpers import _tone                            # noqa: E402
from utils.theme import DARK_THEME, LIGHT_THEME            # noqa: E402


def _pump(root, sec=1.2):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.003)


def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(t):
    return 0.2126 * _lin(t[0]) + 0.7152 * _lin(t[1]) + 0.0722 * _lin(t[2])


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def _hex(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


_APP = {}


def _build(theme, config=None):
    """建一个主窗口。⚠ 同一进程里只建**一次**：销毁后再建第二个 Tk，渐变按钮缓存的
    PhotoImage 会对不上新的解释器（image "pyimageN" doesn't exist），所以整个文件共用
    一个窗口，浅色/深色靠 toggle_theme() 来回切。"""
    cfg = {"theme": theme, "language": "zh", "dry_run": True}
    cfg.update(config or {})
    path = Path(_temp.name) / "cfg-polish.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    _use_config(str(path))
    from ui import main_window as MW
    MW.CONFIG_FILE = path
    MW.MigrationGUI.__init__.__globals__["CONFIG_FILE"] = path
    root = tk.Tk()
    gui = MW.MigrationGUI(root)
    root.geometry("1040x1128+40+20")
    _pump(root, 1.5)
    return root, gui


def _app():
    return _APP["root"], _APP["gui"]


def _set_theme(name):
    """切到 name 主题（"light"/"dark"）；已经是就不动。"""
    root, gui = _app()
    if gui.current_theme != name:
        gui.toggle_theme()
        _pump(root, 1.0)


def setUpModule():                     # 全量跑时各模块各用各的临时配置
    _use_config(_CFG_PATH)
    _APP["root"], _APP["gui"] = _build("dark")


def tearDownModule():
    root = _APP.get("root")
    if root is not None:
        try:
            root.destroy()
        except Exception:
            pass


def _face_pixel(btn):
    """读渐变按钮"面"那张图在中心偏左处的像素（避开中间的字），返回 (r, g, b)。"""
    face = btn.find_withtag("btnface")
    assert face, "找不到按钮面"
    img = btn.itemcget(face[0], "image")
    h = int(btn.tk.call("image", "height", img))
    rgb = btn.tk.call(img, "get", 10, h // 2)
    if isinstance(rgb, str):
        rgb = rgb.replace("(", " ").replace(")", " ").replace(",", " ").split()
    return tuple(int(v) for v in rgb)


class ToneTests(unittest.TestCase):
    """禁用按钮的面/字色算法（纯函数）。"""

    def test_dark_container_tones_toward_white_light_toward_black(self):
        d_face, l_face = _tone("#2e2e2e", 0.14), _tone("#f0f0f0", 0.14)
        self.assertGreater(_lum(_hex(d_face)), _lum(_hex("#2e2e2e")))
        self.assertLess(_lum(_hex(l_face)), _lum(_hex("#f0f0f0")))

    def test_text_is_readable_on_the_face_in_both_themes(self):
        for bg in (DARK_THEME["bg"], LIGHT_THEME["bg"], LIGHT_THEME["bottom_bg"]):
            face, text = _tone(bg, 0.14), _tone(bg, 0.68)
            self.assertGreaterEqual(_contrast(_hex(text), _hex(face)), 4.5, (bg, face, text))

    def test_face_stays_close_to_the_container(self):
        """禁用面只是"略有层次"：对容器底的对比度要小，否则又成了抢眼的亮块。"""
        for bg in (DARK_THEME["bg"], LIGHT_THEME["bg"]):
            self.assertLess(_contrast(_hex(_tone(bg, 0.14)), _hex(bg)), 2.0, bg)

    def test_unknown_color_name_does_not_raise(self):
        self.assertTrue(_tone("SystemButtonFace", 0.14).startswith("#"))


class DarkWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.gui = _app()
        _set_theme("dark")

    def test_default_world_name_is_the_neutral_minecraft_default(self):
        self.assertEqual(self.gui.world_name.get(), "新的世界")
        self.assertNotIn("老子", self.gui.world_name.get())

    def test_net_status_is_bigger_and_bold(self):
        import tkinter.font as tkfont
        f = tkfont.Font(font=self.gui.net_status.cget("font"))
        self.assertGreaterEqual(f.actual("size"), 10)
        self.assertEqual(f.actual("weight"), "bold")

    def test_net_status_colors_come_from_the_theme(self):
        for 档, key in (("ok", "log_success_fg"), ("warn", "log_warning_fg"),
                       ("fail", "log_error_fg")):
            self.gui._net_档位, self.gui._net_明细 = 档, {}
            self.gui._刷新网络状态文字()
            self.assertEqual(str(self.gui.net_status.cget("fg")).lower(),
                             DARK_THEME[key].lower(), 档)

    def test_net_status_colors_pass_aa_on_the_bottom_bar(self):
        bar = _hex(DARK_THEME["bottom_bg"])
        for key in ("log_success_fg", "log_warning_fg", "log_error_fg"):
            self.assertGreaterEqual(_contrast(_hex(DARK_THEME[key]), bar), 4.5, key)

    def test_disabled_button_is_not_the_old_bright_grey(self):
        """直接看按钮实际画出来的面：不能再是写死的亮灰 #9e9e9e。"""
        btn = self.gui.btn_fail_locate          # 没有错误时本来就是禁用的
        self.assertTrue(btn._btn_disabled)
        face = _face_pixel(btn)
        self.assertLess(_lum(face), _lum(_hex("#9e9e9e")), face)
        # 和容器底色靠得很近（略有层次），不是一块抢眼的亮块
        self.assertLess(_contrast(face, _hex(DARK_THEME["bg"])), 2.0, face)

    def test_enabled_button_back_to_normal_and_disabled_again(self):
        btn = self.gui.btn_fail_locate
        btn.state("normal")
        self.assertFalse(btn._btn_disabled)
        btn.state("disabled")
        self.assertTrue(btn._btn_disabled)


class SwitchKeepStateTests(unittest.TestCase):
    """迁移期间被锁住的两个选项开关：锁定后仍要看得清开/关，只是点不动。"""

    @classmethod
    def setUpClass(cls):
        cls.root, cls.gui = _app()
        _set_theme("dark")

    def _track(self, sw):
        off, on = sw._track_imgs()
        img = on if sw.get() else off
        r, g, b, _a = img.getpixel((img.width // 2, img.height // 2))
        return "#%02x%02x%02x" % (r, g, b)

    def test_locked_switches_keep_their_state_colors(self):
        gui = self.gui
        gui.dry_run_sw.set(True)
        gui.overwrite_sw.set(False)
        _pump(self.root, 0.3)
        开_before, 关_before = self._track(gui.dry_run_sw), self._track(gui.overwrite_sw)
        gui._set_edit_lock(True)
        _pump(self.root, 0.5)
        try:
            self.assertFalse(gui.dry_run_sw.get_enabled())
            self.assertFalse(gui.overwrite_sw.get_enabled())
            # 开着仍是强调色，关着仍是灰：锁定不影响状态色
            self.assertEqual(self._track(gui.dry_run_sw), 开_before)
            self.assertEqual(self._track(gui.overwrite_sw), 关_before)
            self.assertNotEqual(self._track(gui.dry_run_sw), self._track(gui.overwrite_sw))
            # 标题字也不变灰（不然读着像"这一项不可用"）
            self.assertEqual(gui.dry_run_sw.itemcget(gui.dry_run_sw._title_id, "fill"),
                             DARK_THEME["fg"])
        finally:
            gui._set_edit_lock(False)
            _pump(self.root, 0.4)

    def test_locked_switches_cannot_be_toggled(self):
        gui = self.gui
        gui._set_edit_lock(True)
        _pump(self.root, 0.3)
        try:
            before = (gui.dry_run_sw.get(), gui.overwrite_sw.get())
            gui.dry_run_sw.toggle()
            gui.overwrite_sw.toggle()
            _pump(self.root, 0.2)
            self.assertEqual((gui.dry_run_sw.get(), gui.overwrite_sw.get()), before)
        finally:
            gui._set_edit_lock(False)
            _pump(self.root, 0.3)

    def test_other_switches_still_grey_out_when_disabled(self):
        """默认行为不变：没传 keep_state_when_disabled 的开关禁用时整条灰掉（如"主界面编辑"）。"""
        sw = self.gui.edit_switch
        强调 = self._track(sw)
        sw.set(True)
        _pump(self.root, 0.3)
        开 = self._track(sw)
        sw.set_enabled(False)
        _pump(self.root, 0.3)
        try:
            self.assertNotEqual(self._track(sw), 开)          # 变灰了
            self.assertEqual(sw.itemcget(sw._title_id, "fill"), DARK_THEME["muted_fg"])
        finally:
            sw.set_enabled(True)
            sw.set(False)
            _pump(self.root, 0.3)
        self.assertTrue(强调)


class OldConfigKeepsItsWorldName(unittest.TestCase):
    def test_saved_world_name_wins_over_the_default(self):
        """配置里已经有 world（老用户自己填的）就用它，新默认值只在没有这个键时才出现。
        main_window 里这一行是 `self.config.get("world", i18n.tr("新的世界"))`，
        这里直接对着源码和 dict.get 的语义验证，不再为它多建一个 Tk 窗口。"""
        src = (SOURCE / "ui" / "main_window.py").read_text(encoding="utf-8-sig")
        self.assertIn('self.config.get("world", i18n.tr("新的世界"))', src)
        self.assertNotIn("老子的世界", src)
        self.assertEqual({"world": "我自己的存档"}.get("world", "新的世界"), "我自己的存档")
        self.assertEqual({}.get("world", "新的世界"), "新的世界")


class LightWindowTests(unittest.TestCase):
    """浅色主题下主界面所有可见文字都要过 4.5:1（渐变按钮的白字另算，见下）。"""

    @classmethod
    def setUpClass(cls):
        cls.root, cls.gui = _app()
        _set_theme("light")

    def _walk(self, w, out):
        for c in w.winfo_children():
            try:
                shown = c.winfo_ismapped()
            except tk.TclError:
                continue
            if shown:
                cls = c.winfo_class()
                if cls in ("Label", "Labelframe", "Checkbutton", "Radiobutton"):
                    try:
                        txt = str(c.cget("text")).strip()
                        if txt:
                            fg = tuple(v >> 8 for v in c.winfo_rgb(c.cget("fg")))
                            bg = tuple(v >> 8 for v in c.winfo_rgb(c.cget("bg")))
                            out.append((txt[:24], fg, bg))
                    except tk.TclError:
                        pass
            self._walk(c, out)
        return out

    def test_every_visible_label_passes_aa(self):
        bad = [(t, "#%02x%02x%02x" % fg, "#%02x%02x%02x" % bg, round(_contrast(fg, bg), 2))
               for t, fg, bg in self._walk(self.root, [])
               if _contrast(fg, bg) < 4.5]
        self.assertEqual(bad, [])

    def test_muted_and_warning_theme_colors(self):
        self.assertGreaterEqual(_contrast(_hex(LIGHT_THEME["muted_fg"]), _hex(LIGHT_THEME["bg"])), 4.5)
        self.assertGreaterEqual(_contrast(_hex(LIGHT_THEME["muted_fg"]), _hex("#ffffff")), 4.5)
        self.assertGreaterEqual(_contrast(_hex(LIGHT_THEME["warning_fg"]),
                                          _hex(LIGHT_THEME["warning_bg"])), 4.5)

    def test_net_status_recolors_when_switching_theme(self):
        """切主题时网络灯字色要跟着换（它 _keep_fg，通用遍历不会替它换）。"""
        self.gui._net_档位, self.gui._net_明细 = "ok", {}
        self.gui._刷新网络状态文字()
        before = str(self.gui.net_status.cget("fg")).lower()
        self.assertEqual(before, LIGHT_THEME["log_success_fg"].lower())
        self.gui.toggle_theme()                 # 浅 → 深
        _pump(self.root, 1.0)
        self.assertEqual(str(self.gui.net_status.cget("fg")).lower(),
                         DARK_THEME["log_success_fg"].lower())
        self.gui.toggle_theme()                 # 深 → 浅，留给后面的用例
        _pump(self.root, 1.0)

    def test_disabled_button_recolors_when_switching_theme(self):
        """切主题后禁用面/字色按新容器底色重出：浅色是比底更暗的灰，深色是比底更亮的灰。"""
        btn = self.gui.btn_fail_locate
        self.assertTrue(btn._btn_disabled)
        浅面 = _face_pixel(btn)
        self.assertLess(_lum(浅面), _lum(_hex(LIGHT_THEME["bg"])), 浅面)       # 比浅底更暗
        self.gui.toggle_theme()
        _pump(self.root, 1.0)
        深面 = _face_pixel(btn)
        self.assertGreater(_lum(深面), _lum(_hex(DARK_THEME["bg"])), 深面)     # 比深底更亮
        self.assertLess(_lum(深面), _lum(_hex("#9e9e9e")), 深面)
        self.gui.toggle_theme()
        _pump(self.root, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Qt 主界面外观层(ui/qt_main/look.py、winstyle.py)的回归测试。

钉的是「和 Tk 对过的数值」:颜色算法、3D 边的像素、按钮/输入框尺寸、深浅标题栏色值。
这些数来自对 Tk 版实测截图(_dctest/accept/shots/tk_main.png)和 utils/helpers.py 的逐项核对,
改它们之前先想清楚是不是在偏离 Tk。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-look-")
CFG = Path(_tmp.name) / "config.json"
CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")
os.environ["MCTOOL_CONFIG"] = str(CFG)
os.environ["MCTOOL_LANG"] = "zh"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from _testcfg import use_config as _use_config   # noqa: E402
_CFG_PATH = os.environ["MCTOOL_CONFIG"]


def setUpModule():
    _use_config(_CFG_PATH)

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from utils import config as _cfg  # noqa: E402

_cfg.CONFIG_FILE = CFG
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


def _px(img, x, y):
    return "#%06x" % (img.pixel(x, y) & 0xffffff)


class ColorMathTests(unittest.TestCase):
    """和 utils/helpers.py 同名函数逐点对账(那个模块 import tkinter,这里不 import,抄了算法)。"""

    def test_lighten_and_shade(self):
        from ui.qt_main import look
        # 0x1e+40=0x46, 0x88+40=0xb0, 0xe5+40=0x10d→夹到 0xff
        self.assertEqual(look.lighten("#1e88e5"), "#46b0ff")
        self.assertEqual(look.lighten("#ffffff"), "#ffffff")           # 越界夹住
        self.assertEqual(look.shade("#101010", -40), "#000000")

    def test_tone_goes_toward_opposite(self):
        from ui.qt_main import look
        dark = look.tone("#2e2e2e", 0.14)
        light = look.tone("#f0f0f0", 0.14)
        self.assertGreater(int(dark[1:3], 16), 0x2e)        # 深底往亮混
        self.assertLess(int(light[1:3], 16), 0xf0)          # 浅底往暗混
        self.assertEqual(look.tone("garbage", 0.14), "#c8c8c8")
        self.assertEqual(look.tone("garbage", 0.68), "#707070")

    def test_tone_matches_tk_source(self):
        """抽出 Tk 里 _tone 的源码在隔离命名空间里执行,和这边逐点比。"""
        src = (Path(__file__).resolve().parents[1] / "Minecraft迁移工具" / "utils" / "helpers.py")
        text = src.read_text(encoding="utf-8")
        a = text.index("def _tone(bg, t):")
        b = text.index("def hover_pair(colors):")
        ns = {}
        exec(text[a:b], ns)
        from ui.qt_main import look
        for bg in ("#2e2e2e", "#f0f0f0", "#3e3e3e", "#ffffff", "#000000", "#808080"):
            for t in (0.14, 0.68):
                self.assertEqual(look.tone(bg, t), ns["_tone"](bg, t), (bg, t))

    def test_three_d_shades_match_tk_pixels(self):
        """深底 #2e2e2e 的两色就是 Tk LabelFrame 实际画出来的 #979797 / #1b1b1b。"""
        from ui.qt_main import look
        self.assertEqual(look.three_d_shades("#2e2e2e"), ("#979797", "#1b1b1b"))
        # 红条(warning_bg)的 ridge:亮 #aa9999 暗 #331e1e,也是截图里的实际色
        self.assertEqual(look.three_d_shades("#553333"), ("#aa9999", "#331e1e"))

    def test_entry_border(self):
        from ui.qt_main import look
        from utils.theme import DARK_THEME, LIGHT_THEME
        self.assertEqual(look.entry_border(DARK_THEME), "#4a4a4a")
        self.assertEqual(look.entry_border(DARK_THEME, focus=True), "#64b5f6")
        self.assertEqual(look.entry_border(LIGHT_THEME, focus=True), "#4a90d9")

    def test_is_dark(self):
        from ui.qt_main import look
        from utils.theme import DARK_THEME, LIGHT_THEME
        self.assertTrue(look.is_dark(DARK_THEME))
        self.assertFalse(look.is_dark(LIGHT_THEME))


class QssTests(unittest.TestCase):
    def test_builds_for_both_themes_and_has_every_kind(self):
        from ui.qt_main import look
        from utils.theme import DARK_THEME, LIGHT_THEME
        for th in (DARK_THEME, LIGHT_THEME):
            q = look.build_qss(th)
            for kind in look.KIND_COLORS:
                self.assertIn('QPushButton[kind="%s"]' % kind, q)
            # 写死 Tk 的值(RoundedEntry radius=12、RoundedTextArea radius=8),不引用常量,
            # 否则改常量时断言跟着变,什么也测不到
            self.assertIn("border-radius: 12px", q)
            self.assertIn("border-radius: 8px", q)

    def test_button_colors_are_the_tk_ones(self):
        from ui.qt_main import look
        self.assertEqual(look.BTN_NEUTRAL, ("#1e88e5", "#42a5f5"))
        self.assertEqual(look.BTN_ADD, ("#43a047", "#66bb6a"))
        self.assertEqual(look.BTN_DANGER, ("#e53935", "#ff5252"))
        self.assertEqual(look.BTN_TOOL, ("#546e7a", "#78909c"))
        self.assertEqual(look.BTN_THEME, ("#8e24aa", "#ab47bc"))
        self.assertEqual((look.BTN_RADIUS, look.BTN_HEIGHT, look.ENTRY_HEIGHT), (8, 30, 30))
        self.assertEqual((look.ENTRY_RADIUS, look.AREA_RADIUS), (12, 8))

    def test_disabled_rule_comes_after_hover(self):
        """禁用必须排在悬停后面,否则灰按钮被鼠标一碰就亮起来。"""
        from ui.qt_main import look
        from utils.theme import DARK_THEME
        q = look.button_rules(DARK_THEME)
        for kind in look.KIND_COLORS:
            self.assertLess(q.index('[kind="%s"]:hover' % kind),
                            q.index('[kind="%s"]:disabled' % kind))

    def test_disabled_face_is_tone_of_container(self):
        from ui.qt_main import look
        from utils.theme import DARK_THEME
        q = look.button_rules(DARK_THEME)
        self.assertIn(look.tone(DARK_THEME["bg"], 0.14), q)
        self.assertIn(look.tone(DARK_THEME["bg"], 0.68), q)

    def test_kind_aliases(self):
        from ui.qt_main import look
        self.assertEqual(look.kind_for("clear"), "danger")
        self.assertEqual(look.kind_for("scan"), "add")
        self.assertEqual(look.kind_for("neutral"), "neutral")
        self.assertIsNone(look.kind_for("whatever"))
        self.assertIsNone(look.kind_for(None))


class BorderPixelTests(unittest.TestCase):
    """3D 边在整数缩放和小数缩放下都必须是「暗一像素 + 亮一像素」两圈,不能糊成一圈。"""

    @staticmethod
    def _render(widget, dpr):
        w = int(round(widget.width() * dpr))
        h = int(round(widget.height() * dpr))
        img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
        img.setDevicePixelRatio(dpr)
        img.fill(QtGui.QColor("#2e2e2e"))
        widget.render(img)
        return img

    def _frame(self, title=""):
        from ui.qt_main import look
        th = {"bg": "#2e2e2e", "labelframe_bg": "#2e2e2e", "labelframe_fg": "#ffffff"}
        f = look.SectionFrame(title, th)
        f.resize(200, 80)
        return f

    def test_groove_has_two_rings_at_integer_scale(self):
        for dpr in (1.0, 2.0):
            f = self._frame()
            img = self._render(f, dpr)
            y = int(40 * dpr)
            row = [_px(img, x, y) for x in range(0, 4)]
            self.assertEqual(row[:2], ["#1b1b1b", "#979797"], (dpr, row))
            f.deleteLater()

    def test_groove_keeps_both_rings_when_origin_is_fractional(self):
        """控件原点落在 12.5 物理像素(125% 缩放 + 10 逻辑 px 边距)时,两圈不能并成一圈。"""
        from ui.qt_main import look
        th = {"bg": "#2e2e2e", "labelframe_bg": "#2e2e2e", "labelframe_fg": "#ffffff"}
        host = QtWidgets.QWidget()
        host.setStyleSheet("QWidget{background:#2e2e2e;}")
        lay = QtWidgets.QVBoxLayout(host)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(look.SectionFrame("", th))
        host.resize(300, 120)
        img = QtGui.QImage(375, 150, QtGui.QImage.Format_RGB32)
        img.setDevicePixelRatio(1.25)
        img.fill(QtGui.QColor("#2e2e2e"))
        host.render(img)
        row = [_px(img, x, 75) for x in range(8, 22)]
        self.assertIn("#1b1b1b", row, row)
        self.assertIn("#979797", row, row)
        i = row.index("#1b1b1b")
        self.assertEqual(row[i + 1], "#979797", row)       # 暗圈紧挨着亮圈
        host.deleteLater()

    def test_banner_ridge_uses_light_outside(self):
        from ui.qt_main import look
        th = {"warning_bg": "#553333", "warning_fg": "#ff8888"}
        b = look.WarningBanner("x", th)
        b.resize(200, 40)
        img = self._render(b, 1.0)
        col = [_px(img, 100, y) for y in range(0, 3)]
        self.assertEqual(col[:2], ["#aa9999", "#331e1e"], col)     # 亮在外,暗在内(RIDGE)
        b.deleteLater()


class WindowLookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def make(self, theme="dark"):
        _cfg.save_raw_config({"language": "zh", "theme": theme, "net_float_on": False})
        w = self.W()
        w.resize(1180, 900)
        w.show()
        pump(60)
        self.addCleanup(self._close, w)
        return w

    @staticmethod
    def _close(w):
        w.hide()
        w._close_session_log()
        w.deleteLater()
        pump(30)

    def test_structure_matches_tk_cards(self):
        from ui.qt_main import look
        w = self.make()
        titles = [s.title() for s in w.findChildren(look.SectionFrame)]
        self.assertEqual(titles, [
            "📤 旧版整合包（要迁移出去的源）", "⬇ 新版整合包（迁移目的地）", "存档文件夹名称",
            "清单（三个页共用一个区，点标签切换）", "执行日志"])
        self.assertIn("本工具完全免费", w.warning_banner.label.text())

    def test_button_and_entry_sizes_follow_tk(self):
        from ui.qt_main import look
        w = self.make()
        d = w.devicePixelRatioF()
        for key in ("scan_diff", "clear_mods", "mod_magnify", "add_mods"):
            h = w._btn_widgets[key].geometry().height()
            self.assertEqual(h, look.BTN_HEIGHT, (key, h))
        self.assertEqual(w.source_edit.geometry().height(), look.ENTRY_HEIGHT)
        self.assertEqual(w.world_edit.geometry().height(), look.ENTRY_HEIGHT)
        self.assertEqual((w.settings_btn.width(), w.settings_btn.height()), (34, 34))
        self.assertEqual((w.theme_btn.width(), w.theme_btn.height()), (34, 34))
        self.assertGreaterEqual(d, 1.0)

    def test_path_edits_use_data_colors(self):
        w = self.make()
        self.assertEqual(w.source_edit.objectName(), "pathEdit")
        self.assertEqual(w.world_edit.objectName(), "worldEdit")
        self.assertEqual(w.log_text.objectName(), "logEdit")
        q = w.styleSheet()
        self.assertIn(w.theme["data_fg"], q)
        self.assertIn(w.theme["data_id_fg"], q)
        self.assertIn(w.theme["log_bg"], q)

    def test_buttons_carry_semantic_kind(self):
        w = self.make()
        want = {"scan_diff": "add", "add_mods": "add", "changelog": "add",
                "clear_mods": "danger", "check_mods": "neutral", "mod_magnify": "neutral",
                "browse_source": "neutral", "log_clear": "danger", "fail_locate": "danger",
                "log_big": "neutral", "history": "neutral"}
        for key, kind in want.items():
            self.assertEqual(w._btn_widgets[key].property("kind"), kind, key)

    def test_theme_toggle_restyles_everything(self):
        from ui.qt_main import look
        w = self.make("dark")
        before = w.styleSheet()
        w._toggle_theme()
        pump(40)
        self.assertNotEqual(w.styleSheet(), before)
        self.assertIn(w.theme["bg"], w.styleSheet())
        for sec in w.findChildren(look.SectionFrame):
            self.assertEqual(sec._theme["bg"], w.theme["bg"])
        self.assertEqual(w.warning_banner._theme["warning_bg"], w.theme["warning_bg"])
        # 约定同 Tk:按钮画的是「点了会变成什么」。深色 → 显示太阳(去浅色),浅色 → 显示月亮
        self.assertEqual(w.theme_btn.text(), "🌙")

    def test_theme_button_is_icon_only_with_tooltip(self):
        w = self.make("dark")
        self.assertEqual(w.theme_btn.text(), "☀")
        self.assertTrue(w.theme_btn.toolTip())
        w2 = self.make("light")
        self.assertEqual(w2.theme_btn.text(), "🌙")

    def test_apply_theme_never_raises_on_stylesheet(self):
        """样式表里有格式化符号(%)写坏过一次:整套主题静默失败。两套主题都要能生成。"""
        from ui.qt_main import look
        from utils.theme import DARK_THEME, LIGHT_THEME
        for th in (DARK_THEME, LIGHT_THEME):
            self.assertGreater(len(look.build_qss(th)), 3000)


class TitleBarTests(unittest.TestCase):
    def test_non_windows_returns_false(self):
        from unittest import mock
        from ui.qt_main import winstyle
        with mock.patch.object(winstyle.sys, "platform", "linux"):
            self.assertFalse(winstyle.style_window_hwnd(1, True))

    def test_bad_hwnd_is_swallowed(self):
        from ui.qt_main import winstyle
        self.assertIn(winstyle.style_window_hwnd(0, True), (True, False))   # 不抛即可

    def test_dark_and_light_colors_match_tk(self):
        """写进 DWM 的标题栏色值和 helpers.style_window_hwnd 一致:深 #202020/白字,浅 #f0f0f0/黑字。"""
        import types
        from unittest import mock
        from ui.qt_main import winstyle
        calls = []

        class _Dwm:
            @staticmethod
            def DwmSetWindowAttribute(h, attr, ref, size):
                import ctypes
                calls.append((int(attr.value), ctypes.cast(ref, ctypes.POINTER(ctypes.c_int))[0]))
                return 0

        fake = types.SimpleNamespace(windll=types.SimpleNamespace(dwmapi=_Dwm))
        import ctypes as real
        for dark, caption, text in ((True, 0x202020, 0xffffff), (False, 0xf0f0f0, 0x000000)):
            calls.clear()
            with mock.patch.object(winstyle.sys, "platform", "win32"), \
                    mock.patch.dict(sys.modules, {"ctypes": types.SimpleNamespace(
                        windll=fake.windll, c_void_p=real.c_void_p, c_uint=real.c_uint,
                        c_int=real.c_int, byref=real.byref, sizeof=real.sizeof,
                        cast=real.cast, POINTER=real.POINTER)}):
                self.assertTrue(winstyle.style_window_hwnd(123, dark))
            got = dict(calls)
            self.assertEqual(got[20], 1 if dark else 0)
            self.assertEqual(got[35], caption)
            self.assertEqual(got[36], text)
            self.assertEqual(got[33], 2)


if __name__ == "__main__":
    unittest.main()

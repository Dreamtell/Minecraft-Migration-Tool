"""Qt pack-update warning bar: expand/collapse, persistence, prompt text, dialog, theme, busy lock."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-packwarn-")
CFG = Path(_tmp.name) / "config.json"
CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")
os.environ["MCTOOL_CONFIG"] = str(CFG)
os.environ["MCTOOL_LANG"] = "zh"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from _testcfg import use_config as _use_config   # noqa: E402
_CFG_PATH = os.environ["MCTOOL_CONFIG"]


def setUpModule():                     # 全量跑时各模块各用各的临时配置
    _use_config(_CFG_PATH)

from PySide6 import QtCore, QtWidgets  # noqa: E402
from utils import config as _cfg  # noqa: E402

_cfg.CONFIG_FILE = CFG
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


class PackWarningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        self.w = self.W()
        self.w.show()
        pump(30)

    def tearDown(self):
        self.w._migration_running = False
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)

    def test_expand_collapse_swaps_bar_and_pill_and_persists(self):
        w = self.w
        bar, pill = w.pack_warning_bar, w.pack_pill
        bar.set_collapsed(True)
        self.assertFalse(bar.isVisible())
        self.assertTrue(pill.isVisible())
        self.assertTrue(w.config["pack_warning_collapsed"])
        self.assertTrue(_cfg.load_raw_config()["pack_warning_collapsed"])
        bar.set_collapsed(False)
        self.assertTrue(bar.isVisible())
        self.assertFalse(pill.isVisible())
        self.assertFalse(_cfg.load_raw_config()["pack_warning_collapsed"])
        w.save_config()                       # 整体保存不能把这个键抹掉
        self.assertIn("pack_warning_collapsed", _cfg.load_raw_config())

    def test_new_user_defaults_to_collapsed_like_tk(self):
        raw = _cfg.load_raw_config()
        raw.pop("pack_warning_collapsed", None)
        CFG.write_text(json.dumps(raw), encoding="utf-8")
        w2 = self.W()
        try:
            self.assertTrue(w2.pack_warning_bar.is_collapsed())
        finally:
            w2._close_session_log()
            w2.deleteLater()

    def test_copy_ai_prompt_uses_current_paths_and_flashes(self):
        w = self.w
        w.source_path = r"C:\packs\Old Pack 1.0"
        w.target_path = r"C:\packs\New Pack 2.0"
        w.pack_warning_bar.copy_prompt()
        text = QtWidgets.QApplication.clipboard().text()
        self.assertIn("整合包：Old Pack 1.0", text)
        self.assertIn(r"旧实例目录：C:\packs\Old Pack 1.0", text)
        self.assertIn(r"新实例目录：C:\packs\New Pack 2.0", text)
        self.assertEqual(w.pack_warning_bar._copy_label.text(), "已复制 ✓")
        pump(1700)
        self.assertEqual(w.pack_warning_bar._copy_label.text(), "复制 AI 提示词")

    def test_prompt_without_paths_says_not_selected(self):
        from ui.qt_main.pack_warning import build_prompt
        text = build_prompt("", "")
        self.assertIn("整合包：整合包", text)
        self.assertEqual(text.count("（未选择）"), 2)

    def test_dialog_offers_expand_only_when_collapsed(self):
        bar = self.w.pack_warning_bar
        bar.set_collapsed(True)
        bar.open_dialog()
        texts = [b.text() for b in bar._dialog.findChildren(QtWidgets.QPushButton)]
        self.assertIn("展开成横幅", texts)
        bar._dialog.close()
        pump(30)
        bar.set_collapsed(False)
        bar.open_dialog()
        texts = [b.text() for b in bar._dialog.findChildren(QtWidgets.QPushButton)]
        self.assertNotIn("展开成横幅", texts)
        self.assertIn("复制 AI 提示词", texts)
        bar._dialog.close()
        pump(30)

    def test_bar_follows_theme(self):
        from ui.qt_main.pack_warning import palette
        bar = self.w.pack_warning_bar
        bar.set_dark(False)
        self.assertIn(palette(False)["bg"], bar.styleSheet())
        bar.set_dark(True)
        self.assertIn(palette(True)["bg"], bar.styleSheet())


class BusyLockTests(unittest.TestCase):
    """迁移 / 扫描 / 倒计时 / 历史窗口里的回滚期间,按钮置灰、输入只读(Tk 的 _refresh_busy_state)。"""

    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        self.w = self.W()
        self.w.show()
        pump(30)

    def tearDown(self):
        self.w._migration_running = False
        self.w._scanning = False
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)

    def _locked(self):
        w = self.w
        return ([w._btn_widgets[k] for k in w._BUSY_LOCKED_KEYS]
                + [w.settings_btn, w.theme_btn, w.buttons_btn, w.src_browse_btn,
                   w.tgt_browse_btn] + list(w._path_btns.values()))

    def _inputs(self):
        """路径 / 存档名:闲时可改,忙时只读。"""
        w = self.w
        return (w.source_edit, w.target_edit, w.world_edit)

    def _lists(self):
        """三个清单:闲时是否可改取决于「主界面编辑」开关(默认关 = 只读),忙时一律只读。"""
        w = self.w
        return (w.mod_text, w.config_text, w.extra_text)

    def test_scan_locks_and_unlocks(self):
        w = self.w
        self.assertTrue(all(x.isEnabled() for x in self._locked()))
        w._scanning = True
        w._refresh_busy_state()
        self.assertFalse(any(x.isEnabled() for x in self._locked()))
        self.assertTrue(all(e.isReadOnly() for e in self._inputs() + self._lists()))
        self.assertFalse(w.start_btn.isEnabled())
        w._scanning = False
        w._refresh_busy_state()
        self.assertTrue(all(x.isEnabled() for x in self._locked()))
        self.assertFalse(any(e.isReadOnly() for e in self._inputs()))
        self.assertTrue(all(e.isReadOnly() for e in self._lists()))   # 编辑开关没开:清单仍只读
        self.assertTrue(w.start_btn.isEnabled())

    def test_countdown_locks_but_start_button_stays_clickable(self):
        w = self.w
        w._begin_countdown(5, {})
        try:
            self.assertFalse(any(x.isEnabled() for x in self._locked()))
            self.assertTrue(w.start_btn.isEnabled())        # 再点一下 = 取消
            self.assertTrue(w.source_edit.isReadOnly())
        finally:
            w._cancel_countdown()
        self.assertTrue(all(x.isEnabled() for x in self._locked()))
        self.assertFalse(w.source_edit.isReadOnly())

    def test_prepare_and_unlock_toggle_the_lock(self):
        w = self.w
        with mock.patch("ui.qt_main.migration.PrepareWorker", return_value=mock.MagicMock()), \
                mock.patch.object(QtCore.QThread, "start"):
            w._begin_prepare({"cancel_event": threading.Event()})
        self.assertTrue(w.source_edit.isReadOnly())
        self.assertFalse(w._btn_widgets["clear_mods"].isEnabled())
        w._unlock_after_prepare()
        self.assertFalse(w.source_edit.isReadOnly())
        self.assertTrue(w._btn_widgets["clear_mods"].isEnabled())

    def test_rollback_in_history_window_counts_as_busy(self):
        w = self.w
        w._rollback_busy = lambda: True
        w._refresh_busy_state()
        self.assertFalse(w.settings_btn.isEnabled())
        self.assertEqual(w.busy_task_name(), "回滚")
        w._rollback_busy = None
        w._refresh_busy_state()
        self.assertTrue(w.settings_btn.isEnabled())

    def test_fail_locate_button_follows_failed_list_not_busy(self):
        w = self.w
        w._failed_items = [(0, "x.jar")]
        w._refresh_busy_state()
        self.assertTrue(w.fail_btn.isEnabled())
        w._failed_items = []
        w._refresh_busy_state()
        self.assertFalse(w.fail_btn.isEnabled())

    def test_main_window_has_no_rollback_button_any_more(self):
        # Tk 的 cafd9e8 把回滚并进了历史窗口;Qt 版不该在主界面再放一颗
        from ui.qt_main.buttons import DEFAULT_ORDER, LABELS
        self.assertFalse(hasattr(self.w, "rollback_btn"))
        self.assertNotIn("rollback", self.w._btn_widgets)
        self.assertNotIn("rollback", LABELS)
        self.assertEqual(DEFAULT_ORDER["action"], ["history", "start"])


class SmallSettingsTests(unittest.TestCase):
    """浮标阴影 / 点消息中心外面就收起:与 Tk 版同键,设置窗里能改,改了立刻生效。"""

    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        self.w = self.W()
        self.w.show()
        pump(30)

    def tearDown(self):
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)

    def test_settings_dialog_exposes_and_returns_the_two_keys(self):
        from ui.qt_main.settings import SettingsDialog
        self.w.config.update({"net_float_shadow": False, "msg_click_outside_close": True})
        d = SettingsDialog(self.w, self.w.config)
        v = d.values()
        self.assertFalse(v["net_float_shadow"])
        self.assertTrue(v["msg_click_outside_close"])
        d.float_shadow_check.setChecked(True)
        d.msg_outside_check.setChecked(False)
        v = d.values()
        self.assertTrue(v["net_float_shadow"])
        self.assertFalse(v["msg_click_outside_close"])

    def test_apply_settings_persists_both_keys(self):
        self.w._apply_settings({"theme": "dark", "net_float_shadow": False,
                                "msg_click_outside_close": True})
        saved = _cfg.load_raw_config()
        self.assertFalse(saved["net_float_shadow"])
        self.assertTrue(saved["msg_click_outside_close"])

    def test_float_shadow_switch_changes_the_painted_pixels(self):
        from ui.qt_main.net_float import NetFloat
        f = NetFloat(self.w, size=56, rel=(100, 100), shadow=True)
        try:
            f.show()
            pump(30)
            below = f.width() // 2, f._circle_rect().bottom().__int__() + 1
            with_shadow = f.grab().toImage().pixelColor(*below).alpha()
            f.set_shadow(False)
            without = f.grab().toImage().pixelColor(*below).alpha()
            self.assertGreater(with_shadow, 0)         # 圆下沿外 1px 有阴影
            self.assertEqual(without, 0)               # 关掉后是全透明
        finally:
            f.close()

    def _open_dock(self, outside):
        w = self.w
        w.config["msg_click_outside_close"] = outside
        w.toggle_message_center()
        pump(30)
        self.assertTrue(w._msg_dock.isVisible())
        return w._msg_dock

    def _click(self, widget):
        from PySide6 import QtGui
        ev = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(3, 3),
                               QtCore.QPointF(3, 3), QtCore.Qt.LeftButton,
                               QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(widget, ev)

    def test_click_outside_closes_dock_only_when_enabled(self):
        w = self.w
        dock = self._open_dock(False)
        self._click(w.mod_text)
        self.assertTrue(dock.isVisible())                 # 默认关:点外面不动
        dock.set_close_on_outside_click(True)
        self._click(w.mod_text)
        self.assertFalse(dock.isVisible())                # 开了:点主窗口内容就收

    def test_click_inside_dock_or_on_bell_does_not_close_it(self):
        w = self.w
        dock = self._open_dock(True)
        self._click(dock.list)
        self.assertTrue(dock.isVisible())                 # 点面板自己不收
        self._click(w.msg_bell)
        self.assertTrue(dock.isVisible())                 # 点铃铛交给铃铛自己的开关

    def test_switching_off_removes_the_app_wide_filter(self):
        dock = self._open_dock(True)
        dock.set_close_on_outside_click(False)
        self._click(self.w.mod_text)
        self.assertTrue(dock.isVisible())

if __name__ == "__main__":
    unittest.main()

"""Qt main window: edit-mode switch, mirror-window lock, blank-click blur, sound effects, key preservation."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-edit-")
CFG = Path(_tmp.name) / "config.json"
os.environ["MCTOOL_CONFIG"] = str(CFG)
os.environ["MCTOOL_LANG"] = "zh"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from utils import config as _cfg  # noqa: E402

_cfg.CONFIG_FILE = CFG
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


def write_cfg(**extra):
    base = {"language": "zh", "theme": "dark", "net_float_on": False}
    base.update(extra)
    CFG.write_text(json.dumps(base), encoding="utf-8")


def press(widget):
    ev = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(2, 2),
                           QtCore.QPointF(2, 2), QtCore.Qt.LeftButton,
                           QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(widget, ev)
    pump(10)          # 两次点击之间事件循环一定会转一圈(窗口的「同一次点击」去重靠它)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def make(self, **cfg):
        write_cfg(**cfg)
        w = self.W()
        w.show()
        pump(30)
        self.addCleanup(self._dispose, w)
        return w

    @staticmethod
    def _dispose(w):
        w._migration_running = False
        w._scanning = False
        APP.removeEventFilter(w)
        w.hide()
        w._close_session_log()
        w.deleteLater()
        pump(30)


class EditModeTests(Base):
    def test_lists_are_read_only_until_the_switch_is_on(self):
        w = self.make()
        boxes = (w.mod_text, w.config_text, w.extra_text)
        self.assertTrue(all(b.isReadOnly() for b in boxes))
        w.edit_switch.setChecked(True)
        self.assertFalse(any(b.isReadOnly() for b in boxes))
        w.edit_switch.setChecked(False)
        self.assertTrue(all(b.isReadOnly() for b in boxes))

    def test_switch_state_persists_and_is_restored(self):
        w = self.make()
        w.edit_switch.setChecked(True)
        self.assertTrue(_cfg.load_raw_config()["edit_enabled"])
        w.save_config()                                     # 整体保存不能把它抹掉
        self.assertTrue(_cfg.load_raw_config()["edit_enabled"])
        w2 = self.make(edit_enabled=True)
        self.assertTrue(w2.edit_switch.isChecked())
        self.assertFalse(w2.mod_text.isReadOnly())

    def test_busy_makes_lists_read_only_even_when_editing_is_on(self):
        w = self.make(edit_enabled=True)
        self.assertFalse(w.mod_text.isReadOnly())
        w._scanning = True
        w._refresh_busy_state()
        self.assertTrue(w.mod_text.isReadOnly())
        w._scanning = False
        w._refresh_busy_state()
        self.assertFalse(w.mod_text.isReadOnly())           # 闲下来恢复成用户的选择

    def test_busy_does_not_switch_editing_on_by_itself(self):
        w = self.make()                                      # 开关是关的
        w._scanning = True
        w._refresh_busy_state()
        w._scanning = False
        w._refresh_busy_state()
        self.assertTrue(w.mod_text.isReadOnly())             # 解除忙态不能把只读放开

    def test_programmatic_edits_still_work_while_read_only(self):
        # 添加 / 清空 / 导入 / 放大查看写回都是程序改清单,不受「只读」影响
        w = self.make()
        w._replace_text(w.mod_text, "a.jar\nb.jar")
        self.assertEqual(w.mod_text.toPlainText(), "a.jar\nb.jar")
        w._clear_list(w.mod_text)
        self.assertEqual(w.mod_text.toPlainText(), "")


class MirrorLockTests(Base):
    def test_lock_turns_editing_off_and_restores_the_users_choice(self):
        w = self.make(edit_enabled=True)
        w._lock_edit("bigview")
        self.assertTrue(w.mod_text.isReadOnly())
        self.assertFalse(w.edit_switch.isEnabled())          # 开关灰掉点不动
        self.assertIn("放大查看", w.edit_desc.text())
        self.assertTrue(w._edit_enabled_for_config())        # 存盘存的是意图,不是被锁成的 False
        w.save_config()
        self.assertTrue(_cfg.load_raw_config()["edit_enabled"])
        w._unlock_edit("bigview")
        self.assertTrue(w.edit_switch.isEnabled())
        self.assertTrue(w.edit_switch.isChecked())           # 还回「开」
        self.assertFalse(w.mod_text.isReadOnly())
        self.assertEqual(w.edit_desc.text(), "直接改动清单文字 · 谨慎使用")

    def test_lock_when_editing_was_off_stays_off_after_unlock(self):
        w = self.make()
        w._lock_edit("diff")
        w._unlock_edit("diff")
        self.assertFalse(w.edit_switch.isChecked())
        self.assertTrue(w.mod_text.isReadOnly())

    def test_two_mirror_windows_unlock_only_when_both_are_gone(self):
        w = self.make(edit_enabled=True)
        w._lock_edit("bigview")
        w._lock_edit("diff")
        w._unlock_edit("bigview")
        self.assertTrue(w.mod_text.isReadOnly())             # 差异窗口还开着
        self.assertFalse(w.edit_switch.isEnabled())
        w._unlock_edit("diff")
        self.assertFalse(w.mod_text.isReadOnly())

    def test_unlock_is_idempotent_and_unknown_kind_is_ignored(self):
        w = self.make(edit_enabled=True)
        w._lock_edit("nonsense")
        self.assertFalse(w._edit_locked())
        w._lock_edit("diff")
        w._unlock_edit("diff")
        w._unlock_edit("diff")                               # 第二次什么都不做
        self.assertTrue(w.edit_switch.isChecked())

    def test_lock_tightens_when_diff_window_is_replaced(self):
        # 重新扫描时旧窗口的 destroyed 晚于新窗口创建:不能把新窗口的锁解掉
        w = self.make(edit_enabled=True)
        w._lock_edit("diff")
        old_token = object()
        w._diff_view_token = object()                        # 当前窗口的 token 已经换了
        w._on_diff_view_destroyed(old_token)                 # 旧窗口迟到的信号
        self.assertTrue(w._edit_locked())
        w._on_diff_view_destroyed(w._diff_view_token)
        self.assertFalse(w._edit_locked())

    def test_lock_covers_the_real_big_view_open_and_close(self):
        w = self.make(edit_enabled=True)
        w.source_path = _tmp.name
        w.mod_text.setReadOnly(False)
        w._replace_text(w.mod_text, "a.jar")
        w._open_big_view("mod")
        pump(60)
        try:
            self.assertTrue(w._edit_locked())
            self.assertTrue(w.mod_text.isReadOnly())
        finally:
            for v in list(w._big_views.values()):
                v.close()
            pump(200)
        self.assertFalse(w._edit_locked())
        self.assertTrue(w.edit_switch.isChecked())


class BlankClickTests(Base):
    def _focus_list(self, w):
        w.tabs.setCurrentIndex(0)
        w.mod_text.setFocus()
        pump(30)

    def test_click_on_blank_area_blurs_lists_but_keeps_the_switch_on(self):
        w = self.make(edit_enabled=True)
        w.mod_text.setPlainText("abc")
        cur = w.mod_text.textCursor()
        cur.select(QtGui.QTextCursor.Document)
        w.mod_text.setTextCursor(cur)
        self.assertTrue(w.mod_text.textCursor().hasSelection())
        press(w.centralWidget())
        self.assertFalse(w.mod_text.textCursor().hasSelection())    # 选区清了
        self.assertTrue(w.edit_switch.isChecked())                   # 开关没被顺手关掉
        self.assertFalse(w.mod_text.isReadOnly())

    def test_click_inside_a_list_does_not_blur_it(self):
        w = self.make(edit_enabled=True)
        w.mod_text.setPlainText("abc")
        cur = w.mod_text.textCursor()
        cur.select(QtGui.QTextCursor.Document)
        w.mod_text.setTextCursor(cur)
        press(w.mod_text)
        self.assertTrue(w.mod_text.textCursor().hasSelection())

    def test_click_on_buttons_and_inputs_is_not_blank(self):
        w = self.make(edit_enabled=True)
        for target in (w.history_btn, w.source_edit, w.tabs.tabBar()):
            self.assertFalse(w._is_blank_click_target(target), type(target).__name__)
        self.assertTrue(w._is_blank_click_target(w.centralWidget()))

    def test_setting_off_makes_blank_clicks_do_nothing(self):
        w = self.make(edit_enabled=True, blank_exit_edit=False)
        w.mod_text.setPlainText("abc")
        cur = w.mod_text.textCursor()
        cur.select(QtGui.QTextCursor.Document)
        w.mod_text.setTextCursor(cur)
        press(w.centralWidget())
        self.assertTrue(w.mod_text.textCursor().hasSelection())

    def test_switch_off_does_not_react_to_blank_clicks(self):
        w = self.make()
        with mock.patch.object(w, "_blur_list_texts") as blur:
            press(w.centralWidget())
        blur.assert_not_called()


class SettingsAndKeysTests(Base):
    def test_settings_dialog_returns_blank_exit_and_sfx(self):
        from ui.qt_main.settings import SettingsDialog
        w = self.make(blank_exit_edit=False, sfx_on=True)
        d = SettingsDialog(w, w.config)
        v = d.values()
        self.assertFalse(v["blank_exit_edit"])
        self.assertTrue(v["sfx_on"])
        d.blank_exit_check.setChecked(True)
        d.sfx_check.setChecked(False)
        v = d.values()
        self.assertTrue(v["blank_exit_edit"])
        self.assertFalse(v["sfx_on"])

    def test_apply_settings_updates_and_persists_both(self):
        w = self.make()
        with mock.patch("utils.sfx.设置") as sfx_set, mock.patch("utils.sfx.预热"), \
                mock.patch("utils.sfx.可用", return_value=True):
            w._apply_settings({"theme": "dark", "blank_exit_edit": False, "sfx_on": True})
        self.assertFalse(w.blank_exit_edit)
        saved = _cfg.load_raw_config()
        self.assertFalse(saved["blank_exit_edit"])
        self.assertTrue(saved["sfx_on"])
        self.assertTrue(any(c.kwargs.get("enabled") is True for c in sfx_set.call_args_list))

    def test_tk_only_keys_survive_a_qt_save(self):
        # 这些键 Qt 版不用,但两个界面共用同一份配置,保存时不能抹掉
        keep = {"double_click_sec": 0.37, "double_click_auto": True, "scroll_speed": 5,
                "lock_mode": "real", "lock_wait_key": False, "sfx_hover": "H.wav",
                "sfx_press": "P.wav"}
        w = self.make(**keep)
        w._apply_settings({"theme": "dark"})
        w.save_config()
        saved = _cfg.load_raw_config()
        for k, v in keep.items():
            self.assertEqual(saved.get(k), v, k)


class SfxTests(Base):
    def test_startup_configures_sfx_module_from_config(self):
        with mock.patch("utils.sfx.设置") as sfx_set:
            self.make(sfx_on=True, sfx_hover="H.wav", sfx_press="P.wav")
        sfx_set.assert_any_call(enabled=True, hover="H.wav", press="P.wav")

    def test_clicking_an_enabled_button_plays_press(self):
        w = self.make()
        with mock.patch("utils.sfx.play") as play:
            press(w.history_btn)
        play.assert_called_once_with("press")

    def test_disabled_buttons_and_checkboxes_are_silent(self):
        w = self.make()
        w.history_btn.setEnabled(False)
        with mock.patch("utils.sfx.play") as play:
            press(w.history_btn)
            press(w.dry_run_check)
            press(w.mod_text)
        play.assert_not_called()

    def test_flip_start_button_also_clicks(self):
        w = self.make()
        with mock.patch("utils.sfx.play") as play:
            press(w.start_btn)
        play.assert_called_once_with("press")

    def test_sfx_module_itself_is_silent_when_off(self):
        from utils import sfx
        sfx.设置(enabled=False)
        self.assertFalse(sfx.可用())
        sfx.play("press")                                    # 关着时只是 return,不抛、不开线程

    def test_sfx_module_has_no_tkinter_dependency(self):
        # Qt 主进程不能碰 Tk:utils.sfx 必须保持纯 ctypes + 线程
        src = (Path(__file__).resolve().parents[1] / "Minecraft迁移工具" / "utils" / "sfx.py").read_text(encoding="utf-8")
        self.assertNotIn("tkinter", src)

    def test_test_button_warns_when_files_are_missing(self):
        from ui.qt_main.settings import SettingsDialog
        w = self.make()
        d = SettingsDialog(w, w.config)
        d._sfx_hover = d._sfx_press = r"Z:\no\such.wav"
        with mock.patch("utils.sfx.可用", return_value=False), \
                mock.patch("utils.sfx.预热"), \
                mock.patch.object(QtWidgets.QMessageBox, "warning") as warn:
            d._test_sfx()
        warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()

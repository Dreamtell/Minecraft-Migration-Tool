"""Qt main window parity with Tk-side settings: marker, silent run, rollback scope, finish, foreground."""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-parity-")
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


class ProgressFinishTests(unittest.TestCase):
    def test_finish_pushes_to_100_before_closing(self):
        from ui.qt_main.migration import ProgressDialog
        dlg = ProgressDialog(None, 6, 600, {})
        dlg.update_progress(4, "a.jar", 10.0, "copy")
        self.assertEqual(dlg.bar.value(), 66)
        closed = []
        t0 = time.perf_counter()
        dlg.finish(on_done=lambda: closed.append(time.perf_counter() - t0))
        self.assertEqual(dlg.bar.value(), 100)               # 立刻满
        self.assertIn("6 / 6", dlg.stats_label.text())
        self.assertFalse(dlg.cancel_btn.isEnabled())
        self.assertEqual(closed, [])                          # 还没关
        pump(ProgressDialog.FINISH_HOLD_MS + 120)
        self.assertEqual(len(closed), 1)
        self.assertGreaterEqual(closed[0], ProgressDialog.FINISH_HOLD_MS / 1000.0 - 0.02)


class RollbackScopeTests(unittest.TestCase):
    def test_picker_uses_saved_default_and_remember_flag(self):
        from ui.qt_main.history import PartPickerDialog
        avail = {"mods": 3, "config": 2, "saves": 0, "files": 1, "extras": 4}
        d = PartPickerDialog(None, avail, {"mods", "files"}, True)
        self.assertEqual(d.selected_parts(), {"mods", "files"})
        self.assertTrue(d.remember_check.isChecked())
        d2 = PartPickerDialog(None, avail)
        self.assertEqual(d2.selected_parts(), {"mods", "config", "files", "extras"})   # 无默认 = 有的全勾
        self.assertFalse(d2.remember_check.isChecked())
        d3 = PartPickerDialog(None, avail, {"saves"})            # 默认里有但备份里没有 → 不勾
        self.assertEqual(d3.selected_parts(), set())


class MainWindowTests(unittest.TestCase):
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
        self.w._close_session_log()      # 同真实退出路径:关掉会话日志文件
        self.w.deleteLater()
        pump(30)

    def _finish(self, success=True, err="", silent=False):
        self.w._silent_run = silent
        self.w._worker_thread = None
        self.w._progress_dialog = None
        with mock.patch.object(QtWidgets.QMessageBox, "information") as info, \
                mock.patch.object(QtWidgets.QMessageBox, "critical") as crit, \
                mock.patch.object(self.w, "_notify") as notify:
            self.w._on_migration_finished(success, err)
        return info, crit, notify

    def test_silent_run_skips_message_boxes_but_notifies(self):
        info, crit, notify = self._finish(True, silent=True)
        info.assert_not_called()
        crit.assert_not_called()
        self.assertEqual(notify.call_count, 1)
        info, crit, notify = self._finish(False, "boom", silent=True)
        crit.assert_not_called()
        self.assertEqual(notify.call_count, 1)

    def test_normal_run_still_shows_message_boxes(self):
        info, crit, notify = self._finish(True, silent=False)
        info.assert_called_once()
        notify.assert_not_called()                              # 窗口没在托盘 → 不发通知
        info, crit, notify = self._finish(False, "boom", silent=False)
        crit.assert_called_once()

    def test_progress_dialog_closes_after_hold_on_success(self):
        from ui.qt_main.migration import ProgressDialog
        dlg = ProgressDialog(self.w, 6, 600, {})
        dlg.show()
        self.w._progress_dialog = dlg
        self.w._silent_run = False
        self.w._worker_thread = None
        with mock.patch.object(QtWidgets.QMessageBox, "information"):
            self.w._on_migration_finished(True, "")
        self.assertEqual(dlg.bar.value(), 100)
        self.assertTrue(dlg.isVisible())                         # 150ms 内还在
        pump(ProgressDialog.FINISH_HOLD_MS + 150)
        self.assertFalse(dlg.isVisible())

    def _real_start(self, cfg_over):
        """用真实临时目录跑真实的 _start_migration,截住它交给倒计时/准备阶段的 params。"""
        w = self.w
        base = Path(_tmp.name) / ("inst-%d" % id(cfg_over))
        for side in ("src", "tgt"):
            (base / side / "mods").mkdir(parents=True, exist_ok=True)
        (base / "src" / "mods" / "a.jar").write_bytes(b"x")
        w.source_path, w.target_path, w.world_name = str(base / "src"), str(base / "tgt"), "world"
        w.mod_text.setPlainText("a.jar")
        w.config.update(cfg_over)
        w.config["start_countdown"] = 1
        got = {}
        with mock.patch("ui.qt_main.migration.show_precheck", return_value=True), \
                mock.patch.object(w, "_begin_countdown",
                                  side_effect=lambda secs, p: got.setdefault("p", p)), \
                mock.patch.object(w, "_begin_prepare",
                                  side_effect=lambda p: got.setdefault("p", p)):
            w._start_migration()
        self.assertIn("p", got, "没走到组参数这一步")
        return got["p"]

    def test_real_start_carries_marker_and_silent_when_enabled(self):
        p = self._real_start({"rename_migrated_mods": True, "rename_marker": "◆",
                              "silent_background": True})
        self.assertEqual(p["marker"], "◆")
        self.assertTrue(p["silent"])

    def test_real_start_has_no_marker_when_disabled(self):
        p = self._real_start({"rename_migrated_mods": False, "rename_marker": "◆",
                              "silent_background": False})
        self.assertIsNone(p["marker"])
        self.assertFalse(p["silent"])

    def test_apply_settings_writes_shared_keys_and_persists(self):
        vals = {"theme": "dark", "rename_migrated_mods": True, "rename_marker": "NEW_",
                "silent_background": True}
        self.w._apply_settings(vals)
        self.assertEqual(self.w.config["rename_marker"], "NEW_")
        saved = _cfg.load_raw_config()
        self.assertTrue(saved["rename_migrated_mods"])
        self.assertEqual(saved["rename_marker"], "NEW_")
        self.assertTrue(saved["silent_background"])

    def test_settings_dialog_exposes_the_new_switches(self):
        from ui.qt_main.settings import SettingsDialog
        self.w.config.update({"rename_migrated_mods": True, "rename_marker": "✦",
                              "silent_background": True})
        d = SettingsDialog(self.w, self.w.config)
        v = d.values()
        self.assertTrue(v["rename_migrated_mods"])
        self.assertEqual(v["rename_marker"], "✦")
        self.assertTrue(v["silent_background"])
        d.rename_check.setChecked(False)
        self.assertFalse(d.marker_combo.isEnabled())             # 没开标记时符号选择灰掉

    def test_force_foreground_never_raises_and_is_called_on_show(self):
        with mock.patch.object(self.w, "_force_foreground") as ff:
            self.w.show_from_tray()
        ff.assert_called_once()
        self.w._force_foreground()                               # 真跑一遍:不能抛

    # ---------- 补齐的四个按钮 ----------
    def test_three_buttons_exist_and_rollback_lives_only_in_history(self):
        from ui.qt_main.buttons import DEFAULT_ORDER, LABELS
        self.assertIn("copy_target", self.w._path_btns)
        for key in ("log_big", "log_open"):
            self.assertIn(key, self.w._btn_widgets)
            self.assertIn(key, LABELS)
            self.assertTrue(any(key in v for v in DEFAULT_ORDER.values()))
        # Tk 的 cafd9e8 把回滚并进了历史窗口:主界面不该再有这颗按钮
        self.assertFalse(hasattr(self.w, "rollback_btn"))
        self.assertNotIn("rollback", self.w._btn_widgets)
        self.assertNotIn("rollback", LABELS)
        self.assertEqual(DEFAULT_ORDER["action"], ["history", "start"])

    def test_copy_target_to_source(self):
        self.w.target_edit.setText(r"C:\pack\new")
        self.w._path_btns["copy_target"].click()
        self.assertEqual(self.w.source_edit.text(), r"C:\pack\new")
        self.w.target_edit.setText("")
        self.w.source_edit.setText("keep")
        self.w._path_btns["copy_target"].click()
        self.assertEqual(self.w.source_edit.text(), "keep")    # 目标为空:不动源路径

    def test_open_log_folder_opens_session_dir(self):
        self.w.log("一行日志", "INFO")
        with mock.patch("os.startfile", create=True) as sf:
            self.w._btn_widgets["log_open"].click()
        sf.assert_called_once_with(str(self.w._session_log.directory))

    def test_log_writes_session_file_and_redacts(self):
        from utils import secrets
        with mock.patch.object(secrets, "redact", side_effect=lambda s: s.replace("SECRET", "***")):
            self.w.log("token=SECRET", "WARNING")
        self.w._session_log.flush()
        text = self.w._session_log.path.read_text(encoding="utf-8")
        self.assertIn("token=***", text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("SECRET", self.w.log_text.toPlainText())

    def test_big_log_view_copies_and_follows(self):
        self.w._clear_log()
        self.w.log("第一行", "INFO")
        self.w.log("出错了", "ERROR")
        self.w._btn_widgets["log_big"].click()
        pump(30)
        win = self.w._log_big_view
        self.assertTrue(win.isVisible())
        self.assertIn("出错了", self.w._log_big_text.toPlainText())
        self.assertIn("2", self.w._log_big_count.text())
        self.w.log("新来的", "INFO")                               # 开着时实时追加
        self.assertIn("新来的", self.w._log_big_text.toPlainText())
        self.assertIn("3", self.w._log_big_count.text())
        self.w._btn_widgets["log_big"].click()                    # 再点复用同一个窗口
        self.assertIs(self.w._log_big_view, win)
        win.close()

    def test_quit_paths_close_session_log(self):
        self.w.log("落盘一行", "INFO")
        self.assertIsNotNone(self.w._session_log._file)
        with mock.patch.object(QtWidgets.QApplication, "quit"):
            self.w.request_quit()
        self.assertIsNone(self.w._session_log._file)
        self.w._quitting = False


class ScrollSpeedTests(unittest.TestCase):
    """scroll_speed(1~5 档)与 Tk 版同键:设置窗读写、_apply_settings 立刻生效并落盘、真的改变缓动曲线。"""

    @classmethod
    def setUpClass(cls):
        from ui.qt_big_view import _SmoothWheel
        cls.SW = _SmoothWheel

    def tearDown(self):
        self.SW.set_speed(3)

    def test_levels_map_to_tk_scales_and_clamp(self):
        from utils.helpers import SmoothScroller
        for lvl, scale in zip(range(1, 6), SmoothScroller._SPEED_SCALES):
            self.assertEqual(self.SW.set_speed(lvl), lvl)
            self.assertEqual(self.SW.GLIDE_SCALE, scale)
        self.assertEqual(self.SW.set_speed(0), 1)
        self.assertEqual(self.SW.set_speed(99), 5)
        self.assertEqual(self.SW.set_speed("abc"), 3)
        self.assertEqual(self.SW.set_speed(None), 3)

    def test_apply_config_speed_reads_the_shared_config(self):
        CFG.write_text('{"language":"zh","scroll_speed":5}', encoding="utf-8")
        try:
            self.assertEqual(self.SW.apply_config_speed(), 5)
        finally:
            CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")

    def _travel(self, level, steps=6):
        """一格滚轮后推进固定时长,返回已走的距离:档位越快,走得越远。"""
        from ui.qt_big_view import SmoothTextEdit
        self.SW.set_speed(level)
        v = SmoothTextEdit()
        v.resize(400, 200)
        v.setPlainText(chr(10).join(str(n) for n in range(2000)))
        v.show()
        APP.processEvents()
        v._sw_setup()
        self.SW.set_speed(level)                      # _sw_setup 可能重读配置,这里以测试给的档位为准
        v.verticalScrollBar().setValue(1000)
        v._sw_cur = v._sw_target = 1000.0
        v._sw_target = 1000.0 + 100.0
        v._sw_active = True
        v._sw_last = 0.0
        for n in range(1, steps + 1):
            v.tick_scroll(n * 0.008)
        moved = v._sw_cur - 1000.0
        v._sw_cancel()
        v.close()
        v.deleteLater()
        return moved

    def test_faster_level_travels_farther_in_the_same_time(self):
        moved = [self._travel(n) for n in range(1, 6)]
        self.assertEqual(moved, sorted(moved), moved)           # 档位越大走得越远
        self.assertLess(moved[0], moved[2])
        self.assertLess(moved[2], moved[4])

    def test_default_level_matches_the_original_curve(self):
        """第 3 档 = GLIDE_SCALE 1.0 = 改动前的曲线(不能因为接了档位就改掉默认手感)。"""
        self.SW.set_speed(3)
        ease, frame, dt = self.SW.WHEEL_EASE, self.SW.WHEEL_FRAME_S, 0.008
        expect = 100.0 * (1.0 - (1.0 - ease) ** (dt / frame))
        from ui.qt_big_view import SmoothTextEdit
        v = SmoothTextEdit()
        v.resize(400, 200)
        v.setPlainText(chr(10).join(str(n) for n in range(2000)))
        v.show()
        APP.processEvents()
        v._sw_setup()
        self.SW.set_speed(3)
        v.verticalScrollBar().setValue(1000)
        v._sw_cur, v._sw_target, v._sw_active, v._sw_last = 1000.0, 1100.0, True, 0.0
        v.tick_scroll(dt)
        self.assertAlmostEqual(v._sw_cur - 1000.0, expect, places=6)
        v._sw_cancel()
        v.close()
        v.deleteLater()


class ScrollSpeedSettingsTests(unittest.TestCase):
    def setUp(self):
        from ui.qt_main.window import QtMainWindow
        self.w = QtMainWindow()
        self.w.show()
        pump(30)

    def tearDown(self):
        from ui.qt_big_view import _SmoothWheel
        _SmoothWheel.set_speed(3)
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)

    def test_settings_dialog_reads_and_returns_scroll_speed(self):
        from ui.qt_main.settings import SettingsDialog
        self.w.config["scroll_speed"] = 5
        d = SettingsDialog(self.w, self.w.config)
        self.assertEqual(d.scroll_speed_spin.value(), 5)
        self.assertEqual(d.values()["scroll_speed"], 5)
        d.scroll_speed_spin.setValue(2)
        self.assertEqual(d.values()["scroll_speed"], 2)
        self.assertEqual((d.scroll_speed_spin.minimum(), d.scroll_speed_spin.maximum()), (1, 5))
        self.w.config["scroll_speed"] = "坏值"                  # 配置里是脏数据 → 回默认 3,不抛
        self.assertEqual(SettingsDialog(self.w, self.w.config).scroll_speed_spin.value(), 3)

    def test_apply_settings_takes_effect_immediately_and_persists(self):
        from ui.qt_big_view import _SmoothWheel
        from utils.helpers import SmoothScroller
        self.w._apply_settings({"theme": "dark", "scroll_speed": 4})
        self.assertEqual(_SmoothWheel.GLIDE_SCALE, SmoothScroller._SPEED_SCALES[3])
        self.assertEqual(self.w.config["scroll_speed"], 4)
        self.assertEqual(_cfg.load_raw_config()["scroll_speed"], 4)     # 写进了共用配置,Tk 版读得到

    def test_apply_settings_without_the_key_keeps_the_current_speed(self):
        from ui.qt_big_view import _SmoothWheel
        _SmoothWheel.set_speed(5)
        self.w.config["scroll_speed"] = 5
        self.w._apply_settings({"theme": "dark"})
        self.assertEqual(self.w.config["scroll_speed"], 5)
        self.assertEqual(_SmoothWheel.GLIDE_SCALE, 0.75)


if __name__ == "__main__":
    unittest.main()

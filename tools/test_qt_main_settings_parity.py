"""Qt 设置窗补齐 Tk 设置窗的三块:联网分类、CurseForge Key、回滚默认还原范围。

密钥文件和主配置都指到临时目录,绝不碰用户真实的 ~/.minecraft_migrate_secret.json。
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-settings-parity-")
CFG = Path(_tmp.name) / "config.json"
CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")
SECRET = Path(_tmp.name) / "secret.json"
os.environ["MCTOOL_CONFIG"] = str(CFG)
os.environ["MCTOOL_SECRET_FILE"] = str(SECRET)
os.environ["MCTOOL_LANG"] = "zh"
os.environ.pop("MCTOOL_CURSEFORGE_KEY", None)          # 开发机上可能设了,会盖住"未设置"的断言
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from _testcfg import use_config as _use_config   # noqa: E402
_CFG_PATH = os.environ["MCTOOL_CONFIG"]


def setUpModule():                     # 全量跑时各模块各用各的临时配置
    _use_config(_CFG_PATH)

from PySide6 import QtCore, QtWidgets  # noqa: E402
from utils import config as _cfg  # noqa: E402
from utils import secrets  # noqa: E402

_cfg.CONFIG_FILE = CFG
secrets.SECRET_FILE = SECRET          # import 时已定,这里再钉一次,保证写的是临时文件
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

FAKE_KEY = "TESTKEY-0123456789abcdef-WXYZ"


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


class _Host(QtWidgets.QWidget):
    """设置窗的 parent:只记 toast / log,免得起整个主窗口。"""

    def __init__(self):
        super().__init__()
        self.toasts = []
        self.logs = []

    def toast(self, text, level="info", ms=None, key=None):
        self.toasts.append((level, text))

    def log(self, text, level="INFO"):
        self.logs.append((level, text))


class _Base(unittest.TestCase):
    def setUp(self):
        secrets.clear_key()
        self.host = _Host()

    def tearDown(self):
        secrets.clear_key()
        self.host.deleteLater()
        pump(20)

    def dialog(self, **cfg):
        from ui.qt_main.settings import SettingsDialog
        return SettingsDialog(self.host, cfg)


class RollbackDefaultsTests(_Base):
    def test_order_matches_core(self):
        from core.migrator import _RESTORE_PARTS
        from ui.qt_main.settings import SettingsDialog
        self.assertEqual(tuple(SettingsDialog._ROLLBACK_ORDER), tuple(_RESTORE_PARTS))

    def test_unset_means_all_checked(self):
        d = self.dialog()
        self.assertEqual(d.values()["rollback_parts"],
                         ["mods", "config", "saves", "files", "extras"])

    def test_saved_subset_is_prechecked_and_roundtrips_in_core_order(self):
        d = self.dialog(rollback_parts=["extras", "mods"])          # 存的顺序乱也无所谓
        self.assertEqual(d.values()["rollback_parts"], ["mods", "extras"])
        d._rollback_checks["saves"].setChecked(True)
        self.assertEqual(d.values()["rollback_parts"], ["mods", "saves", "extras"])

    def test_cannot_uncheck_the_last_one(self):
        d = self.dialog(rollback_parts=["config"])
        d._rollback_checks["config"].setChecked(False)
        self.assertTrue(d._rollback_checks["config"].isChecked())    # 被勾回去
        self.assertEqual(d.values()["rollback_parts"], ["config"])

    def test_garbage_config_falls_back_to_something_checked(self):
        d = self.dialog(rollback_parts=["nope", "也不是"])
        self.assertEqual(d.values()["rollback_parts"], ["mods"])
        d2 = self.dialog(rollback_parts="mods")                       # 不是 list
        self.assertEqual(len(d2.values()["rollback_parts"]), 5)


class OnlineTagsTests(_Base):
    def test_default_off_and_roundtrip(self):
        d = self.dialog()
        self.assertFalse(d.values()["online_tags"])
        d.tags_check.setChecked(True)
        self.assertTrue(d.values()["online_tags"])
        self.assertTrue(self.dialog(online_tags=True).values()["online_tags"])

    def test_cache_label_shows_count_and_path(self):
        with mock.patch("core.mod_search.tag_cache_size", return_value=42):
            d = self.dialog()
        txt = d.tag_cache_label.text()
        self.assertIn("42", txt)
        self.assertIn(".minecraft_migrate_tags.json", txt)


class ApplySettingsTests(unittest.TestCase):
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

    def test_online_tags_applied_persisted_and_announced(self):
        self.w.config["online_tags"] = False
        with mock.patch.object(self.w, "toast") as toast:
            self.w._apply_settings({"theme": "dark", "online_tags": True})
        self.assertTrue(self.w.config["online_tags"])
        self.assertTrue(_cfg.load_raw_config()["online_tags"])        # 落盘,Tk 版读同一个键
        self.assertTrue(any("联网分类已开启" in c.args[0] for c in toast.call_args_list))

    def test_online_tags_unchanged_does_not_toast(self):
        self.w.config["online_tags"] = True
        with mock.patch.object(self.w, "toast") as toast:
            self.w._apply_settings({"theme": "dark", "online_tags": True})
        self.assertFalse(any("联网分类" in c.args[0] for c in toast.call_args_list))

    def test_rollback_parts_persisted_in_core_order(self):
        self.w._apply_settings({"theme": "dark", "rollback_parts": ["extras", "config"]})
        self.assertEqual(self.w.config["rollback_parts"], ["config", "extras"])
        self.assertEqual(_cfg.load_raw_config()["rollback_parts"], ["config", "extras"])

    def test_rollback_parts_empty_or_unknown_falls_back_to_all(self):
        self.w._apply_settings({"theme": "dark", "rollback_parts": []})
        self.assertEqual(len(self.w.config["rollback_parts"]), 5)
        self.w._apply_settings({"theme": "dark", "rollback_parts": ["bogus"]})
        self.assertEqual(len(self.w.config["rollback_parts"]), 5)

    def test_rollback_picker_prechecks_what_settings_saved(self):
        """设置里存的范围,正是回滚对话框预勾的范围(两处用同一个键)。"""
        from ui.qt_main.history import PartPickerDialog
        self.w._apply_settings({"theme": "dark", "rollback_parts": ["mods", "files"]})
        saved = self.w.config["rollback_parts"]
        avail = {"mods": 3, "config": 2, "saves": 1, "files": 1, "extras": 4}
        d = PartPickerDialog(None, avail, set(saved), False)
        self.assertEqual(d.selected_parts(), {"mods", "files"})

    def test_without_the_keys_nothing_is_overwritten(self):
        self.w.config["online_tags"] = True
        self.w.config["rollback_parts"] = ["saves"]
        self.w._apply_settings({"theme": "dark"})
        self.assertTrue(self.w.config["online_tags"])
        self.assertEqual(self.w.config["rollback_parts"], ["saves"])


class CurseForgeKeyTests(_Base):
    def test_status_line_when_unset(self):
        d = self.dialog()
        self.assertIn("未设置", d.cf_status_label.text())

    def test_input_is_masked(self):
        d = self.dialog()
        self.assertEqual(d.cf_key_edit.echoMode(), QtWidgets.QLineEdit.Password)

    def test_save_writes_only_the_secret_file_and_clears_the_box(self):
        d = self.dialog()
        d.cf_key_edit.setText('  "%s"  ' % FAKE_KEY)                # 网页复制常见的脏形态
        d._save_cf_key()
        self.assertEqual(secrets.get_key(), FAKE_KEY)
        self.assertTrue(SECRET.exists())
        self.assertEqual(d.cf_key_edit.text(), "")                   # 存完立刻清空
        self.assertNotIn(FAKE_KEY, d.cf_status_label.text())         # 状态行只有末 4 位 + 指纹
        self.assertIn(FAKE_KEY[-4:], d.cf_status_label.text())
        self.assertNotIn("curseforge_api_key", CFG.read_text(encoding="utf-8"))   # 不进主配置
        self.assertNotIn("curseforge_api_key", repr(d.values()))     # 也不会随「确定」回传
        self.assertEqual(self.host.toasts[-1][0], "ok")

    def test_key_never_reaches_log_or_toast(self):
        d = self.dialog()
        d.cf_key_edit.setText(FAKE_KEY)
        d._save_cf_key()
        blob = repr(self.host.logs) + repr(self.host.toasts)
        self.assertNotIn(FAKE_KEY, blob)
        self.assertTrue(any("指纹" in t for _, t in self.host.logs))

    def test_empty_save_writes_nothing(self):
        d = self.dialog()
        d.cf_key_edit.setText("   ")
        d._save_cf_key()
        self.assertFalse(SECRET.exists())
        self.assertEqual(self.host.toasts[-1][0], "warn")

    def test_clear_removes_the_key(self):
        secrets.set_key(FAKE_KEY)
        d = self.dialog()
        self.assertIn(FAKE_KEY[-4:], d.cf_status_label.text())
        d.cf_key_edit.setText("typed")
        d._clear_cf_key()
        self.assertFalse(secrets.has_key())
        self.assertFalse(SECRET.exists())
        self.assertEqual(d.cf_key_edit.text(), "")
        self.assertIn("未设置", d.cf_status_label.text())

    def test_buttons_do_not_trigger_the_ok_button(self):
        d = self.dialog()
        for b in (d.cf_save_btn, d.cf_test_btn, d.cf_clear_btn):
            self.assertFalse(b.autoDefault())

    def test_test_without_any_key_does_not_hit_the_network(self):
        d = self.dialog()
        with mock.patch("core.mod_search.test_curseforge_key") as net:
            d._test_cf_key()
            pump(60)
        net.assert_not_called()
        self.assertEqual(self.host.toasts[-1][0], "warn")
        self.assertTrue(d.cf_test_btn.isEnabled())

    def _run_test(self, d, result):
        """跑「测试」:网络那层换成假的,等后台线程把结果送回主线程。"""
        with mock.patch("core.mod_search.test_curseforge_key", return_value=result) as net:
            d._test_cf_key()
            self.assertFalse(d.cf_test_btn.isEnabled())               # 测试期间按钮置灰
            self.assertTrue(d._cf_testing)
            end = time.perf_counter() + 3
            while d._cf_testing and time.perf_counter() < end:
                pump(10)
        return net

    def test_test_uses_the_typed_key_and_reports_success_without_leaking(self):
        d = self.dialog()
        d.cf_key_edit.setText(FAKE_KEY)
        net = self._run_test(d, (True, "Key 可用（指纹 abcd1234）"))
        net.assert_called_once_with(FAKE_KEY)                         # 测输入框里的,不是已保存的
        self.assertFalse(d._cf_testing)
        self.assertTrue(d.cf_test_btn.isEnabled())
        self.assertEqual(self.host.toasts[-1], ("ok", "✅ CurseForge Key 可用"))
        self.assertFalse(SECRET.exists())                             # 测试不保存
        self.assertEqual(d.cf_key_edit.text(), FAKE_KEY)              # 也不清空输入框

    def test_test_failure_is_reported_as_warn(self):
        secrets.set_key(FAKE_KEY)
        d = self.dialog()
        net = self._run_test(d, (False, "HTTP 403"))
        net.assert_called_once_with(None)                             # 输入框空 → 测已保存的
        self.assertEqual(self.host.toasts[-1][0], "warn")
        self.assertTrue(any(lv == "ERROR" and "HTTP 403" in t for lv, t in self.host.logs))

    def test_test_exception_is_redacted(self):
        d = self.dialog()
        d.cf_key_edit.setText(FAKE_KEY)
        boom = RuntimeError("request failed: x-api-key: %s" % FAKE_KEY)
        with mock.patch("core.mod_search.test_curseforge_key", side_effect=boom):
            d._test_cf_key()
            end = time.perf_counter() + 3
            while d._cf_testing and time.perf_counter() < end:
                pump(10)
        self.assertNotIn(FAKE_KEY, repr(self.host.logs) + repr(self.host.toasts))

    def test_second_click_while_testing_is_ignored(self):
        d = self.dialog()
        d.cf_key_edit.setText(FAKE_KEY)
        gate = threading.Event()

        def slow(key=None):
            gate.wait(2)
            return True, "ok"

        with mock.patch("core.mod_search.test_curseforge_key", side_effect=slow) as net:
            d._test_cf_key()
            d._test_cf_key()
            d._test_cf_key()
            gate.set()
            end = time.perf_counter() + 3
            while d._cf_testing and time.perf_counter() < end:
                pump(10)
        self.assertEqual(net.call_count, 1)

    def test_closing_the_dialog_mid_test_does_not_crash(self):
        d = self.dialog()
        d.cf_key_edit.setText(FAKE_KEY)
        gate = threading.Event()

        def slow(key=None):
            gate.wait(2)
            return True, "ok"

        with mock.patch("core.mod_search.test_curseforge_key", side_effect=slow):
            d._test_cf_key()
            d.deleteLater()
            pump(30)
            gate.set()
            pump(150)                      # 线程这时去 emit 一个已销毁的对象:不能抛到主线程


if __name__ == "__main__":
    unittest.main()

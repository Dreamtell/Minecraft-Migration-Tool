"""Qt 主界面补齐 Tk 版的两处:设置窗「快捷链接」、路径区按钮进「界面按钮」显隐/排序。

配置指到临时目录;浏览器调用全部 mock 掉,不会真的打开网页。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-links-buttons-")
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


class _Host(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.toasts = []
        self.logs = []

    def toast(self, text, level="info", ms=None, key=None):
        self.toasts.append((level, text))

    def log(self, text, level="INFO"):
        self.logs.append((level, text))


class QuickLinksTests(unittest.TestCase):
    def setUp(self):
        from ui.qt_main.settings import SettingsDialog
        self.host = _Host()
        self.dlg = SettingsDialog(self.host, {})

    def tearDown(self):
        self.dlg.deleteLater()
        self.host.deleteLater()
        pump(20)

    def test_same_urls_as_tk(self):
        """Qt 这边是抄的一份(不能 import 带 tkinter 的 mw_common),两边必须一致。"""
        from ui import mw_common
        from ui.qt_main import settings as s
        for name in ("LINK_MINECRAFT", "LINK_GITHUB", "LINK_MCMOD", "LINK_PCL"):
            self.assertEqual(getattr(s, name), getattr(mw_common, name), name)

    def test_four_buttons_in_tk_order(self):
        self.assertEqual(list(self.dlg.link_buttons), ["minecraft", "mcmod", "pcl", "github"])
        self.assertEqual(self.dlg.link_buttons["pcl"].text(), "🚀 PCL 启动器")

    def test_click_opens_new_tab_logs_and_toasts(self):
        from ui.qt_main.settings import LINK_MCMOD
        with mock.patch("webbrowser.open_new_tab") as opener:
            self.dlg.link_buttons["mcmod"].click()
        opener.assert_called_once_with(LINK_MCMOD)
        self.assertTrue(any(LINK_MCMOD in t for _, t in self.host.logs))
        self.assertTrue(any(LINK_MCMOD in t for _, t in self.host.toasts))

    def test_click_does_not_close_the_dialog(self):
        with mock.patch("webbrowser.open_new_tab"):
            self.dlg.link_buttons["github"].click()
        self.assertEqual(self.dlg.result(), 0)

    def test_failure_shows_error_and_no_success_toast(self):
        with mock.patch("webbrowser.open_new_tab", side_effect=OSError("boom")), \
                mock.patch.object(QtWidgets.QMessageBox, "critical") as crit:
            self.dlg.link_buttons["minecraft"].click()
        crit.assert_called_once()
        self.assertIn("boom", crit.call_args.args[2])
        self.assertEqual(self.host.toasts, [])

    def test_values_unchanged_by_links(self):
        self.assertFalse(any("link" in k for k in self.dlg.values()))


class PathButtonPrefsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def make(self, **cfg):
        w = self.W()
        w.config.update(cfg)
        w._apply_button_prefs()
        w.show()
        pump(30)
        self.addCleanup(self._close, w)
        return w

    @staticmethod
    def _close(w):
        w.hide()
        w._close_session_log()
        w.deleteLater()
        pump(30)

    def test_groups_and_keys_match_tk(self):
        from ui import mw_common
        from ui.qt_main.buttons import DEFAULT_ORDER, GROUPS, LABELS
        gids = [g for g, _ in GROUPS]
        self.assertEqual(gids[:2], ["path", "path_target"])
        for g in ("path", "path_target"):
            self.assertEqual(DEFAULT_ORDER[g], mw_common._DEFAULT_BUTTON_ORDER[g])
            for k in DEFAULT_ORDER[g]:
                self.assertIn(k, LABELS)

    def test_hidden_path_buttons_are_hidden(self):
        w = self.make(buttons_hidden=["copy_target", "browse_target"])
        self.assertFalse(w._path_btns["copy_target"].isVisible())
        self.assertFalse(w.tgt_browse_btn.isVisible())
        self.assertTrue(w.src_browse_btn.isVisible())

    def test_reorder_keeps_edit_and_status_in_place(self):
        w = self.make(button_order={"path": ["copy_target", "browse_source"]})
        copy_btn = w._path_btns["copy_target"]
        self.assertLess(copy_btn.geometry().x(), w.src_browse_btn.geometry().x())
        # 输入框在按钮左边、状态图标在按钮右边,重排没有把它们摘掉
        self.assertTrue(w.source_edit.isVisible())
        self.assertLess(w.source_edit.geometry().right(), copy_btn.geometry().x())
        self.assertGreater(w.source_status.geometry().x(), w.src_browse_btn.geometry().x())
        self.assertTrue(w.target_edit.isVisible())

    def test_dialog_roundtrip_keeps_tk_only_keys(self):
        from ui.qt_main.buttons import merge_into_config
        hidden, order = merge_into_config(
            ["rollback", "copy_target"], {"path": ["copy_target", "browse_source"],
                                          "action": ["history", "rollback", "start"]},
            [], {"path": ["browse_source", "copy_target"]})
        self.assertIn("rollback", hidden)
        self.assertNotIn("copy_target", hidden)
        self.assertEqual(order["path"], ["browse_source", "copy_target"])
        self.assertEqual(order["action"], ["history", "rollback", "start"])

    def test_busy_still_disables_path_buttons(self):
        w = self.make()
        with mock.patch.object(w, "_is_busy", return_value=True):
            w._refresh_busy_state()
        self.assertFalse(w._path_btns["copy_target"].isEnabled())
        self.assertFalse(w.tgt_browse_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()

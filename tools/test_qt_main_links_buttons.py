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

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
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

    def test_click_opens_the_right_url(self):
        """原意：点哪个链接就打开哪个地址(不能串台，也不能点了没反应)。

        新设置窗不再往主窗口写日志/弹提示(改动即时生效，没有"操作成功"回执)，
        所以这里把"给出反馈"那半条断言换成逐个按钮校验它打开的是自己的 URL。
        """
        from ui.qt_main import settings as s
        urls = {"minecraft": s.LINK_MINECRAFT, "mcmod": s.LINK_MCMOD,
                "pcl": s.LINK_PCL, "github": s.LINK_GITHUB}
        self.assertEqual(set(urls), set(self.dlg.link_buttons))
        for key, url in urls.items():
            with mock.patch("webbrowser.open") as opener:
                self.dlg.link_buttons[key].click()
            opener.assert_called_once_with(url)
            # 按钮 tooltip 写着目标地址，用户点之前看得到要去哪
            self.assertEqual(self.dlg.link_buttons[key].toolTip(), url)

    def test_click_does_not_close_the_dialog(self):
        with mock.patch("webbrowser.open") as opener:     # 别真的弹出浏览器
            self.dlg.link_buttons["github"].click()
        opener.assert_called_once()
        self.assertEqual(self.dlg.result(), 0)

    def test_failure_is_contained_and_dialog_stays_usable(self):
        """打不开链接：同 Tk 要弹错误框，但不能把设置窗带崩，也不能装作成功。

        错误框会把测试卡死（无头环境没人点按钮），所以这里 mock 掉它，
        只验证「弹了、消息里带着原因」。
        """
        from ui.qt_main.settings import LINK_GITHUB, LINK_MCMOD
        with mock.patch("webbrowser.open", side_effect=OSError("boom")), \
                mock.patch.object(QtWidgets.QMessageBox, "warning") as warn:
            self.dlg.link_buttons["minecraft"].click()      # 不抛
        self.assertEqual(warn.call_count, 1)
        self.assertIn("boom", warn.call_args[0][2])
        self.assertEqual(self.dlg.result(), 0)              # 没被关掉
        self.assertEqual(self.host.toasts, [])              # 失败不能报成功
        self.assertFalse(any("boom" in t for _, t in self.host.logs))
        with mock.patch("webbrowser.open") as opener:
            self.dlg.link_buttons["mcmod"].click()
        opener.assert_called_once_with(LINK_MCMOD)          # 失败没污染后续点击
        self.assertEqual(self.dlg.link_buttons["github"].toolTip(), LINK_GITHUB)

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


class ExtraStatusTextTests(unittest.TestCase):
    def setUp(self):
        self.src = Path(tempfile.mkdtemp(dir=_tmp.name))
        (self.src / "shaderpacks").mkdir()
        (self.src / "servers.dat").write_text("x", encoding="utf-8")

    def text(self, lines, src=None):
        from ui.qt_main.settings import extra_status_text
        return extra_status_text(lines, self.src if src is None else src)

    def test_empty_list(self):
        self.assertIn("当前清单是空的", self.text(["", "  "]))

    def test_no_source(self):
        self.assertIn("清单里 2 条；选了源整合包目录后", self.text(["a/", "b"], src=""))

    def test_all_found(self):
        t = self.text(["shaderpacks/", "servers.dat", "shaderpacks\\"])
        self.assertIn("清单里 3 条，源目录里找得到 3 条", t)
        self.assertIn("这次都会带上", t)

    def test_missing_lists_first_four_then_etc(self):
        t = self.text(["shaderpacks/"] + ["m%d/" % i for i in range(6)])
        self.assertIn("找得到 1 条", t)
        self.assertIn("m0/、m1/、m2/、m3/", t)
        self.assertNotIn("m4/", t)
        self.assertIn(" 等", t)

    def test_dialog_reads_parent_list_and_source(self):
        from ui.qt_main.settings import SettingsDialog
        host = _Host()
        host.extra_text = QtWidgets.QPlainTextEdit("shaderpacks/\nnope/")
        host.source_path = str(self.src)
        dlg = SettingsDialog(host, {})
        try:
            self.assertIn("清单里 2 条，源目录里找得到 1 条", dlg.extra_status_label.text())
            self.assertIn("nope/", dlg.extra_status_label.text())
        finally:
            dlg.deleteLater()
            host.deleteLater()
            pump(20)


class LegacyExtraDefaultsTests(unittest.TestCase):
    """老配置的 extra_defaults 在 Qt 主界面启动时也要一次性并进清单(同 Tk)。"""

    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def make(self, **raw):
        cfg = {"language": "zh", "theme": "dark", "net_float_on": False}
        cfg.update(raw)
        _cfg.save_raw_config(cfg)
        w = self.W()
        self.addCleanup(self._close, w)
        return w

    @staticmethod
    def _close(w):
        w._close_session_log()
        w.deleteLater()
        pump(30)

    def lines(self, w):
        return [x for x in w.extra_text.toPlainText().splitlines() if x.strip()]

    def test_merged_once_and_cleared(self):
        w = self.make(extra_list="options.txt\nshaderpacks", extra_defaults=[
            "shaderpacks", "kubejs", "bogus", "kubejs"])
        self.assertEqual(self.lines(w), ["options.txt", "shaderpacks", "kubejs/"])
        self.assertEqual(w.config["extra_defaults"], [])
        raw = _cfg.load_raw_config()
        self.assertEqual(raw["extra_defaults"], [])
        self.assertIn("kubejs/", raw["extra_list"])          # 并入结果已落盘
        self.assertEqual(w.extra_text._undo_stack, [])       # 并入不算一步撤销

    def test_deleted_entries_do_not_come_back(self):
        self.make(extra_defaults=["kubejs"])._close_session_log()
        raw = _cfg.load_raw_config()
        raw["extra_list"] = ""
        _cfg.save_raw_config(raw)
        w = self.W()
        self.addCleanup(self._close, w)
        self.assertEqual(self.lines(w), [])

    def test_all_present_still_clears_and_logs(self):
        w = self.make(extra_list="kubejs/", extra_defaults=["kubejs"])
        self.assertEqual(self.lines(w), ["kubejs/"])
        self.assertEqual(_cfg.load_raw_config()["extra_defaults"], [])
        self.assertIn("都在清单里了", w.log_text.toPlainText())

    def test_nothing_valid_leaves_config_alone(self):
        w = self.make(extra_list="a", extra_defaults=["bogus"])
        self.assertEqual(self.lines(w), ["a"])
        self.assertEqual(_cfg.load_raw_config()["extra_defaults"], ["bogus"])

    def test_garbage_value_is_ignored(self):
        w = self.make(extra_list="a", extra_defaults="shaderpacks")
        self.assertEqual(self.lines(w), ["a"])


class SmallListPageTests(unittest.TestCase):
    """模组页说明、config 页检查图例、倒计时开始时关子窗口(都对齐 Tk)。"""

    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        _cfg.save_raw_config({"language": "zh", "theme": "dark", "net_float_on": False})
        self.w = self.W()
        self.w.show()
        pump(30)

    def tearDown(self):
        self.w._stop_countdown_timer()
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)

    def test_mod_page_has_hint(self):
        page = self.w.tabs.widget(self.w._mod_tab_index)
        texts = [lab.text() for lab in page.findChildren(QtWidgets.QLabel)]
        self.assertTrue(any("同名匹配不区分大小写" in t for t in texts))

    def test_legend_on_config_page_only_and_matches_mark_colors(self):
        legend = self.w._check_legend
        self.assertEqual([c.text() for c in legend.values()], ["✅ 存在", "❌ 缺失", "⚠️ 重复"])
        cfg_page = self.w.tabs.widget(self.w._config_tab_index)
        for chip in legend.values():
            self.assertTrue(cfg_page.isAncestorOf(chip))
        colors = self.w._check_mark_colors()
        for st, chip in legend.items():
            self.assertIn(QtGui.QColor(colors[st]).name(), chip.styleSheet())

    def test_legend_follows_theme(self):
        from ui.qt_main import window as win
        self.w.theme = win.LIGHT_THEME if self.w.theme is win.DARK_THEME else win.DARK_THEME
        self.w._repaint_list_labels()
        for st, chip in self.w._check_legend.items():
            self.assertIn(QtGui.QColor(self.w._check_mark_colors()[st]).name(), chip.styleSheet())

    def test_countdown_closes_titled_children_but_not_frameless(self):
        child = QtWidgets.QWidget(self.w, QtCore.Qt.Window)
        child.setWindowTitle("diff")
        child.show()
        floater = QtWidgets.QWidget(self.w, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        floater.show()
        loose = QtWidgets.QDialog()                 # 没有 parent 的顶层窗(差异窗就是这样)
        loose.show()
        pump(30)
        self.w._begin_countdown(3, {})
        pump(30)
        self.assertFalse(child.isVisible())
        self.assertFalse(loose.isVisible())
        self.assertTrue(floater.isVisible())
        self.assertTrue(self.w.isVisible())
        for x in (child, floater, loose):
            x.deleteLater()


if __name__ == "__main__":
    unittest.main()

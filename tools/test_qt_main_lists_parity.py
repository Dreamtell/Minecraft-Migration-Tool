"""Qt 主窗口补齐 Tk 版清单区的两块:「＋ 常用目录」预设菜单、「应用迁移记录 / 还原到应用前」。

配置和历史目录(跟着配置文件走)都指到临时目录,不碰用户真实的迁移档案。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-lists-parity-")
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
from utils import history_store  # noqa: E402

_cfg.CONFIG_FILE = CFG
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


class PresetTableTests(unittest.TestCase):
    def test_shared_by_tk_and_qt_and_not_duplicated(self):
        from core.extra_presets import EXTRA_PRESETS
        import ui.mw_common as common
        self.assertIs(common._EXTRA_PRESETS, EXTRA_PRESETS)       # Tk 侧老接口面还在,且是同一份
        self.assertEqual(len(EXTRA_PRESETS), 14)
        self.assertEqual(len({k for k, _r, _d in EXTRA_PRESETS}), 14)     # 配置键不重复
        self.assertEqual(len({r for _k, r, _d in EXTRA_PRESETS}), 14)     # 条目不重复
        self.assertIn("shaderpacks/", [r for _k, r, _d in EXTRA_PRESETS])

    def test_qt_process_does_not_need_tkinter_for_presets(self):
        """真正的约束是「不 import tkinter」(注释里提到它不算):按语法树查 import,别按字符串查。"""
        import ast
        src = (Path(__file__).resolve().parents[1] / "Minecraft迁移工具" / "core"
               / "extra_presets.py").read_text(encoding="utf-8")
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        self.assertNotIn("tkinter", mods)
        self.assertNotIn("ui", mods)                         # 也不许反向依赖界面层
        self.assertEqual(mods, {"utils"})

    def test_preset_rows_mark_existing_entries_case_and_slash_insensitively(self):
        from core.extra_presets import preset_rows
        rows = {rel: done for rel, _d, done in
                preset_rows(["", "  Shaderpacks  ", "resourcepacks\\", "# 注释", "servers.dat"])}
        self.assertTrue(rows["shaderpacks/"])          # 大小写、末尾斜杠都不影响
        self.assertTrue(rows["resourcepacks/"])        # 反斜杠也认
        self.assertTrue(rows["servers.dat"])
        self.assertFalse(rows["kubejs/"])
        self.assertEqual(len(rows), 14)

    def test_describe(self):
        from core.extra_presets import describe
        self.assertTrue(describe("kubejs/"))
        self.assertEqual(describe("不是预设/"), "")


class _WinBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        for p in history_store._root().glob("*.json") if history_store._root().exists() else []:
            p.unlink()
        self.pack = tempfile.TemporaryDirectory(prefix="mctool-pack-")
        self.addCleanup(self.pack.cleanup)
        self.src = Path(self.pack.name) / "src"
        self.src.mkdir()
        self.w = self.W()
        self.w.show()
        pump(30)

    def tearDown(self):
        self.w._migration_running = False
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)
        root = history_store._root()
        for p in (root.glob("*.json") if root.exists() else []):
            p.unlink()

    def set_source(self, path):
        self.w.source_edit.setText(str(path))
        self.assertEqual(self.w.source_path, str(path))

    def lines(self, box):
        return [x for x in box.toPlainText().splitlines() if x.strip()]


class PresetMenuTests(_WinBase):
    def test_button_exists_registered_and_ordered(self):
        from ui.qt_main.buttons import DEFAULT_ORDER, LABELS
        self.assertIn("add_extra_preset", self.w._btn_widgets)
        self.assertIn("add_extra_preset", LABELS)
        order = DEFAULT_ORDER["extra"]
        self.assertEqual(order[:2], ["add_extra_options", "add_extra_preset"])   # 同 Tk:紧跟 options.txt
        self.assertEqual(self.w._btn_widgets["add_extra_preset"].text(), "＋ 常用目录 ▾")

    def test_button_is_locked_while_busy(self):
        self.assertIn("add_extra_preset", self.w._BUSY_LOCKED_KEYS)
        self.w._scanning = True
        self.w._refresh_busy_state()
        self.assertFalse(self.w._btn_widgets["add_extra_preset"].isEnabled())
        self.w._scanning = False
        self.w._refresh_busy_state()
        self.assertTrue(self.w._btn_widgets["add_extra_preset"].isEnabled())

    def test_menu_lists_all_presets_and_ticks_existing(self):
        self.w._replace_text(self.w.extra_text, "shaderpacks/\nkubejs")
        with mock.patch.object(QtWidgets.QMenu, "popup"):
            self.w._open_extra_preset_menu()
        acts = self.w._preset_menu.actions()
        self.assertEqual(len(acts), 14)
        by = {a.text().split()[0]: a.text() for a in acts}
        self.assertIn("✅", by["shaderpacks/"])
        self.assertIn("✅", by["kubejs/"])                # 清单里写的是 kubejs(无斜杠)也算已在
        self.assertNotIn("✅", by["resourcepacks/"])

    def test_clicking_a_menu_item_adds_it(self):
        (self.src / "kubejs").mkdir()
        self.set_source(self.src)
        with mock.patch.object(QtWidgets.QMenu, "popup"):
            self.w._open_extra_preset_menu()
        act = next(a for a in self.w._preset_menu.actions() if a.text().startswith("kubejs/"))
        act.trigger()
        self.assertEqual(self.lines(self.w.extra_text), ["kubejs/"])

    def test_add_when_present_in_source(self):
        (self.src / "shaderpacks").mkdir()
        self.set_source(self.src)
        self.assertEqual(self.w._quick_add_preset("shaderpacks/"), 1)
        self.assertEqual(self.lines(self.w.extra_text), ["shaderpacks/"])

    def test_missing_in_source_is_refused_and_not_added(self):
        self.set_source(self.src)                        # 空目录,什么预设都没有
        with mock.patch.object(self.w, "toast") as toast:
            self.assertEqual(self.w._quick_add_preset("shaderpacks/"), 0)
        self.assertEqual(self.lines(self.w.extra_text), [])
        self.assertEqual(toast.call_args.args[1], "warn")
        self.assertIn("shaderpacks/", toast.call_args.args[0])

    def test_no_source_is_refused(self):
        self.w.source_edit.setText("")
        with mock.patch.object(self.w, "toast") as toast:
            self.assertEqual(self.w._quick_add_preset("shaderpacks/"), 0)
        self.assertEqual(toast.call_args.args[1], "warn")

    def test_duplicate_is_not_added_twice_even_with_different_spelling(self):
        (self.src / "resourcepacks").mkdir()
        self.set_source(self.src)
        self.w._replace_text(self.w.extra_text, "Resourcepacks\\")
        with mock.patch.object(self.w, "toast") as toast:
            self.assertEqual(self.w._quick_add_preset("resourcepacks/"), 0)
        self.assertEqual(self.lines(self.w.extra_text), ["Resourcepacks\\"])
        self.assertEqual(toast.call_args.args[1], "info")

    def test_files_presets_work_too(self):
        (self.src / "servers.dat").write_text("x", encoding="utf-8")
        self.set_source(self.src)
        self.assertEqual(self.w._quick_add_preset("servers.dat"), 1)
        self.assertEqual(self.lines(self.w.extra_text), ["servers.dat"])

    def test_add_is_undoable_with_ctrl_z(self):
        (self.src / "kubejs").mkdir()
        self.set_source(self.src)
        self.w.extra_text.reset_undo()
        self.w._quick_add_preset("kubejs/")
        self.assertEqual(self.lines(self.w.extra_text), ["kubejs/"])
        self.w.extra_text.undo()
        self.assertEqual(self.lines(self.w.extra_text), [])


def _record(**kw):
    rec = {"source": r"C:\packs\old", "target": r"C:\packs\new", "world": "世界A",
           "lists": {"mods": ["a.jar", "b.jar"], "configs": ["x.toml"], "extras": ["kubejs/"]}}
    rec.update(kw)
    return rec


class ApplyRecordTests(_WinBase):
    def fill(self):
        self.w.source_edit.setText(r"D:\now\src")
        self.w.target_edit.setText(r"D:\now\tgt")
        self.w.world_edit.setText("当前世界")
        self.w._replace_text(self.w.mod_text, "cur.jar")
        self.w._replace_text(self.w.config_text, "cur.cfg")
        self.w._replace_text(self.w.extra_text, "cur/")

    def test_apply_fills_everything_and_does_not_start_a_migration(self):
        self.fill()
        with mock.patch.object(self.w, "_start_migration") as start:
            ok = self.w._apply_record(_record())
        self.assertTrue(ok)
        start.assert_not_called()                                    # 只回填,不开跑
        self.assertEqual(self.w.source_edit.text(), r"C:\packs\old")
        self.assertEqual(self.w.target_edit.text(), r"C:\packs\new")
        self.assertEqual(self.w.world_edit.text(), "世界A")
        self.assertEqual(self.lines(self.w.mod_text), ["a.jar", "b.jar"])
        self.assertEqual(self.lines(self.w.config_text), ["x.toml"])
        self.assertEqual(self.lines(self.w.extra_text), ["kubejs/"])

    def test_apply_replaces_the_lists_not_appends(self):
        self.fill()
        self.w._apply_record(_record(lists={"mods": ["only.jar"], "configs": [], "extras": []}))
        self.assertEqual(self.lines(self.w.mod_text), ["only.jar"])
        self.assertEqual(self.lines(self.w.config_text), [])          # 记录里空的清单也清空,同 Tk
        self.assertEqual(self.lines(self.w.extra_text), [])

    def test_record_without_paths_keeps_current_paths(self):
        self.fill()
        self.w._apply_record(_record(source="", target="", world=""))
        self.assertEqual(self.w.source_edit.text(), r"D:\now\src")
        self.assertEqual(self.w.world_edit.text(), "当前世界")

    def test_blank_entries_are_dropped(self):
        self.w._apply_record(_record(lists={"mods": ["a.jar", "", "  ", "b.jar"], "configs": [], "extras": []}))
        self.assertEqual(self.w.mod_text.toPlainText().splitlines(), ["a.jar", "b.jar"])    # 没有空行混进去

    def test_empty_or_garbage_record_changes_nothing(self):
        self.fill()
        before = self.w._snapshot_ui()
        for bad in (None, "字符串", [], {}, {"lists": {}}, _record(lists={"mods": [], "configs": [], "extras": []})):
            with mock.patch.object(self.w, "toast") as toast:
                self.assertFalse(self.w._apply_record(bad))
            self.assertEqual(toast.call_args.args[1], "warn")
        self.assertEqual(self.w._snapshot_ui(), before)
        self.assertIsNone(self.w._apply_undo)

    def test_refused_while_busy(self):
        self.fill()
        before = self.w._snapshot_ui()
        self.w._scanning = True
        with mock.patch.object(self.w, "toast") as toast:
            self.assertFalse(self.w._apply_record(_record()))
        self.w._scanning = False
        self.assertEqual(self.w._snapshot_ui(), before)
        self.assertEqual(toast.call_args.args[1], "warn")

    def test_pill_toggles_apply_and_undo(self):
        self.assertEqual(self.w.apply_pill.text(), "🔁 应用上次记录")
        self.fill()
        before = self.w._snapshot_ui()
        self.w._apply_record(_record())
        self.assertEqual(self.w.apply_pill.text(), "↩ 还原到应用前")
        self.assertTrue(self.w._undo_apply_record())
        self.assertEqual(self.w._snapshot_ui(), before)               # 路径、存档名、三个清单全回来
        self.assertEqual(self.w.apply_pill.text(), "🔁 应用上次记录")
        self.assertIsNone(self.w._apply_undo)

    def test_undo_without_apply_is_a_warning_not_a_crash(self):
        with mock.patch.object(self.w, "toast") as toast:
            self.assertFalse(self.w._undo_apply_record())
        self.assertEqual(toast.call_args.args[1], "warn")

    def test_second_apply_keeps_the_first_snapshot_semantics(self):
        """连点两次应用:药丸这时已是「还原」,不会再应用第二次,所以快照始终是第一次之前的状态。"""
        self.fill()
        before = self.w._snapshot_ui()
        self.w._apply_record(_record())
        with mock.patch.object(self.w, "_apply_record") as again:
            self.w.apply_pill.click()                                 # 药丸现在是「还原」
        again.assert_not_called()
        self.assertEqual(self.w._snapshot_ui(), before)

    def test_apply_pill_is_greyed_while_busy(self):
        self.w._scanning = True
        self.w._refresh_busy_state()
        self.assertFalse(self.w.apply_pill.isEnabled())
        self.w._scanning = False
        self.w._refresh_busy_state()
        self.assertTrue(self.w.apply_pill.isEnabled())

    def test_apply_is_one_undo_step_in_the_list_boxes(self):
        self.fill()
        self.w.mod_text.reset_undo()
        self.w._apply_record(_record())
        self.w.mod_text.undo()
        self.assertEqual(self.lines(self.w.mod_text), ["cur.jar"])    # Ctrl+Z 一步回到应用前

    def test_pack_pill_still_there_for_the_update_warning(self):
        self.assertTrue(hasattr(self.w, "pack_pill"))
        self.assertIs(self.w.pack_pill.parentWidget(), self.w.apply_pill.parentWidget())


class ApplyLastRecordTests(_WinBase):
    def test_no_records_warns(self):
        with mock.patch.object(self.w, "toast") as toast:
            self.assertFalse(self.w._apply_last_record())
        self.assertEqual(toast.call_args.args[1], "warn")
        self.assertIn("还没有迁移记录", toast.call_args.args[0])

    def test_uses_the_newest_record(self):
        history_store.save_record(_record(id="old", epoch=100.0, world="旧",
                                          lists={"mods": ["old.jar"], "configs": [], "extras": []}))
        history_store.save_record(_record(id="new", epoch=200.0, world="新",
                                          lists={"mods": ["new.jar"], "configs": [], "extras": []}))
        self.assertTrue(self.w._apply_last_record())
        self.assertEqual(self.w.world_edit.text(), "新")
        self.assertEqual(self.lines(self.w.mod_text), ["new.jar"])

    def test_pill_click_applies_the_last_record(self):
        history_store.save_record(_record(id="r1", epoch=1.0))
        self.w.apply_pill.click()
        self.assertEqual(self.w.world_edit.text(), "世界A")
        self.assertEqual(self.w.apply_pill.text(), "↩ 还原到应用前")

    def test_records_are_written_under_the_temp_dir_not_the_real_one(self):
        self.assertTrue(str(history_store._root()).startswith(str(Path(_tmp.name))))


class HistoryDialogApplyTests(_WinBase):
    def open_dialog(self):
        from ui.qt_main.history import HistoryDialog
        d = HistoryDialog(self.w)
        self.addCleanup(d.deleteLater)
        return d

    def test_apply_button_exists_and_follows_selection(self):
        d = self.open_dialog()                                         # 没有记录
        self.assertFalse(d.apply_btn.isEnabled())
        history_store.save_record(_record(id="r1", epoch=1.0))
        d2 = self.open_dialog()
        d2.list_widget.setCurrentRow(0)
        self.assertTrue(d2.apply_btn.isEnabled())

    def test_apply_from_history_fills_main_window_and_closes_dialog(self):
        history_store.save_record(_record(id="r1", epoch=1.0))
        d = self.open_dialog()
        d.list_widget.setCurrentRow(0)
        with mock.patch.object(self.w, "_start_migration") as start:
            d.apply_btn.click()
        start.assert_not_called()
        self.assertEqual(self.w.world_edit.text(), "世界A")
        self.assertEqual(self.lines(self.w.mod_text), ["a.jar", "b.jar"])
        self.assertEqual(d.result(), QtWidgets.QDialog.Accepted)       # 应用成功就关窗
        self.assertEqual(self.w.apply_pill.text(), "↩ 还原到应用前")

    def test_failed_apply_keeps_the_dialog_open(self):
        history_store.save_record(_record(id="r1", epoch=1.0,
                                          lists={"mods": [], "configs": [], "extras": []}))
        d = self.open_dialog()
        d.show()
        d.list_widget.setCurrentRow(0)
        with mock.patch.object(self.w, "toast"):
            d.apply_btn.click()
        self.assertTrue(d.isVisible())                                 # 没清单可回填:窗口留着

    def test_selected_record_is_the_one_applied_not_the_first(self):
        history_store.save_record(_record(id="a", epoch=1.0, world="第一份"))
        history_store.save_record(_record(id="b", epoch=2.0, world="第二份"))
        d = self.open_dialog()                                         # 倒序:第二份在第 0 行
        d.list_widget.setCurrentRow(1)
        d.apply_btn.click()
        self.assertEqual(self.w.world_edit.text(), "第一份")

    def test_apply_is_disabled_while_a_rollback_runs(self):
        history_store.save_record(_record(id="r1", epoch=1.0))
        d = self.open_dialog()
        d.list_widget.setCurrentRow(0)
        d._rb_thread = object()                                        # 假装回滚在跑
        d._set_buttons_for(d._current())
        self.assertFalse(d.apply_btn.isEnabled())
        # 主窗口那头(_is_busy 经 _rollback_busy 钩子)本来就会拦;这里把钩子摘掉,
        # 单独验证历史窗自己这一层守卫 —— 否则去掉它测试也看不出来
        self.w._rollback_busy = None
        self.assertFalse(self.w._is_busy())
        d._apply_current()
        self.assertNotEqual(self.w.world_edit.text(), "世界A")
        self.assertNotEqual(d.result(), QtWidgets.QDialog.Accepted)
        d._rb_thread = None


if __name__ == "__main__":
    unittest.main()

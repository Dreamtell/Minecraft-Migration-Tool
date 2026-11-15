"""Qt 主窗口补齐 Tk 版「输入侧」的三块:路径有效性状态、文件夹多选、模组/config 添加的反馈与规整。

配置和历史目录都指到临时目录;被测的整合包实例也都是临时目录里现造的,不碰用户的真实数据。
"""
import ast
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-input-parity-")
CFG = Path(_tmp.name) / "config.json"
CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")
os.environ["MCTOOL_CONFIG"] = str(CFG)
os.environ["MCTOOL_LANG"] = "zh"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Minecraft迁移工具"))

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


def make_pack(base, *, mods=("A.jar",), saves=False, versions=("1.20.1",), options=False,
              pcl=False, launcher=False, config=True, mods_dir=True):
    """在 base 下造一个整合包实例;参数控制缺什么。"""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    if mods_dir:
        (base / "mods").mkdir(exist_ok=True)
        for m in mods:
            (base / "mods" / m).write_text("x")
    if config:
        (base / "config").mkdir(exist_ok=True)
    if saves:
        (base / "saves").mkdir(exist_ok=True)
    for v in versions:
        (base / "versions" / v).mkdir(parents=True, exist_ok=True)
        (base / "versions" / v / "version.json").write_text("{}")
    if options:
        (base / "options.txt").write_text("x")
    if pcl:
        (base / "PCL.ini").write_text("x")
    if launcher:
        (base / "launcher_profiles.json").write_text("{}")
    return base


class InstanceCheckTests(unittest.TestCase):
    """core.instance_check:逐条对应 Tk 版 _is_valid_instance 的判定。"""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory(prefix="mctool-inst-")
        self.addCleanup(self.d.cleanup)
        self.root = Path(self.d.name)

    def check(self, p):
        from core.instance_check import is_valid_instance
        return is_valid_instance(str(p))

    def test_valid_pack(self):
        ok, why, d = self.check(make_pack(self.root / "p", saves=True))
        self.assertTrue(ok)
        self.assertTrue(d["is_valid"])
        self.assertEqual(d["mod_count"], 1)
        self.assertTrue(d["has_saves"])

    def test_each_failure_reason(self):
        self.assertEqual(self.check(self.root / "nope")[1], "路径不存在")
        f = self.root / "file.txt"
        f.write_text("x")
        self.assertEqual(self.check(f)[1], "不是目录")
        self.assertEqual(self.check(make_pack(self.root / "a", mods_dir=False))[1],
                         "缺少 mods 目录（不是有效的整合包）")
        self.assertEqual(self.check(make_pack(self.root / "b", config=False))[1],
                         "缺少 config 目录（不是有效的整合包）")
        self.assertEqual(self.check(make_pack(self.root / "c", mods=()))[1],
                         "mods 目录为空（没有模组文件）")
        self.assertEqual(self.check(make_pack(self.root / "d", versions=()))[1],
                         "缺少 version.json 或 options.txt，无法识别为有效实例")

    def test_old_pack_with_options_but_no_version_json_is_valid(self):
        ok, _w, _d = self.check(make_pack(self.root / "old", versions=(), options=True))
        self.assertTrue(ok)

    def test_empty_mods_fails_before_version_check(self):
        """顺序:先看 mods 为空,再看版本;两个都缺时报的是前者(同 Tk)。"""
        self.assertEqual(self.check(make_pack(self.root / "e", mods=(), versions=()))[1],
                         "mods 目录为空（没有模组文件）")

    def test_unwritable_target_is_reported(self):
        p = make_pack(self.root / "ro")
        with mock.patch("core.instance_check.check_writable", side_effect=PermissionError):
            ok, why, d = self.check(p)
        self.assertFalse(ok)
        self.assertEqual(why, "无读写权限，请以管理员身份运行")
        self.assertFalse(d["read_write"])

    def test_loader_and_launcher_detection(self):
        p = make_pack(self.root / "l", mods=("sodium-fabric-0.5.jar", "x-forge-1.jar"),
                      pcl=True, launcher=True)
        _ok, _w, d = self.check(p)
        self.assertTrue(d["fabric_mods"])
        self.assertTrue(d["forge_mods"])
        self.assertTrue(d["has_pcl_ini"])
        self.assertTrue(d["has_launcher_profiles"])

    def test_chinese_path_is_flagged_but_still_valid(self):
        ok, _w, d = self.check(make_pack(self.root / "我的整合包"))
        self.assertTrue(ok)
        self.assertTrue(d["has_chinese"])

    def test_status_for_kinds_and_tooltips(self):
        from core.instance_check import status_for
        self.assertEqual(status_for("", "源")[0], "muted")
        self.assertIn("源", status_for("", "源")[2])
        kind, shown, tip = status_for(str(make_pack(self.root / "g", saves=True)), "源")
        self.assertEqual((kind, shown), ("ok", "✅"))
        self.assertIn("· 有 saves/ 存档目录", tip)
        self.assertIn("· 模组 1 个", tip)
        self.assertIn("· 版本：1.20.1", tip)
        kind, shown, tip = status_for(str(make_pack(self.root / "h", config=False)), "源")
        self.assertEqual((kind, shown), ("fail", "❌"))
        self.assertIn("· 原因：缺少 config 目录", tip)
        kind, shown, tip = status_for(str(make_pack(self.root / "中文")), "源")
        self.assertEqual((kind, shown), ("warn", "⚠️"))
        self.assertIn("路径含中文", tip)

    def test_status_reason_is_translated_in_english(self):
        from utils import i18n
        from core.instance_check import status_for
        i18n.set_language_qt("en")
        i18n.install_qt()
        self.addCleanup(lambda: (i18n.set_language_qt("zh"), i18n.uninstall()))
        _k, _s, tip = status_for(str(make_pack(self.root / "q", mods=())), "源")
        self.assertIn("The mods folder is empty", tip)
        self.assertNotIn("没有模组文件", tip)

    def test_matches_tk_rules_on_the_same_directories(self):
        """两份规则不许分叉:同一批目录,Tk 的判定和这里逐项相等(Tk 模块只在测试里读源码比对,
        不 import——它顶层 import tkinter)。这里抽出 Tk 方法的源码,在隔离命名空间里执行。"""
        src = (ROOT / "Minecraft迁移工具" / "ui" / "mw_paths.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for c in tree.body if isinstance(c, ast.ClassDef)
                  for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "_is_valid_instance")
        ns = {"Path": Path, "check_writable": __import__("utils.file_io", fromlist=["x"]).check_writable}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "mw_paths", "exec"), ns)
        tk_rule = lambda p: ns["_is_valid_instance"](None, str(p))   # noqa: E731
        cases = [
            make_pack(self.root / "t1"), make_pack(self.root / "t2", saves=True, pcl=True),
            make_pack(self.root / "t3", mods_dir=False), make_pack(self.root / "t4", config=False),
            make_pack(self.root / "t5", mods=()), make_pack(self.root / "t6", versions=()),
            make_pack(self.root / "t7", versions=(), options=True),
            make_pack(self.root / "t8", mods=("a-fabric.jar", "b-forge.jar"), launcher=True),
            make_pack(self.root / "中文t9"), self.root / "missing",
        ]
        for p in cases:
            ours = self.check(p)
            theirs = tk_rule(p)
            self.assertEqual(ours[0], theirs[0], p)
            self.assertEqual(ours[1], theirs[1], p)
            self.assertEqual(ours[2], theirs[2], p)

    def test_module_does_not_import_tkinter(self):
        for name in ("instance_check", "list_ops"):
            src = (ROOT / "Minecraft迁移工具" / "core" / f"{name}.py").read_text(encoding="utf-8")
            mods = set()
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods.add(node.module.split(".")[0])
            self.assertNotIn("tkinter", mods, name)
            self.assertNotIn("ui", mods, name)


class ListOpsTests(unittest.TestCase):
    def test_entry_key(self):
        from core.list_ops import entry_key
        self.assertEqual(entry_key(" JEI/ "), "jei")
        self.assertEqual(entry_key("a\\B\\"), "a/b")
        self.assertEqual(entry_key("jei"), entry_key("jei/"))

    def test_normalize_collapses_paths_and_dedupes_keeping_notes(self):
        from core.list_ops import normalize_mod_lines
        text = "A.jar\nC:/x/mods/A.jar\n# 注释 c:/a/b\n\nB.jar\nb.JAR\n"
        out, changed = normalize_mod_lines(text)
        self.assertEqual(out.split("\n"), ["A.jar", "# 注释 c:/a/b", "", "B.jar", ""])
        # 计数口径与 Tk 版相同(见下一条对账):`C:/x/mods/A.jar` 先因「带路径」算 1 次,
        # 收成 A.jar 后又因与第一行重名被删,再算 1 次;`b.JAR` 与 B.jar 重名算 1 次 → 3
        self.assertEqual(changed, 3)

    def test_normalize_matches_tk_except_that_comment_lines_are_preserved(self):
        """和 Tk 的 `_normalize_mod_lines` 对账。唯一有意的不同:`# 注释` 行原样保留。
        Tk 把含 `/` 的注释当成路径取文件名(`# 注释 c:/a/b` 会被改写成 `b`),这是 Tk 的 bug,
        本批不碰 Tk,只在 Qt 侧做对。"""
        src = (ROOT / "Minecraft迁移工具" / "ui" / "mw_lists.py").read_text(encoding="utf-8")
        cls = next(c for c in ast.parse(src).body if isinstance(c, ast.ClassDef))
        fns = [n for n in cls.body if isinstance(n, ast.FunctionDef)
               and n.name in {"_模组条目名", "_normalize_mod_lines"}]
        ns = {"Path": Path}
        exec(compile(ast.Module(body=fns, type_ignores=[]), "mw_lists", "exec"), ns)

        class Fake:
            _模组条目名 = staticmethod(ns["_模组条目名"])
        tk = lambda t: ns["_normalize_mod_lines"](Fake(), t)   # noqa: E731
        from core.list_ops import normalize_mod_lines
        for text in ("A.jar\nC:/x/mods/A.jar\nB.jar\nb.JAR", "C:/old/mods/A.jar\nB.jar",
                     "A.jar\nA.jar", "x.jar", "A.jar\nB.jar\nC.jar"):
            self.assertEqual(normalize_mod_lines(text), tk(text), text)
        self.assertEqual(tk("A.jar\n# 注释 c:/a/b\nB.jar")[0], "A.jar\nb\nB.jar")           # Tk 的毛病
        self.assertEqual(normalize_mod_lines("A.jar\n# 注释 c:/a/b\nB.jar")[0],
                         "A.jar\n# 注释 c:/a/b\nB.jar")                                   # Qt 保留

    def test_merge_adds_only_new_jars_case_insensitively(self):
        from core.list_ops import merge_mods
        text, added, tidied = merge_mods("A.jar\n", ["/x/a.JAR", "/x/B.jar", "/x/readme.txt", "/y/B.jar"])
        self.assertEqual(added, ["B.jar"])
        self.assertEqual(text, "A.jar\nB.jar")
        self.assertEqual(tidied, 0)

    def test_merge_to_empty_has_no_leading_blank_line(self):
        from core.list_ops import merge_mods
        text, added, _t = merge_mods("", ["/m/A.jar"])
        self.assertEqual(text, "A.jar")
        self.assertEqual(added, ["A.jar"])

    def test_merge_nothing_new_returns_content_unchanged_except_tidy(self):
        from core.list_ops import merge_mods
        text, added, tidied = merge_mods("A.jar", ["/m/A.jar"])
        self.assertEqual((text, added, tidied), ("A.jar", [], 0))

    def test_merge_tidies_old_full_path_lines(self):
        from core.list_ops import merge_mods
        text, added, tidied = merge_mods("C:/old/mods/A.jar\nB.jar", ["/m/C.jar"])
        self.assertEqual(text.split("\n"), ["A.jar", "B.jar", "C.jar"])
        self.assertEqual(tidied, 1)

    def test_mark_config_folders(self):
        from core.list_ops import mark_config_folders
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "jei").mkdir()
            (base / "a.toml").write_text("x")
            out, ch = mark_config_folders(base, "jei\na.toml\n# jei\n\n../jei\njei/")
            self.assertTrue(ch)
            self.assertEqual(out.split("\n"), ["jei/", "a.toml", "# jei", "", "../jei", "jei/"])
            again, ch2 = mark_config_folders(base, out)           # 幂等
            self.assertFalse(ch2)
            self.assertEqual(again, out)
            self.assertEqual(mark_config_folders(base / "no", "jei"), ("jei", False))

    def test_mark_config_folders_does_not_touch_filesystem_for_unsafe_paths(self):
        from core.list_ops import mark_config_folders
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "config"
            base.mkdir()
            (Path(d) / "secret").mkdir()
            out, ch = mark_config_folders(base, "../secret\n/abs\nC:\\x")
            self.assertFalse(ch)
            self.assertEqual(out, "../secret\n/abs\nC:\\x")


class FolderPickerTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(prefix="mctool-fp-")
        self.addCleanup(self.d.cleanup)
        self.base = Path(self.d.name)
        for rel in ("a/x/y", "a/z", "b", "c/d"):
            (self.base / rel).mkdir(parents=True)
        (self.base / "file.txt").write_text("x")

    def dlg(self):
        from ui.qt_main.folder_picker import FolderPickerDialog
        dlg = FolderPickerDialog(None, self.base)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def top(self, dlg):
        root = dlg.tree.invisibleRootItem()
        return {root.child(i).text(0): root.child(i) for i in range(root.childCount())}

    def test_drop_covered(self):
        from ui.qt_main.folder_picker import drop_covered
        self.assertEqual(drop_covered(["a/b", "a", "c", "a/b/c", "ab"]), ["a", "ab", "c"])
        self.assertEqual(drop_covered([]), [])
        self.assertEqual(drop_covered(["a\\b/", "a"]), ["a"])
        self.assertEqual(drop_covered(["a", "ab"]), ["a", "ab"])      # 前缀相同但不是祖先,不能误删

    def test_lists_only_folders_sorted_and_lazy(self):
        dlg = self.dlg()
        top = self.top(dlg)
        self.assertEqual(list(top), ["a", "b", "c"])                  # file.txt 不在
        a = top["a"]
        self.assertEqual(a.childCount(), 1)                           # 占位
        self.assertEqual(a.child(0).text(0), "…")
        self.assertEqual(top["b"].childCount(), 0)                    # 没有子文件夹 → 没有展开箭头
        a.setExpanded(True)
        self.assertEqual([a.child(i).text(0) for i in range(a.childCount())], ["x", "z"])
        self.assertEqual(a.child(0).text(1), "a/x")

    def test_tick_untick_and_count(self):
        dlg = self.dlg()
        top = self.top(dlg)
        top["b"].setCheckState(0, QtCore.Qt.Checked)
        top["c"].setCheckState(0, QtCore.Qt.Checked)
        self.assertEqual(dlg.picked(), ["b", "c"])
        self.assertIn("2", dlg.count_label.text())
        top["b"].setCheckState(0, QtCore.Qt.Unchecked)
        self.assertEqual(dlg.picked(), ["c"])

    def test_parent_covers_child_in_result(self):
        dlg = self.dlg()
        top = self.top(dlg)
        top["a"].setExpanded(True)
        top["a"].child(0).setCheckState(0, QtCore.Qt.Checked)         # a/x
        top["a"].setCheckState(0, QtCore.Qt.Checked)
        self.assertEqual(dlg.picked(), ["a"])

    def test_select_top_and_clear_all_sync_the_boxes(self):
        dlg = self.dlg()
        top = self.top(dlg)
        top["a"].setExpanded(True)
        top["a"].child(0).setCheckState(0, QtCore.Qt.Checked)
        dlg.select_top()
        self.assertEqual(dlg.picked(), ["a", "b", "c"])               # 子项被顶层覆盖
        self.assertTrue(all(top[k].checkState(0) == QtCore.Qt.Checked for k in "abc"))
        self.assertEqual(top["a"].child(0).checkState(0), QtCore.Qt.Unchecked)   # 之前勾的子项被替换掉
        dlg.clear_all()
        self.assertEqual(dlg.picked(), [])
        self.assertTrue(all(top[k].checkState(0) == QtCore.Qt.Unchecked for k in "abc"))

    def test_expanding_does_not_count_as_ticking(self):
        dlg = self.dlg()
        self.top(dlg)["a"].setExpanded(True)
        self.assertEqual(dlg.picked(), [])

    def test_ticked_state_survives_collapse_and_reexpand(self):
        dlg = self.dlg()
        a = self.top(dlg)["a"]
        a.setExpanded(True)
        a.child(1).setCheckState(0, QtCore.Qt.Checked)                # a/z
        a.setExpanded(False)
        a.setExpanded(True)
        self.assertEqual(dlg.picked(), ["a/z"])

    def test_pick_folders_returns_none_on_cancel_or_empty(self):
        from ui.qt_main import folder_picker as fp
        with mock.patch.object(fp.FolderPickerDialog, "exec", return_value=QtWidgets.QDialog.Rejected):
            self.assertIsNone(fp.pick_folders(None, self.base))
        with mock.patch.object(fp.FolderPickerDialog, "exec", return_value=QtWidgets.QDialog.Accepted):
            self.assertIsNone(fp.pick_folders(None, self.base))       # 没勾任何 → None

    def test_unreadable_base_does_not_crash(self):
        from ui.qt_main.folder_picker import FolderPickerDialog, child_dirs
        self.assertEqual(child_dirs(self.base / "nope"), [])
        dlg = FolderPickerDialog(None, self.base / "nope")
        self.addCleanup(dlg.deleteLater)
        self.assertEqual(dlg.picked(), [])


class _WinBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        self.pack = tempfile.TemporaryDirectory(prefix="mctool-pack-")
        self.addCleanup(self.pack.cleanup)
        self.root = Path(self.pack.name)
        self.src = make_pack(self.root / "src", mods=("A.jar", "B.jar"))
        (self.src / "config" / "jei").mkdir()
        (self.src / "config" / "jei" / "x.toml").write_text("x")
        (self.src / "config" / "sodium.json").write_text("x")
        for d in ("shaderpacks", "resourcepacks"):
            (self.src / d).mkdir()
        self.w = self.W()
        self.w.show()
        pump(30)

    def tearDown(self):
        self.w._migration_running = False
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)

    def lines(self, box):
        return [x for x in box.toPlainText().splitlines() if x.strip()]


class PathStatusTests(_WinBase):
    def test_labels_exist_and_start_muted(self):
        for lab in (self.w.source_status, self.w.target_status):
            self.assertEqual(lab.property("_semantic"), "muted")
            self.assertEqual(lab.text(), "（未选择）")

    def test_source_change_updates_icon_and_tooltip(self):
        self.w.source_edit.setText(str(self.src))
        self.assertEqual(self.w.source_status.text(), "✅")
        self.assertEqual(self.w.source_status.property("_semantic"), "ok")
        self.assertIn(str(self.src), self.w.source_status.toolTip())
        self.assertEqual(self.w.target_status.text(), "（未选择）")        # 目标没动

    def test_target_change_updates_its_own_icon(self):
        self.w.target_edit.setText(str(self.root / "nope"))
        self.assertEqual(self.w.target_status.text(), "❌")
        self.assertIn("路径不存在", self.w.target_status.toolTip())
        self.assertEqual(self.w.source_status.text(), "（未选择）")

    def test_clearing_the_path_returns_to_muted(self):
        self.w.source_edit.setText(str(self.src))
        self.w.source_edit.setText("")
        self.assertEqual(self.w.source_status.property("_semantic"), "muted")

    def test_validation_error_does_not_break_typing(self):
        with mock.patch("core.instance_check.is_valid_instance", side_effect=RuntimeError("boom")):
            self.w.source_edit.setText(str(self.src))
        self.assertEqual(self.w.source_status.text(), "❌")
        self.assertEqual(self.w.source_path, str(self.src))           # 路径本身照常记下

    def test_theme_switch_recolors_without_rescanning(self):
        self.w.source_edit.setText(str(self.src))
        before = self.w.source_status.styleSheet()
        with mock.patch("core.instance_check.is_valid_instance") as scan:
            self.w._toggle_theme()
            pump(20)
        scan.assert_not_called()                                      # 换色不能重新扫目录
        self.assertEqual(self.w.source_status.text(), "✅")           # 状态保持
        self.assertNotEqual(self.w.source_status.styleSheet(), before)

    def test_startup_validates_the_saved_paths(self):
        d = json.loads(CFG.read_text(encoding="utf-8"))
        d.update(source=str(self.src), target=str(self.root / "gone"))
        CFG.write_text(json.dumps(d), encoding="utf-8")
        try:
            w2 = self.W()
            self.addCleanup(lambda: (w2._close_session_log(), w2.deleteLater()))
            self.assertEqual(w2.source_status.text(), "✅")
            self.assertEqual(w2.target_status.text(), "❌")
        finally:
            CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")

    def test_icons_are_not_locked_when_busy(self):
        """忙态把路径框设成只读,但状态图标只是标签,不该受影响。"""
        self.w._scanning = True
        self.w._refresh_busy_state()
        self.w.source_edit.setReadOnly(False)                         # 模拟外部写入
        self.w.source_edit.setText(str(self.src))
        self.assertEqual(self.w.source_status.text(), "✅")
        self.w._scanning = False
        self.w._refresh_busy_state()


class AddModsTests(_WinBase):
    def test_add_via_dialog_dedupes_and_starts_in_mods_dir(self):
        self.w.source_edit.setText(str(self.src))
        picked = [str(self.src / "mods" / "A.jar"), str(self.src / "mods" / "B.jar")]
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileNames",
                               return_value=(picked, "")) as dlg:
            self.w._add_mods()
        self.assertEqual(dlg.call_args[0][2], str(self.src / "mods"))   # 默认定位到 mods
        self.assertEqual(self.lines(self.w.mod_text), ["A.jar", "B.jar"])
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileNames",
                               return_value=([picked[0].lower()], "")), \
                mock.patch.object(self.w, "toast") as toast:
            self.w._add_mods()
        self.assertEqual(self.lines(self.w.mod_text), ["A.jar", "B.jar"])   # 没重复
        self.assertTrue(any("已在清单中" in str(c.args[0]) for c in toast.call_args_list))

    def test_dialog_falls_back_to_source_root_without_mods_dir(self):
        bare = self.root / "bare"
        bare.mkdir()
        self.w.source_edit.setText(str(bare))
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileNames", return_value=([], "")) as dlg:
            self.w._add_mods()
        self.assertEqual(dlg.call_args[0][2], str(bare))

    def test_add_is_one_undo_step(self):
        self.w.mod_text.reset_undo()
        self.w._append_mods([self.src / "mods" / "A.jar", self.src / "mods" / "B.jar"])
        self.assertEqual(self.lines(self.w.mod_text), ["A.jar", "B.jar"])
        self.w.mod_text.undo()
        self.assertEqual(self.lines(self.w.mod_text), [])

    def test_old_full_path_lines_are_tidied_on_add(self):
        self.w._replace_text(self.w.mod_text, "C:/old/mods/A.jar\nB.jar")
        with mock.patch.object(self.w, "toast") as toast:
            n = self.w._append_mods([self.src / "mods" / "C.jar"])
        self.assertEqual(n, 1)
        self.assertEqual(self.lines(self.w.mod_text), ["A.jar", "B.jar", "C.jar"])
        self.assertTrue(any("顺手整理了 1 行" in str(c.args[0]) for c in toast.call_args_list))

    def test_tidy_still_happens_when_nothing_new_is_added(self):
        self.w._replace_text(self.w.mod_text, "C:/old/mods/A.jar")
        self.assertEqual(self.w._append_mods([self.src / "mods" / "A.jar"]), 0)
        self.assertEqual(self.lines(self.w.mod_text), ["A.jar"])

    def test_non_jar_files_never_enter_the_list(self):
        (self.root / "readme.txt").write_text("x")
        self.assertEqual(self.w._append_mods([self.root / "readme.txt"]), 0)
        self.assertEqual(self.lines(self.w.mod_text), [])

    def test_drop_feedback_for_each_reason(self):
        txt = self.root / "a.txt"
        txt.write_text("x")
        empty_dir = self.root / "emptydir"
        empty_dir.mkdir()
        cases = [
            ([txt], "只认 .jar 文件（收到 1 个其它文件）"),
            ([empty_dir], "文件夹里没找到 .jar（1 个文件夹）"),
        ]
        for paths, expect in cases:
            with mock.patch.object(self.w, "toast") as toast:
                self.w._drop_mods([str(p) for p in paths])
            self.assertTrue(any(expect in str(c.args[0]) for c in toast.call_args_list), expect)
        self.w._append_mods([self.src / "mods" / "A.jar"])
        with mock.patch.object(self.w, "toast") as toast:
            self.w._drop_mods([str(self.src / "mods" / "A.jar")])
        self.assertTrue(any("已经在清单里了（1 个）" in str(c.args[0]) for c in toast.call_args_list))

    def test_drop_a_mods_folder_adds_jars_and_says_so(self):
        with mock.patch.object(self.w, "toast") as toast:
            self.w._drop_mods([str(self.src / "mods")])
        self.assertEqual(self.lines(self.w.mod_text), ["A.jar", "B.jar"])
        self.assertTrue(any("从文件夹里加了 2 个模组" in str(c.args[0]) for c in toast.call_args_list))

    def test_drop_only_expands_one_level(self):
        (self.src / "mods" / "sub").mkdir()
        (self.src / "mods" / "sub" / "deep.jar").write_text("x")
        self.w._drop_mods([str(self.src / "mods")])
        self.assertNotIn("deep.jar", self.w.mod_text.toPlainText())


class ConfigFolderTests(_WinBase):
    def test_adding_a_folder_gets_trailing_slash_and_files_do_not(self):
        self.w.source_edit.setText(str(self.src))
        n = self.w._add_paths_to("config", [str(self.src / "config" / "jei"),
                                            str(self.src / "config" / "sodium.json")])
        self.assertEqual(n, 2)
        self.assertEqual(self.lines(self.w.config_text), ["jei/", "sodium.json"])

    def test_same_folder_with_or_without_slash_is_not_added_twice(self):
        self.w.source_edit.setText(str(self.src))
        self.w._replace_text(self.w.config_text, "JEI")
        with mock.patch.object(self.w, "toast") as toast:
            n = self.w._add_paths_to("config", [str(self.src / "config" / "jei")])
        self.assertEqual(n, 0)
        self.assertEqual(self.lines(self.w.config_text), ["JEI"])
        self.assertTrue(any("均已在清单中" in str(c.args[0]) for c in toast.call_args_list))

    def test_outside_paths_are_skipped_and_not_called_duplicates(self):
        self.w.source_edit.setText(str(self.src))
        with mock.patch.object(self.w, "toast") as toast:
            n = self.w._add_paths_to("config", [str(self.root)])
        self.assertEqual(n, 0)
        self.assertFalse(any("均已在清单中" in str(c.args[0]) for c in toast.call_args_list))

    def test_extra_list_folders_also_get_slash(self):
        self.w.source_edit.setText(str(self.src))
        self.w._add_paths_to("extra", [str(self.src / "shaderpacks")])
        self.assertEqual(self.lines(self.w.extra_text), ["shaderpacks/"])

    def test_mark_runs_when_source_changes(self):
        self.w._replace_text(self.w.config_text, "jei\nsodium.json")
        self.w.source_edit.setText(str(self.src))
        self.assertEqual(self.lines(self.w.config_text), ["jei/", "sodium.json"])

    def test_mark_is_idempotent_and_leaves_undo_alone_when_unchanged(self):
        self.w.source_edit.setText(str(self.src))
        self.w._replace_text(self.w.config_text, "jei/\nsodium.json")
        self.w.config_text.reset_undo()
        self.w._mark_config_folders()
        self.assertFalse(self.w.config_text._undo_stack)              # 没改就不该多出撤销步

    def test_mark_is_one_undo_step(self):
        self.w.source_edit.setText(str(self.src))
        self.w._replace_text(self.w.config_text, "jei")
        self.w.config_text.reset_undo()
        self.w._mark_config_folders()
        self.assertEqual(self.lines(self.w.config_text), ["jei/"])
        self.w.config_text.undo()
        self.assertEqual(self.lines(self.w.config_text), ["jei"])

    def test_mark_skipped_while_mirror_window_has_the_list_locked(self):
        self.w.source_edit.setText(str(self.src))
        self.w._replace_text(self.w.config_text, "jei")
        self.w._edit_locks["bigview"] = True
        self.w._mark_config_folders()
        self.assertEqual(self.lines(self.w.config_text), ["jei"])
        self.w._edit_locks.clear()

    def test_check_config_marks_folders_first(self):
        self.w.source_edit.setText(str(self.src))
        self.w._replace_text(self.w.config_text, "jei")
        self.w._edit_locks.clear()
        with mock.patch.object(self.w, "toast"):
            self.w._check_list("config")
        self.assertEqual(self.lines(self.w.config_text), ["jei/"])

    def test_no_source_means_no_marking_and_no_crash(self):
        self.w._replace_text(self.w.config_text, "jei")
        self.w._mark_config_folders()
        self.assertEqual(self.lines(self.w.config_text), ["jei"])

    def test_folder_button_opens_the_picker_and_adds_the_ticked_ones(self):
        self.w.source_edit.setText(str(self.src))
        from ui.qt_main import folder_picker as fp
        with mock.patch.object(fp, "pick_folders", return_value=["jei"]) as pick:
            self.w._add_config_dir()
        self.assertEqual(pick.call_args[0][1], self.src / "config")   # 基准目录是 config
        self.assertEqual(self.lines(self.w.config_text), ["jei/"])

    def test_extra_folder_button_uses_pack_root_as_base(self):
        self.w.source_edit.setText(str(self.src))
        from ui.qt_main import folder_picker as fp
        with mock.patch.object(fp, "pick_folders", return_value=["shaderpacks", "resourcepacks"]) as pick:
            self.w._add_extra_dir()
        self.assertEqual(pick.call_args[0][1], self.src)
        self.assertEqual(self.lines(self.w.extra_text), ["shaderpacks/", "resourcepacks/"])

    def test_cancelled_picker_adds_nothing(self):
        self.w.source_edit.setText(str(self.src))
        from ui.qt_main import folder_picker as fp
        with mock.patch.object(fp, "pick_folders", return_value=None):
            self.w._add_config_dir()
        self.assertEqual(self.lines(self.w.config_text), [])

    def test_folder_button_without_source_warns_and_does_not_open_picker(self):
        from ui.qt_main import folder_picker as fp
        with mock.patch.object(fp, "pick_folders") as pick, \
                mock.patch.object(self.w, "toast") as toast:
            self.w._add_config_dir()
        pick.assert_not_called()
        self.assertTrue(any("请先选择源整合包实例根目录" in str(c.args[0]) for c in toast.call_args_list))

    def test_folder_button_with_missing_config_dir_logs_error(self):
        bare = self.root / "bare"
        bare.mkdir()
        self.w.source_edit.setText(str(bare))
        from ui.qt_main import folder_picker as fp
        with mock.patch.object(fp, "pick_folders") as pick, mock.patch.object(self.w, "log") as log:
            self.w._add_config_dir()
        pick.assert_not_called()
        self.assertTrue(any("源 config 目录不存在" in str(c.args[0]) for c in log.call_args_list))


class ScanSameDirTests(_WinBase):
    def test_same_source_and_target_is_refused_before_scanning(self):
        self.w.source_edit.setText(str(self.src))
        self.w.target_edit.setText(str(self.src))
        with mock.patch.object(QtWidgets.QMessageBox, "information") as info, \
                mock.patch("threading.Thread") as thread:
            self.w._scan_diff()
        info.assert_called_once()
        thread.assert_not_called()
        self.assertFalse(self.w._scanning)

    def test_same_dir_through_a_different_spelling_is_still_the_same(self):
        self.w.source_edit.setText(str(self.src))
        self.w.target_edit.setText(str(self.src / "mods" / ".."))
        with mock.patch.object(QtWidgets.QMessageBox, "information") as info, \
                mock.patch("threading.Thread"):
            self.w._scan_diff()
        info.assert_called_once()

    def test_different_dirs_still_scan(self):
        tgt = make_pack(self.root / "tgt")
        self.w.source_edit.setText(str(self.src))
        self.w.target_edit.setText(str(tgt))
        with mock.patch("threading.Thread") as thread:
            self.w._scan_diff()
        thread.assert_called_once()
        self.w._scanning = False


class MissingBeforeScanLogicTests(unittest.TestCase):
    """core.list_ops.missing_mod_rows:规则同 Tk 的 _mark_missing_before_scan。"""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory(prefix="mctool-miss-")
        self.addCleanup(self.d.cleanup)
        self.src = Path(self.d.name)
        (self.src / "mods").mkdir()
        for n in ("A.jar", "[CN] B.jar", "c-1.0.jar"):
            (self.src / "mods" / n).write_text("x")

    def rows(self, text, src=None):
        from core.list_ops import missing_mod_rows
        return missing_mod_rows(self.src if src is None else src, text)

    def test_finds_only_the_missing_ones_with_line_numbers(self):
        rows, names = self.rows("A.jar\nZ.jar\n# 注释\n\nc-1.0.jar\nY.jar")
        self.assertEqual(rows, [1, 5])
        self.assertEqual(names, ["Z.jar", "Y.jar"])

    def test_bracket_prefix_is_ignored_in_both_directions(self):
        self.assertEqual(self.rows("B.jar")[1], [])                 # 源里是 [CN] B.jar,清单写 B.jar
        self.assertEqual(self.rows("[X] A.jar")[1], [])             # 清单带前缀,源里没有

    def test_comments_and_blank_lines_are_never_missing(self):
        self.assertEqual(self.rows("# a\n   \n\n#b"), ([], []))

    def test_no_mods_dir_or_empty_mods_means_nothing_is_judged(self):
        self.assertEqual(self.rows("A.jar", self.src / "nope"), ([], []))
        empty = Path(self.d.name) / "empty"
        (empty / "mods").mkdir(parents=True)
        self.assertEqual(self.rows("A.jar\nZ.jar", empty), ([], []))   # 不能把整张清单都报成缺失

    def test_blank_source_is_safe(self):
        self.assertEqual(self.rows("A.jar", ""), ([], []))

    def test_whole_line_is_matched_so_old_full_path_lines_are_flagged(self):
        """和 Tk 一致:按整行匹配,不先取文件名。老式整条路径行会被标出来,让用户去整理。"""
        self.assertEqual(self.rows("C:/x/mods/A.jar")[1], ["C:/x/mods/A.jar"])

    def test_matches_tk_on_the_same_inputs(self):
        """抽出 Tk 方法的源码,在假控件上跑,逐项对账(不 import Tk 模块)。"""
        src = (ROOT / "Minecraft迁移工具" / "ui" / "mw_migration.py").read_text(encoding="utf-8")
        cls = next(c for c in ast.parse(src).body
                   if isinstance(c, ast.ClassDef) and c.name == "MigrationMixin")
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_mark_missing_before_scan")
        ns = {"Path": Path, "tk": mock.MagicMock(END="end"), "trace_exc": lambda *a, **k: None}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "mw_migration", "exec"), ns)

        class FakeText:
            def __init__(self, text):
                self.text, self.marked = text, []

            def get(self, *_a):
                return self.text

            def tag_remove(self, *_a):
                self.marked = []

            def tag_add(self, _tag, start, _end):
                self.marked.append(int(start.split(".")[0]) - 1)

        class FakeSelf:
            pass
        for text in ("A.jar\nZ.jar\n# x\n\nc-1.0.jar", "B.jar\n[X] A.jar", "C:/x/A.jar\nA.jar", ""):
            fs = FakeSelf()
            fs.mod_text = FakeText(text)
            tk_names = ns["_mark_missing_before_scan"](fs, str(self.src))
            ours_rows, ours_names = self.rows(text)
            self.assertEqual(ours_names, tk_names, text)
            self.assertEqual(ours_rows, fs.mod_text.marked, text)

class ScanFlowTests(_WinBase):
    def setUp(self):
        super().setUp()
        self.tgt = make_pack(self.root / "tgt", mods=("A.jar",))
        self.w.source_edit.setText(str(self.src))
        self.w.target_edit.setText(str(self.tgt))

    def run_scan(self, opened=None):
        """真跑一次后台扫描,等它结束;差异窗口换成记录器,免得在测试里真弹窗。"""
        if opened is None:
            opened = []
        with mock.patch.object(self.W, "_open_diff_view", lambda s, r: opened.append(r)):
            self.w._scan_diff()
            end = time.time() + 20
            while self.w._scanning and time.time() < end:
                pump(10)
            pump(60)
        return opened

    def test_scan_button_shows_progress_then_resets(self):
        labels = []
        orig = self.w._set_scan_label
        with mock.patch.object(self.w, "_set_scan_label", lambda t: (labels.append(t), orig(t))[1]):
            self.run_scan()
        self.assertEqual(labels[0], "⏳ 扫描中…")
        self.assertEqual(self.w._scan_button().text(), "🔍 扫描差异")
        self.assertFalse(self.w._scan_poll_timer.isActive())
        self.assertIsNone(self.w._scan_progress)

    def test_poll_turns_queued_progress_into_the_button_text(self):
        """3 个 jar 扫得比 16ms 还快,真跑拿不到中间态;这里手工往队列里放,单独验证取队列的逻辑。"""
        import queue
        self.w._scanning = True
        self.w._scan_total = 10
        self.w._scan_progress = q = queue.Queue()
        for i in (1, 2, 3):
            q.put((i, "x%d.jar" % i))
        self.w._poll_scan_progress()
        self.assertEqual(self.w._scan_button().text(), "⏳ 解析中 (3/10)")    # 同一轮只留最后一条
        q.put(None)                                                        # 「进度到头」不能把轮询停掉
        self.w._poll_scan_progress()
        self.assertEqual(self.w._scan_button().text(), "⏳ 解析中 (3/10)")
        self.assertTrue(self.w._scanning)
        self.w._scanning = False
        self.w._stop_scan_poll()

    def test_poll_without_a_known_total_uses_the_open_ended_text(self):
        import queue
        self.w._scanning = True
        self.w._scan_total = 0
        self.w._scan_progress = q = queue.Queue()
        q.put((1, "a.jar"))
        self.w._poll_scan_progress()
        self.assertEqual(self.w._scan_button().text(), "⏳ 解析中...")
        self.w._scanning = False
        self.w._stop_scan_poll()

    def test_poll_stops_itself_once_the_scan_is_over(self):
        self.w._scanning = False
        self.w._scan_poll_timer = QtCore.QTimer(self.w)
        self.w._scan_poll_timer.start(1000)
        self.w._poll_scan_progress()
        self.assertFalse(self.w._scan_poll_timer.isActive())

    def test_progress_total_counts_both_sides(self):
        self.run_scan()                      # 源 2 个 + 目标 1 个
        self.assertEqual(self.w._scan_total, 3)

    def test_result_rows_reach_the_diff_view(self):
        opened = self.run_scan()
        self.assertEqual([(r[0], r[1]) for r in opened[0]], [("B.jar", "新增")])

    def test_missing_entries_are_marked_and_logged_before_scanning(self):
        self.w._replace_text(self.w.mod_text, "A.jar\nZ.jar")
        with mock.patch.object(self.w, "log") as log:
            self.run_scan()
        self.assertTrue(any("1 项在源 mods 里找不到" in str(c.args[0]) for c in log.call_args_list))
        self.assertEqual(len(self.w.mod_text.extraSelections()), 1)

    def test_missing_marks_clear_when_the_text_changes(self):
        self.w._replace_text(self.w.mod_text, "A.jar\nZ.jar")
        self.w._mark_missing_before_scan(str(self.src))
        self.assertEqual(len(self.w.mod_text.extraSelections()), 1)
        self.w._replace_text(self.w.mod_text, "A.jar")
        self.assertEqual(len(self.w.mod_text.extraSelections()), 0)

    def test_missing_marks_clear_when_the_user_types(self):
        """S07:用户手打字不走 _replace_text,标记也得清掉(靠 textChanged 的那条连接)。"""
        self.w._replace_text(self.w.mod_text, "A.jar\nZ.jar")
        self.w._mark_missing_before_scan(str(self.src))
        self.assertEqual(len(self.w.mod_text.extraSelections()), 1)
        self.w.mod_text.setReadOnly(False)
        cur = self.w.mod_text.textCursor()
        cur.movePosition(QtGui.QTextCursor.End)
        self.w.mod_text.setTextCursor(cur)
        self.w.mod_text.insertPlainText("x")          # 和敲键盘一样走文档编辑,不经过 _replace_text
        self.assertEqual(len(self.w.mod_text.extraSelections()), 0)

    def test_scanner_really_receives_the_progress_queue_and_total(self):
        """S09:3 个 jar 扫得比 16ms 还快,看按钮文字抓不到;直接断言传给扫描器的参数。"""
        got = {}

        def fake_scan(src, tgt, progress_queue=None, total=0):
            got["queue"], got["total"] = progress_queue, total
            return []
        with mock.patch("core.scanner.scan_mod_differences", fake_scan), \
                mock.patch.object(QtWidgets.QMessageBox, "information"):
            self.w._scan_diff()
            end = time.time() + 10
            while self.w._scanning and time.time() < end:
                pump(10)
            pump(40)
        self.assertIsNotNone(got.get("queue"))
        self.assertEqual(got["total"], 3)

    def test_poll_timer_is_stopped_the_moment_a_scan_succeeds(self):
        """S13:成功路径要立刻停轮询并复位按钮,不能等下一拍轮询自己发现。

        不停也不会空转(轮询开头见 _scanning 为假会自停),但那样结束后按钮文字会多停留
        十几拍(实测约 240ms),窗口又会在这期间弹出差异窗口。所以直接调结果槽,
        不泵事件循环,看它返回时的状态。
        """
        self.w._scanning = True
        self.w._scan_progress = object()
        self.w._start_scan_poll()
        self.assertTrue(self.w._scan_poll_timer.isActive())
        self.w._set_scan_label("⏳ 解析中 (1/3)")
        with mock.patch.object(self.W, "_open_diff_view"):
            self.w._show_diff_result([("B.jar", "新增", "B.jar", 1.0, "", "?", "?", "?", "x")])
        self.assertFalse(self.w._scan_poll_timer.isActive())
        self.assertEqual(self.w._scan_button().text(), "🔍 扫描差异")
        self.assertFalse(self.w._scanning)

    def test_poll_timer_is_stopped_the_moment_a_scan_fails(self):
        self.w._scanning = True
        self.w._scan_progress = object()
        self.w._start_scan_poll()
        with mock.patch.object(QtWidgets.QMessageBox, "critical"):
            self.w._on_scan_failed("坏了")
        self.assertFalse(self.w._scan_poll_timer.isActive())
        self.assertEqual(self.w._scan_button().text(), "🔍 扫描差异")

    def test_nothing_missing_means_no_marks_and_no_warning(self):
        self.w._replace_text(self.w.mod_text, "A.jar")
        with mock.patch.object(self.w, "log") as log:
            self.run_scan()
        self.assertFalse(any("找不到" in str(c.args[0]) for c in log.call_args_list))
        self.assertEqual(len(self.w.mod_text.extraSelections()), 0)

    def test_scan_error_in_foreground_shows_a_dialog_and_resets(self):
        with mock.patch("core.scanner.scan_mod_differences", side_effect=RuntimeError("坏了")), \
                mock.patch.object(QtWidgets.QMessageBox, "critical") as crit:
            self.w._scan_diff()
            end = time.time() + 10
            while self.w._scanning and time.time() < end:
                pump(10)
            pump(40)
        crit.assert_called_once()
        self.assertIn("坏了", crit.call_args[0][2])
        self.assertFalse(self.w._scanning)
        self.assertEqual(self.w._scan_button().text(), "🔍 扫描差异")

    def test_scan_error_while_in_tray_notifies_instead_of_popping_a_dialog(self):
        self.w._hidden_in_tray = True
        sent = []
        self.w._notify = lambda title, msg: sent.append((title, msg))
        with mock.patch.object(QtWidgets.QMessageBox, "critical") as crit:
            self.w._on_scan_failed("坏了")
        crit.assert_not_called()
        self.assertEqual(len(sent), 1)
        self.assertIn("坏了", sent[0][1])

    def test_no_difference_in_foreground_informs_with_a_dialog(self):
        with mock.patch.object(QtWidgets.QMessageBox, "information") as info:
            self.w._show_diff_result([])
        info.assert_called_once()

    def test_no_difference_in_tray_notifies_and_does_not_pop_a_dialog(self):
        self.w._hidden_in_tray = True
        sent = []
        self.w._notify = lambda title, msg: sent.append((title, msg))
        with mock.patch.object(QtWidgets.QMessageBox, "information") as info:
            self.w._show_diff_result([])
        info.assert_not_called()
        self.assertEqual(len(sent), 1)

    def test_result_in_tray_is_held_back_then_opened_when_the_window_returns(self):
        rows = [("B.jar", "新增", "B.jar", 1.0, "", "?", "?", "?", "x")]
        self.w._hidden_in_tray = True
        sent = []
        self.w._notify = lambda title, msg: sent.append((title, msg))
        opened = []
        with mock.patch.object(self.W, "_open_diff_view", lambda s, r: opened.append(r)):
            self.w._show_diff_result(rows)
            self.assertEqual(opened, [])                       # 压着,不弹
            self.assertIn("1 项差异", sent[0][1])
            self.w.show_from_tray()
            self.assertEqual(opened, [rows])                   # 叫回主界面才开
            self.w.show_from_tray()
            self.assertEqual(opened, [rows])                   # 只开一次

    def test_result_in_foreground_opens_immediately(self):
        rows = [("B.jar", "新增", "B.jar", 1.0, "", "?", "?", "?", "x")]
        opened = []
        with mock.patch.object(self.W, "_open_diff_view", lambda s, r: opened.append(r)):
            self.w._show_diff_result(rows)
        self.assertEqual(opened, [rows])

    def test_none_result_still_warns_in_foreground(self):
        with mock.patch.object(QtWidgets.QMessageBox, "warning") as warn:
            self.w._show_diff_result(None)
        warn.assert_called_once()

class DiffApplyTests(_WinBase):
    def test_apply_in_diff_view_appends_and_keeps_existing_entries(self):
        """用户决定保持「追加」(Tk 是整张替换):清单里原有的条目不能被覆盖。"""
        self.w._replace_text(self.w.mod_text, "Keep.jar")
        captured = {}

        class FakeView:
            def __init__(self, result, theme, hooks=None, apply_callback=None, **kw):
                captured["cb"] = apply_callback

            def setAttribute(self, *a):
                pass

            class _Sig:
                def connect(self, *a):
                    pass
            destroyed = _Sig()

            def show_centered(self):
                pass
        with mock.patch("ui.qt_diff_view.QtDiffView", FakeView):
            self.w._open_diff_view([("B.jar", "新增", "B.jar", 1.0, "", "?", "?", "?", "x")])
        captured["cb"](["B.jar"])
        self.assertEqual(self.lines(self.w.mod_text), ["Keep.jar", "B.jar"])
        self.w._unlock_edit("diff")


class ExtraConflictRowTests(_WinBase):
    def tearDown(self):
        CFG.write_text('{"language":"zh","theme":"dark","net_float_on":false}', encoding="utf-8")
        super().tearDown()

    def test_row_exists_and_reflects_the_config(self):
        self.assertEqual(self.w.extra_conflict, "overwrite")
        self.assertTrue(self.w._conflict_btns["overwrite"].isChecked())
        self.assertFalse(self.w._conflict_btns["skip"].isChecked())
        self.assertEqual(self.w._conflict_btns["overwrite"].text(), "覆盖（先备份）")
        self.assertEqual(self.w._conflict_btns["skip"].text(), "跳过（目标保持不变）")

    def test_click_updates_the_value_and_persists_it(self):
        self.w._conflict_btns["skip"].click()
        self.assertEqual(self.w.extra_conflict, "skip")
        self.assertEqual(json.loads(CFG.read_text(encoding="utf-8")).get("extra_conflict"), "skip")
        self.w._conflict_btns["overwrite"].click()
        self.assertEqual(json.loads(CFG.read_text(encoding="utf-8")).get("extra_conflict"), "overwrite")

    def test_invalid_value_is_ignored(self):
        self.w._set_extra_conflict("nope")
        self.assertEqual(self.w.extra_conflict, "overwrite")

    def test_settings_dialog_change_moves_the_row(self):
        self.w._apply_settings({**self.w.config, "extra_conflict": "skip", "theme": "dark"})
        self.assertTrue(self.w._conflict_btns["skip"].isChecked())
        self.assertFalse(self.w._conflict_btns["overwrite"].isChecked())

    def test_sync_does_not_toast(self):
        with mock.patch.object(self.w, "toast") as toast:
            self.w.extra_conflict = "skip"
            self.w._sync_conflict_row()
        toast.assert_not_called()

    def test_clicking_the_current_value_does_not_toast_again(self):
        with mock.patch.object(self.w, "toast") as toast:
            self.w._set_extra_conflict("overwrite")
        toast.assert_not_called()

    def test_locked_while_busy(self):
        self.w._scanning = True
        self.w._refresh_busy_state()
        self.assertFalse(any(b.isEnabled() for b in self.w._conflict_btns.values()))
        self.w._scanning = False
        self.w._refresh_busy_state()
        self.assertTrue(all(b.isEnabled() for b in self.w._conflict_btns.values()))

    def _px(self, btn):
        """按钮上边缘中间那个点的颜色(选中态是 2px 描边 + 高亮底,没选中是普通按钮底)。"""
        img = btn.grab().toImage()
        return QtGui.QColor(img.pixel(img.width() // 2, 3)).name()

    def test_the_chosen_segment_looks_different_from_the_other_one(self):
        """全局样式表里没有 :checked 规则,不单独写的话选中和没选中一模一样,用户看不出选的是哪个。"""
        self.w.tabs.setCurrentIndex(self.w._extra_tab_index)
        pump(30)
        a, b = self.w._conflict_btns["overwrite"], self.w._conflict_btns["skip"]
        self.assertNotEqual(self._px(a), self._px(b))
        self.w._conflict_btns["skip"].click()
        pump(30)
        self.assertNotEqual(self._px(a), self._px(b))
        # 选中的换成了 skip:它现在应当是主题的高亮色,原来选中的 overwrite 退回普通按钮底色
        self.assertEqual(self._px(b).lower(), self.w.theme["highlight_bg"].lower())
        self.assertNotEqual(self._px(a).lower(), self.w.theme["highlight_bg"].lower())

    def test_chosen_segment_color_follows_the_theme(self):
        self.w.tabs.setCurrentIndex(self.w._extra_tab_index)
        pump(30)
        sel = self.w._conflict_btns["overwrite"]
        dark = self._px(sel)
        self.w._toggle_theme()
        pump(30)
        light = self._px(sel)
        self.assertNotEqual(dark, light)
        self.assertEqual(light.lower(), self.w.theme["highlight_bg"].lower())

class PageHintTests(_WinBase):
    def labels(self, role):
        return [l for l in self.w.findChildren(QtWidgets.QLabel) if l.property("_role") == role]

    def test_config_and_extra_pages_have_their_hints_and_config_has_the_warning(self):
        hints = [l.text() for l in self.labels("list_hint")]
        self.assertTrue(any("相对源实例的 config 目录" in t for t in hints))
        self.assertTrue(any("相对整合包根目录" in t and "不会自动带任何东西" in t for t in hints))
        warns = [l.text() for l in self.labels("list_warning")]
        self.assertEqual(len(warns), 1)
        self.assertIn("覆盖目标 config 中的同名文件", warns[0])

    def test_labels_are_recolored_when_the_theme_switches(self):
        warn = self.labels("list_warning")[0]
        hint = self.labels("list_hint")[0]
        before = (warn.styleSheet(), hint.styleSheet())
        self.w._toggle_theme()
        pump(20)
        self.assertNotEqual((warn.styleSheet(), hint.styleSheet()), before)
        self.assertIn("font-weight: bold", warn.styleSheet())

    def test_the_conflict_row_lives_on_the_extra_page_only(self):
        extra_page = self.w.tabs.widget(self.w._extra_tab_index)
        self.assertTrue(extra_page.isAncestorOf(self.w._conflict_btns["skip"]))
        mod_page = self.w.tabs.widget(self.w._mod_tab_index)
        self.assertFalse(mod_page.isAncestorOf(self.w._conflict_btns["skip"]))


class TrayNoticeTests(_WinBase):
    def test_in_tray_follows_the_hidden_flag(self):
        self.assertFalse(self.w._in_tray())
        self.w._hidden_in_tray = True
        self.assertTrue(self.w._in_tray())
        self.w._hidden_in_tray = False
        self.assertFalse(self.w._in_tray())


class LogWriteFailureNoticeTests(_WinBase):
    """界面上只留最近几千行,完整日志在磁盘文件里;写盘失败时用户得知道(同 Tk 的 `_冲写日志`)。"""

    def break_disk(self):
        """把日志目录指到一个「父路径是文件」的位置,真实触发 OSError,不去伪造状态。"""
        blocker = Path(_tmp.name) / ("blocked-%d" % time.time_ns())
        blocker.write_text("x")
        self.w._session_log.close()
        self.w._session_log.directory = blocker / "sub"

    def test_hidden_when_everything_is_fine(self):
        self.w.log("一行", "INFO")
        self.assertFalse(self.w.log_status_label.isVisible())
        self.assertEqual(self.w.log_status_label.text(), "")

    def test_shown_with_the_reason_when_the_disk_write_fails(self):
        self.break_disk()
        self.w.log("写不进去的一行", "INFO")
        self.assertTrue(self.w._session_log.error)                    # 真的是 SessionLog 报的错
        self.assertTrue(self.w.log_status_label.isVisible())
        self.assertIn("完整日志保存失败", self.w.log_status_label.text())
        self.assertIn(self.w._session_log.error, self.w.log_status_label.text())

    def test_the_line_is_still_shown_in_the_live_log_even_if_disk_fails(self):
        self.break_disk()
        self.w.log("第一行", "INFO")          # 写盘失败是在这一次 append 里才发生的
        self.assertTrue(self.w._session_log.error)
        self.w.log("仍然要显示在界面上", "INFO")   # 这时 error 已经有了:界面不能因此就不再显示新日志
        text = self.w.log_text.toPlainText()
        self.assertIn("第一行", text)
        self.assertIn("仍然要显示在界面上", text)

    def test_clears_again_once_the_error_is_gone(self):
        self.break_disk()
        self.w.log("坏的", "INFO")
        self.assertTrue(self.w.log_status_label.isVisible())
        self.w._session_log.error = None
        self.w.log("好了", "INFO")
        self.assertFalse(self.w.log_status_label.isVisible())

    def test_color_follows_the_theme(self):
        self.break_disk()
        self.w.log("坏的", "INFO")
        before = self.w.log_status_label.styleSheet()
        self.assertIn(self.w.theme["fail_fg"], before)
        self.w._toggle_theme()
        pump(20)
        after = self.w.log_status_label.styleSheet()
        self.assertNotEqual(before, after)
        self.assertIn(self.w.theme["fail_fg"], after)

    def test_log_buttons_are_still_all_there(self):
        """把提示行塞进日志区时不能把那排按钮挤丢(改版时就吃掉过 log_tools 一行)。"""
        for key in ("log_big", "log_open", "fail_locate", "log_clear"):
            self.assertIn(key, self.w._btn_widgets)
            self.assertIsNotNone(self.w._btn_widgets[key].parent())



if __name__ == "__main__":
    unittest.main()

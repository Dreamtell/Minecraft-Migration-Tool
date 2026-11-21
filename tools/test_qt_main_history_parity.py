"""Qt 历史窗补齐 Tk 版的展示:概览(分档统计、结果上色)、按级别上色的完整日志、头部、放大、确认文案。

历史目录跟着配置文件走,测试把它指到临时目录;不碰用户真实的迁移档案(~/migration_history)。
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
_tmp = tempfile.TemporaryDirectory(prefix="mctool-qt-history-parity-")
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
from utils import history_store  # noqa: E402

_cfg.CONFIG_FILE = CFG
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


def rec(**kw):
    r = {"id": "r1", "epoch": 1.0, "time": "2026-11-15 01:00:00", "mode": "dry", "result": "模拟运行",
         "source": r"C:\old", "target": r"C:\new", "world": "世界A", "duration": 1.5,
         "stats": {"mods": {"success": 374, "skipped": 3, "failed": 0},
                   "saves": {"success": 2406, "skipped": 0, "failed": 0},
                   "config": {"success": 61, "skipped": 0, "failed": 0},
                   "extra": {"success": 2, "skipped": 1, "failed": 0}},
         "lists": {"mods": ["a.jar"], "configs": [], "extras": [], "failed": []},
         "log_lines": [["INFO", "开始"], ["ERROR", "❌ 出错了"], ["SIMULATE", "[模拟] x"]]}
    r.update(kw)
    return r


class HistoryViewLogicTests(unittest.TestCase):
    """core.history_view:规则同 Tk 的 `显示` / `_日志级别标签`。"""

    def test_infer_level_follows_tk_order(self):
        from core.history_view import infer_level
        self.assertEqual(infer_level("❌ 失败"), "ERROR")
        self.assertEqual(infer_level("something ERROR here"), "ERROR")
        self.assertEqual(infer_level("⚠️ 注意"), "WARNING")
        self.assertEqual(infer_level("⏭️ 跳过"), "WARNING")
        self.assertEqual(infer_level("✅ 好"), "SUCCESS")
        self.assertEqual(infer_level("[模拟] 将复制"), "SIMULATE")
        self.assertEqual(infer_level("[SIMULATE] x"), "SIMULATE")
        self.assertEqual(infer_level("普通一行"), "INFO")
        self.assertEqual(infer_level(None), "INFO")
        # 顺序:同时出现 ❌ 和 ✅ 时错误优先
        self.assertEqual(infer_level("✅ 然后 ❌"), "ERROR")

    def test_infer_level_matches_tk_on_the_same_lines(self):
        """抽出 Tk 方法的源码对账(不 import Tk 模块)。Tk 返回的是标签名,这里换成级别名比。"""
        src = (ROOT / "Minecraft迁移工具" / "ui" / "mw_migration.py").read_text(encoding="utf-8")
        cls = next(c for c in ast.parse(src).body
                   if isinstance(c, ast.ClassDef) and c.name == "MigrationMixin")
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_日志级别标签")
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "mw_migration", "exec"), ns)
        tk = ns["_日志级别标签"]
        tag_to_level = {"log_error": "ERROR", "log_warning": "WARNING", "log_success": "SUCCESS",
                        "log_simulate": "SIMULATE", "log_info": "INFO"}
        from core.history_view import infer_level
        for line in ("❌ x", "ERROR x", "⚠️ x", "⏭️ x", "✅ x", "[模拟] x", "[SIMULATE] x", "plain",
                     "✅ ❌", "⚠️ ✅", "", "[模拟] ✅"):
            self.assertEqual(infer_level(line), tag_to_level[tk(line)], line)

    def test_new_archives_use_stored_levels(self):
        from core.history_view import log_lines
        lines, n = log_lines({"log_lines": [["INFO", "a"], ["warning", "b"], ["ERROR", "c"]]})
        self.assertEqual(lines, [("INFO", "a"), ("WARNING", "b"), ("ERROR", "c")])
        self.assertEqual(n, 3)

    def test_unknown_level_falls_back_to_info(self):
        from core.history_view import log_lines
        self.assertEqual(log_lines({"log_lines": [["WEIRD", "x"]]})[0], [("INFO", "x")])

    def test_old_archives_infer_levels_from_the_single_log_string(self):
        from core.history_view import log_lines
        lines, n = log_lines({"log": "ok\n❌ bad\n[模拟] s"})
        self.assertEqual(lines, [("INFO", "ok"), ("ERROR", "❌ bad"), ("SIMULATE", "[模拟] s")])
        self.assertEqual(n, 3)

    def test_log_lines_wins_over_log_when_both_exist(self):
        from core.history_view import log_lines
        lines, _n = log_lines({"log_lines": [["INFO", "new"]], "log": "old"})
        self.assertEqual(lines, [("INFO", "new")])

    def test_no_log_at_all(self):
        from core.history_view import log_lines
        self.assertEqual(log_lines({}), ([], 0))
        self.assertEqual(log_lines({"log_lines": [], "log": ""}), ([], 0))
        self.assertEqual(log_lines(None), ([], 0))

    def test_bad_entries_are_skipped_not_fatal(self):
        from core.history_view import log_lines
        lines, n = log_lines({"log_lines": [["INFO", "a"], "oops", [], None, ["ERROR", "b"]]})
        self.assertEqual(lines, [("INFO", "a"), ("ERROR", "b")])
        self.assertEqual(n, 5)                     # 行数显示的是档案里存了多少条,同 Tk 用 len(行们)

    def test_stat_rows_shape_and_defaults(self):
        from core.history_view import stat_rows
        rows = stat_rows(rec())
        self.assertEqual(rows, [("模组", 374, 3, 0), ("存档", 2406, None, 0),
                                ("config", 61, None, 0), ("其它", 2, 1, 0)])

    def test_old_archive_without_saves_shows_zero(self):
        from core.history_view import stat_rows
        old = {"stats": {"mods": {"success": 377, "skipped": 0, "failed": 0},
                         "config": {"success": 61, "skipped": 0, "failed": 0}}}
        self.assertEqual(stat_rows(old)[1], ("存档", 0, None, 0))
        self.assertEqual(stat_rows(old)[3], ("其它", 0, 0, 0))

    def test_garbage_stats_never_crash(self):
        from core.history_view import stat_rows
        for bad in (None, {}, {"stats": None}, {"stats": "x"}, {"stats": {"mods": "x"}},
                    {"stats": {"mods": {"success": "多", "failed": True}}}):
            rows = stat_rows(bad)
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(isinstance(r[1], int) for r in rows))
        self.assertEqual(stat_rows({"stats": {"mods": {"failed": True}}})[0][3], 0)   # bool 不算数字

    def test_result_kind(self):
        from core.history_view import result_kind
        self.assertEqual(result_kind({"result": "成功"}), "ok")
        self.assertEqual(result_kind({"result": "模拟运行"}), "warn")
        self.assertEqual(result_kind({"result": "失败"}), "fail")
        self.assertEqual(result_kind({}), "fail")
        self.assertEqual(result_kind(None), "fail")

    def test_failed_count(self):
        from core.history_view import failed_count
        self.assertEqual(failed_count({"lists": {"failed": ["a", "b"]}}), 2)
        for bad in (None, {}, {"lists": None}, {"lists": {"failed": None}}, {"lists": {"failed": "x"}}):
            self.assertEqual(failed_count(bad), 0)

    def test_real_archives_all_render_without_error(self):
        """用户机器上的真实档案(只读)也要都能走完整条逻辑;没有就跳过。"""
        import glob
        files = glob.glob(os.path.expanduser("~/migration_history/*.json"))
        if not files:
            self.skipTest("没有真实档案")
        from core.history_view import log_lines, stat_rows, result_kind, failed_count
        for f in files:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            lines, n = log_lines(d)
            self.assertEqual(len(stat_rows(d)), 4)
            result_kind(d), failed_count(d)
            self.assertGreaterEqual(n, len(lines))

    def test_module_does_not_import_tkinter_or_ui(self):
        src = (ROOT / "Minecraft迁移工具" / "core" / "history_view.py").read_text(encoding="utf-8")
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        self.assertNotIn("tkinter", mods)
        self.assertNotIn("ui", mods)


class _WinBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.qt_main.window import QtMainWindow
        cls.W = QtMainWindow

    def setUp(self):
        self._clean()
        self.w = self.W()
        self.w.show()
        pump(30)

    def _clean(self):
        root = history_store._root()
        for p in (root.glob("*.json") if root.exists() else []):
            p.unlink()

    def tearDown(self):
        self.w._migration_running = False
        self.w.hide()
        self.w._close_session_log()
        self.w.deleteLater()
        pump(30)
        self._clean()

    def dialog(self):
        from ui.qt_main.history import HistoryDialog
        d = HistoryDialog(self.w)
        self.addCleanup(d.deleteLater)
        return d


class HistoryDialogViewTests(_WinBase):
    def test_history_actions_have_semantic_button_kinds(self):
        d = self.dialog()
        self.assertEqual(d.zoom_btn.property("kind"), "neutral")
        self.assertEqual(d.rollback_btn.property("kind"), "danger")
        self.assertEqual(d.apply_btn.property("kind"), "neutral")
        self.assertEqual(d.delete_btn.property("kind"), "danger")
        self.assertEqual(d.clear_btn.property("kind"), "danger")

    def test_history_dir_is_the_temp_one(self):
        self.assertTrue(str(history_store._root()).startswith(str(Path(_tmp.name))))

    def test_header_shows_count_limit_and_folder(self):
        history_store.save_record(rec(id="a", epoch=1.0))
        history_store.save_record(rec(id="b", epoch=2.0))
        d = self.dialog()
        text = d.header.text()
        self.assertIn("共 2 份档案", text)
        self.assertIn("最多保存 %d 份" % history_store.get_limit(), text)
        self.assertIn(str(history_store._root()), text)

    def test_header_follows_delete(self):
        history_store.save_record(rec(id="a", epoch=1.0))
        history_store.save_record(rec(id="b", epoch=2.0))
        d = self.dialog()
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes):
            d._delete_current()
        self.assertIn("共 1 份档案", d.header.text())

    def test_empty_state(self):
        d = self.dialog()
        self.assertIn("共 0 份档案", d.header.text())
        self.assertIn("还没有任何迁移记录", d.detail.toPlainText())
        self.assertEqual(d.log_view.toPlainText(), "")
        self.assertEqual(d.log_count.text(), "")

    def test_overview_has_the_four_stat_rows_with_colored_numbers(self):
        history_store.save_record(rec())
        d = self.dialog()
        html = d.detail.toHtml()
        text = d.detail.toPlainText()
        for needle in ("源目录", "目标目录", "模组", "存档", "config", "其它", "耗时", "失败"):
            self.assertIn(needle, text, needle)
        self.assertIn("374", text)
        self.assertIn("2406", text)
        self.assertIn("跳过", text)
        self.assertIn(d._theme()["data_num_fg"].lower(), html.lower())          # 数字用数据色

    def span_colors(self, d):
        """概览里每一段带颜色的文字 → [(文字, 颜色小写)]。按 Qt 解析后的字符格式取,不靠字符串匹配 HTML。"""
        out = []
        blk = d.detail.document().begin()
        while blk.isValid():
            it = blk.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.text().strip():
                    out.append((frag.text().strip(), frag.charFormat().foreground().color().name().lower()))
                it += 1
            blk = blk.next()
        return out

    def color_of(self, d, text):
        for t, c in self.span_colors(d):
            if t == text:
                return c
        self.fail("概览里找不到文字 %r;实际有:%r" % (text, [t for t, _ in self.span_colors(d)]))

    def test_overview_numbers_use_the_data_color_and_skipped_uses_the_warning_color(self):
        history_store.save_record(rec())
        d = self.dialog()
        t = d._theme()
        self.assertEqual(self.color_of(d, "374"), t["data_num_fg"].lower())      # 成功数 → 数据色
        self.assertEqual(self.color_of(d, "2406"), t["data_num_fg"].lower())
        self.assertEqual(self.color_of(d, "3"), t["log_warning_fg"].lower())     # 跳过 3 → 警示色
        self.assertEqual(self.color_of(d, "1.5 秒"), t["data_num_fg"].lower())   # 耗时 → 数据色

    def test_zero_skipped_and_zero_failed_are_not_alarming(self):
        history_store.save_record(rec(stats={"mods": {"success": 5, "skipped": 0, "failed": 0}}))
        d = self.dialog()
        t = d._theme()
        colors = [c for tx, c in self.span_colors(d) if tx == "0"]
        self.assertTrue(colors)
        self.assertNotIn(t["log_warning_fg"].lower(), colors)                    # 跳过 0 → 灰,不是警示色
        self.assertNotIn(t["fail_fg"].lower(), colors)                           # 失败 0 → 绿,不是红

    def test_nonzero_failed_is_red(self):
        history_store.save_record(rec(stats={"mods": {"success": 5, "skipped": 0, "failed": 2}}))
        d = self.dialog()
        self.assertEqual(self.color_of(d, "2"), d._theme()["fail_fg"].lower())

    def test_result_text_color_matches_its_kind(self):
        t = None
        for result, key in (("成功", "ok_fg"), ("模拟运行", "log_warning_fg"), ("失败", "fail_fg")):
            self._clean()
            history_store.save_record(rec(result=result, mode="real"))
            d = self.dialog()
            t = d._theme()
            self.assertEqual(self.color_of(d, result), t[key].lower(), result)

    def test_overview_title_and_result_color(self):
        history_store.save_record(rec(result="成功", mode="real"))
        d = self.dialog()
        self.assertIn("2026-11-15 01:00:00", d.detail.toPlainText())
        self.assertIn("实际迁移", d.detail.toPlainText())

    def test_failed_row_shows_count_in_red_or_none_in_green(self):
        history_store.save_record(rec(lists={"mods": [], "configs": [], "extras": [],
                                           "failed": ["x.jar", "y.jar"]}))
        d = self.dialog()
        self.assertIn("2 条（详见日志）", d.detail.toPlainText())
        self.assertIn(d._theme()["fail_fg"].lower(), d.detail.toHtml().lower())
        history_store.save_record(rec(id="r2", epoch=2.0))
        d2 = self.dialog()                                 # 倒序:r2 在第 0 行,没有失败
        self.assertIn("无", d2.detail.toPlainText())
        self.assertNotIn("详见日志", d2.detail.toPlainText())

    def test_old_archive_without_saves_still_renders(self):
        history_store.save_record(rec(stats={"mods": {"success": 5, "skipped": 0, "failed": 0}}))
        d = self.dialog()
        self.assertIn("存档", d.detail.toPlainText())

    def test_garbage_record_does_not_break_the_dialog(self):
        history_store.save_record(rec(stats="坏的", lists=None, duration=None, log_lines=["x", None]))
        d = self.dialog()
        self.assertTrue(d.detail.toPlainText())          # 概览还是出来了

    def test_rolled_back_flag_is_shown(self):
        history_store.save_record(rec(rolled_back=True, rollback_time="2026-11-15 02:00:00"))
        d = self.dialog()
        self.assertIn("已回滚", d.detail.toPlainText())
        self.assertIn("2026-11-15 02:00:00", d.detail.toPlainText())


class HistoryDialogLogTests(_WinBase):
    def colors_by_line(self, d):
        """每一行日志的前景色(#rrggbb 小写)。"""
        doc = d.log_view.document()
        out = []
        blk = doc.begin()
        while blk.isValid():
            if blk.text():
                it = blk.begin()
                frag = None
                while not it.atEnd():
                    frag = it.fragment()
                    break
                out.append((blk.text(), frag.charFormat().foreground().color().name().lower()))
            blk = blk.next()
        return out

    def test_each_line_gets_the_color_of_its_level(self):
        history_store.save_record(rec())
        d = self.dialog()
        t = d._theme()
        got = dict(self.colors_by_line(d))
        self.assertEqual(got["开始"], t["log_info_fg"].lower())
        self.assertEqual(got["❌ 出错了"], t["log_error_fg"].lower())
        self.assertEqual(got["[模拟] x"], t["log_simulate_fg"].lower())
        self.assertEqual(d.log_count.text(), "3 行")

    def test_old_archive_levels_are_inferred(self):
        old = rec(log="普通\n✅ 好\n⚠️ 注意")
        old.pop("log_lines")
        history_store.save_record(old)
        d = self.dialog()
        t = d._theme()
        got = dict(self.colors_by_line(d))
        self.assertEqual(got["✅ 好"], t["log_success_fg"].lower())
        self.assertEqual(got["⚠️ 注意"], t["log_warning_fg"].lower())
        self.assertEqual(got["普通"], t["log_info_fg"].lower())

    def test_archive_without_log_says_so(self):
        r = rec()
        r.pop("log_lines")
        history_store.save_record(r)
        d = self.dialog()
        self.assertIn("这份档案没有日志", d.log_view.toPlainText())
        self.assertEqual(d.log_count.text(), "")

    def test_selecting_another_record_swaps_the_log(self):
        history_store.save_record(rec(id="a", epoch=1.0, log_lines=[["INFO", "甲"]]))
        history_store.save_record(rec(id="b", epoch=2.0, log_lines=[["INFO", "乙"], ["INFO", "丙"]]))
        d = self.dialog()                                  # 倒序:b 在第 0 行
        self.assertEqual(d.log_view.toPlainText().split(), ["乙", "丙"])
        d.list_widget.setCurrentRow(1)
        self.assertEqual(d.log_view.toPlainText().split(), ["甲"])
        self.assertEqual(d.log_count.text(), "1 行")

    def test_the_list_page_does_not_carry_the_log_so_it_is_read_per_selection(self):
        history_store.save_record(rec())
        d = self.dialog()
        self.assertNotIn("log", d.records[0])              # list_records 默认不带 log,避免列表页太重
        self.assertTrue(d.log_view.toPlainText())          # 但日志照样显示出来了(按 id 再读)

    def test_refresh_renders_the_log_once_per_selection(self):
        history_store.save_record(rec(id="a", epoch=1.0))
        history_store.save_record(rec(id="b", epoch=2.0))
        d = self.dialog()
        calls = []
        orig = d._show_log
        with mock.patch.object(d, "_show_log", lambda r: (calls.append(r and r["id"]), orig(r))[1]):
            d._refresh(keep_row=0)
            self.assertEqual(calls, ["b"])
            calls.clear()
            d._refresh(keep_row=1)
            self.assertEqual(calls, ["a"])                 # 换行也只渲染一遍,日志有几千行,别渲染两遍

    def test_refresh_after_delete_shows_the_next_record_not_the_deleted_one(self):
        history_store.save_record(rec(id="a", epoch=1.0, log_lines=[["INFO", "甲"]]))
        history_store.save_record(rec(id="b", epoch=2.0, log_lines=[["INFO", "乙"]]))
        d = self.dialog()
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes):
            d._delete_current()                            # 删掉第 0 行 b
        self.assertEqual(d.log_view.toPlainText().strip(), "甲")

    def test_large_log_loads_fast_enough(self):
        history_store.save_record(rec(log_lines=[["SIMULATE", "[模拟] 将复制: saves\\x\\%d" % i]
                                                  for i in range(2000)]))
        t0 = time.perf_counter()
        d = self.dialog()
        elapsed = time.perf_counter() - t0
        self.assertEqual(d.log_count.text(), "2000 行")
        self.assertLess(elapsed, 3.0, "2000 行日志渲染 %.2fs 太慢" % elapsed)

    def test_log_follows_the_theme_colors(self):
        history_store.save_record(rec())
        d = self.dialog()
        dark = dict(self.colors_by_line(d))["❌ 出错了"]
        self.w._toggle_theme()
        pump(20)
        d2 = self.dialog()
        light = dict(self.colors_by_line(d2))["❌ 出错了"]
        self.assertNotEqual(dark, light)


class HistoryDialogZoomTests(_WinBase):
    def test_zoom_hides_the_list_and_overview_then_restores(self):
        history_store.save_record(rec())
        d = self.dialog()
        d.show()
        pump(20)
        self.assertTrue(d.list_widget.isVisible() and d.detail.isVisible())
        self.assertEqual(d.zoom_btn.text(), "⤢  放大")
        d.zoom_btn.click()
        self.assertFalse(d.list_widget.isVisible())
        self.assertFalse(d.detail.isVisible())
        self.assertTrue(d.log_view.isVisible())
        self.assertEqual(d.zoom_btn.text(), "⤡  还原")
        d.zoom_btn.click()
        self.assertTrue(d.list_widget.isVisible() and d.detail.isVisible())
        self.assertEqual(d.zoom_btn.text(), "⤢  放大")


class HistoryDialogConfirmTests(_WinBase):
    def make_rollbackable(self, d):
        d._record_backup_dir = lambda r: Path(_tmp.name)
        self.w.target_path = _tmp.name

    def test_delete_confirmation_names_the_record_and_says_games_are_untouched(self):
        history_store.save_record(rec())
        d = self.dialog()
        with mock.patch.object(QtWidgets.QMessageBox, "question",
                               return_value=QtWidgets.QMessageBox.No) as q:
            d._delete_current()
        text = q.call_args[0][2]
        self.assertIn("2026-11-15 01:00:00", text)
        self.assertIn("不影响任何游戏文件", text)
        self.assertEqual(len(history_store.list_records()), 1)         # 点了「否」没删

    def test_confirm_rollback_default_button_is_the_safe_one(self):
        history_store.save_record(rec())
        d = self.dialog()
        seen = {}

        def fake_exec(box):
            seen["default"] = box.defaultButton().text()
            seen["text"] = box.text()
            seen["buttons"] = [b.text() for b in box.buttons()]
            seen["kinds"] = [b.property("kind") for b in box.buttons()]
            return 0
        with mock.patch.object(QtWidgets.QMessageBox, "exec", fake_exec):
            go = d._confirm_rollback(rec(), {"mods", "config"})
        self.assertFalse(go)                                           # 没点「回滚」
        self.assertEqual(seen["default"], "先不滚")                    # 回车不会误回滚
        self.assertEqual(seen["buttons"], ["回滚", "先不滚"])
        self.assertEqual(seen["kinds"], ["danger", "neutral"])

    def test_confirm_rollback_text_is_complete(self):
        d = self.dialog()
        seen = {}
        with mock.patch.object(QtWidgets.QMessageBox, "exec",
                               lambda box: seen.update(text=box.text()) or 0):
            d._confirm_rollback(rec(), {"mods", "config"})
        t = seen["text"]
        self.assertIn("2026-11-15 01:00:00", t)                        # 说清是哪一次
        self.assertIn("会用备份覆盖当前内容", t)
        self.assertIn("迁移前不存在的东西会被删掉", t)
        self.assertIn("此操作不可撤销", t)
        self.assertIn("config / mods", t)                              # 本次还原哪些

    def test_confirm_rollback_without_a_time_uses_the_latest_wording(self):
        d = self.dialog()
        seen = {}
        with mock.patch.object(QtWidgets.QMessageBox, "exec",
                               lambda box: seen.update(text=box.text()) or 0):
            d._confirm_rollback({"id": "x"}, {"mods"})
        self.assertIn("恢复到最近一次迁移之前", seen["text"])

    def test_clicking_rollback_in_the_confirmation_returns_true(self):
        d = self.dialog()
        with mock.patch.object(QtWidgets.QMessageBox, "exec", return_value=0), \
                mock.patch.object(QtWidgets.QMessageBox, "clickedButton",
                                  lambda box: next(b for b in box.buttons() if b.text() == "回滚")):
            self.assertTrue(d._confirm_rollback(rec(), {"mods"}))

    def test_declining_the_confirmation_does_not_start_a_rollback(self):
        history_store.save_record(rec())
        d = self.dialog()
        self.make_rollbackable(d)
        started = []
        with mock.patch("ui.qt_main.history.pick_rollback_parts", return_value={"mods"}), \
                mock.patch.object(d, "_confirm_rollback", return_value=False), \
                mock.patch.object(d, "_start_rollback_worker", lambda *a: started.append(a)):
            d._do_rollback()
        self.assertEqual(started, [])

    def test_accepting_the_confirmation_starts_it_with_the_chosen_parts(self):
        history_store.save_record(rec())
        d = self.dialog()
        self.make_rollbackable(d)
        started = []
        with mock.patch("ui.qt_main.history.pick_rollback_parts", return_value={"mods"}), \
                mock.patch.object(d, "_confirm_rollback", return_value=True), \
                mock.patch.object(d, "_start_rollback_worker", lambda *a: started.append(a)):
            d._do_rollback()
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][3], {"mods"})


if __name__ == "__main__":
    unittest.main()

"""Tk-side regressions found in the Qt-refactor review."""
import ast
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_temp = tempfile.TemporaryDirectory(prefix="mctool-tk-regressions-")
os.environ["MCTOOL_CONFIG"] = str(Path(_temp.name) / "config.json")
os.environ["MCTOOL_LANG"] = "zh"
Path(os.environ["MCTOOL_CONFIG"]).write_text('{"language":"zh"}', encoding="utf-8")
SOURCE = Path(__file__).resolve().parents[1] / "Minecraft迁移工具"
sys.path.insert(0, str(SOURCE))

import tkinter as tk  # noqa: E402
from core import migrator as m  # noqa: E402
from utils.helpers import SmoothScroller  # noqa: E402
from utils.session_log import SessionLog  # noqa: E402


def extract_methods(rel, cls_name, methods, namespace):
    tree = ast.parse((SOURCE / rel).read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    cls.bases, cls.decorator_list = [], []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), rel, "exec"),
         namespace)
    return namespace[cls_name]


class MigrationThreadTests(unittest.TestCase):
    def _run(self, prune):
        run = mock.Mock(return_value=True)
        traced = []
        ns = {"threading": threading, "do_backup": mock.Mock(), "run_migration": run,
              "prune_legacy_backup": mock.Mock(), "MigrationCancelled": m.MigrationCancelled,
              "trace_exc": lambda where, e=None: traced.append(where),
              "trp": lambda s, *a: s.format(*a)}
        cls = extract_methods("ui/mw_migration.py", "MigrationMixin", ["_run_migration_thread"], ns)
        obj = cls()
        obj.progress_queue = None
        obj._ui_post, obj._complete_migration, obj.log = mock.Mock(), mock.Mock(), mock.Mock()
        obj._run_migration_thread("s", "t", "", [], [], False, True,
                                  backup_fn=mock.Mock(), prune_fn=prune)
        return obj, run, traced

    def test_prune_failure_does_not_fail_migration(self):
        obj, run, traced = self._run(mock.Mock(side_effect=OSError("locked")))
        self.assertTrue(run.called)
        obj._ui_post.assert_called_once_with(obj._complete_migration, True, None)
        self.assertEqual(traced, ["mw_migration/清理旧备份"])

    def test_prune_count_is_logged(self):
        obj, run, _ = self._run(mock.Mock(return_value=3))
        self.assertTrue(any("3" in str(c.args[0]) for c in obj.log.call_args_list))


class SilentNotifyTests(unittest.TestCase):
    def _complete(self, silent):
        ns = {"messagebox": mock.Mock(), "trp": lambda s, *a: s.format(*a)}
        cls = extract_methods("ui/mw_migration.py", "MigrationMixin", ["_complete_migration"], ns)
        obj = cls()
        obj.after_id = None
        obj.progress_window = None
        obj.root = mock.Mock()
        obj._日志尾巴起步 = mock.Mock()
        obj.when_log_bottom = mock.Mock()
        obj._notify_task_done = mock.Mock()
        obj._silent_notify_on_finish = silent
        obj._complete_migration(True)
        return obj._notify_task_done

    def test_silent_mode_forces_notification(self):
        notify = self._complete(True)
        self.assertTrue(notify.call_args.kwargs.get("force"))

    def test_normal_mode_does_not_force(self):
        notify = self._complete(False)
        self.assertFalse(notify.call_args.kwargs.get("force", False))


class ConfigFileTests(unittest.TestCase):
    def test_load_config_follows_patched_module_constant(self):
        import ui.main_window as MW
        alt = Path(_temp.name) / "alt.json"
        alt.write_text('{"language":"en","probe":1}', encoding="utf-8")
        with mock.patch.object(MW, "CONFIG_FILE", alt):
            self.assertEqual(MW.MigrationGUI.load_config(None).get("probe"), 1)


class SessionLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_keeps_only_newest_files(self):
        d = Path(self.temp.name)
        for i in range(5):
            (d / ("2026010%d-000000-old.txt" % i)).write_text("x", encoding="utf-8")
        log = SessionLog(d, keep=3)
        log.append("new")
        log.close()
        names = sorted(p.name for p in d.glob("*.txt"))
        self.assertEqual(len(names), 3)
        self.assertIn(log.path.name, names)
        self.assertNotIn("20260100-000000-old.txt", names)

    def test_open_failure_backs_off(self):
        log = SessionLog(self.temp.name)
        with mock.patch.object(Path, "open", side_effect=OSError("disk full")) as op:
            for _ in range(50):
                log.append("line")
        self.assertEqual(op.call_count, 1)
        self.assertEqual(log.error, "disk full")
        self.assertEqual(len(log.snapshot()[2]), 50)
        log._retry_at = time.perf_counter() - 1
        log.append("after")
        log.close()
        self.assertEqual(log.path.read_text(encoding="utf-8"), "after\n")


class SafePathTests(unittest.TestCase):
    def test_reserved_name_with_space_before_extension(self):
        for name in ("nul .txt", "con .log", "nul", "aux.txt"):
            self.assertFalse(m._is_safe_path(name), name)
        self.assertTrue(m._is_safe_path("mods/a.jar"))


class CanvasScrollTests(unittest.TestCase):
    def test_short_content_does_not_move(self):
        root = tk.Tk()
        try:
            c = tk.Canvas(root, width=200, height=300, yscrollincrement=1)
            c.pack()
            c.create_rectangle(0, 0, 100, 100)
            root.update()
            c.configure(scrollregion=c.bbox("all"))
            root.update()
            sc = SmoothScroller.for_canvas(c)
            before = c.canvasy(0)
            self.assertEqual(sc._mover(-5), (0, True))
            self.assertEqual(c.canvasy(0), before)
            c.create_rectangle(0, 0, 100, 2000)
            c.configure(scrollregion=c.bbox("all"))
            root.update()
            self.assertEqual(sc._mover(40), (40, False))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()

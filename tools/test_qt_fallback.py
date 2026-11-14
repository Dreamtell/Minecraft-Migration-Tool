"""Qt main window fallback: once PySide6 is loaded, never build Tk() in the same process."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_temp = tempfile.TemporaryDirectory(prefix="mctool-qt-fallback-")
os.environ["MCTOOL_CONFIG"] = str(Path(_temp.name) / "config.json")
os.environ["MCTOOL_LANG"] = "zh"
Path(os.environ["MCTOOL_CONFIG"]).write_text('{"language":"zh"}', encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from _testcfg import use_config as _use_config   # noqa: E402
_CFG_PATH = os.environ["MCTOOL_CONFIG"]


def setUpModule():                     # 全量跑时各模块各用各的临时配置
    _use_config(_CFG_PATH)

import app  # noqa: E402


class _Exit(Exception):
    pass


class QtFallbackTests(unittest.TestCase):
    def test_restart_forces_tk_and_releases_lock(self):
        app._LOCK_HOLDER = object()
        with mock.patch("subprocess.Popen") as popen, \
                mock.patch.object(app.os, "_exit", side_effect=_Exit):
            with self.assertRaises(_Exit):
                app._重启为纯Tk进程()
        self.assertIsNone(app._LOCK_HOLDER)
        self.assertEqual(popen.call_count, 1)
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["MCTOOL_MAIN"], "tk")
        cmd = popen.call_args.args[0]
        self.assertFalse(any(a.startswith("--qt-host") for a in cmd))

    def test_env_tk_overrides_qt_backend(self):
        Path(os.environ["MCTOOL_CONFIG"]).write_text(
            '{"language":"zh","main_window_backend":"qt"}', encoding="utf-8")
        with mock.patch.dict(os.environ, {"MCTOOL_MAIN": "tk"}):
            self.assertFalse(app._想用Qt主界面())
        with mock.patch.dict(os.environ, {"MCTOOL_MAIN": ""}):
            self.assertTrue(app._想用Qt主界面())


if __name__ == "__main__":
    unittest.main()

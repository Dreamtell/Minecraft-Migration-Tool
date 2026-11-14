# -*- coding: utf-8 -*-
"""联网搜索的图标缓存（core/mod_search.fetch_icon + 卡片任务）。

只测「不联网也能测」的部分：
  - 空地址直接返回 ""
  - 已经缓存过的地址 = 0 请求，直接回路径
  - 坏地址（连不上）返回 ""，且不会留下 .part 半成品
  - 卡片任务把结果发回主线程（带批次号）
真下载（Modrinth/CurseForge 图标 URL）不在单测里做——那是网络测试，会看网脸色。
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

_产品 = Path(__file__).resolve().parent.parent / "Minecraft迁移工具"
if str(_产品) not in sys.path:
    sys.path.insert(0, str(_产品))

from core import mod_search  # noqa: E402


class FetchIconTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="mctool_icons_")
        self._旧 = mod_search._ICON_DIR
        mod_search._ICON_DIR = Path(self._tmp)

    def tearDown(self):
        mod_search._ICON_DIR = self._旧
        for p in Path(self._tmp).glob("*"):
            try:
                p.unlink()
            except Exception:
                pass
        try:
            os.rmdir(self._tmp)
        except Exception:
            pass

    def _缓存名(self, url):
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".img"

    def test_empty_url_returns_empty(self):
        self.assertEqual(mod_search.fetch_icon(""), "")
        self.assertEqual(mod_search.fetch_icon(None), "")

    def test_cached_file_is_reused_without_network(self):
        url = "https://example.invalid/icon/abc.png"
        目标 = Path(self._tmp) / self._缓存名(url)
        目标.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        # 域名是 .invalid，真去请求一定失败；能拿到路径就说明走的是缓存
        self.assertEqual(mod_search.fetch_icon(url), str(目标))

    def test_bad_url_returns_empty_and_leaves_no_part_file(self):
        p = mod_search.fetch_icon("http://127.0.0.1:9/nope.png", timeout=2)
        self.assertEqual(p, "")
        self.assertEqual(list(Path(self._tmp).glob("*.part")), [])

    def test_cache_dir_created(self):
        d = mod_search.icon_cache_dir()
        self.assertTrue(Path(d).is_dir())


class IconTaskTests(unittest.TestCase):
    def test_task_emits_gen_row_and_path(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtCore, QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from ui import qt_big_view as B

        sig = B._IconSignals()
        结果 = []
        sig.done.connect(lambda *a: 结果.append(a))
        任务 = B._IconTask(3, "", sig, 7)
        任务.run()                      # 直接在同线程跑：空地址不会联网
        for _ in range(50):
            app.processEvents(QtCore.QEventLoop.AllEvents, 5)
        self.assertEqual(结果, [(7, 3, "")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

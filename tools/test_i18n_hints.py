# -*- coding: utf-8 -*-
"""英文模式下 Tk 界面上的长提示必须有英文译文。

背景：`utils/i18n.py` 的英文表里原本只有 Qt 版措辞的 `.jar` 提示键（「可从变更日志
导入、点「添加模组」选」），Tk 那句以「点「添加模组」选」开头，是另一个字符串，
英文模式下原样输出中文。这里盯住 Tk 实际用的那两句。
"""
import os
import sys
import unittest
from pathlib import Path

os.environ["MCTOOL_LANG"] = "en"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from utils import i18n                                  # noqa: E402


_模组提示 = ("每行一个 .jar 文件名（点「添加模组」选，"
         "或直接把 jar / mods 文件夹拖进来）。同名匹配不区分大小写，改过名的也能对上。")
_拖放提示 = "　⚠ 拖放不可用（缺少 tkinterdnd2），请用「添加模组」按钮。"


def _有中文(s):
    return any("一" <= c <= "鿿" for c in s)


class TkHintTranslationTests(unittest.TestCase):
    def setUp(self):
        i18n.install()                # MCTOOL_LANG=en 时才会真正激活

    def tearDown(self):
        i18n.uninstall()

    def test_mod_hint_has_english(self):
        译 = i18n.trp(_模组提示)
        self.assertNotEqual(译, _模组提示)
        self.assertFalse(_有中文(译), 译)

    def test_drag_unavailable_hint_has_english(self):
        译 = i18n.trp(_拖放提示)
        self.assertNotEqual(译, _拖放提示)
        self.assertFalse(_有中文(译), 译)


if __name__ == "__main__":
    unittest.main(verbosity=2)

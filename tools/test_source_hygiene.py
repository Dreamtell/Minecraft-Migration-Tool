# -*- coding: utf-8 -*-
"""源码卫生：同一文件里不能有重复的顶层定义，不能留合并冲突标记。

为什么：Python 里后一个同名 `def` 会**静默覆盖**前一个，没有警告、没有报错。
`utils/helpers.py` 的 `_hwnd_of` 就重复过两份，而且两份失败返回值不同
（`None` / `0`）—— 删错一份，调用点的语义就悄悄变了。这个测试把这类问题挡住。
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "Minecraft迁移工具"


def _py_files():
    for p in SOURCE.rglob("*.py"):
        if "__pycache__" not in p.parts:
            yield p


class SourceHygieneTests(unittest.TestCase):
    def test_no_duplicate_top_level_definitions(self):
        重复 = []
        for p in _py_files():
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as exc:                 # pragma: no cover
                重复.append("%s: 语法错误 %s" % (p.relative_to(SOURCE), exc))
                continue
            seen = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    if node.name in seen:
                        重复.append("%s:%d %s" % (p.relative_to(SOURCE),
                                                  node.lineno, node.name))
                    seen.add(node.name)
        self.assertEqual(重复, [], "重复的顶层定义会被后一个静默覆盖")

    def test_no_merge_conflict_markers(self):
        # 只看行首的完整标记；单独的 ======= 在注释分隔线里很常见，不算
        标记 = ("<<<<<<< ", ">>>>>>> ")
        命中 = []
        for p in _py_files():
            for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if line.startswith(标记):
                    命中.append("%s:%d" % (p.relative_to(SOURCE), i))
        self.assertEqual(命中, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

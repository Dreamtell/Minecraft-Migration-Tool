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


# 建了无边框窗口、但**故意**不标 mark_overlay 的模块：它们是"用户的另一个窗口"
# 或者跟浮标无关的独立窗口，浮标应该为他们让位。
_不是浮层 = {
    "ui/message_center.py": "消息中心抽屉：贴在主界面右下，浮标必须让它、不能被压在它上面",
    "ui/rollback_pick.py": "回滚选择窗：真正的对话框",
    "ui/precheck_dialog.py": "自检窗：真正的对话框",
    "ui/splash.py": "启动画面：那时候还没有浮标",
}


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
                # 类里也要查：`ui/net_float.py` 的 _on_root_destroy 就定义过两次，
                # 后一个静默覆盖前一个，两份行为还略有差别。
                if isinstance(node, ast.ClassDef):
                    类内 = set()
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if sub.name in 类内:
                                重复.append("%s:%d %s.%s" % (p.relative_to(SOURCE),
                                                           sub.lineno, node.name,
                                                           sub.name))
                            类内.add(sub.name)
        self.assertEqual(重复, [], "重复的顶层定义会被后一个静默覆盖")

    def test_overlay_windows_are_marked(self):
        """建了无边框窗口的模块必须调 mark_overlay，否则浮标会把它当成"别的窗口"。

        浮标靠 `_has_other_window()` 判断要不要抬升，而它只认显式标记过的浮层；
        新加一个无边框浮层又忘了标记，就会复现「浮标时有时无」。
        """
        漏标 = []
        for p in _py_files():
            rel = p.relative_to(SOURCE).as_posix()
            src = p.read_text(encoding="utf-8", errors="replace")
            if "overrideredirect(True)" not in src:
                continue
            if "mark_overlay" in src:
                continue
            if rel in _不是浮层:
                continue
            漏标.append(rel)
        self.assertEqual(漏标, [], "这些模块建了无边框窗口却没调 mark_overlay")

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

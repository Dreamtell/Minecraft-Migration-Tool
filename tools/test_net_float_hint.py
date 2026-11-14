# -*- coding: utf-8 -*-
"""浮标贴边提示的迟滞逻辑（`ui/net_float.py` 的 `_detect_edge`）。

以前只有一个阈值：光标在 8px 边界上抖 1px，"将要吸附"的提示就亮→灭→亮，
光带跟着一闪一闪。现在分两档（进门 8px、出门 26px）且当前那条边有粘性。
这里用桩对象直接调 `NetFloat._detect_edge`，不建真窗口。
"""

import os
import sys
import unittest

_产品 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Minecraft迁移工具")
if _产品 not in sys.path:
    sys.path.insert(0, _产品)

from ui.net_float import NetFloat            # noqa: E402


class _桩:
    SNAP = 8
    _parent = (1200, 800)
    _float = (56, 56)

    def __init__(self):
        self._hint = None
        self._rel = [600, 300]

    def _parent_size(self):
        return self._parent

    def _cur_size(self):
        return self._float


class EdgeHintTests(unittest.TestCase):
    def setUp(self):
        self.st = _桩()
        self.detect = NetFloat._detect_edge.__get__(self.st)

    def _走(self, 轨迹):
        """按轨迹移动并像 `_set_hint` 那样写回 `_hint`，返回边变化次数。"""
        当前 = self.st._hint        # 从当前提示态算起，不然第一次会误记成"切了一次"
        次数 = 0
        for x, y in 轨迹:
            self.st._rel = [x, y]
            e = self.detect()
            if e != 当前:
                次数 += 1
                当前 = e
            self.st._hint = e
        return 次数, 当前

    def test_抖动不重复切边(self):
        # 在 8px 阈值上来回抖 ±3px：老逻辑会切十几次，现在只该进一次
        轨迹 = [(x, 300) for x in (10, 9, 8, 9, 10, 9, 8, 9, 10, 9, 8, 7, 8, 9)]
        for x, y in 轨迹:                     # 先让它亮起来
            self.st._rel = [x, y]
            self.st._hint = self.detect()
        次数, 当前 = self._走([(x, 300) for x in (9, 10, 11, 12, 13, 14, 13, 12, 11)])
        self.assertEqual(次数, 0, "已经亮着的边在 8~26px 之间不该反复开关")
        self.assertEqual(当前, "left")

    def test_出门阈值更大(self):
        self.st._rel = [4, 300]
        self.assertEqual(self.detect(), "left")
        self.st._hint = "left"
        self.st._rel = [20, 300]              # 8 < 20 < 26：还亮着
        self.assertEqual(self.detect(), "left")
        self.st._rel = [40, 300]              # 超过 26：灭
        self.assertIsNone(self.detect())

    def test_居中时无提示(self):
        次数, 当前 = self._走([(600, 300)] * 5)
        self.assertEqual(次数, 0)
        self.assertIsNone(当前)

    def test_角落粘住当前边(self):
        # 左边 9px、上边 10px 都近：正在提示左边时不要因为上边近 1px 就换过去
        self.st._rel = [9, 10]
        self.st._hint = "left"
        self.assertEqual(self.detect(), "left")


if __name__ == "__main__":
    unittest.main(verbosity=2)

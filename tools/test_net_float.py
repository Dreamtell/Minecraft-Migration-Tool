# -*- coding: utf-8 -*-
"""浮标（`ui/net_float.py`）的三处回归：

1. 贴边光带的 ULW 常量 alpha 必须是 255 —— 像素自己的 alpha 已经在 `_draw()`
   里乘过 `_alpha`，再拿 0~1 当 0~255 用，整条光带只剩 1/255，肉眼看就是没有；
2. 延迟数字变化时只叠数字，不重画底图（6 倍超采样一次 10~30ms）；
3. `close()` 要把所有 `after()` 链都收掉（交叉溶解 / 悬停轮询 / 拖动计时器）。
"""

import ctypes
import io
import os
import sys
import time
import types
import unittest
from unittest import mock

_产品 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Minecraft迁移工具")
if _产品 not in sys.path:
    sys.path.insert(0, _产品)

from ui import net_float as nf                      # noqa: E402
from ui.net_float import NetFloat, _EdgeGlow        # noqa: E402


def _桩浮标(val=45, conn=True, d=56, docked=None, peek=False, hover=False):
    """不建真窗口的 NetFloat：只填 _image / _state 需要的那几个字段。"""
    o = object.__new__(NetFloat)
    o.d = d
    o.tab_w = 22
    o.dock_enabled = True
    o._morph = None
    o._docked = docked
    o._peek = peek
    o._hover = hover
    o.shadow = True
    o.bg = "#2e2e2e"
    o._hint = None
    o._base_cache = {}

    def 历史():
        if val is None:
            return []
        return [(time.time(), val, 2 if conn else 0, 2)]

    o.get_history = 历史
    return o


class StateTests(unittest.TestCase):
    def test_新鲜数据_正常(self):
        o = _桩浮标(val=45)
        self.assertEqual(o._state(), (True, 45))
        self.assertTrue(o._通色())

    def test_新鲜数据_断开(self):
        o = _桩浮标(val=0, conn=False)
        self.assertEqual(o._state(), (False, None))
        self.assertFalse(o._通色())

    def test_过期数据_按无数据(self):
        o = _桩浮标(val=45)
        o.get_history = lambda: [(time.time() - 5.0, 45, 2, 2)]
        self.assertEqual(o._state(), (True, None))

    def test_没有历史(self):
        o = _桩浮标(val=None)
        self.assertEqual(o._state(), (True, None))


class BaseCacheTests(unittest.TestCase):
    def test_数字变化不重画底图(self):
        o = _桩浮标(val=45)
        a = o._image()
        base = list(o._base_cache.values())[0]
        o.get_history = lambda: [(time.time(), 450, 2, 2)]
        b = o._image()
        self.assertIs(list(o._base_cache.values())[0], base, "底图应该复用")
        self.assertLessEqual(len(o._base_cache), 1)
        self.assertNotEqual(a.tobytes(), b.tobytes(), "数字变了图应该跟着变")

    def test_状态变化要重画底图(self):
        o = _桩浮标(val=45)
        o._image()
        o.get_history = lambda: [(time.time(), 0, 0, 2)]
        o._image()
        self.assertEqual(len(o._base_cache), 2, "通/断是两种底图")

    def test_悬停也是不同底图(self):
        o = _桩浮标(val=45)
        o._image()
        o._hover = True
        o._image()
        self.assertEqual(len(o._base_cache), 2)


class EdgeGlowBlendTests(unittest.TestCase):
    def test_常量alpha是255(self):
        """像素 alpha 已在 _draw 里乘过 _alpha，ULW 的常量必须保持 255。"""
        seen = {}

        class _U:
            def GetDC(self, _):
                return 1

            def ReleaseDC(self, _a, _b):
                return 1

            def UpdateLayeredWindow(self, hwnd, hdc, dst, size, mem, src, color,
                                    blend, flags):
                b = ctypes.cast(blend, ctypes.POINTER(nf._BLENDFUNCTION)).contents
                seen["sca"] = b.SourceConstantAlpha
                seen["fmt"] = b.AlphaFormat
                return 1

        g = object.__new__(_EdgeGlow)
        g._dib = (1, 2, 3, 4)
        g._pos = (10, 20)
        g._hwnd = 0
        g._alpha = 1.0
        fake = types.SimpleNamespace(user32=_U())
        with mock.patch.object(nf.ctypes, "windll", fake):
            g._blit()
        self.assertEqual(seen.get("sca"), 255)
        self.assertEqual(seen.get("fmt"), nf._AC_SRC_ALPHA)


class CloseTests(unittest.TestCase):
    def test_全部定时器都取消(self):
        o = object.__new__(NetFloat)
        o._stop = False
        cancelled = []

        class _Root:
            def after_cancel(self, j):
                cancelled.append(j)

        class _Win:
            def destroy(self):
                pass

        class _Glow:
            def destroy(self):
                pass

        o.root = _Root()
        o.win = _Win()
        o._glow = _Glow()
        o._drag = {"job": "drag"}
        for k in ("_job", "_follow_job", "_vis_job", "_show_job", "_fade_job",
                  "_collapse_job", "_hint_job", "_morph_job", "_hover_job"):
            setattr(o, k, k)
        o.on_close = None
        o.close()
        for k in ("_job", "_follow_job", "_vis_job", "_show_job", "_fade_job",
                  "_collapse_job", "_hint_job", "_morph_job", "_hover_job", "drag"):
            self.assertIn(k, cancelled, "close() 漏掉了 %s" % k)


class SetBgTests(unittest.TestCase):
    def test_换底色会清缓存并重画(self):
        o = _桩浮标(val=45)
        o._shape_cache = {1: 1}
        o._image()
        calls = []
        o.win = mock.Mock()
        o.canvas = mock.Mock()
        o._redraw = lambda: calls.append(1)
        o.set_bg("#f0f0f0")
        self.assertEqual(o.bg, "#f0f0f0")
        self.assertEqual(o._base_cache, {})
        self.assertEqual(o._shape_cache, {})
        self.assertEqual(calls, [1])
        o.win.configure.assert_called_with(bg="#f0f0f0")

    def test_同色不重画(self):
        o = _桩浮标(val=45)
        o._shape_cache = {}
        calls = []
        o.win = mock.Mock()
        o.canvas = None
        o._redraw = lambda: calls.append(1)
        o.set_bg(o.bg)
        self.assertEqual(calls, [])


class CloseReasonTests(unittest.TestCase):
    """"程序退出"不能当成"用户关掉浮标"：否则每次正常退出都会把开关写成关。"""

    def _关(self, **kw):
        o = object.__new__(NetFloat)
        o._stop = False
        o.root = mock.Mock()
        o.win = mock.Mock()
        o._glow = None
        o._drag = {}
        o.on_close = mock.Mock()
        o.close(**kw)
        return o

    def test_退出不改开关(self):
        o = self._关(退出=True)
        o.on_close.assert_called_once_with(o, False)

    def test_用户关闭改开关(self):
        o = self._关()
        o.on_close.assert_called_once_with(o, True)



class ReshowTests(unittest.TestCase):
    """藏起来再出现：贴边浮标必须回到收起态，并且悬停轮询要重新起来。"""

    def test_重新显示会纠正到收起态(self):
        o = object.__new__(NetFloat)
        o._stop = False
        o._parked = True
        o._shown = False
        o._docked = "left"
        o._peek = True
        o._hover = True
        o._fade_hr = False
        o._alpha = 0
        o.root = mock.Mock()
        o.win = mock.Mock()
        o._hwnd = 0
        o._layered = True
        o._dib = None
        o._glow = None
        o._drag = {"on": False}
        o._root_should_show = lambda: True
        o._redraw = mock.Mock()
        o._apply_layered = mock.Mock()
        o._real_hwnd = lambda: 0
        o._has_other_window = lambda: False
        o._fade_to = mock.Mock()
        o._cursor_inside = lambda 余量=2: False
        o._collapse_now = mock.Mock()
        o._poll_hover = mock.Mock()
        o._hover_job = None
        o._docked = "left"
        o.dock_enabled = True
        o._collapse_job = None
        o._warm_shapes = mock.Mock()
        o._show()
        self.assertFalse(o._parked)
        self.assertTrue(o._shown)
        self.assertFalse(o._hover, "光标不在浮标上，不应还是悬停态")
        self.assertIsNotNone(o._collapse_job, "应该排一次收缩")


class EdgeGlowDestroyTests(unittest.TestCase):
    def test_销毁立刻停链(self):
        g = object.__new__(_EdgeGlow)
        cancelled = []
        g.root = mock.Mock()
        g.root.after_cancel = lambda j: cancelled.append(j)
        g.win = mock.Mock()
        g._job = "tick"
        g._edge = "left"
        g._alpha = 1.0
        g._target = 1.0
        g._dib = (1, 2, 3, 4)
        g._fine = True
        g.destroy()
        self.assertIn("tick", cancelled)
        self.assertIsNone(g._edge)
        self.assertIsNone(g._dib)
        g.win.destroy.assert_called_once()



class VisibilityTests(unittest.TestCase):
    def _桩(self, state="normal", viewable=True, fg=True):
        o = object.__new__(NetFloat)
        o.root = mock.Mock()
        o.root.state.return_value = state
        o.root.winfo_viewable.return_value = viewable
        o._app_is_foreground = lambda: fg
        return o

    def test_最大化要显示(self):
        """Tk 最大化时 state() 是 "zoomed"：当成"不是 normal"会让浮标永久消失。"""
        self.assertTrue(self._桩("zoomed")._root_should_show())

    def test_最小化不显示(self):
        self.assertFalse(self._桩("iconic")._root_should_show())

    def test_收托盘不显示(self):
        self.assertFalse(self._桩("withdrawn")._root_should_show())

    def test_别的程序在前台不显示(self):
        self.assertFalse(self._桩("normal", fg=False)._root_should_show())


class DockSwitchTests(unittest.TestCase):
    def test_关掉贴边收纳就不提示(self):
        st = object.__new__(NetFloat)
        st.SNAP = 8
        st._hint = None
        st._rel = [0, 300]          # 紧贴左边缘
        st.dock_enabled = False
        st._parent_size = lambda: (1200, 800)
        st._cur_size = lambda: (56, 56)
        self.assertIsNone(NetFloat._detect_edge(st))

    def test_开着时才提示(self):
        st = object.__new__(NetFloat)
        st.SNAP = 8
        st._hint = None
        st._rel = [0, 300]
        st.dock_enabled = True
        st._parent_size = lambda: (1200, 800)
        st._cur_size = lambda: (56, 56)
        self.assertEqual(NetFloat._detect_edge(st), "left")


class DragThresholdTests(unittest.TestCase):
    def _桩(self):
        o = object.__new__(NetFloat)
        o._drag = {"p0": None}
        return o

    def test_按下那一帧不算拖(self):
        o = self._桩()
        self.assertFalse(o._moved_enough(types.SimpleNamespace(x=100, y=100)))

    def test_手抖两像素仍是点击(self):
        o = self._桩()
        o._moved_enough(types.SimpleNamespace(x=100, y=100))
        self.assertFalse(o._moved_enough(types.SimpleNamespace(x=102, y=101)))

    def test_真的移开算拖(self):
        o = self._桩()
        o._moved_enough(types.SimpleNamespace(x=100, y=100))
        self.assertTrue(o._moved_enough(types.SimpleNamespace(x=140, y=100)))


class CollapsedRefreshTests(unittest.TestCase):
    def test_收纳成小标签仍会刷新(self):
        """胶囊条的绿/红是连通状态：跳过刷新的话断网了它还是绿的。"""
        o = object.__new__(NetFloat)
        o._stop = False
        o._shown = True
        o._docked = "left"
        o._peek = False
        o._drag = {"on": False}
        o.root = mock.Mock()
        o.root.winfo_exists.return_value = True
        o._sync_visibility = mock.Mock()
        o._tick_redraw = mock.Mock()
        o._tick()
        o._tick_redraw.assert_called_once()


class ShapeCacheTests(unittest.TestCase):
    def test_通断变化要作废形态图(self):
        o = _桩浮标(val=45)
        o._shape_cache = {}
        o._render_base = mock.Mock(return_value=__import__("PIL.Image", fromlist=["Image"]).new("RGBA", (56, 56)))
        o._shape_img((56, 56))
        n = o._render_base.call_count
        o.get_history = lambda: [(time.time(), 0, 0, 2)]      # 断网
        o._shape_img((56, 56))
        self.assertEqual(o._render_base.call_count, n + 1, "断网后还拿了旧颜色的缓存图")


class UnbindTests(unittest.TestCase):
    def test_关闭按funcid解绑(self):
        o = object.__new__(NetFloat)
        unbound = []

        class _Root:
            def unbind(self, ev, fid):
                unbound.append((ev, fid))

            def after_cancel(self, j):
                pass

        o.root = _Root()
        o.win = mock.Mock()
        o._glow = None
        o._drag = {}
        o.on_close = None
        o._root_binds = [("<Configure>", "id1"), ("<Map>", "id2")]
        o.close()
        self.assertIn(("<Configure>", "id1"), unbound)
        self.assertIn(("<Map>", "id2"), unbound)
        self.assertEqual(o._root_binds, [])


class EdgeGlowSharesTests(unittest.TestCase):
    def test_两条动画各占各的精度(self):
        o = object.__new__(NetFloat)
        o._hr = {"fade": False, "morph": False}
        with mock.patch.object(nf.anim, "hold") as hold, \
                mock.patch.object(nf.anim, "release") as rel:
            o._hold_hr("fade")
            o._hold_hr("morph")
            o._hold_hr("fade")            # 幂等
            self.assertEqual(hold.call_count, 2)
            o._release_hr("fade")
            self.assertEqual(rel.call_count, 1)
            o._release_hr("morph")
            self.assertEqual(rel.call_count, 2)
            o._release_hr("morph")        # 没占过就不还
            self.assertEqual(rel.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

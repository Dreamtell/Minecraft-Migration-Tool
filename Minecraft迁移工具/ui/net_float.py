# ui/net_float.py
"""主窗口旁的悬浮图标：点一下打开网络延迟曲线窗口。

实现要点（每一条都是踩过坑的）：

1. **真透明靠分层窗口（UpdateLayeredWindow），不用 -transparentcolor。**
   色键在部分机器上不生效，会留下一块方形底色；而且它的抗锯齿边缘会和键色
   混出一圈彩边。

2. **分层窗口里不能有子控件。** Tk 的 Canvas 会用自己的底色把 ULW 画的内容
   整块盖住 —— 表现就是「圆外面多出一块方块」。所以分层模式下不建 Canvas，
   只有退回色键方案时才建。

3. **必须置顶。** 无边框弹出窗口不置顶的话会被自己的主窗口盖住（实测把主窗口
   置前之后浮标直接消失）。Tk/Win32 没有「只压在主窗口之上、又低于其它程序」
   这种中间层级可用。

4. **拖动公式**：`指针屏幕坐标 - 主窗口屏幕坐标 - 指针在控件内的偏移`。
   不要用「控件当前位置」参与运算 —— 它在拖动过程中一直在变，会算两遍位移，
   表现为瞬移、不跟手。

5. **限制在主窗口范围内**：位置存的是相对主窗口左上角的偏移，主窗口移动时
   跟着走，拖到边界外会被夹回来。

6. **贴边收纳**：缩成一个 22px 宽的小标签（不能"滑出窗外"——弹出窗口不会被
   别的窗口裁剪），鼠标移上去展开成完整的圆。
"""

import ctypes
import math
import os
import time
import tkinter as tk
from ctypes import wintypes

from utils import anim
from utils.dpi import px

# 联网正常 → 绿色主题；探测不通 → 红色主题（整个浮标跟着变色，不只是环）
_TILE = (38, 50, 56, 255)          # 无数据时的中性底色（一般用不到）
_FG = "#ffffff"
_MUTED = "#b0bec5"
_GOOD_TILE = (24, 55, 32)
_GOOD = (67, 160, 71)
_GOOD_HOVER = (102, 187, 106)
_GOOD_MUTED = "#a5d6a7"
_BAD_TILE = (58, 24, 24)
_BAD = (229, 57, 53)
_BAD_HOVER = (239, 83, 80)
_BAD_MUTED = "#ef9a9a"

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TOOLWINDOW = 0x00000080
_ULW_ALPHA = 0x00000002
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _EdgeGlow:
    """紧贴主窗口某条边的发光带 + 向内侧溢出的颗粒（"要被吸进去了"的提示）。

    实现要点：
      · 是一个**点击穿透**的分层窗口（WS_EX_TRANSPARENT + WS_EX_LAYERED），
        压在窗口内侧那条边上，不拦鼠标、不抢焦点；
      · 只画浮标上下各 160px 的一条带子（不是整条边），面积小、每帧重传也便宜；
      · 粒子从边缘生成、往窗口内侧飘、逐渐淡出，看起来就是"溢出来"。
    """

    W = 22          # 带宽（设计像素，往窗口内侧延伸）
    H = 320         # 仅作为"没拿到窗口尺寸"时的兜底高度
    FPS_MS = 16     # 60fps：粒子是"运动"的东西，20fps 看着明显一顿一顿
    寿命 = 2.2      # 颗粒从边框飘到最内侧的时间（秒）
    色 = (110, 220, 130)

    def __init__(self, root):
        import random
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.withdraw()
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            self.win.update_idletasks()
            self._hwnd = int(self.win.wm_frame(), 16)
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
            u.SetWindowLongW(self._hwnd, _GWL_EXSTYLE,
                             ex | _WS_EX_LAYERED | _WS_EX_TOOLWINDOW | 0x00000020)
            self._ok = True
        except Exception:
            self._ok = False
        self._dib = None
        self._job = None
        self._edge = None
        self._rnd = random.Random()
        self._parts = []        # [x, y, vx, vy, life, size]
        self._size = (px(self.W), px(self.H))

    # ---------- 显示 / 隐藏 ----------
    def show(self, edge, geom, center_y):
        if edge not in ("left", "right", "top", "bottom"):
            self.hide()
            return
        self._edge = edge
        self._geom = dict(geom)
        self._cy = center_y
        if not self._parts:
            # 粒子数按边长来：整条边太长的话 18 个会显得稀
            长 = max(self._geom.get("w", 0), self._geom.get("h", 0)) or 900
            self._spawn(max(18, min(64, int(长 / 26))))
        self._place()
        try:
            self.win.deiconify()
        except Exception:
            pass
        if self._job is None:
            try:
                ctypes.windll.winmm.timeBeginPeriod(1)
                self._fine = True
            except Exception:
                self._fine = False
            self._last_t = time.perf_counter()
            self._tick()

    def hide(self):
        self._edge = None
        if getattr(self, "_fine", False):
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
            self._fine = False
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        try:
            self.win.withdraw()
        except Exception:
            pass

    def _place(self):
        """把带子铺满窗口的**整条边**，并且严格裁在窗口客户区内（不溢出）。"""
        g = self._geom
        厚 = px(self.W)
        gw, gh = max(2, int(g.get("w") or 0)), max(2, int(g.get("h") or 0))
        if self._edge in ("left", "right"):
            w, h = 厚, gh
            x = g["x"] if self._edge == "left" else g["x"] + gw - w
            y = g["y"]
        else:
            w, h = gw, 厚
            x = g["x"]
            y = g["y"] if self._edge == "top" else g["y"] + gh - h
        self._size = (w, h)
        self._pos = (int(x), int(y))
        try:
            ctypes.windll.user32.SetWindowPos(self._hwnd, -1, int(x), int(y), w, h,
                                              0x0004 | 0x0010)
        except Exception:
            pass

    # ---------- 粒子 ----------
    # 用"边缘空间"描述：v = 离边框的深度（0 在边框上，1 到最内侧），u = 沿边方向。
    # 这样四条边的逻辑完全一样，只是映射到像素时换一下方向。
    def _spawn(self, n):
        for _ in range(n):
            self._parts.append([self._rnd.uniform(0.0, 1.0),     # v 深度
                                self._rnd.uniform(0.0, 1.0),     # u 沿边
                                self._rnd.uniform(0.0, 40.0),    # 年龄
                                self._rnd.uniform(1.2, 2.6)])    # 半径

    def _tick(self):
        self._job = None
        if not self._edge:
            return
        现在 = time.perf_counter()
        上次 = getattr(self, "_last_t", None) or 现在
        self._last_t = 现在
        dt = min(0.08, max(0.001, 现在 - 上次))     # 卡一下也不会瞬移
        self._step(dt)
        self._draw()
        try:
            self._job = self.root.after(self.FPS_MS, self._tick)
        except Exception:
            self._job = None

    def _step(self, dt):
        """按**时间**推进（不是按帧）：改 fps 不会改变粒子的速度。

        粒子只做**直线运动**：垂直朝窗口内侧走，速度恒定、不平移不抖动，
        随机的只有出生位置（沿边）和大小。这样看着像一排整齐的雨丝，
        而不是一群乱飘的萤火虫。
        """
        for p in self._parts:
            p[2] += dt                     # 年龄（秒）
            p[0] += dt * 0.45              # 深度：等速直线
            if p[0] > 1.05 or p[2] > self.寿命:
                p[0] = 0.0
                p[1] = self._rnd.uniform(0.0, 1.0)
                p[2] = 0.0
                p[3] = self._rnd.uniform(1.2, 2.6)

    # ---------- 绘制 ----------
    def _draw(self):
        from PIL import Image, ImageDraw
        W, H = self._size
        色 = self.色
        水平 = self._edge in ("left", "right")
        深 = W if 水平 else H          # 深度方向的像素长度

        # 渐变是静态的：按尺寸缓存一份，每帧只 copy + 画粒子
        key = (W, H, self._edge)
        if getattr(self, "_base_key", None) != key:
            底 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d0 = ImageDraw.Draw(底)
            for j in range(深):
                t = j / max(1, 深 - 1)          # 0 = 边框，1 = 最内侧
                a = int(140 * (1 - t) ** 1.7)
                if a <= 0:
                    continue
                if 水平:
                    x = (W - 1 - j) if self._edge == "right" else j
                    d0.line([(x, 0), (x, H - 1)], fill=色 + (a,))
                else:
                    y = (H - 1 - j) if self._edge == "bottom" else j
                    d0.line([(0, y), (W - 1, y)], fill=色 + (a,))
            self._base_img, self._base_key = 底, key

        img = self._base_img.copy()
        d = ImageDraw.Draw(img)

        def 像素(v, u):
            """(深度 0..1, 沿边 0..1) → 图像像素坐标。"""
            v = max(0.0, min(1.0, v))
            if 水平:
                x = (W - v * W) if self._edge == "right" else (v * W)
                return x, u * H
            y = (H - v * H) if self._edge == "bottom" else (v * H)
            return u * W, y

        for v, u, 年龄, 半径 in self._parts:
            t = 1.0 - min(1.0, 年龄 / self.寿命)
            a = int(210 * t * (1.0 - 0.6 * v))
            if a <= 0:
                continue
            x, y = 像素(v, u)
            r = 半径 * (0.6 + 0.4 * t)
            d.ellipse([x - r, y - r, x + r, y + r], fill=色 + (a,))
        self._upload(img)

    def _real_hwnd(self):
        """真正的顶层窗口句柄（一路往上找没有 parent 的那个）。"""
        try:
            h = int(self.win.wm_frame(), 16)
        except Exception:
            return 0
        u = ctypes.windll.user32
        for _ in range(8):
            p = u.GetParent(h)
            if not p:
                break
            h = p
        return h

    def _apply_layered(self):
        """给当前句柄补上 WS_EX_LAYERED（hide/deiconify 之后 Tk 可能把它弄丢）。"""
        try:
            if not self._hwnd:
                return False
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
            u.SetWindowLongW(self._hwnd, _GWL_EXSTYLE,
                             ex | _WS_EX_LAYERED | _WS_EX_TOOLWINDOW)
            # ⚠ 对**已经显示**的窗口改扩展样式必须 SWP_FRAMECHANGED 才生效，
            # 否则样式写进去了、ULW 却仍然失败（返回 0），窗口只剩自己的底色。
            u.SetWindowPos(self._hwnd, 0, 0, 0, 0, 0,
                           0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
            return True
        except Exception:
            return False

    def _upload(self, img):
        if not self._ok:
            return
        # 预乘 alpha：整条边有 2 万多像素，Python 逐像素循环要 4~5ms，
        # 用 numpy 一次算完（约 0.4ms）。
        try:
            import numpy as np
            arr = np.asarray(img.convert("RGBA"), dtype=np.uint16)
            a = arr[:, :, 3:4]
            arr[:, :, :3] = (arr[:, :, :3] * a) // 255
            buf = np.ascontiguousarray(arr[:, :, [2, 1, 0, 3]].astype(np.uint8)).tobytes()
        except Exception:
            buf = bytearray(img.convert("RGBA").tobytes("raw", "BGRA"))
            for i in range(0, len(buf), 4):
                a = buf[i + 3]
                if a != 255:
                    buf[i] = buf[i] * a // 255
                    buf[i + 1] = buf[i + 1] * a // 255
                    buf[i + 2] = buf[i + 2] * a // 255
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        w, h = img.size
        hdc_screen = u.GetDC(0)
        hdc_mem = g.CreateCompatibleDC(hdc_screen)
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        hbmp = g.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0,
                                  ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, bytes(buf), len(buf))
        g.SelectObject(hdc_mem, hbmp)
        u.ReleaseDC(0, hdc_screen)
        old = self._dib
        self._dib = (hbmp, hdc_mem, w, h)
        self._blit()
        if old:
            try:
                g.DeleteObject(old[0])
                g.DeleteDC(old[1])
            except Exception:
                pass

    def _blit(self):
        if not self._dib:
            return
        hbmp, hdc_mem, w, h = self._dib
        x, y = self._pos
        u = ctypes.windll.user32
        hdc_screen = u.GetDC(0)
        pt_dst = wintypes.POINT(int(x), int(y))
        pt_src = wintypes.POINT(0, 0)
        size = wintypes.SIZE(w, h)
        常 = int(getattr(self, "_alpha", 255))
        blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, max(0, min(255, 常)), _AC_SRC_ALPHA)
        u.UpdateLayeredWindow(self._hwnd, hdc_screen, ctypes.byref(pt_dst),
                              ctypes.byref(size), hdc_mem, ctypes.byref(pt_src),
                              0, ctypes.byref(blend), _ULW_ALPHA)
        u.ReleaseDC(0, hdc_screen)

    def destroy(self):
        self.hide()
        try:
            self.win.destroy()
        except Exception:
            pass



class NetFloat:
    """`get_history()` 返回 [(时间戳, 毫秒, 连通源数, 源总数), ...]。"""

    D = 56
    MIN_D = 40
    MAX_D = 96
    TAB_W = 22          # 收纳成小标签时的宽度（设计像素）
    SNAP = 8            # 离边多近算贴边（设计像素；14 太灵敏，一靠近就吸）

    def __init__(self, root, get_history, on_open_chart, on_close=None,
                 size=None, shadow=True, dock=True, bg="#f0f0f0",
                 x=None, y=None, on_drag_state=None, edge_fx=True):
        self.root = root
        self.get_history = get_history
        self.on_open_chart = on_open_chart
        self.on_close = on_close
        self.on_drag_state = on_drag_state
        self.edge_fx = bool(edge_fx)
        self.shadow = bool(shadow)
        self.dock_enabled = bool(dock)
        self.bg = bg or "#f0f0f0"

        try:
            self.D = max(self.MIN_D, min(self.MAX_D, int(size or self.D)))
        except Exception:
            self.D = 56
        self.d = px(self.D)
        self.tab_w = px(self.TAB_W)

        self._rel = [0, 0]            # 相对主窗口客户区左上角
        self._drag = {"dx": 0, "dy": 0, "on": False, "moved": False,
                      "job": None, "pending": False}
        self._hover = False
        self._collapse_job = None
        self._hover_job = None     # 贴边悬停轮询（不信 Enter/Leave，见 _poll_hover）
        self._morph = None         # 交叉溶解状态（见 _redraw_faded）
        self._morph_job = None
        self._shape_cache = {}     # 尺寸 -> 图标图（换形态时两边都要用）
        self._hint = None          # 正在"将要吸附"的那条边（None = 无提示）
        self._hint_job = None
        self._hint_until = 0.0
        self._glow = None
        self._alpha = 255          # 整体不透明度（淡入淡出用）
        self._fade_job = None
        self._fade_token = 0
        self._fade_hr = False       # 是否占着 1ms 定时器精度（见 _fade_to）
        self._show_job = None
        self._SHOW_DELAY = 150      # 主窗口出现后等这么久再露面（对齐系统显示动画）
        self._peek = False
        self._docked = None
        self._job = None
        self._photo = None
        self._stop = False
        self._dib = None
        self._shown = True
        self._last_geom = None
        self._geom = {"x": 0, "y": 0, "w": 0, "h": 0}
        self._follow_job = None
        self._layered = False
        self._hwnd = 0

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        # ⚠ 不置顶：置顶会让它压在**别的程序**上面，很干扰。
        # 代价是主窗口一被抬起它就会被盖住，所以每次本程序回到前台都 lift() 一下
        # （见 _show / _on_root_focus_in）。这样它永远只在"我们自己的窗口之上"。
        # 不置顶了，失焦不用再降级；真正的"藏起来"由 _sync_visibility 做
        pass
        # ⚠ 不能用 tk 的 transient()：它会让 Tk 把这个窗口变成**子窗口**（实测
        # style 里 WS_CHILD 置位），子窗口被父窗口裁剪、坐标也变成客户区相对，
        # 于是 geometry 按屏幕坐标摆位就永远画不出来。
        # 改成用 Win32 设"归属窗口"（GWLP_HWNDPARENT）：仍是独立的弹出窗口，
        # 透明色键照常生效，同时会跟着主窗口最小化、并排在主窗口之上。
        # 归属窗口暂不设置：实测把 GWLP_HWNDPARENT 指到 root.wm_frame() 之后
        # 窗口就再也不显示了（可能拿到了非顶层句柄）。这里先只保证显示，
        # 同层/跟随最小化交给 Tk 的 transient 之外的机制（见 README 说明）。
        pass
        # 逐像素透明：Tk 的 -transparentcolor 在部分机器上不生效（会留下一块
        # 方形底色），所以优先用分层窗口 + UpdateLayeredWindow。
        try:
            self.win.update_idletasks()
            self._hwnd = int(self.win.wm_frame(), 16)
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
            u.SetWindowLongW(self._hwnd, _GWL_EXSTYLE,
                             ex | _WS_EX_LAYERED | _WS_EX_TOOLWINDOW)
            self._layered = True
        except Exception:
            self._layered = False
        if not self._layered:
            try:
                self.win.attributes("-transparentcolor", self.bg)
            except Exception:
                pass
        self.win.configure(bg=self.bg)

        # ⚠ 分层窗口里**不能有子控件**：Canvas 会用自己的底色盖掉 ULW 画的内容
        # （表现就是圆外面多出一块纯色方块）。所以只有退回色键方案时才建 Canvas。
        self.canvas = None
        if not self._layered:
            self.canvas = tk.Canvas(self.win, bg=self.bg, highlightthickness=0, bd=0,
                                    cursor="hand2")
            self.canvas.pack(fill="both", expand=True)

        # ⚠ 千万不要在创建时 withdraw()：实测对 Tk 顶层窗口 withdraw 之后再 deiconify，
        # UpdateLayeredWindow 会一直返回 0（窗口只剩自己的底色 = 一块深色方块）。
        # "隐藏"改成把窗口挪到屏幕外 + alpha 0。
        self._parked = False
        try:
            root.bind("<Destroy>", self._on_root_destroy, add="+")
        except Exception:
            pass
        self._bind()
        self._menu()
        self._restore(x, y)
        try:
            该显示 = self._root_should_show()
        except Exception:
            该显示 = True
        if 该显示:
            self._alpha = 255
            self._redraw()
        else:
            # 启动阶段：闪屏还在、或主窗口还没露出、或本程序还没到前台 ——
            # 这时候露脸就会"闪一下"。先画一张完全透明的，再挪到屏幕外。
            self._shown = False
            self._alpha = 0
            self._redraw()
            self._park()
        self._tick()          # ⚠ 必须启动刷新循环，否则数字会停在初始化那一刻
        try:
            self._refresh_geom()
            self._last_geom = (self._geom["x"], self._geom["y"],
                               self._geom["w"], self._geom["h"])
            self._follow_job = root.after(40, self._follow_tick)
        except Exception:
            pass

    # ---------------- 坐标 ----------------
    def _refresh_geom(self):
        """缓存主窗口位置/尺寸。

        拖动浮标时每帧都要用它算坐标，而 winfo_rootx/winfo_width 都是 Tcl 往返
        （一次 ~0.05ms，四个就是 0.2ms），60~125fps 下很可观。改成由
        <Configure> 和 100ms 看门狗刷新缓存，拖动时直接读内存。
        """
        try:
            self._geom = {"x": self.root.winfo_rootx(), "y": self.root.winfo_rooty(),
                          "w": self.root.winfo_width(), "h": self.root.winfo_height()}
        except Exception:
            pass

    def _parent_size(self):
        w, h = self._geom.get("w") or 0, self._geom.get("h") or 0
        if w > 1 and h > 1:
            return w, h
        try:
            return (max(self.d, self.root.winfo_width()),
                    max(self.d, self.root.winfo_height()))
        except Exception:
            return 1920, 1080

    def _cur_size(self):
        m = self._morph
        if m is not None:                 # 交叉溶解期间：用两边的较大尺寸承载
            return m["size"]
        if self._docked and self.dock_enabled and not self._peek:
            if self._docked in ("left", "right"):
                return (self.tab_w, self.d)        # 竖条
            return (self.d, self.tab_w)            # 横条（贴上下边时）
        return (self.d, self.d)

    def _clamp(self, x, y):
        w, h = self._parent_size()
        cw, ch = self._cur_size()
        return max(0, min(int(x), w - cw)), max(0, min(int(y), h - ch))

    def _restore(self, x, y):
        try:
            x, y = int(x), int(y)
        except Exception:
            x = y = -1
        if x < 0 or y < 0:
            w, h = self._parent_size()
            x, y = w - self.d - px(18), h - self.d - px(64)
        self._rel = list(self._clamp(x, y))
        self._apply()

    def _abs_pos(self):
        m = self._morph
        if m is not None:                 # 交叉溶解期间：位置也由过渡接管
            return (self._geom.get("x", 0) + m["rel"][0],
                    self._geom.get("y", 0) + m["rel"][1])
        if self._geom.get("w"):
            return self._geom["x"] + self._rel[0], self._geom["y"] + self._rel[1]
        try:
            return (self.root.winfo_rootx() + self._rel[0],
                    self.root.winfo_rooty() + self._rel[1])
        except Exception:
            return self._rel[0], self._rel[1]

    def _has_other_window(self):
        """本程序是不是还有别的窗口开着（设置 / 历史 / 曲线图 / 各种对话框）。"""
        try:
            for w in self.root.winfo_children():
                if w is self.win:
                    continue
                if isinstance(w, tk.Toplevel):
                    try:
                        if w.winfo_viewable():
                            return True
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    def _park(self):
        """把窗口挪到屏幕外（代替 withdraw，withdraw 之后 ULW 会彻底失效）。"""
        self._parked = True
        try:
            ctypes.windll.user32.SetWindowPos(
                int(self._hwnd), 0, -32000, -32000, 0, 0,
                0x0001 | 0x0004 | 0x0010)      # NOSIZE | NOZORDER | NOACTIVATE
        except Exception:
            pass

    def _apply(self):
        """只挪位置：用 SetWindowPos，不重传位图（比 ULW 快 18 倍）。

        UpdateLayeredWindow 每次调用都会重设整张表面 + 触发重合成（实测 0.108ms），
        而拖动时内容根本不变，只需要挪窗口 —— SetWindowPos 是 0.006ms。
        内容/尺寸变化时才走 _upload（那里才调用 ULW）。
        """
        if getattr(self, "_parked", False):
            return
        ax, ay = self._abs_pos()
        if self._layered and self._hwnd and self._dib:
            try:
                ctypes.windll.user32.SetWindowPos(
                    int(self._hwnd), 0, int(ax), int(ay), 0, 0,
                    0x0001 | 0x0004 | 0x0010)      # NOSIZE | NOZORDER | NOACTIVATE
                return
            except Exception:
                pass
        cw, ch = self._cur_size()
        try:
            self.win.geometry("%dx%d+%d+%d" % (cw, ch, int(ax), int(ay)))
        except Exception:
            pass

    # ---------------- 分层窗口位图 ----------------
    def _blit(self, dib, x, y):
        hbmp, hdc_mem, w, h = dib
        u = ctypes.windll.user32
        hdc_screen = u.GetDC(0)
        pt_dst = wintypes.POINT(int(x), int(y))
        pt_src = wintypes.POINT(0, 0)
        size = wintypes.SIZE(w, h)
        # ⚠ 这里是 ULW 的 SourceConstantAlpha，必须用当前 _alpha：以前写死 255，
        # 于是 _fade_to() 只改 self._alpha 却合成不出任何变化 —— 实测同一窗口
        # alpha=255/0/128 截屏像素完全一致，也就是"溶解式换形态"用的是硬切，
        # 收纳时看着就是闪一下。（同文件的 _EdgeGlow._blit 一直是正确写法。）
        blend = _BLENDFUNCTION(_AC_SRC_OVER, 0,
                               max(0, min(255, int(getattr(self, "_alpha", 255)))),
                               _AC_SRC_ALPHA)
        u.UpdateLayeredWindow(self._hwnd, hdc_screen, ctypes.byref(pt_dst),
                              ctypes.byref(size), hdc_mem, ctypes.byref(pt_src),
                              0, ctypes.byref(blend), _ULW_ALPHA)
        u.ReleaseDC(0, hdc_screen)

    def _upload(self, img):
        from PIL import Image
        img = img.convert("RGBA")
        w, h = img.size
        buf = bytearray(img.tobytes("raw", "BGRA"))
        for i in range(0, len(buf), 4):          # 预乘 alpha
            a = buf[i + 3]
            if a != 255:
                buf[i] = buf[i] * a // 255
                buf[i + 1] = buf[i + 1] * a // 255
                buf[i + 2] = buf[i + 2] * a // 255
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        hdc_screen = u.GetDC(0)
        hdc_mem = g.CreateCompatibleDC(hdc_screen)
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        hbmp = g.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0,
                                  ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, bytes(buf), len(buf))
        g.SelectObject(hdc_mem, hbmp)
        u.ReleaseDC(0, hdc_screen)
        old = self._dib
        self._dib = (hbmp, hdc_mem, w, h)
        ax, ay = self._abs_pos()
        self._blit(self._dib, ax, ay)
        if old:                                  # 换掉旧位图
            try:
                g.DeleteObject(old[0])
                g.DeleteDC(old[1])
            except Exception:
                pass

    def position(self):
        return tuple(self._rel)

    # ---------------- 贴边 ----------------
    def _detect_edge(self):
        w, h = self._parent_size()
        cw, ch = self._cur_size()
        x, y = self._rel
        近 = px(self.SNAP)
        左 = x <= 近
        右 = (w - cw - x) <= 近
        上 = y <= 近
        下 = (h - ch - y) <= 近
        if 左 and not 右:
            return "left"
        if 右 and not 左:
            return "right"
        if 上 and not 下:
            return "top"
        if 下 and not 上:
            return "bottom"
        return None

    def _snap(self, edge, apply=True):
        """吸附到某条边。

        apply=False 时**只算位置、不动窗口** —— 展开/收缩会同时改尺寸，如果先
        SetWindowPos 挪位置（尺寸还是旧的），中间会闪过一帧"90px 的圆被挪到窗口
        右边外面"，看起来就是抖一下。此时交给随后的 _redraw（一次
        UpdateLayeredWindow 同时设尺寸和位置）原子完成。
        """
        self._docked = edge
        w, h = self._parent_size()
        cw, ch = self._cur_size()
        x, y = self._rel
        if edge == "left":
            x = 0
        elif edge == "right":
            x = w - cw
        elif edge == "top":
            y = 0
        elif edge == "bottom":
            y = h - ch
        self._rel = [int(x), int(y)]
        if apply:
            self._apply()
        if edge:
            self._ensure_hover_poll()

    # ---------------- 交互 ----------------
    def _target(self):
        return self.canvas if self.canvas is not None else self.win

    def _bind(self):
        c = self._target()
        c.bind("<Button-1>", self._on_press)
        # 不绑 <B1-Motion>：位置由 _move_to_cursor 的定时器读光标得到。
        # 高速拖动时每秒几百个 Motion 事件全进 Python 只会拖慢主循环（实测会出现
        # 40ms+ 的掉帧），而它们是多余的。
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Button-3>", self._popup)
        c.bind("<Enter>", self._on_enter)
        c.bind("<Leave>", self._on_leave)
        try:
            self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        except Exception:
            pass
        try:
            self.root.bind("<Configure>", self._on_root_configure, add="+")
            # 跟着主窗口显示/隐藏：最小化、收进托盘时浮标也必须消失，
            # 否则它会孤零零留在桌面上（"不同图层"最明显的毛病）。
            self.root.bind("<Map>", self._on_root_map, add="+")
            self.root.bind("<Unmap>", self._on_root_unmap, add="+")
            self.root.bind("<FocusIn>", self._on_root_focus_in, add="+")
            self.root.bind("<FocusOut>", self._on_root_focus_out, add="+")
        except Exception:
            pass

    def _on_root_map(self, ev):
        if getattr(ev, "widget", None) is not self.root:
            return
        try:
            if self.root.state() == "iconic":
                return
        except Exception:
            pass
        self._request_show()

    def _on_root_destroy(self, ev):
        """主窗口被销毁 = 程序正在退出：浮标立刻消失，别留着比窗口还久。"""
        try:
            if getattr(ev, "widget", None) is not self.root:
                return
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass

    def _on_root_unmap(self, ev):
        if getattr(ev, "widget", None) is not self.root:
            return
        try:
            立即 = self.root.state() == "withdrawn"
        except Exception:
            立即 = False
        self._hide(fade=not 立即)

    def _on_root_focus_in(self, ev):
        """主窗口回到前台：浮标重新置顶并重画（隐藏过之后 ULW 内容要重传）。"""
        if getattr(ev, "widget", None) is not self.root:
            return
        try:
            if not self._has_other_window():
                self.win.lift()      # 主窗口回到前台：抬回它上面（有别的窗口时让开）
        except Exception:
            pass
        self._request_show()

    def _on_root_focus_out(self, ev):
        """主窗口失去焦点：取消置顶，免得它压在其他程序上面。

        ⚠ 如果焦点只是转到了**本程序自己的另一个窗口**（比如刚打开的曲线图），
        就不能降级 —— 否则点一下浮标打开图，浮标自己就沉到图后面"消失"了。
        """
        if getattr(ev, "widget", None) is not self.root:
            return
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            if pid.value == os.getpid():
                return
        except Exception:
            pass
        try:
            self.win.attributes("-topmost", False)
        except Exception:
            pass

    def _fade_to(self, 目标, 时长=110, 完成=None):
        """用 UpdateLayeredWindow 的常量 alpha 做淡入淡出（不重画位图，只改 alpha）。

        Windows 的最小化/还原本身有 ~200ms 的动画，浮标如果"啪"地消失/出现，
        就会和主窗口一前一后错开，看起来很生硬。

        ⚠ 两处别改回去：
        1. 必须按**真实经过时间**插值。以前是 after(16) + 固定步数（步=时长/16），
           而真 mainloop 里 after(16) 有一半的帧会滑到 31ms（15.6ms 定时器粒度，
           见 utils/anim.py），于是 45ms 的淡出实际只有 2~3 帧还时快时慢 —— 看着就是闪。
        2. 必须让 _blit 用上 _alpha（见 _blit 的注释），否则这里怎么算都不生效。
        """
        self._fade_token += 1
        令 = self._fade_token
        if self._fade_job:
            try:
                self.root.after_cancel(self._fade_job)
            except Exception:
                pass
            self._fade_job = None
        起 = self._alpha
        t0 = time.perf_counter()
        总 = max(1.0, float(时长))
        # 每条淡入淡出自己一对 hold/release（先还旧的再占新的）。不这么做的话，
        # 上一条链的收尾会把**新链**占着的定时器精度还掉，新链于是掉回 15.6ms 粒度、
        # 帧间隔变得忽长忽短 —— 又回到"闪"的老样子。
        if self._fade_hr:
            self._fade_hr = False
            try:
                anim.release()
            except Exception:
                pass
        self._fade_hr = True
        try:
            anim.hold()              # 淡入淡出这几百毫秒要毫秒级定时器，见 anim.py
        except Exception:
            pass

        def 结束(谁=None):
            if 谁 is not None and 谁 != self._fade_token:
                return               # 已经换成新的一条链了，别动它的占用
            self._fade_job = None
            if self._fade_hr:
                self._fade_hr = False
                try:
                    anim.release()
                except Exception:
                    pass

        def 帧():
            if 令 != self._fade_token or self._stop:
                结束(令)
                return
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / 总)
            self._alpha = int(round(起 + (目标 - 起) * k))
            try:
                if self._dib:
                    self._blit(self._dib, *self._abs_pos())
            except Exception:
                pass
            if k < 1.0:
                try:
                    self._fade_job = self.root.after(anim.frame_ms(self.win), 帧)
                except Exception:
                    结束(令)
            else:
                self._alpha = 目标
                结束(令)
                if 完成:
                    try:
                        完成()
                    except Exception:
                        pass

        帧()

    _MORPH_MS = 70         # 交叉溶解总时长（130 太拖；70ms 仍看得出是溶解不是硬跳）

    def _shape_img(self, size):
        """按尺寸取图标图（缓存）。换形态时两边的图都要，不能各渲染两次。"""
        # ⚠ 别把 _hover 放进键里：悬停进出时它会变，于是每次都命中不了缓存、
        # 每次换形态都同步渲染两张图（实测 4~6 次 _image、16~18ms 的起步卡顿）。
        # 两种悬停态由 _warm_shapes() 一次性预热。
        # _hint 也不进键：形态图不需要提示环（提示环由 _hint_frame_list 单独上传），
        # 带上它会让"收纳时亮一下边框"顺带把缓存打掉。
        键 = (size, self._hover, self.shadow, self.bg)
        图 = self._shape_cache.get(键)
        if 图 is not None:
            return 图
        图 = self._image()
        self._shape_cache[键] = 图            # 键带悬停态：进出两种都要留住，
        return 图                             # 只按尺寸存会被对方顶掉，等于白预热

    def _warm_shapes(self):
        """预热"两种形态 × 两种悬停态"的图标图（共 4 张，约 40ms，放在贴边后的空闲时刻）。

        为什么要有：交叉溶解起步要拿到旧、新两张图，各一次 _image()（4~12ms）。
        不预热时这一步就发生在鼠标刚碰上去的那一瞬 —— 实测每次换形态同步耗时
        16~18ms、4~6 次 _image，也就是"反应有点慢"的第一下卡顿。
        """
        if not self._docked or getattr(self, "_warmed_for", None) == self._docked:
            return
        self._warmed_for = self._docked
        try:
            保持图, 保持悬 = self._peek, self._hover
            try:
                for 悬 in (False, True):
                    self._hover = 悬
                    for 展开 in (False, True):
                        self._peek = 展开
                        self._shape_img(self._cur_size())
            finally:
                self._peek, self._hover = 保持图, 保持悬
        except Exception:
            pass

    def _anchor_paste(self, img, box):
        """把 img 按贴边锚点摆进 box 大小的透明画布（右/下边对齐，其余靠左上）。"""
        from PIL import Image
        W, H = box
        w, h = img.size
        x = W - w if self._docked == "right" else 0
        y = H - h if self._docked == "bottom" else 0
        画布 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        画布.paste(img, (x, y))
        return 画布

    def _redraw_faded(self):
        """换形态（圆 ⇄ 小标签）：**交叉溶解** —— 旧形态淡出的同时新形态淡入，全程不消失。

        ⚠ 别改回"淡出 → 换尺寸 → 淡入"：那样浮标会先彻底透明 45ms 再淡回来，
        看起来就是眨一下（用户报"会闪一下"）。
        做法：过渡期窗口临时用两边的**较大尺寸**，把两张图按贴边锚点对到同一画布上，
        逐帧 PIL 混合后上传；结束时 _peek 已翻好，一次 _redraw 落到目标尺寸
        （贴边锚点不变，所以这一步看不见）。
        """
        if not self._dib or not self._docked:
            self._redraw()
            return
        try:
            from PIL import Image
            # 入口时 _peek 已经是**新**值（调用方先翻好再进来），所以新图直接取，
            # 旧图要临时翻回去渲染 —— 顺序反了就会倒着播，结束时还会跳一下。
            新尺寸 = self._cur_size()
            新图 = self._shape_img(新尺寸)
            self._peek = not self._peek
            try:
                旧尺寸 = self._cur_size()
                旧图 = self._shape_img(旧尺寸)
            finally:
                self._peek = not self._peek      # 真正的翻转等溶解结束再落定
            盒 = (max(旧尺寸[0], 新尺寸[0]), max(旧尺寸[1], 新尺寸[1]))
            A = self._anchor_paste(旧图, 盒)
            B = self._anchor_paste(新图, 盒)
        except Exception:
            self._redraw()
            return
        W, H = A.size
        pw, ph = self._parent_size()
        x = (pw - W) if self._docked == "right" else (0 if self._docked == "left" else self._rel[0])
        y = (ph - H) if self._docked == "bottom" else (0 if self._docked == "top" else self._rel[1])
        self._morph = {"size": (W, H), "rel": [int(x), int(y)], "A": A, "B": B, "i": 0}
        if self._morph_job is not None:
            try:
                self.root.after_cancel(self._morph_job)
            except Exception:
                pass
            self._morph_job = None
        if not self._fade_hr:
            self._fade_hr = True
            try:
                anim.hold()
            except Exception:
                pass
        t0 = time.perf_counter()
        总 = float(self._MORPH_MS)

        def 帧():
            m = self._morph
            if m is None or self._stop:
                return
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / 总)
            try:
                self._upload(Image.blend(m["A"], m["B"], k))
            except Exception:
                pass
            if k < 1.0:
                try:
                    self._morph_job = self.root.after(anim.frame_ms(self.win), 帧)
                except Exception:
                    self._morph_job = None
            else:
                self._morph_job = None
                self._morph = None
                # _peek 在渲染两张图时已经还原成**新**值，这里千万别再翻一次
                # （翻了就会停在原地：实测收起后仍是 70、展开后仍是 28）。
                try:
                    self._snap(self._docked, apply=False)
                except Exception:
                    pass
                self._redraw()
                if self._fade_hr:
                    self._fade_hr = False
                    try:
                        anim.release()
                    except Exception:
                        pass

        帧()

    def _show(self):
        # 统一在这里把门：主窗口没露出、或本程序不在前台，就绝不显示。
        # 否则 <Map> / <FocusIn> 一来就先淡入，40ms 后看门狗又把它藏掉 —— 看着就是闪一下。
        try:
            if not self._root_should_show():
                self._shown = False
                self._alpha = 0
                self.win.withdraw()
                return
        except Exception:
            pass
        # "刚打开"现在以 _parked 为准（不再用 withdraw 表达隐藏）
        刚打开 = bool(getattr(self, "_parked", False))
        self._parked = False
        self._shown = True
        self._parked = False
        if 刚打开:
            self._alpha = 0                 # 从全透明开始，再淡入
            try:
                self.win.deiconify()
                self.win.update_idletasks()
                self.win.update()
            except Exception:
                pass
            # 显示之后才是真正的顶层句柄，样式也可能被 Tk 重置 —— 补一次再画
            try:
                self._hwnd = self._real_hwnd() or self._hwnd
                self._apply_layered()
            except Exception:
                pass
        self._redraw()
        try:
            if not self._has_other_window():
                self.win.lift()      # 抬到主窗口之上（不置顶，不压住别的程序）
        except Exception:
            pass
        if 刚打开:
            self._fade_to(255, 120)

    def _hide(self, fade=True):
        self._shown = False
        # 主窗口被挡住/最小化时，发光带也要一起收掉（它是独立的置顶窗口）
        self._hint = None
        self._hint_until = 0.0
        if getattr(self, "_glow", None) is not None:
            try:
                self._glow.hide()
            except Exception:
                pass
        if not fade or self._drag["on"]:
            # 程序正在关闭 / 拖动中：立刻收，不做淡出
            self._alpha = 0
            self._park()
            return
        self._fade_to(0, 90, self._park)

    def _on_root_configure(self, ev):
        try:
            if ev.widget is not self.root:
                return
        except Exception:
            pass
        self._sync_geometry()

    def _sync_geometry(self):
        """窗口移动/缩放后让浮标跟上。

        · 移动：位置存的是相对偏移，重算绝对坐标即可；
        · 缩放：先把偏移夹回新的客户区（否则窗口变小后浮标会留在外面），
          再按贴边状态重新吸附 —— 收纳成小标签时也要贴着新的边。
        """
        self._refresh_geom()
        try:
            self._last_geom = (self._geom["x"], self._geom["y"],
                               self._geom["w"], self._geom["h"])
        except Exception:
            pass
        self._rel = list(self._clamp(*self._rel))
        if self._docked:
            self._snap(self._docked)
        else:
            self._apply()

    def _follow_tick(self):
        """100ms 看门狗：<Configure> 在拖动窗口时可能被合并/延迟，这里兜底。"""
        if self._stop:
            return
        try:
            geom = (self.root.winfo_rootx(), self.root.winfo_rooty(),
                    self.root.winfo_width(), self.root.winfo_height())
            if geom != self._last_geom:
                self._refresh_geom()
                self._sync_geometry()
            self._sync_visibility()
            # 不置顶的情况下，主窗口一旦被抬起就会盖住浮标。这里每 ~240ms 把它抬回来
            # （只是 SetWindowPos 换 z 序，成本可忽略），保证它始终贴在我们窗口之上。
            self._lift_n = getattr(self, "_lift_n", 0) + 1
            # 只有"本程序没有别的窗口开着"时才把自己抬到主窗口之上 ——
            # 否则设置/历史/曲线图这些窗口会被浮标压住（用户说的"不在同一层"）。
            if (self._shown and self._lift_n % 6 == 0
                    and not self._has_other_window()):
                try:
                    self.win.lift()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._follow_job = self.root.after(40, self._follow_tick)
        except Exception:
            pass

    def _on_press(self, e):
        self._drag.update(dx=e.x, dy=e.y, on=True, moved=False, ending=False)
        try:
            ctypes.windll.user32.SetCapture(int(self._hwnd or 0))
        except Exception:
            pass
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            self._timer_fine = True
        except Exception:
            self._timer_fine = False
        # 按下期间由**定时器**驱动移动：拖快时指针瞬间就离开浮标范围，
        # Tk 不再发 <B1-Motion>，事件驱动的实现会停在原地。
        self._notify_drag(True)
        self._move_to_cursor()
        self._drag["job"] = self.root.after(8, self._flush_drag)

    def _notify_drag(self, 在拖):
        try:
            if self.on_drag_state:
                self.on_drag_state(bool(在拖))
        except Exception:
            pass

    def _on_drag(self, e):
        """只标记"动过了"，位置一律由 _move_to_cursor 现取光标算。"""
        if self._drag["on"]:
            self._drag["moved"] = True

    def _move_to_cursor(self):
        """把浮标挪到"光标 - 抓取偏移"的位置（不依赖任何事件坐标）。"""
        pt = wintypes.POINT()
        try:
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return False
        except Exception:
            return False
        nx = pt.x - self._geom["x"] - self._drag["dx"]
        ny = pt.y - self._geom["y"] - self._drag["dy"]
        动 = (abs(nx - self._rel[0]) > 1 or abs(ny - self._rel[1]) > 1)
        if 动:
            if self._drag["on"]:
                self._drag["moved"] = True
            if self._docked:                 # 真的移动了才取消收纳
                self._docked = None
                self._peek = False
                self._redraw()
        self._rel = list(self._clamp(nx, ny))
        self._apply()
        if self._drag["on"] and self._drag["moved"]:
            # 拖动中：离哪条边够近就亮哪条边的边框提示（松手会收纳）
            self._set_hint(self._detect_edge())
            if self._hint:
                self._update_glow()      # 跟着浮标移动
        return True

    def _flush_drag(self):
        self._drag["job"] = None
        if not self._drag["on"] or self._drag.get("ending"):
            return
        # 左键其实已经松了（松在窗口外 / 被别的程序抢走焦点）：直接收尾
        try:
            if not (ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000):
                self._on_release(None)
                return
        except Exception:
            pass
        if not self._move_to_cursor():
            return
        if self._drag["on"]:
            try:
                self._drag["job"] = self.root.after(8, self._flush_drag)
            except Exception:
                pass

    def _on_release(self, _e):
        # ⚠ 必须防重入：旧实现在这里调用 _flush_drag，而 _flush_drag 的兜底分支
        # 又会回调 _on_release，于是 _open_chart 被调用两次 —— 表现就是
        # "点一下浮标，窗口打开又立刻关上"。
        if not self._drag["on"] or self._drag.get("ending"):
            return
        self._drag["ending"] = True
        try:
            try:
                if self._drag.get("job"):
                    self.root.after_cancel(self._drag["job"])
            except Exception:
                pass
            self._drag["job"] = None
            self._drag["on"] = False
            self._move_to_cursor()          # 最后一帧，避免停在旧位置
            try:
                ctypes.windll.user32.ReleaseCapture()
            except Exception:
                pass
            if getattr(self, "_timer_fine", False):
                try:
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass
                self._timer_fine = False
            if self._drag["moved"]:
                edge = self._detect_edge()
                if edge:
                    self._docked = edge
                    self._snap(edge, apply=False)
                    self._redraw_faded()
                    self._set_hint(edge, 持续=0.4)   # 收纳成功后边框再亮一下
                else:
                    self._docked = None
                    self._rel = list(self._clamp(*self._rel))
                    self._apply()
                if self.on_close:
                    self.on_close(self, False)
            else:
                self._open_chart()
        finally:
            self._drag["ending"] = False
            self._notify_drag(False)

    def _on_enter(self, _e):
        # 拖动中指针会反复进出浮标（窗口跟着动），此时不该重画：图标是 6 倍超采样，
        # 一次 20~30ms，拖快一点就是"中间掉了几帧"。松手后再按真实悬停状态重画。
        if self._drag["on"]:
            return
        self._hover = True
        # 取消还没执行的收缩（光标只是"闪"出去一下又回来了）
        if self._collapse_job:
            try:
                self.root.after_cancel(self._collapse_job)
            except Exception:
                pass
            self._collapse_job = None
        # 贴边时交给轮询按**真实光标**判定（事件在几何变化时会撒谎，见 _poll_hover）
        if self._docked:
            self._hover_update()
            self._ensure_hover_poll()
        else:
            self._redraw()
        # 已经是展开态：什么都不用做（别每来一次 <Enter> 就重画一遍）

    def _on_leave(self, _e):
        """离开时延迟 180ms 再收缩，别立刻收。

        ⚠ 展开会让窗口变大并左移，Tk/Windows 在几何变化时会再判一次 <Leave>：
        立刻收缩 → 光标又落回小标签 → 再 <Enter> → 再展开……实测 0.8 秒里抖了
        7 个来回（ENLVENLV…）。延迟一下就把这种"几何变化引起的假离开"滤掉了。
        """
        if self._drag["on"]:
            return
        # ⚠ 这里**只**更新状态、不动几何：几何变化会招来假 <Leave>。
        # 真正的收缩决定交给 _poll_hover 按真实光标位置做 —— 只丢弃假事件是不行的：
        # 假 <Leave> 被吃掉之后，用户**真的**移开时 Windows 也不会再补发一次，
        # 展开态就永远收不回去（用户实测"有概率无法收纳"）。
        if self._docked:
            self._hover_update()
            self._ensure_hover_poll()
        else:
            self._hover = False
            self._redraw()

    def _cursor_inside(self, 余量=2):
        """光标是否**真的**还在浮标窗口里（GetCursorPos + 窗口矩形）。

        ⚠ 别用 <Enter>/<Leave> 判断这件事：展开/收缩会改窗口矩形，Windows 在几何变化
        时会再判一次 <Leave> —— 那是"假离开"。据它收缩就会进死循环：收缩 → 光标落回
        标签 → <Enter> → 展开 → 假 <Leave> → 收缩……（实测光标**静止不动**时 2.2 秒里
        换了 5 次形态、还发起 2 次收缩，看着就是不停闪）。180ms 延迟只是把它拖慢，
        没根治；这里直接用真实光标位置做判定，几何变化骗不了它。
        """
        try:
            pt = wintypes.POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return False
            ax, ay = self._abs_pos()
            cw, ch = self._cur_size()
            return ((ax - 余量) <= pt.x <= (ax + cw + 余量)
                    and (ay - 余量) <= pt.y <= (ay + ch + 余量))
        except Exception:
            return False

    _HOVER_POLL_MS = 60
    # 离开后等这么久才收起：轮询已经在 60ms 内确认过光标位置了，不需要再留长缓冲
    _COLLAPSE_DELAY_MS = 110

    def _hover_update(self):
        """按**真实光标位置**决定"展开 / 收起"。这是唯一的判定入口。"""
        if self._stop or self._parked or self._drag["on"]:
            return
        if not (self._docked and self.dock_enabled):
            return
        if self._cursor_inside():
            self._hover = True
            if self._collapse_job:
                try:
                    self.root.after_cancel(self._collapse_job)
                except Exception:
                    pass
                self._collapse_job = None
            if not self._peek:
                self._peek = True
                self._snap(self._docked, apply=False)   # 尺寸+位置交给 _redraw 一次做完
                self._redraw_faded()
        else:
            self._hover = False
            if self._peek and self._collapse_job is None:
                try:
                    self._collapse_job = self.root.after(self._COLLAPSE_DELAY_MS,
                                                         self._collapse_now)
                except Exception:
                    self._collapse_job = None

    def _ensure_hover_poll(self):
        if getattr(self, "_polling", False):
            return                    # 轮询里又走到这儿，别开第二条链
        if self._hover_job is None and self._docked and not self._stop:
            self._poll_hover()
        if getattr(self, "_warmed_for", None) != self._docked:
            try:                       # 趁着用户还没碰，先把两张形态图渲染好
                self.root.after(150, self._warm_shapes)
            except Exception:
                pass

    def _poll_hover(self):
        """贴边状态下每 120ms 巡一次真实光标。

        为什么不能只靠 <Enter>/<Leave>：展开/收缩会改窗口矩形，Windows 在几何变化时
        会再判一次 <Leave>（假离开）。靠事件两头都会出事 ——
          信它 → 收起/展开来回抖（实测光标静止 2.2 秒换了 5 次形态）；
          丢它 → 假事件被吃掉之后，用户**真的**移开时也不再有新事件，
                 展开态就永远收不回去（"有概率无法收纳"）。
        轮询没有状态残留，收/展只取决于"光标现在在哪"。
        """
        self._hover_job = None
        if self._stop or self._parked or not (self._docked and self.dock_enabled):
            return
        self._polling = True
        try:
            self._hover_update()
        finally:
            self._polling = False
        if not (self._stop or self._parked or not self._docked):
            try:
                self._hover_job = self.root.after(self._HOVER_POLL_MS, self._poll_hover)
            except Exception:
                self._hover_job = None

    def _set_hint(self, edge, 持续=0.0):
        """设置/清除"将要收纳"的提示。持续>0 时会在一段时间后自动清除（松手确认用）。"""
        if edge != self._hint:
            self._hint = edge
            # 拖动中**不重画图标**：光标在吸附边界来回时，这条边会反复变 None/Right，
            # 每变一次都重渲染（20ms）就是"拖起来掉帧"。此时窗口边缘的绿色光带
            # 已经足够说明问题，图标上的绿环留到松手后再亮。
            # 换形态的淡变还在跑时别重画：_image() 一次 20~50ms，正好卡在淡入中途，
            # 看起来就是"闪一下"。等这轮淡完，提示帧下一拍自然会补上。
            if not self._drag["on"] and self._fade_job is None:
                self._redraw()
            self._update_glow()
        if 持续:
            self._hint_until = time.time() + 持续
        if self._hint and self._hint_job is None:
            self._hint_tick()

    _HINT_FRAMES = 6          # 脉冲预渲染帧数

    def _hint_frame_list(self):
        """按当前尺寸/提示态预渲染一组脉冲帧（只做一次，之后循环播放）。

        ⚠ 以前每 60ms 调一次 _redraw() → _image()：图标是 6 倍超采样再 LANCZOS
        缩小的，一次 20~50ms —— 拖着浮标靠近边缘时，脉冲就把主线程一段段堵死，
        看起来就是"中间几帧丢了"。现在只在上传一张 90x90 位图（~0.3ms）。
        """
        键 = (self._cur_size(), self._hint, self._通色())
        缓存 = getattr(self, "_hint_cache", None)
        if 缓存 is not None and 缓存[0] == 键:
            return 缓存[1]
        帧 = []
        for i in range(self._HINT_FRAMES):
            try:
                帧.append(self._image(脉冲k=i / float(self._HINT_FRAMES), 超采样=3))
            except Exception:
                break
        self._hint_cache = (键, 帧)
        self._hint_i = 0
        return 帧

    def _通色(self):
        """当前主题色签名（用于判断预渲染帧是否过期）。"""
        try:
            历史 = list(self.get_history() or [])
            if 历史:
                t0, v, 通源, 总源 = 历史[-1][:4]
                return (t0 > time.time() - 150) and 通源 > 0
        except Exception:
            pass
        return True

    def _hint_tick(self):
        """提示期间的脉冲：60ms 一帧，让边框亮度呼吸；提示消失就停（不空转）。

        帧是预渲染好的，这里只换图（_upload），不重新绘制。
        """
        self._hint_job = None
        if not self._hint:
            return
        if self._fade_job is not None:
            # 正在换形态的淡入淡出：这一拍别上传（尺寸刚变过，重建脉冲帧要几十毫秒，
            # 会把淡变卡成一次闪），淡完再继续。
            try:
                self._hint_job = self.root.after(60, self._hint_tick)
            except Exception:
                self._hint_job = None
            return
        if self._hint_until and time.time() > self._hint_until:
            self._hint = None
            self._hint_until = 0.0
            self._redraw()
            self._update_glow()
            return
        帧 = self._hint_frame_list()
        if 帧:
            self._hint_i = (getattr(self, "_hint_i", 0) + 1) % len(帧)
            try:
                self._upload(帧[self._hint_i])
            except Exception:
                self._redraw()
        else:
            self._redraw()
        try:
            self._hint_job = self.root.after(60, self._hint_tick)
        except Exception:
            self._hint_job = None

    def _update_glow(self):
        """窗口边框的发光带 + 颗粒：提示"松手会被这条边吸进去"。"""
        if not getattr(self, "edge_fx", True):
            return
        if not self._hint:
            if self._glow is not None:
                try:
                    self._glow.hide()
                except Exception:
                    pass
            return
        if self._glow is None:
            try:
                self._glow = _EdgeGlow(self.root)
            except Exception:
                self._glow = None
                return
        ax, ay = self._abs_pos()
        cw, ch = self._cur_size()
        中心 = (ay + ch // 2) if self._hint in ("left", "right") else (ax + cw // 2)
        try:
            self._glow.show(self._hint,
                            {"x": self._geom.get("x", 0), "y": self._geom.get("y", 0),
                             "w": self._geom.get("w", 0), "h": self._geom.get("h", 0)},
                            中心)
        except Exception:
            pass

    def _collapse_now(self):
        self._collapse_job = None
        if self._hover:                 # 期间又回来了
            return
        # 最后一道闸：收之前再问一次真实光标。_hover 可能被一串假 <Leave>/<Enter>
        # 弄得来回翻，只有光标**确实**不在窗口上才允许收缩（见 _cursor_inside）。
        if self._cursor_inside():
            self._hover = True
            return
        if self._docked and self.dock_enabled and self._peek:
            self._peek = False
            self._snap(self._docked, apply=False)   # 同上
            self._redraw_faded()

    def _menu(self):
        self.menu = tk.Menu(self._target(), tearoff=0)
        self.menu.add_command(label="打开曲线图", command=self._open_chart)
        self.menu.add_command(label="复位位置", command=self._reset_pos)
        self.menu.add_separator()
        self.menu.add_command(label="隐藏浮标", command=self.close)

    def _popup(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _open_chart(self):
        try:
            self.on_open_chart()
        except Exception:
            pass

    def _reset_pos(self):
        self._docked = None
        self._restore(-1, -1)
        self._redraw()
        if self.on_close:
            self.on_close(self, False)

    # ---------------- 绘制 ----------------
    def _app_is_foreground(self):
        """前台窗口是不是本程序的（主界面 / 设置 / 曲线图 / 浮标自身都算）。"""
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            return int(pid.value) == os.getpid()
        except Exception:
            return True

    def _root_should_show(self):
        """浮标该不该显示。

        三个条件：没最小化、没 withdraw（收托盘）、**本程序在前台**。

        第三条是重点：别的程序把主界面盖住时（焦点被抢走），浮标如果还挂在
        最上层就很突兀 —— 用户的原话是"主动隐藏能同步，被动覆盖就不同步了"。
        现在按前台窗口判定：只要前台不是本程序，浮标就跟着藏起来。
        """
        try:
            if self.root.state() != "normal":
                return False
            if not self.root.winfo_viewable():
                return False
            return self._app_is_foreground()
        except Exception:
            return True

    def _request_show(self):
        """请求显示：**延迟一小会儿**再露脸。

        主窗口"显示"有 ~200ms 的系统动画，浮标如果和 <Map> 同时出现，看起来就是
        "浮标比主界面还快"。等 150ms 再淡入，正好和窗口显现的节奏对上。
        """
        if self._shown or self._show_job is not None:
            return
        try:
            self._show_job = self.root.after(self._SHOW_DELAY, self._do_show)
        except Exception:
            self._show_job = None
            self._show()

    def _do_show(self):
        self._show_job = None
        try:
            if self._root_should_show():
                self._show()
        except Exception:
            pass

    def _cancel_show(self):
        if self._show_job is not None:
            try:
                self.root.after_cancel(self._show_job)
            except Exception:
                pass
            self._show_job = None

    def _sync_visibility(self):
        """轮询兜底：withdraw()（收托盘）不一定触发 <Unmap>，光靠事件会漏。"""
        want = self._root_should_show()
        if want and not self._shown:
            self._request_show()
        elif not want and self._shown:
            self._cancel_show()
            self._shown = False
            立即 = True
            try:
                立即 = self.root.state() == "withdrawn" or not self.root.winfo_exists()
            except Exception:
                pass
            self._hide(fade=not 立即)
        elif not want:
            self._cancel_show()

    def _on_root_destroy(self, e=None):
        """只有主窗口自己被销毁才收尾（子控件销毁也会冒泡到这里）。"""
        try:
            if e is not None and getattr(e, "widget", None) is not self.root:
                return
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass

    def _tick(self):
        if self._stop:
            return
        # ⚠ 主窗口没了就自毁：浮标是独立顶层窗口（Toplevel + 分层/色键），
        # 主窗口一旦销毁，它没了"该不该显示"的判据，会孤零零留在桌面上
        # （用户报"主界面关闭了它还在"）。这里先兜一层，别指望调度顺序。
        try:
            if not self.root.winfo_exists():
                self.close()
                return
        except Exception:
            self.close()
            return
        try:
            self._sync_visibility()
        except Exception:
            pass
        try:
            if self._shown and not self._drag["on"] and not (self._docked and not self._peek):
                self._tick_redraw()
        except Exception:
            pass
        try:
            self._job = self.root.after(1000, self._tick)
        except Exception:
            pass

    def _image(self, 脉冲k=None, 超采样=None):
        from PIL import Image, ImageDraw, ImageFont, ImageFilter

        cw, ch = self._cur_size()
        S = int(超采样 or 6)
        W, H = cw * S, ch * S

        try:
            历史 = list(self.get_history() or [])
        except Exception:
            历史 = []
        通 = True
        当前 = None
        if 历史:
            t0, v, 通源, 总源 = 历史[-1][:4]
            if (time.time() - t0) <= 150:
                通 = 通源 > 0
                当前 = int(v) if 通 else None      # 不通时显示「—」，别显示 0ms
        if 通:
            底 = _GOOD_TILE
            环 = _GOOD_HOVER if self._hover else _GOOD
            副 = _GOOD_MUTED
        else:
            底 = _BAD_TILE
            环 = _BAD_HOVER if self._hover else _BAD
            副 = _BAD_MUTED

        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pad = int(min(W, H) * 0.06) if self.shadow else 0
        box = [pad, pad, W - 1 - pad, H - 1 - pad]

        if self.shadow:
            sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(sh).ellipse(
                [box[0] + S * 1.5, box[1] + S * 2.5,
                 box[2] + S * 1.5, box[3] + S * 2.5], fill=(0, 0, 0, 70))
            img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(2.2 * S)))

        dr = ImageDraw.Draw(img)
        ring = max(2 * S, int(min(W, H) * 0.055))
        if abs(cw - ch) <= 1:
            # 正圆必须用 ellipse：`rounded_rectangle(radius=半边长)` 在 Pillow 里
            # 是四段圆弧拼出来的，边缘会有细微的棱/毛刺，看起来像手画的。
            dr.ellipse(box, fill=底 + (255,))
            dr.ellipse(box, outline=环 + (255,), width=ring)
        else:
            # 收纳成小标签（细长胶囊）才用圆角矩形
            radius = min(W, H) * 0.5
            dr.rounded_rectangle(box, radius=radius, fill=底 + (255,))
            dr.rounded_rectangle(box, radius=radius, outline=环 + (255,), width=ring)

        # ---- 收纳提示：脉冲边框 + 朝向那条边的短条 ----
        提示 = getattr(self, "_hint", None)
        if 提示:
            if 脉冲k is None:
                k = 0.5 + 0.5 * math.sin(time.time() * 9.0)  # 0..1 呼吸
            else:
                k = float(脉冲k)                              # 预渲染帧用固定相位
            alpha = int(80 + 130 * k)
            提示色 = (110, 220, 130)                         # 亮绿：和普通状态的深绿环区分得开
            外 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d2 = ImageDraw.Draw(外)
            pad2 = max(0, pad - int(2.5 * S))
            box2 = [pad2, pad2, W - 1 - pad2, H - 1 - pad2]
            d2.ellipse(box2, outline=提示色 + (alpha,),
                       width=max(2 * S, int(min(W, H) * 0.035)))
            img = Image.alpha_composite(img, 外)
            dr = ImageDraw.Draw(img)      # ⚠ 合成换了对象，draw 目标要重新绑，否则后面的字画在旧图上
            条宽 = max(3 * S, int(min(W, H) * 0.06))
            bx0, by0, bx1, by1 = box
            if 提示 == "right":
                条 = [bx1 - 条宽, by0 + (by1 - by0) * 0.32, bx1, by0 + (by1 - by0) * 0.68]
            elif 提示 == "left":
                条 = [bx0, by0 + (by1 - by0) * 0.32, bx0 + 条宽, by0 + (by1 - by0) * 0.68]
            elif 提示 == "top":
                条 = [bx0 + (bx1 - bx0) * 0.32, by0, bx0 + (bx1 - bx0) * 0.68, by0 + 条宽]
            else:
                条 = [bx0 + (bx1 - bx0) * 0.32, by1 - 条宽, bx0 + (bx1 - bx0) * 0.68, by1]
            try:
                ImageDraw.Draw(img).rounded_rectangle(
                    条, radius=条宽 / 2.0, fill=(170, 255, 190, min(255, alpha + 60)))
            except Exception:
                pass

        # 收纳成小标签（22px 宽）时放不下数字，只留色条
        if cw >= ch * 0.7:
            def 字体(大小, 粗=False):
                for path in ((r"C:\Windows\Fonts\msyhbd.ttc" if 粗
                              else r"C:\Windows\Fonts\msyh.ttc"),
                             r"C:\Windows\Fonts\simhei.ttf"):
                    try:
                        return ImageFont.truetype(path, 大小)
                    except Exception:
                        continue
                return ImageFont.load_default()

            数字 = "—" if 当前 is None else str(当前)
            字号 = int(H * 0.32) if len(数字) <= 3 else int(H * 0.25)
            f1 = 字体(字号, 粗=True)
            f2 = 字体(max(8 * S, int(H * 0.16)))
            try:
                bb = dr.textbbox((0, 0), 数字, font=f1)
                tw, ty = bb[2] - bb[0], H * 0.30 - bb[1]
            except Exception:
                tw, ty = len(数字) * 字号 // 2, H * 0.22
            dr.text(((W - tw) / 2, ty), 数字, font=f1, fill=_FG)
            try:
                w2 = dr.textlength("ms", font=f2)
            except Exception:
                w2 = 12 * S
            dr.text(((W - w2) / 2, H * 0.63), "ms", font=f2, fill=副)

        return img.resize((cw, ch), Image.LANCZOS)

    def _tick_redraw(self):
        """每秒的刷新：只有内容真的变了才重画。

        图标是 6 倍超采样 + LANCZOS，一次 20~30ms；每秒无脑重画一次，正好卡在
        拖动/滚动时。数字没变（同一个延迟值）就什么都不做。
        """
        键 = (self._cur_size(), self._hint, self._通色(), self._hover)
        try:
            值 = self.get_history()[-1][1] if self.get_history() else None
        except Exception:
            值 = None
        键 = 键 + (值,)
        if getattr(self, "_tick_key", None) == 键:
            return
        self._tick_key = 键
        self._redraw()

    def _redraw(self):
        if self._morph is not None:        # 过渡被别的路径打断（主题/数据刷新等）
            self._morph = None
            if self._morph_job is not None:
                try:
                    self.root.after_cancel(self._morph_job)
                except Exception:
                    pass
                self._morph_job = None
            if self._fade_hr:
                self._fade_hr = False
                try:
                    anim.release()
                except Exception:
                    pass
        img = self._image()
        if self._layered:
            self._upload(img)
            return
        from PIL import ImageTk
        cw, ch = self._cur_size()
        self._photo = ImageTk.PhotoImage(img)
        try:
            self.canvas.configure(width=cw, height=ch)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        except Exception:
            pass
        self._apply()

    # ---------------- 生命周期 ----------------
    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def close(self):
        self._stop = True
        for j in (getattr(self, "_job", None), getattr(self, "_follow_job", None),
                  getattr(self, "_show_job", None),
                  getattr(self, "_fade_job", None),
                  getattr(self, "_collapse_job", None),
                  getattr(self, "_hint_job", None)):
            try:
                if j:
                    self.root.after_cancel(j)
            except Exception:
                pass
        if getattr(self, "_glow", None) is not None:
            try:
                self._glow.destroy()
            except Exception:
                pass
            self._glow = None
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close(self, True)

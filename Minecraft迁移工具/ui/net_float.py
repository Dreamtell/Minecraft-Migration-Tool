# ui/net_float.py
"""桌面浮标：一个圆形小图标，点一下打开网络延迟曲线窗口。

为什么用「分层窗口」而不是 Tk 的 `-transparentcolor`：
  · 色键（`-transparentcolor`）只能整像素透明，抗锯齿的半透明边缘会
    和键色混出一圈彩边（品红边），所以那一版只能画硬边，圆看着有锯齿；
  · `UpdateLayeredWindow` 支持**逐像素 alpha**：圆边可以做抗锯齿，
    外层背景真正透明，不会有彩边。

实现方式：仍然用 Tk 建窗口（这样鼠标事件、右键菜单、DPI 都是现成的），
但**不用 Canvas 绘制**，而是把 Pillow 画好的 RGBA 图通过 ULW 贴到窗口上。
窗口设成 WS_EX_LAYERED 之后，Tk 自己的绘制不会显示，正好由 ULW 接管。

交互：
  · 左键点击 → 打开完整曲线图；左键拖动 → 换位置（松开才存）；
  · 右键 → 菜单（打开曲线图 / 复位位置 / 隐藏浮标）；
  · 圆环颜色表示连通状态（绿=通、红=不通），中央数字是当前延迟。
"""

import ctypes
import time
import tkinter as tk
from ctypes import wintypes

from utils.dpi import px

_TILE = (38, 50, 56)          # 圆的底色 #263238
_FG = "#ffffff"
_MUTED = "#b0bec5"
_GOOD = (46, 125, 50)
_BAD = (198, 40, 40)
_GOOD_HOVER = (76, 175, 80)
_BAD_HOVER = (239, 83, 80)
_KEY = "#ff00ff"              # 仅在没有分层窗口时兜底用

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TOOLWINDOW = 0x00000080
_ULW_ALPHA = 0x00000002
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte),
                ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class NetFloat:
    """`get_history()` 返回 [(时间戳, 毫秒, 连通源数, 源总数), ...]。"""

    D = 56                # 设计直径（会乘 UI_SCALE）

    def __init__(self, parent, get_history, on_open_chart, on_close=None,
                 x=None, y=None):
        self.parent = parent
        self.get_history = get_history
        self.on_open_chart = on_open_chart
        self.on_close = on_close

        self.d = px(self.D)
        self._pos = [0, 0]
        self._drag = {"dx": 0, "dy": 0, "on": False, "moved": False}
        self._job = None
        self._photo = None
        self._stop = False
        self._hover = False
        self._layered = False
        self.hwnd = 0
        self._dib = None

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#263238")
        self._place(x, y)
        self.win.update_idletasks()

        self._setup_layered()
        if not self._layered:                 # 兜底：退回色键方案
            try:
                self.win.attributes("-transparentcolor", _KEY)
                self.win.configure(bg=_KEY)
            except Exception:
                pass
            self.canvas = tk.Canvas(self.win, width=self.d, height=self.d,
                                    bg=_KEY if not self._layered else "#263238",
                                    highlightthickness=0, bd=0, cursor="hand2")
            self.canvas.pack()

        self._bind()
        self._menu()
        self._redraw()
        self._tick()

    # ---------------- 分层窗口 ----------------
    def _setup_layered(self):
        try:
            self.hwnd = int(self.win.wm_frame(), 16)
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(self.hwnd, _GWL_EXSTYLE)
            u.SetWindowLongW(self.hwnd, _GWL_EXSTYLE,
                             ex | _WS_EX_LAYERED | _WS_EX_TOOLWINDOW)
            self._layered = True
        except Exception:
            self._layered = False

    def _upload(self, img):
        """把 RGBA 图上传成分层窗口的位图（只在画面变化时调用）。

        位图缓存下来：拖动时只需要 UpdateLayeredWindow 换个位置，
        不用每帧重建 DIB，也不用 SetWindowPos 反复重排 z 序 ——
        那两个正是之前拖动卡顿的原因。
        """
        from PIL import Image
        img = img.convert("RGBA")
        w, h = img.size

        # 预乘 alpha（ULW 要求 AC_SRC_ALPHA + premultiplied BGRA）
        src = img.tobytes("raw", "BGRA")
        buf = bytearray(src)
        for i in range(0, len(buf), 4):
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
        bmi.bmiHeader.biHeight = -h        # 负数 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        hbmp = g.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0,
                                  ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, bytes(buf), len(buf))
        g.SelectObject(hdc_mem, hbmp)
        u.ReleaseDC(0, hdc_screen)

        # 换掉旧位图
        self._free_dib()
        self._dib = (hbmp, hdc_mem, w, h)
        self._apply_pos()

    def _free_dib(self):
        dib = getattr(self, "_dib", None)
        hbmp, hdc_mem = (dib[0], dib[1]) if dib else (None, None)
        if hbmp:
            try:
                ctypes.windll.gdi32.DeleteObject(hbmp)
            except Exception:
                pass
        if hdc_mem:
            try:
                ctypes.windll.gdi32.DeleteDC(hdc_mem)
            except Exception:
                pass
        self._dib = None

    def _apply_pos(self):
        """用缓存的位图 + 新坐标调用 ULW（不重建位图，也不改 z 序）。"""
        if not (self._layered and self.hwnd and self._dib):
            try:
                self.win.geometry("+%d+%d" % (self._pos[0], self._pos[1]))
            except Exception:
                pass
            return
        hbmp, hdc_mem, w, h = self._dib
        u = ctypes.windll.user32
        hdc_screen = u.GetDC(0)
        pt_dst = wintypes.POINT(self._pos[0], self._pos[1])
        pt_src = wintypes.POINT(0, 0)
        size = wintypes.SIZE(w, h)
        blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
        u.UpdateLayeredWindow(self.hwnd, hdc_screen, ctypes.byref(pt_dst),
                              ctypes.byref(size), hdc_mem, ctypes.byref(pt_src),
                              0, ctypes.byref(blend), _ULW_ALPHA)
        u.ReleaseDC(0, hdc_screen)

    # ---------------- 位置 ----------------
    def _place(self, x=None, y=None):
        if x is None or y is None:
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            x = sw - self.d - px(28)
            y = sh - self.d - px(96)
        self._pos = [int(x), int(y)]
        self.win.geometry("%dx%d+%d+%d" % (self.d, self.d, self._pos[0], self._pos[1]))

    def _move(self, x, y):
        self._pos = [int(x), int(y)]
        self._apply_pos()

    def position(self):
        return tuple(self._pos)

    # ---------------- 交互 ----------------
    def _target(self):
        return self.win if self._layered else self.canvas

    def _bind(self):
        w = self._target()
        w.bind("<Button-1>", self._on_press)
        w.bind("<B1-Motion>", self._on_drag)
        w.bind("<ButtonRelease-1>", self._on_release)
        w.bind("<Button-3>", self._popup)
        w.bind("<Enter>", self._on_enter)
        w.bind("<Leave>", self._on_leave)

    def _on_press(self, e):
        self._drag.update(dx=e.x_root - self._pos[0],
                          dy=e.y_root - self._pos[1],
                          on=True, moved=False)

    def _on_drag(self, e):
        if not self._drag["on"]:
            return
        nx, ny = e.x_root - self._drag["dx"], e.y_root - self._drag["dy"]
        if abs(nx - self._pos[0]) > 2 or abs(ny - self._pos[1]) > 2:
            self._drag["moved"] = True
        self._move(nx, ny)

    def _on_release(self, _e):
        if not self._drag["on"]:
            return
        self._drag["on"] = False
        if self._drag["moved"]:
            if self.on_close:
                self.on_close(self, False)
        else:
            self._open_chart()

    def _on_enter(self, _e):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._redraw()

    def _menu(self):
        self.menu = tk.Menu(self.win, tearoff=0)
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
        self._place(None, None)
        self._redraw()
        if self.on_close:
            self.on_close(self, False)

    # ---------------- 绘制 ----------------
    def _tick(self):
        if self._stop:
            return
        try:
            if not self._drag["on"]:      # 拖动中不重建图像，避免打断手感
                self._redraw()
        except Exception:
            pass
        try:
            self._job = self.win.after(1000, self._tick)
        except Exception:
            pass

    def _image(self):
        from PIL import Image, ImageDraw, ImageFont

        d = self.d
        S = 4                       # 超采样，缩回来就是抗锯齿
        D = d * S
        try:
            历史 = list(self.get_history() or [])
        except Exception:
            历史 = []

        通 = True
        当前 = None
        if 历史:
            t0, v, 通源, 总源 = 历史[-1][:4]
            if (time.time() - t0) <= 120:
                当前 = int(v)
                通 = 通源 > 0

        环 = (_GOOD_HOVER if self._hover else _GOOD) if 通 else \
             (_BAD_HOVER if self._hover else _BAD)

        img = Image.new("RGBA", (D, D), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        pad = int(D * 0.02)
        dr.ellipse([pad, pad, D - 1 - pad, D - 1 - pad], fill=_TILE + (255,))
        ring = max(2 * S, int(round(D * 0.055)))
        dr.ellipse([pad, pad, D - 1 - pad, D - 1 - pad], outline=环 + (255,), width=ring)

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
        字号 = int(D * 0.32) if len(数字) <= 3 else int(D * 0.25)
        f1 = 字体(字号, 粗=True)
        f2 = 字体(max(8 * S, int(D * 0.16)))
        try:
            bb = dr.textbbox((0, 0), 数字, font=f1)
            tw = bb[2] - bb[0]
            base_y = D * 0.30 - bb[1]
        except Exception:
            tw, base_y = len(数字) * 字号 // 2, D * 0.22
        dr.text(((D - tw) / 2, base_y), 数字, font=f1, fill=_FG)
        try:
            w2 = dr.textlength("ms", font=f2)
        except Exception:
            w2 = 12 * S
        dr.text(((D - w2) / 2, D * 0.63), "ms", font=f2, fill=_MUTED)

        return img.resize((d, d), Image.LANCZOS)

    def _redraw(self):
        img = self._image()
        if self._layered:
            self._upload(img)
        else:
            from PIL import ImageTk
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

    # ---------------- 生命周期 ----------------
    def close(self):
        self._stop = True
        try:
            self._free_dib()
        except Exception:
            pass
        try:
            if self._job:
                self.win.after_cancel(self._job)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close(self, True)

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

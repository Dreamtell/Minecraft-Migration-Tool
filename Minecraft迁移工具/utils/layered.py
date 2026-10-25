# utils/layered.py
"""把一个无边框 Tk 窗口变成"逐像素 alpha"的分层窗口（UpdateLayeredWindow）。

## 为什么需要它

Tk 控件没有真透明。常见的两个替代都不彻底：

  · 四角填父容器底色（假圆角）——浮层盖在别的内容上时四角露出一块底色方块；
  · attributes("-transparentcolor", 键色)——只有**1 位**透明度：抗锯齿的半透明边缘
    不会变透明，而是和键色混合成脏边；强行把 alpha 二值化，圆角又变成像素台阶。

UpdateLayeredWindow 支持**完整的 8 位 alpha**：既能真透明，又能保留抗锯齿。
ui/net_float.py 的浮标一直是这么做的，这里抽出最小实现给吐司复用。

## 用法

    surf = LayeredSurface(tk.Toplevel(...))
    surf.upload(pil_rgba_image, screen_x, screen_y)   # 传内容（顺带定尺寸和位置）
    surf.move(screen_x, screen_y)                     # 只挪位置（比 upload 快 18 倍）
    surf.destroy()

⚠ 上传的位图必须**预乘 alpha**，否则边缘会发白发灰（ULW 用的是 AC_SRC_ALPHA）。
⚠ 窗口必须是 overrideredirect 的：分层窗口的可见内容完全来自这张位图，
   Tk 自己在里面画什么都不作数。
"""

import ctypes
from ctypes import wintypes

_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_ULW_ALPHA = 0x02
_WS_EX_LAYERED = 0x00080000
_GWL_EXSTYLE = -20


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte),
                ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte),
                ("AlphaFormat", ctypes.c_byte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


class LayeredSurface:
    """逐像素 alpha 的分层窗口表面。没有 DWM/分层窗口可用时就退化（调用方要能容忍）。"""

    def __init__(self, win):
        self.win = win
        self._dib = None
        self.ok = False
        self._hwnd = None
        self._refresh()

    def _refresh(self):
        """取当前 HWND 并确保带 WS_EX_LAYERED。

        ⚠ 必须每次上传前重取：Tk 的 Toplevel 在 withdraw→deiconify 之间可能**重建窗口**，
        缓存下来的旧 HWND 会让 UpdateLayeredWindow 静默失败（窗口在那儿，但一片空白 ——
        实测就是这个现象）。
        """
        try:
            raw = self.win.winfo_id()
            parent = ctypes.windll.user32.GetParent(raw)
            hwnd = parent if parent else raw
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            if not (ex & _WS_EX_LAYERED):
                u.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex | _WS_EX_LAYERED)
            self._hwnd = hwnd
            self.ok = True
            return True
        except Exception:
            self.ok = False
            return False

    # ---------------------------------------------------------------- 上传
    def upload(self, img, x=None, y=None):
        """把 RGBA 图传上去（顺带定尺寸/位置）。返回是否成功。"""
        if not self._refresh():
            return False
        try:
            from PIL import Image
        except Exception:
            return False
        try:
            img = img.convert("RGBA")
            w, h = img.size
            if w <= 0 or h <= 0:
                return False
            buf = bytearray(img.tobytes("raw", "BGRA"))
            # 预乘 alpha：ULW 用的是 AC_SRC_ALPHA，不预乘边缘会发白发灰
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
            bmi.bmiHeader.biHeight = -h              # 负 = 自上而下
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
            if x is None or y is None:
                x, y = self.pos()
            self._blit(x, y)
            if old:
                try:
                    g.DeleteObject(old[0])
                    g.DeleteDC(old[1])
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def _blit(self, x, y):
        hbmp, hdc_mem, w, h = self._dib
        u = ctypes.windll.user32
        hdc_screen = u.GetDC(0)
        pt_dst = wintypes.POINT(int(x), int(y))
        pt_src = wintypes.POINT(0, 0)
        size = wintypes.SIZE(w, h)
        blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
        u.UpdateLayeredWindow(self._hwnd, hdc_screen, ctypes.byref(pt_dst),
                              ctypes.byref(size), hdc_mem, ctypes.byref(pt_src),
                              0, ctypes.byref(blend), _ULW_ALPHA)
        u.ReleaseDC(0, hdc_screen)

    # ---------------------------------------------------------------- 移动
    def move(self, x, y):
        """只挪位置：SetWindowPos（不重传位图，比 upload 快 18 倍）。"""
        if not self.ok or not self._hwnd:
            return False
        try:
            ctypes.windll.user32.SetWindowPos(int(self._hwnd), 0, int(x), int(y),
                                              0, 0, 0x0001 | 0x0004 | 0x0010)
            return True                      # NOSIZE | NOZORDER | NOACTIVATE
        except Exception:
            return False

    def pos(self):
        """当前窗口左上角的屏幕坐标。"""
        try:
            return self.win.winfo_rootx(), self.win.winfo_rooty()
        except Exception:
            return 0, 0

    def size(self):
        if not self._dib:
            return 0, 0
        return self._dib[2], self._dib[3]

    def destroy(self):
        try:
            g = ctypes.windll.gdi32
            if self._dib:
                g.DeleteObject(self._dib[0])
                g.DeleteDC(self._dib[1])
        except Exception:
            pass
        self._dib = None
        self.ok = False

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
import time
import tkinter as tk
from ctypes import wintypes

from utils.dpi import px

_TILE = (38, 50, 56, 255)
_FG = "#ffffff"
_MUTED = "#b0bec5"
_GOOD = (46, 125, 50)
_BAD = (198, 40, 40)
_GOOD_HOVER = (76, 175, 80)
_BAD_HOVER = (239, 83, 80)

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


class NetFloat:
    """`get_history()` 返回 [(时间戳, 毫秒, 连通源数, 源总数), ...]。"""

    D = 56
    MIN_D = 40
    MAX_D = 96
    TAB_W = 22          # 收纳成小标签时的宽度（设计像素）
    SNAP = 14           # 离边多近算贴边

    def __init__(self, root, get_history, on_open_chart, on_close=None,
                 size=None, shadow=True, dock=True, bg="#f0f0f0",
                 x=None, y=None):
        self.root = root
        self.get_history = get_history
        self.on_open_chart = on_open_chart
        self.on_close = on_close
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
        self._drag = {"dx": 0, "dy": 0, "on": False, "moved": False}
        self._hover = False
        self._peek = False
        self._docked = None
        self._job = None
        self._photo = None
        self._stop = False
        self._dib = None
        self._shown = True
        self._layered = False
        self._hwnd = 0

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        # 必须置顶：无边框弹出窗口如果不置顶，就会被自己的主窗口盖住（实测把主窗口
        # 置前之后浮标就消失了）。Tk/Win32 没有「只压在主窗口之上、又低于其它程序」
        # 这种中间层级可用。
        try:
            self.win.attributes("-topmost", True)
        except Exception:
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

        self._bind()
        self._menu()
        self._restore(x, y)
        self._redraw()
        self._tick()          # ⚠ 必须启动刷新循环，否则数字会停在初始化那一刻

    # ---------------- 坐标 ----------------
    def _parent_size(self):
        try:
            return (max(self.d, self.root.winfo_width()),
                    max(self.d, self.root.winfo_height()))
        except Exception:
            return 1920, 1080

    def _cur_size(self):
        if self._docked and self.dock_enabled and not self._peek:
            return (self.tab_w, self.d)
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
        try:
            return (self.root.winfo_rootx() + self._rel[0],
                    self.root.winfo_rooty() + self._rel[1])
        except Exception:
            return self._rel[0], self._rel[1]

    def _apply(self):
        ax, ay = self._abs_pos()
        if self._layered and self._dib:
            self._blit(self._dib, ax, ay)
            return
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
        blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
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
        x, y = self._rel
        近 = px(self.SNAP)
        左 = x <= 近
        右 = (w - self.d - x) <= 近
        上 = y <= 近
        下 = (h - self.d - y) <= 近
        if 左 and not 右:
            return "left"
        if 右 and not 左:
            return "right"
        if 上 and not 下:
            return "top"
        if 下 and not 上:
            return "bottom"
        return None

    def _snap(self, edge):
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
        self._apply()

    # ---------------- 交互 ----------------
    def _target(self):
        return self.canvas if self.canvas is not None else self.win

    def _bind(self):
        c = self._target()
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Button-3>", self._popup)
        c.bind("<Enter>", self._on_enter)
        c.bind("<Leave>", self._on_leave)
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
        self._show()

    def _on_root_unmap(self, ev):
        if getattr(ev, "widget", None) is not self.root:
            return
        self._hide()

    def _on_root_focus_in(self, ev):
        """主窗口回到前台：浮标重新置顶并重画（隐藏过之后 ULW 内容要重传）。"""
        if getattr(ev, "widget", None) is not self.root:
            return
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        self._show()

    def _on_root_focus_out(self, ev):
        """主窗口失去焦点：取消置顶，免得它压在其他程序上面。"""
        if getattr(ev, "widget", None) is not self.root:
            return
        try:
            self.win.attributes("-topmost", False)
        except Exception:
            pass

    def _show(self):
        self._shown = True
        try:
            if self.win.state() == "withdrawn":
                self.win.deiconify()
        except Exception:
            pass
        self._redraw()

    def _hide(self):
        self._shown = False
        try:
            self.win.withdraw()
        except Exception:
            pass

    def _on_root_configure(self, ev):
        try:
            if ev.widget is not self.root:
                return
        except Exception:
            pass
        if self._docked:
            self._snap(self._docked)
        else:
            self._apply()

    def _on_press(self, e):
        self._drag.update(dx=e.x, dy=e.y, on=True, moved=False)

    def _on_drag(self, e):
        if not self._drag["on"]:
            return
        nx = e.x_root - self.root.winfo_rootx() - self._drag["dx"]
        ny = e.y_root - self.root.winfo_rooty() - self._drag["dy"]
        if abs(nx - self._rel[0]) > 1 or abs(ny - self._rel[1]) > 1:
            self._drag["moved"] = True
        if self._docked:                     # 拖出来就取消收纳
            self._docked = None
            self._peek = False
            self._redraw()
        self._rel = [int(nx), int(ny)]
        self._apply()

    def _on_release(self, _e):
        if not self._drag["on"]:
            return
        self._drag["on"] = False
        if self._drag["moved"]:
            edge = self._detect_edge()
            if edge:
                self._docked = edge
                self._snap(edge)
                self._redraw()               # 换成小标签尺寸
            else:
                self._docked = None
                self._rel = list(self._clamp(*self._rel))
                self._apply()
            if self.on_close:
                self.on_close(self, False)
        else:
            self._open_chart()

    def _on_enter(self, _e):
        self._hover = True
        if self._docked and self.dock_enabled and not self._peek:
            self._peek = True
            self._snap(self._docked)         # 展开成圆
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        if self._docked and self.dock_enabled and self._peek:
            self._peek = False
            self._snap(self._docked)         # 缩回小标签
        self._redraw()

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
    def _root_should_show(self):
        """主窗口是不是「可见」：既没最小化、也没 withdraw（收托盘）。"""
        try:
            if self.root.state() != "normal":
                return False
            return bool(self.root.winfo_viewable())
        except Exception:
            return True

    def _sync_visibility(self):
        """轮询兜底：withdraw()（收托盘）不一定触发 <Unmap>，光靠事件会漏。"""
        want = self._root_should_show()
        if want and not self._shown:
            self._shown = True
            self._show()
        elif not want and self._shown:
            self._shown = False
            self._hide()

    def _tick(self):
        if self._stop:
            return
        try:
            self._sync_visibility()
        except Exception:
            pass
        try:
            if self._shown and not self._drag["on"] and not (self._docked and not self._peek):
                self._redraw()
        except Exception:
            pass
        try:
            self._job = self.root.after(1000, self._tick)
        except Exception:
            pass

    def _image(self):
        from PIL import Image, ImageDraw, ImageFont, ImageFilter

        cw, ch = self._cur_size()
        S = 6
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
                当前 = int(v)
                通 = 通源 > 0
        环 = (_GOOD_HOVER if self._hover else _GOOD) if 通 else \
             (_BAD_HOVER if self._hover else _BAD)

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
            dr.ellipse(box, fill=_TILE)
            dr.ellipse(box, outline=环 + (255,), width=ring)
        else:
            # 收纳成小标签（细长胶囊）才用圆角矩形
            radius = min(W, H) * 0.5
            dr.rounded_rectangle(box, radius=radius, fill=_TILE)
            dr.rounded_rectangle(box, radius=radius, outline=环 + (255,), width=ring)

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
            dr.text(((W - w2) / 2, H * 0.63), "ms", font=f2, fill=_MUTED)

        return img.resize((cw, ch), Image.LANCZOS)

    def _redraw(self):
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
        try:
            if self._job:
                self.root.after_cancel(self._job)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close(self, True)

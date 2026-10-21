# ui/net_float.py
"""主窗口内的悬浮图标（不是桌面挂件）。

为什么是窗口内的控件，而不是独立的置顶窗口：
  · 需求是「和主界面同一层、只能在本窗口范围内移动」——用 Toplevel + topmost
    就必然浮在别的程序上面，也没法限制在主窗口内；
  · 做成 root 的 Canvas 子控件之后，父窗口一裁剪它就出不去，主窗口最小化/收起
    它也自动跟着走，层叠关系天然正确；
  · 抗锯齿也不需要分层窗口了：Tk 8.6 的 canvas 会把带 alpha 的 PhotoImage 和
    画布背景正确混合（实测半透明圆叠在 #2e2e2e 上得到 50% 混色），所以直接在
    Pillow 里画好、贴上去就是平滑的。

交互：
  · 左键点击 → 打开延迟曲线窗口；拖动 → 在窗口内移动（自动夹在边界内）；
  · 贴边松手 → 吸附到边；开启「贴边收纳」时还会滑出去一半，鼠标移上去再滑回来；
  · 右键 → 菜单（打开曲线图 / 复位位置 / 隐藏浮标）。
"""

import time
import tkinter as tk

from utils.dpi import px

_TILE = (38, 50, 56, 255)      # 圆的底色 #263238
_FG = "#ffffff"
_MUTED = "#b0bec5"
_GOOD = (46, 125, 50)
_BAD = (198, 40, 40)
_GOOD_HOVER = (76, 175, 80)
_BAD_HOVER = (239, 83, 80)
_SNAP = 12                     # 离边缘多近就吸附（设计像素）


class NetFloat:
    """`get_history()` 返回 [(时间戳, 毫秒, 连通源数, 源总数), ...]。"""

    D = 56
    MIN_D = 40
    MAX_D = 96

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

        self._drag = {"dx": 0, "dy": 0, "on": False, "moved": False}
        self._job = None
        self._photo = None
        self._hover = False
        self._peek = False          # 收纳状态下鼠标悬停时是否临时展开
        self._docked = None         # None / "left" / "right" / "top" / "bottom"
        self._place_xy = [0, 0]

        self.canvas = tk.Canvas(root, width=self.d, height=self.d, bg=self.bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        self._bind()
        self._menu()
        self._restore(x, y)
        self._redraw()
        self._tick()

    # ---------------- 位置 ----------------
    def _bounds(self):
        try:
            w = max(self.d, self.root.winfo_width())
            h = max(self.d, self.root.winfo_height())
        except Exception:
            return 0, 0, self.d, self.d
        return 0, 0, w, h

    def _clamp(self, x, y):
        _, _, w, h = self._bounds()
        return max(0, min(int(x), w - self.d)), max(0, min(int(y), h - self.d))

    def _restore(self, x, y):
        try:
            x, y = int(x), int(y)
        except Exception:
            x = y = -1
        if x < 0 or y < 0:
            _, _, w, h = self._bounds()
            x, y = w - self.d - px(18), h - self.d - px(64)     # 默认右下角偏上
        self.canvas.place(x=0, y=0, width=self.d, height=self.d)
        self._place(*self._clamp(x, y))

    def _place(self, x, y):
        self._place_xy = [int(x), int(y)]
        try:
            self.canvas.place(x=int(x), y=int(y), width=self.d, height=self.d)
        except Exception:
            pass

    def position(self):
        return tuple(self._place_xy)

    def _apply_dock(self, edge):
        """贴边：吸附到边上；开着收纳时再滑出去一半（只露一条边）。"""
        self._docked = edge
        _, _, w, h = self._bounds()
        x, y = self._place_xy
        if edge == "left":
            x = 0
            if self.dock_enabled and not self._peek:
                x = -int(self.d * 0.55)
        elif edge == "right":
            x = w - self.d
            if self.dock_enabled and not self._peek:
                x = w - self.d + int(self.d * 0.55)
        elif edge == "top":
            y = 0
            if self.dock_enabled and not self._peek:
                y = -int(self.d * 0.55)
        elif edge == "bottom":
            y = h - self.d
            if self.dock_enabled and not self._peek:
                y = h - self.d + int(self.d * 0.55)
        self._place(x, y)

    def _detect_edge(self):
        """松手时判断有没有贴到边。返回 None / 边名。"""
        _, _, w, h = self._bounds()
        x, y = self._place_xy
        近 = px(_SNAP)
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

    def _undock(self):
        self._peek = True
        if self._docked:
            self._apply_dock(self._docked)

    def _redock(self):
        self._peek = False
        if self._docked:
            self._apply_dock(self._docked)

    # ---------------- 交互 ----------------
    def _bind(self):
        c = self.canvas
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Button-3>", self._popup)
        c.bind("<Enter>", self._on_enter)
        c.bind("<Leave>", self._on_leave)
        try:
            self.root.bind("<Configure>", self._on_root_configure, add="+")
        except Exception:
            pass

    def _on_root_configure(self, ev):
        try:
            if ev.widget is not self.root:
                return
        except Exception:
            pass
        if self._docked:
            self._apply_dock(self._docked)
        else:
            self._place(*self._clamp(*self._place_xy))

    def _on_press(self, e):
        self._drag.update(dx=e.x_root - self.canvas.winfo_rootx(),
                          dy=e.y_root - self.canvas.winfo_rooty(),
                          on=True, moved=False)

    def _on_drag(self, e):
        if not self._drag["on"]:
            return
        nx = e.x_root - self.canvas.winfo_rootx() - self._drag["dx"]
        ny = e.y_root - self.canvas.winfo_rooty() - self._drag["dy"]
        if abs(nx - self._place_xy[0]) > 1 or abs(ny - self._place_xy[1]) > 1:
            self._drag["moved"] = True
        self._docked = None
        self._place(nx, ny)

    def _on_release(self, _e):
        if not self._drag["on"]:
            return
        self._drag["on"] = False
        if self._drag["moved"]:
            edge = self._detect_edge()
            if edge:
                self._apply_dock(edge)
            else:
                self._docked = None
                self._place(*self._clamp(*self._place_xy))
            if self.on_close:
                self.on_close(self, False)     # 借回调存位置
        else:
            self._open_chart()

    def _on_enter(self, _e):
        self._hover = True
        if self._docked and self.dock_enabled:
            self._undock()
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        if self._docked and self.dock_enabled:
            self._redock()
        self._redraw()

    def _menu(self):
        self.menu = tk.Menu(self.canvas, tearoff=0)
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
    def _tick(self):
        if not self.alive():
            return
        try:
            if not self._drag["on"]:
                self._redraw()
        except Exception:
            pass
        try:
            self._job = self.root.after(1000, self._tick)
        except Exception:
            pass

    def _image(self):
        from PIL import Image, ImageDraw, ImageFont, ImageFilter

        d = self.d
        S = 4
        D = d * S
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

        img = Image.new("RGBA", (D, D), (0, 0, 0, 0))
        pad = int(D * 0.06) if self.shadow else 0
        if self.shadow:
            sh = Image.new("RGBA", (D, D), (0, 0, 0, 0))
            ImageDraw.Draw(sh).ellipse(
                [pad + S * 1.5, pad + S * 2.5, D - 1 - pad + S * 1.5, D - 1 - pad + S * 2.5],
                fill=(0, 0, 0, 70))
            img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(2.2 * S)))

        dr = ImageDraw.Draw(img)
        dr.ellipse([pad, pad, D - 1 - pad, D - 1 - pad], fill=_TILE)
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
            tw, ty = bb[2] - bb[0], D * 0.30 - bb[1]
        except Exception:
            tw, ty = len(数字) * 字号 // 2, D * 0.22
        dr.text(((D - tw) / 2, ty), 数字, font=f1, fill=_FG)
        try:
            w2 = dr.textlength("ms", font=f2)
        except Exception:
            w2 = 12 * S
        dr.text(((D - w2) / 2, D * 0.63), "ms", font=f2, fill=_MUTED)

        return img.resize((d, d), Image.LANCZOS)

    def _redraw(self):
        from PIL import ImageTk
        img = self._image()
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

    # ---------------- 生命周期 ----------------
    def alive(self):
        try:
            return bool(self.canvas.winfo_exists())
        except Exception:
            return False

    def close(self):
        try:
            if self._job:
                self.root.after_cancel(self._job)
        except Exception:
            pass
        try:
            self.canvas.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close(self, True)

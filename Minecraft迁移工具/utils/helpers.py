# utils/helpers.py
import tkinter as tk
import tkinter.font as tkfont
import math
import time
import weakref

# 活着的平滑滚动器（弱引用，不阻止回收）。用来统一管理/诊断，
# 也可以将来做"一键关掉平滑滚动"的开关。
_LIVE_SCROLLERS = weakref.WeakSet()


def live_scrollers():
    return [s for s in _LIVE_SCROLLERS]


def warm_up_emoji_font():
    """提前把「第一次画 emoji」的两笔开销做掉，别让它卡在构建界面中途。

    1) 字体回退枚举：Tk 第一次遇到「微软雅黑里没有的字符」会去枚举系统字体找替代，
       实测单个字符约 267 ms。
    2) 带 emoji 的文字排版：量字体并不能预热这一条路——实测第一个带 ⚠️ 的 Label
       单独还要 30~50 ms，第二个才降到 3 ms。

    这两笔都放在启动闪屏刚画出来的时候做掉（那会儿用户刚看到卡片，卡一下看不出来）；
    留到构建中途就会看到立方体突然停住再接着转。
    """
    try:
        font = tkfont.Font(family="微软雅黑", size=9, weight="bold")
        # 界面里用到的 emoji 都列上：这里没覆盖到的字形，第一次画到时还要再查一次回退
        for ch in ("🌓", "📂", "⚠️", "←", "⚙", "☀️", "🌙"):
            font.measure(ch)
    except Exception:
        pass
    try:
        # 顺手把一个带 emoji 的 Label 建了再扔，把上面第 2 笔开销也带走
        lbl = tk.Label(text="⚠️ 预热", font=("微软雅黑", 10, "bold"))
        lbl.destroy()
    except Exception:
        pass


def lighten_color(hex_color, amount=40):
    """把颜色整体调亮，用于鼠标悬停的高亮反馈。"""
    r = min(255, int(hex_color[1:3], 16) + amount)
    g = min(255, int(hex_color[3:5], 16) + amount)
    b = min(255, int(hex_color[5:7], 16) + amount)
    return "#%02x%02x%02x" % (r, g, b)


def _shade(hex_color, amount):
    """#rrggbb 整体加减一个值（负数=变暗），越界自动夹到 0~255。"""
    try:
        parts = [max(0, min(255, int(hex_color[i:i + 2], 16) + amount))
                 for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(parts)
    except Exception:
        return hex_color


def hover_pair(colors):
    """悬停时的配色 = 基色两个色标都调亮同样一档。

    以前每个调用点各自传 hover_colors，于是有的按钮悬停变亮、有的把渐变整个反过来、
    还有的变暗——手感乱七八糟。现在统一由这里算：全程序一个规则 = 变亮。
    """
    return (lighten_color(colors[0]), lighten_color(colors[1]))


class SmoothScroller:
    """给可滚动控件加平滑滚动（滚轮逐帧动画）。

    实测前提（Tk 8.6 / Windows）：Text 的 `yview_scroll(n, "pixels")` 可用，视图
    **不是**按行对齐的（能停在半行上），每帧只重绘可见区，成本与文档长度无关——
    3000 行和 20000 行都是 1.5ms 上下，所以逐帧动画很宽裕（实测 66fps）。

    两类滚动目标：
    - `for_text`：像素级，真·平滑（Text 类控件）。
    - `for_rows`：只能整行整列走（ttk.Treeview、VirtualTable 这类按行步进的），
      引擎照样每帧插值，只是把"不足一行的零头"攒到下一帧，看起来仍是动画。

    只接管鼠标滚轮：拖滚动条、键盘翻页保持原样；连续滚轮只往目标累加，不互相打断。
    """

    def __init__(self, widget, mover, px_per_notch=48, frame_ms=12, ease=0.30,
                 bind_widgets=None, on_user_scroll=None, on_settle=None):
        self.widget = widget
        self.frame_ms = frame_ms
        self.ease = ease
        self.px_per_notch = px_per_notch
        self._mover = mover
        self._left = 0.0          # 还没滚完的像素（正=向下）
        self._job = None
        self._on_user_scroll = on_user_scroll
        self._on_settle = on_settle
        # 必须覆盖而不是 add="+"：类绑定（Tk 自带的一格跳 3 行）排在控件绑定之后，
        # 用 add 的话会先跳一次再动画，等于滚两倍。表头等也要绑，鼠标停在那儿也能滚。
        for w in (bind_widgets or [widget]):
            try:
                w.bind("<MouseWheel>", self._on_wheel)
            except Exception:
                pass
        try:
            _LIVE_SCROLLERS.add(self)
        except Exception:
            pass

    # ---------------------------------------------------------------- 构造入口
    @classmethod
    def for_text(cls, widget, bind_widgets=None, **kw):
        """Text 类：像素级平滑。一格滚轮沿用 Tk 默认的 3 行，手感速度不变。"""
        def mover(px):
            n = int(round(px))
            if n == 0:
                n = 1 if px > 0 else -1
            before = widget.yview()[0]
            try:
                widget.yview_scroll(n, "pixels")
            except Exception:
                return 0, True
            return (n, False) if abs(widget.yview()[0] - before) > 1e-9 else (0, True)

        return cls(widget, mover, px_per_notch=kw.pop("px_per_notch", None)
                   or _text_notch_px(widget), bind_widgets=bind_widgets, **kw)

    @classmethod
    def for_rows(cls, widget, row_px, on_render=None, bind_widgets=None, **kw):
        """按行滚动的控件（Treeview / 自绘表格）：攒够一行走一行，其余交给动画。

        一帧最多走一行（实测 Treeview 一次行滚动只要 0.03ms，便宜得很），动画才
        "看得见"；只有剩余很多（猛滚十几格）时才允许一帧多走几行，否则一次滚 20 格
        要 60 帧、拖沓得没法用。
        """
        acc = {"v": 0.0}

        def mover(px):
            acc["v"] += px
            if row_px <= 0:
                return 0, True
            rows = int(acc["v"] // row_px)
            remain = abs(acc["v"]) / row_px
            cap = 1 if remain <= 8 else max(1, int(remain / 4))
            rows = max(-cap, min(cap, rows))
            if rows == 0:
                return 0, False           # 零头先攒着，不算"滚不动"
            acc["v"] -= rows * row_px
            before = widget.yview()[0]
            try:
                widget.yview_scroll(rows, "units")
            except Exception:
                return 0, True
            if on_render is not None:
                try:
                    on_render()
                except Exception:
                    pass
            if abs(widget.yview()[0] - before) <= 1e-9:
                acc["v"] = 0.0
                return 0, True            # 到顶/底了
            return rows * row_px, False

        return cls(widget, mover, px_per_notch=kw.pop("px_per_notch", None) or row_px * 3,
                   bind_widgets=bind_widgets, **kw)

    # ------------------------------------------------------------------- 状态
    def scrolling(self):
        return self._job is not None or abs(self._left) >= 1.0

    def scroll_px(self, px):
        """按像素滚动（键盘/程序触发），走的是和滚轮同一套动画，手感一致。"""
        try:
            self._left += float(px)
        except Exception:
            return
        if self._job is None:
            try:
                self._job = self.widget.after(1, self._step)
            except Exception:
                self._job = None

    def stop(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._left = 0.0

    # ------------------------------------------------------------------- 动画
    def _on_wheel(self, event):
        delta = getattr(event, "delta", 0) or 0
        # 触控板在 Windows 上会发小于 120 的细粒度 delta，按比例算，别一刀切
        self._left += (-delta / 120.0) * self.px_per_notch
        if self._on_user_scroll is not None:
            try:
                self._on_user_scroll(self._left < 0)   # True = 用户往上滚
            except Exception:
                pass
        if self._job is None:
            self._job = self.widget.after(1, self._step)
        return "break"

    def _settle(self):
        if self._on_settle is not None:
            try:
                self._on_settle()
            except Exception:
                pass

    def _step(self):
        self._job = None
        try:
            if not self.widget.winfo_exists():
                return
        except Exception:
            return
        if abs(self._left) < 1.0:
            self._left = 0.0
            self._settle()
            return
        # 每帧走剩余量的 ease 倍；不足 1 像素也要走 1，否则会卡在最后一点点
        move = self._left * self.ease
        if abs(move) < 1.0:
            move = 1.0 if move > 0 else -1.0
        before_left = self._left
        consumed, blocked = self._mover(move)
        if blocked:
            self._left = 0.0
            self._settle()
            return
        self._left -= consumed
        # 兜底：mover 的消费量必须让剩余量变小。若某个 mover 返回了绝对值而不是
        # 带符号的位移，向上滚时剩余量会被越减越大 —— 表现就是"向上滚异常快"。
        # 真出现这种情况就当滚到底直接停，宁可不动也别失控。
        if abs(self._left) > abs(before_left):
            self._left = 0.0
            self._settle()
            return
        self._job = self.widget.after(self.frame_ms, self._step)


def _hwnd_of(win):
    """取窗口的 HWND（Tk 主窗口 / Toplevel 都行）。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        return user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id()
    except Exception:
        return None


def is_dark_theme(theme):
    """判断给进来的主题是不是深色（theme 是 utils.theme 里那两个字典之一）。"""
    try:
        from utils.theme import DARK_THEME
        if theme is DARK_THEME:
            return True
        return isinstance(theme, dict) and theme.get("bg") == DARK_THEME.get("bg")
    except Exception:
        return False


def _stylable(win):
    """判断这个窗口该不该上原生标题栏样式。

    两类窗口必须跳过：
    - **overrideredirect 的无边框窗口**（启动闪屏就是）：它压根没有标题栏，
      给它写 caption/边框颜色反而会把它的分层渲染搞坏——实测闪屏卡片会变成
      "能透出桌面代码"的怪透明窗（用户看到的就是这个）。
    - **带 alpha 的 layered 窗口**：同理，DWM 标题栏属性和 layered 混用会出问题。
    """
    try:
        if win.overrideredirect():
            return False
    except Exception:
        pass
    try:
        if float(win.attributes("-alpha")) < 0.999:
            return False
    except Exception:
        pass
    return True


def style_window(win, dark=False, round_corners=True):
    """给窗口上原生外观：标题栏颜色跟随主题 + Win11 原生圆角。

    Tk 完全管不到标题栏：深色主题下窗口内容全黑、标题栏还是白的，割裂得厉害。
    这里用 pywinstyles（CC0 公共领域、纯 ctypes、无依赖 → 打包零风险）。

    关键坑一：**只把 immersive dark mode（属性 19/20）设回 0 是不会恢复浅色标题栏的**
    ——DWM 对第一次的 dark 有"粘性"，实测属性回 0 后标题栏依旧是黑的（强制
    FRAMECHANGED / RedrawWindow 都没用）。所以这里额外显式写标题栏配色
    （DWMWA_CAPTION_COLOR=35、DWMWA_TEXT_COLOR=36，Win11 22000+ 起支持）。
    关键坑二：无边框 / 半透明窗口不能刷（见 _stylable），否则会坏掉它自己的渲染。
    没装库 / 系统不支持 / 任何异常都静默跳过，界面退回原来的样子。
    """
    if not _stylable(win):
        return False
    try:
        import pywinstyles
    except Exception:
        return False
    try:
        pywinstyles.apply_style(win, "dark" if dark else "light")
    except Exception:
        pass
    # 标题栏底色 + 标题文字色：显式指定，避免 dark 粘住
    caption, title_fg = ("#202020", "#ffffff") if dark else ("#f0f0f0", "#000000")
    try:
        pywinstyles.change_header_color(win, caption)
    except Exception:
        pass
    try:
        pywinstyles.change_title_color(win, title_fg)
    except Exception:
        pass
    if round_corners:
        hwnd = _hwnd_of(win)
        if hwnd:
            try:
                import ctypes
                pywinstyles.ChangeDWMAttrib(hwnd, 33, ctypes.c_int(2))
            except Exception:
                pass
    return True


def _text_notch_px(widget):
    """一格滚轮的像素数 = 3 行（跟 Tk 默认的滚动距离一致）。"""
    try:
        import tkinter.font as tkfont
        f = tkfont.Font(font=widget.cget("font"))
        return max(9, 3 * int(f.metrics("linespace")))
    except Exception:
        return 48


def tree_row_px(widget=None, default=20):
    """取 Treeview 的行高（按行平滑滚动要用）。"""
    try:
        import tkinter.ttk as ttk
        return max(8, int(ttk.Style().lookup("Treeview", "rowheight") or default))
    except Exception:
        return default


try:
    from PIL import Image as _PILImage, ImageDraw as _PILDraw, ImageTk as _PILImageTk
    _PIL_OK = True
except Exception:      # 没有 Pillow 也能跑：退回原来的直角矩形画法
    _PIL_OK = False

_BTN_RADIUS = 6        # 渐变按钮圆角半径（像素）；按钮太矮时按高度收紧
_BTN_SS = 4            # 超采样倍数：4 倍画完再缩回来，圆角边缘才不会有锯齿
_BTN_CACHE_MAX = 512
_btn_cache = {}

# 圆角输入框的底图缓存：(宽,高,圆角,填充,描边,父底色) -> PIL 图
_field_cache = {}
_FIELD_SS = 4


def _rounded_field_image(w, h, radius, fill, border, corner_bg=None, border_w=1,
                         ss=None):
    """圆角输入框的底：描边 + 内部填充，四角透明（交给 Tk 跟父容器底色混）。

    Tk 的 Entry 没有 border-radius，圆角只能靠"背后垫一张图"来做。
    border_w 用来加粗描边（正在编辑的输入框用 2px，一眼能看出是哪个）。
    ss 是超采样倍数：4 倍最细腻但一张要 4~8ms；拖窗口缩放时先用 1 倍顶上，
    停手后再补一张精细的（见 RoundedEntry._on_configure）。
    """
    if w <= 2 or h <= 2:
        return None
    bw = max(1, min(int(border_w), max(1, min(w, h) // 2 - 1)))
    s = int(ss or _FIELD_SS)
    key = (int(w), int(h), int(radius), str(fill), str(border), str(corner_bg), bw, s)
    hit = _field_cache.get(key)
    if hit is not None:
        return hit
    try:
        im = _PILImage.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        d = _PILDraw.Draw(im)
        d.rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=max(0, radius * s),
                            fill=border)
        d.rounded_rectangle([bw * s, bw * s, (w - bw) * s - 1, (h - bw) * s - 1],
                            radius=max(0, radius * s - bw * s), fill=fill)
        if s != 1:
            im = im.resize((w, h), _PILImage.LANCZOS)
    except Exception:
        return None
    if len(_field_cache) > 64:
        # 实际只需要"当前尺寸 × 聚焦与否 × 深浅主题 × 超采样"这十来个键；
        # 拖拽缩放时会不断产生新宽度，所以给个上限兜住内存（一张最大约 120KB）。
        _field_cache.clear()
    _field_cache[key] = im
    return im


class RoundedEntry(tk.Frame):
    """圆角单行输入框：Canvas 垫一张圆角底图，真正的 tk.Entry 放在上面。

    只改外观——输入、IME 中文输入、右键菜单、拖选、快捷键全都还是原生 Entry
    在处理，所以不会因为"自绘"而丢功能。
    用法跟 tk.Entry 基本一致：get/delete/insert/bind/icursor 等都会转发给内部
    Entry；pack/grid 作用于外壳（Frame）。
    """

    def __init__(self, master, theme, textvariable=None, chars=20, height=30,
                 radius=8, pad_x=9, font=("微软雅黑", 10), width=None, **kw):
        base_bg = "#f0f0f0"
        try:
            base_bg = master.cget("bg")
        except Exception:
            pass
        if str(base_bg).startswith("System"):
            base_bg = (theme or {}).get("bg", "#f0f0f0")
        super().__init__(master, bg=base_bg, height=height)
        self._is_rounded_entry = True
        self.theme = dict(theme or {})
        self._radius = radius
        self._pad_x = pad_x
        self._h = height
        self._focus = False
        self._photo = None
        try:
            self.pack_propagate(False)
            self.grid_propagate(False)
        except Exception:
            pass
        self._font = tkfont.Font(font=font)
        ch_w = self._font.measure("0") or 7
        self._px_w = int(width if width else ch_w * max(4, int(chars)) + pad_x * 2)

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=base_bg,
                                width=self._px_w, height=height)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.set_corner_bg = self._set_corner_bg
        self.entry = tk.Entry(self.canvas, textvariable=textvariable, bd=0,
                              highlightthickness=0, relief="flat",
                              bg=self.theme.get("entry_bg", "#ffffff"),
                              fg=self.theme.get("entry_fg", "#000000"),
                              insertbackground=self.theme.get("fg", "#000000"),
                              font=font, **kw)
        self._img_id = self.canvas.create_image(0, 0, anchor="nw")
        self._entry_id = self.canvas.create_window(pad_x, height // 2, anchor="w",
                                                   window=self.entry)
        self._last_w = None
        self._fine_job = None
        self.canvas.bind("<Configure>", lambda e: self._on_configure())
        # 点在圆角留白处也应该聚焦到输入框上
        self.canvas.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.configure(width=self._px_w)
        self.after_idle(self._redraw)

    # -------------------------------------------------------------- 兼容 tk.Entry
    def __getattr__(self, name):
        # __init__ 期间也会走到这里（属性还没建好），必须先挡住，否则无限递归
        if name in ("entry", "canvas", "_redraw", "theme"):
            raise AttributeError(name)
        entry = self.__dict__.get("entry")
        if entry is None:
            raise AttributeError(name)
        return getattr(entry, name)

    # 下面这几个 Frame 自己也有同名方法，__getattr__ 轮不到它们 —— 必须显式转发。
    # 尤其 bind：不转发的话绑定会落在"外壳 Frame"上，而 Frame 拿不到键盘事件，
    # 搜索框里按回车就没反应了。
    def bind(self, sequence=None, func=None, add=None):
        return self.entry.bind(sequence, func, add)

    def unbind(self, sequence, funcid=None):
        return self.entry.unbind(sequence, funcid)

    def focus_set(self):
        return self.entry.focus_set()

    def focus_force(self):
        return self.entry.focus_force()

    def focus(self):
        return self.entry.focus()

    # ------------------------------------------------------------------ 外观
    def _set_corner_bg(self, color):
        """主题切换时同步外壳/画布底色（圆角是透明的，露出的就是这个色）。"""
        try:
            self.configure(bg=color)
            self.canvas.configure(bg=color)
        except Exception:
            pass

    def _base_bg(self):
        """外壳/画布该用的底色 = 父容器的真实底色。

        构造时父容器（LabelFrame 之类）可能还没被主题刷过，cget("bg") 会给出
        系统色 SystemButtonFace —— 那样圆角外面会露出一圈浅灰（深色主题下很扎眼）。
        所以系统色一律换成主题底色。
        """
        bg = None
        try:
            bg = self.master.cget("bg")
        except Exception:
            bg = None
        if not bg or str(bg).startswith("System"):
            bg = self.theme.get("bg", "#f0f0f0")
        return bg

    def _on_configure(self):
        """尺寸变化：宽度真变了就先画一张便宜的（1 倍超采样），停手后补精细的。

        4 倍超采样 + LANCZOS 一张 957×30 要 8ms；拖窗口缩放时每秒会来几十次
        <Configure>，每次重生成会明显拖慢拖拽。1 倍只要 1ms 左右，边角在拖动
        过程中略糙、停手 140ms 后自动变回精细版。
        """
        w = self.canvas.winfo_width()
        if w <= 1:
            return
        if w != self._last_w:
            self._last_w = w
            self._redraw(fast=True)
            if self._fine_job is not None:
                try:
                    self.after_cancel(self._fine_job)
                except Exception:
                    pass
            self._fine_job = self.after(140, self._fine_redraw)
        else:
            self._redraw()

    def _fine_redraw(self):
        self._fine_job = None
        self._redraw()

    def _set_focus(self, focused):
        self._focus = bool(focused)
        self._redraw()

    def set_theme(self, theme):
        """主题切换：更新填充/描边/文字色并重画。"""
        self.theme = dict(theme or {})
        try:
            self.entry.configure(bg=self.theme.get("entry_bg", "#ffffff"),
                                 fg=self.theme.get("entry_fg", "#000000"),
                                 insertbackground=self.theme.get("fg", "#000000"))
        except Exception:
            pass
        self._set_corner_bg(self._base_bg())
        self._redraw()

    def _redraw(self, fast=False):
        w = self.canvas.winfo_width()
        if w <= 1:
            w = self._px_w
        h = self._h
        fill = self.theme.get("entry_bg", "#ffffff")
        dark = is_dark_theme(self.theme)
        if self._focus:
            # 正在编辑的那个必须最显眼：深色主题用亮蓝，浅色主题用中蓝，并且加粗到 2px。
            # （以前这两种都用 accent_bg 压暗算：深色主题的 accent_bg 是 #3a4a5a，
            #   压暗后接近全黑，反而比没聚焦的浅灰描边还看不见 —— 等于搞反了。）
            border = "#64b5f6" if dark else "#4a90d9"
            bw = 2
        else:
            # 没在编辑的保持低调：深色主题的 border 是 #bebebe，在暗底上太抢眼
            border = "#4a4a4a" if dark else self.theme.get("border", "#c8c8c8")
            bw = 1
        img = _rounded_field_image(w, h, self._radius, fill, border,
                                   self.canvas.cget("bg"), bw,
                                   ss=1 if fast else None)
        if img is not None and _PIL_OK:
            try:
                self._photo = _PILImageTk.PhotoImage(img)
                self.canvas.itemconfigure(self._img_id, image=self._photo)
            except Exception:
                pass
        else:
            # 没有 Pillow：退回直角框，功能不受影响
            try:
                self.canvas.itemconfigure(self._img_id, image="")
                self.canvas.delete("fallback")
                self.canvas.create_rectangle(0, 0, w - 1, h - 1, outline=border,
                                             tags="fallback")
                self.canvas.tag_lower("fallback")
            except Exception:
                pass
        try:
            self.canvas.coords(self._img_id, 0, 0)
            self.canvas.coords(self._entry_id, self._pad_x, h // 2)
            self.canvas.itemconfigure(self._entry_id,
                                      width=max(10, w - self._pad_x * 2),
                                      height=max(10, h - 8))
        except Exception:
            pass


def _tk_rgb(widget, color):
    """Tk 颜色（#rrggbb 或系统色名）→ PIL 用的 0~255 三元组。"""
    if isinstance(color, (tuple, list)) and len(color) == 3:
        return tuple(max(0, min(255, int(c))) for c in color)
    try:
        r, g, b = widget.winfo_rgb(color)
        return (r // 256, g // 256, b // 256)
    except Exception:
        return (240, 240, 240)


def _rounded_gradient(width, height, colors, hover, radius, widget):
    """按尺寸+配色缓存地生成「圆角渐变图」（RGBA，四角透明）。

    四角为什么用透明而不是涂成父容器底色：涂底色的话，一换主题所有按钮都得重新
    出图；透明角交给 Tk 自己跟画布底色混合，换主题只要改画布底色就行。
    """
    c0 = _tk_rgb(widget, colors[0])
    c1 = _tk_rgb(widget, colors[1])
    h0 = _tk_rgb(widget, hover[0])
    h1 = _tk_rgb(widget, hover[1])
    key = (width, height, c0, c1, h0, h1, radius)
    hit = _btn_cache.get(key)
    if hit is not None:
        return hit

    W, H = width * _BTN_SS, height * _BTN_SS
    r = max(0, min(int(radius) * _BTN_SS, min(W, H) // 2))

    def band(a, b):
        # 竖直渐变：先画 1 像素宽的一列再横向拉满，比逐像素填整张图快得多
        col = _PILImage.new("RGB", (1, H))
        px = col.load()
        for y in range(H):
            t = y / (H - 1) if H > 1 else 0.0
            px[0, y] = (int(a[0] + (b[0] - a[0]) * t),
                        int(a[1] + (b[1] - a[1]) * t),
                        int(a[2] + (b[2] - a[2]) * t))
        return col.resize((W, H))

    def rounded(base):
        img = base.convert("RGBA")
        if r > 0:
            mask = _PILImage.new("L", (W, H), 0)
            _PILDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1],
                                                  radius=r, fill=255)
            img.putalpha(mask)
        return img.resize((width, height), _PILImage.LANCZOS)

    imgs = {"normal": rounded(band(c0, c1)), "hover": rounded(band(h0, h1))}
    if len(_btn_cache) >= _BTN_CACHE_MAX:
        _btn_cache.clear()
    _btn_cache[key] = imgs
    return imgs


_icon_cache = {}


def make_theme_icon(kind, size=20, color="#ffffff"):
    """手绘的月亮/太阳图标（RGBA、纯白）。

    为什么不用 emoji：15pt 的 ☀️ 在 Tk 里就是一坨圆点加花边，用户根本不认。
    自己画的话圆心半径、光芒长度粗细全由自己定，配 4 倍超采样也够锐。
    """
    key = (kind, size, color)
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    if not _PIL_OK:
        return None

    S = size * _BTN_SS
    img = _PILImage.new("RGBA", (S, S), (0, 0, 0, 0))
    d = _PILDraw.Draw(img)
    c = S / 2.0

    if kind == "sun":
        # 参数是比出来的：核心太大+光芒太细像蒲公英，光芒太粗又像隔壁那个齿轮，
        # 这组（核心 r=0.22S，光芒 0.32S→0.48S，粗 0.10S）在 22px 下最像太阳。
        r_core = 0.22 * S
        d.ellipse([c - r_core, c - r_core, c + r_core, c + r_core], fill=color)
        w = max(2, int(round(0.10 * S)))
        r0, r1 = 0.32 * S, 0.48 * S
        for i in range(8):
            a = math.radians(i * 45)
            x0, y0 = c + r0 * math.cos(a), c + r0 * math.sin(a)
            x1, y1 = c + r1 * math.cos(a), c + r1 * math.sin(a)
            d.line([x0, y0, x1, y1], fill=color, width=w)
            # line 是方头，两端各补一个小圆，光芒才不是一截一截的方块
            for px_, py_ in ((x0, y0), (x1, y1)):
                d.ellipse([px_ - w / 2.0, py_ - w / 2.0,
                           px_ + w / 2.0, py_ + w / 2.0], fill=color)
    else:
        # 月牙 = 大圆减掉一个偏移的圆
        pad = 0.09 * S
        d.ellipse([pad, pad, S - pad, S - pad], fill=color)
        cut = _PILImage.new("L", (S, S), 0)
        off = 0.33 * S
        _PILDraw.Draw(cut).ellipse([pad + off, pad - 0.14 * S,
                                    S - pad + off, S - pad - 0.14 * S], fill=255)
        img.putalpha(_PILImage.composite(_PILImage.new("L", (S, S), 0),
                                         img.getchannel("A"), cut))

    img = img.resize((size, size), _PILImage.LANCZOS)
    if len(_icon_cache) > 64:
        _icon_cache.clear()
    _icon_cache[key] = img
    return img


def create_gradient_button(parent, text, command, colors=("#00bcd4", "#3f51b5"),
                           width=180, height=32, font=("微软雅黑", 10, "bold"),
                           click_guard_ms=300):
    state = {"colors": colors, "hover": hover_pair(colors), "text": text, "icon": None}
    radius = max(0, min(_BTN_RADIUS, height // 3))
    use_img = bool(_PIL_OK and radius > 0)
    canvas = tk.Canvas(parent, width=width, height=height, highlightthickness=0,
                       bg=parent.cget("bg"))
    canvas.pack_propagate(False)
    canvas._icon_photo = None
    bg_id = {"id": None}

    def _photos():
        got = _rounded_gradient(width, height, state["colors"], state["hover"],
                               radius, parent)
        return {k: _PILImageTk.PhotoImage(v) for k, v in got.items()}

    # PhotoImage 必须留引用，否则会被回收成空白
    canvas._btn_photos = _photos() if use_img else {}

    def draw_bg(hover=False):
        if use_img:
            key = "hover" if hover else "normal"
            if bg_id["id"] is None:
                bg_id["id"] = canvas.create_image(0, 0, anchor="nw",
                                                  image=canvas._btn_photos[key],
                                                  tags="bg")
            else:
                # 换图比重画几十行矩形便宜，悬停手感更稳
                canvas.itemconfigure(bg_id["id"], image=canvas._btn_photos[key])
            return
        canvas.delete("bg")
        bg_id["id"] = None
        c0, c1 = state["hover"] if hover else state["colors"]
        for i in range(height):
            ratio = i / height
            r = int(int(c0[1:3], 16) + (int(c1[1:3], 16) - int(c0[1:3], 16)) * ratio)
            g = int(int(c0[3:5], 16) + (int(c1[3:5], 16) - int(c0[3:5], 16)) * ratio)
            b = int(int(c0[5:7], 16) + (int(c1[5:7], 16) - int(c0[5:7], 16)) * ratio)
            color = f"#{r:02x}{g:02x}{b:02x}"
            canvas.create_rectangle(0, i, width, i+1, fill=color, outline="", tags="bg")

    def draw_content():
        """画按钮内容：有图标就贴图标，否则写文字。悬停/换色后都要重画一次。"""
        canvas.delete("text")
        canvas.delete("icon")
        canvas._icon_photo = None
        icon = state.get("icon")
        if icon is not None and _PIL_OK:
            canvas._icon_photo = _PILImageTk.PhotoImage(icon)   # 必须留引用
            canvas.icon_id = canvas.create_image(width // 2, height // 2,
                                                 image=canvas._icon_photo, tags="icon")
            return
        canvas.text_id = canvas.create_text(width//2, height//2, text=state["text"],
                                            fill="white", font=font, tags="text")

    def set_gradient(c0, c1, h0=None, h1=None):
        """热切换渐变配色（用于溢出提示等）。没给悬停色就按新基色重算高亮。"""
        state["colors"] = (c0, c1)
        state["hover"] = (h0, h1) if (h0 and h1) else hover_pair((c0, c1))
        if use_img:
            canvas._btn_photos = _photos()
        draw_bg(False)
        draw_content()

    canvas.set_gradient = set_gradient

    def set_corner_bg(new_bg):
        """主题切换时同步画布底色：圆角是透明的，由 Tk 拿画布底色去混。

        必须用「父容器的真实底色」而不是主题里的 bg —— 按钮可能坐在 LabelFrame
        这类底色不同的容器上，用错了四角会露出一点别的颜色。
        """
        try:
            canvas.configure(bg=new_bg)
        except Exception:
            pass
    canvas.set_corner_bg = set_corner_bg

    # 支持像普通 Button 一样动态改 command / state
    cmd = {"fn": command}
    def set_command(fn):
        cmd["fn"] = fn
    canvas.set_command = set_command

    def set_text(new_text):
        """动态改按钮文字（替代 tk.Button 的 config(text=...)）。"""
        state["text"] = new_text
        state["icon"] = None      # 回到文字模式
        draw_content()
    canvas.set_text = set_text

    def set_icon(pil_img):
        """把按钮内容换成一张居中的图标图（PIL Image）；传 None 回到文字。"""
        state["icon"] = pil_img
        draw_content()
    canvas.set_icon = set_icon

    flags = {"disabled": False}
    def state_get(key):
        return flags.get(key, False)
    canvas.state = lambda new_state=None: (None if new_state is None else flags.update(
        {"disabled": str(new_state).lower() == "disabled"}))
    canvas._base_colors = tuple(colors)
    canvas._base_hover = tuple(state["hover"])

    def on_enter(e):
        if not state_get("disabled"):
            draw_bg(True)
            draw_content()
    def on_leave(e):
        if not state_get("disabled"):
            draw_bg(False)
            draw_content()
    click_at = {"t": 0.0}

    def on_click(e):
        if state_get("disabled") or cmd["fn"] is None:
            return
        # 防连点/误双击：冷却期内忽略重复点击，避免一个按钮被连点触发多次重活
        now = time.time()
        if click_guard_ms and (now - click_at["t"]) * 1000 < click_guard_ms:
            return
        click_at["t"] = now
        cmd["fn"]()

    draw_bg(False)
    draw_content()
    canvas.bind("<Enter>", on_enter)
    canvas.bind("<Leave>", on_leave)
    canvas.bind("<Button-1>", on_click)
    return canvas

def center_window(win, w=None, h=None):
    """把窗口居中到屏幕。

    关键：必须在窗口仍然 withdraw 的时候调用，然后再 deiconify —— 否则会看到
    「先闪现在某个角落、再跳到屏幕中间」的瞬移。
    w/h 省略或无效时按窗口自身尺寸计算。
    """
    try:
        win.update_idletasks()
        if not w or w <= 1:
            w = win.winfo_width()
        if not h or h <= 1:
            h = win.winfo_height()
        if w <= 1:
            w = win.winfo_reqwidth()
        if h <= 1:
            h = win.winfo_reqheight()
        x = max(0, (win.winfo_screenwidth() - w) // 2)
        y = max(0, (win.winfo_screenheight() - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        pass


def circular_reveal(win, cx, cy, on_switch=None, steps=30, interval=13, on_done=None):
    """真·圆形揭示过渡：圆内是「新界面」，圆外是「旧界面」。

    Tk 既不能给窗口做圆形裁剪，画布也没有 alpha 合成，所以分成四步：
      1. 用 Pillow 把窗口当前画面截下来（这就是「旧界面」）
      2. 用一个无边框覆盖窗原样盖住 —— 此时用户察觉不到任何变化
      3. 在覆盖层底下真正切换主题
      4. 用 Windows 的 SetWindowRgn 在截图上挖一个不断扩大的「圆洞」，
         把下面的新界面一点点露出来
    cx/cy 是屏幕坐标。任何一步失败都会立刻执行切换，功能不受影响。
    """
    state = {"switched": False, "done": False, "ov": None}

    def do_switch():
        if not state["switched"]:
            state["switched"] = True
            if on_switch:
                try:
                    on_switch()
                except Exception:
                    pass

    def finish():
        if not state["done"]:
            state["done"] = True
            do_switch()          # 兜底：无论走哪条路径，主题都必须切过去
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass

    try:
        import ctypes
        from PIL import ImageGrab, ImageTk

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        # 64 位下句柄必须声明成指针，否则会被截断成 32 位
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        gdi32.CreateRectRgn.restype = ctypes.c_void_p
        gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        gdi32.CreateEllipticRgn.restype = ctypes.c_void_p
        gdi32.CreateEllipticRgn.argtypes = [ctypes.c_int] * 4
        gdi32.CombineRgn.restype = ctypes.c_int
        gdi32.CombineRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_int]
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]

        # 覆盖范围必须把已打开的子窗口也算进去：否则它们不会被截图遮住，
        # 主窗口还在做圆形揭示时它们就已经变成新主题了，看起来会不同步。
        wins = [win]
        try:
            for ch in win.winfo_children():
                if isinstance(ch, tk.Toplevel) and ch.winfo_ismapped():
                    wins.append(ch)
        except Exception:
            pass
        x0 = min(x.winfo_rootx() for x in wins)
        y0 = min(x.winfo_rooty() for x in wins)
        x1 = max(x.winfo_rootx() + x.winfo_width() for x in wins)
        y1 = max(x.winfo_rooty() + x.winfo_height() for x in wins)
        w, h = x1 - x0, y1 - y0
        if w <= 1 or h <= 1:
            finish()
            return None

        # 1) 截下旧界面
        shot = ImageGrab.grab(bbox=(x0, y0, x0 + w, y0 + h))
        photo = ImageTk.PhotoImage(shot)

        # 2) 覆盖层原样显示旧界面，所以下面的切换是「偷偷」发生的
        ov = tk.Toplevel(win)
        ov._is_theme_overlay = True      # 主题刷新时要跳过它（内容是一张截图）
        state["ov"] = ov
        ov.overrideredirect(True)
        ov.geometry(f"{w}x{h}+{x0}+{y0}")
        cv = tk.Canvas(ov, width=w, height=h, highlightthickness=0, bd=0)
        cv.pack()
        cv.create_image(0, 0, anchor="nw", image=photo)
        cv.image = photo              # 保住引用，否则图片被回收会变成空白
        try:
            ov.attributes("-topmost", True)
        except Exception:
            pass
        ov.update_idletasks()
        ov.deiconify()
        ov.update()

        # 3) 在覆盖层底下真正切换主题
        do_switch()
        ov.update()

        target = user32.GetParent(ctypes.c_void_p(ov.winfo_id())) or ov.winfo_id()
        px = min(max(0, cx - x0), w)
        py = min(max(0, cy - y0), h)
        # 半径要够大到让圆能盖住离圆心最远的那个角
        full_r = int(((max(px, w - px)) ** 2 + (max(py, h - py)) ** 2) ** 0.5) + 2

        def set_hole(r):
            """把覆盖层裁成「矩形 - 圆」，圆的位置就是露出来的新界面。"""
            box = gdi32.CreateRectRgn(0, 0, w, h)
            circ = gdi32.CreateEllipticRgn(int(px - r), int(py - r),
                                           int(px + r), int(py + r))
            if circ:
                gdi32.CombineRgn(ctypes.c_void_p(box), ctypes.c_void_p(box),
                                 ctypes.c_void_p(circ), 4)      # RGN_DIFF
                gdi32.DeleteObject(ctypes.c_void_p(circ))       # 未被系统接管，自己删
            if box:
                # box 交给系统后不能再删
                user32.SetWindowRgn(ctypes.c_void_p(target), ctypes.c_void_p(box), True)

        set_hole(0)
        step = {"i": 0}

        def tick():
            step["i"] += 1
            t = step["i"] / float(steps)
            eased = 1 - (1 - t) ** 3        # ease-out：先快后慢，收尾更自然
            set_hole(int(full_r * eased))
            if step["i"] < steps:
                ov.after(interval, tick)
            else:
                try:
                    ov.destroy()
                except Exception:
                    pass
                finish()

        ov.after(interval, tick)
        return ov
    except Exception:
        ov = state.get("ov")
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass
        finish()
        return None


def clear_layered_style(win):
    """摘掉窗口的 WS_EX_LAYERED 样式。

    Tk 只要用过一次 -alpha，这个样式就一直留在窗口上；而 **Windows 对 layered
    窗口会跳过系统的隐藏/显示过渡动画**——实测 withdraw() 之后窗口直接消失，
    连同为普通窗口时那段约 190ms 的淡出都没有。
    等淡入结束、窗口已经完全不透明时把它摘掉，窗口就恢复成普通窗口的行为，
    关闭时系统那段动画才会回来。摘掉时窗口是 alpha=1.0，所以视觉上没有变化。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_long]
        hwnd = user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id()
        GWL_EXSTYLE, WS_EX_LAYERED = -20, 0x00080000
        ex = user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
        if ex & WS_EX_LAYERED:
            user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE,
                                  ex & ~WS_EX_LAYERED)
        return True
    except Exception:
        return False


def focus_window(win):
    """把焦点交给刚弹出的窗口，并把它提到最前。

    必须在 deiconify() 之后调用 —— 窗口还隐藏时抢焦点是无效的。
    """
    try:
        win.lift()
    except Exception:
        pass
    try:
        win.focus_force()
    except Exception:
        pass


def get_icon_path():
    """获取图标文件路径（支持开发环境和打包环境）"""
    import sys
    import os
    from pathlib import Path

    # 1. 打包后路径（sys._MEIPASS）
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
        # 在打包后的临时目录根目录查找
        icon_path = os.path.join(base_path, "1.ico")
        if os.path.exists(icon_path):
            return icon_path

    # 2. 开发环境：从当前文件（utils/helpers.py）向上找两级
    current_dir = Path(__file__).parent  # utils/
    project_root = current_dir.parent     # 项目根目录
    icon_path = project_root / "1.ico"
    if icon_path.exists():
        return str(icon_path)

    # 3. 尝试当前工作目录
    icon_path = Path.cwd() / "1.ico"
    if icon_path.exists():
        return str(icon_path)

    # 4. 都没找到，返回 None
    return None

def set_window_icon(window):
    icon_path = get_icon_path()
    if icon_path:
        try:
            window.iconbitmap(icon_path)
        except:
            pass
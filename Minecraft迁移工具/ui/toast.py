# ui/toast.py
"""应用内吐司消息：浮在主窗口右下角的临时提示条。

为什么要它：执行日志要只留"迁移相关"的内容。可原本一次性反馈（保存设置、复制路径、
清空清单、校验结果、目录不存在……）也全塞在日志里，把真正的过程信息淹掉了。
吐司是第三条通道：**纯告知、不阻塞、不进日志、不进 messagebox**。

形态（对齐用户给的参考图）：深色圆角条 + 等级图标 + 一行文案 + 底部进度条（走完自动消失）
+ 右上角 ×。右下角向上堆叠，最多 4 条，超出挤掉最旧的。

实现要点：
  · 圆角靠 PIL 出图当背景（2 倍超采样），**按宽度缓存**：同样的宽度只渲染一次，
    一条吐司从出现到消失不会重渲。
  · 进度条是"沙漏"不是动效：50ms 走一格就够，不必跟刷新率抢帧。
  · 滑入用 place 的 y 位置分几帧推进（place 不触发几何传播，比 pack 便宜得多）。
  · 不抢焦点、不拦鼠标：除了右上角那个 ×，别的地方点下去会穿到下面的界面。
"""

import time
import tkinter as tk
import tkinter.font as tkfont

from utils.dpi import px

_LEVELS = {
    "info":  ("i", "#4c9aff"),
    "ok":    ("✓", "#4caf50"),
    "warn":  ("!", "#ffa000"),
    "error": ("×", "#e53935"),
}


class ToastHost:
    """挂在主窗口上的吐司容器。整个程序只建一个，跟着主题走。

    master 上的位置：右下角，向上堆叠，右边距 _MARGIN。
    """

    _MARGIN = 18          # 离窗口右下角的距离（设计像素）
    _GAP = 8              # 两条之间的间隔
    _DRAIN_MS = 50        # 进度条刷新间隔
    _SLIDE_FRAMES = 4     # 滑入分几帧

    def __init__(self, master, theme, max_items=4):
        self.master = master
        self.theme = dict(theme or {})
        self.max_items = max(1, int(max_items))
        self._items = []          # 旧的在前、新的在后
        self._imgs = {}           # (宽, 高, 等级, 配色) -> PhotoImage 缓存
        self._closed = False
        # 预热：第一次弹吐司时才 import PIL 要 ~85ms，那一下会卡在你刚点的操作上。
        # 让它在窗口建完后的空闲时刻付掉（此时 PIL 一般已经因为图标/圆角被导入过了）。
        try:
            self.master.after(500, self._warm)
        except Exception:
            pass

    def _warm(self):
        try:
            self.card_image(px(240), px(46), "info")
        except Exception:
            pass

    # ---------------------------------------------------------------- 对外
    def show(self, text, level="info", ms=3000, sticky=None):
        """弹一条吐司。

        level：info / ok / warn / error（决定图标和强调色）
        ms   ：自动消失的毫秒数；sticky=True 时不自动消失，必须点 ×
        sticky 缺省：error 不自动消失，其余自动。
        """
        if self._closed:
            return None
        try:
            text = str(text or "").strip()
        except Exception:
            text = ""
        if not text:
            return None
        if level not in _LEVELS:
            level = "info"
        if sticky is None:
            sticky = (level == "error")
        try:
            item = _ToastItem(self, text, level, int(ms), bool(sticky))
        except Exception:
            return None
        self._items.append(item)
        while len(self._items) > self.max_items:
            try:
                self._items.pop(0).destroy()
            except Exception:
                pass
        self._relayout(animate=True)
        return item

    def clear(self):
        for it in list(self._items):
            try:
                it.destroy()
            except Exception:
                pass
        self._items = []
        self._relayout()

    def set_theme(self, theme):
        """主题变了：整批重建（吐司都很短命，不值得原地改配色）。"""
        self.theme = dict(theme or {})
        self._imgs.clear()
        texts = [(it.text, it.level, it.ms, it.sticky) for it in self._items]
        self.clear()
        for t, lv, ms, st in texts:
            self.show(t, lv, ms, st)

    def close(self):
        self._closed = True
        self.clear()

    def alive(self):
        return (not self._closed) and bool(self.master.winfo_exists())

    # ---------------------------------------------------------------- 布局
    def _relayout(self, animate=False):
        """从下往上排：最新的贴底，旧的往上顶。"""
        if self._closed:
            return
        try:
            mw = self.master.winfo_width()
            mh = self.master.winfo_height()
        except Exception:
            return
        if mw <= 1 or mh <= 1:
            try:
                self.master.after(30, self._relayout)
            except Exception:
                pass
            return
        y = mh - px(self._MARGIN)
        for it in reversed(self._items):
            w, h = it.size()
            y -= h
            x = mw - px(self._MARGIN) - w
            it.place(x, y)
            y -= px(self._GAP)

    # ---------------------------------------------------------------- 配色/位图
    def palette(self):
        """(卡片底, 描边, 文字色) —— 深色主题用近黑，浅色主题用白。"""
        th = self.theme or {}
        dark = str(th.get("bg", "#ffffff")).lower() in (
            "#1e1e1e", "#202020", "#252525", "#2e2e2e", "#1b1b1b")
        if dark:
            return ("#262626", "#3c3c3c", th.get("fg", "#e8e8e8"))
        return ("#ffffff", "#d8d8d8", th.get("fg", "#222222"))

    def card_pil(self, w, h, level):
        """圆角卡底（PIL 版，便于单测取像素）。"""
        from PIL import Image, ImageDraw
        S = 2
        fill, border, _fg = self.palette()
        条 = _LEVELS.get(level, _LEVELS["info"])[1]
        W, H = w * S, h * S
        box = [0, 0, W - 1, H - 1]
        r = px(10) * S
        # 左侧等级色条：先画整块圆角条色，再把"圆角卡片"贴在它上面、只让出左边 4px。
        # ⚠ 别用"整块圆角 + 方形矩形盖住右边"的老写法：那个矩形会把右侧圆角盖成直角。
        bar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(bar).rounded_rectangle(box, radius=r, fill=条)
        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(card).rounded_rectangle(box, radius=r, fill=fill,
                                               outline=border, width=S)
        mask = Image.new("L", (W, H), 255)
        ImageDraw.Draw(mask).rectangle([0, 0, px(4) * S - 1, H], fill=0)
        bar.paste(card, (0, 0), mask)
        return bar.resize((w, h), Image.LANCZOS)

    def card_image(self, w, h, level):
        """圆角卡底（按宽度缓存成 PhotoImage）。"""
        key = (int(w), int(h), level, self.palette())
        img = self._imgs.get(key)
        if img is not None:
            return img
        from PIL import ImageTk
        img = ImageTk.PhotoImage(self.card_pil(w, h, level))
        self._imgs[key] = img
        return img


class _ToastItem:
    """一条吐司。自己管自己的进度条和计时。"""

    _H = 46               # 设计高度
    _PAD = 12

    def __init__(self, host, text, level, ms, sticky):
        self.host = host
        self.text = text
        self.level = level
        self.ms = ms
        self.sticky = sticky
        self.gone = False
        self.t0 = time.perf_counter()
        self._drain_job = None
        self._slide_job = None

        self._w = 0
        self._h = 0

        import tkinter as _tk
        self.frame = _tk.Frame(host.master, bd=0, highlightthickness=0)
        self.canvas = _tk.Canvas(self.frame, bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)

        self._measure()
        self._draw()
        self._drain()

    # ---------------------------------------------------------------- 尺寸
    _FONT = None

    def _measure(self):
        """宽度按文字实测（限 180~340 设计像素），高度固定。

        Font 对象要缓存：每建一条吐司都 new 一个是 ~1ms 的纯浪费（吐司很短命）。
        """
        try:
            if _ToastItem._FONT is None:
                _ToastItem._FONT = tkfont.Font(family="微软雅黑", size=9)
            w = _ToastItem._FONT.measure(self.text)
        except Exception:
            w = len(self.text) * 12
        self._w = int(min(px(340), max(px(180), w + px(78))))
        self._h = int(px(self._H))
        try:
            self.frame.configure(width=self._w, height=self._h)
        except Exception:
            pass

    def size(self):
        return self._w, self._h

    def _draw(self):
        c = self.canvas
        try:
            c.delete("all")
        except Exception:
            pass
        img = self.host.card_image(self._w, self._h, self.level)
        self._card = img
        c.create_image(0, 0, anchor="nw", image=img)
        图, 色 = _LEVELS.get(self.level, _LEVELS["info"])
        _fill, _border, fg = self.host.palette()
        c.create_text(px(20), self._h // 2, anchor="w", text=图, fill=色,
                      font=("微软雅黑", 10, "bold"))
        c.create_text(px(40), self._h // 2, anchor="w", text=self.text, fill=fg,
                      font=("微软雅黑", 9))
        # 右上角 ×
        c.create_text(self._w - px(14), self._h // 2, anchor="e", text="✕",
                      fill=fg, font=("微软雅黑", 9))
        # 底部进度条（占满时不可见，随进度缩短）
        self._bar = c.create_rectangle(0, self._h - px(3), self._w,
                                       self._h, fill=色, outline="")

    # ---------------------------------------------------------------- 动画
    def _drain(self):
        if self.gone:
            return
        if self.sticky:
            try:
                self.canvas.itemconfigure(self._bar, state="hidden")
            except Exception:
                pass
            return
        try:
            已 = (time.perf_counter() - self.t0) * 1000.0
            余 = max(0.0, 1.0 - 已 / max(1, self.ms))
            self.canvas.coords(self._bar, 0, self._h - px(3),
                               int(self._w * 余), self._h)
            if 余 <= 0.0:
                self.destroy()
                return
            self._drain_job = self.canvas.after(self.host._DRAIN_MS, self._drain)
        except Exception:
            self._drain_job = None

    def place(self, x, y):
        try:
            self.frame.place(x=int(x), y=int(y))
            self.frame.lift()
        except Exception:
            pass

    def _on_click(self, _e=None):
        self.destroy()
        return "break"

    def destroy(self):
        if self.gone:
            return
        self.gone = True
        for job in (self._drain_job, self._slide_job):
            if job is not None:
                try:
                    self.canvas.after_cancel(job)
                except Exception:
                    pass
        try:
            self.frame.place_forget()
            self.frame.destroy()
        except Exception:
            pass
        try:
            if self in self.host._items:
                self.host._items.remove(self)
            self.host._relayout()
        except Exception:
            pass

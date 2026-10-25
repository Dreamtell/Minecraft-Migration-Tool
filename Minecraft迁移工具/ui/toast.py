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

from utils import anim
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
            item._new = True
        except Exception:
            return None
        self._items.append(item)
        while len(self._items) > self.max_items:
            try:
                self._items.pop(0).destroy(animate=False)
            except Exception:
                pass
        self._relayout(animate=True)
        return item

    def clear(self):
        for it in list(self._items):
            try:
                it.destroy(animate=False)
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
            if getattr(it, "_new", False):
                # 新卡：先放到目标位下方 16px，再滑上去（"冒出来"而不是"啪一下出现"）
                it._new = False
                it._x, it._y = x, y + px(16)
                it._place()
                it.move_to(x, y, animate=True)
            else:
                it.move_to(x, y, animate=True)      # 给新来的让位：平滑上移
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
        from PIL import Image, ImageDraw, ImageChops
        S = 2
        fill, border, _fg = self.palette()
        条 = _LEVELS.get(level, _LEVELS["info"])[1]
        W, H = w * S, h * S
        box = [0, 0, W - 1, H - 1]
        r = px(10) * S
        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(card).rounded_rectangle(box, radius=r, fill=fill,
                                               outline=border, width=S)
        # 左侧等级色条 = **卡片轮廓 ∩ 左侧 4px**，让色条两端跟着卡片圆弧走。
        # ⚠ 别写成"画一个 4px 宽的圆角矩形"：PIL 会把圆角半径夹到宽度的一半（=2px），
        # 色条两端看着就是方头（用户报"左侧圆角化有问题"）。
        sil = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sil).rounded_rectangle(box, radius=r, fill=255)
        strip = Image.new("L", (W, H), 0)
        ImageDraw.Draw(strip).rectangle([0, 0, px(4) * S - 1, H], fill=255)
        强调 = Image.new("RGBA", (W, H), 条)
        card.paste(强调, (0, 0), ImageChops.multiply(sil, strip))
        return card.resize((w, h), Image.LANCZOS)

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
    """一条吐司：自己管进度条、入场/出场、以及堆叠时的移动动画。

    ⚠ 三处不能退回去（都是实测被用户指出来的）：
    1. 进度条**必须内缩**。原来画的是整宽矩形（x 从 0 到 width），两端是直角还压在
       卡片的圆角上 —— 截图上就是一根方头绿条戳在圆角外面。
    2. 进度条**必须按帧平滑缩**。原来是 50ms 一跳（20fps），肉眼能看到它一格一格掉。
    3. 出入场**必须有动画**。原来 place() 直接定位、结束直接 destroy，卡片是"啪"地
       出现和消失，观感生硬（用户原话"不流畅不灵动"）。
    """

    _H = 46               # 设计高度
    _IN_MS = 150          # 入场：从下方滑上来
    _OUT_MS = 110         # 出场：向下滑走
    _MOVE_MS = 110        # 堆叠重排时其它卡片的让位
    _DRAIN_MS = 16        # 进度条刷新（≈60fps，一次只有一次 canvas.coords）

    def __init__(self, host, text, level, ms, sticky):
        self.host = host
        self.text = text
        self.level = level
        self.ms = ms
        self.sticky = sticky
        self.gone = False
        self.t0 = time.perf_counter()
        self._drain_job = None
        self._move_job = None
        self._x = 0
        self._y = 0
        self._w = 0
        self._h = 0

        import tkinter as _tk
        # ⚠ 尺寸顺序不能颠倒：先量出 (w,h)，再让 Canvas 用这个尺寸建、并且**不许
        # pack(fill="both", expand=True)**。否则画布会把它自己的默认尺寸（472x331）
        # 反向传给 Frame，Frame 变成一个巨大的不透明灰框 —— 卡片只画在左上角，
        # 其余部分把下面几条吐司全盖住（用户截图里"只有一条 + 周围一片灰"就是这个）。
        self._measure()
        self.frame = _tk.Frame(host.master, bd=0, highlightthickness=0,
                               width=self._w, height=self._h)
        try:
            self.frame.pack_propagate(False)
        except Exception:
            pass
        底 = None
        try:
            底 = host.master.cget("bg")
        except Exception:
            底 = None
        self.canvas = _tk.Canvas(self.frame, bd=0, highlightthickness=0,
                                 width=self._w, height=self._h,
                                 bg=底 or host.palette()[0])
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)

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

    # ---------------------------------------------------------------- 绘制
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
        c.create_text(px(22), self._h // 2, anchor="w", text=图, fill=色,
                      font=("微软雅黑", 10, "bold"))
        c.create_text(px(42), self._h // 2, anchor="w", text=self.text, fill=fg,
                      font=("微软雅黑", 9))
        c.create_text(self._w - px(14), self._h // 2, anchor="e", text="✕",
                      fill=fg, font=("微软雅黑", 9))
        # 进度条：铺满整条底边，但**按卡片底部的圆角裁剪**。
        # 一条直线贴着底边走，在两端圆弧处会戳到卡片轮廓外（用户报"没兼容圆角化"）。
        # 做法：拆成 3 行 1px 线段，每行按"该高度上圆弧的内缩量"左右各缩进一点，
        # 于是整条进度条始终落在卡片轮廓里。
        半径 = px(10)
        self._bar_rows = []
        for 距底 in (0.5, 1.5, 2.5):
            y = self._h - 距底
            弧 = max(0.0, 半径 - 距底)
            内缩 = 半径 - (max(0.0, 半径 ** 2 - 弧 ** 2)) ** 0.5
            x0 = 内缩
            iid = c.create_line(x0, y, self._w - x0, y, width=1,
                                capstyle="butt", fill=色)
            self._bar_rows.append((iid, x0, y))

    # ---------------------------------------------------------------- 进度条
    def _drain(self):
        """进度条按帧平滑缩（不是"沙漏"，用户要看得出它在走）。"""
        if self.gone:
            return
        if self.sticky:
            try:
                for iid, _x0, _y in self._bar_rows:
                    self.canvas.itemconfigure(iid, state="hidden")
            except Exception:
                pass
            return
        try:
            已 = (time.perf_counter() - self.t0) * 1000.0
            余 = max(0.0, 1.0 - 已 / max(1, self.ms))
            for iid, x0, y in self._bar_rows:
                x1 = x0 + (self._w - 2 * x0) * 余
                self.canvas.coords(iid, x0, y, x1, y)
            if 余 <= 0.0:
                self.destroy()
                return
            self._drain_job = self.canvas.after(self._DRAIN_MS, self._drain)
        except Exception:
            self._drain_job = None

    # ---------------------------------------------------------------- 位置/动画
    def move_to(self, x, y, animate=True):
        """移到目标位置。入场与堆叠让位共用这一条动画（ease-out，按真实时间）。"""
        tx, ty = int(x), int(y)
        if self.gone:
            return
        if not animate:
            self._x, self._y = tx, ty
            self._place()
            return
        fx, fy = self._x, self._y
        t0 = time.perf_counter()
        总 = float(self._IN_MS if (fx == 0 and fy == 0) else self._MOVE_MS)
        if self._move_job is not None:
            try:
                self.canvas.after_cancel(self._move_job)
            except Exception:
                pass
            self._move_job = None
            fx, fy = self._x, self._y

        def 帧():
            if self.gone:
                return
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / 总)
            缓 = 1 - (1 - k) ** 3
            self._x = fx + (tx - fx) * 缓
            self._y = fy + (ty - fy) * 缓
            self._place()
            if k < 1.0:
                try:
                    self._move_job = self.canvas.after(max(1, anim.frame_ms(self.canvas)), 帧)
                except Exception:
                    self._move_job = None
            else:
                self._x, self._y = tx, ty
                self._place()
                self._move_job = None

        self._place()
        帧()

    def _place(self):
        try:
            self.frame.place(x=int(self._x), y=int(self._y))
            self.frame.lift()
        except Exception:
            pass

    def _on_click(self, _e=None):
        self.destroy()
        return "break"

    # ---------------------------------------------------------------- 回收
    def destroy(self, animate=True):
        """向下滑走再销毁（没动画的话卡片是"啪"地消失）。"""
        if self.gone:
            return
        for job in (self._drain_job, self._move_job):
            if job is not None:
                try:
                    self.canvas.after_cancel(job)
                except Exception:
                    pass
        self._drain_job = None
        self._move_job = None
        if not animate:
            self._finish()
            return
        t0 = time.perf_counter()
        fy = self._y

        def 帧():
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / self._OUT_MS)
            缓 = k * k                      # ease-in：先慢后快，像被"抽走"
            self._y = fy + px(18) * 缓
            self._place()
            if k < 1.0:
                try:
                    self._move_job = self.canvas.after(max(1, anim.frame_ms(self.canvas)), 帧)
                except Exception:
                    self._move_job = None
            else:
                self._finish()

        帧()

    def _finish(self):
        self.gone = True
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

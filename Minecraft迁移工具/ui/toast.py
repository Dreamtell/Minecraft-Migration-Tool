# ui/toast.py
"""应用内吐司消息：浮在主窗口右下角的临时提示条。

为什么要它：执行日志要只留"迁移相关"的内容。可原本一次性反馈（保存设置、复制路径、
清空清单、校验结果、目录不存在……）也全塞在日志里，把真正的过程信息淹掉了。
吐司是第三条通道：**纯告知、不阻塞、不进日志、不进 messagebox**。

形态：圆角卡 + 等级色胶囊 + 等级图标 + 一行文案 + 底部进度条 + 右上角 ×。
右下角向上堆叠，最多 4 条，超出挤掉最旧的。

## 为什么是"独立 Toplevel + transparentcolor"，而不是挂在主窗口上的 Frame

Tk 的控件**没有真透明**：圆角只能靠"四角填上父容器的底色"来假装。只要这块东西
浮在**别的内容**上方（日志、清单、卡片……），那四角就会露出一块父底色的方块 ——
用户报的"浮层盖在别的内容上时，四角露出底色方块"就是这个。

所以吐司改成：一个无边框 Toplevel，用透明键色让那部分像素**真透明**
（并且点击穿透到下面的窗口）。卡片四角画成键色 → 真透明，盖在任何东西上都不会
露方块 —— 和 ui/net_float.py 是同一套路。

代价：它是个独立窗口，得自己跟着主窗口移动/缩放（见 _sync_pos），
主窗口不可见时要跟着藏（也在 _sync_pos 里）。

## 画法

整叠吐司共用**一张 Canvas**：每张卡是几个 canvas 图元（卡底图 + 3 段文字 + 3 行进度条），
每个卡片的图元打一个 tag。动画（入场/让位/出场）用 canvas.move 平移 tag，
进度条只改那 3 行线段的 coords —— 都不重建图元，帧成本远低于 1ms。
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

# 透明键色：卡片里不会出现的颜色，被 -transparentcolor 吃成全透明
_KEY = "#010203"


class ToastHost:
    """一整叠吐司（一个无边框、真透明的 Toplevel）。整个程序只建一个。"""

    _MARGIN = 18          # 离主窗口右下角的距离（设计像素）
    _GAP = 8              # 两条之间的间隔
    # 画布底部留出的滑动余量（设计像素）。入场从下往上滑、出场往下滑都会越过
    # 卡片自身的高度；不留余量的话 canvas 会把卡片**裁掉半截**（用户截图里消失
    # 动画"下半截没了"就是这个）。这块区域是透明键色，看不见也不挡点击。
    _PADB = 22

    def __init__(self, master, theme, max_items=4):
        self.master = master
        self.theme = dict(theme or {})
        self.max_items = max(1, int(max_items))
        self._items = []
        self._imgs = {}
        self._closed = False
        self._master_bind = None

        self.win = tk.Toplevel(master)
        try:
            self.win.withdraw()
        except Exception:
            pass
        try:
            self.win.overrideredirect(True)
        except Exception:
            pass
        try:
            self.win.attributes("-transparentcolor", _KEY)
        except Exception:
            pass
        try:
            self.win.configure(bg=_KEY)
        except Exception:
            pass
        self.canvas = tk.Canvas(self.win, bg=_KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)

        try:
            self._master_bind = master.bind("<Configure>", self._on_master_configure, add="+")
        except Exception:
            self._master_bind = None

        try:
            master.after(500, self._warm)
        except Exception:
            pass

    # ---------------------------------------------------------------- 退出清理
    def close(self):
        self._closed = True
        self.clear()
        try:
            if self._master_bind is not None:
                self.master.unbind("<Configure>", self._master_bind)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

    def alive(self):
        try:
            return (not self._closed) and bool(self.win.winfo_exists())
        except Exception:
            return False

    def set_theme(self, theme):
        self.theme = dict(theme or {})
        self._imgs.clear()
        旧 = [(it.text, it.level, it.ms, it.sticky) for it in self._items]
        self.clear()
        for t, lv, ms, st in 旧:
            self.show(t, lv, ms, st)

    # ---------------------------------------------------------------- 对外
    def show(self, text, level="info", ms=3000, sticky=None):
        """弹一条吐司；level = info / ok / warn / error，error 默认不自动消失。"""
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
            item = _Toast(self, text, level, int(ms), bool(sticky))
        except Exception:
            return None
        item._new = True
        self._items.append(item)
        while len(self._items) > self.max_items:
            try:
                self._items.pop(0).kill()
            except Exception:
                pass
        self._rebuild()
        return item

    def clear(self):
        for it in list(self._items):
            try:
                it.kill()
            except Exception:
                pass
        self._items = []
        self._rebuild()

    # ---------------------------------------------------------------- 画
    def card_image(self, w, h, level):
        """圆角卡底（按尺寸+配色缓存成 PhotoImage）。"""
        key = (int(w), int(h), level, self.palette())
        img = self._imgs.get(key)
        if img is not None:
            return img
        from PIL import ImageTk
        img = ImageTk.PhotoImage(self.card_pil(w, h, level))
        self._imgs[key] = img
        return img

    def card_pil(self, w, h, level):
        """圆角卡底（PIL 版，便于单测取像素）。"""
        from PIL import Image, ImageDraw
        S = 2
        fill, border, _fg = self.palette()
        条 = _LEVELS.get(level, _LEVELS["info"])[1]
        W, H = w * S, h * S
        box = [0, 0, W - 1, H - 1]
        r = px(10) * S
        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(card).rounded_rectangle(box, radius=r, fill=fill,
                                               outline=border, width=S)
        # 左侧等级色：内缩的胶囊（两端自己圆角）。别做成"4px 宽圆角矩形"——
        # PIL 会把半径夹到宽度一半，端头变方；也别做成"卡片轮廓 ∩ 竖条"——会被削成月牙。
        内缩 = px(3) * S
        条宽 = max(2, px(5) * S)
        条高 = max(1, H - 内缩 * 2)
        胶囊 = Image.new("RGBA", (条宽, 条高), (0, 0, 0, 0))
        ImageDraw.Draw(胶囊).rounded_rectangle([0, 0, 条宽 - 1, 条高 - 1],
                                               radius=条宽 // 2, fill=条)
        card.paste(胶囊, (内缩, 内缩), 胶囊)
        return card.resize((w, h), Image.LANCZOS)

    def palette(self):
        """(卡片底, 描边, 文字色)。深色下必须和窗口底拉开，否则圆角读不出来。"""
        th = self.theme or {}
        dark = str(th.get("bg", "#ffffff")).lower() in (
            "#1e1e1e", "#202020", "#252525", "#2e2e2e", "#1b1b1b")
        if dark:
            return ("#2e2e2e", "#4d4d4d", th.get("fg", "#e8e8e8"))
        return ("#ffffff", "#d8d8d8", th.get("fg", "#222222"))

    def _warm(self):
        try:
            self.card_image(px(240), px(46), "info")
        except Exception:
            pass

    # ---------------------------------------------------------------- 布局
    def _rebuild(self):
        """重排+重画整叠（新建/移除时调；逐帧动画只 move/coords，不走这里）。"""
        if self._closed:
            return
        try:
            self.canvas.delete("all")
        except Exception:
            return
        if not self._items:
            try:
                self.win.withdraw()
            except Exception:
                pass
            return
        总高 = sum(it.h for it in self._items) + self._GAP * (len(self._items) - 1)
        宽 = max(it.w for it in self._items)
        self._pad = px(self._PADB)
        y = 总高
        for it in self._items:
            y -= it.h
            it.draw(self.canvas, 0, y)
            y -= self._GAP
        # ⚠ 尺寸要**自己记下来**，定位时别去问 winfo_width/height：geometry() 是
        # 异步生效的，紧接着读会拿到旧尺寸（实测拿到 325，而实际应是 86），
        # 于是位置算歪 —— 吐司会飘到离窗口底 265px 的地方（用户报"位置异常"）。
        self._size = (int(宽), int(总高 + self._pad))
        self._sync_pos()
        try:
            self.win.deiconify()
            self.win.lift()
        except Exception:
            pass

    def _sync_pos(self):
        """主窗口底边右对齐；主窗口不可见就收起。"""
        if self._closed:
            return
        try:
            if not self._items:
                return
            if not self.master.winfo_viewable() or self.master.state() == "iconic":
                self.win.withdraw()
                return
            mw = self.master.winfo_width()
            mh = self.master.winfo_height()
            if mw <= 1 or mh <= 1:
                return
            尺寸 = getattr(self, "_size", None)
            if not 尺寸:
                return
            w, h = 尺寸
            # 窗口比"整叠"高出底部那点滑动余量：定位时把余量扣掉，
            # 这样视觉上整叠的底边才真的贴着 MARGIN。
            余 = getattr(self, "_pad", 0)
            x = self.master.winfo_rootx() + mw - px(self._MARGIN) - w
            y = self.master.winfo_rooty() + mh - px(self._MARGIN) - (h - 余)
            # 尺寸和位置一次给全，避免"先设尺寸、再按旧尺寸算位置"
            self.win.geometry("%dx%d+%d+%d" % (w, h, int(x), int(y)))
            if not self.win.winfo_ismapped():
                self.win.deiconify()
            self.win.lift()
        except Exception:
            pass

    def _on_master_configure(self, _e=None):
        try:
            self.win.after(30, self._sync_pos)
        except Exception:
            self._sync_pos()

    # ---------------------------------------------------------------- 交互
    def _on_click(self, e):
        """点在哪张卡上就把哪张收掉。"""
        try:
            y = self.canvas.canvasy(e.y)
        except Exception:
            y = e.y
        累 = 0
        for it in reversed(self._items):
            累 += it.h
            if y >= 累 - it.h:
                it.dismiss()
                break
            累 += self._GAP
        return "break"


class _Toast:
    """一条吐司的数据与图元。自己管进度条和三个动画。"""

    _IN_MS = 150
    _OUT_MS = 110
    _MOVE_MS = 110
    _DRAIN_MS = 16

    _FONT = None

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
        self.tag = "t%d" % id(self)
        self._bar = []
        self.h = 0
        self.w = 0
        self._measure()

    def _measure(self):
        try:
            if _Toast._FONT is None:
                _Toast._FONT = tkfont.Font(family="微软雅黑", size=9)
            w = _Toast._FONT.measure(self.text)
        except Exception:
            w = len(self.text) * 12
        self.w = int(min(px(340), max(px(180), w + px(78))))
        self.h = int(px(46))

    # ---------------------------------------------------------------- 画
    def draw(self, c, x, y):
        """把这一条画到共享画布上（x,y 是卡左上角）。"""
        img = self.host.card_image(self.w, self.h, self.level)
        self._card = img
        图, 色 = _LEVELS.get(self.level, _LEVELS["info"])
        _fill, _border, fg = self.host.palette()
        c.create_image(x, y, anchor="nw", image=img, tags=(self.tag,))
        c.create_text(x + px(22), y + self.h // 2, anchor="w", text=图, fill=色,
                      font=("微软雅黑", 10, "bold"), tags=(self.tag,))
        c.create_text(x + px(42), y + self.h // 2, anchor="w", text=self.text,
                      fill=fg, font=("微软雅黑", 9), tags=(self.tag,))
        c.create_text(x + self.w - px(14), y + self.h // 2, anchor="e", text="✕",
                      fill=fg, font=("微软雅黑", 9), tags=(self.tag,))
        # 进度条：3 行 1px 线，按卡片底部圆角逐行内缩（贴着轮廓走）
        半径 = px(10)
        self._bar = []
        for 距底 in (0.5, 1.5, 2.5):
            yy = y + self.h - 距底
            弧 = max(0.0, 半径 - 距底)
            内缩 = 半径 - (max(0.0, 半径 ** 2 - 弧 ** 2)) ** 0.5
            iid = c.create_line(x + 内缩, yy, x + self.w - 内缩, yy, width=1,
                                capstyle="butt", fill=色, tags=(self.tag,))
            self._bar.append((iid, x + 内缩, yy, self.w - 2 * 内缩))
        self._drain()

    # ---------------------------------------------------------------- 进度
    def _drain(self):
        if self.gone:
            return
        c = self.host.canvas
        if self.sticky:
            for iid, _x0, _y, _w in self._bar:
                try:
                    c.itemconfigure(iid, state="hidden")
                except Exception:
                    pass
            return
        try:
            已 = (time.perf_counter() - self.t0) * 1000.0
            余 = max(0.0, 1.0 - 已 / max(1, self.ms))
            for iid, x0, y, w in self._bar:
                c.coords(iid, x0, y, x0 + w * 余, y)
            if 余 <= 0.0:
                self.dismiss()
                return
            self._drain_job = c.after(self._DRAIN_MS, self._drain)
        except Exception:
            self._drain_job = None

    # ---------------------------------------------------------------- 动画
    def _shift(self, dy):
        try:
            self.host.canvas.move(self.tag, 0, dy)
        except Exception:
            pass

    def play_in(self, dy):
        """从下方 dy 处滑到位。"""
        self._shift(dy)
        self._anim(-dy, self._IN_MS)

    def _anim(self, 总位移, 时长):
        self._cancel_move()
        t0 = time.perf_counter()
        已 = 0.0

        def 帧():
            nonlocal 已
            if self.gone:
                return
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / max(1.0, 时长))
            想要 = 总位移 * (1 - (1 - k) ** 3)
            增量 = 想要 - 已
            if abs(增量) >= 0.5:
                self._shift(增量)
                已 = 想要
            if k < 1.0:
                try:
                    self._move_job = self.host.canvas.after(
                        max(1, anim.frame_ms(self.host.win)), 帧)
                except Exception:
                    self._move_job = None
            else:
                self._move_job = None

        帧()

    def _cancel_move(self):
        if self._move_job is not None:
            try:
                self.host.canvas.after_cancel(self._move_job)
            except Exception:
                pass
            self._move_job = None

    # ---------------------------------------------------------------- 回收
    def dismiss(self):
        if self.gone:
            return
        if self._drain_job is not None:
            try:
                self.host.canvas.after_cancel(self._drain_job)
            except Exception:
                pass
            self._drain_job = None
        self._cancel_move()
        t0 = time.perf_counter()
        起 = 0.0

        def 帧():
            nonlocal 起
            if self.gone:
                return
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / self._OUT_MS)
            想要 = px(18) * (k * k)
            增量 = 想要 - 起
            if abs(增量) >= 0.5:
                self._shift(增量)
                起 = 想要
            if k < 1.0:
                try:
                    self._move_job = self.host.canvas.after(
                        max(1, anim.frame_ms(self.host.win)), 帧)
                except Exception:
                    self._move_job = None
            else:
                self.kill()

        帧()

    def kill(self):
        if self.gone:
            return
        self.gone = True
        self._cancel_move()
        if self._drain_job is not None:
            try:
                self.host.canvas.after_cancel(self._drain_job)
            except Exception:
                pass
            self._drain_job = None
        try:
            self.host.canvas.delete(self.tag)
        except Exception:
            pass
        try:
            if self in self.host._items:
                self.host._items.remove(self)
            self.host._rebuild()
        except Exception:
            pass

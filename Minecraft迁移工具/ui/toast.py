# ui/toast.py
"""应用内吐司消息：浮在主窗口右下角的临时提示条。

为什么要它：执行日志要只留"迁移相关"的内容。可原本一次性反馈（保存设置、复制路径、
清空清单、校验结果、目录不存在……）也全塞在日志里，把真正的过程信息淹掉了。
吐司是第三条通道：**纯告知、不阻塞、不进日志、不进 messagebox**。

形态：圆角卡 + 等级色胶囊 + 等级图标 + 一行文案 + 底部进度条 + 右侧 ×。
右下角向上堆叠，最多 4 条，超出挤掉最旧的。

## 为什么整块用 PIL 画、再用分层窗口贴上去

Tk 没有真透明，这条路走过三遍都不彻底（详见 docs/HANDOFF.md 第六节）：

  · 四角填父容器底色 —— 吐司盖在日志/清单上时四角露出一块底色方块；
  · attributes("-transparentcolor", 键色) —— 只有 1 位透明：抗锯齿的半透明边缘
    不会透明，而是和键色（近黑）混成脏边，整条圆角边糊一圈灰；
  · 把 alpha 二值化消掉脏边 —— 圆角变成像素台阶。

于是改成：内容全部用 PIL 画（PIL 有真正的抗锯齿），再用 UpdateLayeredWindow 贴到
一个无边框窗口上（8 位 alpha，见 utils/layered.py）。透明是真透明，圆角是平滑的。

代价：Tk 不能在里面画任何东西，所以文字/图标/进度条都得自己在 PIL 里画 —— 图标是
矢量描的（不依赖字体里有没有 ✓），文字用系统微软雅黑。
"""

import time
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont

from utils import anim
from utils.dpi import px
from utils.layered import LayeredSurface

_LEVELS = {
    "info":  ("i", "#4c9aff"),
    "ok":    ("✓", "#4caf50"),
    "warn":  ("!", "#ffa000"),
    "error": ("×", "#e53935"),
}

_字体缓存 = {}


def _字体(号):
    """按字号取一个中文字体（缓存）。找不到就退回 PIL 内置位图字体。"""
    if 号 in _字体缓存:
        return _字体缓存[号]
    形 = None
    for 名 in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
               r"C:\Windows\Fonts\simhei.ttf"):
        try:
            形 = ImageFont.truetype(名, 号)
            break
        except Exception:
            continue
    if 形 is None:
        try:
            形 = ImageFont.load_default()
        except Exception:
            形 = None
    _字体缓存[号] = 形
    return 形


def _hex(v, 缺="#888888"):
    """'#rrggbb' → (r,g,b)。"""
    try:
        s = str(v)
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
    except Exception:
        return tuple(int(缺[i:i + 2], 16) for i in (1, 3, 5))


class ToastHost:
    """一整叠吐司（一个无边框、逐像素透明的分层窗口）。整个程序只建一个。"""

    _MARGIN = 18
    _GAP = 8
    _PADB = 26            # 底部滑动余量（出场往下滑时别被窗口边界切掉）

    def __init__(self, master, theme, max_items=4):
        self.master = master
        self.theme = dict(theme or {})
        self.max_items = max(1, int(max_items))
        self._items = []
        self._closed = False
        self._master_bind = None
        self._bar_cache = {}
        self._card_cache = {}

        self.win = tk.Toplevel(master)
        try:
            self.win.withdraw()
            self.win.overrideredirect(True)
            # ⚠ 不许被主题遍历刷底色（apply_theme_to_widget_tree 会刷 Toplevel）；
            # 虽然内容不靠底色，但别让人误以为是底色在起作用。
            self.win._skip_theme_walk = True
        except Exception:
            pass
        self.surf = LayeredSurface(self.win)
        try:
            self.win.bind("<Button-1>", self._on_click)
        except Exception:
            pass
        try:
            self._master_bind = master.bind("<Configure>",
                                            self._on_master_configure, add="+")
        except Exception:
            self._master_bind = None
        try:
            master.after(500, self._warm)
        except Exception:
            pass

    # ---------------------------------------------------------------- 生命周期
    def close(self):
        self._closed = True
        self.clear()
        try:
            if self._master_bind is not None:
                self.master.unbind("<Configure>", self._master_bind)
        except Exception:
            pass
        try:
            self.surf.destroy()
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
        self._card_cache.clear()
        self._bar_cache.clear()
        旧 = [(it.text, it.level, it.ms, it.sticky) for it in self._items]
        self.clear()
        for t, lv, ms, st in 旧:
            self.show(t, lv, ms, st)

    # ---------------------------------------------------------------- 对外
    def show(self, text, level="info", ms=3000, sticky=None):
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
        item.dy = px(16)                  # 从下方滑上来
        self._items.append(item)
        while len(self._items) > self.max_items:
            try:
                self._items.pop(0).kill()
            except Exception:
                pass
        self._render()
        item.animate_to(0.0, item._IN_MS)
        item.start()                       # 进度条开始走（走完自动 dismiss）
        return item

    def clear(self):
        for it in list(self._items):
            try:
                it.kill()
            except Exception:
                pass
        self._items = []
        try:
            self.win.withdraw()
        except Exception:
            pass

    # ---------------------------------------------------------------- 配色
    def palette(self):
        """(卡片底, 描边, 文字色)。深色下必须和窗口底拉开，否则圆角读不出来。"""
        th = self.theme or {}
        dark = str(th.get("bg", "#ffffff")).lower() in (
            "#1e1e1e", "#202020", "#252525", "#2e2e2e", "#1b1b1b")
        if dark:
            return ("#2e2e2e", "#4d4d4d", th.get("fg", "#e8e8e8"))
        return ("#ffffff", "#d8d8d8", th.get("fg", "#222222"))

    # ---------------------------------------------------------------- 画一张卡
    def card_pil(self, w, h, level):
        """一张卡的底图（RADIUS 圆角 + 描边 + 左侧胶囊 + 图标 + 文案 + ×）。

        结果按 (w,h,level,配色) 缓存 —— 一条吐司从出现到消失只渲染一次。
        """
        键 = (int(w), int(h), level, self.palette())
        图 = self._card_cache.get(键)
        if 图 is not None:
            return 图.copy()
        S = 2                                   # 2 倍超采样，缩小后就是抗锯齿
        底, 边, 字色 = self.palette()
        条 = _LEVELS.get(level, _LEVELS["info"])[1]
        W, H = int(w) * S, int(h) * S
        图 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        画 = ImageDraw.Draw(图)
        半径 = px(10) * S
        画.rounded_rectangle([0, 0, W - 1, H - 1], radius=半径, fill=底,
                            outline=边, width=S)
        # 左侧等级色：内缩胶囊（两端自己圆角；4px 宽的圆角矩形会被 PIL 夹成方头）
        内缩 = px(3) * S
        条宽 = max(2, px(5) * S)
        条高 = max(1, H - 内缩 * 2)
        画.rounded_rectangle([内缩, 内缩, 内缩 + 条宽 - 1, 内缩 + 条高 - 1],
                            radius=max(1, 条宽 // 2), fill=条)
        图 = 图.resize((int(w), int(h)), Image.LANCZOS)
        画 = ImageDraw.Draw(图)
        # 图标：矢量描，不依赖字体里有没有 ✓ 这类字符
        cx, cy = px(22), int(h) // 2
        r = max(2.0, px(4))
        lw = max(1, int(px(2)))
        if level == "ok":
            画.line([(cx - r, cy), (cx - px(1), cy + r * 0.7),
                     (cx + r, cy - r * 0.9)], fill=条, width=lw, joint="curve")
        elif level == "error":
            画.line([(cx - r, cy - r), (cx + r, cy + r)], fill=条, width=lw)
            画.line([(cx - r, cy + r), (cx + r, cy - r)], fill=条, width=lw)
        elif level == "warn":
            画.line([(cx, cy - r), (cx, cy + r * 0.25)], fill=条, width=lw)
            画.ellipse([cx - lw // 2, cy + r * 0.6, cx + lw // 2 + 1, cy + r * 0.6 + lw],
                       fill=条)
        else:                                   # info
            画.ellipse([cx - lw // 2, cy - r - 1, cx + lw // 2 + 1, cy - r + lw],
                       fill=条)
            画.line([(cx, cy - r * 0.35), (cx, cy + r)], fill=条, width=lw)
        # 文案
        形 = _字体(max(9, int(px(9))))
        if 形 is not None:
            try:
                画.text((px(42), cy), str(self._当前文本 or ""), font=形,
                        fill=字色, anchor="lm")
            except Exception:
                pass
        # 右侧 ×
        ex, ey = int(w) - px(14), cy
        q = max(1.5, px(3))
        画.line([(ex - q, ey - q), (ex + q, ey + q)], fill=字色, width=1)
        画.line([(ex - q, ey + q), (ex + q, ey - q)], fill=字色, width=1)
        self._card_cache[键] = 图.copy()
        return 图

    def bar_strip(self, w, h, level):
        """底部进度条整条（铺满宽度），按宽度缓存；画的时候只横向裁到当前进度。"""
        键 = (int(w), int(h), level, self.palette())
        图 = self._bar_cache.get(键)
        if 图 is not None:
            return 图
        S = 2
        条 = _LEVELS.get(level, _LEVELS["info"])[1]
        W, H, bh = int(w) * S, int(h) * S, max(1, px(4)) * S
        整 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        画 = ImageDraw.Draw(整)
        # 用与卡片同半径的圆角矩形打底，只取最底部 bh 行 → 进度条跟着卡片圆角走
        画.rounded_rectangle([0, 0, W - 1, H - 1], radius=px(10) * S, fill=条)
        条图 = 整.crop((0, H - bh, W, H)).resize((int(w), max(1, bh // S)),
                                               Image.LANCZOS)
        self._bar_cache[键] = 条图
        return 条图

    # ---------------------------------------------------------------- 排版/上传
    def _render(self):
        """重排整叠并贴到窗口上（内容变化时才调；挪位置只走 surf.move）。"""
        if self._closed:
            return
        if not self._items:
            try:
                self.win.withdraw()
            except Exception:
                pass
            return
        try:
            mw = self.master.winfo_width()
            mh = self.master.winfo_height()
            rx = self.master.winfo_rootx()
            ry = self.master.winfo_rooty()
        except Exception:
            return
        if mw <= 1 or mh <= 1:
            return
        总高 = sum(it.h for it in self._items) + self._GAP * (len(self._items) - 1)
        宽 = max(it.w for it in self._items)
        图 = Image.new("RGBA", (int(宽), int(总高 + px(self._PADB))), (0, 0, 0, 0))
        y = 总高
        for it in self._items:
            y -= it.h
            it.compose(图, 0, int(y + getattr(it, "dy", 0)))
            y -= self._GAP
        x = rx + mw - px(self._MARGIN) - int(宽)
        yy = ry + mh - px(self._MARGIN) - 总高
        # 先让窗口显示出来再上传：withdraw 状态下的分层窗口收不到内容
        try:
            if not self.win.winfo_ismapped():
                self.win.deiconify()
        except Exception:
            pass
        if not self.surf.upload(图, x, yy):
            return
        try:
            self.win.lift()
        except Exception:
            pass
        self._last_pos = (x, yy)

    def _on_master_configure(self, _e=None):
        try:
            self.win.after(20, self._render)
        except Exception:
            pass

    # ---------------------------------------------------------------- 交互
    def _on_click(self, e):
        y = 0
        for it in self._items:
            y += it.h
            if e.y < y:
                it.dismiss()
                return "break"
            y += self._GAP
        return "break"

    def _warm(self):
        try:
            t = _Toast(self, "预热", "info", 1000, False)
            t.base()
        except Exception:
            pass


class _Toast:
    """一条吐司：自己渲染、自己管进度条和动画。"""

    _IN_MS = 150
    _OUT_MS = 120
    _DRAIN_MS = 16

    def __init__(self, host, text, level, ms, sticky):
        self.host = host
        self.text = text
        self.level = level
        self.ms = ms
        self.sticky = sticky
        self.gone = False
        self.t0 = time.perf_counter()
        self.dy = 0.0
        self._drain_job = None
        self._anim_job = None
        self._base = None
        self.h = int(px(46))
        self.w = self._量宽()

    def _量宽(self):
        try:
            形 = _字体(max(9, int(px(9))))
            w = 形.getlength(self.text) if 形 is not None else len(self.text) * 12
        except Exception:
            w = len(self.text) * 12
        return int(min(px(340), max(px(180), w + px(78))))

    # ---------------------------------------------------------------- 渲染
    def base(self):
        """卡片底图（不含进度条），带缓存。"""
        if self._base is None:
            self.host._当前文本 = self.text
            try:
                self._base = self.host.card_pil(self.w, self.h, self.level)
            finally:
                self.host._当前文本 = ""
        return self._base

    def compose(self, 画布, x, y):
        """把这一条贴到整叠图上（画布是 RGBA）。"""
        图 = self.base()
        画布.alpha_composite(图, (int(x), int(y)))
        if self.sticky:
            return
        try:
            余 = 1.0 - (time.perf_counter() - self.t0) * 1000.0 / max(1, self.ms)
        except Exception:
            余 = 1.0
        if 余 <= 0.0:
            return
        条 = self.host.bar_strip(self.w, self.h, self.level)
        宽 = max(1, int(条.width * min(1.0, 余)))
        段 = 条.crop((0, 0, 宽, 条.height))
        画布.alpha_composite(段, (int(x), int(y) + self.h - 条.height))

    # ---------------------------------------------------------------- 进度
    def _drain(self):
        if self.gone:
            return
        if self.sticky:
            return
        已 = (time.perf_counter() - self.t0) * 1000.0
        if 已 >= self.ms:
            self.dismiss()
            return
        self.host._render()
        try:
            self._drain_job = self.host.win.after(self._DRAIN_MS, self._drain)
        except Exception:
            self._drain_job = None

    def start(self):
        self._drain()

    # ---------------------------------------------------------------- 动画
    def animate_to(self, 目标, 时长):
        self._cancel_anim()
        起 = float(self.dy)
        t0 = time.perf_counter()

        def 帧():
            if self.gone:
                return
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / max(1.0, 时长))
            self.dy = 起 + (目标 - 起) * (1 - (1 - k) ** 3)
            self.host._render()
            if k < 1.0:
                try:
                    self._anim_job = self.host.win.after(
                        max(1, anim.frame_ms(self.host.win)), 帧)
                except Exception:
                    self._anim_job = None
            else:
                self.dy = 目标
                self._anim_job = None

        帧()

    def _cancel_anim(self):
        if self._anim_job is not None:
            try:
                self.host.win.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    # ---------------------------------------------------------------- 回收
    def dismiss(self):
        if self.gone:
            return
        if self._drain_job is not None:
            try:
                self.host.win.after_cancel(self._drain_job)
            except Exception:
                pass
            self._drain_job = None
        self._cancel_anim()
        起 = float(self.dy)
        t0 = time.perf_counter()

        def 帧():
            if self.gone:
                return
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / self._OUT_MS)
            self.dy = 起 + px(20) * (k * k)
            self.host._render()
            if k < 1.0:
                try:
                    self._anim_job = self.host.win.after(
                        max(1, anim.frame_ms(self.host.win)), 帧)
                except Exception:
                    self._anim_job = None
            else:
                self.kill()

        帧()

    def kill(self):
        if self.gone:
            return
        self.gone = True
        self._cancel_anim()
        if self._drain_job is not None:
            try:
                self.host.win.after_cancel(self._drain_job)
            except Exception:
                pass
            self._drain_job = None
        try:
            if self in self.host._items:
                self.host._items.remove(self)
        except Exception:
            pass
        self.host._render()

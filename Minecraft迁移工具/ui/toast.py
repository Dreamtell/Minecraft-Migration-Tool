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


_字体缓存 = {}
_表情缓存 = {}

# 表情符号的码位范围。微软雅黑里**没有 emoji 字形**，混在一起画会变成空白或豆腐块；
# 这些字符要单独用 Segoe UI Emoji 画（而且要 embedded_color=True，否则是单色剪影）。
_表情区段 = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF))
_表情单字 = {0x2139, 0x2328, 0x2705, 0x274C, 0x2764, 0x203C, 0xFE0F, 0x200D}


def _是表情(ch):
    cp = ord(ch)
    if cp in _表情单字:
        return True
    for a, b in _表情区段:
        if a <= cp <= b:
            return True
    return False


def _表情字体(号):
    if 号 in _表情缓存:
        return _表情缓存[号]
    形 = None
    for 名 in (r"C:\Windows\Fonts\seguiemj.ttf",):
        try:
            形 = ImageFont.truetype(名, 号)
            break
        except Exception:
            continue
    _表情缓存[号] = 形
    return 形


def _分段(文本):
    """把文本切成"表情段 / 文字段"，相邻同类合并。"""
    段 = []
    for ch in 文本:
        e = _是表情(ch)
        if 段 and 段[-1][1] == e:
            段[-1][0].append(ch)
        else:
            段.append([[ch], e])
    return [("".join(t), e) for t, e in 段]


def _量文本(文本, 号):
    """按字体实际测宽（表情和文字各用各的字体）。"""
    宽 = 0.0
    for t, e in _分段(文本):
        形 = _表情字体(号) if e else _字体(号)
        try:
            宽 += 形.getlength(t) if 形 is not None else len(t) * 号
        except Exception:
            宽 += len(t) * 号
    return 宽


def _截断(文本, 号, 最大宽):
    """超宽就截断并补省略号（以前没有截断，长文案会直接画到卡片外面去）。"""
    if 最大宽 <= 0 or _量文本(文本, 号) <= 最大宽:
        return 文本
    省 = "…"
    省宽 = _量文本(省, 号)
    低, 高 = 0, len(文本)
    while 低 < 高:
        中 = (低 + 高 + 1) // 2
        if _量文本(文本[:中], 号) + 省宽 <= 最大宽:
            低 = 中
        else:
            高 = 中 - 1
    return 文本[:低] + 省


def _画文本(画, x, y, 文本, 字色, 号):
    """逐段画：文字用雅黑、表情用 Segoe UI Emoji（带 embedded_color）。"""
    for t, e in _分段(文本):
        形 = _表情字体(号) if e else _字体(号)
        if 形 is None:
            continue
        try:
            if e:
                画.text((x, y), t, font=形, fill=字色, anchor="lm",
                        embedded_color=True)
            else:
                画.text((x, y), t, font=形, fill=字色, anchor="lm")
        except Exception:
            try:
                画.text((x, y), t, font=形, fill=字色, anchor="lm")
            except Exception:
                pass
        try:
            x += 形.getlength(t)
        except Exception:
            x += len(t) * 号


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
        self._tick_job = None
        self._dirty = True
        self._底 = None
        self._总高 = 0
        self._last_pos = (0, 0)
        self._当前文本 = ""
        self._指纹 = None
        self._帧字节 = None
        self._帧图 = None
        self._hr = False           # 是否持有 1ms 定时器精度（动画期间）
        self._底字节 = None
        self._底键 = None
        self._条缓存 = {}          # (w,h,level) → (预乘字节, 条高)

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
        # 主窗口销毁 → 吐司必须跟着走（否则它是个独立顶层窗口，会孤零零留在桌面上）
        try:
            master.bind("<Destroy>", self._on_master_destroy, add="+")
        except Exception:
            pass
        try:
            master.after(200, self._warm)
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
    def palette(self):
        """(卡片底, 描边, 文字色)。深色下必须和窗口底拉开，否则圆角读不出来。"""
        th = self.theme or {}
        dark = str(th.get("bg", "#ffffff")).lower() in (
            "#1e1e1e", "#202020", "#252525", "#2e2e2e", "#1b1b1b")
        if dark:
            return ("#2e2e2e", "#4d4d4d", th.get("fg", "#e8e8e8"))
        return ("#ffffff", "#d8d8d8", th.get("fg", "#222222"))

    # ---------------------------------------------------------------- 画一张卡
    def card_pil(self, w, h, level, 文案=""):
        """一张卡的底图（圆角 + 描边 + 左侧胶囊 + 图标 + 文案 + ×）。

        ⚠ 缓存键**必须带文案**：只按 (w,h,level) 缓存时，同宽的两条不同文案会
        互相顶用 —— 预热那张"预热"就曾顶掉了别人的卡片（截图里第三条显示成"预热"）。
        """
        键 = (int(w), int(h), level, self.palette(), str(文案))
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
        # 文案：按实际像素测宽**截断**（以前长文案会画到卡片外面），
        # 表情段单独换字体（雅黑没有 emoji 字形）
        号 = max(9, int(px(9)))
        可用 = max(px(16), int(w) - px(42) - px(26))
        _画文本(画, px(42), cy, _截断(str(文案 or ""), 号, 可用), 字色, 号)
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
        self._items.append(item)
        while len(self._items) > self.max_items:
            try:
                self._items.pop(0).kill()
            except Exception:
                pass
        self._layout(重排已有=True)      # 旧的往上让位（带过渡）
        item.dy = px(16)                 # 新的从下方滑上来
        item.animate(0.0, _Toast._IN_MS)
        self._dirty = True
        self._ensure_tick()
        return item

    def clear(self):
        for it in list(self._items):
            try:
                it.kill()
            except Exception:
                pass
        self._items = []
        self._dirty = True
        self._stop_tick()
        try:
            self.win.withdraw()
        except Exception:
            pass

    # ---------------------------------------------------------------- 排版/渲染
    def _layout(self, 重排已有=False):
        """算每条的目标 y（自下而上），并让已有条目平滑让位。"""
        旧 = {id(it): it._y for it in self._items} if 重排已有 else {}
        总高 = sum(it.h for it in self._items) + self._GAP * (len(self._items) - 1)
        y = 总高
        for it in self._items:
            y -= it.h
            it._y = y
            y -= self._GAP
        self._总高 = 总高
        for it in self._items:
            老 = 旧.get(id(it))
            if 老 is None or it._leaving:
                continue
            差 = 老 - it._y
            if abs(差) >= 0.5:
                it.dy += 差
                it.animate(0.0, _Toast._MOVE_MS)
        self._dirty = True

    def _ensure_tick(self):
        if self._tick_job is None and self._items and not self._closed:
            try:
                self._tick_job = self.win.after(16, self._tick)
            except Exception:
                self._tick_job = None

    def _stop_tick(self):
        if getattr(self, "_hr", False):
            try:
                anim.release()
            except Exception:
                pass
            self._hr = False
        if self._tick_job is not None:
            try:
                self.win.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _tick(self):
        """**唯一**的渲染循环：整栈每帧最多画一次（以前是每条吐司各跑一个
        16ms 定时器、各自重画整栈 + UpdateLayeredWindow，4 条就是每帧画 4 次，
        频繁弹出时直接把主线程压满 —— 用户报"进度条卡 / 整个程序卡"）。"""
        self._tick_job = None
        if self._closed:
            return
        现在 = time.perf_counter()
        动画中 = False
        for it in list(self._items):
            if it._advance(现在):
                动画中 = True
        self._compose()
        if not self._items:
            if getattr(self, "_hr", False):
                try:
                    anim.release()
                except Exception:
                    pass
                self._hr = False
            try:
                self.win.withdraw()
            except Exception:
                pass
            return
        # 动画中**跟随显示器刷新率**（本机 240Hz → 4ms）；只有进度条在走时 16ms。
        # 别再写死 8ms：那等于把 240Hz 屏砍成 125fps，上移/收回一眼就能看出是"挪"不是"滑"。
        #
        # ⚠ 光排 after(4) 没用：Tk 的 after 会被量化到 **15.6ms 系统节拍**，实测 p90 直接
        # 从 5ms 掉到 17.5ms（"偶尔顿一下"就是这个）。必须 hold 住 1ms 定时器精度。
        # 动画**和**进度条阶段都要持有：只给动画 hold 的话，动画一结束精度就还回去，
        # 之后 after(16) 又被量化成 15.6/31ms —— 实测 p90 直接 30ms，看着就是"一下一下挪"。
        # 吐司总共只活两三秒，这点代价远小于观感损失。
        有进度在走 = any(not it.sticky for it in self._items)
        if (动画中 or 有进度在走) and not self._hr:
            anim.hold()
            self._hr = True
        elif (not 动画中) and (not 有进度在走) and self._hr:
            # 只剩"粘性"吐司（不自动消失那种）时不必再占着 1ms 精度：
            # 那会让整机定时器精度一直偏高（费电），而它已经静止了。
            anim.release()
            self._hr = False
        间隔 = anim.frame_ms(self.win) if 动画中 else 8
        try:
            self._tick_job = self.win.after(max(4, int(间隔)), self._tick)
        except Exception:
            self._tick_job = None

    def _compose(self):
        """铺底图 + 贴进度条 + 上传。底图（卡片本身）只在变化时重建。"""
        if self._closed or not self._items:
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
        # ⚠ 主窗口收进托盘（root 只是 withdraw）或最小化时，**必须把吐司也收起来**：
        # 它是个独立顶层窗口，主窗口藏了它不会自动跟着藏，会一直飘在桌面上
        # （用户报"主界面都退出了，消息还在"）。
        if not self._master_可见():
            try:
                self.win.withdraw()
            except Exception:
                pass
            return
        宽 = max(it.w for it in self._items)
        高 = int(self._总高 + px(self._PADB))
        # 内容真变了才重画+上传：进度条宽度是按整像素算的，3 秒的条一秒才变十几像素，
        # 不必每帧都贴（否则同一张图被反复预乘 + UpdateLayeredWindow，白烧 CPU）
        指纹 = (self._dirty, self._底 is None,
                tuple((round(it._y + it.dy, 1), it.w) for it in self._items),
                tuple(int(it.w * it.progress()) if not (it.sticky or it._leaving) else -1
                      for it in self._items))
        if (not self._dirty) and self._底 is not None and 指纹 == self._指纹:
            return
        self._指纹 = 指纹
        x = rx + mw - px(self._MARGIN) - int(宽)
        yy = ry + mh - px(self._MARGIN) - int(self._总高)
        # 静 = 全部卡片都不在动。此时底图（卡片部分）可以**预乘一次缓存**，
        # 每帧只把进度条那几行拼进字节里 —— 省掉整图预乘（0.4ms/次）。
        # ⚠ 别把动画中的 dy 烘焙进这张缓存：那样卡片会冻在旧位置，只有进度条在动
        # （表现就是卡片错位/缺卡/底部多出一条游离的进度条，实测截过）。
        静 = all(abs(it.dy) < 0.5 for it in self._items)
        if 静:
            键 = (int(宽), int(高), tuple(id(it) for it in self._items),
                  tuple(it._y for it in self._items), self.palette())
            if self._底键 != 键 or self._底字节 is None:
                base = Image.new("RGBA", (int(宽), int(高)), (0, 0, 0, 0))
                for it in self._items:
                    it.compose(base, 0, int(it._y))
                self._底字节 = LayeredSurface.premult(base)
                self._底键 = 键
                self._dirty = False
            # ⚠ 缓冲**复用**，别每帧新建 bytearray：250 帧/秒 × 81KB ≈ 20MB/s 的分配，
            # 会给 GC 制造压力，偶发的十几毫秒停顿多半来自这里（观感就是"卡一下"）。
            总字节 = 高 * int(宽) * 4
            if self._帧字节 is None or len(self._帧字节) != 总字节:
                self._帧字节 = bytearray(self._底字节)
            else:
                self._帧字节[:] = self._底字节      # 就地 memcpy
            buf = self._帧字节
            for it in self._items:
                if it.sticky or it._leaving:
                    continue
                分 = it.progress()
                if 分 <= 0.0:
                    continue
                条, bh = self._条预乘(it.w, it.h, it.level)
                # **亚像素**进度：整像素部分 memcpy，边界那一列按小数部分做 alpha 混合。
                # 只按整像素推进时，3 秒的条每帧走 0.3px，会有三分之一的帧看着没动 ——
                # 这列混合把"没动"变成"边缘淡了一点"，眼睛就读成连续运动。
                浮 = it.w * min(1.0, 分)
                宽2 = max(1, int(浮))
                余 = 浮 - int(浮)
                行 = int(it._y) + it.h - bh
                for r in range(bh):
                    源 = r * it.w * 4
                    目 = ((行 + r) * int(宽)) * 4
                    buf[目:目 + 宽2 * 4] = 条[源:源 + 宽2 * 4]
                    if 余 > 0.02 and 宽2 < int(it.w):
                        目2 = 目 + 宽2 * 4
                        源2 = 源 + 宽2 * 4
                        for k in range(3):
                            底v = buf[目2 + k]
                            buf[目2 + k] = int(条[源2 + k] * 余 + 底v * (1.0 - 余))
            图 = None
        else:
            self._底字节 = None
            self._底键 = None
            # 同样复用：每帧新建一张 RGBA 也是白给的分配量
            if self._帧图 is None or self._帧图.size != (int(宽), int(高)):
                self._帧图 = Image.new("RGBA", (int(宽), int(高)), (0, 0, 0, 0))
            else:
                self._帧图.paste((0, 0, 0, 0), (0, 0, int(宽), int(高)))
            图 = self._帧图
            for it in self._items:
                it.compose(图, 0, int(it._y + it.dy))
            for it in self._items:
                if it.sticky or it._leaving:
                    continue
                条 = self.bar_strip(it.w, it.h, it.level)
                分 = it.progress()
                if 分 <= 0.0:
                    continue
                宽2 = max(1, int(条.width * min(1.0, 分)))
                图.alpha_composite(条.crop((0, 0, 宽2, 条.height)),
                                   (0, int(it._y + it.dy) + it.h - 条.height))
        try:
            if not self.win.winfo_ismapped():
                self.win.deiconify()
        except Exception:
            pass
        ok = (self.surf.upload(图, x, yy) if 图 is not None
              else self.surf.upload_raw(buf, int(宽), int(高), x, yy))
        if ok:
            self._last_pos = (x, yy)
        try:
            self.win.lift()
        except Exception:
            pass

    def _条预乘(self, w, h, level):
        """进度条整条的**预乘字节**（按 w/h/level 缓存），供拼帧时逐行拷贝。"""
        键 = (int(w), int(h), level, self.palette())
        项 = self._条缓存.get(键)
        if 项 is None:
            条 = self.bar_strip(w, h, level)
            项 = (LayeredSurface.premult(条), 条.height)
            self._条缓存[键] = 项
        return 项

    def _on_master_configure(self, _e=None):
        try:
            self.win.after(20, self._ensure_tick)
        except Exception:
            pass

    def _on_master_destroy(self, e=None):
        """只有主窗口自己被销毁才收尾（子控件销毁也会冒泡到这里）。"""
        try:
            if e is not None and getattr(e, "widget", None) is not self.master:
                return
        except Exception:
            pass
        self.close()

    def _master_可见(self):
        """主窗口可见吗？收进托盘 / 最小化 / 已销毁都不算。"""
        try:
            if not self.master.winfo_exists():
                return False
            if self.master.state() == "iconic":
                return False
            return bool(self.master.winfo_viewable())
        except Exception:
            return False

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
        """预热：把常用卡片图和字体缓存先建好，并**让窗口先映射一次**。

        ⚠ 第一次 deiconify 一个新建的 Toplevel 要 20~30ms（建窗口 + 合成），
        正好落在第一张吐司出现的那一帧上 —— 用户感觉就是"弹出来会顿一下"。
        这里先贴一张 1x1 全透明图把窗口映射好，之后每次只是改内容。
        """
        try:
            t = _Toast(self, "预热", "info", 1000, False)
            t.base()
            for 级 in ("ok", "warn", "error"):
                self.card_pil(t.w, t.h, 级, "预热")
        except Exception:
            pass
        try:
            from PIL import Image as _I
            self.surf.upload(_I.new("RGBA", (1, 1), (0, 0, 0, 0)),
                             self.master.winfo_rootx(), self.master.winfo_rooty())
            self.win.deiconify()
        except Exception:
            pass


class _Toast:
    """一条吐司的数据。渲染和动画都由宿主那一个循环推进。"""

    _IN_MS = 150
    _OUT_MS = 120
    _MOVE_MS = 110

    def __init__(self, host, text, level, ms, sticky):
        self.host = host
        self.text = text
        self.level = level
        self.ms = ms
        self.sticky = sticky
        self.gone = False
        self.t0 = time.perf_counter()
        self.dy = 0.0
        self._y = 0.0
        self._leaving = False
        self._anim = None            # [起, 目标, t0, 时长, 结束后回调]
        self._base = None
        self.h = int(px(46))
        self.w = self._量宽()

    def _量宽(self):
        try:
            w = _量文本(self.text, max(9, int(px(9))))
        except Exception:
            w = len(self.text) * 12
        return int(min(px(340), max(px(180), w + px(78))))

    # ---------------------------------------------------------------- 渲染
    def base(self):
        """卡片底图（不含进度条），带缓存。"""
        if self._base is None:
            self._base = self.host.card_pil(self.w, self.h, self.level, self.text)
        return self._base

    def compose(self, 画布, x, y):
        画布.alpha_composite(self.base(), (int(x), int(y)))

    def progress(self):
        if self.sticky or self.ms <= 0:
            return 1.0 if self.sticky else 0.0
        try:
            return max(0.0, 1.0 - (time.perf_counter() - self.t0) * 1000.0 / self.ms)
        except Exception:
            return 1.0

    # ---------------------------------------------------------------- 动画/计时
    def animate(self, 目标, 时长, 结束后=None):
        self._anim = [float(self.dy), float(目标), time.perf_counter(),
                      max(1.0, float(时长)), 结束后]

    def _advance(self, 现在):
        """推进动画/判断到点。返回是否仍在动画中（宿主据此决定下一帧间隔）。"""
        if self.gone:
            return False
        if self._anim is not None:
            起, 目标, t0, 时长, 回调 = self._anim
            k = min(1.0, (现在 - t0) * 1000.0 / 时长)
            self.dy = 起 + (目标 - 起) * (1 - (1 - k) ** 3)
            if k >= 1.0:
                self.dy = 目标
                self._anim = None
                if 回调 is not None:
                    try:
                        回调()
                    except Exception:
                        pass
            else:
                return True
        if not self.sticky and not self._leaving:
            if (现在 - self.t0) * 1000.0 >= self.ms:
                self.dismiss()
                return True
            return False
        return self._anim is not None

    # ---------------------------------------------------------------- 回收
    def dismiss(self):
        if self.gone or self._leaving:
            return
        self._leaving = True
        self.animate(px(20), self._OUT_MS, 结束后=self.kill)

    def kill(self):
        if self.gone:
            return
        self.gone = True
        self._anim = None
        try:
            if self in self.host._items:
                self.host._items.remove(self)
        except Exception:
            pass
        self.host._layout()
        self.host._ensure_tick()

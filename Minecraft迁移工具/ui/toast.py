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
        self._零字节 = b""
        self._帧图 = None
        self._hr = False           # 是否持有 1ms 定时器精度（动画期间）
        self._上帧 = None          # 上一帧的时刻（算卡顿用）
        self._上次报卡顿 = 0.0
        self._卡顿 = []            # [(时刻, 推迟毫秒)]，最近 40 条
        self.stall_log = None      # 可选：卡顿汇报钩子（主窗口把它接到执行日志）
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
        # 入场用**淡入**（不是纵向位移）：见 _Toast.fade_to 的说明
        item.淡 = 0.0
        while len(self._items) > self.max_items:
            try:
                self._items.pop(0).kill()      # kill 内部会 _layout(重排已有=True)
            except Exception:
                pass
        self._layout(重排已有=True)      # 旧的往上让位（带过渡）
        item.fade_to(1.0, _Toast._IN_MS)
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
        # ⚠ 必须 **reversed**：self._items 是"旧→新"，而堆叠要求**最新的贴底**、
        # 旧的往上顶。按列表顺序排的话最旧的会占最下面那格，于是有新消息时
        # 旧的那条被迫**往下滑**去补位 —— 用户看到的就是"进度条卡一下还回弹"。
        for it in reversed(self._items):
            y -= it.h
            it._y = y
            y -= self._GAP
        # ⚠ 让位动画的补偿必须**算上窗口原点的位移**：整叠是"底部对齐主窗口"的，
        # 多一条吐司时窗口顶部会整体上移 (新总高 - 旧总高)，旧卡片的"窗口内 y"却没变。
        # 只补 y 差值的话它会被窗口带着**瞬移**上去（看着就是"跳一下/回弹"），
        # 补上 Δ窗口 它才会从原来的屏幕位置平滑滑到新位置。
        Δ窗口 = 总高 - getattr(self, "_上次总高", 总高)
        for it in self._items:
            老 = 旧.get(id(it))
            if 老 is None or it._leaving:
                continue
            差 = (老 - it._y) + Δ窗口
            if abs(差) >= 0.5:
                it.dy += 差
                it.animate(0.0, _Toast._MOVE_MS)
        self._总高 = 总高
        self._上次总高 = 总高
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

    def _记卡顿(self, 毫秒):
        """记一次"这一帧被推迟了多久"（限流 2 秒一条，避免刷屏）。"""
        try:
            现在 = time.perf_counter()
            if 现在 - getattr(self, "_上次报卡顿", 0.0) < 2.0:
                return
            self._上次报卡顿 = 现在
            self._卡顿.append((round(现在, 1), round(毫秒, 1)))
            if len(self._卡顿) > 40:
                del self._卡顿[:-40]
            钩 = getattr(self, "stall_log", None)
            if 钩 is not None:
                try:
                    钩("⏱ 吐司帧被推迟 %.0fms（主线程当时在忙别的事）" % 毫秒)
                except Exception:
                    pass
        except Exception:
            pass

    def _tick(self):
        """**唯一**的渲染循环：整栈每帧最多画一次（以前是每条吐司各跑一个
        16ms 定时器、各自重画整栈 + UpdateLayeredWindow，4 条就是每帧画 4 次，
        频繁弹出时直接把主线程压满 —— 用户报"进度条卡 / 整个程序卡"）。"""
        self._tick_job = None
        if self._closed:
            return
        现在 = time.perf_counter()
        # 卡顿记录：这一帧比预期晚到多久。正常情况下每帧 4~8ms，一旦主线程被别的事占住
        # （写日志、刷列表、扫目录），这里就会记到几十~上百毫秒 —— 这是判断"吐司自己慢"
        # 还是"被主线程挤"的唯一直接证据。记录会通过 stall_log 钩子进执行日志。
        距上帧 = 0.0
        if self._上帧 is not None:
            距上帧 = (现在 - self._上帧) * 1000.0
            if 距上帧 > 60.0:
                self._记卡顿(距上帧)
        self._上帧 = 现在
        动画中 = False
        for it in list(self._items):
            if it._advance(现在):
                动画中 = True
        try:
            self._compose()
        except Exception:
            # ⚠ 这一层不能少：_compose 抛异常的话，下面的重排代码就执行不到，
            # 整个 tick 链就此断掉 —— 吐司会永久冻在屏幕上（实测踩过一次）。
            pass
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
        # 内容真变了才重画+上传。
        # ⚠ 进度条项**必须按 1/8 像素**量化，不能按整像素：按整像素的话，更新只发生在
        # "整像素变了"的那一帧，我加的亚像素混合几乎永远不执行 —— 进度条实际是 1px
        # 硬跳，还带着随机小数，看着就是"一格一格地卡"（用户反复报的就是这个）。
        指纹 = (self._dirty, self._底 is None,
                tuple((round(it._y + it.dy, 1), it.w) for it in self._items),
                tuple(int(it.w * it.progress() * 8)
                      if not (it.sticky or it._leaving) else -1
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
            # 动画期间**也不走 PIL 合成**：卡片的预乘字节按行 memcpy 进帧缓冲即可。
            # 实测 4 条 425px 宽的卡，原来每帧 "4 次 alpha_composite + 整图预乘" 要
            # p90 2.9ms / max 9.6ms，帧率掉到 10~20fps —— 这就是"短消息很顺、长消息卡"
            # 的原因（我早先只用 225px 宽的短卡测，所以没暴露出来）。
            总字节 = 高 * int(宽) * 4
            # ⚠ _零字节 可能不存在：稳态分支只分配 _帧字节（它从 _底字节 拷），
            # 于是"先静止、再来新消息"时这里会 AttributeError 崩掉
            # （真程序里点开几条等它们静止、再触发一条就会崩）。
            if (self._帧字节 is None or len(self._帧字节) != 总字节
                    or len(getattr(self, "_零字节", b"")) != 总字节):
                self._帧字节 = bytearray(总字节)
                self._零字节 = bytes(总字节)
            else:
                self._帧字节[:] = self._零字节
            buf = self._帧字节
            # 顺序：旧→新，**新卡写在后（也就是在前）** —— 让位时它从下面滑进来，
            # 压住上面那条的下沿是对的（那是"滑入"的正常遮挡）。
            # 圆角处的透明像素靠 _贴卡 的"只写不透明段"处理，不会留下洞。
            for it in self._items:
                self._贴卡(buf, it, int(round(it._y + it.dy)), int(宽), 高)
            for it in self._items:
                self._贴条(buf, it, int(round(it._y + it.dy)), int(宽), 高)
            图 = None
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

    def _卡字节(self, it):
        """一条吐司卡片的**预乘字节** + 每行的不透明区间（每张卡只算一次）。"""
        字 = getattr(it, "_预字节", None)
        if 字 is None:
            字 = LayeredSurface.premult(it.base())
            it._预字节 = 字
            # 每行圆角处的不透明内缩量。按行只写不透明段，纯 memcpy 才不会把
            # 圆角的透明像素写成"洞"（两个卡片重叠时那会直接透出桌面）。
            半 = max(1.0, px(10))
            表 = []
            for r in range(it.h):
                内 = 0.0
                if r < 半:
                    弧 = 半 - r
                    内 = 半 - (max(0.0, 半 * 半 - 弧 * 弧)) ** 0.5
                elif it.h - 1 - r < 半:
                    弧 = 半 - (it.h - 1 - r)
                    内 = 半 - (max(0.0, 半 * 半 - 弧 * 弧)) ** 0.5
                表.append(int(内) + 1)          # +1：宁可少写一列，也不留下洞
            it._内缩表 = 表
        return 字

    def _淡表(self, it, 倍):
        """按不透明度取一张 256 字节的换算表（缓存），给 bytes.translate 用。

        预乘字节四个通道同乘一个系数就是正确的"整体变淡"，而 translate 是 C 实现，
        96KB 只要 0.05ms —— 比每帧用 PIL 重新算 alpha 便宜得多。
        """
        阶 = max(1, min(31, int(round(倍 * 31))))
        表缓 = getattr(it, "_淡表缓存", None)
        if 表缓 is None:
            表缓 = it._淡表缓存 = {}
        表 = 表缓.get(阶)
        if 表 is None:
            f = 阶 / 31.0
            表 = bytes(int(v * f) for v in range(256))
            表缓[阶] = 表
        return 表

    def _贴卡(self, buf, it, 行, 宽, 高):
        """按行把卡片拷进帧缓冲：只写这一行的**不透明段**（圆角处收窄）。"""
        倍 = float(getattr(it, "淡", 1.0))
        if 倍 <= 0.02:
            return
        源全 = self._卡字节(it)
        if 倍 < 0.999:
            源全 = 源全.translate(self._淡表(it, 倍))
        行4 = it.w * 4
        表 = it._内缩表
        for r in range(it.h):
            y = 行 + r
            if not (0 <= y < 高):
                continue
            内 = 表[r]
            if 内 * 2 >= it.w:
                continue
            目 = (y * 宽) * 4 + 内 * 4
            起 = r * 行4 + 内 * 4
            buf[目:目 + (it.w - 2 * 内) * 4] = 源全[起:起 + (it.w - 2 * 内) * 4]

    def _贴条(self, buf, it, 行, 宽, 高):
        """把进度条按行拷进帧缓冲，边界那一列做亚像素混合。"""
        if it.sticky or it._leaving:
            return
        分 = it.progress()
        if 分 <= 0.0:
            return
        条, bh = self._条预乘(it.w, it.h, it.level)
        倍 = float(getattr(it, "淡", 1.0))
        if 倍 <= 0.02:
            return
        if 倍 < 0.999:
            条 = 条.translate(self._淡表(it, 倍))
        浮 = it.w * min(1.0, 分)
        宽2 = max(1, int(浮))
        余 = 浮 - int(浮)
        for r in range(bh):
            y = 行 + it.h - bh + r
            if not (0 <= y < 高):
                continue
            目 = (y * 宽) * 4
            起 = r * it.w * 4
            buf[目:目 + 宽2 * 4] = 条[起:起 + 宽2 * 4]
            if 余 > 0.02 and 宽2 < it.w:
                目2 = 目 + 宽2 * 4
                起2 = 起 + 宽2 * 4
                for k in range(3):
                    底v = buf[目2 + k]
                    buf[目2 + k] = int(条[起2 + k] * 余 + 底v * (1.0 - 余))

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
    _OUT_MS = 130
    # 让位动画：原来 110ms + ease-out cubic，头两帧就走完 37% 的距离，之后慢慢爬 ——
    # 眼睛读成"猛一下再磨蹭"。改成 160ms + smoothstep，每帧位移约为原来的一半。
    _MOVE_MS = 160

    def __init__(self, host, text, level, ms, sticky):
        self.host = host
        self.text = text
        self.level = level
        self.ms = ms
        self.sticky = sticky
        self.gone = False
        self.t0 = time.perf_counter()
        self.dy = 0.0
        # ⚠ _y 用 None 表示"还没排过版"。用 0.0 的话，_layout 会把新卡当成
        # "原本就在 0 位置"的老卡，算出 dy = (0 - 新位置) + Δ窗口 = 负数 →
        # 新卡会从上面几张卡片中间**往下滑**到自己的位置（用户说的"回弹"）。
        self._y = None
        self.淡 = 1.0                 # 不透明度（入场淡入、出场淡出用）
        self._淡动画 = None
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

    def fade_to(self, 目标, 时长, 结束后=None):
        """透明度动画。

        ⚠ 入场/出场**用淡入淡出，不要用纵向位移**：整叠是底部对齐的，新卡要占的位置
        正是旧卡原来的屏幕位置 —— 让两者同时纵向移动就必然"互相穿插"，纯字节拷贝下
        表现为一张卡被另一张啃掉一角（截图验过）。淡入淡出没有交叉，也就没有这瑕疵。
        """
        self._淡动画 = [float(self.淡), float(目标), time.perf_counter(),
                        max(1.0, float(时长)), 结束后]

    def _advance(self, 现在):
        """推进动画/判断到点。返回是否仍在动画中（宿主据此决定下一帧间隔）。"""
        if self.gone:
            return False
        忙 = False
        if self._淡动画 is not None:
            起, 目标, t0, 时长, 回调 = self._淡动画
            k = min(1.0, (现在 - t0) * 1000.0 / 时长)
            # 线性就够：透明度线性变化比纵向缓动更难被眼睛察觉
            self.淡 = 起 + (目标 - 起) * k
            if k >= 1.0:
                self.淡 = 目标
                self._淡动画 = None
                if 回调 is not None:
                    try:
                        回调()
                    except Exception:
                        pass
            else:
                忙 = True
        if self._anim is not None:
            起, 目标, t0, 时长, 回调 = self._anim
            k = min(1.0, (现在 - t0) * 1000.0 / 时长)
            # smoothstep：两端都平缓，中段最快 —— 起步不"猛一下"，收尾不"磨蹭"。
            # 比 ease-out cubic 的峰值速度低约 1/3（cubic 的峰值在起点，smoothstep 在中间）。
            self.dy = 起 + (目标 - 起) * (k * k * (3.0 - 2.0 * k))
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
            return 忙
        return 忙 or (self._anim is not None)

    # ---------------------------------------------------------------- 回收
    def dismiss(self):
        if self.gone or self._leaving:
            return
        self._leaving = True
        # 出场同样用淡出：往下滑会钻进下面那条卡的位置，同样会互相啃
        self.fade_to(0.0, self._OUT_MS, 结束后=self.kill)

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
        # ⚠ 必须带 重排已有：不带的话 _y 已被重算过，后面 show() 里那次 _layout
        # 再算差值就是 0 —— 幸存者不会"滑"上去，而是**直接瞬跳**（第 5 条挤掉最旧时
        # 最容易看到，用户报的"老问题还在"就是这个）。
        self.host._layout(重排已有=True)
        self.host._ensure_tick()

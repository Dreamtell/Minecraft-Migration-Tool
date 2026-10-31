# ui/mw_migration.py
"""迁移流程：统计、磁盘检查、后台线程、进度、历史与回滚、差异扫描。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import os
import queue
import shutil
import threading

NL = chr(10)  # 拼多行文案用（补丁脚本写反斜杠n容易被二次转义）
import time
import tkinter as tk
from core import precheck
from core.migrator import (
    _is_safe_path, do_backup, do_restore, get_backup_path, get_record_backup_path,
    list_record_backups, load_history, mark_rollback, match_mod,
    prune_legacy_backup, run_migration,
)
from core.scanner import scan_mod_differences
from pathlib import Path
from tkinter import messagebox
from ui.dialogs import ProgressWindow, ScanProgressWindow, ask_migrate_confirm
from ui.mw_common import (
    _SCAN_DONE, _UI_PUMP_BUDGET_MS, _UI_PUMP_MAX_ITEMS, _UI_PUMP_MS,
    _center_window, _file_task_lock, _grad_width,
)
from ui.virtual_table import VirtualTable
from utils import anim            # 帧节奏（跟随显示器刷新率）—— 只用来问"多久排下一帧"
from utils.dpi import px, window_geometry
from utils.helpers import DataText, create_gradient_button, focus_window, set_window_icon
from utils.theme import apply_theme_to_widget_tree

# 界面语言：日志/文案模板走 trp（中文模式下与原 f-string 逐字一致）
from utils.i18n import trp


def _打桩优先(名字, 默认):
    """取被 _dctest 验证脚本打过桩的那个函数，没打桩就用本模块 import 的那份。

    拆分前这些名字就住在 ui/main_window.py 里，脚本一直是这么打桩的：
        MW.ask_migrate_confirm = lambda *a, **k: True     # 验证迁移收尾.py
        MW.do_backup = lambda *a, **kw: True              # double_start2.py
    现在方法搬到了这个 mixin，`from ... import` 是**一次性绑定**，脚本改 main_window
    的属性就影响不到这里了（确认框会真弹、备份会真跑）。所以这两处依赖改成调用时
    再去主模块取一次 —— 脚本一行都不用改，语义和拆分前完全一致。
    """
    import ui.main_window as _mw
    return getattr(_mw, 名字, 默认)


def _占定时器(拥有者):
    """滚动链启动时把系统定时器精度提到 1ms（配对 anim.release）。

    为什么非占不可：Tk 的 after() 在真 mainloop 里被 Windows 默认的 15.6ms 定时器粒度
    卡死 —— 实测 after(4) 只有 64fps、p50 15.9ms，raise 之后才是 232fps、p50 4.05ms。
    但全程 raise 会让整机空转耗电，所以只在链跑着的时候占。
    ⚠ 绝不每帧 hold/release：计数漏还一次，高精度定时器就被永久占着了。
    """
    if not getattr(拥有者, "_holding", False):
        anim.hold()
        拥有者._holding = True


def _停计时(拥有者):
    """还回高精度定时器。滚动链的每条出口都要走这里，宁可多还一次也不能漏还。"""
    if getattr(拥有者, "_holding", False):
        拥有者._holding = False
        anim.release()


class _SmoothText(tk.Text):
    """日志框：滚轮走缓动，而不是一格一格地跳。

    Tk 的 Text 没有像素滚动（`yview units` 的单位就是"行"），所以这里按**行**
    做缓动：滚轮只改目标行号，然后每帧朝目标走一小段，看起来就是滑过去。
    目标按"当前顶部行"重新算，所以拖滚动条/按 PageDown 之后也不会漂。

    ⚠ 走多快**必须按真实时间折算**：改前是 after(16) + 每帧 1 行（= "1 行 / 16ms"）
    定死的，帧间隔一变成 4ms，同样的"每帧 1 行"就快 4 倍。现在每帧先算"从手势开始
    到现在本该滚过几行"（见 _anim），只把差额滚出去 —— 16ms 排帧和 4ms 排帧走完
    同样的距离、花同样的时间，观感和改前一致。
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self._job = None
        self._target = 1
        self._holding = False
        # 兜底：日志框会随历史窗口一起销毁，那时链还没跑完（甚至正卡在一帧中间），
        # 不走这条绑定的话 hold() 的计数永远停在 1，整机一直占着 1ms 精度。
        self.bind("<Destroy>", lambda e: _停计时(self), add="+")
        self.bind("<MouseWheel>", self._on_wheel, add="+")
        self.bind("<Button-4>", lambda e: (self._scroll(-3), "break")[-1], add="+")
        self.bind("<Button-5>", lambda e: (self._scroll(3), "break")[-1], add="+")

    def _top(self):
        try:
            return int(float(self.index("@0,0")))
        except Exception:
            return 1

    def _on_wheel(self, e):
        步 = int(e.delta / 120) or (1 if e.delta > 0 else -1)
        self._scroll(-步 * 3)
        return "break"      # ⚠ 必须拦住 Text 的默认类绑定，否则它自己一次跳 3 行

    def _scroll(self, 行):
        if self._job is None:
            self._target = self._top()
        总 = 1
        try:
            总 = max(1, int(float(self.index("end-1c"))))
        except Exception:
            pass
        self._target = max(1, min(总, self._target + 行))
        self._anim()

    # 手感基准：改前是 after(16) + 每帧滚 1 行，也就是"1 行 / 16ms"。这是**速度基准**，
    # 不要换成 anim.frame_ms()：帧间隔是"多久算一次"，这里要的是"每毫秒走多远"。
    _行基准 = 0.016

    def _anim(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        _占定时器(self)            # 链一起跑就 raise 定时器精度，见 _占定时器 的说明
        # 手势起点：这两帧之后"走了多远"一律按 (现在 - 起点) 的**绝对时间**算，
        # 不逐帧累加。累加会把每帧取整的零头攒着，收尾时 4ms 排帧与 16ms 排帧能差
        # 出一整帧的量（实测 3 行 47ms vs 36ms，差 24%）。
        self._起_t = time.perf_counter()
        self._起行 = self._top()

        def 帧():
            剩 = self._target - self._top()
            if 剩 == 0:
                self._job = None
                _停计时(self)
                return
            # 这一帧"总共该滚过几行"：+1 是改前"滚轮一到就立刻滚 1 行"的那一步，
            # 之后每过 _行基准(16ms) 再多 1 行。
            # 那 1ms 是**取整余量**：真机的 after(16) 实测 15.6~16.1ms，差那么一点
            # 就会被 int() 截掉，于是每一行都要多等一帧（3 行 47ms 而不是 32ms）。
            # 反过来也不能"四舍五入"（+8ms 会让 4ms 排帧每行早到半格，实测快 41%）。
            该走 = int((time.perf_counter() - self._起_t + 0.001) / self._行基准) + 1
            已走 = abs(self._top() - self._起行)
            步 = min(abs(剩), 该走 - 已走)
            if 步 > 0:
                self.yview_scroll(步 if 剩 > 0 else -步, "units")
                # 到位就当场停：否则还要多排一帧空转，"开始到停住"的总时长会随帧间隔
                # 长短而变化（帧间隔越短空转越短，看着像变快了）。
                if self._top() == self._target:
                    self._job = None
                    _停计时(self)
                    return
            self._job = self.after(anim.delay(self), 帧)

        帧()


def _hex_rgb(v, 默认=(128, 128, 128)):
    """'#rrggbb' / 'rrggbb' / '#rgb' → (r, g, b)。"""
    v = str(v or "").lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except Exception:
        return 默认


def _palette(theme):
    """按主题亮度给自绘卡片取一套颜色：(面板, 边框, 主色, 是否深色)。"""
    底 = _hex_rgb(theme.get("bg"), (255, 255, 255))
    深 = (0.299 * 底[0] + 0.587 * 底[1] + 0.114 * 底[2]) < 128
    面板 = _hex_rgb(theme.get("panel_bg") or ("#333333" if 深 else "#fafafa"))
    边框 = _hex_rgb(theme.get("border") or ("#3f3f3f" if 深 else "#d6d6d6"))
    主色 = _hex_rgb(theme.get("accent") or ("#64b5f6" if 深 else "#1e88e5"))
    return 面板, 边框, 主色, 深


class _ArchiveList(tk.Frame):
    """左侧档案卡列表：圆角卡片 + 悬停高亮 + 平滑滚动。

    卡片用 Pillow 画（3 倍超采样再缩小），圆角是抗锯齿的 —— Tk 自己的
    create_rectangle 是硬边，做不出圆角，create_polygon(smooth=1) 又会在
    直边处鼓出来，所以这里走位图。
    每张卡按「普通 / 悬停 / 选中」三种状态各缓存一张图，滚动或悬停时只换图，
    不重新渲染（否则 60fps 会卡）。
    """

    CARD_H = 92
    GAP = 8
    RADIUS = 10
    # 文字图元的 y 相对卡片顶的偏移（_build 里 create_text 用的就是这几个值；0 号是整卡图片）
    _文字DY = {1: 16, 2: 16, 3: 38, 4: 60, 5: 78}

    def __init__(self, master, theme, records, on_select):
        super().__init__(master, bg=theme["bg"])
        self.theme = theme
        self.records = records
        self.on_select = on_select
        self.sel = 0
        self.hover = -1
        self.offset = 0.0
        self.target = 0.0
        self._job = None
        self._last_t = 0.0
        self._holding = False
        self._imgs = {}
        self.canvas = tk.Canvas(self, bg=theme["bg"], highlightthickness=0, bd=0,
                                cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        # 兜底：窗口关掉时链还没跑完，不走这条绑定就会把 hold() 的计数留在 1
        self.bind("<Destroy>", lambda e: _停计时(self), add="+")
        self.redraw()

    # ---------- 几何 ----------
    def _top(self, i):
        return px(4) + i * (px(self.CARD_H) + px(self.GAP)) - self.offset

    def _hit(self, y):
        step = px(self.CARD_H) + px(self.GAP)
        i = int((y + self.offset - px(4)) // step)
        return i if 0 <= i < len(self.records) else -1

    def _total(self):
        return len(self.records) * (px(self.CARD_H) + px(self.GAP)) + px(8)

    def _max_off(self):
        return max(0.0, self._total() - (self.canvas.winfo_height() or 0))

    # ---------- 交互 ----------
    def _click(self, e):
        i = self._hit(e.y)
        if i >= 0:
            self.sel = i
            self.redraw()
            self.on_select(i)

    def _motion(self, e):
        i = self._hit(e.y)
        if i != self.hover:
            self.hover = i
            self.redraw()

    def _leave(self, _e):
        if self.hover != -1:
            self.hover = -1
            self.redraw()

    # 缓动系数：每 16ms 仍剩 70% 距离（≈210ms 收敛，和原来 220ms 的手感对齐）
    _REST = 0.70

    def _wheel(self, e):
        # 触控板/滚轮都给一个"步数"：只挪目标，正在跑的缓动接着追，绝不重开
        步 = int(e.delta / 120) or (1 if e.delta > 0 else -1)
        self.target = max(0.0, min(self._max_off(), self.target - 步 * px(56)))
        self._anim()

    def _anim(self):
        """滚动缓动：一阶低通，每帧按**真实 dt** 收掉剩余距离的 30%。

        ⚠ 这里前后换过三版，前两版都栽在同一件事上 —— 别改回"每次滚轮重开一条插值"：

        · 每帧走剩余 35%：滚动量大时越滚越慢、拖尾很长。
        · 固定时长 220ms 的 easeOutCubic：每来一个滚轮事件就从当前位置重开一条链，
          而插值第 0 帧的位移恒为 0；触控板之类 8ms 就发一次事件，16ms 的定时器还没跑
          就被取消 —— 整个滑动期间列表**一动不动**，事件停了才"啪"地跳过去
          （实测 40 次 @8ms 连发：期间位移 0px，结束后一步跳 990px）。
          不取消而是放任多条链并行，则会来回跳着写 offset（实测 63 帧里 14 帧往回跳）。

        低通没有"重开"这一步：滚轮只改 target，位移天然连续，也永远不会卡住不动。
        收尾用 0.5px 阈值直接吸附，避免留下看不出来的尾巴一直空转。
        """
        if abs(self.target - self.offset) < 0.5:
            self.offset = self.target
            self.redraw()
            return
        if self._job is not None:
            return                      # 链已经在跑，让它继续追新的 target
        _占定时器(self)                # 4ms 排帧必须先把定时器精度提到 1ms
        self._last_t = time.perf_counter()
        self._tick()

    def _tick(self):
        # ⚠ 计时必须用 perf_counter：这台机器上 time.monotonic() 走的是 GetTickCount64，
        # 实测 15.6ms 一格（sleep(4ms) 采样出的间隔 p50=0.00ms / max=16.00ms），
        # 4ms 排帧时 4 帧里有 3 帧 dt=0 —— 缓动只在 15.6ms 的格子里跳，提帧等于白提。
        # perf_counter 同一测法 p50=4.51ms，每帧都有真实 dt。
        now = time.perf_counter()
        dt = max(0.0, min(0.05, now - self._last_t))   # 卡了一下也别一帧跳太远
        self._last_t = now
        self.offset += (self.target - self.offset) * (1.0 - self._REST ** (dt / 0.016))
        if abs(self.target - self.offset) < 0.5:
            self.offset = self.target
            self.redraw()
            self._job = None
            _停计时(self)              # 出口 1：正常吸附收工
            return
        self.redraw()
        try:
            # 多久排下一帧交给 anim.delay：240Hz 屏给 4ms（最小化/失焦自动降到 33ms）。
            # 上面那行 `_REST ** (dt / 0.016)` 里的 0.016 是**手感基准**，不是帧间隔，别动。
            # 排帧间隔要**扣掉本帧已经花掉的时间**：after() 是从"现在"起算的，
            # 直接给 anim.delay() 的话帧周期 = 一帧的活(实测 3.4~4.6ms，其中 Tk 重绘
            # 2.4~3.6ms) + 4ms ≈ 8ms —— 真 mainloop 实测 p50 8.08ms / 131fps，
            # 到不了 240Hz。扣掉之后周期才贴着刷新率走（实测 p50 4.4ms / ~227fps）。
            用了 = int((time.perf_counter() - now) * 1000)
            self._job = self.after(max(1, anim.delay(self) - 用了), self._tick)
        except Exception:
            self._job = None
            _停计时(self)              # 出口 2：排帧失败（控件没了）

    # ---------- 绘制 ----------
    def _card_img(self, w, h, 状态, 条色="#2e7d32"):
        w = max(8, int(w))
        key = (w, h, 状态, 条色)
        图 = self._imgs.get(key)
        if 图 is not None:
            return 图
        from PIL import Image, ImageDraw, ImageTk

        面板, 边, 主, _深 = _palette(self.theme)
        if 状态 == "s":
            填充 = tuple(min(255, int(c + (主[k] - c) * 0.10)) for k, c in enumerate(面板))
            描边, 粗 = 主, 2
        elif 状态 == "h":
            填充 = tuple(min(255, int(c + (主[k] - c) * 0.05)) for k, c in enumerate(面板))
            描边, 粗 = tuple(int(边[k] * 0.6 + 主[k] * 0.4) for k in range(3)), 1
        else:
            填充, 描边, 粗 = 面板, 边, 1

        S = 3
        img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        r = px(self.RADIUS) * S
        框 = [0, 0, w * S - 1, h * S - 1]
        d.rounded_rectangle(框, radius=r, fill=填充 + (255,))
        # 左侧色条：先铺满整块圆角，再用面板色盖掉右边，只留 5px 宽
        d.rounded_rectangle(框, radius=r, fill=_hex_rgb(条色, (46, 125, 50)) + (255,))
        d.rectangle([px(5) * S, 0, w * S, h * S], fill=填充 + (255,))
        d.rounded_rectangle(框, radius=r, outline=描边 + (255,), width=粗 * S)
        图 = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
        self._imgs[key] = 图
        return 图

    def _build(self, w, h):
        """建一次图元；之后滚动只改坐标（不删了重建，否则每帧都闪）。"""
        c = self.canvas
        try:
            c.delete("all")
        except Exception:
            pass
        self._items = []
        弱色 = self.theme.get("muted_fg", "#888888")
        for i, rec in enumerate(self.records):
            模式 = rec.get("mode")
            结果 = rec.get("result") or ""
            if 模式 == "dry":
                条 = self.theme.get("log_warning_fg", "#e65100")
            elif 结果 == "成功":
                条 = self.theme.get("ok_fg", "#2e7d32")
            else:
                条 = self.theme.get("fail_fg", "#c62828")
            状态 = "s" if i == self.sel else ("h" if i == self.hover else "n")
            img = self._card_img(w - px(14), px(self.CARD_H), 状态, 条)
            组 = [c.create_image(px(4), 0, anchor="nw", image=img)]

            def _名(pth):
                段 = str(pth or "").replace("\\", "/").rstrip("/").split("/")
                return 段[-1] if 段 else "?"
            组.append(c.create_text(px(20), px(16), anchor="w",
                                    text=rec.get("time", "?"),
                                    fill=self.theme["fg"], font=("微软雅黑", 10, "bold")))
            组.append(c.create_text(w - px(24), px(16), anchor="e",
                                    text=(trp("模拟") if 模式 == "dry" else trp("实际")),
                                    fill=条, font=("微软雅黑", 9, "bold")))
            组.append(c.create_text(px(20), px(38), anchor="w",
                                    text="%s → %s" % (_名(rec.get("source")), _名(rec.get("target"))),
                                    fill=弱色, font=("微软雅黑", 9)))
            统 = rec.get("stats") or {}
            组.append(c.create_text(
                px(20), px(60), anchor="w",
                text=trp("模组 {0}　·　config {1}　·　其它 {2}",
                    (统.get("mods") or {}).get("success", 0),
                    (统.get("config") or {}).get("success", 0),
                    (统.get("extra") or {}).get("success", 0)),
                fill=弱色, font=("微软雅黑", 9)))
            组.append(c.create_text(
                px(20), px(78), anchor="w",
                text=trp("耗时 {0} 秒　·　{1}", rec.get("duration", "?"), 结果 or "—"),
                fill=弱色, font=("微软雅黑", 8)))
            # 滚动时只需要 (图元, x, 相对卡片顶的 dy)：文字图元的 x 建完就再也不变了，
            # 所以在建的时候取一次存下来。改前 redraw() 是每帧每个图元都
            # `c.coords(item, c.coords(item)[0], top + 相对)` —— 先取再设，两次 Tcl 往返，
            # 12 卡 × 6 图元 = 每帧 144 次（实测单次 6~12us，光这一段就 0.9ms）。
            # ⚠ 0 号图片的 dy 必须直接写 0：px(0) 是 1（px 有"至少 1 像素"的语义），
            # 用它算偏移会把整张卡往下挪 1px（实测改动前后快照对不上就是这个）。
            self._items.append([(item, px(4) if k == 0 else c.coords(item)[0],
                                 px(self._文字DY[k]) if k in self._文字DY else 0)
                                for k, item in enumerate(组)])
        self._sb = c.create_rectangle(0, 0, 0, 0,
                                      fill=self.theme.get("muted_fg", "#888888"),
                                      outline="")
        self._sig = (w, h, self.sel, self.hover, len(self.records))
        # ⚠ 别写成 self._w / self._h：那是 Tkinter 自己存的**控件路径名**（widget path），
        # 覆盖掉之后这个 Frame 的所有 winfo_* / bind 查询 / destroy 都会报
        # bad window path name "420"（实测 winfo_exists() 直接返回 0，
        # anim.is_slow(self) 永远为 False、<Destroy> 绑定也不会响）。
        self._卡宽, self._卡高 = w, h

    def redraw(self):
        c = self.canvas
        w = c.winfo_width() or px(360)
        h = c.winfo_height() or px(400)
        if w < px(24):
            return              # 展开动画把列表压到 0 宽时不画（PIL 会报负宽度）
        sig = (w, h, self.sel, self.hover, len(self.records))
        if sig != getattr(self, "_sig", None):
            self._build(w, h)
        # 只改坐标：滚动时没有删除/重建，所以不会闪。
        # 每个图元只发一次 coords（x / dy 在 _build 里就记好了，不用每帧再取回来）。
        for i, 列 in enumerate(getattr(self, "_items", [])):
            top = self._top(i)
            for item, x, dy in 列:
                c.coords(item, x, top + dy)
        # 滚动条
        total, hh = self._total(), h
        try:
            if total > hh:
                bar = max(px(24), int(hh * hh / total))
                top = int((hh - bar) * (self.offset / max(1.0, total - hh)))
                c.coords(self._sb, w - px(6), top, w - px(3), top + bar)
                c.itemconfigure(self._sb, state="normal")
            else:
                c.itemconfigure(self._sb, state="hidden")
        except Exception:
            pass


class MigrationMixin:
    """迁移流程：统计、磁盘检查、后台线程、进度、历史与回滚、差异扫描。"""

    # ---------- 历史记录 ----------
    # ---------- 迁移历史（档案形式） ----------
    @staticmethod
    def _日志级别标签(行):
        """旧档案没有级别字段时，从行里的图标/关键字推断该用哪个颜色标签。"""
        文 = str(行 or "")
        if "❌" in 文 or "ERROR" in 文:
            return "log_error"
        if "⚠️" in 文 or "⏭️" in 文:
            return "log_warning"
        if "✅" in 文:
            return "log_success"
        if "[模拟]" in 文 or "[SIMULATE]" in 文:
            return "log_simulate"
        return "log_info"

    def action_show_history(self):
        """迁移历史：一次迁移 = 一份档案，按时间倒序排列，可看统计/清单/完整日志。

        档案由 `core/migrator.run_migration` 在收尾时写入 `utils/history_store`
        （真实迁移和模拟运行都会写），这里只负责读取和展示。
        """
        existing = getattr(self, "_history_win", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    focus_window(existing)      # 已有就置顶
                    return
            except Exception:
                pass

        from utils import history_store
        记录 = history_store.list_records()
        上限 = history_store.get_limit()

        顶 = tk.Toplevel(self.root)
        self._history_win = 顶
        顶.withdraw()      # 构建完居中后再显示，避免"闪现-跳到中间"
        顶.title(trp("迁移历史"))
        try:
            顶.geometry(window_geometry(顶, 1040, 660))
        except Exception:
            顶.geometry("%dx%d" % (px(1040), px(660)))
        顶.minsize(px(880), px(520))
        顶.configure(bg=self.theme["bg"])
        顶.transient(self.root)
        try:
            set_window_icon(顶)
        except Exception:
            pass

        面板色 = self.theme.get("panel_bg", self.theme.get("bg", "#ffffff"))
        深色系 = self.theme.get("bg", "#ffffff").lower() in ("#2e2e2e", "#1e1e1e", "#252525", "#202020")
        边框色 = self.theme.get("border") or ("#3a3a3a" if 深色系 else "#d0d0d0")
        弱色 = self.theme.get("muted_fg", "#888888")
        主色 = self.theme.get("accent", "#1e88e5")
        好色 = self.theme.get("ok_fg", "#2e7d32")
        警色 = self.theme.get("log_warning_fg", "#e65100")
        坏色 = self.theme.get("fail_fg") or self.theme.get("err_fg", "#c62828")

        # ---- 头部 ----
        头 = tk.Frame(顶, bg=self.theme["bg"])
        头.pack(fill="x", padx=px(18), pady=(px(14), px(6)))
        tk.Label(头, text="🗂️", font=("微软雅黑", 20), bg=self.theme["bg"],
                 fg=主色).pack(side="left", padx=(0, px(12)), anchor="n")
        标题框 = tk.Frame(头, bg=self.theme["bg"])
        标题框.pack(side="left", fill="x", expand=True)
        tk.Label(标题框, text="迁移历史", font=("微软雅黑", 15, "bold"),
                 bg=self.theme["bg"], fg=self.theme["fg"], anchor="w").pack(fill="x")
        tk.Label(标题框, text=trp("共 {0} 份档案　·　最多保存 {1} 份（可在设置里调整）　·　{2}",
                 len(记录), 上限, history_store._root()),
                 font=("微软雅黑", 9), bg=self.theme["bg"], fg=弱色,
                 anchor="w").pack(fill="x", pady=(px(3), 0))

        # ---- 主体：左档案卡列表 / 右详情 ----
        主体 = tk.Frame(顶, bg=self.theme["bg"])
        主体.pack(fill="both", expand=True, padx=px(16), pady=(px(4), px(4)))

        左 = tk.Frame(主体, bg=self.theme["bg"], width=px(380))
        左.pack(side="left", fill="y")
        左.pack_propagate(False)

        # 右侧详情：用 Canvas 画圆角背景，内容放在它上面的一个 Frame 里
        右 = tk.Canvas(主体, bg=self.theme["bg"], highlightthickness=0, bd=0)
        右.pack(side="left", fill="both", expand=True, padx=(px(12), 0))
        内容 = tk.Frame(右, bg=面板色)
        _内窗 = 右.create_window(0, 0, window=内容, anchor="nw")
        _背景 = {"img": None}

        def _画圆角背景(_e=None):
            from PIL import Image, ImageDraw, ImageTk

            _面板, _边框, _主, _ = _palette(self.theme)

            w = max(2, 右.winfo_width())
            h = max(2, 右.winfo_height())
            # 先把整块铺成面板色（瞬间），再延后一帧画圆角图 ——
            # 否则缩放的那一帧会露出深色底，看起来就是"闪一下"。
            try:
                右.delete("bg")
                右.create_rectangle(0, 0, w, h, fill=面板色, outline="", tags="bg")
            except Exception:
                pass
            if getattr(self, "_hist_animating", False):
                return          # 展开/还原的动画期间只铺纯色，动画结束再画圆角
            S = 2
            半径 = px(14) * S
            img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([0, 0, w * S - 1, h * S - 1], radius=半径,
                                fill=_面板 + (255,),
                                outline=_边框 + (255,),
                                width=S)
            背景图 = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
            _背景["img"] = 背景图

            def _换上():
                try:
                    if (右.winfo_width(), 右.winfo_height()) != (w, h):
                        return                      # 尺寸又变了，这一帧作废
                    右.delete("bg")
                    右.create_image(0, 0, anchor="nw", image=背景图, tags="bg")
                    右.tag_lower("bg")
                except Exception:
                    pass
            try:
                右.after_idle(_换上)
            except Exception:
                _换上()
            # ⚠ 动画期间别把 _内窗 拽回原位：放大/还原会改画布尺寸，<Configure> 正好
            # 在这时触发，重置一下就等于把滑动动画直接掐掉。
            if not getattr(self, "_hist_animating", False):
                右.coords(_内窗, px(1), px(1))
            右.itemconfig(_内窗, width=w - px(2), height=h - px(2))

        右.bind("<Configure>", _画圆角背景)

        列表 = _ArchiveList(左, self.theme, 记录, lambda i: 选中(i))
        列表.pack(fill="both", expand=True)

        # ---- 右侧详情 ----
        详情 = {}

        def _行(父, 名, 值, 值色=None):
            行 = tk.Frame(父, bg=面板色)
            行.pack(fill="x", pady=(0, px(3)))
            tk.Label(行, text=名, font=("微软雅黑", 9), bg=面板色, fg=弱色,
                     width=8, anchor="w").pack(side="left")
            lbl = tk.Label(行, text=值, font=("微软雅黑", 9), bg=面板色,
                           fg=值色 or self.theme["fg"], anchor="w", justify="left")
            lbl.pack(side="left", fill="x", expand=True)
            return lbl

        顶条 = tk.Frame(内容, bg=面板色)
        顶条.pack(fill="x", padx=px(14), pady=(px(12), px(6)))
        详情["标题"] = tk.Label(顶条, text="", font=("微软雅黑", 13, "bold"),
                                bg=面板色, fg=self.theme["fg"], anchor="w")
        详情["标题"].pack(fill="x")
        详情["副标题"] = tk.Label(顶条, text="", font=("微软雅黑", 9), bg=面板色,
                                  fg=弱色, anchor="w")
        详情["副标题"].pack(fill="x", pady=(px(2), 0))

        概览 = tk.Frame(内容, bg=面板色)
        概览.pack(fill="x", padx=px(14), pady=(px(4), px(2)))
        数据色 = self.theme.get("data_fg", 主色)
        数字色 = self.theme.get("data_num_fg", 主色)

        def _行文本(名, 值色=None):
            行 = tk.Frame(概览, bg=面板色)
            行.pack(fill="x", pady=(0, px(3)))
            tk.Label(行, text=名, font=("微软雅黑", 9), bg=面板色, fg=弱色,
                     width=8, anchor="w").pack(side="left")
            标 = tk.Label(行, text="", font=("微软雅黑", 9), bg=面板色,
                          fg=值色 or self.theme["fg"], anchor="w", justify="left")
            标.pack(side="left", fill="x", expand=True)
            return 标

        def _行数据(名):
            """带颜色分级的统计行：数字用数据色，跳过用警示色，失败用红色。"""
            行 = tk.Frame(概览, bg=面板色)
            行.pack(fill="x", pady=(0, px(3)))
            tk.Label(行, text=名, font=("微软雅黑", 9), bg=面板色, fg=弱色,
                     width=8, anchor="w").pack(side="left")
            槽 = tk.Frame(行, bg=面板色)
            槽.pack(side="left", fill="x", expand=True)
            return 槽

        def _填数据(槽, 成功, 跳过=0, 失败=0, 有跳过=True):
            for c in 槽.winfo_children():
                c.destroy()
            段 = [(str(成功), "data_num_fg"), (trp(" 个"), "muted_fg")]
            if 有跳过:
                段 += [(trp("　跳过 "), "muted_fg"),
                       (str(跳过), "log_warning_fg" if 跳过 else "muted_fg")]
            段 += [(trp("　失败 "), "muted_fg"),
                   (str(失败), "fail_fg" if 失败 else "ok_fg")]
            DataText(槽, self.theme, 段, font=("微软雅黑", 9)).pack(anchor="w")

        详情["源"] = _行文本(trp("源目录"), 数据色)
        详情["目标"] = _行文本(trp("目标目录"), 数据色)
        详情["模组"] = _行数据(trp("模组"))
        详情["config"] = _行数据("config")
        详情["其它"] = _行数据(trp("其它"))
        详情["耗时"] = _行文本(trp("耗时"), 数字色)
        详情["失败"] = _行文本(trp("失败"))

        日志标题 = tk.Frame(内容, bg=面板色)
        日志标题.pack(fill="x", padx=px(14), pady=(px(10), px(2)))
        tk.Label(日志标题, text="执行日志", font=("微软雅黑", 9, "bold"),
                 bg=面板色, fg=self.theme["fg"], anchor="w").pack(side="left")
        详情["日志计数"] = tk.Label(日志标题, text="", font=("微软雅黑", 8),
                                    bg=面板色, fg=弱色, anchor="e")
        详情["日志计数"].pack(side="right")

        # 放大查看：**在本窗口里**把日志铺满（不另开窗口）
        _放大 = {"on": False}
        放大钮 = tk.Label(日志标题, text=trp("⤢  放大"), bg=面板色, fg=主色,
                        cursor="hand2", font=("微软雅黑", 9),
                        padx=px(9), pady=px(1), bd=0,
                        highlightthickness=1, highlightbackground=边框色)
        放大钮.pack(side="right", padx=(0, px(10)))
        _悬停底 = self.theme.get("hover_bg") or ("#3a3a3a" if 深色系 else "#eef2f7")
        放大钮.bind("<Enter>", lambda e: 放大钮.config(bg=_悬停底), add="+")
        放大钮.bind("<Leave>", lambda e: 放大钮.config(bg=面板色), add="+")

        def _滑内容(方向=1, 时长=160):
            """把右侧详情整块从偏移处滑到位置（放大/还原、切档案都用它）。

            ⚠ 两个都不能改回去：
            1. **按真实时间推进**。原来是 after(16) + 每帧 k += 16/170，真 mainloop 里
               after(16) 有一半的帧滑到 31ms，缓动被不规则采样 —— 看着就是"生硬"。
            2. **不要再用"逐帧改左栏宽度"来做放大**。实测改一帧宽度要 5~11ms
               （pack 下 10.5ms、place 下也有 5ms，因为列表里的卡片图是按宽度缓存的，
               宽度一变就要重渲 12 张 PIL 卡图），150ms 里根本排不出几帧，又硬又卡。
               现在布局**瞬间切换**，只让详情整块滑一下 —— canvas 挪一个 window 图元
               实测 0.18ms/帧，可以跑满刷新率。
            """
            try:
                if _滑动["job"]:
                    顶.after_cancel(_滑动["job"])
            except Exception:
                pass
            _滑动["job"] = None
            self._hist_animating = True
            if not _滑动.get("hr"):
                _滑动["hr"] = True
                try:
                    anim.hold()
                except Exception:
                    pass
            t0 = time.perf_counter()
            总 = max(1.0, float(时长))

            def 帧():
                k = min(1.0, (time.perf_counter() - t0) * 1000.0 / 总)
                缓 = 1 - (1 - k) ** 3
                dx = int(px(30) * 方向 * (1 - 缓))
                try:
                    右.coords(_内窗, px(1) + dx, px(1))
                except Exception:
                    pass
                if k < 1.0:
                    try:
                        _滑动["job"] = 顶.after(max(1, anim.frame_ms(顶)), 帧)
                    except Exception:
                        _滑动["job"] = None
                    return
                try:
                    右.coords(_内窗, px(1), px(1))
                except Exception:
                    pass
                _滑动["job"] = None
                self._hist_animating = False
                if _滑动.get("hr"):
                    _滑动["hr"] = False
                    try:
                        anim.release()
                    except Exception:
                        pass
                try:
                    右.event_generate("<Configure>")     # 动画结束再画圆角背景
                except Exception:
                    pass

            帧()

        def _切换放大(_e=None):
            """在窗口内放大/还原执行日志。

            布局**瞬间切换**（收/放左栏各要重排整块，逐帧做太贵，见 _滑内容 的说明），
            动感交给详情整块滑动 —— 那个只有 0.18ms/帧。
            """
            _放大["on"] = not _放大["on"]
            if _放大["on"]:
                放大钮.config(text=trp("⤡  还原"))
                概览.pack_forget()
                左.pack_forget()
                _滑内容(+1)
            else:
                放大钮.config(text=trp("⤢  放大"))
                左.configure(width=px(380))
                左.pack(side="left", fill="y", before=右)
                概览.pack(fill="x", padx=px(14), pady=(px(4), px(2)), after=顶条)
                _滑内容(-1)
        放大钮.bind("<Button-1>", _切换放大)

        日志框 = tk.Frame(内容, bg=面板色)
        日志框.pack(fill="both", expand=True, padx=px(14), pady=(0, px(12)))
        详情["日志"] = _SmoothText(日志框, wrap="word", bd=0, highlightthickness=1,
                               highlightbackground=边框色, bg=面板色,
                               fg=self.theme["fg"], font=("Consolas", 9),
                               padx=px(8), pady=px(6), spacing1=1, spacing3=1)
        详情["日志"].pack(side="left", fill="both", expand=True)
        滚 = tk.Scrollbar(日志框, command=详情["日志"].yview, width=px(10))
        滚.pack(side="right", fill="y")
        详情["日志"].config(yscrollcommand=滚.set, state="disabled")

        # 日志按级别上色（和历史窗口、主界面同一套主题色）
        级别标签 = {
            "INFO": ("log_info", self.theme.get("log_info_fg", 弱色)),
            "WARNING": ("log_warning", self.theme.get("log_warning_fg", 警色)),
            "ERROR": ("log_error", self.theme.get("log_error_fg", 坏色)),
            "SUCCESS": ("log_success", self.theme.get("log_success_fg", 好色)),
            "SIMULATE": ("log_simulate", self.theme.get("log_simulate_fg", 主色)),
        }
        for _名, _色 in 级别标签.values():
            详情["日志"].tag_configure(_名, foreground=_色)

        def _清空日志():
            详情["日志"].config(state="normal")
            详情["日志"].delete("1.0", "end")
            详情["日志"].config(state="disabled")
            详情["日志计数"].config(text="")

        _滑动 = {"job": None, "当前": -1, "hr": False}

        def _滑入():
            """切换档案时让详情内容从右边滑进来（和放大共用同一套 dt 驱动实现）。"""
            _滑内容(+1, 170)

        def 选中(i):
            变了 = (i != _滑动["当前"])
            if not 变了 and 0 <= i < len(记录):
                # 点的是当前已选中的档案：只更新卡片高亮，不重排详情（重排会让日志闪）
                列表.sel = i
                列表.redraw()
                return
            列表.sel = i
            列表.redraw()
            显示(记录[i] if 0 <= i < len(记录) else None)
            try:
                getattr(self, "_history_sel_hook", lambda: None)()
            except Exception:
                pass
            if 变了:
                _滑动["当前"] = i
                _滑入()

        def 显示(rec):
            if not rec:
                详情["标题"].config(text=trp("没有档案"))
                详情["副标题"].config(text="")
                for k in ("源", "目标", "耗时", "失败"):
                    详情[k].config(text="—")
                for k in ("模组", "config", "其它"):
                    _填数据(详情[k], 0, 0, 0)
                _清空日志()
                return
            模式 = trp("模拟运行") if rec.get("mode") == "dry" else trp("实际迁移")
            结果 = rec.get("result") or ""
            色 = 好色 if 结果 == "成功" else (警色 if 结果 == "模拟运行" else 坏色)
            详情["标题"].config(text=trp("{0}　·　{1}", rec.get("time", "?"), 模式))
            # 结果里存的是中文原值（"成功"/"模拟运行"/…），显示时过一遍词表；
            # 上面的颜色比较仍然用原值，别改那里的字面量。
            详情["副标题"].config(text=(trp(结果) if 结果 else "—"), fg=色)
            详情["源"].config(text=rec.get("source", "—"))
            详情["目标"].config(text=rec.get("target", "—"))
            统计 = rec.get("stats") or {}
            m = 统计.get("mods") or {}
            c = 统计.get("config") or {}
            e = 统计.get("extra") or {}
            _填数据(详情["模组"], m.get("success", 0), m.get("skipped", 0), m.get("failed", 0))
            _填数据(详情["config"], c.get("success", 0), 0, c.get("failed", 0), 有跳过=False)
            _填数据(详情["其它"], e.get("success", 0), e.get("skipped", 0), e.get("failed", 0))
            详情["耗时"].config(text=trp("{0} 秒", rec.get("duration", "—")))
            失败 = (rec.get("lists") or {}).get("failed") or []
            if 失败:
                详情["失败"].config(text=trp("{0} 条（详见日志）", len(失败)), fg=坏色)
            else:
                详情["失败"].config(text=trp("无"), fg=好色)

            full = history_store.load_record(rec.get("id") or "") or rec
            详情["日志"].config(state="normal")
            详情["日志"].delete("1.0", "end")
            行们 = full.get("log_lines")
            if isinstance(行们, list) and 行们:
                for 项 in 行们:
                    try:
                        级, 文 = 项[0], 项[1]
                    except Exception:
                        continue
                    名, _ = 级别标签.get(str(级).upper(), 级别标签["INFO"])
                    详情["日志"].insert("end", str(文) + "\n", 名)
                行数 = len(行们)
            else:
                # 旧档案没有 log_lines：按行里的图标/关键字推断级别，照样上色
                日志 = full.get("log") or trp("（这份档案没有日志）")
                行数 = 0
                for 行 in 日志.split("\n"):
                    行数 += 1
                    详情["日志"].insert("end", 行 + "\n", self._日志级别标签(行))
            详情["日志"].config(state="disabled")
            详情["日志计数"].config(text=trp("{0} 行", 行数))

        # ---- 底部按钮 ----
        底 = tk.Frame(顶, bg=self.theme["bg"])
        底.pack(fill="x", padx=px(18), pady=(0, px(12)))

        def 删当前():
            i = 列表.sel
            if not (0 <= i < len(记录)):
                return
            rec = 记录[i]
            if not messagebox.askyesno(trp("删除档案"),
                                       trp("确定删除 {0} 这份档案吗？" + NL + "（只删记录，不影响任何游戏文件）",
                                       rec.get("time", "")), parent=顶):
                return
            history_store.delete_record(rec.get("id") or "")
            记录.pop(i)
            列表.records = 记录
            列表.sel = min(i, max(0, len(记录) - 1))
            列表.redraw()
            显示(记录[列表.sel] if 记录 else None)
            详情["副标题"].config(text=trp("已删除"))

        # 回滚并进这里（用户要求）：主界面不再有「回滚」按钮，回滚入口只在这扇窗里。
        # 语义没变 —— 仍然是"把目标实例恢复到最近一次迁移之前"，走的还是同一个
        # action_rollback（后台线程 + 备份还原 + 历史里标记已回滚）。
        def 回滚():
            NL = chr(10)
            选中记录 = 记录[列表.sel] if (记录 and 0 <= 列表.sel < len(记录)) else None
            时间 = str((选中记录 or {}).get("time") or "")
            头句 = trp("要把目标实例恢复到「{0}」这次迁移之前吗？", 时间) if 时间 else trp("要把目标实例恢复到最近一次迁移之前吗？")
            问 = (头句 + NL + NL
                  + trp("会用备份覆盖当前内容：mods / config / saves 以及「其它文件」清单里复制过的部分都会还原，迁移前不存在的东西会被删掉。")
                  + NL + NL
                  + trp("此操作不可撤销。回滚过程看主界面的执行日志。"))
            try:
                from ui import precheck_dialog as _pd
                继续 = bool(_pd.确认(self.root, self.theme, "↩ 回滚到迁移前", 问,
                                    主按钮="回滚", 次按钮=trp("先不滚"), 图标级别="fail"))
            except Exception:
                继续 = bool(messagebox.askyesno("↩ 回滚到迁移前", trp(问)))
            if not 继续:
                return
            try:
                顶.destroy()
            except Exception:
                pass
            self.action_rollback(选中记录)

        回滚钮 = create_gradient_button(底, "↩ 回滚到迁移前", 回滚,
                                       colors=("#e53935", "#ff7043"))
        回滚钮.pack(side="left", padx=(0, px(8)))

        def _刷回滚钮():
            """选中的那条有没有自己的备份 —— 没有就点不了（早期记录没有）。"""
            try:
                备份表 = list_record_backups(self.target_path.get().strip())
                选 = 记录[列表.sel] if (记录 and 0 <= 列表.sel < len(记录)) else None
                有 = bool(选 and str(选.get("id") or "") in 备份表)
                回滚钮.state("normal" if 有 else "disabled")
                if not 有 and 选 is not None:
                    回滚钮._提示 = trp("这条记录没有自己的备份")
            except Exception:
                pass

        self._history_sel_hook = _刷回滚钮
        _刷回滚钮()
        删钮 = create_gradient_button(底, trp("删除这份档案"), 删当前, colors=("#e53935", "#b71c1c"))
        删钮.pack(side="left")
        create_gradient_button(底, "关闭", lambda: 顶.destroy()).pack(side="right")

        if 记录:
            选中(0)
        else:
            显示(None)

        def _关窗():
            try:
                self._history_sel_hook = lambda: None
            except Exception:
                pass
            顶.destroy()

        顶.protocol("WM_DELETE_WINDOW", _关窗)
        try:
            apply_theme_to_widget_tree(顶, self.theme)
        except Exception:
            pass
        _center_window(顶, px(1040), px(660))
        顶.deiconify()

    def action_rollback(self, 记录=None):
        """回滚到某次迁移之前。

        `记录`：迁移档案（dict）。给了就用它自己的那份备份（rec_<id>/），
        没给就挑最近的一份备份 —— 优先按记录分的，其次老的单一备份目录。
        """
        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，暂不能回滚。")
            return
        self.log("=" * 50, level="INFO")
        self.log("🔄 用户请求执行回滚操作", level="INFO")

        tgt = self.target_path.get().strip()
        if not tgt:
            self.log("❌ 回滚失败：未选择目标实例根目录", level="ERROR")
            messagebox.showerror("错误", "请先选择目标实例根目录")
            return
        tgt_path = Path(tgt)
        if not tgt_path.exists():
            self.log(trp("❌ 回滚失败：目标路径不存在 {0}", tgt), level="ERROR")
            messagebox.showerror("错误", trp("目标路径不存在：{0}", tgt))
            return

        记录id = str((记录 or {}).get("id") or "") if isinstance(记录, dict) else ""
        backup_root = None
        if 记录id:
            backup_root = get_record_backup_path(tgt_path, 记录id)
            if not backup_root.exists():
                self.log(trp("❌ 回滚失败：这份档案没有自己的备份（{0}）", 记录id), level="ERROR")
                messagebox.showerror(
                    "回滚失败",
                    trp("这条迁移记录没有可用的备份，回滚不了。" + NL
                        + "（早期版本的迁移不会按记录留备份）"))
                return
        else:
            备份表 = list_record_backups(tgt_path)
            if 备份表:
                backup_root = max(备份表.values(), key=lambda p: p.stat().st_mtime)
                记录id = next((k for k, v in 备份表.items() if v == backup_root), "") or 记录id
            else:
                backup_root = get_backup_path(tgt_path)
        if not backup_root.exists():
            self.log(trp("❌ 回滚失败：未找到备份目录 {0}", backup_root), level="ERROR")
            messagebox.showerror("回滚失败", "没有找到可用的备份，无法回滚。")
            return

        self.log(trp("📁 找到备份目录：{0}", backup_root), level="INFO")

        if not messagebox.askyesno(
                "⚠️ 确认回滚",
                trp("即将把目标实例恢复到迁移前的状态，此操作将覆盖当前所有内容！\n\n目标路径：{0}\n备份路径：{1}\n\nmods / config / saves 以及「其它文件」清单里复制过的东西都会还原；\n迁移前不存在的部分会被删掉。\n\n此操作不可撤销！\n确定要继续吗？", tgt, backup_root)
        ):
            self.log(trp("❌ 用户取消了回滚操作"), level="WARNING")
            return

        self.log("✅ 用户确认回滚，开始执行...", level="SUCCESS")
        # 回滚要读写成千上万个文件，绝不能跑在主线程上：do_restore 同步跑的话整个
        # 界面会冻住，Windows 给窗口标"未响应"，用户一慌就强杀进程 —— 目标目录
        # 正好停在一半，既不是迁移后的样子、也不是迁移前的样子。
        # 登记成"文件类任务"：期间按钮全灰、开始迁移会被劝住、关窗口也拦得住。
        self._begin_file_task("回滚")
        threading.Thread(target=self._run_rollback_thread,
                         args=(tgt_path, str(backup_root), 记录id),
                         daemon=True).start()

    def _run_rollback_thread(self, tgt_path, backup_dir=None, record_id=""):
        """后台执行回滚。do_restore 的日志回调通过 _ui_post 排回主线程。"""
        def 日志(文本, level="INFO"):
            self._ui_post(self.log, 文本, level)

        success = False
        try:
            success = bool(do_restore(tgt_path, log_func=日志, backup_dir=backup_dir))
        except Exception as e:
            self._ui_post(self.log, trp("❌ 回滚过程中出现异常：{0}", e), "ERROR")
        self._ui_post(self._finish_rollback, tgt_path, success, record_id)

    def _finish_rollback(self, tgt_path, success, record_id=""):
        """回滚收尾（主线程）：标记历史、解除锁定、弹结果。"""
        try:
            if success:
                if mark_rollback(tgt_path, record_id or None):
                    self.log("📝 已标记本次回滚到历史记录", level="INFO")
                else:
                    self.log("ℹ️ 历史记录里没有可标记的迁移记录（可能已经回滚过了）",
                             level="WARNING")
            else:
                self.log("❌ 回滚操作失败，目标实例可能处于不完整状态，"
                         "备份仍然保留，可重新执行回滚", level="ERROR")
        except Exception:
            self.log(trp("❌ 回滚收尾时出错（备份仍保留，可以重新执行回滚）"), level="ERROR")
        finally:
            self._end_file_task()       # 解除"在忙"：按钮恢复、关闭保护解除
        self.log("=" * 50, level="INFO")
        if success:
            messagebox.showinfo("回滚完成", "目标实例已恢复到迁移前的状态。")
            self._notify_task_done("回滚", trp("目标实例已恢复到迁移前的状态"))
        else:
            messagebox.showerror("回滚失败",
                                 "回滚未能完整完成，详情见日志。\n"
                                 "备份目录仍然保留，可以再次尝试回滚。")

    # ---------- 进度轮询 ----------
    def _poll_progress(self):
        # 只要队列在就轮询：模拟运行没有进度窗口，但背面那条 3px 条一样要跟着走
        # （见下面 progress_window 为 None 的分支）。
        if self.progress_queue is not None:
            try:
                while True:
                    msg = self.progress_queue.get_nowait()
                    if msg is None:
                        if self.progress_window is not None:
                            self.progress_window.close()
                        self.progress_window = None
                        self.progress_queue = None
                        if self.after_id is not None:
                            self.root.after_cancel(self.after_id)
                            self.after_id = None
                        self._migration_running = False
                        # 停在 85%：剩下 15% 交给"日志滚完"那一段推（用户要的是
                        # 日志滚完条才满，不是活儿干完就满）。
                        if not self._是模拟运行():
                            self._flip_progress(0.85)
                        self._日志尾巴起步()
                        # 收尾中不解锁：否则完成态被这条抢掉
                        if not getattr(self, "_mig_finish_pending", False):
                            self._unlock_main_window()  # 进度窗口关掉时同步解锁
                        self._notify_task_done(
                            "迁移", trp("任务已结束，点托盘图标打开主界面查看日志"))
                        return
                    if self.progress_window is not None:
                        self.progress_window.update_progress(*msg)
                    # 顺手把进度同步到背面那条 3px 条（copied_bytes / 总量）
                    try:
                        总 = float(getattr(self, "_mig_total_size", 0) or 0)
                        if 总 > 0:
                            # 15% → 85%：干活这一段；剩下 15% 留给"日志还在一行行滚"
                            if not self._是模拟运行():
                                self._flip_progress(
                                    0.85 * min(1.0, float(msg[2]) / 总))
                    except Exception:
                        pass
            except queue.Empty:
                pass
            if self.progress_window and self.progress_window.cancelled:
                if self.progress_queue:
                    self.progress_queue.put(None)
            self.after_id = self.root.after(100, self._poll_progress)
        else:
            self.after_id = None

    def _日志尾巴起步(self):
        """迁移活儿干完了，但日志还在往外滚：条从 85% 推到 100%，和日志一个节奏。

        用剩余日志像素（`_log_remain_px`）当尺子 —— 它减到 0 的那一刻，正好是
        用户看到日志停在最底下的那一刻，条也刚好满。
        """
        if getattr(self, "_log_tail_job", None) is not None:
            return
        self._log_tail_from = None
        self._log_tail_job = -1          # 占位，防重入
        self.root.after(50, self._日志尾巴步)

    def _日志尾巴步(self):
        self._log_tail_job = None
        try:
            剩余 = float(self._log_remain_px())
        except Exception:
            剩余 = 0.0
        if not getattr(self, "_log_tail_from", None):
            self._log_tail_from = max(1.0, 剩余)
        比 = 1.0 - min(1.0, 剩余 / max(1.0, float(self._log_tail_from)))
        if not self._是模拟运行():
            self._flip_progress(0.85 + 0.15 * 比)
        # 日志滚到底、或者收尾已经把按钮翻回正面了 —— 两种都收手
        if 剩余 > 0.5 and getattr(self, "_ending_wait_log", False):
            self._log_tail_job = self.root.after(60, self._日志尾巴步)
        else:
            if not self._是模拟运行():
                self._flip_progress(1.0)

    # ---------- 扫描相关 ----------
    def action_scan_mod_diff(self):
        # 差异窗口已经开着就直接叫回来（Qt 子进程 / 进程内 Qt / Tk 各判断一次）
        if self._qt_host_alive("diff"):
            self._send_qt_host_command("raise", kind="diff")
            return
        qt_diff = getattr(self, "diff_qt", None)
        if qt_diff is not None and qt_diff.is_alive():
            try:
                qt_diff.show()
                qt_diff.raise_()
                qt_diff.activateWindow()
            except Exception:
                pass
            return
        if hasattr(self,
                   'diff_window') and self.diff_window is not None and self.diff_window.winfo_exists():
            focus_window(self.diff_window)      # 已有就置顶
            return

        if self._scanning:
            return

        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        if not src or not tgt:
            messagebox.showerror("错误", "请先选择源和目标路径")
            return

        if Path(src).resolve() == Path(tgt).resolve():
            messagebox.showinfo("提示", "源目录和目标目录相同，无需比较。")
            return

        self._scanning = True
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="⏳ 扫描中…")
        self._refresh_busy_state()
        self.log("🔍 开始扫描模组差异，请稍候...", level="INFO")
        self.root.update_idletasks()

        total = 0
        if src:
            src_mods = Path(src) / "mods"
            if src_mods.exists():
                total += sum(1 for _ in src_mods.glob("*.jar"))
        if tgt:
            tgt_mods = Path(tgt) / "mods"
            if tgt_mods.exists():
                total += sum(1 for _ in tgt_mods.glob("*.jar"))
        self._scan_total = total

        self.scan_progress_window = ScanProgressWindow(self.root, total, self.theme)

        progress_queue = queue.Queue()
        self._scan_progress_queue = progress_queue

        def scan_task():
            try:
                data = scan_mod_differences(src, tgt, progress_queue, self._scan_total)
            except Exception as e:
                data = None
                error_msg = str(e)
            else:
                error_msg = None
            # 结果丢回队列，由主线程的轮询取走。**不要**在这里调 self.root.after()：
            # tkinter 明确声明 Tk 不是线程安全的；实测事件循环不是 mainloop 时
            # （验证脚本用 update() 泵的那种）会抛 `main thread is not in main loop`，
            # 扫描结果直接丢掉。迁移进度用的是同一条队列路线（见 _run_migration_thread）。
            progress_queue.put((_SCAN_DONE, data, error_msg))

        self._poll_scan_progress()
        threading.Thread(target=scan_task, daemon=True).start()

    # ---------- 后台线程 → 主线程的 UI 通道 ----------
    def _ui_post(self, fn, *args):
        """从**任意线程**请求主线程执行一段 UI 代码（线程安全：只碰队列）。

        为什么不直接用 `root.after`：tkinter 的文档明确说 Tk 不是线程安全的。实测在
        "事件循环不是 mainloop"的场合（验证脚本用 update() 泵的那种）从 worker 调
        `after` 会抛 `main thread is not in main loop`，那一句日志 / 那次收尾就没了
        （`log()` 里原来那段 RuntimeError 兜底注释说的就是这件事）。迁移进度本来就是
        "队列 + 主线程轮询"，这里把日志和迁移收尾也统一到这条路上。
        """
        self._ui_calls.put((fn, args))

    def _ui_pump(self):
        """主线程：执行后台线程排下的 UI 调用，然后继续等下一批。

        **每轮都有预算**（条数 + 耗时）。迁移时后台一秒能塞几百条日志进来，要是
        一轮就把队列清空，主线程会被几百次"插文字 + 重绘"按住 —— 看门狗抓到过
        21.45 秒的未响应（栈：_ui_pump → mw_log._log → update_idletasks）。
        超预算的一律留给下一轮：积压几秒的日志，好过窗口假死。
        """
        队列 = getattr(self, "_ui_calls", None)
        # 队列空的时候**不能**开始批次：开始会置 _批处理中 = True，而没有条目时
        # finally 里那句 `if 已处理:` 不会执行 —— 标志就一直挂着，之后主线程自己
        # 逐条写日志（root.after(0, _log) 那条路）全被当成"批内"，不修剪、不跟随
        # 到底部（实测：锁屏日志涨到 900 行还停在顶部）。
        if 队列 is not None and not 队列.empty():
            起 = time.perf_counter()
            已处理 = 0
            # 告诉日志：这一批里别逐条 configure / see / 修剪，整批完了统一收尾。
            # 锁屏态实测过：2500 条日志逐条走这套，最长一次卡 357.7ms、30 秒排不完。
            try:
                self._日志批次开始()
            except Exception:
                self._批处理中 = True
            try:
                while 已处理 < _UI_PUMP_MAX_ITEMS:
                    try:
                        fn, args = 队列.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        fn(*args)
                    except Exception:
                        pass
                    已处理 += 1
                    if (time.perf_counter() - 起) * 1000.0 >= _UI_PUMP_BUDGET_MS:
                        break
            finally:
                if 已处理:
                    try:
                        self._日志批次结束()          # 修剪 + see + 恢复只读 + 刷一次
                    except Exception:
                        self._批处理中 = False
                        try:
                            self.root.update_idletasks()
                        except Exception:
                            pass
        try:
            self._ui_pump_id = self.root.after(_UI_PUMP_MS, self._ui_pump)
        except Exception:
            self._ui_pump_id = None

    def _poll_scan_progress(self):
        try:
            while True:
                msg = self._scan_progress_queue.get_nowait()
                if msg is None:
                    # 扫描器报完进度会放一个 None（"进度到头了"）：关掉进度窗，
                    # 但**不能就此停止轮询** —— 结果还在后头（扫描线程跑完才放进队列）。
                    if hasattr(self, 'scan_progress_window'):
                        self.scan_progress_window.close()
                        delattr(self, 'scan_progress_window')
                    break
                if isinstance(msg, tuple) and msg[:1] == (_SCAN_DONE,):
                    # 扫描线程把结果交回来了（这里已经是主线程）—— 收尾后停止轮询，
                    # 下次扫描会重新起这个轮询，别让它空转一辈子。
                    self._finish_scan(msg[1], msg[2])
                    return
                current, filename = msg
                total = getattr(self, '_scan_total', 0)
                if total > 0:
                    self.scan_btn.itemconfig(self.scan_btn.text_id,
                                             text=trp("⏳ 解析中 ({0}/{1})", current, total))
                else:
                    self.scan_btn.itemconfig(self.scan_btn.text_id, text="⏳ 解析中...")
                if hasattr(self, 'scan_progress_window'):
                    self.scan_progress_window.update_progress(current, filename)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_scan_progress)

    def _finish_scan(self, data, error_msg):
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="🔍 扫描模组差异")
        self._scanning = False
        self._refresh_busy_state()

        if hasattr(self, 'scan_progress_window'):
            self.scan_progress_window.close()
            delattr(self, 'scan_progress_window')

        if error_msg:
            self.log(trp("❌ 扫描出错: {0}", error_msg), level="ERROR")
            if self._in_tray():
                self._notify_task_done("扫描模组差异", trp("扫描出错：{0}", error_msg))
            else:
                messagebox.showerror("扫描错误", trp("扫描过程中发生异常：{0}", error_msg))
            return

        if data is None:
            return

        if not data:
            self.log("📊 扫描完成：无差异", level="INFO")
            if self._in_tray():
                self._notify_task_done("扫描模组差异", trp("两个 mods 目录完全一致，没有差异"))
            else:
                messagebox.showinfo("提示", "两个 mods 目录完全一致，没有任何差异。")
            return

        self.log(trp("📊 扫描完成，发现 {0} 项差异", len(data)), level="SUCCESS")

        def apply_callback(selected_files):
            self.mod_text.configure(state=tk.NORMAL)
            self.mod_text.delete(1.0, tk.END)
            self.mod_text.insert(tk.END, "\n".join(selected_files))
            self.mod_text.edit_reset()
            self._update_text_states()
            self.log(trp("✅ 从差异扫描中导入了 {0} 个模组", len(selected_files)), level="SUCCESS")
            self.save_config()
            self._notify_modlist_change()

        # 调用 show_diff_window 并保存窗口引用
        if self._in_tray():
            # 窗口挂在托盘里：别突然弹出这个窗口，先压着，等叫回主界面再开
            self._pending_diff = (data, apply_callback)
            self._notify_task_done("扫描模组差异",
                                   trp("发现 {0} 项差异，点托盘图标查看", len(data)))
            return
        self._open_diff_window(data, apply_callback)

    # ---------- 迁移 ----------
    def _busy_task_name(self):
        """当前正在跑的文件类任务名；没有就返回 None。

        迁移、扫描差异、检查存在性、导入变更日志、回滚、大窗口检测都算 —— 它们都会碰文件，
        同时跑就可能互相踩（比如迁移正复制文件时又去回滚）。
        """
        if self._migration_running:
            return "迁移"
        if self._scanning:
            return "扫描模组差异"
        return getattr(self, "_file_task", None)

    def _begin_file_task(self, name):
        """登记一个文件类任务：期间迁移按钮会变灰，start_migration 也会直接拒绝。"""
        self._file_task = name
        self._refresh_busy_state()

    def _end_file_task(self):
        self._file_task = None
        self._refresh_busy_state()

    def _flip_start_btn(self, front=True, 结束后=None):
        """把「开始迁移」按钮翻回正面（front=False 则翻到背面）。

        组件见 ui/flip_button.py。旧按钮（helpers.create_gradient_button）没有这
        两个方法 —— 翻转组件构造失败时会降级回旧按钮，所以这里全程 getattr 兜着。
        """
        btn = getattr(self, "start_btn", None)
        if front:
            # 翻回正面时无条件复原背面样式：黄色/满条/⚠️ 文案都是"干活中"的临时态，
            # 不复原的话第一次跑完按钮就永远停在黄面 ⚠️ 上（用户 m09684 报的故障）。
            try:
                btn.set_back_warn(False)
                btn.set_bar_off(False)
                btn.set_back_progress(None)
            except Exception:
                pass
            self._flip_back_text(None)
        方法 = getattr(btn, "flip_to_front" if front else "flip_to_back", None)
        if 方法 is None:
            if 结束后 is not None:
                try:
                    结束后()
                except Exception:
                    pass
            return
        try:
            if 结束后 is not None and front:
                方法(结束后=结束后)
            else:
                方法()
        except Exception:
            if 结束后 is not None:
                try:
                    结束后()
                except Exception:
                    pass

    # ---------- 开始前倒计时 ----------
    def _countdown_seconds(self):
        """设置里的倒计时秒数（1~10）。0 = 关掉，点了就直接开跑。"""
        try:
            return max(0, min(10, int(getattr(self, "start_countdown", 5))))
        except Exception:
            return 3

    def _flip_back_text(self, text=None):
        """换背面文案（倒计时用）；旧按钮没有这个方法，getattr 兜着。"""
        btn = getattr(self, "start_btn", None)
        方法 = getattr(btn, "set_back_text", None)
        if 方法 is None:
            return
        try:
            方法(text)
        except Exception:
            pass

    def _是模拟运行(self):
        """模拟运行不落盘，进度是"假装"的 —— 这条数不值得往条上推。"""
        try:
            return bool(self.dry_run.get())
        except Exception:
            return False

    def _条真进度(self, value):
        """模拟运行阶段不接数值：按用户要求，这段时间条只做随机滚动。

        注意不是"推 None"，而是调用点直接跳过 —— 反复 set_back_progress(None) 会把
        `_prog_show` 和历史一遍遍清掉，条看着就一顿一顿（像在闪）。
        """
        return None if self._是模拟运行() else value

    def _flip_progress(self, value=None):
        """背面那条 3px 条：0..1 = 真进度，None = 默认的来回扫。"""
        btn = getattr(self, "start_btn", None)
        方法 = getattr(btn, "set_back_progress", None)
        if 方法 is None:
            return
        try:
            方法(value)
        except Exception:
            pass

    def _close_child_windows(self):
        """关掉主窗口以外所有真子窗口（设置 / 差异 / 历史 / 大视图 / 进度窗…）。

        overrideredirect 的那几个不算：吐司、桌面浮标、锁屏遮罩都没有标题栏，
        浮标更是要一直挂在桌面上（迁移期间也得在），关掉它等于关掉一个功能。
        """
        try:
            import tkinter as tk
            子窗 = [w for w in self.root.winfo_children()
                    if isinstance(w, tk.Toplevel)]
        except Exception:
            return
        for w in 子窗:
            try:
                if int(w.overrideredirect()):
                    continue
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass

    def _warn_file_task_busy(self, task):
        """别的文件任务在跑时的统一拦截（倒计时前 / 真正开跑前都用这一份）。"""
        messagebox.showwarning(
            "提示",
            trp("正在执行「{0}」，为避免两个任务同时改动同一批文件，请等它结束后再开始迁移（模拟运行同样需要等待）。", task))
        self.log(trp("⚠️ 已拦截：{0} 进行中，暂不允许启动迁移", task),
                 level="WARNING")

    def start_migration(self):
        """开始迁移（按钮入口）。

        点下去先翻到背面倒数几秒（设置里可调 1~10 秒，模拟运行同样），
        数完才走真正的准备+执行逻辑；倒计时期间再点一下按钮就取消。
        为什么要倒计时：翻转本身就是"已经开始"的信号，误点一次没有回头路，
        只能等它跑完再回滚 —— 留几秒后悔时间比事后回滚便宜得多。
        """
        if self._migration_running or getattr(self, "_starting", False):
            self.log("⚠️ 已有迁移在进行中（或正在准备），这次点击已忽略",
                     level="WARNING", save=False)
            return
        # 倒计时期间再点一下 = 取消（这时它是唯一亮着的按钮）
        if getattr(self, "_countdown_running", False):
            self._cancel_start_countdown()
            return
        # 别的文件任务在跑就别先倒数几秒再弹警告了，当下拦住
        task = self._busy_task_name()
        if task:
            self._warn_file_task_busy(task)
            self._flip_start_btn()      # 点击时已经翻到背面，这里翻回来
            return
        # 自检放在倒计时之前：有问题当场说，别让用户先等几秒再被拦下
        if not self._precheck_before_start():
            self._flip_start_btn()      # 点下去已经翻到背面，被拦下就翻回来
            self._refresh_busy_state()
            return
        秒 = self._countdown_seconds()
        if 秒 <= 0:
            self._run_migration_entry()
            return
        self._begin_start_countdown(秒)

    def _按钮待确认(self, 是):
        """等用户做选择（自检弹窗开着）时：满条 + 黄面 + 「⚠️ 等待……」文案。

        用户 m09734：「这个进度条别动了，让它占满，而且文本改成等待……」。
        """
        btn = getattr(self, "start_btn", None)
        try:
            if 是:
                btn.flip_to_back(force=True)
                btn.set_back_warn(True)          # 黄面 + 满条（长度不动）
                btn.set_back_text(trp("⚠️ 等待……"))
            else:
                btn.set_back_text(None)
                btn.set_back_warn(False)
                self._flip_start_btn()           # 回到正面等下一步
            btn.update_idletasks()
        except Exception:
            pass

    def _precheck_before_start(self):
        """迁移前自检：路径/权限/重叠/清单一致性。返回 False 表示这次不该往下走。

        只看"便宜且确定"的东西（列目录、stat、试写一个临时文件），不读 jar、不算总大小。
        失败项直接拦下；只有提醒时问一句，用户点继续照样能跑。
        """
        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        world = self.world_name.get().strip()
        if not src or not tgt:
            return True          # 路径都没填：交给正式流程那几条校验去报错，这里别抢
        try:
            modlist = [x.strip() for x in self.mod_text.get(1.0, tk.END).splitlines()
                       if x.strip() and not x.strip().startswith("#")]
            configlist = [x.strip() for x in self.config_text.get(1.0, tk.END).splitlines()
                          if x.strip() and not x.strip().startswith("#")]
            extralist = [x.strip().replace("\\", "/").rstrip("/")
                         for x in self.extra_text.get(1.0, tk.END).splitlines()
                         if x.strip() and not x.strip().startswith("#")]
        except Exception as e:
            trace_exc("main_window/自检读清单", e)
            return True

        self.log("🔎 开始迁移前自检…", level="INFO")
        try:
            结果 = precheck.run_precheck(src, tgt, world, modlist, configlist,
                                         extralist, self.overwrite_mods.get(),
                                         self.extra_conflict.get() != "skip")
        except Exception as e:
            # 自检自己炸了不该挡住迁移：记一笔，放行
            trace_exc("main_window/迁移前自检", e)
            self.log(trp("⚠️ 自检没跑完（{0}），已跳过自检继续", e), level="WARNING")
            return True

        错误 = [c["text"] for c in 结果["items"] if c["level"] == "fail"]
        提醒 = [c["text"] for c in 结果["items"] if c["level"] == "warn"]
        提示 = [c["text"] for c in 结果["items"] if c["level"] == "info"]
        for 文 in 错误:
            self.log("❌ " + 文, level="ERROR")
        for 文 in 提醒:
            self.log("⚠️ " + 文, level="WARNING")
        for 文 in 提示:
            self.log("ℹ️ " + 文, level="INFO")

        if 错误:
            self.log(trp("⛔ 自检没通过（{0} 项错误），已拦下，没有动任何文件", len(错误)),
                     level="ERROR")
            self._按钮待确认(True)
            try:
                from ui import precheck_dialog as _pd
                _pd.ask_errors(self.root, self.theme, 错误[:12])
            except Exception as _e:
                trace_exc("main_window/自检错误弹窗", _e)
                messagebox.showerror(
                    trp("迁移前自检没通过"),
                    trp("下面这些不解决，迁移跑不起来：") + NL + NL
                    + NL.join("· " + x for x in 错误[:8]))
            finally:
                self._按钮待确认(False)
            return False
        if 提醒:
            self.log(trp("⚠️ 自检有 {0} 项提醒，等用户确认", len(提醒)), level="WARNING")
            self._按钮待确认(True)
            try:
                from ui import precheck_dialog as _pd
                继续 = bool(_pd.ask_warnings(self.root, self.theme, 提醒[:12]))
            except Exception as _e:
                trace_exc("main_window/自检提醒弹窗", _e)
                继续 = bool(messagebox.askyesno(
                    trp("迁移前自检有提醒"),
                    trp("这些不影响运行，但结果可能不是你要的：") + NL + NL
                    + NL.join("· " + x for x in 提醒[:8]) + NL + NL
                    + trp("还是要现在开始吗？"),
                    icon="warning", default="yes"))
            finally:
                self._按钮待确认(False)
            if not 继续:
                self.log(trp("ℹ️ 你选择了「先不开始」，这次没有迁移任何文件"),
                         level="INFO")
            return 继续
        self.log(trp("✅ 自检通过（{0} 项提示）", len(提示)), level="INFO")
        return True

    # 读到 0 秒后停留多久（毫秒）。留足一档，肉眼能看清，又不至于像卡住。
    _倒计时零秒ms = 900

    def _begin_start_countdown(self, 秒):
        self._countdown_running = True
        self._countdown_total = float(秒)
        self._countdown_end = time.perf_counter() + self._countdown_total
        # 读到 0 秒之后再停这么久才真正开跑：3/2/1 之后得看见 0，不能直接跳
        self._countdown_zero_at = None
        self._countdown_job = None
        self._countdown_txt = None
        self._refresh_busy_state()      # 别的按钮先灰掉
        self._close_child_windows()     # 子窗口全关（设置窗/差异窗/大视图）
        self._flip_start_btn(front=False)
        # 倒计时是"沙漏"：满格起步往回收（用户要的就是这个，别把它藏起来）
        try:
            self.start_btn.set_bar_off(False)
        except Exception:
            pass
        self._flip_progress(1.0)
        self.log(trp("⏳ 开始前倒计时 {0} 秒：期间再点一下「开始迁移」可取消", 秒),
                 level="INFO")
        self._countdown_tick()

    def _countdown_tick(self):
        if not getattr(self, "_countdown_running", False):
            return
        剩 = self._countdown_end - time.perf_counter()
        if 剩 <= 0:
            # 0 秒也当成一档：先把「0 秒后开始」摆出来停一会儿，再进下一步
            if self._countdown_zero_at is None:
                self._countdown_zero_at = time.perf_counter()
                文案 = trp("⏳ {0} 秒后开始", 0)
                self._countdown_txt = 文案
                self._flip_back_text(文案)
                # 0 秒时条已经空了 —— 直接藏掉，别留一条 0 宽的细线在左边
                self._flip_progress(None)
            elif time.perf_counter() - self._countdown_zero_at >= self._倒计时零秒ms / 1000.0:
                self._finish_start_countdown()
                return
            self._countdown_job = self.root.after(16, self._countdown_tick)
            return
        总 = self._countdown_total or 1.0
        秒数 = max(1, int(剩 + 0.9999))
        # 倒计时 = 沙漏：条从满格往回收（1 → 0），数到 0 收空；
        # 关键是拿"屏幕上显示的那个秒数"算条宽 —— 用 剩/总 会差一整格：
        # 剩 = 2.99 秒时文字已经写"3 秒"了，条却只有 60%。
        self._flip_progress(max(0.0, min(1.0, 秒数 / 总)))
        文案 = trp("⏳ {0} 秒后开始", 秒数)
        if 文案 != self._countdown_txt:
            self._countdown_txt = 文案
            self._flip_back_text(文案)
        # 16ms：条单独画了之后刷新很便宜，25 次/秒看着会一格一格跳
        self._countdown_job = self.root.after(16, self._countdown_tick)

    def _finish_start_countdown(self):
        self._countdown_job = None
        self._countdown_running = False
        self._countdown_txt = None
        self._flip_back_text(None)
        try:
            self.start_btn.set_back_warn(False)   # 先复原成红面，下面按模拟/真跑再切
        except Exception:
            pass
        # 倒计时收在 0，干活段从 0 接着长（真迁移由进度队列推真值；
        # 模拟运行不推数值，条自己滚动）。
        # 数完了：真跑从 0 接着长；模拟运行不接数值（推 None 让条回到"滚动"，
        # 否则 override 停在 0.0，条就是一条 0 宽的线 —— 看着像不动了）
        try:
            self.start_btn.set_bar_off(False)
        except Exception:
            pass
        # 干活段统一换成"黄色 + 满进度条 + ⚠ 文案"（用户 m09602 定的样式）。
        # 真迁移也用它 —— 精确进度在进度窗口里，按钮上只表达"正在跑、别动"。
        try:
            self.start_btn.set_back_warn(True)
        except Exception:
            pass
        self._flip_back_text(trp("⚠️ 模拟中…") if self._是模拟运行()
                             else trp("⚠️ 迁移中…"))
        self._flip_progress(self._条真进度(0.0))
        self._run_migration_entry()

    def _cancel_start_countdown(self):
        """倒计时期间再点一下：撤销，什么都不动。"""
        if self._countdown_job is not None:
            try:
                self.root.after_cancel(self._countdown_job)
            except Exception:
                pass
        self._countdown_job = None
        self._countdown_running = False
        self._countdown_txt = None
        self._flip_back_text(None)
        self._flip_progress(None)
        self._flip_start_btn()
        self._refresh_busy_state()
        self.log(trp("ℹ️ 已取消：倒计时期间又点了一下「开始迁移」，没有动任何文件。"),
                 level="INFO")

    def _run_migration_entry(self):
        """真正开始（倒计时数完 / 倒计时关掉时直接走这里）。

        两段式：先挡住重入、立刻把按钮变灰，再走原来的准备+执行逻辑。
        为什么必须这样：准备阶段（校验清单 / 统计文件大小 / 磁盘检查）可能要几秒，
        期间只要出现过弹窗（messagebox 会开嵌套事件循环），排队的那次点击就会被派发，
        而那时 _migration_running 还没置位 —— 于是一次点击变两次迁移。
        """
        self._starting = True
        # 立刻禁用：既是视觉反馈，也让"点了没反应"变成"按钮本来就是灰的"
        try:
            self.start_btn.set_inert(True)
        except Exception:
            pass
        try:
            self._start_migration_locked()
        finally:
            self._starting = False
            if not self._migration_running:
                # 提前返回（校验没过/磁盘不足/备份失败…）：把按钮恢复
                self._flip_start_btn()      # 点击时它已经自己翻到背面了，这里翻回来
                self._refresh_busy_state()

    def _start_migration_locked(self):
        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，请勿重复启动")
            return
        # 别的文件任务在跑时禁止迁移：模拟运行也禁（它同样会读整份清单/校验路径，
        # 而且用户容易把"模拟"当成安全的并行操作，实际它和真迁移共用同一套流程）。
        task = self._busy_task_name()
        if task:
            self._warn_file_task_busy(task)
            return

        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        world = self.world_name.get().strip()
        if not src or not tgt:
            messagebox.showerror("错误", "请选择源和目标实例根目录")
            return
        if not world:
            messagebox.showerror("错误", "请输入存档名称")
            return

        src_path = Path(src)
        tgt_path = Path(tgt)
        if not src_path.exists():
            messagebox.showerror("错误", trp("源路径不存在：{0}", src))
            return
        if not tgt_path.exists():
            messagebox.showerror("错误", trp("目标路径不存在：{0}", tgt))
            return

        modlist_raw = self.mod_text.get(1.0, tk.END).splitlines()
        modlist = [line.strip() for line in modlist_raw if line.strip() and not line.strip().startswith("#")]

        configlist_raw = self.config_text.get(1.0, tk.END).splitlines()
        configlist = []
        for line in configlist_raw:
            line = line.strip()
            if line and not line.startswith("#"):
                if _is_safe_path(line):
                    configlist.append(line)
                else:
                    self.log(trp("⚠️ 跳过不安全 config 路径: {0}", line), level="WARNING")
                    messagebox.showwarning("不安全路径",
                                           trp("Config 清单中的 '{0}' 包含 '..'，已自动跳过。", line))

        # 其它文件清单：路径相对整合包根目录，安全校验和 config 一样
        extralist_raw = self.extra_text.get(1.0, tk.END).splitlines()
        extralist = []
        for line in extralist_raw:
            line = line.strip().replace("\\", "/")
            if line and not line.startswith("#"):
                if _is_safe_path(line):
                    extralist.append(line.rstrip("/") or line)
                else:
                    self.log(trp("⚠️ 跳过不安全的其它文件路径: {0}", line), level="WARNING")

        if not modlist and not configlist and not extralist:
            messagebox.showwarning("提示", "三个清单都是空的，没有可迁移的内容。")
            return

        # 「其它文件」清单是**唯一依据**：以前这里会把「默认携带的目录」自动并进这次
        # 迁移的临时清单（不写回用户清单），用户要求"一切都要自己选" —— 那套删了。
        # 想带常用目录，去清单页用「＋ 常用目录」加进清单：看得见、能删、有备份策略。

        # 计算要复制的文件数与总大小
        total_files, total_size = self._calculate_migration_stats(
            src_path, tgt_path, world, modlist, configlist, extralist)
        self.log(trp("📦 待迁移文件 {0} 个，总大小 {1:.1f} MB", total_files, total_size / 1024 / 1024),
                 level="INFO")

        if total_files == 0:
            messagebox.showinfo("提示", "没有找到需要复制的文件，请检查清单。")
            return

        # 磁盘空间检查（目标盘需容纳 迁移数据 + 目标备份，附带余量）
        ok, free, needed = self._check_disk_space(tgt_path, total_size)
        if free < 0:
            self.log("⚠️ 无法读取目标磁盘信息，已跳过空间检查", level="WARNING")
        elif not ok:
            messagebox.showerror(
                "磁盘空间不足",
                trp("目标磁盘剩余空间 {0:.1f} MB，本次迁移约需 {1:.1f} MB（含备份余量）。\n空间不足，请清理目标磁盘后重试。", free / 1024 / 1024, needed / 1024 / 1024))
            return

        # 正式迁移前再确认一次（模拟运行不用 —— 它不改任何文件）。设置里可以关掉。
        if not self.dry_run.get() and self.confirm_migrate.get():
            确认框 = _打桩优先("ask_migrate_confirm", ask_migrate_confirm)
            if not 确认框(self.root, self.theme, {
                    "源": src, "目标": tgt, "存档": world,
                    "模组": len(modlist), "config": len(configlist),
                    "其它文件": len(extralist), "文件数": total_files,
                    "大小MB": total_size / 1024 / 1024,
                    "覆盖模组": self.overwrite_mods.get(),
                    "其它文件冲突": self.extra_conflict.get()}):
                self.log(trp("ℹ️ 已取消：确认框里点了「取消」，没有动任何文件。"), level="INFO")
                return

        # 置为"迁移中"，禁用相关按钮、并给主窗口上锁，防止重复触发/误操作
        self._migration_running = True
        self._clear_fail_marks()          # 新一次迁移，先把上次的红标和失败列表清掉
        self._mig_t0 = time.time()        # 完成态总结要算耗时
        self._mig_plan = (len(modlist), len(configlist), len(extralist))
        self._refresh_busy_state()
        self._lock_main_window(trp("正在执行迁移任务") if not self.dry_run.get()
                              else trp("正在执行迁移任务（模拟运行）"))

        # 模拟模式
        if self.dry_run.get():
            # 模拟运行也开一条进度队列（不建进度窗口）：背面那条 3px 条就能跟着
            # 「处理到第几个文件」走，和日志是同一个节奏 —— 用户要的同步就是这个。
            try:
                _总数, _总量 = self._calculate_migration_stats(
                    src_path, tgt_path, world, modlist, configlist, extralist)
                self.progress_queue = queue.Queue()
                self._mig_total_size = float(_总量 or 0)
                if self.after_id is None:
                    self._poll_progress()
            except Exception as e:
                trace_exc("main_window/模拟运行进度队列", e)
                self.progress_queue = None
            self.log("========== 开始迁移（模拟） ==========", level="INFO")
            self.log(trp("旧版目录（源）: {0}", src), level="INFO")
            self.log(trp("新版目录（目标）: {0}", tgt), level="INFO")
            self.log(trp("存档名称: {0}", world), level="INFO")
            self.log("模拟模式: 是", level="INFO")
            self.log("不会实际修改任何文件", level="INFO")
            thread = threading.Thread(
                target=self._run_migration_thread,
                args=(src_path, tgt_path, world, modlist, configlist, True,
                      self.overwrite_mods.get(), extralist,
                      self.extra_conflict.get() != "skip", None)
            )
            thread.daemon = True
            thread.start()
            return

        # 实际迁移：先备份。**要把这次迁移的「其它文件」清单一起传进去** ——
        # 那些条目迁移时会被覆盖/新建，没备份的话"覆盖（先备份）"就是空话、回滚也还原不了
        #
        # 备份按"记录"分目录：先生成这次迁移的档案 id，备份存到 rec_<id>/ 下，
        # 迁移结束时那份档案也用同一个 id —— 于是历史窗口里每一条都能对应到自己的回滚点。
        try:
            from utils import history_store as _hs
            记录id = _hs.new_id()
        except Exception:
            记录id = None
        self._mig_record_id = 记录id
        try:
            _打桩优先("do_backup", do_backup)(
                tgt_path, log_func=self.log, extra_entries=extralist,
                record_id=记录id)
        except Exception as e:
            self.log(trp("❌ 备份失败：{0}", e), level="ERROR")
            messagebox.showerror("备份错误", trp("备份目标实例失败：{0}\n迁移已取消。", e))
            self._migration_running = False
            self._unlock_main_window()
            self._refresh_busy_state()
            return
        # 新备份已经落盘，旧版那一份单一备份就没用了（不然一直占着几百兆）
        try:
            清掉 = _打桩优先("prune_legacy_backup", prune_legacy_backup)(
                tgt_path, log_func=self.log)
            if 清掉:
                self.log(trp("🧹 已清理旧版留下的单一备份（{0} 项）", 清掉), level="INFO")
        except Exception as e:
            trace_exc("main_window/清理旧备份", e)

        if self.silent_background:
            # 后台静默执行：不弹进度窗口，也不建队列/轮询，全过程只写日志
            self.progress_queue = None
            self.progress_window = None
            self.log("🤫 后台静默执行已开启：不显示进度窗口，完成后用系统通知提醒",
                     level="INFO")
            self._silent_notify_on_finish = True
        else:
            self.progress_queue = queue.Queue()
            # 背面那条 3px 条按真实进度走，换算要用到总量
            self._mig_total_size = float(total_size or 0)
            self.progress_window = ProgressWindow(self.root, total_files, total_size,
                                              theme=self.theme)
            if self.after_id is None:
                self._poll_progress()

        thread = threading.Thread(
            target=self._run_migration_thread,
            args=(src_path, tgt_path, world, modlist, configlist, False,
                  self.overwrite_mods.get(), extralist,
                  self.extra_conflict.get() != "skip", 记录id)
        )
        thread.daemon = True
        thread.start()

    def _calculate_migration_stats(self, src_path, tgt_path, world, modlist,
                                   configlist, extralist=None):
        total_files = 0
        total_size = 0

        src_mods = src_path / "mods"
        if src_mods.exists():
            source_files = {f.name: f for f in src_mods.glob("*.jar")}
            for mod in modlist:
                matched = match_mod(mod, source_files, {})
                if matched:
                    total_files += 1
                    total_size += (src_mods / matched).stat().st_size

        src_world = src_path / "saves" / world
        if src_world.exists():
            for f in src_world.rglob("*"):
                if f.is_file():
                    total_files += 1
                    total_size += f.stat().st_size

        src_config = src_path / "config"
        for entry in configlist:
            src_entry = src_config / entry
            if src_entry.is_file():
                total_files += 1
                total_size += src_entry.stat().st_size
            elif src_entry.is_dir():
                for f in src_entry.rglob("*"):
                    if f.is_file():
                        total_files += 1
                        total_size += f.stat().st_size

        # 其它文件清单：相对整合包根目录（shaderpacks/、options.txt、kubejs/ 这类）
        for entry in (extralist or []):
            src_entry = src_path / str(entry).replace("\\", "/")
            if src_entry.is_file():
                total_files += 1
                total_size += src_entry.stat().st_size
            elif src_entry.is_dir():
                for f in src_entry.rglob("*"):
                    if f.is_file():
                        total_files += 1
                        total_size += f.stat().st_size

        return total_files, total_size

    def _dir_size(self, path):
        """递归计算目录下所有文件大小（跳过迁移备份目录和符号链接，避免虚高/死循环）。

        以前用 rglob 把 .migrate_backup 也算进来：上一次迁移留下的备份可能比实例还大，
        于是 needed 虚高，明明空间够也会被判"空间不足"而拒绝执行。
        """
        total = 0
        try:
            for dirpath, dirnames, filenames in os.walk(Path(path)):
                dirnames[:] = [d for d in dirnames
                               if d not in (".migrate_backup", ".migrate_backup.tmp",
                                            ".migrate_backup.old", ".git")]
                for name in filenames:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, name))
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def _check_disk_space(self, tgt_path, total_size):
        """检查目标磁盘空间是否足够（迁移数据 + 目标备份，附加余量）。
        返回 (ok, free_bytes, needed_bytes)；无法读取磁盘时返回 (True, -1, -1)。"""
        try:
            free = shutil.disk_usage(str(tgt_path)).free
        except Exception:
            return True, -1, -1
        # 备份需容纳目标实例当前大小，迁移需写入 total_size；加 1.2 倍余量 + 100MB 缓冲。
        target_size = self._dir_size(tgt_path)
        needed = int((total_size + target_size) * 1.2) + 100 * 1024 * 1024
        return free >= needed, free, needed

    def _run_migration_thread(self, src_path, tgt_path, world, modlist, configlist,
                              dry_run, overwrite, extralist=None, extra_overwrite=True,
                              record_id=None):
        """迁移工作线程。

        overwrite / extra_overwrite 由主线程读好再传进来：Tk 变量只能在主线程碰，
        子线程直接 self.overwrite_mods.get() 会抛 "main thread is not in main loop"。
        """
        def progress_callback(file_index, file_name, copied_bytes, step=None):
            if file_index is None:
                # 这是迁移结束的哨兵：以前这里直接 return 把它吞了，_poll_progress
                # 永远等不到 None —— 进度窗口（还是 grab_set 的）跑完也不关，
                # 界面就那样卡在"迁移进度"上不收。
                if self.progress_queue:
                    self.progress_queue.put(None)
                return
            if self.progress_queue:
                self.progress_queue.put((file_index, file_name, copied_bytes, step))

        def check_cancel():
            return self.progress_window and self.progress_window.cancelled

        try:
            run_migration(
                src_path=src_path,
                tgt_path=tgt_path,
                world_name=world,
                modlist=modlist,
                configlist=configlist,
                dry_run=dry_run,
                overwrite=overwrite,
                progress_callback=progress_callback,
                log_callback=self.log,
                check_cancel=check_cancel,
                add_history=True,
                record_id=record_id,
                # 迁移标记：只改目标文件名，加载器认的是 jar 里的 modid，不受影响
                rename_marker=(self.rename_marker
                               if getattr(self, "rename_migrated_mods", False) else None),
                # 其它文件清单（相对整合包根目录）+ 同名冲突策略
                extralist=extralist,
                extra_overwrite=bool(extra_overwrite)
            )
        finally:
            # 先立"收尾中"标志，再翻 _migration_running：锁屏那个 250ms 的
            # _watch_migration_end 一看到"没在跑"就会立刻解锁，和下面这条
            # _ui_post(_finish_migration_lock) 抢 ——抢赢的那次用户就看不到
            # 完成态（他报的"完成迁移后锁屏界面直接退出"就是这个）。
            self._mig_finish_pending = True
            self._migration_running = False
            # 收尾交给"锁屏完成态"：写总结 → 边框红转绿 → 等用户按任意键。
            # 这里是**迁移线程**，不能直接碰 Tk —— 丢给主线程的 UI 泵执行。
            self._ui_post(self._finish_migration_lock)

            # 「迁移结束」这个标志要等**执行日志真的滚到底**再给：
            # 用户看的是那片日志，日志还在滚就把「开始迁移」翻回正面、按钮全部点亮，
            # 会觉得「我还没看完就结束了」。
            def _收尾():
                # 迁移结束（成功/失败/取消都走这里）：把「开始迁移」翻回正面
                if getattr(self, "_log_tail_job", None) is not None:
                    try:
                        self.root.after_cancel(self._log_tail_job)
                    except Exception:
                        pass
                    self._log_tail_job = None
                # 条先留着（满格），等翻面那一下放完再复位 —— 否则它在翻之前就
                # "啪"一下没了，正好卡在翻面那一帧，看着很突兀。
                self._ending_wait_log = False
                self._flip_start_btn(
                    结束后=lambda: self._flip_progress(None))
                self._refresh_busy_state()

            # 日志还没滚完的这段时间也要算「忙」：否则按钮先亮了、按钮却还在背面，
            # 看着像两个状态打架。
            self._ending_wait_log = True
            # 3 秒兜底：日志太长时不能一直把按钮按在背面（用户会当成卡死）。
            self._ui_post(lambda: self.when_log_bottom(_收尾, timeout=3.0))
            # 静默模式下没有进度窗口来宣布结束，这里补一条系统通知
            if getattr(self, "_silent_notify_on_finish", False):
                self._silent_notify_on_finish = False
                self._ui_post(lambda: self._notify_task_done(
                    "迁移", trp("后台静默执行已结束，点托盘图标查看日志"), force=True))

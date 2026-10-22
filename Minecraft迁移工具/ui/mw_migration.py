# ui/mw_migration.py
"""迁移流程：统计、磁盘检查、后台线程、进度、历史与回滚、差异扫描。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import os
import queue
import shutil
import threading
import time
import tkinter as tk
from core.migrator import (
    _is_safe_path, do_backup, do_restore, get_backup_path, load_history, mark_rollback,
    match_mod, run_migration,
)
from core.scanner import scan_mod_differences
from pathlib import Path
from tkinter import messagebox
from ui.dialogs import ProgressWindow, ScanProgressWindow, ask_migrate_confirm
from ui.mw_common import (
    _SCAN_DONE, _UI_PUMP_MS, _center_window, _file_task_lock, _grad_width,
)
from ui.virtual_table import VirtualTable
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


class _SmoothText(tk.Text):
    """日志框：滚轮走缓动，而不是一格一格地跳。

    Tk 的 Text 没有像素滚动（`yview units` 的单位就是"行"），所以这里按**行**
    做缓动：滚轮只改目标行号，然后每帧朝目标走一小段，看起来就是滑过去。
    目标按"当前顶部行"重新算，所以拖滚动条/按 PageDown 之后也不会漂。
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self._job = None
        self._target = 1
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

    def _anim(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

        def 帧():
            剩 = self._target - self._top()
            if abs(剩) <= 0:
                self._job = None
                return
            self.yview_scroll(1 if 剩 > 0 else -1, "units")
            self._job = self.after(16, 帧)

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
        self._imgs = {}
        self.canvas = tk.Canvas(self, bg=theme["bg"], highlightthickness=0, bd=0,
                                cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
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

    def _wheel(self, e):
        # 触控板/滚轮都给一个"步数"，然后交给缓动去追
        步 = int(e.delta / 120) or (1 if e.delta > 0 else -1)
        self.target = max(0.0, min(self._max_off(), self.target - 步 * px(56)))
        self._anim()

    def _anim(self):
        """滚动缓动：固定时长 220ms、easeOutCubic，跟手指"甩一下"的手感更接近。

        以前是"每帧走剩余距离的 35%"，滚动量大时会显得越滚越慢、拖尾很长；
        改成按时间插值后，无论滚多远都是同一个时长，停下来就停（不空转）。
        """
        起 = self.offset
        终 = self.target
        总 = px(220)

        def 帧(k):
            缓 = 1 - (1 - k) ** 3
            self.offset = 起 + (终 - 起) * 缓
            self.redraw()
            if k < 1.0:
                try:
                    self._job = self.after(16, lambda: 帧(min(1.0, k + 16.0 / 总)))
                except Exception:
                    self._job = None
            else:
                self.offset = 终
                self.redraw()
                self._job = None

        帧(0.0)

    # ---------- 绘制 ----------
    def _card_img(self, w, h, 状态, 条色="#2e7d32"):
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

    def redraw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or px(360)
        h = c.winfo_height() or px(400)
        弱色 = self.theme.get("muted_fg", "#888888")
        for i, rec in enumerate(self.records):
            y0 = self._top(i)
            y1 = y0 + px(self.CARD_H)
            if y1 < 0 or y0 > h:
                continue
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
            c.create_image(px(4), int(y0), anchor="nw", image=img)
            c.create_text(px(20), y0 + px(16), anchor="w", text=rec.get("time", "?"),
                          fill=self.theme["fg"], font=("微软雅黑", 10, "bold"))
            c.create_text(w - px(24), y0 + px(16), anchor="e",
                          text=("模拟" if 模式 == "dry" else "实际"),
                          fill=条, font=("微软雅黑", 9, "bold"))

            def _名(pth):
                段 = str(pth or "").replace("\\", "/").rstrip("/").split("/")
                return 段[-1] if 段 else "?"
            c.create_text(px(20), y0 + px(38), anchor="w",
                          text="%s → %s" % (_名(rec.get("source")), _名(rec.get("target"))),
                          fill=弱色, font=("微软雅黑", 9))
            统 = rec.get("stats") or {}
            m = (统.get("mods") or {}).get("success", 0)
            cf = (统.get("config") or {}).get("success", 0)
            ex = (统.get("extra") or {}).get("success", 0)
            c.create_text(px(20), y0 + px(60), anchor="w",
                          text="模组 %d　·　config %d　·　其它 %d" % (m, cf, ex),
                          fill=弱色, font=("微软雅黑", 9))
            c.create_text(px(20), y0 + px(78), anchor="w",
                          text="耗时 %s 秒　·　%s" % (rec.get("duration", "?"), 结果 or "—"),
                          fill=弱色, font=("微软雅黑", 8))

        total, hh = self._total(), h
        if total > hh:
            bar = max(px(24), int(hh * hh / total))
            top = int((hh - bar) * (self.offset / max(1.0, total - hh)))
            c.create_rectangle(w - px(6), top, w - px(3), top + bar,
                               fill=self.theme.get("muted_fg", "#888888"), outline="")


class MigrationMixin:
    """迁移流程：统计、磁盘检查、后台线程、进度、历史与回滚、差异扫描。"""

    # ---------- 历史记录 ----------
    # ---------- 迁移历史（档案形式） ----------
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
        顶.title("迁移历史")
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
        tk.Label(标题框, text="共 %d 份档案　·　最多保存 %d 份（可在设置里调整）　·　%s"
                 % (len(记录), 上限, history_store._root()),
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
            S = 3
            半径 = px(14) * S
            img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([0, 0, w * S - 1, h * S - 1], radius=半径,
                                fill=_面板 + (255,),
                                outline=_边框 + (255,),
                                width=S)
            背景图 = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
            _背景["img"] = 背景图
            右.delete("bg")
            右.create_image(0, 0, anchor="nw", image=背景图, tags="bg")
            右.tag_lower("bg")
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
        详情["源"] = _行(概览, "源目录", "")
        详情["目标"] = _行(概览, "目标目录", "")
        详情["统计"] = _行(概览, "统计", "")
        详情["耗时"] = _行(概览, "耗时", "")
        详情["失败"] = _行(概览, "失败", "")

        日志标题 = tk.Frame(内容, bg=面板色)
        日志标题.pack(fill="x", padx=px(14), pady=(px(10), px(2)))
        tk.Label(日志标题, text="执行日志", font=("微软雅黑", 9, "bold"),
                 bg=面板色, fg=self.theme["fg"], anchor="w").pack(side="left")
        详情["日志计数"] = tk.Label(日志标题, text="", font=("微软雅黑", 8),
                                    bg=面板色, fg=弱色, anchor="e")
        详情["日志计数"].pack(side="right")

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

        _滑动 = {"job": None, "当前": -1}

        def _滑入():
            """切换档案时让详情内容从右边滑进来。

            Tk 没有真透明，做不了淡入淡出；滑动是唯一看起来"有过渡"又不失真的做法。
            内容是 Canvas 里的一个 window item，只要改它的 x 坐标即可，没有重排开销。
            """
            try:
                if _滑动["job"]:
                    顶.after_cancel(_滑动["job"])
            except Exception:
                pass

            def 帧(k):
                缓 = 1 - (1 - k) ** 3
                dx = int(px(30) * (1 - 缓))
                try:
                    右.coords(_内窗, px(1) + dx, px(1))
                except Exception:
                    return
                if k < 1.0:
                    try:
                        _滑动["job"] = 顶.after(16, lambda: 帧(min(1.0, k + 16.0 / 170)))
                    except Exception:
                        _滑动["job"] = None
                else:
                    try:
                        右.coords(_内窗, px(1), px(1))
                    except Exception:
                        pass
                    _滑动["job"] = None

            帧(0.0)

        def 选中(i):
            变了 = (i != _滑动["当前"])
            列表.sel = i
            列表.redraw()
            显示(记录[i] if 0 <= i < len(记录) else None)
            if 变了:
                _滑动["当前"] = i
                _滑入()

        def 显示(rec):
            if not rec:
                详情["标题"].config(text="没有档案")
                详情["副标题"].config(text="")
                for k in ("源", "目标", "统计", "耗时", "失败"):
                    详情[k].config(text="—")
                详情["日志"].config(state="normal")
                详情["日志"].delete("1.0", "end")
                详情["日志"].config(state="disabled")
                return
            模式 = "模拟运行" if rec.get("mode") == "dry" else "实际迁移"
            结果 = rec.get("result") or ""
            色 = 好色 if 结果 == "成功" else (警色 if 结果 == "模拟运行" else 坏色)
            详情["标题"].config(text="%s　·　%s" % (rec.get("time", "?"), 模式))
            详情["副标题"].config(text=结果, fg=色)
            详情["源"].config(text=rec.get("source", "—"))
            详情["目标"].config(text=rec.get("target", "—"))
            统计 = rec.get("stats") or {}
            m = 统计.get("mods") or {}
            c = 统计.get("config") or {}
            e = 统计.get("extra") or {}
            详情["统计"].config(text="模组 %d（跳过 %d，失败 %d）　·　config %d（失败 %d）　·　其它 %d（跳过 %d，失败 %d）"
                                % (m.get("success", 0), m.get("skipped", 0), m.get("failed", 0),
                                   c.get("success", 0), c.get("failed", 0),
                                   e.get("success", 0), e.get("skipped", 0), e.get("failed", 0)))
            详情["耗时"].config(text="%s 秒" % rec.get("duration", "—"))
            失败 = (rec.get("lists") or {}).get("failed") or []
            if 失败:
                详情["失败"].config(text="%d 条（详见日志）" % len(失败), fg=坏色)
            else:
                详情["失败"].config(text="无", fg=好色)
            full = history_store.load_record(rec.get("id") or "") or rec
            日志 = full.get("log") or "（这份档案没有日志）"
            详情["日志"].config(state="normal")
            详情["日志"].delete("1.0", "end")
            详情["日志"].insert("1.0", 日志)
            详情["日志"].config(state="disabled")
            详情["日志计数"].config(text="%d 行" % (日志.count("\n") + 1))

        # ---- 底部按钮 ----
        底 = tk.Frame(顶, bg=self.theme["bg"])
        底.pack(fill="x", padx=px(18), pady=(0, px(12)))

        def 删当前():
            i = 列表.sel
            if not (0 <= i < len(记录)):
                return
            rec = 记录[i]
            if not messagebox.askyesno("删除档案",
                                       "确定删除 %s 这份档案吗？\n（只删记录，不影响任何游戏文件）"
                                       % rec.get("time", ""), parent=顶):
                return
            history_store.delete_record(rec.get("id") or "")
            记录.pop(i)
            列表.records = 记录
            列表.sel = min(i, max(0, len(记录) - 1))
            列表.redraw()
            显示(记录[列表.sel] if 记录 else None)
            详情["副标题"].config(text="已删除")

        删钮 = create_gradient_button(底, "删除这份档案", 删当前, colors=("#e53935", "#b71c1c"))
        删钮.pack(side="left")
        create_gradient_button(底, "关闭", lambda: 顶.destroy()).pack(side="right")

        if 记录:
            选中(0)
        else:
            显示(None)

        顶.protocol("WM_DELETE_WINDOW", 顶.destroy)
        try:
            apply_theme_to_widget_tree(顶, self.theme)
        except Exception:
            pass
        _center_window(顶, px(1040), px(660))
        顶.deiconify()

    def action_rollback(self):
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
            self.log("❌ 用户取消了回滚操作", level="WARNING")
            return

        self.log("✅ 用户确认回滚，开始执行...", level="SUCCESS")
        success = do_restore(tgt_path, log_func=self.log)
        if success:
            if mark_rollback(tgt_path):
                self.log("📝 已标记本次回滚到历史记录", level="INFO")
            else:
                self.log("ℹ️ 历史记录里没有可标记的迁移记录（可能已经回滚过了）", level="WARNING")
            messagebox.showinfo("回滚完成", "目标实例已恢复到迁移前的状态。")
        else:
            self.log("❌ 回滚操作失败，目标实例可能处于不完整状态，"
                     "备份仍然保留，可重新执行回滚", level="ERROR")
            messagebox.showerror("回滚失败",
                                 "回滚未能完整完成，详情见日志。\n"
                                 "备份目录仍然保留，可以再次尝试回滚。")
        self.log("=" * 50, level="INFO")

    # ---------- 进度轮询 ----------
    def _poll_progress(self):
        if self.progress_queue is not None and self.progress_window is not None:
            try:
                while True:
                    msg = self.progress_queue.get_nowait()
                    if msg is None:
                        self.progress_window.close()
                        self.progress_window = None
                        self.progress_queue = None
                        if self.after_id is not None:
                            self.root.after_cancel(self.after_id)
                            self.after_id = None
                        self._migration_running = False
                        self._unlock_main_window()      # 进度窗口关掉时同步解锁
                        self._notify_task_done(
                            "迁移", "任务已结束，点托盘图标打开主界面查看日志")
                        return
                    self.progress_window.update_progress(*msg)
            except queue.Empty:
                pass
            if self.progress_window and self.progress_window.cancelled:
                if self.progress_queue:
                    self.progress_queue.put(None)
            self.after_id = self.root.after(100, self._poll_progress)
        else:
            self.after_id = None

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
        """主线程：执行后台线程排下的 UI 调用，然后继续等下一批。"""
        队列 = getattr(self, "_ui_calls", None)
        if 队列 is not None:
            while True:
                try:
                    fn, args = 队列.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn(*args)
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
                self._notify_task_done("扫描模组差异", f"扫描出错：{error_msg}")
            else:
                messagebox.showerror("扫描错误", trp("扫描过程中发生异常：{0}", error_msg))
            return

        if data is None:
            return

        if not data:
            self.log("📊 扫描完成：无差异", level="INFO")
            if self._in_tray():
                self._notify_task_done("扫描模组差异", "两个 mods 目录完全一致，没有差异")
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
                                   f"发现 {len(data)} 项差异，点托盘图标查看")
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

    def start_migration(self):
        """开始迁移（按钮入口）。

        两段式：先挡住重入、立刻把按钮变灰，再走原来的准备+执行逻辑。
        为什么必须这样：准备阶段（校验清单 / 统计文件大小 / 磁盘检查）可能要几秒，
        期间只要出现过弹窗（messagebox 会开嵌套事件循环），排队的那次点击就会被派发，
        而那时 _migration_running 还没置位 —— 于是一次点击变两次迁移。
        """
        if self._migration_running or getattr(self, "_starting", False):
            self.log("⚠️ 已有迁移在进行中（或正在准备），这次点击已忽略",
                     level="WARNING", save=False)
            return
        self._starting = True
        # 立刻禁用：既是视觉反馈，也让"点了没反应"变成"按钮本来就是灰的"
        try:
            self.start_btn.state("disabled")
        except Exception:
            pass
        try:
            self._start_migration_locked()
        finally:
            self._starting = False
            if not self._migration_running:
                # 提前返回（校验没过/磁盘不足/备份失败…）：把按钮恢复
                self._refresh_busy_state()

    def _start_migration_locked(self):
        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，请勿重复启动")
            return
        # 别的文件任务在跑时禁止迁移：模拟运行也禁（它同样会读整份清单/校验路径，
        # 而且用户容易把"模拟"当成安全的并行操作，实际它和真迁移共用同一套流程）。
        task = self._busy_task_name()
        if task:
            messagebox.showwarning(
                "提示",
                trp("正在执行「{0}」，为避免两个任务同时改动同一批文件，请等它结束后再开始迁移（模拟运行同样需要等待）。", task))
            self.log(trp("⚠️ 已拦截：{0} 进行中，暂不允许启动迁移", task), level="WARNING")
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
                self.log("ℹ️ 已取消：确认框里点了「取消」，没有动任何文件。", level="INFO")
                return

        # 置为"迁移中"，禁用相关按钮、并给主窗口上锁，防止重复触发/误操作
        self._migration_running = True
        self._clear_fail_marks()          # 新一次迁移，先把上次的红标和失败列表清掉
        self._mig_t0 = time.time()        # 完成态总结要算耗时
        self._mig_plan = (len(modlist), len(configlist), len(extralist))
        self._refresh_busy_state()
        self._lock_main_window("正在执行迁移任务" if not self.dry_run.get()
                              else "正在执行迁移任务（模拟运行）")

        # 模拟模式
        if self.dry_run.get():
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
                      self.extra_conflict.get() != "skip")
            )
            thread.daemon = True
            thread.start()
            return

        # 实际迁移：先备份。**要把这次迁移的「其它文件」清单一起传进去** ——
        # 那些条目迁移时会被覆盖/新建，没备份的话"覆盖（先备份）"就是空话、回滚也还原不了
        try:
            _打桩优先("do_backup", do_backup)(
                tgt_path, log_func=self.log, extra_entries=extralist)
        except Exception as e:
            self.log(trp("❌ 备份失败：{0}", e), level="ERROR")
            messagebox.showerror("备份错误", trp("备份目标实例失败：{0}\n迁移已取消。", e))
            self._migration_running = False
            self._unlock_main_window()
            self._refresh_busy_state()
            return

        if self.silent_background:
            # 后台静默执行：不弹进度窗口，也不建队列/轮询，全过程只写日志
            self.progress_queue = None
            self.progress_window = None
            self.log("🤫 后台静默执行已开启：不显示进度窗口，完成后用系统通知提醒",
                     level="INFO")
            self._silent_notify_on_finish = True
        else:
            self.progress_queue = queue.Queue()
            self.progress_window = ProgressWindow(self.root, total_files, total_size)
            if self.after_id is None:
                self._poll_progress()

        thread = threading.Thread(
            target=self._run_migration_thread,
            args=(src_path, tgt_path, world, modlist, configlist, False,
                  self.overwrite_mods.get(), extralist,
                  self.extra_conflict.get() != "skip")
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
                              dry_run, overwrite, extralist=None, extra_overwrite=True):
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
                # 迁移标记：只改目标文件名，加载器认的是 jar 里的 modid，不受影响
                rename_marker=(self.rename_marker
                               if getattr(self, "rename_migrated_mods", False) else None),
                # 其它文件清单（相对整合包根目录）+ 同名冲突策略
                extralist=extralist,
                extra_overwrite=bool(extra_overwrite)
            )
        finally:
            self._migration_running = False
            # 收尾交给"锁屏完成态"：写总结 → 边框红转绿 → 等用户按任意键。
            # 这里是**迁移线程**，不能直接碰 Tk —— 丢给主线程的 UI 泵执行。
            self._ui_post(self._finish_migration_lock)
            self._ui_post(self._refresh_busy_state)
            # 静默模式下没有进度窗口来宣布结束，这里补一条系统通知
            if getattr(self, "_silent_notify_on_finish", False):
                self._silent_notify_on_finish = False
                self._ui_post(lambda: self._notify_task_done(
                    "迁移", "后台静默执行已结束，点托盘图标查看日志", force=True))

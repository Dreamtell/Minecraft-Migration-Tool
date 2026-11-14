# -*- coding: utf-8 -*-
"""执行日志右侧的总览条（VS Code minimap 那种）。

日志一滚就是几十屏，出错的几行往往在看不到的地方。这里把整份日志压成一条
十几像素宽的竖条：每条 ERROR / WARNING / SUCCESS / SIMULATE 在条上留一个色点
（位置 = 它在这份日志里的相对行号），中间半透明的框是当前可见的一段 ——
点一下、拖一下就能跳过去。

INFO 不画：占绝大多数，画满就成了一块灰条，反而看不出异常在哪。

只依赖 tkinter；主题跟着 register 时传进来的那份走，切主题调 `set_theme()`。
"""

import tkinter as tk

try:
    from ui.mw_common import _mix
except Exception:                                   # 单独跑模块时不至于炸
    def _mix(a, b, t):
        return a

# 一眼能分出来的四种，颜色取主题里的日志配色（和正文里那些行的字色一致）
_LEVELS = (("ERROR", "log_error_fg", "#e53935"),
           ("WARNING", "log_warning_fg", "#ff9800"),
           ("SUCCESS", "log_success_fg", "#43a047"),
           ("SIMULATE", "log_simulate_fg", "#1e88e5"))

_POLL_MS = 250          # 轮询间隔：日志是追加式的，没有稳定的事件可挂
_POLL_SLOW_MS = 800     # 日志特别长时放慢：_marks() 要把每种 tag 的范围都扫一遍
_TICK_H = 4             # 色点最小高度（像素）：2px 在长日志里细得看不见
_TICK_MAX = 9           # 同一像素行里压了好几条时最多长到这么高


class LogOverview(tk.Canvas):
    """日志总览条。`text` 是那个 Text 控件（只读也无所谓）。"""

    def __init__(self, parent, text, theme, width=14, **kw):
        bg = theme.get("bg", "#1e1e1e")
        kw.setdefault("bg", bg)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, width=width, **kw)
        self.text = text
        self.theme = dict(theme)
        self._sig = None            # (末行号, yview) —— 没变就不重画
        self._interval = _POLL_MS   # 当前轮询间隔（长日志会放慢）
        self._after = None
        self._drag = False
        self.configure(cursor="hand2")
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda _e: self._redraw(force=True))
        self._schedule()

    # ---------------- 生命周期 ----------------
    def destroy(self):
        if self._after is not None:
            try:
                self.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        super().destroy()

    def _schedule(self):
        if self._after is not None:
            return
        try:
            self._after = self.after(self._interval, self._poll)
        except Exception:
            self._after = None

    def _poll(self):
        self._after = None
        try:
            self._redraw()
        except tk.TclError:
            return                      # 窗口关掉了
        self._schedule()

    def set_theme(self, theme):
        self.theme = dict(theme)
        try:
            self.configure(bg=self.theme.get("bg", "#1e1e1e"))
        except Exception:
            pass
        self._redraw(force=True)

    # ---------------- 交互 ----------------
    def _frac_at(self, y):
        h = max(1, self.winfo_height())
        return max(0.0, min(1.0, float(y) / h))

    def _jump(self, y):
        try:
            self.text.yview_moveto(self._frac_at(y))
        except tk.TclError:
            pass

    def _on_press(self, e):
        self._drag = True
        self._jump(e.y)

    def _on_drag(self, e):
        if self._drag:
            self._jump(e.y)

    def _on_release(self, _e):
        self._drag = False

    # ---------------- 绘制 ----------------
    def _marks(self):
        """[(行号, tag)]：按 tag 的范围批量取，再换算成行号（比逐行查 tag 快得多）。"""
        out = []
        for tag, _key, _fallback in _LEVELS:
            try:
                rng = self.text.tag_ranges(tag)
            except tk.TclError:
                continue
            for i in range(0, len(rng), 2):
                try:
                    ln = int(str(self.text.index(rng[i])).split(".")[0])
                except (tk.TclError, ValueError):
                    continue
                out.append((ln, tag))
        return out

    def _redraw(self, force=False):
        try:
            end = int(str(self.text.index("end-1c")).split(".")[0])
            yv = self.text.yview()
        except (tk.TclError, ValueError):
            return
        sig = (end, round(yv[0], 4), round(yv[1], 4), self.winfo_height(), self.winfo_width())
        if not force and sig == self._sig:
            return
        self._sig = sig
        # 日志长了就放慢轮询：几万行的时候每 250ms 扫一遍 tag 范围纯属浪费
        self._interval = _POLL_SLOW_MS if end > 8000 else _POLL_MS
        self.delete("all")
        h = max(1, self.winfo_height())
        w = max(1, self.winfo_width())
        if end <= 0:
            return
        # 轨道：比底色略深/略浅一点点。竖条本身要有"存在的形状"，
        # 不然长日志里只剩几颗漂浮的色点，看着像画错了
        track = _mix(self.theme.get("bg", "#1e1e1e"),
                     self.theme.get("fg", "#ffffff"), 0.10)
        self.create_rectangle(0, 0, w, h, fill=track, outline="")
        # 色点：同一行只留优先级最高的一种（ERROR > WARNING > SUCCESS > SIMULATE）
        best = {}
        order = {tag: i for i, (tag, _k, _f) in enumerate(_LEVELS)}
        for ln, tag in self._marks():
            if ln not in best or order[tag] < order[best[ln]]:
                best[ln] = tag
        # 行号 → 像素行：长日志里一个像素行可能压着几十条，先归并再画。
        # 同一像素行叠了几条就把色点画高一点（最多 _TICK_MAX），这样"某一段密集报错"
        # 能一眼看出来；原来每条各画 2px，几千行的时候挤成一条看不见的虚线。
        桶 = {}
        for ln, tag in best.items():
            y = int(round((ln - 1) / max(1, end - 1) * (h - _TICK_H - 1)))
            cur = 桶.get(y)
            if cur is None:
                桶[y] = [tag, 1]
            else:
                cur[1] += 1
                if order[tag] < order[cur[0]]:
                    cur[0] = tag
        # 相邻像素行、同一种颜色 → 合并成一段连续的色块：几十条错误挤在同一段时，
        # 看起来是"一条红杠"而不是几颗互不相连的豆子
        行 = sorted(桶.items())
        段 = []
        for y, (tag, n) in 行:
            tall = min(_TICK_MAX, _TICK_H + n // 2)
            if 段 and 段[-1][1] == tag and y <= 段[-1][3]:
                段[-1][3] = max(段[-1][3], y + tall)
                段[-1][2] += n
            else:
                段.append([y, tag, n, y + tall])
        for y0, tag, _n, y1 in 段:
            key = next(k for t, k, _f in _LEVELS if t == tag)
            fallback = next(f for t, _k, f in _LEVELS if t == tag)
            self.create_rectangle(1, y0, w - 1, min(h, y1),
                                  fill=self.theme.get(key, fallback), outline="")
        # 可见区域框：Tk canvas 没有 alpha，用"底色→强调色"混出来的实色块代替，
        # 比 stipple 网点干净得多（网点在小尺寸下会变成一块噪点）
        top, bottom = float(yv[0]), float(yv[1])
        if bottom - top < 0.999:
            y0, y1 = top * h, bottom * h
            # 日志特别长时可见区只有几个像素，太小既看不见也抓不住：
            # 给一个最小高度（12px），并以原来的中心对齐
            if y1 - y0 < 12:
                中 = (y0 + y1) / 2.0
                y0, y1 = 中 - 6, 中 + 6
                if y0 < 0:
                    y0, y1 = 0, 12
                if y1 > h:
                    y0, y1 = h - 12, h
            底 = self.theme.get("bg", "#1e1e1e")
            强调 = self.theme.get("accent", "#4a90d9")
            框 = _mix(底, 强调, 0.45)
            self.create_rectangle(1, y0, w - 1, y1, fill=框, outline=强调)
            # 上下各一条实线，拖动时能看清抓到的是哪一段
            self.create_line(1, y0, w - 1, y0, fill=强调)
            self.create_line(1, y1, w - 1, y1, fill=强调)

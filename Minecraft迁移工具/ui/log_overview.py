# -*- coding: utf-8 -*-
"""执行日志右侧的总览条（VS Code minimap 那种）。

日志一滚就是几十屏，出错的几行往往在看不到的地方。这里把整份日志压成一条
十几像素宽的竖条：每条 ERROR / WARNING / SUCCESS / SIMULATE 在条上留一个色点，
中间的框是当前可见的一段 —— 点一下、拖一下就能跳过去。

色点的位置按**像素**算（和滚动条同一个标尺），不是按行号：日志里有折行的长行，
按行号摆的话，色点和"跳过去之后看到的内容"对不上（量过：点红点，7 个里 0 个能看到）。
点在色点上会直接对准那一条（长日志里 1px 对应几十行，光靠比例点不准）；
点在别处则让可见区的中心落在点击处。

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
_MIN_BOX = 12           # 可见区框的最小高度：日志很长时可见区只有几个像素，看不见也抓不住
_HIT_PAD = 2            # 点击离色点多近算"点在色点上"（像素）

LEVELS = _LEVELS        # 对外的名字：日志缩略图（ui/log_minimap.py）按同样四种级别上色


def marks_of(text, lo=None, hi=None):
    """[(行号, tag)]：按 tag 的范围批量取，再展开成逐行（比逐行查 tag 快得多）。

    Tk 把相邻的、同一个 tag 的文字合并成**一个**范围：连着 12 行 ERROR，
    `tag_ranges` 只返回 1 对（61.0 → 73.0）。只取起点的话，一整段连续报错在条上
    只剩 1 个点，"这里密集出错"就看不出来了，所以按范围的起止行号展开。
    lo / hi 只要这个行号区间（含两端）里的，缩略图只画一屏多一点时不必展开整份日志。
    """
    out = []
    for tag, _key, _fallback in _LEVELS:
        try:
            rng = text.tag_ranges(tag)
        except tk.TclError:
            continue
        for i in range(0, len(rng) - 1, 2):
            try:
                a = int(str(rng[i]).split(".")[0])
                b_idx = str(rng[i + 1])
                b = int(b_idx.split(".")[0])
                # 范围终点在某行行首（x.0）表示那一行本身不在范围里
                if b_idx.endswith(".0") and b > a:
                    b -= 1
            except (tk.TclError, ValueError):
                continue
            if lo is not None:
                a = max(a, lo)
            if hi is not None:
                b = min(b, hi)
            for ln in range(a, b + 1):
                out.append((ln, tag))
    return out


def tag_sig(text):
    """四种级别 tag 的首尾范围：日志满额后头部被截掉、尾部追加，行数和总高度都可能不变，
    但每个标记的行号整体前移了，只看行数的签名会认为"没变"，色点就停在旧位置。"""
    out = []
    for tag, _k, _f in _LEVELS:
        try:
            a = text.tag_nextrange(tag, "1.0")
            b = text.tag_prevrange(tag, "end")
        except tk.TclError:
            a = b = ()
        out.append((tuple(str(x) for x in a), tuple(str(x) for x in b)))
    return tuple(out)


def line_visible(text, ln):
    """第 ln 行现在在视口里吗（折行后只要有一部分露出来就算）。"""
    try:
        return text.dlineinfo("%d.0" % ln) is not None
    except tk.TclError:
        return False


def goto_line(text, ln):
    """把第 ln 行放到可见区中间偏上。

    不用 yview_moveto(比例)：Tk 的 Text 折行高度是后台慢慢量出来的，日志里折行多时
    比例在头一两秒里是估的，按比例跳会落偏。`yview("行号.0")` 是按位置定的，
    不受估算影响；再往上挪半个可见区（行比可见区还高就只到开头）。
    """
    try:
        text.yview("%d.0" % ln)
        视口 = max(1, text.winfo_height())
        info = text.dlineinfo("%d.0" % ln)
        行高 = min(info[3], 视口) if info else 18
        上移 = int(max(0, (视口 - 行高) / 2.0))
        if 上移:
            text.yview_scroll(-上移, "pixels")
    except tk.TclError:
        pass


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
        self._sig = None            # 上次全量重绘时的完整签名（测试/排查看的）
        self._body_sig = None       # 不含 yview 的签名：行数/总高/tag 范围/尺寸。没变 = 只可能是滚动
        self._interval = _POLL_MS   # 当前轮询间隔（长日志会放慢）
        self._after = None
        self._drag = False
        self._grab = 0.0            # 按下时抓住的位置相对"可见区中心"的偏移（占条高的比例）
        self._segs = []             # [(y0, y1, tag, [(y, 行号, tag)])]：上次画出来的色块，点击吸附用
        self._box = None            # 上次画出来的可见区框 (y0, y1)，没画（整份都看得见）是 None
        self._box_items = None      # 可见区框的 3 个 canvas 项（矩形 + 上下两条线），滚动时只改它们的坐标
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

    def _total_px(self):
        """整份日志折行后的总像素高；取不到（控件没映射等）返回 0。

        不加 `update`：加了会同步算完全部折行，窗口改宽度时要卡几百毫秒甚至更久。
        不加的话 Tk 在后台慢慢算，数字会在一两秒内收敛；每次轮询签名里带着它，
        变了就重画，所以色点会自己挪到准确位置。
        """
        try:
            v = self.text.count("1.0", "end", "ypixels") or (0,)
            return int(v if isinstance(v, int) else v[0])
        except (tk.TclError, ValueError, TypeError):
            return 0

    def _snap(self, y):
        """点在某个色块上（含 _HIT_PAD 的容差）→ 返回那一条日志的行号，否则 None。

        长日志里 1px 对应几十行，光靠"点击位置占比"是点不准一条错误的。
        一个色块里压了好几条时，取和色块同级别（红块取 ERROR）且离点击最近的那一条。
        """
        最近, 距 = None, None
        for y0, y1, tag, marks in self._segs:
            d = 0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
            if d <= _HIT_PAD and (距 is None or d < 距):
                最近, 距 = (tag, marks), d
        if 最近 is None:
            return None
        tag, marks = 最近
        cand = [m for m in marks if m[2] == tag] or marks
        return min(cand, key=lambda m: abs(m[0] - y))[1]

    def _visible(self, ln):
        return line_visible(self.text, ln)

    def _goto_line(self, ln):
        goto_line(self.text, ln)

    def _center_on(self, frac):
        """让可见区的中心落在 frac（0~1）处。"""
        try:
            yv = self.text.yview()
            vh = yv[1] - yv[0]
            self.text.yview_moveto(max(0.0, min(1.0 - vh, frac - vh / 2.0)))
        except tk.TclError:
            pass

    def _on_press(self, e):
        self._drag = True
        self._redraw()                  # 色块位置要是最新的（日志满额后行号会前移）
        cy = self._frac_at(e.y)
        try:
            yv = self.text.yview()
        except tk.TclError:
            return
        box = self._box
        ln = self._snap(e.y)
        # 按在可见区框上：不跳，只记下抓的位置，随后拖动框跟着走（和滚动条一样）。
        # 例外：点的是色点、而那一行现在看不见 —— 框被撑成最小高度时会盖住旁边的色点，
        # 这时用户要的是"带我去那条错误"，不是抓框。
        if box is not None and box[0] <= e.y <= box[1] and (ln is None or self._visible(ln)):
            self._grab = cy - (yv[0] + yv[1]) / 2.0
            return
        if ln is not None:
            self._goto_line(ln)
            try:
                yv = self.text.yview()
            except tk.TclError:
                return
            self._grab = cy - (yv[0] + yv[1]) / 2.0     # 吸附之后接着拖，框不会再蹦一下
        else:
            self._grab = 0.0
            self._center_on(cy)

    def _on_drag(self, e):
        if self._drag:
            self._center_on(self._frac_at(e.y) - self._grab)

    def _on_release(self, _e):
        self._drag = False
        self._grab = 0.0

    # ---------------- 绘制 ----------------
    def _marks(self):
        return marks_of(self.text)

    def _tag_sig(self):
        return tag_sig(self.text)

    def _redraw(self, force=False):
        """全量重绘：轨道 + 色点 + 滑块。只在内容 / 尺寸 / 主题变了时才需要。

        滚动（只有 yview 变了）走 `sync_box()`，那条路只挪滑块，不碰色点：
        一次全量约 2 ms（占滚动一帧 7 ms 的三成），而且每次都 delete("all") 重建几十个
        canvas 项，滚动时就是一顿一顿的。
        """
        try:
            end = int(str(self.text.index("end-1c")).split(".")[0])
            yv = self.text.yview()
            total = self._total_px()
            tags = self._tag_sig()
        except (tk.TclError, ValueError):
            return
        h = max(1, self.winfo_height())
        w = max(1, self.winfo_width())
        body = (end, total, tags, h, w)
        if not force and body == self._body_sig:
            # 内容没变：只可能是滚动了，只动滑块
            self._move_box(yv, h, w)
            return
        self._body_sig = body
        self._sig = (body, round(yv[0], 4), round(yv[1], 4))
        # 日志长了就放慢轮询：几万行的时候每 250ms 扫一遍 tag 范围纯属浪费
        self._interval = _POLL_SLOW_MS if end > 8000 else _POLL_MS
        self.delete("all")
        self._segs = []
        self._box = None
        self._box_items = None
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

        def frac(ln):
            """这一行起点在整份日志里的位置（0~1）。按像素算，和滚动条、可见区框同一个标尺；
            控件还没布局（总高取不到）时退回按行号算。"""
            if total > 0:
                try:
                    return (self.text.count("1.0", "%d.0" % ln, "ypixels") or (0,))[0] / float(total)
                except (tk.TclError, ValueError, TypeError):
                    pass
            return (ln - 1) / float(max(1, end - 1))

        # 位置 → 像素行：长日志里一个像素行可能压着几十条，先归并再画。色点以所在位置为中心，
        # 这样"这一行在可见区里"时色点也落在可见区框里（顶对齐的话会多出框外 4px）。
        # 同一像素行叠了几条就把色点画高一点（最多 _TICK_MAX），"某一段密集报错"一眼能看出来。
        桶 = {}
        for ln, tag in best.items():
            py = frac(ln) * h
            y = max(0, min(h - _TICK_H, int(round(py - _TICK_H / 2.0))))
            cur = 桶.get(y)
            if cur is None:
                桶[y] = [tag, 1, [(py, ln, tag)]]
            else:
                cur[1] += 1
                cur[2].append((py, ln, tag))
                if order[tag] < order[cur[0]]:
                    cur[0] = tag
        # 相邻像素行、同一种颜色 → 合并成一段连续的色块：几十条错误挤在同一段时，
        # 看起来是"一条红杠"而不是几颗互不相连的豆子
        段 = []
        for y, (tag, n, ms) in sorted(桶.items()):
            tall = min(_TICK_MAX, _TICK_H + n // 2)
            if 段 and 段[-1][1] == tag and y <= 段[-1][3]:
                段[-1][3] = max(段[-1][3], y + tall)
                段[-1][2] += n
                段[-1][4].extend(ms)
            else:
                段.append([y, tag, n, y + tall, list(ms)])
        for y0, tag, _n, y1, ms in 段:
            key = next(k for t, k, _f in _LEVELS if t == tag)
            fallback = next(f for t, _k, f in _LEVELS if t == tag)
            self.create_rectangle(1, y0, w - 1, min(h, y1),
                                  fill=self.theme.get(key, fallback), outline="")
            self._segs.append((y0, min(h, y1), tag, ms))
        self._move_box(yv, h, w)

    # ---------------- 滑块（可见区框） ----------------
    def _box_geom(self, yv, h):
        """可见区框的 (y0, y1)；整份日志一屏就放得下（yv 覆盖全部）时返回 None。"""
        top, bottom = float(yv[0]), float(yv[1])
        if bottom - top >= 0.999:
            return None
        y0, y1 = top * h, bottom * h
        # 日志特别长时可见区只有几个像素，太小既看不见也抓不住：
        # 给一个最小高度，并以原来的中心对齐
        if y1 - y0 < _MIN_BOX:
            中 = (y0 + y1) / 2.0
            y0, y1 = 中 - _MIN_BOX / 2.0, 中 + _MIN_BOX / 2.0
            if y0 < 0:
                y0, y1 = 0, _MIN_BOX
            if y1 > h:
                y0, y1 = h - _MIN_BOX, h
        return y0, y1

    def _move_box(self, yv, h, w):
        """画 / 挪滑块。已经画过就只改坐标（3 个 canvas 项），不重建。

        Tk canvas 没有 alpha，用"底色→强调色"混出来的实色块代替，比 stipple 网点干净得多
        （网点在小尺寸下会变成一块噪点）。上下各一条实线，拖动时能看清抓到的是哪一段。
        """
        g = self._box_geom(yv, h)
        items = self._box_items
        if g is None:
            if items is not None:
                for it in items:
                    self.delete(it)
                self._box_items = None
            self._box = None
            return
        y0, y1 = g
        self._box = (y0, y1)
        if items is not None:
            try:
                self.coords(items[0], 1, y0, w - 1, y1)
                self.coords(items[1], 1, y0, w - 1, y0)
                self.coords(items[2], 1, y1, w - 1, y1)
                return
            except tk.TclError:
                self._box_items = None
        底 = self.theme.get("bg", "#1e1e1e")
        强调 = self.theme.get("accent", "#4a90d9")
        框 = _mix(底, 强调, 0.45)
        r = self.create_rectangle(1, y0, w - 1, y1, fill=框, outline=强调)
        l1 = self.create_line(1, y0, w - 1, y0, fill=强调)
        l2 = self.create_line(1, y1, w - 1, y1, fill=强调)
        self._box_items = (r, l1, l2)

    def sync_box(self, first=None, last=None):
        """文本滚动时由 LiveLogView 的 yscrollcommand 直接调：只挪滑块，立刻生效。

        这是总览条"滚动跟手"的关键：以前靠 250 ms 一次的轮询追，滚动一次滑块只更新
        一两次、滞后 120~190 ms。first/last 就是 Tk 传给 yscrollcommand 的那两个数；
        没传就自己读一次 yview。
        """
        try:
            if first is None or last is None:
                yv = self.text.yview()
            else:
                yv = (float(first), float(last))
            h = max(1, self.winfo_height())
            w = max(1, self.winfo_width())
        except (tk.TclError, ValueError):
            return
        self._move_box(yv, h, w)

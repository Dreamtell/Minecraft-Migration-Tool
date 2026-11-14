# -*- coding: utf-8 -*-
"""执行日志放大查看右侧的缩略图（VS Code minimap 那种）。

和 `log_overview.py` 的窄色条分工不同：总览条把**整份**日志压成一根竖线，只看得出
"哪儿有错"；缩略图把日志**按原样缩小**画出来 —— 每行一排小色块（一个词一块，宽度
= 这个词占的字符格数），所以能看出这一段是长句还是短句、哪里是空行、哪里连着一片红。
ERROR / WARNING / SUCCESS / SIMULATE 的行按日志配色上色，ERROR 行再垫一条淡红底。

一页放不下时缩略图自己跟着滚（和 VS Code 一样）：滑块在缩略图里的位置 = 文本在
整份日志里的位置。点一下 → 把点到的那一行放到视口中间；按住滑块拖 → 像滚动条那样
平移；滚轮 → 转给日志文本框（走它自己的平滑滚动和"回到底部"逻辑）。

绘制用 PIL 画到一张图上再整张贴到 Canvas：一屏几百行、两三千个色块，一帧 1~2 ms；
每行的词跨度按行内容缓存，滚动时只重画可见的那一页。主题切换调 `set_theme()`。
"""

import tkinter as tk
import unicodedata

from PIL import Image, ImageDraw, ImageTk

from ui.log_overview import LEVELS, goto_line, marks_of, tag_sig

try:
    from ui.mw_common import _mix
except Exception:                                   # 单独跑模块时不至于炸
    def _mix(a, b, t):
        return a

try:
    from utils.dpi import px
except Exception:                                   # 同上
    def px(v):
        return max(1, int(round(float(v))))

_POLL_MS = 50           # 轮询间隔：滚动是平滑动画，慢了滑块会跟不上；没变化时一次轮询不到 0.1 ms
_PAD = 3                # 色块距左边的留白（左边那条 1px 分隔线之后）
_MAX_CHARS = 400        # 一行最多看前这么多个字符（再长也超出缩略图宽度了）
_SLIDER_MIN = 10        # 滑块最小高度（像素）：日志很长时可见区只占一两行，太小抓不住
_ALPHA = (0.14, 0.24, 0.34)     # 滑块不透明度：平时 / 鼠标在上面 / 正在拖
_BAND_MIX = 0.22        # ERROR 行底色 = 日志底色往错误色混这么多
_INFO_MIX = 0.40        # INFO 行的色块 = 日志底色往正文色混这么多（压低，让彩色行跳出来）

_CELL = {}              # 字符 → 占几格（宽字符 2 格，组合符/变体选择符 0 格）


def _rgb(color, fallback=(30, 30, 30)):
    try:
        c = str(color)
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return fallback


def _cells(ch):
    n = _CELL.get(ch)
    if n is None:
        if unicodedata.combining(ch) or ch in "\ufe0f\u200d":
            n = 0
        else:
            n = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if len(_CELL) < 8000:
            _CELL[ch] = n
    return n


class LogMinimap(tk.Canvas):
    """日志缩略图。`text` 是日志的 Text 控件。

    on_before：程序要动视口之前先调一下（放大日志窗口传 `view.scroller.stop`，
    免得平滑滚动的余量把刚跳过去的位置又拖走）。
    on_wheel：滚轮事件交给谁；不传就直接滚 text。
    """

    def __init__(self, parent, text, theme, width=110, on_before=None, on_wheel=None, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("height", 1)              # Canvas 默认请求 7cm 高，会把并排的文本框撑变形
        kw.setdefault("bg", theme.get("log_bg", "#1e1e1e"))
        super().__init__(parent, width=width, **kw)
        self.text = text
        self.theme = dict(theme)
        self._on_before = on_before
        self._on_wheel = on_wheel
        self._cw = px(1)                        # 一个字符格的宽
        self._lh = px(3)                        # 一行占的高（含行距）
        self._bh = max(1, self._lh - px(1))     # 色块高
        self._spans_cache = {}
        self._photo = None
        self._img_id = self.create_image(0, 0, anchor="nw")
        self._base = None                       # 缓存的底图（RGBA）
        self._base_key = None
        self._sig = None
        self._after = None
        self._theme_gen = 0
        self._hover = False
        self._drag = False
        self._grab = 0.0
        self._slider = None                     # (y0, y1)，没画是 None
        self._apply_colors()
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<MouseWheel>", self._on_mousewheel)
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
        if self._after is None:
            try:
                self._after = self.after(_POLL_MS, self._poll)
            except Exception:
                self._after = None
    def _poll(self):
        self._after = None
        try:
            if self.winfo_viewable():
                self._redraw()
        except tk.TclError:
            return                              # 窗口关掉了
        self._schedule()

    def set_theme(self, theme):
        self.theme = dict(theme)
        self._theme_gen += 1
        self._apply_colors()
        try:
            self.configure(bg=self.theme.get("log_bg", "#1e1e1e"))
        except tk.TclError:
            return
        self._redraw(force=True)

    def _apply_colors(self):
        t = self.theme
        bg = t.get("log_bg", "#1e1e1e")
        fg = t.get("log_fg", "#ffffff")
        self._bg = _rgb(bg)
        self._fg = _rgb(fg, (255, 255, 255))
        self._info = _rgb(_mix(bg, fg, _INFO_MIX))
        self._lvl = {tag: _rgb(_mix(bg, t.get(key, fallback), 0.9))
                     for tag, key, fallback in LEVELS}
        self._band = _rgb(_mix(bg, t.get("log_error_fg", "#e53935"), _BAND_MIX))
        self._edge = _rgb(_mix(bg, fg, 0.12))

    # ---------------- 几何 ----------------
    def _geom(self):
        """当前的几何：总行数、视口覆盖的行区间、缩略图自己的滚动偏移。取不到返回 None。"""
        try:
            n = int(str(self.text.index("end-1c")).split(".")[0])
            hh = max(1, self.text.winfo_height())
            top = int(str(self.text.index("@0,0")).split(".")[0])
            bot = int(str(self.text.index("@0,%d" % (hh - 1))).split(".")[0])
        except (tk.TclError, ValueError):
            return None
        if n <= 0:
            return None
        top = max(1, min(top, n))
        bot = max(top, min(bot, n))
        h = max(1, self.winfo_height())
        lh = self._lh
        v = bot - top + 1                       # 视口覆盖的逻辑行数
        content = n * lh
        if content <= h or n <= v:
            off = 0.0
        else:
            off = (top - 1) / float(n - v) * (content - h)
        return {"n": n, "top": top, "bot": bot, "v": v, "off": off, "h": h}
    def _slider_box(self, g, w):
        """滑块的 (y0, y1)；整份日志一屏就放得下时不画（返回 None）。"""
        if g["v"] >= g["n"]:
            return None
        lh, h = self._lh, g["h"]
        y0 = (g["top"] - 1) * lh - g["off"]
        y1 = y0 + g["v"] * lh
        if y1 - y0 < _SLIDER_MIN:
            mid = (y0 + y1) / 2.0
            y0, y1 = mid - _SLIDER_MIN / 2.0, mid + _SLIDER_MIN / 2.0
        if y0 < 0:
            y0, y1 = 0.0, y1 - y0
        if y1 > h:
            y0, y1 = y0 - (y1 - h), float(h)
        return (max(0.0, y0), min(float(h), y1))

    # ---------------- 绘制 ----------------
    def _spans(self, line):
        sp = self._spans_cache.get(line)
        if sp is not None:
            return sp
        out, x, start = [], 0, None
        for ch in line[:_MAX_CHARS]:
            if ch == " " or ch == "\t":
                if start is not None:
                    out.append((start, x))
                    start = None
                x += 4 - x % 4 if ch == "\t" else 1
            else:
                if start is None:
                    start = x
                x += _cells(ch)
        if start is not None:
            out.append((start, x))
        if len(self._spans_cache) > 6000:
            self._spans_cache.clear()
        self._spans_cache[line] = out
        return out
    def _render_base(self, g, w):
        h, lh, bh, cw = g["h"], self._lh, self._bh, self._cw
        img = Image.new("RGBA", (w, h), self._bg + (255,))
        d = ImageDraw.Draw(img)
        off = g["off"]
        r0 = max(0, int(off // lh))
        r1 = min(g["n"], int((off + h) // lh) + 1)
        lo, hi = r0 + 1, r1
        if hi >= lo:
            try:
                raw = self.text.get("%d.0" % lo, "%d.0" % (hi + 1))
            except tk.TclError:
                raw = ""
            lines = raw.split("\n")
            level = {}
            for ln, tag in marks_of(self.text, lo, hi):
                level.setdefault(ln, tag)       # marks_of 按 ERROR > WARNING > ... 的顺序给
            for k in range(min(len(lines), hi - lo + 1)):
                ln = lo + k
                y = int(round((ln - 1) * lh - off))
                tag = level.get(ln)
                if tag == "ERROR":
                    d.rectangle((1, y, w, y + lh - 1), fill=self._band)
                col = self._lvl[tag] if tag else self._info
                for c0, c1 in self._spans(lines[k]):
                    x0 = _PAD + c0 * cw
                    if x0 >= w:
                        break
                    d.rectangle((x0, y, min(w - 1, _PAD + c1 * cw - 1), y + bh - 1), fill=col)
        d.line((0, 0, 0, h), fill=self._edge)    # 左边一条 1px 分隔线，和文本框隔开
        return img
    def _redraw(self, force=False):
        g = self._geom()
        w = max(1, self.winfo_width())
        if g is None:
            if self._sig != "empty" or force:
                self._sig = "empty"
                self._slider = None
                self._base = None
                self._base_key = None
                self._photo = None
                self.itemconfigure(self._img_id, image="")
            return
        try:
            first = self.text.get("1.0", "1.end")
            last = self.text.get("end-1c linestart", "end-1c")
            tags = tag_sig(self.text)
        except tk.TclError:
            return
        state = 2 if self._drag else (1 if self._hover else 0)
        base_key = (g["n"], int(g["off"]), w, g["h"], first, last, tags, self._theme_gen)
        sig = (base_key, g["top"], g["bot"], state)
        if not force and sig == self._sig:
            return
        self._sig = sig
        if self._base is None or self._base_key != base_key:
            self._base = self._render_base(g, w)
            self._base_key = base_key
        box = self._slider_box(g, w)
        self._slider = box
        img = self._base
        if box is not None:
            y0, y1 = int(round(box[0])), int(round(box[1]))
            if y1 > y0:
                region = img.crop((0, y0, w, y1))
                over = Image.new("RGBA", region.size, self._fg + (int(255 * _ALPHA[state]),))
                img = img.copy()
                img.paste(Image.alpha_composite(region, over), (0, y0))
        try:
            if self._photo is not None and (self._photo.width(), self._photo.height()) == img.size:
                self._photo.paste(img)
            else:
                self._photo = ImageTk.PhotoImage(img, master=self)
            self.itemconfigure(self._img_id, image=self._photo)
        except tk.TclError:
            pass
    # ---------------- 交互 ----------------
    def _before(self):
        if self._on_before is not None:
            try:
                self._on_before()
            except Exception:
                pass

    def _line_at(self, y, g):
        i = int((y + g["off"]) // self._lh)
        return max(1, min(g["n"], i + 1))

    def _on_press(self, e):
        g = self._geom()
        if g is None:
            return
        self._drag = True
        box = self._slider
        if box is not None and box[0] <= e.y <= box[1]:
            self._grab = e.y - box[0]           # 抓在滑块上：不跳，随后拖动滑块跟着走
        else:
            self._before()
            goto_line(self.text, self._line_at(e.y, g))
            self._redraw()
            box = self._slider
            self._grab = (box[1] - box[0]) / 2.0 if box is not None else 0.0
        self._redraw()

    def _on_drag(self, e):
        if not self._drag:
            return
        g = self._geom()
        if g is None:
            return
        box = self._slider
        sh = (box[1] - box[0]) if box is not None else 0.0
        top_y = e.y - self._grab
        lh, h, n, v = self._lh, g["h"], g["n"], g["v"]
        if n * lh <= h:
            s = top_y / float(lh)
        else:
            span = max(1.0, h - sh)
            s = max(0.0, min(1.0, top_y / span)) * max(0, n - v)
        ln = max(1, min(n, int(round(s)) + 1))
        self._before()
        try:
            self.text.yview("%d.0" % ln)
        except tk.TclError:
            return
        self._redraw()

    def _on_release(self, _e):
        self._drag = False
        self._grab = 0.0
        self._redraw()

    def _on_motion(self, e):
        box = self._slider
        hover = box is not None and box[0] <= e.y <= box[1]
        if hover != self._hover:
            self._hover = hover
            self._redraw()

    def _on_leave(self, _e):
        if self._hover:
            self._hover = False
            self._redraw()

    def _on_mousewheel(self, e):
        try:
            if self._on_wheel is not None:
                self._on_wheel(e)
            else:
                self.text.yview_scroll(-int(e.delta / 120) * 3, "units")
        except tk.TclError:
            pass
        return "break"

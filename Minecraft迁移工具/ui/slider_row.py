# -*- coding: utf-8 -*-
"""滑杆控件：轨道 + 圆点手柄 + 右侧数值（离散整数档）。

替掉"－/＋ 两个按钮夹一个数字"那套：点十几次才到顶，而且得先算清楚点了几下。

做法与 SwitchRow / SegmentedControl 同一套（都在这套 UI 里验证过了）：
- PIL 出图，圆角与圆点 4 倍超采样（直接画圆边上会有锯齿），轨道底图按宽度缓存；
- 文字用画布文字（字体渲染比 PIL 好）；
- 拖动时只重画「填充条 + 圆点」，不重建整张图。

用法：
    s = SliderRow(parent, theme, 1, 15, value=10, command=on_change,
                  release=on_release)
    s.get()      # -> 10
    s.set(4)     # 摆到某档（不触发 command）
"""

import tkinter as tk
from tkinter import font as tkfont

from utils.helpers import (
    _PILImage, _PILImageTk, _圆角矩形, is_dark_theme, trace_exc,
)


class SliderRow(tk.Canvas):
    """一条滑杆。滚轮、点轨道、拖动、左右方向键都能改值。"""

    _TRACK_H = 6        # 轨道粗细
    _KNOB = 14          # 圆点直径
    _GAP = 12           # 轨道右端与数值之间
    _PAD = 2            # 轨道左端留白

    def __init__(self, parent, theme, minimum, maximum, value=None,
                 command=None, release=None, font=("微软雅黑", 10, "bold"),
                 value_font=("微软雅黑", 11, "bold"), accent=None,
                 height=None, **kw):
        self._is_slider = True
        self.theme = dict(theme)
        self._min = int(minimum)
        self._max = int(maximum)
        if self._max < self._min:
            self._min, self._max = self._max, self._min
        self._v = self._clamp(self._min if value is None else value)
        self._command = command
        self._release = release
        self._accent_key = accent or "switch_on"
        self._font = font
        self._value_font = value_font
        self._enabled = True
        self._drag = False
        self._hover = False
        self._cw = 0
        self._h = int(height or 34)
        self._base = None
        self._base_key = None
        self._photo = None
        try:
            bg = parent.cget("bg")
        except Exception:
            bg = self.theme.get("bg", "#f0f0f0")
        super().__init__(parent, height=self._h, highlightthickness=0, bd=0,
                         bg=bg, **kw)
        self._img = self.create_image(0, 0, anchor="nw")
        self._val_id = self.create_text(0, 0, anchor="e", tags="txt",
                                        font=self._value_font)
        try:
            self.configure(cursor="hand2")
        except Exception:
            pass
        self.bind("<Configure>", lambda _e: self._on_resize())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Left>", lambda _e: self._step(-1))
        self.bind("<Right>", lambda _e: self._step(1))
        self._redraw()

    # ---------------------------------------------------------------- 对外
    def get(self):
        return int(self._v)

    def set(self, value, notify=False):
        v = self._clamp(value)
        if v == self._v:
            return
        self._v = v
        self._redraw()
        if notify and self._command is not None:
            try:
                self._command(v)
            except Exception as e:
                trace_exc("helpers", e)

    def set_theme(self, theme):
        self.theme = dict(theme)
        try:
            self.configure(bg=self.theme.get("bg", "#f0f0f0"))
        except Exception:
            pass
        self._base = None
        self._base_key = None
        self._redraw()

    def set_enabled(self, on):
        self._enabled = bool(on)
        try:
            self.configure(cursor="hand2" if self._enabled else "")
        except Exception:
            pass
        self._redraw()

    # ---------------------------------------------------------------- 内部
    def _clamp(self, v):
        try:
            v = int(round(float(v)))
        except Exception:
            v = self._min
        return max(self._min, min(self._max, v))

    def _rgb(self, 键, 缺省="#888888"):
        def 拆(色):
            try:
                return tuple(int(str(色)[i:i + 2], 16) for i in (1, 3, 5))
            except Exception:
                return tuple(int(缺省[i:i + 2], 16) for i in (1, 3, 5))
        return 拆(self.theme.get(键) or 缺省)

    def _is_dark(self):
        try:
            return bool(is_dark_theme(self.theme))
        except Exception:
            return False

    def _on_resize(self):
        w = int(self.winfo_width())
        if abs(w - self._cw) > 1:
            self._cw = w
            self._base = None
            self._base_key = None
            self._redraw()

    def _label_w(self):
        """右侧数值要占多宽（位数会变，按最宽的那个量）。"""
        try:
            f = tkfont.Font(font=self._value_font)
            return max(f.measure(str(self._min)), f.measure(str(self._max))) + 2
        except Exception:
            return 26

    def _track_span(self):
        w = max(int(self._cw or self.winfo_width()), 40)
        左 = self._PAD + self._KNOB // 2
        右 = w - self._label_w() - self._GAP - self._KNOB // 2
        if 右 <= 左:
            右 = 左 + 1
        return 左, 右

    def _x_of(self, v):
        左, 右 = self._track_span()
        if self._max == self._min:
            return 左
        t = (v - self._min) / float(self._max - self._min)
        return 左 + (右 - 左) * t

    def _v_of(self, x):
        左, 右 = self._track_span()
        t = (x - 左) / float(max(1, 右 - 左))
        t = max(0.0, min(1.0, t))
        return self._clamp(round(self._min + t * (self._max - self._min)))

    def _set_hover(self, on):
        if on == self._hover:
            return
        self._hover = on
        self._redraw()

    def _on_press(self, event):
        if not self._enabled:
            return
        self._drag = True
        try:
            self.focus_set()            # 之后方向键能接着微调
        except Exception:
            pass
        self._apply(self._v_of(event.x))

    def _on_drag(self, event):
        if not self._enabled or not self._drag:
            return
        self._apply(self._v_of(event.x))

    def _on_release(self, _event):
        if not self._drag:
            return
        self._drag = False
        self._redraw()
        if self._release is not None:
            try:
                self._release(self.get())
            except Exception as e:
                trace_exc("helpers", e)

    def _on_wheel(self, event):
        """滚轮**不改数值**：设置页是滚动浏览的，鼠标划过滑条就改参数太容易误触
        （用户反馈"正常滑动设置界面时被意外改动"）。要改就按住拖动，或点轨道。"""
        return None

    def _step(self, d):
        if not self._enabled:
            return
        self._apply(self._clamp(self._v + d))
        if self._release is not None:       # 键盘/滚轮没有松开事件，直接落盘
            try:
                self._release(self.get())
            except Exception as e:
                trace_exc("helpers", e)

    def _apply(self, v):
        v = self._clamp(v)
        if v == self._v:
            return
        self._v = v
        self._redraw()
        if self._command is not None:
            try:
                self._command(v)
            except Exception as e:
                trace_exc("helpers", e)

    def _base_img(self, w, h):
        """轨道底图（未填充那一段）：宽度和主题不变就复用。"""
        键 = (w, h, self._is_dark(), self._enabled)
        if self._base_key != 键:
            槽 = self._rgb("ttk_trough_bg", "#e0e0e0")
            if not self._enabled:
                槽 = tuple(int(c * 0.85 + 40 * 0.15) for c in 槽)
            self._base = _圆角矩形((w, self._TRACK_H), self._TRACK_H / 2.0, 槽)
            self._base_key = 键
        return self._base

    def _redraw(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        w = max(int(self._cw or self.winfo_width()), 40)
        h = self._h
        帧 = _PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
        左, 右 = self._track_span()
        轨道宽 = max(1, int(右 - 左))
        ky = int((h - self._TRACK_H) / 2)
        帧.alpha_composite(self._base_img(轨道宽, h), (int(左), ky))
        # 已填充部分：从轨道左端到圆点
        点x = self._x_of(self._v)
        填宽 = int(max(0, 点x - 左))
        if 填宽 > 2:
            帧.alpha_composite(
                _圆角矩形((填宽, self._TRACK_H), self._TRACK_H / 2.0,
                          self._rgb(self._accent_key, "#4da3f0")),
                (int(左), ky))
        # 圆点：悬停/拖动时放大两像素，给出"抓住了"的反馈
        直径 = self._KNOB + (2 if (self._hover or self._drag) else 0)
        色 = (self._rgb(self._accent_key, "#4da3f0") if self._enabled
             else self._rgb("muted_fg", "#9e9e9e"))
        帧.alpha_composite(_圆角矩形((直径, 直径), 直径 / 2.0, 色),
                          (int(round(点x - 直径 / 2.0)),
                           int(round(h / 2.0 - 直径 / 2.0))))
        try:
            self._photo = _PILImageTk.PhotoImage(帧)
            self.itemconfig(self._img, image=self._photo)
            self.coords(self._val_id, w - 1, h / 2.0)
            self.itemconfig(
                self._val_id, text=str(self._v),
                fill=(self.theme.get("data_num_fg", "#6a1b9a") if self._enabled
                      else self.theme.get("muted_fg", "#888888")))
            self.tag_raise("txt")
        except Exception:
            pass

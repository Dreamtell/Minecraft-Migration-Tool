# ui/qt_main/star_slider.py
"""星空滑块：深色圆角卡片 + 紫粉渐变标题 + 星点渐变轨道 + 白色圆形拇指。

照着参考图做的可复用控件（`ui/qt_main/` 这一套自绘控件的老规矩：只 import PySide6，
每帧用 QPainter 现画，不进主进程的 Tk 世界）。

三个可单独用的部件：
  - StarSlider      轨道本身：渐变填充 + 星点闪烁 + 白色拇指，值 0~1，可拖/可键盘/可滚轮
  - GradientText    渐变文字（标题那种紫→品红，用 QPainterPath 填渐变，不是图片）
  - StarSliderCard  截图那张卡：标题 + 副标题 + 滑块

画法要点：
  - 星点位置用固定种子生成，**只画已填充区域内的**（拖动时像「点亮」而不是整片滑动）
  - 闪烁按真实时间算（sin 相位），帧率变了节奏不变；不可见时定时器自动停
  - 拇指带柔和白光（径向渐变）+ 一层暗色落影；hover / 拖动时略大一点
  - 轨道底槽与填充都按半高圆角，填充宽度不足一个圆头时自动收半径，不会出现尖角

预览：
    python -m ui.qt_main.star_slider            # 开窗口
    python -m ui.qt_main.star_slider --shot x.png   # 只截图（离屏，CI/探针用）
"""
import math
import random
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets

_UI = 1.30


def _u(v):
    """设计稿像素 → 当前 UI 缩放后的像素（与 toast.py / flip_button.py 同一套）。"""
    return int(round(float(v) * _UI))


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


# ---- 配色：深浅各一套，键名保持一致 ----
DARK = {
    "card": "#1c1c1e",
    "border": (255, 255, 255, 16),
    "title": ("#9d6bff", "#e46bff"),
    "sub": "#8e8e93",
    "track": "#2a2a2e",
    "grad": ((0.0, "#4b5cff"), (0.55, "#7a5cff"), (1.0, "#a86bff")),
    "thumb": "#ffffff",
    "solid": "#5566f0",
    "node_on": (255, 255, 255, 180),
    "node_off": (128, 128, 140, 230),
    "node_ring": (255, 255, 255, 66),
}
LIGHT = {
    "card": "#f5f5f7",
    "border": (0, 0, 0, 20),
    "title": ("#6a4bdc", "#b34bdc"),
    "sub": "#6e6e73",
    "track": "#e3e3e8",
    "grad": ((0.0, "#4a5cf0"), (0.55, "#7a5cf0"), (1.0, "#a05cf0")),
    "thumb": "#ffffff",
    "solid": "#5a63e8",
    "node_on": (255, 255, 255, 235),
    "node_off": (130, 130, 142, 215),
    "node_ring": (120, 90, 220, 55),
}

_TRACK_H = 26.0        # 轨道高（设计稿像素）
_THUMB_R = 15.0        # 拇指半径
_TICK_MS = 16          # 兜底帧间隔；真正用的间隔按屏幕刷新率算（见 _apply_refresh_rate）
_ANIM_MS = 220.0       # setValue(animate=True) 的收敛时间
_STAR_SEED = 20261108


def _rounded(rect, radius):
    path = QtGui.QPainterPath()
    radius = max(0.0, min(float(radius), rect.width() / 2.0, rect.height() / 2.0))
    path.addRoundedRect(rect, radius, radius)
    return path


def _make_stars(count=46, seed=_STAR_SEED):
    """固定种子的星点表：(x 归一化, y 归一化, 半径, 基准 alpha, 相位, 角速度, 是否大点)。"""
    rnd = random.Random(seed)
    stars = []
    for _ in range(count):
        big = rnd.random() < 0.10
        stars.append((
            rnd.uniform(0.015, 0.985),
            rnd.uniform(0.16, 0.84),
            rnd.uniform(0.9, 1.5) if big else rnd.uniform(0.55, 1.05),
            rnd.randint(150, 235),
            rnd.uniform(0.0, math.tau),
            rnd.uniform(2.2, 5.5),
            big,
        ))
    return stars


def _norm_stops(stops):
    """把「档位数」或「档位表」统一成升序的 0~1 列表；None 表示连续滑动。"""
    if stops is None:
        return None
    if isinstance(stops, int):
        n = max(2, int(stops))
        return [i / float(n - 1) for i in range(n)]
    vals = sorted(_clamp(float(v)) for v in stops)
    return vals or None


class StarSlider(QtWidgets.QWidget):
    """渐变星空轨道。值 0~1（0 = 拇指在最左，1 = 最右）。

    stops 给了就是「一格一格」的：4 → 0/⅓/⅔/1 四个档位，拖动/滚轮/方向键都吸附到档位。
    """

    valueChanged = QtCore.Signal(float)

    def __init__(self, parent=None, value=1.0, dark=True, twinkle=True, stops=None):
        super().__init__(parent)
        self._stops = _norm_stops(stops)
        self._stops_visible = True
        self._value = self._snap(_clamp(float(value)))
        self._dark = bool(dark)
        self._twinkle = bool(twinkle)
        self._hover = 0.0
        self._fx = 1.0 if self._at_ultra() else 0.0   # Ultra 特效强度 0~1
        self._drag = False
        self._drag_dir = 0           # 最近一次拖动方向：-1 左 / +1 右 / 0 没动
        self._last_x = None
        self._t0 = time.perf_counter()
        self._last_tick = self._t0
        self._anim = None            # (from, to, t0)
        self._stars = _make_stars()
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._interval = _TICK_MS
        self._screen_connected = False
        self._apply_refresh_rate()
        self.setMinimumHeight(_u(38))
        self.setMinimumWidth(_u(120))
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAttribute(QtCore.Qt.WA_Hover, True)

    # ---------------- 对外 ----------------
    def value(self):
        return self._value

    def setValue(self, v, animate=False):
        v = self._snap(_clamp(float(v)))
        self._anim = None
        if animate and abs(v - self._value) > 1e-4 and self.isVisible():
            self._anim = (self._value, v, time.perf_counter())
            self._ensure_tick()
        else:
            self._set(v)
        self.update()

    def set_value(self, v, animate=False):
        self.setValue(v, animate)

    def stops(self):
        return list(self._stops) if self._stops else []

    def set_stops_visible(self, on):
        """档位点是否显示（默认显示；单点连续滑块本来就没有档位点）。"""
        on = bool(on)
        if on == self._stops_visible:
            return
        self._stops_visible = on
        self.update()

    def set_stops(self, stops):
        self._stops = _norm_stops(stops)
        self._set(self._snap(self._value))
        self.update()

    def step(self, direction=1):
        """下一个/上一个档位的值；连续模式下返回 None。"""
        return self._step(direction)

    def set_dark(self, dark):
        self._dark = bool(dark)
        self.update()

    def set_twinkle(self, on):
        self._twinkle = bool(on)
        self._ensure_tick()
        self.update()

    def sizeHint(self):
        return QtCore.QSize(_u(280), _u(38))

    def minimumSizeHint(self):
        return QtCore.QSize(_u(120), _u(38))

    # ---------------- 内部 ----------------
    def _snap(self, v):
        """有档位就吸到最近的档位，没有就原样返回。"""
        if not self._stops:
            return _clamp(float(v))
        return min(self._stops, key=lambda s: abs(s - v))

    def _snap_with_direction(self, v, direction=0):
        """松手吸附：以「离哪个节点最近」为主，拖动方向只在中间一小段「拉一把」。

        离两端节点越近越按距离走；只有落在两档中线 ±16% 的模糊带里，
        才由最后一段拖动方向决定是往前走还是往回退。这样快速甩一下就进下一档，
        慢慢挪动仍然就近吸附，手指的抖动不会把档位甩飞。
        """
        v = _clamp(float(v))
        if not self._stops:
            return v
        if v <= self._stops[0]:
            return self._stops[0]
        if v >= self._stops[-1]:
            return self._stops[-1]
        lo, hi = self._stops[0], self._stops[-1]
        for a, b in zip(self._stops, self._stops[1:]):
            if a <= v <= b:
                lo, hi = a, b
                break
        gap = hi - lo
        if gap < 1e-9:
            return hi
        mid = lo + gap * 0.5
        zone = gap * 0.16
        if direction > 0 and v >= mid - zone:
            return hi
        if direction < 0 and v <= mid + zone:
            return lo
        return hi if v >= mid else lo

    def _step(self, direction):
        """往上下一个档位挪一格；到头就停在端点。返回 None 表示是连续模式。"""
        if not self._stops:
            return None
        cur = self._value
        if direction > 0:
            cands = [s for s in self._stops if s > cur + 1e-6]
            return min(cands) if cands else self._stops[-1]
        cands = [s for s in self._stops if s < cur - 1e-6]
        return max(cands) if cands else self._stops[0]

    def _set(self, v):
        v = _clamp(float(v))
        if abs(v - self._value) < 1e-9:
            self._value = v
            return
        self._value = v
        self.valueChanged.emit(v)
        self._ensure_tick()      # 进出 Ultra 时要跑特效淡入淡出

    def _palette(self):
        return DARK if self._dark else LIGHT

    def _thumb_radius(self):
        """拇指最大半径（含 hover 放大），轨道两端留够这个距离，圆点才不会被裁。"""
        return _u(_THUMB_R) * (1.0 + 0.08)

    def _track_rect(self):
        m = self._thumb_radius() + _u(2.0)
        th = _u(_TRACK_H)
        cy = self.height() / 2.0
        return QtCore.QRectF(m, cy - th / 2.0, max(1.0, self.width() - 2 * m), th)

    def _value_at(self, x):
        tr = self._track_rect()
        return _clamp((float(x) - tr.left()) / max(1.0, tr.width()))

    def _ensure_tick(self):
        want = self.isVisible() and (self._twinkle or self._anim is not None
                                     or self._hover > 1e-3
                                     or abs(self._fx - self._fx_target()) > 1e-3)
        if want:
            if not self._timer.isActive():
                self._timer.start(self._interval)
            elif self._timer.interval() != self._interval:
                self._timer.setInterval(self._interval)
        elif self._timer.isActive():
            self._timer.stop()

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.06, max(0.001, now - self._last_tick))   # 真实帧间隔，掉帧也不会飞
        self._last_tick = now
        if self._anim is not None:
            frm, to, t0 = self._anim
            t = (now - t0) * 1000.0 / _ANIM_MS
            if t >= 1.0:
                self._set(to)
                self._anim = None
            else:
                e = 1.0 - (1.0 - t) ** 3
                self._set(frm + (to - frm) * e)
        # Ultra 特效强度缓动：拉到满档才渐变成彩色流动 + 星星
        fx_t = self._fx_target()
        if self._fx != fx_t:
            step = dt * 5.0
            if self._fx < fx_t:
                self._fx = min(fx_t, self._fx + step)
            else:
                self._fx = max(fx_t, self._fx - step)
        # hover 只是让拇指略大
        target = 1.0 if (self._hover > 1e-3 or self._drag) else 0.0
        if target > self._hover:
            self._hover = min(target, self._hover + dt * 8.0)
        elif target < self._hover:
            self._hover = max(target, self._hover - dt * 6.0)
        self.update()
        self._ensure_tick()

    # ---------------- 交互 ----------------
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._drag = True
            self._drag_dir = 0
            self._last_x = e.position().x()
            self.setFocus(QtCore.Qt.MouseFocusReason)
            self._anim = None
            # 按住时跟手（先不吸档），松手再吸——吸的过程有动画
            self._set(self._value_at(e.position().x()))
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag:
            x = e.position().x()
            if self._last_x is not None and abs(x - self._last_x) >= _u(4.0):
                self._drag_dir = 1 if x > self._last_x else -1
                self._last_x = x
            self._set(self._value_at(x))
            self.update()
            e.accept()
            return
        self.setCursor(QtCore.Qt.PointingHandCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag and e.button() == QtCore.Qt.LeftButton:
            self._drag = False
            # 松手才吸附：就近 + 拖动方向决定跨档阈值，然后缓动滑过去
            target = self._snap_with_direction(self._value, self._drag_dir)
            self.setValue(target, animate=True)
            self._ensure_tick()
            self.update()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):
        self._ensure_tick()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._drag = False
        self._ensure_tick()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        up = e.angleDelta().y() > 0
        if self._stops:
            self.setValue(self._step(1 if up else -1), animate=True)
        else:
            step = 0.05 if e.modifiers() & QtCore.Qt.ShiftModifier else 0.02
            self.setValue(self._value + (step if up else -step), animate=True)
        e.accept()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Down):
            self._nudge(-1, e)
        elif k in (QtCore.Qt.Key_Right, QtCore.Qt.Key_Up):
            self._nudge(1, e)
        elif k == QtCore.Qt.Key_Home:
            self.setValue(self._stops[0] if self._stops else 0.0, animate=True)
        elif k == QtCore.Qt.Key_End:
            self.setValue(self._stops[-1] if self._stops else 1.0, animate=True)
        else:
            super().keyPressEvent(e)
            return
        e.accept()

    def _nudge(self, direction, e):
        """键盘：有档位就跳一档，没有就 0.02（Shift 0.1）。"""
        if self._stops:
            self.setValue(self._step(direction), animate=True)
        else:
            step = 0.1 if e.modifiers() & QtCore.Qt.ShiftModifier else 0.02
            self.setValue(self._value + step * direction, animate=True)

    def _apply_refresh_rate(self):
        """按屏幕刷新率算定时器间隔：60Hz→16ms、144Hz→7ms、240Hz→4ms。"""
        hz = self._refresh_hz()
        self._interval = max(3, int(round(1000.0 / hz)))
        if self._timer.isActive() and self._timer.interval() != self._interval:
            self._timer.setInterval(self._interval)

    def _refresh_hz(self):
        hz = 0.0
        scr = None
        try:
            scr = self.screen()
        except Exception:
            scr = None
        if scr is not None:
            try:
                hz = float(scr.refreshRate())
            except Exception:
                hz = 0.0
        if not (hz > 1.0):
            scr = QtGui.QGuiApplication.primaryScreen()
            if scr is not None:
                try:
                    hz = float(scr.refreshRate())
                except Exception:
                    hz = 0.0
        if not (hz > 1.0):
            hz = 60.0
        return max(24.0, min(240.0, hz))

    def _on_screen_changed(self, *_args):
        self._apply_refresh_rate()

    def _connect_screen(self):
        if self._screen_connected:
            return
        wh = self.windowHandle()
        if wh is None:
            return
        try:
            wh.screenChanged.connect(self._on_screen_changed)
            self._screen_connected = True
        except Exception:
            pass

    def showEvent(self, e):
        self._connect_screen()
        self._apply_refresh_rate()
        self._ensure_tick()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    # ---------------- 画 ----------------
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pal = self._palette()
        tr = self._track_rect()
        th = tr.height()

        # 底槽（未填充部分）
        p.fillPath(_rounded(tr, th / 2.0), QtGui.QColor(pal["track"]))

        # 已填充：平时是单色；只有拉到满档 Ultra 才渐变成会流动的彩色 + 星星
        x = tr.left() + tr.width() * self._value
        fill_w = x - tr.left()
        if fill_w > 0.6:
            fill = QtCore.QRectF(tr.left(), tr.top(), fill_w, th)
            fill_path = _rounded(fill, min(th / 2.0, fill_w / 2.0))
            p.fillPath(fill_path, QtGui.QColor(pal["solid"]))
            if self._fx > 0.01:
                p.save()
                p.setOpacity(self._fx)
                grad = self._ultra_gradient(tr, pal)
                p.fillPath(fill_path, QtGui.QBrush(grad))
                p.restore()
                self._paint_stars(p, fill_path, tr, x, alpha_scale=self._fx)

        # 档位点（让节点位置看得见）
        self._paint_nodes(p, tr, x, pal)

        # 拇指：纯白圆
        self._paint_thumb(p, x, tr.center().y(), pal)

    def _ultra_gradient(self, tr, pal):
        """Ultra 档的彩色：色带在轨道上来回流动，整体色相再轻微漂移。"""
        now = time.perf_counter() - self._t0
        wob = math.sin(now * 0.95) * tr.width() * 0.22
        hue = math.sin(now * 0.62) * 0.045          # ±16° 左右
        grad = QtGui.QLinearGradient(tr.left() - wob, 0.0, tr.right() - wob, 0.0)
        for stop, color in pal["grad"]:
            c = QtGui.QColor(color)
            h, sat, val, a = c.getHsvF()
            if h < 0.0:
                h = 0.0
            c.setHsvF((h + hue) % 1.0, sat, val, 1.0)
            grad.setColorAt(stop, c)
        return grad

    def _at_ultra(self):
        return self._value >= 1.0 - 1e-6

    def _fx_target(self):
        return 1.0 if self._at_ultra() else 0.0

    def _node_alpha(self):
        """档位小点的透明度：拉满 Ultra 后淡出（Ultra 时不显示节点）。"""
        return max(0.0, 1.0 - self._fx)

    def _node_stops(self):
        """要画刻度的档位：去掉两端（0 和 1 是轨道首尾，拇指会停在那儿）。"""
        return list(self._stops[1:-1]) if self._stops and len(self._stops) > 2 else []

    def _paint_nodes(self, p, tr, fill_right, pal):
        """档位刻度：短竖条，让「一格一格」看得见，又不会和星星混在一起。

        拖动时把「松手会落到的那一格」点亮一圈，指明吸附目标。
        两端（0 和 1）不画：那是轨道首尾，拇指本来就会停在那儿。
        """
        if not self._stops or not self._stops_visible:
            return
        fade = self._node_alpha()
        if fade <= 0.01:            # 拉满 Ultra：节点隐藏
            return
        cy = tr.center().y()
        r = _u(2.5) * (1.0 + 0.20 * self._hover)
        target = None
        if self._drag:
            target = self._snap_with_direction(self._value, self._drag_dir)
        p.setPen(QtCore.Qt.NoPen)
        p.save()
        p.setClipPath(_rounded(tr, tr.height() / 2.0))   # 点不越出胶囊两端
        for s in self._node_stops():
            sx = tr.left() + tr.width() * s
            on = sx <= fill_right - 0.5
            if target is not None and abs(s - target) < 1e-6:
                ring = pal["node_ring"]
                p.setBrush(QtGui.QColor(ring[0], ring[1], ring[2], int(ring[3] * fade)))
                p.drawEllipse(QtCore.QPointF(sx, cy), _u(7.2), _u(7.2))
            col = pal["node_on"] if on else pal["node_off"]
            p.setBrush(QtGui.QColor(col[0], col[1], col[2], int(col[3] * fade)))
            p.drawEllipse(QtCore.QPointF(sx, cy), r, r)
        p.restore()

    def _paint_stars(self, p, clip_path, track_rect, fill_right, alpha_scale=1.0):
        now = time.perf_counter()
        p.save()
        p.setClipPath(clip_path)
        p.setPen(QtCore.Qt.NoPen)
        for xn, yn, r, alpha, phase, spd, big in self._stars:
            sx = track_rect.left() + xn * track_rect.width()
            if sx > fill_right - 1.0:          # 只画已填充区域内的星点
                continue
            sy = track_rect.top() + yn * track_rect.height()
            k = 0.15 + 0.85 * (0.5 + 0.5 * math.sin((now - self._t0) * spd + phase))
            a = int(alpha * k * alpha_scale)
            if a <= 2:
                continue
            rad = r * _UI
            if big:
                # 大星：一圈很淡的晕 + 一道短十字星芒，核心还是实心点（别糊成云）
                halo = QtGui.QRadialGradient(QtCore.QPointF(sx, sy), rad * 2.8)
                halo.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(a * 0.18)))
                halo.setColorAt(0.45, QtGui.QColor(255, 255, 255, int(a * 0.10)))
                halo.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                p.setBrush(QtGui.QBrush(halo))
                p.drawEllipse(QtCore.QPointF(sx, sy), rad * 2.8, rad * 2.8)
                p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, int(a * 0.40)), 0.8))
                p.drawLine(QtCore.QPointF(sx - rad * 2.4, sy), QtCore.QPointF(sx + rad * 2.4, sy))
                p.drawLine(QtCore.QPointF(sx, sy - rad * 1.7), QtCore.QPointF(sx, sy + rad * 1.7))
                p.setPen(QtCore.Qt.NoPen)
            rr = rad * (1.15 if big else 1.0)
            p.setBrush(QtGui.QColor(255, 255, 255, min(255, a + 10)))
            p.drawEllipse(QtCore.QPointF(sx, sy), rr, rr)
        p.restore()

    def _paint_thumb(self, p, cx, cy, pal):
        # 纯白圆点：不加光圈、不加落影，背景保持轨道原样
        grow = 1.0 + 0.08 * self._hover
        r = _u(_THUMB_R) * grow
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(pal["thumb"]))
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)


class GradientText(QtWidgets.QWidget):
    """横向渐变填充的单行文字（标题用）。"""

    def __init__(self, text="", dark=True, size=17, bold=True, parent=None):
        super().__init__(parent)
        self._text = str(text)
        self._dark = bool(dark)
        f = self.font()
        f.setPixelSize(_u(size))
        f.setBold(bool(bold))
        self.setFont(f)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

    def setText(self, text):
        text = str(text)
        if text == self._text:
            return
        self._text = text
        self.updateGeometry()
        self.update()

    def text(self):
        return self._text

    def set_dark(self, dark):
        self._dark = bool(dark)
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        return QtCore.QSize(fm.horizontalAdvance(self._text) + _u(4), fm.height() + _u(2))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        if not self._text:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        pal = DARK if self._dark else LIGHT
        fm = QtGui.QFontMetricsF(self.font())
        w = fm.horizontalAdvance(self._text)
        x = (self.width() - w) / 2.0
        y = (self.height() - fm.height()) / 2.0 + fm.ascent()
        path = QtGui.QPainterPath()
        path.addText(x, y, self.font(), self._text)
        grad = QtGui.QLinearGradient(0.0, 0.0, max(1.0, w), 0.0)
        grad.setColorAt(0.0, QtGui.QColor(pal["title"][0]))
        grad.setColorAt(1.0, QtGui.QColor(pal["title"][1]))
        p.fillPath(path, QtGui.QBrush(grad))


class StarSliderCard(QtWidgets.QWidget):
    """截图那张卡：圆角深色底 + 渐变标题 + 灰色副标题 + 星空滑块。"""

    valueChanged = QtCore.Signal(float)

    def __init__(self, title="Ultra", sub="6 Astra ›", parent=None, dark=True, value=1.0,
                 stops=4):
        super().__init__(parent)
        self._dark = bool(dark)
        self._stops = _norm_stops(stops)
        self._title = GradientText(title, dark=dark, parent=self)
        self._sub = QtWidgets.QLabel(sub, self)
        self._sub.setAlignment(QtCore.Qt.AlignCenter)
        self._sub.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._slider = StarSlider(self, value=value, dark=dark, stops=stops)
        self._slider.valueChanged.connect(self._on_value)

        f = self._sub.font()
        f.setPixelSize(_u(12))
        self._sub.setFont(f)
        self._paint_sub_color()

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_u(14), _u(12), _u(14), _u(12))
        lay.setSpacing(0)
        lay.addWidget(self._title, 0, QtCore.Qt.AlignHCenter)
        lay.addSpacing(_u(2))
        lay.addWidget(self._sub, 0, QtCore.Qt.AlignHCenter)
        lay.addSpacing(_u(10))
        lay.addWidget(self._slider)
        self.setMinimumWidth(_u(280))
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    # ---------------- 对外 ----------------
    def slider(self):
        return self._slider

    def value(self):
        return self._slider.value()

    def stops(self):
        return list(self._stops) if self._stops else []

    def set_stops_visible(self, on):
        """档位点是否显示（默认显示；单点连续滑块本来就没有档位点）。"""
        on = bool(on)
        if on == self._stops_visible:
            return
        self._stops_visible = on
        self.update()

    def set_stops(self, stops):
        self._stops = _norm_stops(stops)
        self._slider.set_stops(stops)
        self.setValue(self._slider.value())

    def setValue(self, v, animate=False):
        self._slider.setValue(v, animate)

    def set_value(self, v, animate=False):
        self.setValue(v, animate)

    def set_title(self, text):
        self._title.setText(text)

    def title(self):
        return self._title.text()

    def set_sub(self, text):
        self._sub.setText(str(text))

    def sub(self):
        return self._sub.text()

    def set_dark(self, dark):
        self._dark = bool(dark)
        self._title.set_dark(dark)
        self._slider.set_dark(dark)
        self._paint_sub_color()
        self.update()

    def _paint_sub_color(self):
        pal = DARK if self._dark else LIGHT
        self._sub.setStyleSheet("color: %s; background: transparent;" % pal["sub"])

    def _on_value(self, v):
        self.valueChanged.emit(v)

    def sizeHint(self):
        h = (self.layout().contentsMargins().top() + self._title.sizeHint().height()
             + _u(2) + self._sub.sizeHint().height() + _u(10) + _u(38)
             + self.layout().contentsMargins().bottom())
        return QtCore.QSize(_u(300), h)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pal = DARK if self._dark else LIGHT
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = _rounded(rect, _u(14))
        p.fillPath(path, QtGui.QColor(pal["card"]))
        p.setPen(QtGui.QPen(QtGui.QColor(*pal["border"]), 1.0))
        p.drawPath(path)


class StarSliderDemo(QtWidgets.QWidget):
    """预览窗口：卡片 + 下面一排「选择推理强度」药丸和白色方停按钮（对齐参考图）。"""

    # 四档等距，标题按最近的档位取
    NODES = ((1.0, "Ultra"), (2.0 / 3.0, "High"), (1.0 / 3.0, "Medium"), (0.0, "Low"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("StarSlider 预览")
        self.resize(_u(360), _u(232))
        self.setStyleSheet("StarSliderDemo { background: #0f0f11; }")

        self.card = StarSliderCard("Ultra", "6 Astra ›", self, dark=True, value=1.0)
        self.card.valueChanged.connect(self._on_value)
        try:   # 窗口标题顺带显示识别到的屏幕刷新率，方便确认自适应生效
            hz = self.card.slider()._refresh_hz()
            self.setWindowTitle("StarSlider 预览 · %dHz" % int(round(hz)))
        except Exception:
            pass

        self.pill = QtWidgets.QPushButton("选择推理强度   ⌄", self)
        self.pill.setCursor(QtCore.Qt.PointingHandCursor)
        self.pill.setFixedHeight(_u(32))
        self.pill.setStyleSheet(
            "QPushButton { background: #2c2c2e; color: #b8b8bd; border: none;"
            " border-radius: %dpx; padding: 0 %dpx; font-size: %dpx; }"
            "QPushButton:hover { background: #343437; }" % (_u(16), _u(14), _u(12)))
        self.pill.clicked.connect(self._cycle)

        self.stop = QtWidgets.QPushButton("■", self)
        self.stop.setCursor(QtCore.Qt.PointingHandCursor)
        self.stop.setFixedSize(_u(32), _u(32))
        self.stop.setStyleSheet(
            "QPushButton { background: #ffffff; color: #1c1c1e; border: none;"
            " border-radius: %dpx; font-size: %dpx; }" % (_u(10), _u(11)))

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(_u(8))
        row.addStretch(1)
        row.addWidget(self.pill)
        row.addWidget(self.stop)
        row.addStretch(1)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_u(16), _u(16), _u(16), _u(16))
        lay.setSpacing(_u(12))
        lay.addWidget(self.card)
        lay.addLayout(row)
        lay.addStretch(1)

    def _on_value(self, v):
        name = min(self.NODES, key=lambda p: abs(p[0] - float(v)))[1]
        self.card.set_title(name)

    def _cycle(self):
        """按钮点一下降一档，到底回到最高档（参考图里那排档位是往下选的）。"""
        slider = self.card.slider()
        cur = self.card.value()
        nxt = slider.step(-1)
        if nxt is None:                       # 连续模式：退化成 0.1 递减
            nxt = cur - 0.1
        elif abs(nxt - cur) < 1e-6:           # 已经是最低档 → 回到最高档
            nxt = slider.stops()[-1]
        self.card.setValue(nxt, animate=True)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    shot = None
    if "--shot" in argv:
        shot = argv[argv.index("--shot") + 1]
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv)
    win = StarSliderDemo()
    win.show()
    if shot:
        win.card.set_title("Ultra")
        win.card.setValue(1.0)
        for _ in range(6):
            app.processEvents()
        time.sleep(0.05)
        for _ in range(6):
            app.processEvents()
        win.grab().save(shot)
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

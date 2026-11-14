# ui/qt_main/flip_button.py
"""Qt 主界面的「开始迁移」3D 翻转按钮（对齐 Tk 版 ui/flip_button.py 的视觉与手感）。

三面语义同 Tk 版：正面绿（🚀 开始迁移）/ 正面黄（模拟运行打开时）/ 背面红（⏳ 迁移中…）。
点下去翻红、跑完翻回；翻转用真透视（四角 3D 旋转 → 相机投影 → QTransform.quadToQuad 贴面），
90° 时收成一条 2px 的 accent 亮线。手感三档与 Tk 共用配置键 start_btn_feel。

Tk 版要 PIL 烤帧表、Tk text item 补字体回退；Qt 的 QPainter 有抗锯齿、字体回退和
透视变换，所以这里每帧现画，不需要帧表。

⚠ 本模块只 import PySide6（无 tkinter）。对外接口兼容 QPushButton 的常用部分
（clicked / setText / text / setEnabled），主窗口原来对 start_btn 的调用不用改。
"""
import math
import time

from PySide6 import QtCore, QtGui, QtWidgets

# ---- 配色（与 ui/flip_button.py 同值）----
FACE_IDLE = {"face_top": "#1b2a20", "face_bot": "#0a120d",
             "face_top_hover": "#25382b", "face_bot_hover": "#0f1a13",
             "edge": "#3d5647", "edge_hot": "#7ee0a8", "accent": "#46cf8a",
             "title": "#eefaf2"}
FACE_SEARCH = {"face_top": "#2c1a1a", "face_bot": "#120a0a",
               "face_top_hover": "#3d2424", "face_bot_hover": "#1a0f0f",
               "edge": "#5c3c3c", "edge_hot": "#ff9b9b", "accent": "#ff5a5a",
               "title": "#fdeeee"}
FACE_DRY = {"face_top": "#2a2517", "face_bot": "#12100a",
            "face_top_hover": "#3a3320", "face_bot_hover": "#191510",
            "edge": "#5a5136", "edge_hot": "#ffd76e", "accent": "#f5c451",
            "title": "#fffaf0"}
FACE_DISABLED = {"face_top": "#262626", "face_bot": "#111111",
                 "face_top_hover": "#262626", "face_bot_hover": "#111111",
                 "edge": "#4a4a4a", "edge_hot": "#4a4a4a", "accent": "#6e6e6e",
                 "title": "#9a9a9a"}
FACE_IDLE_L = {"face_top": "#e9f6ee", "face_bot": "#cde7d8",
               "face_top_hover": "#f3fbf6", "face_bot_hover": "#d8eee1",
               "edge": "#9dbfab", "edge_hot": "#1f9d55", "accent": "#1f9d55",
               "title": "#0d2a19"}
FACE_SEARCH_L = {"face_top": "#fbebeb", "face_bot": "#f5d3d3",
                 "face_top_hover": "#fdf5f5", "face_bot_hover": "#f8dede",
                 "edge": "#d9a3a3", "edge_hot": "#cc2b2b", "accent": "#c62828",
                 "title": "#4a0f0e"}
FACE_DRY_L = {"face_top": "#fbf4e2", "face_bot": "#f2e4c3",
              "face_top_hover": "#fdfaf0", "face_bot_hover": "#f7ecd2",
              "edge": "#d3c39a", "edge_hot": "#b8791a", "accent": "#b8791a",
              "title": "#3b2b09"}
FACE_DISABLED_L = {"face_top": "#f2f2f2", "face_bot": "#e4e4e4",
                   "face_top_hover": "#f2f2f2", "face_bot_hover": "#e4e4e4",
                   "edge": "#cfcfcf", "edge_hot": "#cfcfcf", "accent": "#bdbdbd",
                   "title": "#8a8a8a"}

# 三档手感（与 Tk 版 PRESETS 同值；配置键也相同）
PRESETS = {
    "crisp":    {"ms": 340, "over": 1.20, "sink": 0.05},
    "standard": {"ms": 460, "over": 0.85, "sink": 0.06},
    "heavy":    {"ms": 720, "over": 0.45, "sink": 0.10},
}
FEELS = ("crisp", "standard", "heavy")
DEFAULT_FEEL = "standard"
FEEL_LABELS = {"crisp": "干脆", "standard": "标准", "heavy": "沉"}
FEEL_KEY = "start_btn_feel"

_RADIUS = 8.0
_ACCENT_W = 3.0
_ACCENT_INSET_X = 4.0
_ACCENT_INSET_Y = 6.0
_BORDER_W = 1.2
_BORDER_INSET = 0.8
_CAM = 900.0
_SHADE_MAX = 150 / 255.0
_SWEEP_MS = 780.0
_SWEEP_PEAK = 80
_SWEEP_HALF_RATIO = 110.0 / 420.0
_STRIPE_ALPHA = 26
_STRIPE_W = 6.0
_STRIPE_STEP = 28.0
_STRIPE_SPEED = 55.0
_PROGRESS_H = 3.0
_PROGRESS_MIN = 0.06
_TEXT_LO, _TEXT_HI = 0.15, 0.45
_HOVER_IN_MS, _HOVER_OUT_MS = 90.0, 150.0
_TICK_MS = 8


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def _ease_out_back(t, s):
    """ease-out-back：末端冲过头再回弹（与 Tk 版同公式）。"""
    t = _clamp(t)
    u = t - 1.0
    return 1.0 + (s + 1.0) * u * u * u + s * u * u


def _mix(c1, c2, k):
    a, b = QtGui.QColor(c1), QtGui.QColor(c2)
    k = _clamp(k)
    return QtGui.QColor(int(a.red() + (b.red() - a.red()) * k),
                        int(a.green() + (b.green() - a.green()) * k),
                        int(a.blue() + (b.blue() - a.blue()) * k))


def project(w, h, ang, sink):
    """矩形四角绕水平中轴旋转 ang 后的透视投影（左上、右上、右下、左下）。与 Tk 版同公式。"""
    c, s = math.cos(ang), math.sin(ang)
    k0 = 1.0 - sink * abs(s)
    out = []
    for x, y in ((0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))):
        dy = y - h / 2.0
        k = _CAM / (_CAM + dy * s) * k0
        out.append(QtCore.QPointF((x - w / 2.0) * k + w / 2.0, dy * c * k + h / 2.0))
    return out


def _schemes(dark):
    if dark:
        return {"idle": FACE_IDLE, "dry": FACE_DRY, "back": FACE_SEARCH, "off": FACE_DISABLED}
    return {"idle": FACE_IDLE_L, "dry": FACE_DRY_L, "back": FACE_SEARCH_L, "off": FACE_DISABLED_L}


class FlipButton(QtWidgets.QPushButton):
    """三面翻转按钮。f：0 = 正面朝外，1 = 背面朝外（翻转途中带过冲，可略超出 0~1）。"""

    def __init__(self, text="", parent=None, dark=True, feel=DEFAULT_FEEL, dry_run=False):
        super().__init__(text, parent)
        self._labels = {"front": str(text), "back": ""}
        self._dark = bool(dark)
        self._feel = feel if feel in PRESETS else DEFAULT_FEEL
        self._dry = bool(dry_run)
        self._f = 0.0                 # 当前翻面进度
        self._target = "front"        # 正在/已经朝外的那一面
        self._anim_t0 = None
        self._anim_dir = 0
        self._done_cb = None
        self._knife_once = False
        self._last_f = 0.0
        self._hv = 0.0                # hover 量
        self._hv_target = 0.0
        self._sweep_t0 = None
        self._back_t0 = time.perf_counter()
        self._progress = None         # None = 不定进度（脉冲），float = 0~1
        self._t_last = time.perf_counter()
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        f = self.font()
        f.setBold(True)
        f.setPixelSize(15)
        self.setFont(f)

    # ---------------- 对外 ----------------
    def setText(self, text):
        """改「正朝外 / 正翻向」那一面的文字（兼容主窗口原来对 QPushButton 的调用）。"""
        self._labels[self._target] = str(text)
        super().setText(str(text))
        self.updateGeometry()
        self.update()

    def text(self):
        return self._labels.get(self._target, "")

    def is_back(self):
        return self._target == "back"

    def flip_to_back(self, text=None):
        if text is not None:
            self._labels["back"] = str(text)
        if self._target == "back":
            self.update()
            return
        self._back_t0 = time.perf_counter()
        self._start_flip("back", +1)

    def flip_to_front(self, text=None, done=None):
        if text is not None:
            self._labels["front"] = str(text)
        self._done_cb = done
        if self._target == "front" and self._anim_t0 is None:
            self._finish_cb()
            self.update()
            return
        self._start_flip("front", -1)

    def set_progress(self, frac):
        """背面底部进度条：None = 不定进度（来回脉冲），0~1 = 实际进度。"""
        self._progress = None if frac is None else _clamp(float(frac))
        self.update()

    def set_dry_run(self, flag):
        self._dry = bool(flag)
        self.update()

    def set_feel(self, feel):
        if feel in PRESETS:
            self._feel = feel

    def get_feel(self):
        return self._feel

    def set_dark(self, dark):
        self._dark = bool(dark)
        self.update()

    def sizeHint(self):
        fm = QtGui.QFontMetrics(self.font())
        w = max(fm.horizontalAdvance(self._labels["front"]),
                fm.horizontalAdvance(self._labels["back"] or "")) + 2 * 18 + 8
        return QtCore.QSize(max(120, w), 38)

    def minimumSizeHint(self):
        return self.sizeHint()

    # ---------------- 事件 ----------------
    def enterEvent(self, e):
        if self.isEnabled():
            self._hv_target = 1.0
            self._sweep_t0 = time.perf_counter()
            self._ensure_tick()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hv_target = 0.0
        self._ensure_tick()
        super().leaveEvent(e)

    def changeEvent(self, e):
        if e.type() == QtCore.QEvent.EnabledChange:
            self.setCursor(QtCore.Qt.PointingHandCursor if self.isEnabled()
                           else QtCore.Qt.ArrowCursor)
            if not self.isEnabled():
                self._hv_target = 0.0
                self._ensure_tick()
            self.update()
        super().changeEvent(e)

    # ---------------- 动画 ----------------
    def _start_flip(self, target, direction):
        self._target = target
        self._anim_dir = direction
        self._anim_t0 = time.perf_counter()
        self._last_f = self._f
        super().setText(self._labels[target])
        self._ensure_tick()

    def _finish_cb(self):
        cb, self._done_cb = self._done_cb, None
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def _ensure_tick(self):
        want = self._wanted_interval()
        if want is None:
            self._timer.stop()
            return
        if not self._timer.isActive():
            self._t_last = time.perf_counter()
        if self._timer.interval() != want or not self._timer.isActive():
            self._timer.start(want)

    def _wanted_interval(self):
        """只在真有东西在动时起表：翻转 / hover 渐变 / 扫光用 8ms，背面斜纹 33ms，否则停表。"""
        now = time.perf_counter()
        if self._anim_t0 is not None or abs(self._hv - self._hv_target) > 1e-3:
            return _TICK_MS
        if self._sweep_t0 is not None and (now - self._sweep_t0) * 1000.0 < _SWEEP_MS \
                and self._target == "front" and self._hv > 0:
            return _TICK_MS
        if self._target == "back" and self.isVisible():
            return 33
        return None

    def _tick(self):
        now = time.perf_counter()
        dt = (now - self._t_last) * 1000.0
        self._t_last = now
        # hover 线性渐变：淡入 90ms / 淡出 150ms
        if self._hv < self._hv_target:
            self._hv = min(self._hv_target, self._hv + dt / _HOVER_IN_MS)
        elif self._hv > self._hv_target:
            self._hv = max(self._hv_target, self._hv - dt / _HOVER_OUT_MS)
        if self._anim_t0 is not None:
            p = PRESETS[self._feel]
            t = (now - self._anim_t0) * 1000.0 / float(p["ms"])
            e = _ease_out_back(t, p["over"])
            f = e if self._anim_dir > 0 else 1.0 - e
            # 半程必须画到一帧 90°（2px 亮线）：帧时间不一定正好落在 0.5，越过时补一帧
            if (self._last_f - 0.5) * (f - 0.5) <= 0 and self._last_f != f:
                self._knife_once = True
            self._last_f = f
            self._f = f
            if t >= 1.0:
                self._f = 1.0 if self._anim_dir > 0 else 0.0
                self._anim_t0 = None
                if self._target == "front":
                    self._finish_cb()
        self.update()
        want = self._wanted_interval()
        if want is None:
            self._timer.stop()
        elif want != self._timer.interval():
            self._timer.start(want)

    # ---------------- 画 ----------------
    def _palette(self, side, animating):
        sc = _schemes(self._dark)
        if side == "back":
            return sc["back"]               # 背面永不灰（否则「迁移中」只剩一块灰）
        if not self.isEnabled() and not animating:
            return sc["off"]                # 翻转过程不切灰（否则前半程闪一下）
        return sc["dry"] if self._dry else sc["idle"]

    def _face_path(self, w, h):
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(_BORDER_INSET, _BORDER_INSET,
                                          w - 2 * _BORDER_INSET, h - 2 * _BORDER_INSET),
                            _RADIUS, _RADIUS)
        return path

    def _draw_face(self, p, w, h, side, pal, hv, text_alpha, shade=0.0, now=None):
        now = time.perf_counter() if now is None else now
        path = self._face_path(w, h)
        grad = QtGui.QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, _mix(pal["face_top"], pal["face_top_hover"], hv))
        grad.setColorAt(1.0, _mix(pal["face_bot"], pal["face_bot_hover"], hv))
        p.fillPath(path, QtGui.QBrush(grad))

        p.save()
        p.setClipPath(path)
        if side == "back":
            # 斜纹按真实时间流动
            off = ((now - self._back_t0) * _STRIPE_SPEED) % _STRIPE_STEP
            sc = QtGui.QColor(255, 255, 255, _STRIPE_ALPHA) if self._dark \
                else QtGui.QColor(0, 0, 0, _STRIPE_ALPHA)
            sp = QtGui.QPen(sc)
            sp.setWidthF(_STRIPE_W)
            p.setPen(sp)
            x = -h - _STRIPE_STEP
            while x < w + h:
                x0 = x + off
                p.drawLine(QtCore.QPointF(x0, h), QtCore.QPointF(x0 + h, 0))
                x += _STRIPE_STEP
            # 底部进度条：不定进度时来回脉冲（cos 周期 2.417s，同 Tk 版）
            if self._progress is None:
                ph = (now - self._back_t0) * (2.0 * math.pi / 2.417)
                frac = max(_PROGRESS_MIN, 0.5 - 0.5 * math.cos(ph))
            else:
                frac = max(_PROGRESS_MIN, self._progress)
            bg = QtGui.QLinearGradient(0, 0, w, 0)
            bg.setColorAt(0.0, QtGui.QColor("#e53935"))
            bg.setColorAt(1.0, QtGui.QColor("#ff7043"))
            p.fillRect(QtCore.QRectF(0, h - _PROGRESS_H - _BORDER_INSET, w * frac, _PROGRESS_H),
                       QtGui.QBrush(bg))
        elif hv > 0 and self._sweep_t0 is not None:
            # 悬停白色斜扫光：半宽按比例取（照抄绝对 110px 会把窄按钮整面刷白）
            t = (now - self._sweep_t0) * 1000.0 / _SWEEP_MS
            if 0.0 <= t < 1.0:
                half = w * _SWEEP_HALF_RATIO
                cx = -half + (w + 2 * half) * t
                sg = QtGui.QLinearGradient(cx - half, h, cx + half, 0)
                peak = QtGui.QColor(255, 255, 255, int(_SWEEP_PEAK * hv))
                clear = QtGui.QColor(255, 255, 255, 0)
                sg.setColorAt(0.0, clear)
                sg.setColorAt(0.5, peak)
                sg.setColorAt(1.0, clear)
                p.fillRect(QtCore.QRectF(0, 0, w, h), QtGui.QBrush(sg))
        if shade > 0:
            p.fillRect(QtCore.QRectF(0, 0, w, h), QtGui.QColor(0, 0, 0, int(255 * shade)))
        p.restore()

        # 左侧竖条（内缩胶囊）
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(pal["accent"]))
        p.drawRoundedRect(QtCore.QRectF(_ACCENT_INSET_X, _ACCENT_INSET_Y, _ACCENT_W,
                                        max(2.0, h - 2 * _ACCENT_INSET_Y)),
                          _ACCENT_W / 2.0, _ACCENT_W / 2.0)
        # 细描边：hover 时 lerp(edge, edge_hot)
        pen = QtGui.QPen(_mix(pal["edge"], pal["edge_hot"], hv))
        pen.setWidthF(_BORDER_W)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawPath(path)

        if text_alpha > 0.02:
            c = QtGui.QColor(pal["title"])
            c.setAlphaF(_clamp(text_alpha))
            p.setPen(c)
            p.setFont(self.font())
            p.drawText(QtCore.QRectF(_ACCENT_INSET_X + _ACCENT_W, 0,
                                     w - _ACCENT_INSET_X - _ACCENT_W, h),
                       QtCore.Qt.AlignCenter, self._labels.get(side, ""))

    def _draw_knife(self, p, w, h, ang, side):
        pal = self._palette(side, True)
        q = project(w, h, ang, PRESETS[self._feel]["sink"])
        xs = [pt.x() for pt in q]
        x0, x1 = min(xs), max(xs)
        if x1 - x0 < 2:
            x0, x1 = w / 2.0 - 1, w / 2.0 + 1
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(pal["accent"]))
        p.drawRoundedRect(QtCore.QRectF(x0, h / 2.0 - 1.0, x1 - x0, 2.0), 1.0, 1.0)

    def paintEvent(self, _e):
        w, h = float(self.width()), float(self.height())
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        f = self._f
        side = "front" if f < 0.5 else "back"
        ang = (f * math.pi) if side == "front" else ((f - 1.0) * math.pi)
        ang = max(-math.pi, min(math.pi, ang))
        animating = self._anim_t0 is not None
        pal = self._palette(side, animating)
        now = time.perf_counter()
        if self._knife_once or (animating and abs(math.cos(ang)) < 0.02):
            self._knife_once = False
            self._draw_knife(p, w, h, math.pi / 2.0, self._target)
            p.end()
            return
        if abs(ang) < 1e-4:
            # 静止：直接画（字最清楚，hover / 扫光只在这里出现）
            hv = self._hv if (side == "front" and self.isEnabled()) else 0.0
            self._draw_face(p, w, h, side, pal, hv, 1.0, now=now)
            p.end()
            return
        # 翻转中：整面先画进图，再按透视四边形贴上去（真透视，不是压扁）
        dpr = max(1.0, self.devicePixelRatioF())
        img = QtGui.QImage(int(math.ceil(w * dpr)), int(math.ceil(h * dpr)),
                           QtGui.QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(QtCore.Qt.transparent)
        ip = QtGui.QPainter(img)
        ip.setRenderHint(QtGui.QPainter.Antialiasing, True)
        ip.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        c = abs(math.cos(ang))
        text_alpha = _clamp((c - _TEXT_LO) / (_TEXT_HI - _TEXT_LO))
        self._draw_face(ip, w, h, side, pal, 0.0, text_alpha,
                        shade=abs(math.sin(ang)) * _SHADE_MAX, now=now)
        ip.end()
        src = QtGui.QPolygonF([QtCore.QPointF(0, 0), QtCore.QPointF(w, 0),
                               QtCore.QPointF(w, h), QtCore.QPointF(0, h)])
        dst = QtGui.QPolygonF(project(w, h, ang, PRESETS[self._feel]["sink"]))
        tr = QtGui.QTransform()
        if QtGui.QTransform.quadToQuad(src, dst, tr):
            p.setTransform(tr)
            p.drawImage(QtCore.QRectF(0, 0, w, h), img)
        else:
            self._draw_knife(p, w, h, math.pi / 2.0, side)
        p.end()

# ui/qt_main/grad_button.py
"""Qt 版渐变按钮:逐条复刻 Tk `utils/helpers.create_gradient_button` 的绘制。

为什么不用 QSS:Tk 的按钮是 Canvas 自绘的,它的样子里有四件事 QSS 表达不了 ——
  · 彩色「面」比控件四周各小 3/4 物理像素(PADX/PADY = 1.5/2),浮起/沉下都在这个余量里走;
  · 悬停是「整面向白色混 30%」并浮起 1.6px、扫一道光、落三层阴影;
  · 按下**不变暗**,只是沉下去 1.4px(Tk 只有位移,没有换色);
  · 开头的 emoji 单独画、上提 3px,和文字留 6px —— QSS 只有一整串文字。
这四件都在这里按 Tk 的同一套数值画。

尺寸约定(这一条最容易错):
  Tk 的 `height=30` 是**物理像素**(Tk 的控件尺寸不随 DPI 缩放,只有字体按磅值缩放),
  所以 Qt 这边一律用 `tk(30)` = 30 ÷ 屏幕缩放 = 24 逻辑像素;字体仍用磅值(9 磅)。
  这样同一台机器上两边的按钮高、字高、内边距逐像素一致。见 `tk()`。

宽度:Tk 的 `_btn_text_width` 有个缓存 bug —— 第一颗按钮是 15 号的 ⚙,之后**所有**按钮
都按 15 号量宽(实测 4 字按钮 153 物理像素、7 字 228)。为了和 Tk 现在的观感一致,这里
按同一条规则量:15 磅、emoji 记 28 逻辑像素、左右合计 18 物理像素。
"""
import math

from PySide6 import QtCore, QtGui, QtWidgets

from ui.qt_main import look
from ui.qt_frame_anim import FrameAnim

PADX, PADY = 1.5, 2.0        # Tk:面四周留白(物理像素)
SYM_GAP = 6                  # Tk:图标和文字之间的空隙(物理像素)
SYM_DY = 3                   # Tk:图标上提(物理像素)
MEASURE_PT = 15.0            # Tk 量宽缓存里那颗字体(见模块说明)
TEXT_PAD = 18                # Tk _BTN_TEXT_PAD(左右合计)
MIN_W = 44                   # Tk 下限(物理像素)
EMOJI_W = 28                 # Tk 量 emoji 的占宽换成逻辑像素(35 物理 ÷ 1.25)


def _dpr():
    """屏幕缩放(逻辑像素 → 物理像素)。取不到就当 1。"""
    try:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            scr = app.primaryScreen()
            if scr is not None:
                return max(0.5, float(scr.devicePixelRatio()))
    except Exception:
        pass
    return 1.0


def tk(value):
    """Tk 的物理像素 → Qt 逻辑像素(见模块说明的尺寸约定)。"""
    try:
        return float(value) / _dpr()
    except Exception:
        return float(value)


def _mix(c0, c1, t):
    a, b = QtGui.QColor(c0), QtGui.QColor(c1)
    t = max(0.0, min(1.0, t))
    return QtGui.QColor(int(a.red() + (b.red() - a.red()) * t),
                        int(a.green() + (b.green() - a.green()) * t),
                        int(a.blue() + (b.blue() - a.blue()) * t))


def _bg_of(theme):
    return (theme or {}).get("bg", "#f0f0f0")


def disabled_colors(theme):
    """禁用态:面色 tone(容器底, 0.14)、字色 tone(容器底, 0.68)(同 Tk)。"""
    bg = _bg_of(theme)
    return look.tone(bg, 0.14), look.tone(bg, 0.68)


# 开头的 emoji/符号(和 Tk helpers._split_lead_symbol 的字符表一致)
_SYM_CHARS = (set(chr(c) for c in range(0x2190, 0x2200)) |      # 箭头
              set(chr(c) for c in range(0x2300, 0x2400)) |      # 技术符号(⏳ 等)
              set(chr(c) for c in range(0x25A0, 0x27C0)) |      # 几何/杂项符号
              set(chr(c) for c in range(0x2B00, 0x2C00)) |      # 箭头补充
              set(chr(c) for c in range(0x1F000, 0x1FB00)))     # emoji


def split_lead_symbol(text):
    """'🔍 扫描差异' → ('🔍', '扫描差异');开头不是符号就返回 (None, text)。"""
    s = str(text or "")
    if len(s) >= 3 and s[0] in _SYM_CHARS and s[1] == " ":
        return s[0], s[2:]
    return None, s


def _clean(text):
    """去掉变体选择符 U+FE0F(同 Tk:它会被画成一块空白)。"""
    return str(text or "").replace("️", "")


def measure_width(text, dpr=None):
    """按钮宽度(逻辑像素):复刻 Tk `max(44, 量宽@15磅 + 18)` 那条规则。"""
    dpr = _dpr() if dpr is None else dpr
    s = _clean(text)
    lead, rest = split_lead_symbol(s)
    f = QtGui.QFont("Microsoft YaHei")
    f.setPointSizeF(MEASURE_PT)
    fm = QtGui.QFontMetrics(f)
    if lead and rest:
        adv = EMOJI_W + fm.horizontalAdvance(rest)
    else:
        adv = fm.horizontalAdvance(s)
    need = adv + TEXT_PAD / dpr
    return int(round(max(MIN_W / dpr, need)))


class _IconPainter:
    """太阳/月亮图标(同 Tk helpers.make_theme_icon 的几何)。"""

    @staticmethod
    def paint(kind, painter, rect, color):
        S = min(rect.width(), rect.height())
        c = QtCore.QPointF(rect.center())
        p = painter
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        col = QtGui.QColor(color)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        if kind == "sun":
            r_core = 0.22 * S
            p.drawEllipse(c, r_core, r_core)
            w = max(1.0, round(0.10 * S))
            r0, r1 = 0.32 * S, 0.48 * S
            pen = QtGui.QPen(col, w)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            for i in range(8):
                a = math.radians(i * 45)
                p.drawLine(QtCore.QPointF(c.x() + r0 * math.cos(a), c.y() + r0 * math.sin(a)),
                           QtCore.QPointF(c.x() + r1 * math.cos(a), c.y() + r1 * math.sin(a)))
        else:
            # 月牙 = 大圆减掉一个偏移的圆
            pad = 0.09 * S
            big = QtCore.QRectF(rect.center().x() - S / 2 + pad, rect.center().y() - S / 2 + pad,
                                S - 2 * pad, S - 2 * pad)
            path = QtGui.QPainterPath()
            path.addEllipse(big)
            cut = QtGui.QPainterPath()
            off = 0.33 * S
            cut.addEllipse(big.translated(off, -0.14 * S))
            p.fillPath(path.subtracted(cut), col)


class GradientButton(QtWidgets.QPushButton):
    """自绘渐变按钮。接口仍按 QPushButton 用(text/clicked/setEnabled/setFixedSize)。"""

    def __init__(self, text="", kind=None, theme=None, height=30, width=None,
                 font_pt=9.0, bold=True, icon_only=False, icon=None, parent=None,
                 colors=None):
        super().__init__("", parent)
        self._colors = tuple(colors) if colors else None   # 不传就按 kind 取
        self._text = str(text or "")
        self._kind = look.kind_for(kind) if kind else ""
        self.setProperty("kind", self._kind)      # 外部的按钮键映射仍按这个属性读
        self._theme = dict(theme or {})
        self._h = float(height)
        self._icon = icon                 # "sun" / "moon" / None
        self._icon_only = bool(icon_only)
        self._font_pt = float(font_pt)
        self._bold = bool(bold)
        self._explicit_w = width
        self._t = 0.0                     # 悬停进度(OutBack 会过冲到 1 出头)
        self._press = 0.0
        self._sweep = -1.0
        f = QtGui.QFont(self.font())
        f.setPointSizeF(self._font_pt)
        f.setBold(self._bold)
        self.setFont(f)
        self.setFixedHeight(int(round(tk(self._h))))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)      # Tk 的 Canvas 按钮键盘焦点不可达
        self._apply_width()
        self._a = FrameAnim(self)
        self._a.setDuration(190)
        self._a.setEasingCurve(QtCore.QEasingCurve.OutBack)
        self._a.valueChanged.connect(self._on_hover)
        self._sw = FrameAnim(self)
        self._sw.setDuration(340)
        self._sw.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._sw.valueChanged.connect(self._on_sweep)
        self._sw.finished.connect(self._sweep_done)
        self._pa = FrameAnim(self)
        self._pa.setDuration(90)
        self._pa.valueChanged.connect(self._on_press)

    # ---- 对外 ----
    def text(self):
        return self._text

    def setText(self, text):
        self._text = str(text or "")
        if self._explicit_w is None:
            self._apply_width()
        self.update()

    def set_kind(self, kind):
        self._kind = look.kind_for(kind) if kind else ""
        self.setProperty("kind", self._kind)
        self.update()

    def kind(self):
        return self._kind

    def colors(self):
        if self._colors:
            return self._colors
        return look.KIND_COLORS.get(self._kind or "", look.BTN_NEUTRAL)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def set_icon(self, icon):
        self._icon = icon
        if self._explicit_w is None:
            self._apply_width()
        self.update()

    def sizeHint(self):
        w = self._explicit_w if self._explicit_w is not None else self._width_now()
        return QtCore.QSize(int(w), int(round(tk(self._h))))

    def minimumSizeHint(self):
        return self.sizeHint()

    # ---- 尺寸 ----
    def _width_now(self):
        if self._icon_only:
            # Tk 实测:⚙(有字形,15 磅)46 物理像素;纯图标按钮(主题 ☀/🌙)44
            # 有像素图标的是 44(主题按钮);只有字形(⚙)的按量宽算成 46
            return int(round(tk(44 if self._icon is not None else 46)))
        return measure_width(self._text)

    def _apply_width(self):
        if self._explicit_w is not None:
            w = max(int(round(self._explicit_w)), self._width_now())
        else:
            w = self._width_now()
        self.setFixedWidth(int(w))

    # ---- 动画 ----
    def _anim(self, anim, target, duration=None, curve=None):
        if duration is not None:
            anim.setDuration(duration)
        if curve is not None:
            anim.setEasingCurve(curve)
        cur = anim.currentValue()
        anim.stop()
        anim.setStartValue(0.0 if cur is None else float(cur))
        anim.setEndValue(float(target))
        anim.start()

    def _on_hover(self, v):
        self._t = float(v)
        self.update()

    def _on_sweep(self, v):
        self._sweep = float(v)
        self.update()

    def _sweep_done(self):
        self._sweep = -1.0
        self.update()

    def _on_press(self, v):
        self._press = float(v)
        self.update()

    def enterEvent(self, e):
        if self.isEnabled():
            self._anim(self._a, 1.0)
            self._sweep = 0.0
            self._anim(self._sw, 1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim(self._a, 0.0, 150, QtCore.QEasingCurve.OutCubic)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._anim(self._pa, 1.0)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._anim(self._pa, 0.0, 200, QtCore.QEasingCurve.OutBack)
        super().mouseReleaseEvent(e)

    # ---- 绘制 ----
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        dpr = _dpr()
        t = max(0.0, min(1.18, self._t))
        press = max(0.0, min(1.0, self._press))
        # 浮起 1.6px、按下沉 1.4px(按下时浮起量收掉一半,像真被按下去)
        lift = (-tk(1.6) * min(1.0, t) * (1.0 - press * 0.5) + tk(1.4) * press)
        padx, pady = tk(PADX), tk(PADY)
        r = QtCore.QRectF(padx, pady + lift,
                          self.width() - 2 * padx, self.height() - 2 * pady)
        radius = max(0.0, min(tk(8), tk(self._h) / 3.0))
        enabled = self.isEnabled()
        if enabled:
            c1, c2 = self.colors()
            # 悬停:整面往白里混 30%(Tk 的 _brighten)
            k = 0.30 * min(1.0, t)
            c1, c2 = _mix(c1, "#ffffff", k), _mix(c2, "#ffffff", k)
            fg = "#ffffff"
        else:
            face, fg = disabled_colors(self._theme)
            c1 = c2 = face
        p.setPen(QtCore.Qt.NoPen)
        # 阴影:三层 (dy, alpha),悬停变大、按下收窄
        if enabled:
            k = (0.4 + 0.6 * min(1.0, t)) * (1.0 - 0.55 * press)
            for dy, a in ((3.6, 20), (2.4, 30), (1.2, 42)):
                col = QtGui.QColor(0, 0, 0, int(a * k))
                p.setBrush(col)
                p.drawRoundedRect(r.adjusted(-tk(0.6), tk(dy), tk(0.6), tk(dy)),
                                  radius, radius)
        # 面:严格竖直渐变(Tk 的 band 是同一列同色;AnimButton 当年画成了斜的)
        g = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0.0, QtGui.QColor(c1))
        g.setColorAt(1.0, QtGui.QColor(c2))
        p.setBrush(QtGui.QBrush(g))
        p.drawRoundedRect(r, radius, radius)
        # 扫光:一条竖直亮带从左扫到右(alpha 上限 44、宽 55%)
        if enabled and self._sweep >= 0.0:
            path = QtGui.QPainterPath()
            path.addRoundedRect(r, radius, radius)
            p.setClipPath(path)
            band_w = r.width() * 0.55
            x = r.left() - r.width() * 1.1 + self._sweep * (r.width() * 2.2)
            band = QtGui.QLinearGradient(x, r.top(), x + band_w, r.top())
            band.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
            band.setColorAt(0.5, QtGui.QColor(255, 255, 255, 44))
            band.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            p.setBrush(QtGui.QBrush(band))
            p.drawRect(r)
            p.setClipping(False)
        # 内容(图标 / 文字)
        if self._icon is not None:
            size = tk(22) if not self._icon_only else tk(22)
            icon_rect = QtCore.QRectF(r.center().x() - size / 2, r.center().y() - size / 2,
                                      size, size)
            _IconPainter.paint(self._icon, p, icon_rect, fg)
            p.end()
            return
        self._paint_text(p, r, fg, dpr)
        p.end()

    def _paint_text(self, p, r, fg, dpr):
        text = _clean(self._text)
        lead, rest = split_lead_symbol(text)
        p.setPen(QtGui.QColor(fg))
        p.setFont(self.font())
        fm = QtGui.QFontMetrics(self.font())
        dy = 0.0
        if lead and rest and self._font_pt <= 10:
            # 图标单独画、上提 3px;文字跟在 6px 之后(同 Tk)
            gap = tk(SYM_GAP)
            sym_w = fm.horizontalAdvance(lead)
            rest_w = fm.horizontalAdvance(rest)
            total = sym_w + gap + rest_w
            x0 = r.center().x() - total / 2.0
            up = tk(SYM_DY)
            p.drawText(QtCore.QRectF(x0, r.top() - up, sym_w + 2, r.height()),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, lead)
            p.drawText(QtCore.QRectF(x0 + sym_w + gap, r.top(), rest_w + 2, r.height()),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, rest)
            del dy
            return
        p.drawText(r, QtCore.Qt.AlignCenter, text)

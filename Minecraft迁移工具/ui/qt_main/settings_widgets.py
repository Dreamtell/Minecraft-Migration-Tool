# ui/qt_main/settings_widgets.py
"""设置窗用的自绘控件(对齐 Tk 版 utils/helpers.py 里那一套)。

Tk 设置窗里没有一个是系统原生控件:分段条、开关、滑杆、选择卡、分区描边卡全是自绘的。
Qt 版原来用的是 QComboBox / QCheckBox / QSpinBox,观感和 Tk 差得很远,所以这里按 Tk 的
尺寸和配色重画一套。SwitchRow / 渐变按钮已经存在(ui/qt_main/switch_row.py、look.py),
这里只补缺的四个:SegmentedControl / SliderRow / StepperRow / OptionCards,外加分区卡。

数值来源:ui/mw_settings.py 与 utils/helpers.py(SegmentedControl / SliderRow / OptionCards)
以及 ui/mw_common.py 的 _SECTION_STYLE 配色表。
"""
from PySide6 import QtCore, QtGui, QtWidgets

from ui.qt_frame_anim import FrameAnim

from utils.i18n import tr


def _c(theme, key, fallback):
    return QtGui.QColor(theme.get(key) or fallback)


def _mix(甲, 乙, 比例):
    """甲往乙混（比例 0~1）：轨道描边、悬停底都从「底色 → 主色」推，不猜主题键。"""
    a, b = QtGui.QColor(甲), QtGui.QColor(乙)
    if not a.isValid():
        a = QtGui.QColor("#ffffff")
    if not b.isValid():
        b = QtGui.QColor("#888888")
    k = max(0.0, min(1.0, float(比例)))
    return QtGui.QColor(int(a.red() + (b.red() - a.red()) * k),
                        int(a.green() + (b.green() - a.green()) * k),
                        int(a.blue() + (b.blue() - a.blue()) * k))


def _fg_on(color):
    """亮底用深色字、暗底用白字（同 Tk SegmentedControl._accent_fg）。"""
    c = QtGui.QColor(color)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#202020" if lum > 170 else "#ffffff"


class SegmentedControl(QtWidgets.QWidget):
    """分段选择（对齐 Tk 的 utils/helpers.SegmentedControl）。

    Tk 那套尺寸：**高 28**，轨道与高亮块的圆角都等于高的一半；每段宽 = 最长文字宽
    + 30（带国旗图标再加 20），整条宽度按内容算、**不随父容器拉伸**；切换时高亮块
    130ms OutCubic 滑过去；悬停底只在「悬停的不是当前段」时画。
    """

    valueChanged = QtCore.Signal(object)

    _DURATION = 130          # 滑动时长（毫秒），同 Tk 的 _DURATION = 0.13 秒
    PAD_X = 15               # 每段左右内边距（Tk 同值 => 段宽 = 文字宽 + 30）
    INSET = 2                # 高亮块离轨道边的距离

    def __init__(self, items, value=None, theme=None, parent=None, height=28, min_seg_w=0):
        """items: [(label, value)] 或 [(label, value, icon_kind)]。icon_kind 目前只用于语言行的国旗。"""
        super().__init__(parent)
        self._items = [(it[0], it[1], it[2] if len(it) > 2 else None) for it in items]
        self._theme = dict(theme or {})
        self._value = value if value is not None else (self._items[0][1] if self._items else None)
        self._hover = -1
        self._h = int(height)
        self._min_seg_w = int(min_seg_w)
        self._seg_px = 0
        self._t = float(self._index())        # 高亮块的段位置（滑动时是小数，同 Tk 的 _t）
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._fit_width()
        self._anim = FrameAnim(self)
        self._anim.setDuration(self._DURATION)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    # ---- 数据 ----
    def value(self):
        return self._value

    def setValue(self, value, emit=False):
        if value == self._value:
            return
        self._value = value
        if self._anim is not None and self.isVisible():
            self._slide()            # 只有真的露在屏幕上才动（同 Tk 的 winfo_ismapped）
        else:
            self._anim.stop()
            self._t = float(self._index())
        self.update()
        if emit:
            self.valueChanged.emit(value)

    def items(self):
        return list(self._items)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self._fit_width()            # 文案跟着语言变长变短时宽度要重算（同 Tk 的 refresh_language）
        self.update()

    # ---- 几何 ----
    def _index(self):
        for i, it in enumerate(self._items):
            if it[1] == self._value:
                return i
        return 0

    def _font(self):
        f = QtGui.QFont(self.font())
        f.setPointSizeF(9.0)
        f.setBold(False)
        return f

    def _measure(self):
        """每段宽 = 最长文字宽 + 30，带国旗图标再加 20（同 Tk 的 _seg_w 构造）。"""
        if not self._items:
            return max(self._min_seg_w, self.PAD_X * 2)
        fm = QtGui.QFontMetrics(self._font())
        widest = max(fm.horizontalAdvance(str(it[0])) for it in self._items)
        icon_w = 20 if any(it[2] for it in self._items) else 0
        return max(self._min_seg_w, int(widest + icon_w + self.PAD_X * 2))

    def _fit_width(self):
        """整条宽度按内容钉死：Tk 是 configure(width=...)，**不随父容器拉伸**。"""
        self._seg_px = self._measure()
        self.setFixedSize(self._seg_px * max(1, len(self._items)) + self.INSET * 2, self._h)

    def _seg_w(self):
        """每段的像素宽（旧名字，测试 test_fx_level 还按这个方法点算坐标）。"""
        return self._seg_px

    def _cell(self, i):
        """第 i 段的格子（轨道内等宽的一格，起点 = INSET + i * 段宽）。"""
        return QtCore.QRectF(self.INSET + i * self._seg_px, 0, self._seg_px, self._h)

    def _pill(self, 位置):
        """段位置（可以是小数，滑动中是浮点）的高亮块矩形：格子里四周各让 INSET。"""
        return QtCore.QRectF(self.INSET + 位置 * self._seg_px + self.INSET, self.INSET,
                             max(8, self._seg_px - self.INSET * 2),
                             max(8, self._h - self.INSET * 2))

    def _on_anim(self, v):
        try:
            self._t = float(v)
        except Exception:
            pass
        self.update()

    def _slide(self):
        self._anim.stop()
        self._anim.setStartValue(float(self._t))
        self._anim.setEndValue(float(self._index()))
        self._anim.start()

    def _index_at(self, x):
        i = int((x - self.INSET) // max(1, self._seg_px))
        return i if 0 <= i < len(self._items) else -1

    # ---- 事件 ----
    def mousePressEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton:
            return
        i = self._index_at(int(e.position().x()))
        if i >= 0:
            self.setValue(self._items[i][1], emit=True)

    def mouseMoveEvent(self, e):
        i = self._index_at(int(e.position().x()))
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()
        super().leaveEvent(e)

    # ---- 绘制 ----
    def _accent(self):
        th = self._theme
        return _c(th, "card_sel_bar", th.get("switch_on") or th.get("accent_bg") or "#2f7fd1")

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        th = self._theme
        h = self.height()
        # 轨道：圆角 = 高的一半（Tk 的 _圆角矩形((_total_w, 高), 高/2.0, 边色)）
        p.setPen(QtGui.QPen(_mix(_c(th, "entry_bg", "#ffffff"),
                                 _c(th, "muted_fg", "#9e9e9e"), 0.45), 1))
        p.setBrush(_c(th, "entry_bg", "#ffffff"))
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, self.width() - 1, h - 1), h / 2.0, h / 2.0)

        cur = self._index() if self._items else -1
        radius = max(1.0, (h - self.INSET * 2) / 2.0)
        p.setPen(QtCore.Qt.NoPen)
        # 悬停底：只在「悬停的不是当前段」时露出来 —— 原来写在 elif 里，
        # 只要有一段被选中就永远走不到，鼠标划过毫无反馈（Tk 的 _redraw 是独立的 if）
        if 0 <= self._hover < len(self._items) and self._hover != cur:
            p.setBrush(_mix(_c(th, "entry_bg", "#ffffff"), self._accent(), 0.16))
            p.drawRoundedRect(self._pill(self._hover), radius, radius)
        if cur >= 0:
            p.setBrush(self._accent())
            p.drawRoundedRect(self._pill(self._t), radius, radius)

        p.setFont(self._font())
        on_fg = _fg_on(self._accent())
        for i, (label, _v, icon) in enumerate(self._items):
            cell = self._cell(i)
            on = i == cur
            p.setPen(QtGui.QColor(on_fg if on else _c(th, "fg", "#222222")))
            if icon:
                # 国旗靠左、文字紧跟其后（Tk：图标距高亮块左边 12px，文字从 33px 起）
                pill_x = self.INSET * 2 + i * self._seg_px
                self._paint_flag(p, QtCore.QRectF(pill_x + 10, (h - 13) / 2.0, 20, 13), icon)
                p.drawText(QtCore.QRectF(pill_x + 33, 0, max(8, self._seg_px - 37), h),
                           QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)
            else:
                p.drawText(cell, QtCore.Qt.AlignCenter, label)

    @staticmethod
    def _paint_flag(p, rect, kind):
        """语言行的小国旗(简化画法:两条/三条色带 + 边框即可,和 Tk 的 20px 小旗同观感)。"""
        w, h = 20, 13
        x = rect.x() + 2
        y = rect.center().y() - h / 2
        flag = QtCore.QRectF(x, y, w, h)
        bands = {"cn": [("#de2910", 1.0)], "us": [("#b22234", 1.0)]}.get(kind)
        p.save()
        p.setPen(QtGui.QPen(QtGui.QColor("#9e9e9e"), 1))
        if kind == "cn":
            p.setBrush(QtGui.QColor("#de2910"))
            p.drawRect(flag)
            p.setBrush(QtGui.QColor("#ffde00"))
            p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(QtCore.QRectF(x + 3, y + 3, 4, 4))
        elif kind == "us":
            p.setBrush(QtGui.QColor("#ffffff"))
            p.drawRect(flag)
            p.setPen(QtCore.Qt.NoPen)
            for i in range(7):
                if i % 2 == 0:
                    p.setBrush(QtGui.QColor("#b22234"))
                    p.drawRect(QtCore.QRectF(x, y + i * h / 7.0, w, h / 7.0))
            p.setBrush(QtGui.QColor("#3c3b6e"))
            p.drawRect(QtCore.QRectF(x, y, w * 0.45, h * 0.5))
        else:
            p.setBrush(QtGui.QColor("#ffffff"))
            p.drawRect(flag)
        p.restore()


class SliderRow(QtWidgets.QWidget):
    """整宽滑杆 + 右端大号数值(开始前倒计时、历史保存份数)。

    Tk 版是 ui/slider_row.SliderRow:轨道 6px、圆点 14px、右端数值 11 号粗体,松手才回调。
    """

    valueChanged = QtCore.Signal(int)

    def __init__(self, minimum, maximum, value, theme=None, parent=None, width_hint=320):
        super().__init__(parent)
        self._min, self._max = int(minimum), int(maximum)
        self._value = max(self._min, min(self._max, int(value)))
        self._theme = dict(theme or {})
        self._dragging = False
        self._hover = False
        self.setMinimumWidth(int(width_hint))
        self.setFixedHeight(34)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def value(self):
        return self._value

    def setValue(self, v, emit=False):
        v = max(self._min, min(self._max, int(v)))
        if v == self._value:
            return
        self._value = v
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def _track(self):
        return QtCore.QRectF(8, self.height() / 2 - 3, max(1, self.width() - 8 - 46), 6)

    def _value_at(self, x):
        t = self._track()
        if t.width() <= 0:
            return self._min
        k = (x - t.x()) / t.width()
        k = max(0.0, min(1.0, k))
        return int(round(self._min + k * (self._max - self._min)))

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self.setValue(self._value_at(e.position().x()))
            self.update()

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.setValue(self._value_at(e.position().x()))

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self.valueChanged.emit(self._value)     # 松手才回调,同 Tk

    def wheelEvent(self, e):
        # 滚轮**不改数值**(同 Tk ui/slider_row.py:207-210):设置页是滚动浏览的,
        # 鼠标划过滑条就把参数改了太容易误触(用户反馈过)。ignore 之后事件冒到父级,
        # 正好被 SmoothScrollArea 的缓动接住 —— 滚的是页面。
        e.ignore()

    def keyPressEvent(self, e):
        if e.key() in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Down):
            self.setValue(self._value - 1, emit=True)
        elif e.key() in (QtCore.Qt.Key_Right, QtCore.Qt.Key_Up):
            self.setValue(self._value + 1, emit=True)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        th = self._theme
        t = self._track()
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_c(th, "ttk_trough_bg", "#d0d0d0"))
        p.drawRoundedRect(t, 3, 3)
        k = 0.0 if self._max == self._min else (self._value - self._min) / float(self._max - self._min)
        fill = QtCore.QRectF(t.x(), t.y(), t.width() * k, t.height())
        p.setBrush(_c(th, "switch_on", "#4da3f0"))
        p.drawRoundedRect(fill, 3, 3)
        cx = t.x() + t.width() * k
        # 圆点：实心主色球，悬停/拖动时放大一档（同 Tk 的 14→16px），比白底描边环更像现代滑杆
        radius = 8.0 if (self._hover or self._dragging) else 7.0
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_c(th, "switch_on", "#4da3f0"))
        p.drawEllipse(QtCore.QPointF(cx, t.center().y()), radius, radius)

        f = self.font()
        f.setPointSizeF(11.0)
        f.setBold(True)
        p.setFont(f)
        p.setPen(_c(th, "data_num_fg", "#6a1b9a"))
        p.drawText(QtCore.QRectF(self.width() - 40, 0, 40, self.height()),
                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, str(self._value))


class StepperRow(QtWidgets.QWidget):
    """「— 大号数值 ＋」三件套(平滑滚动速度)。Tk 版是两颗渐变小按钮 + 中间大号数字。"""

    valueChanged = QtCore.Signal(int)

    def __init__(self, minimum, maximum, value, theme=None, parent=None):
        super().__init__(parent)
        self._min, self._max = int(minimum), int(maximum)
        self._value = max(self._min, min(self._max, int(value)))
        self._theme = dict(theme or {})
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.minus_btn = QtWidgets.QPushButton("－")
        self.plus_btn = QtWidgets.QPushButton("＋")
        for b in (self.minus_btn, self.plus_btn):
            b.setFixedSize(28, 26)
            b.setCursor(QtCore.Qt.PointingHandCursor)
        self.value_label = QtWidgets.QLabel(str(self._value))
        self.value_label.setFixedWidth(26)
        self.value_label.setAlignment(QtCore.Qt.AlignCenter)
        f = self.value_label.font()
        f.setPointSizeF(11.0)
        f.setBold(True)
        self.value_label.setFont(f)
        lay.addStretch(1)
        lay.addWidget(self.minus_btn)
        lay.addWidget(self.value_label)
        lay.addWidget(self.plus_btn)
        self.minus_btn.clicked.connect(lambda: self.setValue(self._value - 1, emit=True))
        self.plus_btn.clicked.connect(lambda: self.setValue(self._value + 1, emit=True))
        self._sync()

    def value(self):
        return self._value

    def setValue(self, v, emit=False):
        v = max(self._min, min(self._max, int(v)))
        changed = v != self._value
        self._value = v
        self._sync()
        if emit and changed:
            self.valueChanged.emit(v)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self._sync()

    def _sync(self):
        self.value_label.setText(str(self._value))
        self.value_label.setStyleSheet(
            f"color: {self._theme.get('data_num_fg') or '#6a1b9a'};")
        self.minus_btn.setEnabled(self._value > self._min)
        self.plus_btn.setEnabled(self._value < self._max)


class OptionCards(QtWidgets.QWidget):
    """三张单选卡(关闭方式)。Tk 版是 OptionCards:标题 + 副标题两行,选中描边。"""

    valueChanged = QtCore.Signal(object)

    def __init__(self, cards, value=None, theme=None, parent=None):
        """cards: [(value, title, subtitle)]"""
        super().__init__(parent)
        self._cards = [(str(v), str(t), str(s)) for v, t, s in cards]
        self._value = value if value is not None else (self._cards[0][0] if self._cards else None)
        self._theme = dict(theme or {})
        self._hover = -1
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedHeight(58)

    def value(self):
        return self._value

    def setValue(self, v, emit=False):
        if v == self._value:
            return
        self._value = v
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def _card_w(self):
        n = max(1, len(self._cards))
        return max(120, (self.width() - 8 * (n - 1)) // n)

    def _index_at(self, x):
        w = self._card_w()
        for i in range(len(self._cards)):
            if i * (w + 8) <= x <= i * (w + 8) + w:
                return i
        return -1

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            i = self._index_at(int(e.position().x()))
            if i >= 0:
                self.setValue(self._cards[i][0], emit=True)

    def mouseMoveEvent(self, e):
        i = self._index_at(int(e.position().x()))
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        th = self._theme
        w = self._card_w()
        accent = _c(th, "card_sel_bar", th.get("switch_on") or "#2f7fd1")
        for i, (v, title, sub) in enumerate(self._cards):
            r = QtCore.QRectF(i * (w + 8), 1, w, self.height() - 2)
            on = v == self._value
            base = _c(th, "entry_bg", "#ffffff")
            if on:
                face = _mix(base, accent, 0.14)          # 同 Tk：选中卡混 14% 主色
            elif i == self._hover:
                face = _mix(base, QtGui.QColor("#808080"), 0.05)   # 悬停：往灰混 5%
            else:
                face = base
            edge = accent if on else _mix(base, _c(th, "bg", "#f0f0f0"), 0.6)
            p.setPen(QtGui.QPen(edge, 2 if on else 1))
            p.setBrush(face)
            p.drawRoundedRect(r, 8, 8)
            f = self.font()
            f.setPointSizeF(9.5)
            f.setBold(on)
            p.setFont(f)
            p.setPen(accent if on else _c(th, "fg", "#000000"))
            p.drawText(QtCore.QRectF(r.x() + 12, r.y() + 8, r.width() - 20, 20),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title)
            f2 = self.font()
            f2.setPointSizeF(8.0)
            f.setBold(False)
            p.setFont(f2)
            p.setPen(_c(th, "muted_fg", "#888888"))
            p.drawText(QtCore.QRectF(r.x() + 12, r.y() + 28, r.width() - 20, 20),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, sub)


class SettingsSection(QtWidgets.QWidget):
    """分区描边卡（对齐 Tk 的 ui/mw_settings.section()）。

    Tk 的结构是：标题在描边框**外**的上方，标题左边一块 22px 的主色小色块；
    框体是 1px 主色**直角**边框，里面左右各留 8、上下各留 6。
    """

    TINT_W = 22          # 标题左侧主色小色块的宽度（Tk 那颗「  」约 22px）
    TINT_GAP = 6         # 色块与标题之间（Tk 的 padx=(0, 6)）

    def __init__(self, icon, title, color, theme=None, parent=None):
        super().__init__(parent)
        self._icon, self._title = icon, title
        self._color = QtGui.QColor(color)
        self._theme = dict(theme or {})
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)                   # Tk 的 pady=(4, 0)：标题行与框体之间
        # ---- 标题行：在框体外面，色块 + 主色粗体标题 ----
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(self.TINT_GAP)
        self.tint = QtWidgets.QFrame()
        self.tint.setFixedWidth(self.TINT_W)
        head.addWidget(self.tint)
        self.head = QtWidgets.QLabel("%s %s" % (icon, title) if icon else title)
        f = self.head.font()
        f.setPointSizeF(10.0)
        f.setBold(True)
        self.head.setFont(f)
        head.addWidget(self.head)
        head.addStretch(1)
        outer.addLayout(head)
        self.tint.setFixedHeight(QtGui.QFontMetrics(self.head.font()).height())
        # ---- 框体：1px 主色直角描边，内边距左右 8 / 上下 6 ----
        self.frame = QtWidgets.QFrame()
        self.frame.setObjectName("sectionBody")
        outer.addWidget(self.frame)
        self.body = QtWidgets.QVBoxLayout(self.frame)
        self.body.setContentsMargins(8, 6, 8, 6)
        self.body.setSpacing(6)
        self.set_theme(theme)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        col = self._color.name()
        self.tint.setStyleSheet("background: %s; border: none;" % col)
        self.head.setStyleSheet("color: %s; background: transparent;" % col)
        # 只给这一个 Frame 上边框：裸 QFrame 选择器会把里面所有 QFrame 一起描边
        self.frame.setStyleSheet(
            "QFrame#sectionBody { border: 1px solid %s; border-radius: 0px; }" % col)

    def add(self, widget, spacing=0):
        if spacing:
            self.body.addSpacing(spacing)
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout, spacing=0):
        if spacing:
            self.body.addSpacing(spacing)
        self.body.addLayout(layout)
        return layout


class MutedLabel(QtWidgets.QLabel):
    """说明 / 提示文字(8 号 muted,可折行)。"""

    def __init__(self, text="", theme=None, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        f = self.font()
        f.setPointSizeF(8.0)
        self.setFont(f)
        self.set_theme(theme)

    def set_theme(self, theme):
        th = dict(theme or {})
        self.setStyleSheet("color: %s;" % (th.get("muted_fg") or "#888888"))


def make_row(label_text, widget, theme=None):
    """「左标签 + 紧跟其后的控件」一行（同 Tk 的 _行：控件侧 side="left"）。

    控件**不吃拉伸**：分段条宽度是固定的（Tk 也是按文字算宽度），给了 stretch
    会被挤到行尾，和 Tk 里"标签后面紧跟一条药丸"对不上。
    """
    box = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lab = QtWidgets.QLabel(label_text)
    f = lab.font()
    f.setPointSizeF(9.0)
    lab.setFont(f)
    th = dict(theme or {})
    lab.setStyleSheet("color: %s;" % (th.get("fg") or "#000000"))
    lay.addWidget(lab)
    lay.addWidget(widget)
    lay.addStretch(1)
    return box, lab

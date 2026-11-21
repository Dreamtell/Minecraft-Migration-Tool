# ui/qt_main/switch_row.py
"""Qt 版的 SwitchRow:一行「开关 + 标题 + 说明」,整条都能点。

和 Tk 的 utils/helpers.SwitchRow 同一套数值(逐项对过源码):
  · 卡片形态:高 46,圆角 10 的底,轨道 44×22,圆钮 16,左边距 12,开关到文字 12,标题 10 号粗体,
    说明 9 号;开着时底色往强调色混 18%,说明换成警示语
  · 紧凑形态:高 26,没有卡片底,轨道 36×18,圆钮 14,左边距 0,开关到文字 8,标题 10 号
  · 圆钮左右各留 3,滑动 130ms,OutCubic,按真实时间插值(时长再乘 fx.anim_scale(),「特效强度」
    关掉时直接到位)
  · 禁用:整条灰掉、点不动、不是手型;keep_state_when_disabled 的开关禁用后颜色不变

对外接口模仿 QCheckBox 的子集(toggled / isChecked / setChecked / setEnabled / isEnabled),
这样主窗口里原来按复选框写的逻辑不用改。
"""
import time

from PySide6 import QtCore, QtGui, QtWidgets

DURATION = 0.13


def _mix(c0, c1, t):
    a, b = QtGui.QColor(c0), QtGui.QColor(c1)
    t = max(0.0, min(1.0, t))
    return QtGui.QColor(int(a.red() + (b.red() - a.red()) * t),
                        int(a.green() + (b.green() - a.green()) * t),
                        int(a.blue() + (b.blue() - a.blue()) * t))


def _anim_scale():
    try:
        from utils import fx
        return float(fx.anim_scale())
    except Exception:
        return 1.0


class SwitchRow(QtWidgets.QWidget):
    toggled = QtCore.Signal(bool)

    def __init__(self, theme, title, desc="", compact=False, accent=None, warn_desc=None,
                 keep_state_when_disabled=False, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self._title = str(title or "")
        self._desc = str(desc or "")
        self._warn_desc = warn_desc if warn_desc is not None else "⚠ 已开启"
        self._compact = bool(compact)
        self._keep_state = bool(keep_state_when_disabled)
        self._accent_key = accent or ("switch_on" if self._compact else "edit_bg")
        if self._compact:
            self._sw, self._sh, self._knob = 36, 18, 14
            self._pad_l, self._gap, self._h = 0, 8, 26
        else:
            self._sw, self._sh, self._knob = 44, 22, 16
            self._pad_l, self._gap, self._h = 12, 12, 46
        self._on = False
        self._t = 0.0
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setFixedHeight(self._h)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed if self._compact
                           else QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self._fit()

    # ---- 字体(同 Tk:标题 10 号,卡片形态加粗;说明 9 号) ----
    def _title_font(self):
        f = QtGui.QFont(self.font())
        f.setPointSizeF(10.0)
        f.setBold(not self._compact)
        return f

    def _desc_font(self):
        f = QtGui.QFont(self.font())
        f.setPointSizeF(9.0)
        return f

    def _fit(self):
        if self._compact:
            w = (self._pad_l + self._sw + self._gap
                 + QtGui.QFontMetrics(self._title_font()).horizontalAdvance(self._title) + 4)
            self.setFixedWidth(w)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        w = (self._pad_l + self._sw + self._gap
             + QtGui.QFontMetrics(self._title_font()).horizontalAdvance(self._title) + 4)
        return QtCore.QSize(w, self._h)

    # ---- QCheckBox 风格接口 ----
    def isChecked(self):
        return bool(self._on)

    def setChecked(self, on, animate=False):
        """程序设值,语义同 QCheckBox:值变了就发 toggled(想静默用 blockSignals)。

        Tk 的 set() 不触发命令,是因为命令挂在 toggle() 上;Qt 这边主窗口里很多路径
        (载入配置、锁定/解锁、应用设置)是靠这个信号同步状态的,所以保持 QCheckBox 的行为。
        默认不带动画,直接到位。
        """
        on = bool(on)
        if on == self._on and self._t == (1.0 if on else 0.0):
            return
        self._on = on
        if animate:
            self._animate()
        else:
            self._anim.stop()
            self._t = 1.0 if on else 0.0
            self.update()
        self.toggled.emit(self._on)

    def setEnabled(self, on):
        super().setEnabled(bool(on))
        self.setCursor(QtCore.Qt.PointingHandCursor if on else QtCore.Qt.ArrowCursor)
        self.update()

    def setText(self, title=None, desc=None):
        if title is not None:
            self._title = str(title)
        if desc is not None:
            self._desc = str(desc)
        self._fit()

    def text(self):
        return self._title

    def description(self):
        return self._desc

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    # ---- 交互 ----
    def toggle(self):
        if not self.isEnabled():
            return
        self._on = not self._on
        self._animate()
        self.toggled.emit(self._on)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.toggle()
        super().mouseReleaseEvent(e)

    def _animate(self):
        target = 1.0 if self._on else 0.0
        scale = _anim_scale()
        self._anim.stop()
        if scale <= 0:                                   # 特效强度「关闭」:直接到位
            self._t = target
            self.update()
            return
        self._anim.setStartValue(float(self._t))
        self._anim.setEndValue(target)
        self._anim.setDuration(max(1, int(DURATION * 1000 * scale)))
        self._anim.start()

    def _on_anim(self, v):
        self._t = float(v)
        self.update()

    # ---- 绘制 ----
    def _accent(self):
        return QtGui.QColor(self._theme.get(self._accent_key, "#2f7fd1"))

    def _gray_on_disabled(self):
        return (not self.isEnabled()) and not self._keep_state

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        th = self._theme
        muted = QtGui.QColor(th.get("muted_fg", "#888888"))
        gray = self._gray_on_disabled()
        h, t = self.height(), self._t
        if not self._compact:                            # 卡片底:圆角 10,开着时往强调色混 18%
            base = QtGui.QColor(th.get("entry_bg", "#efefef"))        # Tk _card_imgs 取 entry_bg
            if not self.isEnabled():
                base = _mix(base, QtGui.QColor(128, 128, 128), 0.06)
            on = self._accent() if not gray else base
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(_mix(base, on, 0.18 * t))
            p.drawRoundedRect(QtCore.QRectF(0, 1.5, self.width(), h - 3), 10, 10)
        # 轨道:关 = muted_fg,开 = 强调色
        ox, oy = self._pad_l, (h - self._sh) / 2.0
        on_color = muted if gray else self._accent()
        p.setBrush(_mix(muted, on_color, t))
        p.drawRoundedRect(QtCore.QRectF(ox, oy, self._sw, self._sh), self._sh / 2.0, self._sh / 2.0)
        # 圆钮:左右各留 3
        kx = ox + 3 + (self._sw - 6 - self._knob) * t
        ky = oy + (self._sh - self._knob) / 2.0
        p.setBrush(QtGui.QColor(255, 255, 255))
        p.drawEllipse(QtCore.QRectF(kx, ky, self._knob, self._knob))
        # 文字
        tx = self._pad_l + self._sw + self._gap
        fg = QtGui.QColor(muted if gray else th.get("fg", "#222222"))
        p.setPen(fg)
        tf = self._title_font()
        p.setFont(tf)
        p.drawText(QtCore.QRectF(tx, 0, self.width() - tx, h),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._title)
        if not self._compact:
            tw = QtGui.QFontMetrics(tf).horizontalAdvance(self._title)
            warn = t > 0.5 and (self.isEnabled() or self._keep_state)
            p.setFont(self._desc_font())
            p.setPen(QtGui.QColor(self._warn_color() if warn else th.get("muted_fg", "#888888")))
            p.drawText(QtCore.QRectF(tx + tw + 14, 0, max(0, self.width() - tx - tw - 14), h),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       self._warn_desc if warn else self._desc)
        p.end()

    def _warn_color(self):
        """警示语颜色:浅色主题鲜红,深色主题亮红(同 Tk _WARN_LIGHT / _WARN_DARK)。"""
        bg = QtGui.QColor(self._theme.get("bg", "#f0f0f0"))
        return "#ff6b6b" if bg.lightness() < 128 else "#e53935"

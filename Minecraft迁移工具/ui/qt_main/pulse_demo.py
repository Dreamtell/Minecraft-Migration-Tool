# ui/qt_main/pulse_demo.py
"""逐字「波浪」文字特效（pulse / wave）—— 一行字逐字错开相位，像波浪接力。

只留这一种效果（用户挑的就是它），其余三种写法已删，别再加回来当"备选"。

当控件用：

    from ui.qt_main.pulse_demo import PulseText
    lab = PulseText("开始迁移")
    lab.set_dark(True)      # 跟主题
    lab.set_period(2200)    # 一个完整循环多少毫秒
    lab.set_amplitude(1.0)  # 0=不动，1=最暗到 30% 亮度
    lab.set_stagger(0.13)   # 每个字错开多少个周期：越小越像整体呼吸，越大越像波浪

单独跑来看 / 调参：

    python -m ui.qt_main.pulse_demo
    python -m ui.qt_main.pulse_demo --light
    python -m ui.qt_main.pulse_demo --shot 截图.png
"""
import math
import sys

from PySide6 import QtCore, QtGui, QtWidgets

_UI = 1.30                  # 设计稿 → 实际像素（和 star_slider.py / look.py 同一个系数）
_TICK_MS = 16               # 16ms ≈ 60fps
_PERIOD_MS = 1400.0         # 一个完整循环（和「开始迁移」按钮同一套默认）
_AMP = 1.0                 # 幅度：最低亮度 = 1 - 0.70 * AMP（1.0 → 最暗 30%）
_STAGGER = 0.14             # 逐字错开的相位

DARK = {"bg": "#1e1e1e", "fg": "#e8e8e8", "card": "#262626",
        "muted": "#8e8e93", "line": "#3a3a3a"}
LIGHT = {"bg": "#f0f0f0", "fg": "#1b1b1b", "card": "#ffffff",
         "muted": "#6b6b70", "line": "#d8d8d8"}


def _u(v):
    return int(round(v * _UI))


def _with_alpha(color, alpha):
    c = QtGui.QColor(color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


class PulseText(QtWidgets.QWidget):
    """逐字波浪：每个字的亮度按 sin 曲线走，字与字之间错开固定相位。

    相位是**累加**的（不是拿时间取模），所以暂停再继续不会跳一下；
    不可见时定时器自动停，不烧 CPU。
    """

    def __init__(self, text="开始迁移", parent=None):
        super().__init__(parent)
        self._text = text
        self._dark = True
        self._period = _PERIOD_MS
        self._amp = _AMP
        self._stagger = _STAGGER
        self._phase = 0.0
        self._font_px = 21
        self._clock = QtCore.QElapsedTimer()
        self._clock.start()
        self._last_ms = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.setMinimumHeight(_u(38))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    # ---- 对外接口 ----
    def text(self):
        return self._text

    def set_text(self, text):
        if text != self._text:
            self._text = text
            self.update()

    def set_dark(self, dark):
        self._dark = bool(dark)
        self.update()

    def set_font_px(self, px):
        """字号（设计稿像素）。"""
        self._font_px = max(8, int(px))
        self.setMinimumHeight(_u(self._font_px * 1.8))
        self.updateGeometry()
        self.update()

    def period(self):
        return int(self._period)

    def set_period(self, ms):
        # 上限必须 ≥ demo 滑块的 20 秒，否则「拖大没反应」（这里踩过：滑块到 20s、
        # 控件里还卡在 8s，看着完全不动）
        self._period = float(max(300, min(30000, ms)))

    def amplitude(self):
        return self._amp

    def set_amplitude(self, v):
        self._amp = max(0.0, min(1.0, float(v)))
        self.update()

    def stagger(self):
        return self._stagger

    def set_stagger(self, v):
        self._stagger = max(0.0, min(0.5, float(v)))
        self.update()

    def start(self):
        if not self._timer.isActive():
            self._last_ms = self._clock.elapsed()
            self._timer.start(_TICK_MS)

    def stop(self):
        self._timer.stop()

    # ---- 内部 ----
    def showEvent(self, ev):
        self._last_ms = self._clock.elapsed()
        self.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        self.stop()
        super().hideEvent(ev)

    def _tick(self):
        now = self._clock.elapsed()
        dt = (now - self._last_ms) / 1000.0
        self._last_ms = now
        self._phase = (self._phase + dt * 1000.0 / self._period) % 1.0
        if self.isVisible():
            self.update()

    def _color(self):
        return QtGui.QColor("#f2f2f2" if self._dark else "#101010")

    def _font(self):
        f = QtGui.QFont(self.font())
        f.setPixelSize(_u(self._font_px))
        f.setBold(True)
        return f

    @staticmethod
    def _ease(k):
        """0~1 的 sin 曲线：两头慢、中间快，比线性像"呼吸"。"""
        return 0.5 - 0.5 * math.cos(2.0 * math.pi * k)

    def _text_x(self):
        """只有这一种效果了，水平居中——留在左边看着像"还没排版"。"""
        fm = QtGui.QFontMetricsF(self._font())
        return max(float(_u(6)), (self.width() - fm.horizontalAdvance(self._text)) / 2.0)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        f = self._font()
        fm = QtGui.QFontMetricsF(f)
        p.setFont(f)

        lo = 1.0 - 0.70 * self._amp           # 幅度 1 → 最低 30% 亮度
        base = self._color()
        x = self._text_x()
        y = (self.height() + fm.ascent() - fm.descent()) / 2.0
        for i, ch in enumerate(self._text):
            k = self._ease((self._phase + i * self._stagger) % 1.0)
            p.setPen(QtGui.QPen(_with_alpha(base, 255 * (lo + (1.0 - lo) * k))))
            p.drawText(QtCore.QPointF(x, y), ch)
            x += fm.horizontalAdvance(ch)
        p.end()

    def sizeHint(self):
        fm = QtGui.QFontMetricsF(self._font())
        return QtCore.QSize(int(fm.horizontalAdvance(self._text)) + _u(40),
                            int(fm.height() + _u(12)))


class WaveDemo(QtWidgets.QWidget):
    """一行大字 + 三个滑块，用来把这个效果调到手感合适。"""

    def __init__(self, theme=DARK):
        super().__init__()
        self._theme = theme
        self.setWindowTitle("逐字波浪文字特效 · wave")
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(_u(20), _u(18), _u(20), _u(16))
        root.setSpacing(_u(8))

        title = QtWidgets.QLabel("逐字波浪（wave）")
        title.setObjectName("title")
        sub = QtWidgets.QLabel("每个字的亮度错开相位；拖三个滑块调到你要的手感")
        sub.setObjectName("sub")
        root.addWidget(title)
        root.addWidget(sub)
        root.addStretch(1)

        self.lab = PulseText("开始迁移")
        self.lab.set_font_px(30)
        root.addWidget(self.lab)
        root.addStretch(1)

        self.edit = QtWidgets.QLineEdit("开始迁移")
        self.edit.setPlaceholderText("换文案")
        self.edit.textChanged.connect(lambda t: self.lab.set_text(t or " "))
        root.addWidget(self.edit)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(_u(8))

        # 滑块是「速度」：往右 = 更快（以前直接把周期做成滑块，往右反而更慢，
        # 用户第一反应就是"拉大怎么更慢了"，所以这里映射反过来了）
        self.speed = self._slider(row, "速度", 0, 100, self._period_to_speed(_PERIOD_MS), _u(170))
        # 滑块只够拖个大概，想要精确到 0.1 秒就直接在这儿敲
        self.speed_spin = QtWidgets.QDoubleSpinBox()
        self.speed_spin.setRange(0.6, 20.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setSuffix(" 秒/次")
        self.speed_spin.setValue(_PERIOD_MS / 1000.0)
        self.speed_spin.setFixedWidth(_u(96))
        self.speed_spin.valueChanged.connect(self._on_speed_spin)
        row.addWidget(self.speed_spin)

        row.addSpacing(_u(14))
        self.amp = self._slider(row, "幅度", 0, 100, int(_AMP * 100), _u(100))
        row.addSpacing(_u(14))
        self.stg = self._slider(row, "错开", 0, 40, int(_STAGGER * 100), _u(90))

        row.addStretch(1)
        self.theme_btn = QtWidgets.QPushButton("切浅色")
        self.theme_btn.clicked.connect(self._toggle_theme)
        self.pause_btn = QtWidgets.QPushButton("暂停")
        self.pause_btn.clicked.connect(self._toggle_pause)
        row.addWidget(self.theme_btn)
        row.addWidget(self.pause_btn)
        root.addLayout(row)

        self.param_lab = QtWidgets.QLabel()
        self.param_lab.setObjectName("params")
        root.addWidget(self.param_lab)

        self._paused = False
        self.speed.valueChanged.connect(self._on_speed)
        self.amp.valueChanged.connect(self._on_amp)
        self.stg.valueChanged.connect(self._on_stg)
        self.apply_theme(theme)
        self._on_speed(self.speed.value())
        self._on_amp(self.amp.value())
        self._on_stg(self.stg.value())
        self.resize(_u(760), _u(300))

    def _slider(self, row, label, lo, hi, val, width):
        row.addWidget(QtWidgets.QLabel(label))
        s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.setFixedWidth(width)
        row.addWidget(s)
        return s

    # ---- 交互 ----
    _PERIOD_FAST = 600.0
    _PERIOD_SLOW = 20000.0

    @classmethod
    def _speed_to_period(cls, v):
        """0..100 → 20000..600 毫秒（右快左慢）。"""
        k = max(0, min(100, int(v))) / 100.0
        return cls._PERIOD_SLOW + (cls._PERIOD_FAST - cls._PERIOD_SLOW) * k

    @classmethod
    def _period_to_speed(cls, ms):
        span = cls._PERIOD_SLOW - cls._PERIOD_FAST
        k = (cls._PERIOD_SLOW - float(ms)) / span
        return int(round(max(0.0, min(1.0, k)) * 100))

    def _on_speed(self, v):
        ms = self._speed_to_period(v)
        self.lab.set_period(ms)
        if abs(self.speed_spin.value() - ms / 1000.0) > 0.05:
            self.speed_spin.blockSignals(True)
            self.speed_spin.setValue(ms / 1000.0)
            self.speed_spin.blockSignals(False)
        self._refresh_params()

    def _on_speed_spin(self, sec):
        ms = sec * 1000.0
        self.speed.blockSignals(True)
        self.speed.setValue(self._period_to_speed(ms))
        self.speed.blockSignals(False)
        self.lab.set_period(ms)
        self._refresh_params()

    def _on_amp(self, v):
        self.lab.set_amplitude(v / 100.0)
        self._refresh_params()

    def _on_stg(self, v):
        self.lab.set_stagger(v / 100.0)
        self._refresh_params()

    def _refresh_params(self):
        self.param_lab.setText(
            "幅度 %.2f · 错开 %.2f · 周期 %.1fs"
            % (self.amp.value() / 100.0, self.stg.value() / 100.0,
               self.lab.period() / 1000.0))

    def _toggle_pause(self):
        self._paused = not self._paused
        self.lab.stop() if self._paused else self.lab.start()
        self.pause_btn.setText("继续" if self._paused else "暂停")

    def _toggle_theme(self):
        self.apply_theme(LIGHT if self._theme is DARK else DARK)

    # ---- 主题 ----
    def apply_theme(self, theme):
        self._theme = theme
        dark = theme is DARK
        self.lab.set_dark(dark)
        self.setStyleSheet(
            "QWidget{background:%s;color:%s;}"
            "#title{font-size:%dpx;font-weight:600;}"
            "#sub{color:%s;font-size:%dpx;}"
            "#params{color:%s;font-size:%dpx;}"
            "QLineEdit{background:%s;border:1px solid %s;border-radius:%dpx;"
            "padding:%dpx %dpx;font-size:%dpx;color:%s;}"
            "QPushButton{background:%s;border:1px solid %s;border-radius:%dpx;"
            "padding:%dpx %dpx;font-size:%dpx;color:%s;}"
            "QPushButton:hover{border-color:#4a90d9;}"
            "QSlider::groove:horizontal{height:4px;background:%s;border-radius:2px;}"
            "QSlider::handle:horizontal{width:12px;margin:-5px 0;border-radius:6px;"
            "background:#4a90d9;}"
            % (theme["bg"], theme["fg"], _u(15), theme["muted"], _u(11),
               theme["muted"], _u(11),
               theme["card"], theme["line"], _u(8), _u(5), _u(8), _u(12), theme["fg"],
               theme["card"], theme["line"], _u(7), _u(5), _u(12), _u(12), theme["fg"],
               theme["line"])
        )
        self.theme_btn.setText("切深色" if not dark else "切浅色")
        self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(self._theme["bg"]))
        p.end()
        super().paintEvent(ev)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv)
    win = WaveDemo(LIGHT if "--light" in argv else DARK)
    win.show()

    if "--shot" in argv:
        shot = argv[argv.index("--shot") + 1]

        def _grab():
            for _ in range(20):
                app.processEvents(QtCore.QEventLoop.AllEvents, 10)
            win.grab().save(shot)
            print("saved", shot)
            app.quit()

        QtCore.QTimer.singleShot(900, _grab)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

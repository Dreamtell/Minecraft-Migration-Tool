# ui/qt_main/toast.py
"""Qt 主界面的吐司：浮在主窗口右下角的圆角卡片（对齐 Tk 版 ui/toast.py 的外观与规则）。

Tk 版为了真透明要 PIL 画图 + UpdateLayeredWindow；Qt 控件本身就有逐像素 alpha 与抗锯齿，
所以这里直接用 QPainter 画在主窗口的一个透明子控件上，没有那些坑。

规则照抄 Tk 版：
  - 圆角卡 + 贴左边的等级色条 + 矢量等级图标 + 一行文案（超宽省略）+ 右侧 × + 底部进度条
  - 右下角向上堆叠，最多 4 条，超出挤掉最旧的
  - 停留时长按字数算（3 秒起，超过 14 字每字 +70ms，最长 8 秒）；error 默认常驻
  - 同 key 的消息原地换文案、重置倒计时，不新增卡片
  - 文案开头和等级图标重复的符号（⚠️ ✅ ❌ …）剥掉
  - 鼠标停在卡片上时暂停倒计时

⚠ 本模块只 import PySide6 与 utils（无 tkinter），可以放心在 Qt 主进程里用。
"""
import time

from PySide6 import QtCore, QtGui, QtWidgets

LEVELS = {
    "info": "#4c9aff",
    "ok": "#4caf50",
    "warn": "#ffa000",
    "error": "#e53935",
}

MAX_ITEMS = 4
_UI = 1.30
_CARD_H = 34
_GAP = 10
_MARGIN = 16
_MIN_W, _MAX_W = 180, 340
_RADIUS = 10
_BAR_H = 4
_FADE_MS = 160
_SLIDE_PX = 18
_TICK_MS = 16

_阅读基准ms = 3000
_阅读起算字 = 14
_阅读每字ms = 70
_阅读上限ms = 8000

_级别符号 = ("⚠️", "⚠", "❗", "❕", "✅", "✔️", "✔", "☑️", "✓",
             "❌", "✖️", "✖", "✗", "🚫", "⛔", "ℹ️", "ℹ")


def _u(v):
    return int(round(float(v) * _UI))


def 停留时长(文本, 基准=None):
    """一条吐司该停留多久（毫秒）；调用方显式给了 ms 就用它的。"""
    if 基准 is not None:
        try:
            return max(1, int(基准))
        except Exception:
            pass
    字 = len(str(文本 or ""))
    算 = _阅读基准ms + max(0, 字 - _阅读起算字) * _阅读每字ms
    return int(min(_阅读上限ms, max(_阅读基准ms, 算)))


def 去级别符号(文本):
    """剥掉文案开头与等级图标重复的符号（整条只有符号时保留）。"""
    变 = True
    while 变 and 文本:
        变 = False
        for 符 in _级别符号:
            if 文本.startswith(符):
                剩 = 文本[len(符):].lstrip("　\t ")
                if 剩:
                    文本 = 剩
                    变 = True
                break
    return 文本


def palette(theme):
    """(卡片底, 描边, 文字色)。深色下必须和窗口底拉开，否则圆角读不出来。"""
    th = theme or {}
    dark = str(th.get("bg", "#ffffff")).lower() in (
        "#1e1e1e", "#202020", "#252525", "#2e2e2e", "#1b1b1b")
    if dark:
        return ("#2e2e2e", "#4d4d4d", th.get("fg", "#e8e8e8"))
    return ("#ffffff", "#d8d8d8", th.get("fg", "#222222"))


def _ease(a, dt_ms, base_ms=16.0, k=0.28):
    """按真实 dt 的指数趋近系数（HANDOFF 第五节铁律：帧率只改排帧，速度按 dt 算）。"""
    return 1.0 - (1.0 - k) ** (max(0.0, dt_ms) / base_ms)


class _Card(QtWidgets.QWidget):
    """一张吐司卡片。位置/透明度由 ToastHost 每帧推进，这里只负责画和接鼠标。"""

    def __init__(self, host, parent, text, level, ms, sticky, key):
        super().__init__(parent)
        self.host = host
        self.key = key
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.ArrowCursor)
        self.opacity = 0.0
        self.target_opacity = 1.0
        self.y_f = None              # 当前浮点 y（动画用）
        self.x_f = None
        self.leaving = False
        self.hover = False
        self.hover_x = False
        self.set_content(text, level, ms, sticky)

    # ---- 内容 ----
    def set_content(self, text, level, ms=None, sticky=None):
        self.level = level if level in LEVELS else "info"
        self.text = 去级别符号(str(text or "").strip())
        if sticky is not None:
            self.sticky = bool(sticky)
        self.ms = 停留时长(self.text, ms)
        self.elapsed = 0.0           # 已走过的毫秒（悬停期间不累加）
        f = self.font()
        f.setPixelSize(max(12, _u(9) + 2))
        self.setFont(f)
        fm = QtGui.QFontMetrics(f)
        w = min(_u(_MAX_W), max(_u(_MIN_W), fm.horizontalAdvance(self.text) + _u(78)))
        self.resize(int(w), _u(_CARD_H))
        self.update()

    def progress(self):
        if self.sticky:
            return 1.0
        return max(0.0, 1.0 - self.elapsed / float(max(1, self.ms)))

    def expired(self):
        return (not self.sticky) and self.elapsed >= self.ms

    # ---- 鼠标 ----
    def _close_rect(self):
        s = _u(14)
        return QtCore.QRectF(self.width() - _u(15) - s / 2.0, (self.height() - s) / 2.0, s, s)

    def enterEvent(self, e):
        self.hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover = False
        self.hover_x = False
        self.update()
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        hx = self._close_rect().adjusted(-4, -4, 4, 4).contains(e.position())
        if hx != self.hover_x:
            self.hover_x = hx
            self.setCursor(QtCore.Qt.PointingHandCursor if hx else QtCore.Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and \
                self._close_rect().adjusted(-4, -4, 4, 4).contains(e.position()):
            self.host.dismiss(self)
            e.accept()
            return
        e.accept()                   # 点卡片本身不穿透到下面的控件

    # ---- 画 ----
    def paintEvent(self, _e):
        bg, edge, fg = palette(self.host.theme)
        bar = QtGui.QColor(LEVELS[self.level])
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        p.setOpacity(max(0.0, min(1.0, self.opacity)))
        w, h = float(self.width()), float(self.height())
        r = float(_u(_RADIUS))
        body = QtGui.QPainterPath()
        body.addRoundedRect(QtCore.QRectF(0.5, 0.5, w - 1.0, h - 1.0), r, r)
        p.fillPath(body, QtGui.QColor(bg))

        # 左侧等级色条：裁在卡片轮廓里，左侧两个圆角和卡片重合、右边直切
        p.save()
        p.setClipPath(body)
        p.fillRect(QtCore.QRectF(0, 0, max(2, _u(6)), h), bar)
        # 底部进度条：同样裁在轮廓内，跟着卡片圆角走
        frac = self.progress()
        if frac > 0 and not self.sticky:
            p.fillRect(QtCore.QRectF(0, h - _u(_BAR_H) / 1.6, w * frac, h), bar)
        p.restore()

        pen = QtGui.QPen(QtGui.QColor(edge))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.drawPath(body)

        self._draw_icon(p, bar, h)

        # 文案（超宽省略）
        p.setPen(QtGui.QColor(fg))
        fm = QtGui.QFontMetrics(self.font())
        x0 = _u(42)
        avail = max(_u(16), int(w) - x0 - _u(26))
        p.drawText(QtCore.QRectF(x0, 0, avail, h), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                   fm.elidedText(self.text, QtCore.Qt.ElideRight, avail))

        # 右侧 ×（悬停时加深）
        c = QtGui.QColor(fg)
        c.setAlphaF(0.95 if self.hover_x else (0.65 if self.hover else 0.45))
        xp = QtGui.QPen(c)
        xp.setWidthF(max(1.2, _u(1.7) * 0.8))
        xp.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(xp)
        cr = self._close_rect()
        q = _u(3.4)
        cx, cy = cr.center().x(), cr.center().y()
        p.drawLine(QtCore.QPointF(cx - q, cy - q), QtCore.QPointF(cx + q, cy + q))
        p.drawLine(QtCore.QPointF(cx - q, cy + q), QtCore.QPointF(cx + q, cy - q))
        p.end()

    def _draw_icon(self, p, color, h):
        """矢量等级图标（不依赖字体里有没有 ✓）。"""
        cx, cy = float(_u(23)), h / 2.0
        th = max(2.0, _u(2.4))
        r = _u(6.2)
        pen = QtGui.QPen(color)
        pen.setWidthF(th)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        P = QtCore.QPointF
        if self.level == "ok":
            path = QtGui.QPainterPath(P(cx - r, cy + th * 0.10))
            path.lineTo(P(cx - r * 0.22, cy + r * 0.74))
            path.lineTo(P(cx + r, cy - r * 0.80))
            p.drawPath(path)
        elif self.level == "error":
            p.drawLine(P(cx - r, cy - r), P(cx + r, cy + r))
            p.drawLine(P(cx - r, cy + r), P(cx + r, cy - r))
        elif self.level == "warn":
            p.drawLine(P(cx, cy - r), P(cx, cy + r * 0.18))
            p.drawPoint(P(cx, cy + r * 0.70))
        else:
            p.drawPoint(P(cx, cy - r + th / 2.0))
            p.drawLine(P(cx, cy - r * 0.25), P(cx, cy + r))


class ToastHost(QtCore.QObject):
    """管一叠吐司：一个共享定时器推进所有卡片（倒计时、淡入淡出、让位滑动）。

    卡片是 parent（主窗口）的直接子控件，叠在内容上方；只有卡片本身接鼠标，
    卡片之间和外面的区域照常点得到下面的控件。
    """

    def __init__(self, parent, theme=None, max_items=MAX_ITEMS):
        super().__init__(parent)
        self._parent = parent
        self.theme = dict(theme or {})
        self.max_items = int(max_items)
        self._items = []             # 从下往上：_items[-1] 是最新、在最底下
        self._t_last = None
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        parent.installEventFilter(self)

    # ---- 对外 ----
    def show(self, text, level="info", ms=None, sticky=None, key=None):
        text = str(text or "").strip()
        if not text:
            return None
        if level not in LEVELS:
            level = "info"
        if sticky is None:
            sticky = (level == "error")
        # 同一 key = 同一条消息的状态变化：原地换文案、重置倒计时，不新增卡片
        if key is not None:
            for it in self._items:
                if not it.leaving and it.key == key:
                    it.set_content(text, level, ms, sticky)
                    it.target_opacity = 1.0
                    self._layout(snap=False)
                    self._ensure_tick()
                    return it
        card = _Card(self, self._parent, text, level, ms, sticky, key)
        self._items.append(card)
        live = [c for c in self._items if not c.leaving]
        while len(live) > self.max_items:
            self.dismiss(live.pop(0))
        self._layout(snap=False, new=card)
        card.show()
        card.raise_()
        self._ensure_tick()
        return card

    def dismiss(self, card):
        if card in self._items and not card.leaving:
            card.leaving = True
            card.target_opacity = 0.0
            self._layout(snap=False)
            self._ensure_tick()

    def clear(self):
        for c in list(self._items):
            self.dismiss(c)

    def set_theme(self, theme):
        self.theme = dict(theme or {})
        for c in self._items:
            c.update()

    def count(self):
        return sum(1 for c in self._items if not c.leaving)
    # ---- 布局 ----
    def _anchor(self):
        """卡片栈的右下角（主窗口坐标，避开状态栏）。"""
        par = self._parent
        bottom = par.height()
        sb = getattr(par, "statusBar", None)
        try:
            bar = sb() if callable(sb) else None
            if bar is not None and bar.isVisible():
                bottom -= bar.height()
        except Exception:
            pass
        return par.width() - _MARGIN, bottom - _MARGIN

    def _layout(self, snap=False, new=None):
        """算每张卡的目标位置；snap=True 直接落位（窗口缩放时）。"""
        right, y = self._anchor()
        for c in reversed(self._items):
            c.target_x = right - c.width()
            if c.leaving:
                # 要走的卡原地淡出，不占位置：上面的卡会滑下来补位
                c.target_y = c.y_f if c.y_f is not None else y - c.height()
                continue
            c.target_y = y - c.height()
            if c is new or c.y_f is None:
                # 新卡：从目标下方一点淡入滑上来
                c.y_f = c.target_y + (0 if snap else _SLIDE_PX)
                c.x_f = c.target_x
            if snap:
                c.x_f, c.y_f = c.target_x, c.target_y
            c.move(int(round(c.x_f)), int(round(c.y_f)))
            y = c.target_y - _GAP

    def eventFilter(self, obj, ev):
        if obj is self._parent and ev.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
            self._layout(snap=True)
        return False

    # ---- 动画 ----
    def _ensure_tick(self):
        if not self._timer.isActive():
            self._t_last = time.perf_counter()
            self._timer.start(_TICK_MS)

    def _tick(self):
        now = time.perf_counter()
        dt = (now - (self._t_last or now)) * 1000.0
        self._t_last = now
        k = _ease(0, dt)
        fade_step = dt / float(_FADE_MS)
        busy = False
        relayout = False
        for c in list(self._items):
            if not c.leaving and not c.hover:
                c.elapsed += dt
                if c.expired():
                    self.dismiss(c)
            # 透明度线性走，位置按 dt 指数趋近
            if c.opacity < c.target_opacity:
                c.opacity = min(c.target_opacity, c.opacity + fade_step)
            elif c.opacity > c.target_opacity:
                c.opacity = max(c.target_opacity, c.opacity - fade_step)
            tx, ty = c.target_x, c.target_y
            c.y_f += (ty - c.y_f) * k
            c.x_f += (tx - c.x_f) * k
            if abs(ty - c.y_f) < 0.5:
                c.y_f = ty
            if abs(tx - c.x_f) < 0.5:
                c.x_f = tx
            c.move(int(round(c.x_f)), int(round(c.y_f)))
            if c.leaving and c.opacity <= 0.0:
                self._items.remove(c)
                c.hide()
                c.deleteLater()
                relayout = True
                continue
            c.update()
            if (not c.sticky) or c.opacity != c.target_opacity or c.y_f != ty or c.x_f != tx:
                busy = True
        if relayout:
            self._layout(snap=False)
            busy = busy or bool(self._items)
        if not busy:
            self._timer.stop()

# ui/qt_main/net_float.py
"""Qt 版网络浮标 + 延迟曲线窗口。

Tk 版(ui/net_float.py,1900 行)大半篇幅在对付 Tk 没有真透明:手写 UpdateLayeredWindow、
自己算拖动、自己判断前台、贴边收纳全靠轮询。Qt 有 WA_TranslucentBackground 和
Qt.Tool(从属窗口),这些都不用再手写:
  · 圆形 + 抗锯齿边 + 阴影:QPainter 直接画,背景真透明
  · 从属于主窗口(Qt.Tool + parent):主窗口最小化时系统自动带着隐藏,
    不会压住别的程序(不需要 -topmost)
  · 位置存「相对主窗口客户区左上角的偏移」,主窗口移动/缩放时跟着走、夹在窗口范围内
    (配置键 net_float_x / net_float_y,与 Tk 版同键;-1 = 用默认右下角)

数据来源:ui/mw_net.record() 记下的探测历史(主窗口的网络探测线程每 5 秒记一次),
本模块只读,不自己发起网络请求。
"""
import sys
import time
from pathlib import Path

_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtWidgets, QtCore, QtGui  # noqa: E402

from utils.i18n import tr, trp  # noqa: E402

# 状态 → (底色, 环色, 悬停环色);取自 Tk 版 ui/net_float.py 的 _GOOD_* / _BAD_*
_COLORS = {
    "ok":      ((24, 55, 32), (67, 160, 71), (102, 187, 106)),
    "warn":    ((60, 46, 16), (255, 160, 0), (255, 183, 77)),
    "fail":    ((58, 24, 24), (229, 57, 53), (239, 83, 80)),
    "unknown": ((38, 50, 56), (120, 144, 156), (144, 164, 174)),
}
_SERIES_COLORS = {"Modrinth": "#1bd96a", "CurseForge": "#f16436"}
_MARGIN = 6          # 圆外留给阴影的边
_DRAG_START = 4      # 按下后移动超过这么多像素才算拖动(否则算点击)


class NetFloat(QtWidgets.QWidget):
    """主窗口上的圆形网络浮标。clicked:单击;moved(dx, dy):拖动结束,新的相对偏移。"""

    clicked = QtCore.Signal()
    moved = QtCore.Signal(int, int)

    def __init__(self, host, size=56, rel=None):
        super().__init__(host, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.NoDropShadowWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._host = host
        self._d = max(40, min(96, int(size or 56)))
        self.setFixedSize(self._d + 2 * _MARGIN, self._d + 2 * _MARGIN)
        self._state = "unknown"
        self._ms = None
        self._hover = False
        self._press = None       # (按下时的全局坐标, 指针在浮标内的偏移)
        self._dragging = False
        self._rel = None
        if rel and all(isinstance(v, int) and v >= 0 for v in rel):
            self._rel = (int(rel[0]), int(rel[1]))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(tr("网络延迟浮标:单击看曲线,按住拖动换位置"))
        host.installEventFilter(self)
        if host.isVisible():
            self._place()
            self.show()

    # ---------- 位置 ----------
    def _default_rel(self):
        g = self._host.geometry()
        return (max(0, g.width() - self.width() - 16), max(0, g.height() - self.height() - 56))

    def _clamp(self, dx, dy):
        g = self._host.geometry()
        return (max(0, min(int(dx), g.width() - self.width())),
                max(0, min(int(dy), g.height() - self.height())))

    def _place(self):
        dx, dy = self._clamp(*(self._rel or self._default_rel()))
        top_left = self._host.geometry().topLeft()   # 顶层窗口:客户区的屏幕坐标
        self.move(top_left.x() + dx, top_left.y() + dy)

    def eventFilter(self, obj, ev):
        if obj is self._host:
            t = ev.type()
            if t in (QtCore.QEvent.Move, QtCore.QEvent.Resize):
                self._place()
            elif t == QtCore.QEvent.Show:
                self._place()
                self.show()
            elif t == QtCore.QEvent.Hide:
                # 主窗口收进托盘(hide)时浮标一起藏;最小化由系统带着从属窗口隐藏
                self.hide()
        return False

    # ---------- 数据 ----------
    def update_state(self, result):
        """result:{平台: 耗时ms 或 None}(mw_net.probe 的返回)。"""
        result = dict(result or {})
        ok = [v for v in result.values() if v]
        if not result:
            self._state, self._ms = "unknown", None
        elif len(ok) == len(result):
            self._state, self._ms = "ok", max(ok)
        elif ok:
            self._state, self._ms = "warn", max(ok)
        else:
            self._state, self._ms = "fail", None
        self.setToolTip("\n".join(
            ["%s:%s" % (k, ("%d ms" % v) if v else tr("不通")) for k, v in result.items()]
            + [tr("单击看延迟曲线,按住拖动换位置")]))
        self.update()

    # ---------- 绘制 ----------
    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        tile, ring, ring_hover = _COLORS.get(self._state, _COLORS["unknown"])
        r = QtCore.QRectF(_MARGIN, _MARGIN, self._d, self._d)
        # 阴影:往下偏 2px 的半透明黑圆
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(0, 0, 0, 70))
        p.drawEllipse(r.translated(0, 2))
        # 底 + 环
        p.setBrush(QtGui.QColor(*tile))
        pen = QtGui.QPen(QtGui.QColor(*(ring_hover if self._hover else ring)))
        pen.setWidthF(max(2.0, self._d / 18.0))
        p.setPen(pen)
        inset = pen.widthF() / 2
        p.drawEllipse(r.adjusted(inset, inset, -inset, -inset))
        # 文字:数字 + 单位
        p.setPen(QtGui.QColor("#ffffff"))
        f = p.font()
        if self._ms is not None:
            num = str(self._ms) if self._ms < 1000 else "%.1fs" % (self._ms / 1000.0)
            f.setPixelSize(int(self._d * (0.30 if len(num) <= 3 else 0.24)))
            f.setBold(True)
            p.setFont(f)
            p.drawText(r.adjusted(0, -self._d * 0.10, 0, 0), QtCore.Qt.AlignCenter, num)
            f.setPixelSize(max(8, int(self._d * 0.16)))
            f.setBold(False)
            p.setFont(f)
            p.setPen(QtGui.QColor(255, 255, 255, 180))
            p.drawText(r.adjusted(0, self._d * 0.34, 0, 0), QtCore.Qt.AlignHCenter
                       | QtCore.Qt.AlignTop, "ms")
        else:
            f.setPixelSize(int(self._d * 0.34))
            f.setBold(True)
            p.setFont(f)
            p.drawText(r, QtCore.Qt.AlignCenter, "×" if self._state == "fail" else "…")
        p.end()

    # ---------- 鼠标 ----------
    def enterEvent(self, _ev):
        self._hover = True
        self.update()

    def leaveEvent(self, _ev):
        self._hover = False
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            g = ev.globalPosition().toPoint()
            self._press = (g, g - self.frameGeometry().topLeft())
            self._dragging = False

    def mouseMoveEvent(self, ev):
        if self._press is None:
            return
        g = ev.globalPosition().toPoint()
        if not self._dragging and (g - self._press[0]).manhattanLength() < _DRAG_START:
            return
        self._dragging = True
        # 拖动公式(Tk 版第 4 条坑):指针屏幕坐标 - 主窗口客户区屏幕坐标 - 指针在浮标内的偏移
        origin = self._host.geometry().topLeft()
        tl = g - origin - self._press[1]
        self._rel = self._clamp(tl.x(), tl.y())
        self._place()

    def mouseReleaseEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            self.moved.emit(*self._rel)
        else:
            self.clicked.emit()


class _Chart(QtWidgets.QWidget):
    """两条延迟折线(Modrinth / CurseForge),横轴最近 window_s 秒。"""

    def __init__(self, theme, window_s=120, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.window_s = window_s
        self.setMinimumSize(520, 240)

    def paintEvent(self, _ev):
        from ui import mw_net
        series = mw_net.rtt_series()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        bg = QtGui.QColor(self.theme.get("text_bg", "#ffffff"))
        fg = QtGui.QColor(self.theme.get("fg", "#000000"))
        grid = QtGui.QColor(self.theme.get("muted_fg", "#888888"))
        grid.setAlpha(70)
        p.fillRect(self.rect(), bg)

        left, top, right, bottom = 46, 28, 12, 26
        plot = QtCore.QRectF(left, top, self.width() - left - right,
                             self.height() - top - bottom)
        now = time.time()
        t0 = now - self.window_s
        vals = [v for pts in series.values() for t, v in pts if t >= t0]
        ymax = max(100.0, (max(vals) if vals else 0) * 1.2)

        # 网格 + 纵轴刻度
        f = p.font()
        f.setPixelSize(11)
        p.setFont(f)
        for i in range(5):
            y = plot.bottom() - plot.height() * i / 4.0
            p.setPen(grid)
            p.drawLine(QtCore.QPointF(plot.left(), y), QtCore.QPointF(plot.right(), y))
            p.setPen(fg)
            p.drawText(QtCore.QRectF(0, y - 8, left - 6, 16),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, "%d" % (ymax * i / 4.0))
        p.drawText(QtCore.QRectF(plot.left(), plot.bottom() + 4, plot.width(), 18),
                   QtCore.Qt.AlignLeft, trp("{0} 秒前", self.window_s))
        p.drawText(QtCore.QRectF(plot.left(), plot.bottom() + 4, plot.width(), 18),
                   QtCore.Qt.AlignRight, tr("现在"))

        # 折线 + 图例
        lx = plot.left()
        for name, pts in series.items():
            color = QtGui.QColor(_SERIES_COLORS.get(name, "#4c9aff"))
            pts = [(t, v) for t, v in pts if t >= t0]
            pen = QtGui.QPen(color)
            pen.setWidthF(2.0)
            p.setPen(pen)
            path = QtGui.QPainterPath()
            for i, (t, v) in enumerate(pts):
                x = plot.left() + plot.width() * (t - t0) / self.window_s
                y = plot.bottom() - plot.height() * min(v, ymax) / ymax
                (path.moveTo if i == 0 else path.lineTo)(x, y)
            p.drawPath(path)
            last = ("%d ms" % pts[-1][1]) if pts else tr("无数据")
            p.fillRect(QtCore.QRectF(lx, 9, 10, 10), color)
            p.setPen(fg)
            label = "%s  %s" % (name, last)
            p.drawText(QtCore.QPointF(lx + 14, 19), label)
            lx += 24 + p.fontMetrics().horizontalAdvance(label)
        p.end()


class NetChartDialog(QtWidgets.QDialog):
    """延迟曲线窗口(非模态,1 秒重画一次;数据由主窗口的探测线程每 5 秒补一个点)。"""

    def __init__(self, parent, theme):
        super().__init__(parent)
        self.setWindowTitle(tr("网络延迟"))
        self.resize(640, 320)
        v = QtWidgets.QVBoxLayout(self)
        self.chart = _Chart(theme, parent=self)
        v.addWidget(self.chart, 1)
        note = QtWidgets.QLabel(tr("只测 TCP 443 连通耗时,不调用任何 API、不消耗配额"))
        note.setStyleSheet("color: %s; font-size: 11px;" % theme.get("muted_fg", "#888"))
        v.addWidget(note)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.chart.update)
        self._timer.start(1000)

    def set_theme(self, theme):
        self.chart.theme = theme
        self.chart.update()

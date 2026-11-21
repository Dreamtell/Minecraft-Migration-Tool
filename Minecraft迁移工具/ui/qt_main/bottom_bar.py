# ui/qt_main/bottom_bar.py
"""Qt 主界面的底栏:Tk 的 bottom_frame(ui/mw_pages._create_log_widgets 之后那一条)的对应物。

结构和数值同 Tk(逐项对过 mw_pages.py):
  · 底色是主题的 bottom_bg(浅 #ffffff / 深 #2e2e2e),和窗口主体的 bg 不是一个色
  · 左:网络灯「● 网络已连接」(10 号粗体,颜色由状态定)→ 迷你延迟折线(84×16)→ 📌 按钮(胶囊 30×20,灰蓝)
  · 中:声明「本工具完全免费,仅供个人学习交流使用。严禁倒卖或用于商业目的。」8 号字,**整条居中**
        (不是「剩余空间里居中」——左边有东西时那样会偏右)
  · 右:🔔 消息中心入口,有未读时带条数
  · 高 22

折线的数据和 Tk 是同一个 ui/mw_net 的 rtt_history():窗口 120 秒,Y 轴上限只升不骤降
(新上限 = 旧×0.9 + 目标×0.1),连通绿 #81c784 / 不通红 #c62828,末端画一个实心圆点。
"""
import time

from PySide6 import QtCore, QtGui, QtWidgets

HEIGHT = 22
SPARK_W, SPARK_H = 84, 16
WINDOW_S = 120.0
GREEN = QtGui.QColor(129, 199, 132)
RED = QtGui.QColor(198, 40, 40)


class SparkLine(QtWidgets.QWidget):
    """迷你延迟折线。双击打开完整曲线图。"""

    doubleClicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SPARK_W, SPARK_H)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._y_max = None
        self._bg = QtGui.QColor("#ffffff")

    def set_bg(self, color):
        self._bg = QtGui.QColor(color)
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()

    @staticmethod
    def _history():
        try:
            from ui import mw_net
            return mw_net.rtt_history()
        except Exception:
            return []

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(self.rect(), self._bg)
        now = time.time()
        raw = [(x[0], x[1]) for x in self._history() if now - x[0] <= WINDOW_S]
        w, h = self.width(), self.height()
        if len(raw) < 2:
            p.setPen(QtGui.QColor("#888888"))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "…")
            p.end()
            return
        vmax = max(1, max(v for _, v in raw))
        target = max(50.0, vmax * 1.15)
        if self._y_max is None or target > self._y_max:
            top = target
        else:
            top = self._y_max * 0.9 + target * 0.1       # 上限只升不骤降,曲线不会忽大忽小
        self._y_max = top
        edge = 2.0
        aw, ah = max(1.0, w - 2 * edge), max(1.0, h - 2 * edge)
        span = max(0.0, now - raw[0][0])
        base = min(span, WINDOW_S)
        pts = []
        for ts, v in raw:
            pos = (base - (now - ts)) / WINDOW_S
            x = edge + aw * min(1.0, max(0.0, pos))
            y = h - edge - ah * min(1.0, v / top)
            pts.append(QtCore.QPointF(x, y))
        ok = self._history()[-1][2] > 0
        col = GREEN if ok else RED
        pen = QtGui.QPen(col, 1.4)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawPolyline(QtGui.QPolygonF(pts))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(pts[-1], 1.6, 1.6)
        p.end()


class BottomBar(QtWidgets.QFrame):
    """底栏容器。声明文字用绝对居中(resizeEvent 里摆),不参与布局。"""

    def __init__(self, theme, decl_text, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self.setFixedHeight(HEIGHT)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setAutoFillBackground(True)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 4, 0)
        lay.setSpacing(0)

        self.net_label = QtWidgets.QLabel("")
        f = self.net_label.font()
        f.setPointSizeF(10.0)
        f.setBold(True)
        self.net_label.setFont(f)
        self.net_label.setCursor(QtCore.Qt.PointingHandCursor)
        lay.addWidget(self.net_label)
        lay.addSpacing(4)

        self.spark = SparkLine()
        lay.addWidget(self.spark)
        lay.addSpacing(8)

        # 📌:Tk 里是 30×20 的灰蓝胶囊渐变按钮(#90a4ae→#607d8b,8 号字)
        self.pin_btn = QtWidgets.QPushButton("📌")
        self.pin_btn.setFixedSize(30, 20)
        self.pin_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.pin_btn.setToolTip("")
        lay.addWidget(self.pin_btn)
        lay.addStretch(1)

        self.bell = QtWidgets.QToolButton()
        self.bell.setAutoRaise(True)
        self.bell.setCursor(QtCore.Qt.PointingHandCursor)
        lay.addWidget(self.bell)

        self.decl = QtWidgets.QLabel(decl_text, self)
        df = self.decl.font()
        df.setPointSizeF(8.0)
        self.decl.setFont(df)
        self.decl.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.decl.adjustSize()
        self.set_theme(theme)

        # 折线每 250ms 重画一次(Tk 同)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.spark.update)
        self._timer.start()

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        bg = self._theme.get("bottom_bg", self._theme.get("bg", "#f0f0f0"))
        muted = self._theme.get("muted_fg", "#888888")
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(bg))
        self.setPalette(pal)
        self.setStyleSheet("BottomBar{background:%s;}" % bg)
        self.decl.setStyleSheet("color:%s;background:transparent;" % muted)
        self.spark.set_bg(bg)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.decl.adjustSize()
        self.decl.move((self.width() - self.decl.width()) // 2,
                       (self.height() - self.decl.height()) // 2)

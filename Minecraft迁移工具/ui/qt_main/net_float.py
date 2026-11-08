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
import math
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
# 贴边收纳(对齐 Tk 版 ui/net_float.py):离边 8px 内松手就吸附,缩成 22px 宽的小标签,
# 鼠标移上去展开成完整的圆。展开/收缩只按**真实光标位置**轮询判定(不信 Enter/Leave:
# 几何一变 Windows 会再判一次假 Leave,Tk 版第 7 条坑)。换形态交叉溶解,不"淡出再淡入"。
_TAB_W = 22
_SNAP = 8
_POLL_MS = 120
_COLLAPSE_POLLS = 2      # 光标连续离开这么多次轮询才收回去(240ms),免得擦边就缩
_MORPH_MS = 140
_HINT_RGB = (110, 220, 130)   # 将要吸附时的提示环 / 边缘光带(亮绿)


def _ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class NetFloat(QtWidgets.QWidget):
    """主窗口上的圆形网络浮标。clicked:单击;moved(dx, dy):拖动结束,新的相对偏移。"""

    clicked = QtCore.Signal()
    moved = QtCore.Signal(int, int)

    def __init__(self, host, size=56, rel=None, dock=True, edge_fx=True):
        super().__init__(host, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.NoDropShadowWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._host = host
        self._d = max(40, min(96, int(size or 56)))
        self.setFixedSize(self._full(), self._full())
        self._state = "unknown"
        self._ms = None
        self._hover = False
        self._press = None       # (按下时的全局坐标, 指针在浮标内的偏移)
        self._dragging = False
        self._rel = None
        self.dock_enabled = bool(dock)
        self.edge_fx = bool(edge_fx)
        self._docked = None      # 吸附在哪条边(left/right/top/bottom),None = 没贴边
        self._peek = False       # 贴边时是否展开成完整的圆
        self._out_polls = 0
        self._hint = None        # 拖动中"将要吸附"的那条边
        self._hint_t0 = 0.0
        self._morph_t0 = None    # 正在交叉溶解换形态(开始时间)
        self._glow = None
        self._poll = QtCore.QTimer(self)
        self._poll.timeout.connect(self._poll_hover)
        self._anim = QtCore.QTimer(self)
        self._anim.setTimerType(QtCore.Qt.PreciseTimer)
        self._anim.timeout.connect(self._anim_tick)
        if rel and all(isinstance(v, int) and v >= 0 for v in rel):
            self._rel = (int(rel[0]), int(rel[1]))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(tr("网络延迟浮标:单击看曲线,按住拖动换位置"))
        host.installEventFilter(self)
        if host.isVisible():
            self._restore_dock()
            self._place()
            self.show()

    # ---------- 位置 ----------
    def _full(self):
        return self._d + 2 * _MARGIN

    def _default_rel(self):
        g = self._host.geometry()
        F = self._full()
        return (max(0, g.width() - F - 16), max(0, g.height() - F - 56))

    def _clamp(self, dx, dy):
        """夹在主窗口客户区内(按完整圆的尺寸算,收纳成小标签时位置不跟着变)。"""
        g = self._host.geometry()
        F = self._full()
        return (max(0, min(int(dx), g.width() - F)),
                max(0, min(int(dy), g.height() - F)))

    def _cur_wh(self):
        F = self._full()
        if self._docked and not self._peek and self._morph_t0 is None:
            return (_TAB_W, F) if self._docked in ("left", "right") else (F, _TAB_W)
        return (F, F)

    def _rect_for(self, wh):
        """窗口矩形(屏幕坐标)。贴边时按那条边对齐:小标签和展开的圆都紧贴同一条边。"""
        g = self._host.geometry()
        dx, dy = self._clamp(*(self._rel or self._default_rel()))
        w, h = wh
        e = self._docked
        if e == "right":
            dx = g.width() - w
        elif e == "left":
            dx = 0
        elif e == "bottom":
            dy = g.height() - h
        elif e == "top":
            dy = 0
        return QtCore.QRect(g.x() + dx, g.y() + dy, w, h)

    def _place(self):
        r = self._rect_for(self._cur_wh())
        if r.size() != self.size():
            self.setFixedSize(r.size())
        self.move(r.topLeft())
    # ---------- 贴边收纳 ----------
    def _detect_edge(self, rel=None):
        """离哪条边 8px 以内。同 Tk 版:左右优先于上下;只有相对两边都近(窗口太窄)才不吸。"""
        g = self._host.geometry()
        F = self._full()
        x, y = self._clamp(*(rel or self._rel or self._default_rel()))
        左, 右 = x <= _SNAP, (g.width() - F - x) <= _SNAP
        上, 下 = y <= _SNAP, (g.height() - F - y) <= _SNAP
        if 左 != 右:
            return "left" if 左 else "right"
        if 上 != 下:
            return "top" if 上 else "bottom"
        return None

    def _restore_dock(self):
        """启动时:存的位置本来就贴着边,就直接收成小标签。"""
        self._docked = self._detect_edge() if self.dock_enabled else None
        self._peek = False
        if self._docked:
            self._poll.start(_POLL_MS)

    def set_dock_enabled(self, on):
        self.dock_enabled = bool(on)
        if not self.dock_enabled and self._docked:
            self._undock()
        elif self.dock_enabled and not self._docked:
            self._restore_dock()
        self._place()
        self.update()

    def set_edge_fx(self, on):
        self.edge_fx = bool(on)
        if not self.edge_fx and self._glow is not None:
            self._glow.hide()

    def _undock(self):
        self._docked = None
        self._peek = False
        self._morph_t0 = None
        self._poll.stop()

    def _cursor_inside(self):
        return self.frameGeometry().contains(QtGui.QCursor.pos())

    def _poll_hover(self):
        """贴边时每 120ms 按真实光标判定展开/收缩(不靠 Enter/Leave)。"""
        if not self._docked or self._dragging or not self.isVisible():
            return
        inside = self._cursor_inside()
        if inside:
            self._out_polls = 0
            if not self._peek:
                self._set_peek(True)
        elif self._peek:
            self._out_polls += 1
            if self._out_polls >= _COLLAPSE_POLLS:
                self._out_polls = 0
                self._set_peek(False)

    def _set_peek(self, on):
        """换形态:过渡期窗口先取完整尺寸,两张形态逐帧交叉溶解,结束落到目标尺寸。"""
        if on == self._peek:
            return
        self._peek = bool(on)
        self._morph_t0 = time.perf_counter()
        self._place()                    # 过渡期:_cur_wh 给完整尺寸
        self._ensure_anim()

    def _ensure_anim(self):
        if not self._anim.isActive():
            self._anim.start(8)

    def _anim_tick(self):
        busy = False
        if self._morph_t0 is not None:
            if (time.perf_counter() - self._morph_t0) * 1000.0 >= _MORPH_MS:
                self._morph_t0 = None
                self._place()            # 落到目标尺寸(贴边锚点不变,这一步看不见)
            else:
                busy = True
        if self._hint is not None:
            busy = True
        self.update()
        if not busy:
            self._anim.stop()

    def _show_glow(self, edge):
        if not self.edge_fx or edge is None:
            if self._glow is not None:
                self._glow.hide()
            return
        if self._glow is None:
            self._glow = _EdgeGlow(self._host)
        c = self.frameGeometry().center()
        self._glow.show_on(edge, c)
        self.raise_()                    # 光带在主窗口之上、浮标之下

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
                if self._glow is not None:
                    self._glow.hide()
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
    def _circle_rect(self):
        """完整圆在本窗口里的位置:贴边时圆按那条边对齐(和小标签同一条边)。"""
        F = self._full()
        x = self.width() - F if self._docked == "right" else 0
        y = self.height() - F if self._docked == "bottom" else 0
        return QtCore.QRectF(x + _MARGIN, y + _MARGIN, self._d, self._d)

    def _tab_rect(self):
        F = self._full()
        W, H = self.width(), self.height()
        e = self._docked
        if e == "right":
            return QtCore.QRectF(W - _TAB_W, H - F + _MARGIN, _TAB_W, self._d)
        if e == "left":
            return QtCore.QRectF(0, H - F + _MARGIN, _TAB_W, self._d)
        if e == "bottom":
            return QtCore.QRectF(W - F + _MARGIN, H - _TAB_W, self._d, _TAB_W)
        return QtCore.QRectF(W - F + _MARGIN, 0, self._d, _TAB_W)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if self._docked:
            if self._morph_t0 is not None:
                k = _ease_out((time.perf_counter() - self._morph_t0) * 1000.0 / _MORPH_MS)
                a_circle = k if self._peek else 1.0 - k
            else:
                a_circle = 1.0 if self._peek else 0.0
            if a_circle < 1.0:
                p.setOpacity(1.0 - a_circle)
                self._paint_tab(p)
            if a_circle > 0.0:
                p.setOpacity(a_circle)
                self._paint_circle(p)
        else:
            self._paint_circle(p)
        p.end()

    def _paint_tab(self, p):
        """收纳态:贴边的胶囊小标签,只留状态色条(22px 放不下数字)。"""
        tile, ring, ring_hover = _COLORS.get(self._state, _COLORS["unknown"])
        r = self._tab_rect()
        rad = min(r.width(), r.height()) / 2.0
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(*tile))
        p.drawRoundedRect(r, rad, rad)
        inner = r.adjusted(5, 5, -5, -5)
        ir = min(inner.width(), inner.height()) / 2.0
        p.setBrush(QtGui.QColor(*ring))
        p.drawRoundedRect(inner, ir, ir)

    def _paint_circle(self, p):
        tile, ring, ring_hover = _COLORS.get(self._state, _COLORS["unknown"])
        r = self._circle_rect()
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
        # 拖动中"将要吸附":外圈一道呼吸的亮绿环
        if self._hint is not None:
            ph = (time.perf_counter() - self._hint_t0) * 2.0 * math.pi / 0.9
            c = QtGui.QColor(*_HINT_RGB)
            c.setAlpha(int(110 + 110 * (0.5 + 0.5 * math.sin(ph))))
            hp = QtGui.QPen(c)
            hp.setWidthF(2.0)
            p.setPen(hp)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(r.adjusted(-3, -3, 3, 3))

    # ---------- 鼠标 ----------
    def enterEvent(self, _ev):
        self._hover = True
        if self._docked and not self._peek and not self._dragging:
            self._set_peek(True)          # 立刻展开;收回由 _poll_hover 按真实光标决定
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
        if not self._dragging:
            self._dragging = True
            if self._docked:
                # 从边上拖走:先变回完整的圆,按圆的尺寸重算指针偏移,免得一拖就跳
                off = self._press[1] - (self._circle_rect().topLeft().toPoint()
                                        - QtCore.QPoint(_MARGIN, _MARGIN))
                self._undock()
                self._press = (self._press[0], off)
        # 拖动公式(Tk 版第 4 条坑):指针屏幕坐标 - 主窗口客户区屏幕坐标 - 指针在浮标内的偏移
        origin = self._host.geometry().topLeft()
        tl = g - origin - self._press[1]
        self._rel = self._clamp(tl.x(), tl.y())
        self._place()
        edge = self._detect_edge() if self.dock_enabled else None
        if edge != self._hint:
            self._hint = edge
            self._hint_t0 = time.perf_counter()
            if edge is not None:
                self._ensure_anim()
        self._show_glow(edge)

    def mouseReleaseEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            edge, self._hint = self._hint, None
            if self._glow is not None:
                self._glow.hide()
            if edge is not None:
                # 松手吸附:光标还在上面,先停在展开态,离开后由轮询收成小标签
                self._docked, self._peek, self._out_polls = edge, True, 0
                self._place()
                self._poll.start(_POLL_MS)
            self.update()
            self.moved.emit(*self._rel)
        else:
            self.clicked.emit()


class _EdgeGlow(QtWidgets.QWidget):
    """拖动浮标靠近主窗口某条边时,贴着那条边内侧的绿色光带 + 往里飘的颗粒(对齐 Tk 版)。

    点击穿透(WindowTransparentForInput),不拦鼠标、不抢焦点;只在拖动中显示。
    粒子用「边缘空间」描述:v = 离边深度(0 在边上,1 到最内侧),u = 沿边位置。
    """

    THICK = 22
    LIFE = 2.2

    def __init__(self, host):
        super().__init__(host, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.NoDropShadowWindowHint
                         | QtCore.Qt.WindowTransparentForInput)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._host = host
        self._edge = None
        self._focus = 0.5
        self._parts = []
        self._seed = 1
        self._t_last = time.perf_counter()
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def _rand(self):
        # 不用 random 模块的全局状态:小 LCG 足够撒点
        self._seed = (self._seed * 1103515245 + 12345) & 0x7FFFFFFF
        return self._seed / float(0x7FFFFFFF)

    def show_on(self, edge, center):
        g = self._host.geometry()
        t = self.THICK
        if edge in ("left", "right"):
            x = g.x() if edge == "left" else g.right() - t + 1
            rect = QtCore.QRect(x, g.y(), t, g.height())
            self._focus = (center.y() - g.y()) / float(max(1, g.height()))
        else:
            y = g.y() if edge == "top" else g.bottom() - t + 1
            rect = QtCore.QRect(g.x(), y, g.width(), t)
            self._focus = (center.x() - g.x()) / float(max(1, g.width()))
        if edge != self._edge or not self._parts:
            n = max(18, min(64, int(max(g.width(), g.height()) / 26)))
            self._parts = [[self._rand(), self._rand(), self._rand() * self.LIFE,
                            1.2 + 1.4 * self._rand()] for _ in range(n)]
        self._edge = edge
        if self.geometry() != rect:
            self.setGeometry(rect)
        if not self.isVisible():
            self.show()
        if not self._timer.isActive():
            self._t_last = time.perf_counter()
            self._timer.start(8)

    def hide(self):
        self._edge = None
        self._timer.stop()
        super().hide()

    def _tick(self):
        now = time.perf_counter()
        dt = now - self._t_last
        self._t_last = now
        for pt in self._parts:
            pt[2] += dt
            pt[0] = (pt[2] % self.LIFE) / self.LIFE
        self.update()

    def paintEvent(self, _ev):
        if self._edge is None:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        vertical = self._edge in ("left", "right")
        length = H if vertical else W
        r, g, b = _HINT_RGB
        # 光带:边上最亮,往内侧渐隐;沿边方向在浮标附近更亮
        if vertical:
            x0, x1 = (0, W) if self._edge == "left" else (W, 0)
            lg = QtGui.QLinearGradient(x0, 0, x1, 0)
        else:
            y0, y1 = (0, H) if self._edge == "top" else (H, 0)
            lg = QtGui.QLinearGradient(0, y0, 0, y1)
        lg.setColorAt(0.0, QtGui.QColor(r, g, b, 150))
        lg.setColorAt(1.0, QtGui.QColor(r, g, b, 0))
        p.fillRect(self.rect(), QtGui.QBrush(lg))
        if vertical:
            sg = QtGui.QLinearGradient(0, 0, 0, H)
        else:
            sg = QtGui.QLinearGradient(0, 0, W, 0)
        f = max(0.0, min(1.0, self._focus))
        sg.setColorAt(0.0, QtGui.QColor(0, 0, 0, 120))
        sg.setColorAt(max(0.0, f - 0.25), QtGui.QColor(0, 0, 0, 120))
        sg.setColorAt(f, QtGui.QColor(0, 0, 0, 0))
        sg.setColorAt(min(1.0, f + 0.25), QtGui.QColor(0, 0, 0, 120))
        sg.setColorAt(1.0, QtGui.QColor(0, 0, 0, 120))
        p.setCompositionMode(QtGui.QPainter.CompositionMode_DestinationOut)
        p.fillRect(self.rect(), QtGui.QBrush(sg))
        p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        # 颗粒:从边上生成,往内侧飘、逐渐淡出
        p.setPen(QtCore.Qt.NoPen)
        for v, u, _age, rad in self._parts:
            depth = v * self.THICK
            along = u * length
            if self._edge == "left":
                x, y = depth, along
            elif self._edge == "right":
                x, y = W - depth, along
            elif self._edge == "top":
                x, y = along, depth
            else:
                x, y = along, H - depth
            p.setBrush(QtGui.QColor(r, g, b, int(220 * (1.0 - v))))
            p.drawEllipse(QtCore.QPointF(x, y), rad, rad)
        p.end()


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

# ui/qt_tabs.py
"""圆角药丸页签（Qt 版）—— 和设置窗口的 ui/rounded_tabs.py 长得一模一样。

设置窗口是 Tk 的，它的页签是 Canvas 自绘的圆角药丸（高 34、半径 12、左右内边距
22、选中块按缓动滑过去）。Qt 这边没有现成的等价物：QTabBar 是直角标签，样式表
也没法给"选中块滑动"做动画。所以这里照 Tk 那套参数重画一遍，两边看起来是同一
套控件。

对外：
    tabs = PillTabs(theme)
    tabs.add(tr("📋 表格")); tabs.add(tr("🗂 卡片"))
    tabs.changed.connect(lambda i: stack.setCurrentIndex(i))
    tabs.set_current(1, animate=True)
"""
from PySide6 import QtCore, QtGui, QtWidgets

# 这几个数和 ui/rounded_tabs.py 保持一致（改一边记得改另一边）
BAR_H = 44
PILL_H = 34
PILL_Y = 5
PAD_X = 22
GAP = 4
RADIUS = 12
SLIDE_MS = 170

_LEFT = 6           # 第一个药丸左边距（Tk 那边 _layout_bar 里写死的 x = 6）


def _混(甲, 乙, 比例):
    """甲往乙混（比例 0~1）。悬停、未选中都用它从"背景色"推，不猜主题键。"""
    a, b = QtGui.QColor(甲), QtGui.QColor(乙)
    if not a.isValid():
        a = QtGui.QColor("#2e2e2e")
    if not b.isValid():
        b = QtGui.QColor("#ffffff")
    return QtGui.QColor(
        int(a.red() + (b.red() - a.red()) * 比例),
        int(a.green() + (b.green() - a.green()) * 比例),
        int(a.blue() + (b.blue() - a.blue()) * 比例))


class PillTabs(QtWidgets.QWidget):
    """一排圆角药丸；选中块会滑过去。宽度按文字自适应，水平排列。"""

    changed = QtCore.Signal(int)

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = dict(theme or {})
        self._labels = []
        self._xs = []          # 每个药丸的左边界
        self._ws = []          # 每个药丸的宽度
        self._cur = -1
        self._hover = -1
        self._sel_x = 0.0      # 当前选中块的左边界（动画值）
        self._sel_w = 0.0      # 当前选中块的宽度（动画值）
        self.setFixedHeight(BAR_H)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setDuration(SLIDE_MS)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._font = QtGui.QFont()
        self._font.setBold(True)
        try:
            self._font.setPointSize(10)
        except Exception:
            pass
        self.set_theme(theme)

    # ---------------- 对外 ----------------
    def add(self, text):
        self._labels.append(str(text))
        self._relayout()
        if self._cur < 0:
            self.set_current(0, animate=False)
        return len(self._labels) - 1

    def labels(self):
        return list(self._labels)

    def current(self):
        return self._cur

    def set_current(self, idx, animate=True):
        if not self._labels:
            return
        idx = max(0, min(len(self._labels) - 1, int(idx)))
        if idx == self._cur:
            return
        旧 = self._cur
        self._cur = idx
        目标x, 目标w = self._xs[idx], self._ws[idx]
        if animate and 旧 >= 0:
            self._anim.stop()
            self._anim.setStartValue(QtCore.QPointF(self._sel_x, self._sel_w))
            self._anim.setEndValue(QtCore.QPointF(目标x, 目标w))
            self._anim.start()
        else:
            self._anim.stop()
            self._sel_x, self._sel_w = float(目标x), float(目标w)
            self.update()
        if idx != 旧:
            self.changed.emit(idx)

    def set_theme(self, theme):
        self.theme = dict(theme or {})
        self._fg = self.theme.get("fg", "#eaeaea")
        self._muted = self.theme.get("muted_fg", "#9a9a9a")
        self._同步底色()
        self._sel = self.theme.get("card_sel_bar") or self.theme.get("accent_bg", "#2f7fd1")
        # 选中块底色亮就用深色字（和 Tk 那版同一个判断）
        c = QtGui.QColor(self._sel)
        亮度 = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) if c.isValid() else 0
        self._sel_fg = "#202020" if 亮度 > 170 else "#ffffff"
        self.update()

    # ---------------- 内部 ----------------
    def _同步底色(self):
        """底色取**自己的调色板**（Qt 那个 Window 角色），不是 theme["bg"]。

        两个原因：①真实窗口底色由 ui/qt_big_view.ensure_app() 里那份 Fusion
        palette 决定，theme 字典里那套键是给 Tk 渐变按钮用的，值不一定一样
        （button_bg 在深色主题下是浅灰，照着它画就是白底白字）；②调用方可能
        根本传了个空 theme（离屏探针就踩过），这时跟着调色板走才不会一条突兀
        的深色带子挂在浅色对话框里。
        """
        色 = self.palette().window().color()
        if 色.isValid():
            self._bg = 色.name()
        else:
            self._bg = self.theme.get("bg", "#1e1e1e")
        _c = QtGui.QColor(self._bg)
        self._深 = True
        if _c.isValid():
            self._深 = (0.299 * _c.red() + 0.587 * _c.green()
                       + 0.114 * _c.blue()) < 140
        self._normal = _混(self._bg, self._fg, 0.14 if self._深 else 0.08)
        self._hover_bg = _混(self._bg, self._fg, 0.22 if self._深 else 0.14)

    def showEvent(self, e):
        self._同步底色()      # 窗口底色在 show 之后才定下来（palette 可能刚被应用）
        super().showEvent(e)

    def _relayout(self):
        fm = QtGui.QFontMetrics(self._font)
        x = _LEFT
        self._xs, self._ws = [], []
        for t in self._labels:
            w = fm.horizontalAdvance(t) + PAD_X * 2
            self._xs.append(x)
            self._ws.append(w)
            x += w + GAP
        self.setFixedWidth(min(2000, x + _LEFT))
        if 0 <= self._cur < len(self._xs):
            self._sel_x, self._sel_w = float(self._xs[self._cur]), float(self._ws[self._cur])
        self.update()

    def _index_at(self, x):
        for i, (px, pw) in enumerate(zip(self._xs, self._ws)):
            if px <= x <= px + pw:
                return i
        return -1

    def _on_anim(self, 值):
        try:
            self._sel_x, self._sel_w = float(值.x()), float(值.y())
        except Exception:
            pass
        self.update()

    def mouseMoveEvent(self, e):
        i = self._index_at(int(e.position().x()))
        if i != self._hover:
            self._hover = i
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            i = self._index_at(int(e.position().x()))
            if i >= 0:
                self.set_current(i, animate=True)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # 自己铺底色：QWidget 默认会用调色板的浅色窗口色填一块，深色对话框里
        # 就是一条突兀的白带。这里按主题背景铺，和对话框融为一体。
        p.fillRect(self.rect(), QtGui.QColor(self._bg))
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        p.setFont(self._font)
        # 顺序很重要：先把**所有药丸**铺完，最后才写字。
        # 边画边写字的话，滑动中的选中块会从上面盖住隔壁那个页签的字
        # （实拍过：滑到一半时"表格"两个字被蓝块吃掉，只剩"卡片"）。
        p.setPen(QtCore.Qt.NoPen)
        for i in range(len(self._labels)):
            if i == self._cur:
                continue          # 选中块单独画
            r = QtCore.QRectF(self._xs[i], PILL_Y, self._ws[i], PILL_H)
            p.setBrush(self._hover_bg if i == self._hover
                       else QtGui.QColor(self._normal))
            p.drawRoundedRect(r, RADIUS, RADIUS)
        if self._cur >= 0 and self._sel_w > 0:
            p.setBrush(QtGui.QColor(self._sel))
            p.drawRoundedRect(QtCore.QRectF(self._sel_x, PILL_Y, self._sel_w, PILL_H),
                              RADIUS, RADIUS)
        # 字全部压在药丸上面
        for i, t in enumerate(self._labels):
            r = QtCore.QRectF(self._xs[i], PILL_Y, self._ws[i], PILL_H)
            p.setPen(QtGui.QColor(self._sel_fg if i == self._cur else self._fg))
            p.drawText(r, QtCore.Qt.AlignCenter, t)
        p.end()

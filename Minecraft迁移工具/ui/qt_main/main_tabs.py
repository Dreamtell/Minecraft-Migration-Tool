# ui/qt_main/main_tabs.py
"""主界面的清单页签:Tk 的 ui/rounded_tabs.RoundedTabs 在 Qt 里的对应物。

和 Tk 一致的地方(逐项对过 rounded_tabs.py):
  · 药丸高 34、半径 12、左右内边距 22、间隙 4、条高 44、第一块左边距 6、选中块 170ms OutCubic 滑动
  · 标签「名字 + 条数」两段画:数字用数据色(tab_num_fg;选中块上换亮一档 tab_num_sel_fg)
  · 选中块的字色:块底亮用深字 #202020,暗用白字
  · 条右端挂尾部药丸(⚠ 更新提醒 / 🔁 应用上次记录):**按住 1 秒才触发**(防误点),
    按住期间药丸从左往右填满,松手/移出作废
  · 锁定:迁移 / 扫描期间不让切页(set_locked)
  · 页面切入:新页从下方 10px 滑到位,150ms OutCubic
  · 时长都乘 fx.anim_scale();「特效强度」关闭时一律直接到位

对外模仿 QTabWidget 的一个子集(addTab / widget / count / setTabText / tabText / setCurrentIndex /
currentIndex / currentChanged / tabBar),主窗口里原来按 QTabWidget 写的代码基本不用改。
"""
import re
import time

from PySide6 import QtCore, QtGui, QtWidgets

BAR_H = 44
PILL_H = 34
PILL_Y = 5
PAD_X = 22
GAP = 4
RADIUS = 12
SLIDE_MS = 170
PAGE_MS = 150
LEFT = 6
LONG_PRESS_S = 1.0

_TAIL_NUM = re.compile(r"^(.*?)(\d+)$", re.S)


def _anim_scale():
    try:
        from utils import fx
        return float(fx.anim_scale())
    except Exception:
        return 1.0


def _lighten(h, amount=40):
    c = QtGui.QColor(h)
    return QtGui.QColor(min(255, c.red() + amount), min(255, c.green() + amount),
                        min(255, c.blue() + amount))


def _is_dark(theme):
    c = QtGui.QColor((theme or {}).get("bg", "#f0f0f0"))
    return c.lightness() < 128


class _Bar(QtWidgets.QWidget):
    """药丸那一条:自己画,自己处理悬停/点击/长按。"""

    clicked = QtCore.Signal(int)

    def __init__(self, tabs):
        super().__init__(tabs)
        self._tabs = tabs
        self.setFixedHeight(BAR_H)
        self.setMouseTracking(True)
        self._hover = -1
        self._tail_hover = -1
        self._press = -1                 # 正被按住的尾按钮
        self._press_t0 = 0.0
        self._press_timer = QtCore.QTimer(self)
        self._press_timer.setInterval(16)
        self._press_timer.timeout.connect(self._tick_press)
        self._sel_x = 0.0
        self._sel_w = 0.0
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        # 结束时按当前几何校准:滑动期间标签文字可能改过(条数变了),不然会停在旧尺寸上
        self._anim.finished.connect(self._on_anim_done)

    # ---- 几何 ----
    def font_(self):
        f = QtGui.QFont(self.font())
        f.setPointSizeF(9.0)
        f.setBold(True)
        return f

    def layout_items(self):
        """返回 ([(x, w)...标签], [(x, w)...尾按钮])。尾按钮贴右端,放不下就紧接在标签后面。"""
        fm = QtGui.QFontMetrics(self.font_())
        x = LEFT
        tabs = []
        for t in self._tabs._labels:
            w = fm.horizontalAdvance(t) + PAD_X * 2
            tabs.append((x, w))
            x += w + GAP
        tails = []
        if self._tabs._tails:
            ws = [fm.horizontalAdvance(t["text"]) + PAD_X * 2 for t in self._tabs._tails]
            total = sum(ws) + GAP * (len(ws) - 1)
            start = max(self.width(), x + 6) - 6 - total
            if start < x + GAP:
                start = x + GAP
            for w in ws:
                tails.append((start, w))
                start += w + GAP
        return tabs, tails

    def snap_selection(self):
        tabs, _ = self.layout_items()
        i = self._tabs._cur
        if 0 <= i < len(tabs):
            self._sel_x, self._sel_w = float(tabs[i][0]), float(tabs[i][1])
        self.update()

    def slide_to(self, idx, animate):
        tabs, _ = self.layout_items()
        if not (0 <= idx < len(tabs)):
            return
        x, w = tabs[idx]
        scale = _anim_scale()
        self._anim.stop()
        if animate and scale > 0 and self._sel_w > 0:
            self._anim.setDuration(max(1, int(SLIDE_MS * scale)))
            self._anim.setStartValue(QtCore.QPointF(self._sel_x, self._sel_w))
            self._anim.setEndValue(QtCore.QPointF(float(x), float(w)))
            self._anim.start()
        else:
            self._sel_x, self._sel_w = float(x), float(w)
            self.update()

    def _on_anim(self, v):
        self._sel_x, self._sel_w = float(v.x()), float(v.y())
        self.update()

    def _on_anim_done(self):
        """动画跑完:按当前标签宽度重新贴一次,别停在启动时算出的旧几何上。"""
        self._sel_x = float(self._sel_x)
        self.snap_selection()

    def _hit(self, x):
        tabs, _ = self.layout_items()
        for i, (px, pw) in enumerate(tabs):
            if px <= x < px + pw:
                return i
        return -1

    def _hit_tail(self, x):
        _, tails = self.layout_items()
        for i, (px, pw) in enumerate(tails):
            if px <= x < px + pw:
                return i
        return -1

    # ---- 事件 ----
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.snap_selection()

    def mouseMoveEvent(self, e):
        x = int(e.position().x())
        if self._tabs.locked:
            self.setCursor(QtCore.Qt.ArrowCursor)
            return
        i, tail = self._hit(x), self._hit_tail(x)
        if tail >= 0 and not self._tabs._tails[tail].get("enabled", True):
            tail = -1                           # 禁用的药丸:不给悬停、不给手型
            i = -1 if self._hit(x) < 0 else i
        if self._press >= 0 and tail != self._press:
            self._cancel_press()
        self.setCursor(QtCore.Qt.PointingHandCursor if (i >= 0 or tail >= 0)
                       else QtCore.Qt.ArrowCursor)
        new_h = i if tail < 0 else -1
        if new_h != self._hover or tail != self._tail_hover:
            self._hover, self._tail_hover = new_h, tail
            self.update()

    def leaveEvent(self, e):
        self._cancel_press()
        if self._hover != -1 or self._tail_hover != -1:
            self._hover = self._tail_hover = -1
            self.update()
        super().leaveEvent(e)

    def event(self, e):
        """抓取被收回 / 窗口失活时也取消长按 —— 这两种情况都收不到 mouseReleaseEvent。"""
        if e.type() in (QtCore.QEvent.UngrabMouse, QtCore.QEvent.WindowDeactivate):
            self._cancel_press()
        return super().event(e)

    def mousePressEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton or self._tabs.locked:
            return
        x = int(e.position().x())
        tail = self._hit_tail(x)
        if tail >= 0:
            if not self._tabs._tails[tail].get("enabled", True):
                return
            self._press, self._press_t0 = tail, time.perf_counter()
            self._press_timer.start()
            self.update()
            return
        i = self._hit(x)
        if i >= 0:
            self.clicked.emit(i)

    def mouseReleaseEvent(self, e):
        self._cancel_press()
        super().mouseReleaseEvent(e)

    # ---- 长按尾按钮 ----
    def press_progress(self):
        if self._press < 0:
            return 0.0
        return min(1.0, (time.perf_counter() - self._press_t0) / LONG_PRESS_S)

    def _cancel_press(self):
        if self._press >= 0:
            self._press = -1
            self._press_timer.stop()
            self.update()

    def _tick_press(self):
        if self._press < 0:
            self._press_timer.stop()
            return
        # 鼠标抓取可能被系统收回(弹出菜单 / 切窗口 / 别的窗口抢走 capture):这时收不到
        # mouseReleaseEvent,只靠进度判断就会在松手之后照样触发。左键已经不按着就取消。
        if not (QtGui.QGuiApplication.mouseButtons() & QtCore.Qt.LeftButton):
            self._cancel_press()
            return
        if self.press_progress() >= 1.0:
            idx = self._press
            self._cancel_press()
            tails = self._tabs._tails
            cmd = tails[idx].get("command") if 0 <= idx < len(tails) else None
            if cmd:
                cmd()
            return
        self.update()

    # ---- 配色 ----
    def _tab_colors(self):
        th = self._tabs.theme
        base = QtGui.QColor(th.get("button_bg", "#e0e0e0"))
        hover = _lighten(th.get("button_bg", "#e0e0e0"))
        sel = QtGui.QColor(th.get("card_sel_bar") or th.get("accent_bg", "#2f7fd1"))
        lum = 0.299 * sel.red() + 0.587 * sel.green() + 0.114 * sel.blue()
        sel_fg = QtGui.QColor("#202020" if lum > 170 else "#ffffff")
        return base, hover, sel, sel_fg

    def _tail_color(self, kind, slot):
        dark = _is_dark(self._tabs.theme)
        if slot == "apply":
            if kind == "hover":
                return "#155a86" if dark else "#bfe0f7"
            return "#12496e" if dark else "#d6ecfb"
        if kind == "hover":
            return "#5f1b1e" if dark else "#f8cfc9"
        return "#4d1517" if dark else "#fde5e1"

    def _tail_fg(self, slot):
        dark = _is_dark(self._tabs.theme)
        if slot == "apply":
            return "#66c6ff" if dark else "#1565a8"
        return "#ff6b70" if dark else "#c5221f"

    # ---- 绘制 ----
    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        th = self._tabs.theme
        p.fillRect(self.rect(), QtGui.QColor(th.get("bg", "#f0f0f0")))
        base, hover, sel, sel_fg = self._tab_colors()
        tabs, tails = self.layout_items()
        font = self.font_()
        p.setFont(font)
        fm = QtGui.QFontMetrics(font)
        p.setPen(QtCore.Qt.NoPen)
        # 先铺所有药丸、最后才写字(滑动中的选中块不能盖住邻居的字)
        for i, (x, w) in enumerate(tabs):
            if i == self._tabs._cur:
                continue
            p.setBrush(hover if (i == self._hover and not self._tabs.locked) else base)
            p.drawRoundedRect(QtCore.QRectF(x, PILL_Y, w, PILL_H), RADIUS, RADIUS)
        if self._tabs._cur >= 0 and self._sel_w > 0:
            p.setBrush(sel)
            p.drawRoundedRect(QtCore.QRectF(self._sel_x, PILL_Y, self._sel_w, PILL_H),
                              RADIUS, RADIUS)
        for j, (x, w) in enumerate(tails):
            t = self._tabs._tails[j]
            col = QtGui.QColor(self._tail_color("hover" if j == self._tail_hover else "normal",
                                                t["kind"]))
            if not t.get("enabled", True):          # 禁用:往窗口底色靠 65%,看着是「点不动」
                bgc = QtGui.QColor(th.get("bg", "#f0f0f0"))
                col = QtGui.QColor(int(col.red() * 0.35 + bgc.red() * 0.65),
                                   int(col.green() * 0.35 + bgc.green() * 0.65),
                                   int(col.blue() * 0.35 + bgc.blue() * 0.65))
            p.setBrush(col)
            p.drawRoundedRect(QtCore.QRectF(x, PILL_Y, w, PILL_H), RADIUS, RADIUS)
            if j == self._press:                   # 长按进度:从左往右填满(提亮 46)
                filled = w * self.press_progress()
                p.save()
                clip = QtGui.QPainterPath()
                clip.addRoundedRect(QtCore.QRectF(x, PILL_Y, w, PILL_H), RADIUS, RADIUS)
                p.setClipPath(clip)
                p.fillRect(QtCore.QRectF(x, PILL_Y, filled, PILL_H),
                           _lighten(self._tail_color("normal", t["kind"]), 46))
                p.restore()
        # 字
        cy = PILL_Y + PILL_H / 2.0
        for i, (x, w) in enumerate(tabs):
            label = self._tabs._labels[i]
            m = _TAIL_NUM.match(label)
            main, num = (m.group(1), m.group(2)) if m else (label, "")
            is_sel = (i == self._tabs._cur)
            total = max(fm.horizontalAdvance(main) + (fm.horizontalAdvance(num) if num else 0),
                        fm.horizontalAdvance(label))
            x0 = x + max(0, (w - total) // 2)
            rect_h = QtCore.QRectF(0, cy - 20, 0, 40)
            p.setPen(sel_fg if is_sel else QtGui.QColor(th.get("fg", "#000000")))
            p.drawText(QtCore.QRectF(x0, cy - 20, fm.horizontalAdvance(main) + 4, 40),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, main)
            if num:
                key = "tab_num_sel_fg" if is_sel else "tab_num_fg"
                p.setPen(QtGui.QColor(th.get(key) or th.get("data_num_fg") or th.get("fg", "#000")))
                p.drawText(QtCore.QRectF(x0 + fm.horizontalAdvance(main), cy - 20,
                                         fm.horizontalAdvance(num) + 4, 40),
                           QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, num)
            del rect_h
        for j, (x, w) in enumerate(tails):
            t = self._tabs._tails[j]
            p.setPen(QtGui.QColor(self._tail_fg(t["kind"]) if t.get("enabled", True)
                                  else th.get("muted_fg", "#888888")))
            p.drawText(QtCore.QRectF(x, PILL_Y, w, PILL_H), QtCore.Qt.AlignCenter, t["text"])
        p.end()


class _Pages(QtWidgets.QWidget):
    """页面容器:只显示当前页,切换时新页从下方 10px 滑到位。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages = []
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._cur_page = None
        self._dy = 0

    def add(self, page):
        page.setParent(self)
        page.hide()
        self._pages.append(page)

    def show_page(self, idx, animate):
        self._anim.stop()
        for i, pg in enumerate(self._pages):
            pg.setVisible(i == idx)
        self._cur_page = self._pages[idx] if 0 <= idx < len(self._pages) else None
        scale = _anim_scale()
        if self._cur_page is None:
            return
        if animate and scale > 0:
            self._dy = 10
            self._anim.setStartValue(10.0)
            self._anim.setEndValue(0.0)
            self._anim.setDuration(max(1, int(PAGE_MS * scale)))
            self._anim.start()
        else:
            self._dy = 0
        self._place()

    def _on_anim(self, v):
        self._dy = int(round(float(v)))
        self._place()

    def _place(self):
        if self._cur_page is not None:
            self._cur_page.setGeometry(0, self._dy, self.width(), self.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()


class TailHandle:
    """尾部药丸的句柄:一个自绘药丸没有自己的 QWidget,这里模仿 QPushButton 的最小接口,
    让主窗口和测试可以按「按钮」的方式读写它(文字 / 可用 / 可见 / 点击)。

    click() 直接触发命令——给键盘和程序用;真人鼠标走长按 1 秒(防误点,同 Tk)。
    """

    def __init__(self, tabs, key, kind, text="", command=None, visible=True):
        self._tabs = tabs
        self.key = key
        self.kind = kind
        self._text = str(text)
        self.command = command
        self._enabled = True
        self._visible = bool(visible)

    def text(self):
        return self._text

    def setText(self, text):
        self._text = str(text)
        self._tabs._sync_tails()

    def isEnabled(self):
        return self._enabled

    def setEnabled(self, on):
        self._enabled = bool(on)
        self._tabs._sync_tails()

    def isVisible(self):
        return self._visible

    def setVisible(self, on):
        self._visible = bool(on)
        self._tabs._sync_tails()

    def click(self):
        if self._enabled and self._visible and self.command:
            self.command()

    def parentWidget(self):
        return self._tabs


class MainTabs(QtWidgets.QWidget):
    currentChanged = QtCore.Signal(int)

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = dict(theme or {})
        self._labels = []
        self._tails = []                 # 真正画出来的(只含可见的)
        self._handles = []               # 全部句柄(含隐藏的),顺序固定
        self._cur = -1
        self.locked = False
        self.bar = _Bar(self)
        self.bar.clicked.connect(self._on_bar_click)
        self.pages = _Pages(self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.bar)
        lay.addWidget(self.pages, 1)

    # ---- QTabWidget 风格的接口 ----
    def addTab(self, page, label):
        self._labels.append(str(label))
        self.pages.add(page)
        idx = len(self._labels) - 1
        if self._cur < 0:
            self._cur = 0
            self.pages.show_page(0, animate=False)
            self.bar.snap_selection()
        self.bar.snap_selection()
        self.bar.update()
        return idx

    def widget(self, idx):
        return self.pages._pages[idx] if 0 <= idx < len(self.pages._pages) else None

    def count(self):
        return len(self._labels)

    def tabBar(self):
        return self.bar

    def tabText(self, idx):
        return self._labels[idx] if 0 <= idx < len(self._labels) else ""

    def setTabText(self, idx, text):
        """只改文字(主界面拿它挂条数)。文字变了宽度就变:整条重排,选中块立刻贴回当前页,不做动画。

        如果此刻正在滑动(启动恢复上次页签后就紧接着写条数),先把动画停掉 —— 否则下一次
        tick 会用旧尺寸的插值把刚贴好的几何覆盖回去,结束时停在旧位置。
        """
        if not (0 <= idx < len(self._labels)) or self._labels[idx] == str(text):
            return
        self._labels[idx] = str(text)
        anim = getattr(self.bar, "_anim", None)
        if anim is not None and anim.state() == QtCore.QAbstractAnimation.Running:
            anim.stop()
        self.bar.snap_selection()

    def currentIndex(self):
        return self._cur

    def setCurrentIndex(self, idx, animate=True):
        if not (0 <= idx < len(self._labels)) or idx == self._cur:
            return
        old = self._cur
        self._cur = idx
        self.pages.show_page(idx, animate=animate and old >= 0)
        self.bar.slide_to(idx, animate=animate and old >= 0)
        self.currentChanged.emit(idx)

    def _on_bar_click(self, idx):
        if not self.locked:
            self.setCurrentIndex(idx)

    # ---- Tk RoundedTabs 的额外接口 ----
    def set_locked(self, locked):
        """锁定 / 解锁切页(迁移、扫描期间锁上)。锁上时不给悬停高亮和手型光标。"""
        self.locked = bool(locked)
        self.bar.setCursor(QtCore.Qt.ArrowCursor)
        if self.locked:
            self.bar._hover = self.bar._tail_hover = -1
            self.bar._cancel_press()
        self.bar.update()

    def add_tail(self, key, kind, text, command, visible=True):
        """加一颗尾部药丸,返回它的句柄。kind: "warn"(红)/"apply"(蓝)。"""
        h = TailHandle(self, key, kind, text, command, visible)
        self._handles.append(h)
        self._sync_tails()
        return h

    def _sync_tails(self):
        """按句柄状态重生成要画的尾按钮:隐藏的不画;禁用的照画但点不动、不给悬停。"""
        self._tails = [{"text": h._text, "command": (h.command if h._enabled else None),
                        "kind": h.kind, "enabled": h._enabled, "handle": h}
                       for h in self._handles if h._visible]
        self.bar._cancel_press()
        self.bar.update()

    def set_theme(self, theme):
        self.theme = dict(theme or {})
        self.bar.update()
        self.pages.update()

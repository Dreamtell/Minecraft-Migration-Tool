# ui/qt_main/messages.py
"""Qt 版消息中心:右侧停靠面板,列出 utils.message_store 里的消息。

和 Tk 版共用同一份存档(.minecraft_migrate_messages.json),warn/error 跨次运行保留。
Tk 版是自绘的滑出抽屉(SetWindowRgn 圆角 + 逐帧动画);Qt 这边用 QDockWidget,
停靠在主窗口右侧,可以拖出来浮动,也能用 Ctrl+M / 状态栏铃铛开关。
"""
import sys
import time
from pathlib import Path

_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtWidgets, QtCore, QtGui  # noqa: E402

from utils.i18n import tr, trp  # noqa: E402
from utils.message_store import 外观, 级别名  # noqa: E402


class MessageDock(QtWidgets.QDockWidget):
    """消息中心面板。refresh() 从 store 重画列表;显示出来时把未读清零。"""

    def __init__(self, parent, store, theme, close_on_outside_click=False):
        super().__init__(tr("🔔 消息中心"), parent)
        self.store = store
        self.theme = theme
        self._outside = False
        self._bell = None
        self.setObjectName("message_dock")       # saveState 需要 objectName
        self.setAllowedAreas(QtCore.Qt.RightDockWidgetArea | QtCore.Qt.LeftDockWidgetArea)
        self.setMinimumWidth(320)
        self.setMinimumHeight(300)

        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(6, 6, 6, 6)
        self.list = QtWidgets.QListWidget()
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        v.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        self.count_label = QtWidgets.QLabel("")
        row.addWidget(self.count_label)
        row.addStretch()
        clear = QtWidgets.QPushButton(tr("清空"))
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        v.addLayout(row)
        self.setWidget(body)

        self.visibilityChanged.connect(self._on_visible)
        self.refresh()
        self.set_close_on_outside_click(close_on_outside_click)

    # ---- 点消息中心以外就收起(配置键 msg_click_outside_close,默认关;同 Tk 版) ----
    def set_close_on_outside_click(self, on):
        """开着才在整个应用上挂点击监听;关掉就摘掉,不白占事件循环。"""
        on = bool(on)
        if on == self._outside:
            return
        self._outside = on
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)

    def set_bell(self, widget):
        """铃铛按钮自己负责开关面板,不能被「点外面」当成外部点击(否则点铃铛 = 先关再开)。"""
        self._bell = widget

    def _inside_dock_or_bell(self, obj):
        w = obj if isinstance(obj, QtWidgets.QWidget) else None
        while w is not None:
            if w is self or w is self._bell:
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, obj, ev):
        if (self._outside and self.isVisible() and not self.isFloating()
                and ev.type() == QtCore.QEvent.MouseButtonPress
                and isinstance(obj, QtWidgets.QWidget)
                and obj is not self.parentWidget()
                and obj.window() is self.parentWidget()
                and not self._inside_dock_or_bell(obj)):
            # 只在点中主窗口里的控件时收起;弹出的对话框 / 菜单是别的顶层窗口,不算。
            # ⚠ 鼠标按下会沿父控件链往上冒:点面板里的控件时,事件随后还会被送给主窗口本身
            # (它不在面板里),必须把「主窗口自己」排除掉,否则点面板也被当成点外面
            self.hide()
        return False

    def closeEvent(self, ev):
        self.set_close_on_outside_click(False)
        super().closeEvent(ev)

    def set_theme(self, theme):
        self.theme = theme
        self.refresh()

    def refresh(self):
        items = self.store.全部() if self.store else []
        self.list.clear()
        muted = self.theme.get("muted_fg", "#888")
        # 最新的放最上面(和 Tk 抽屉一致)
        for x in reversed(items):
            lvl = x.get("level") if x.get("level") in 外观 else "info"
            icon, color = 外观[lvl]
            when = time.strftime("%m-%d %H:%M:%S", time.localtime(float(x.get("t") or 0)))
            n = int(x.get("n") or 1)
            text = str(x.get("text") or "")
            if n > 1:
                text += "  ×%d" % n
            head = "%s  %s · %s" % (icon, when, tr(级别名.get(lvl, "")))
            if x.get("旧"):
                head += "  " + tr("(上次运行)")
            it = QtWidgets.QListWidgetItem(head + "\n" + text)
            it.setForeground(QtGui.QColor(color if not x.get("旧") else muted))
            if x.get("未读"):
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.list.addItem(it)
        self.count_label.setText(trp("共 {0} 条", len(items)))

    def _on_visible(self, visible):
        if visible and self.store is not None:
            self.store.标记已读()
            self.refresh()
            p = self.parent()
            if hasattr(p, "_refresh_msg_badge"):
                p._refresh_msg_badge()

    def _clear(self):
        if self.store is None:
            return
        self.store.clear()
        self.refresh()
        p = self.parent()
        if hasattr(p, "_refresh_msg_badge"):
            p._refresh_msg_badge()

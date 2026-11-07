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

    def __init__(self, parent, store, theme):
        super().__init__(tr("🔔 消息中心"), parent)
        self.store = store
        self.theme = theme
        self.setObjectName("message_dock")       # saveState 需要 objectName
        self.setAllowedAreas(QtCore.Qt.RightDockWidgetArea | QtCore.Qt.LeftDockWidgetArea)
        self.setMinimumWidth(320)

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

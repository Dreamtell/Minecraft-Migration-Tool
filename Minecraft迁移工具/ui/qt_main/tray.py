# ui/qt_main/tray.py
"""Qt 版托盘 + 第二实例唤起。

为什么不复用 ui/tray.py(pywin32):那套是为 Tk 主线程设计的(消息靠 Tk 的 after
泵抽),而 Qt 主窗口有自己的事件循环 —— QSystemTrayIcon 直接挂在上面,不需要
任何轮询,也不依赖 pywin32。

第二实例唤起:Tk 版靠给托盘的 win32 隐藏窗口 PostMessage。Qt 版没有那个窗口,
改用 QLocalServer(Windows 上是命名管道):已运行的实例监听,新实例连上去发一句
"show" 就退出。
"""
import sys
from pathlib import Path

_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtWidgets, QtCore, QtGui, QtNetwork  # noqa: E402

from utils.i18n import tr  # noqa: E402

# 管道名:同一用户下唯一即可(QLocalServer 在 Windows 上自带按用户隔离的前缀)
SERVER_NAME = "MinecraftMigrationTool-QtMain"


def request_show_existing(timeout_ms=500):
    """新实例调用:请已在运行的 Qt 实例把主界面叫出来。成功返回 True。"""
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(b"show\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer(QtCore.QObject):
    """已运行实例这边:监听唤起请求,收到 show 就发 show_requested 信号。"""

    show_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QtNetwork.QLocalServer(self)
        self._server.newConnection.connect(self._on_conn)
        # 上次异常退出可能留下同名管道,先清掉再监听
        QtNetwork.QLocalServer.removeServer(SERVER_NAME)
        self.ok = self._server.listen(SERVER_NAME)

    def _on_conn(self):
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        sock.readyRead.connect(lambda s=sock: self._on_read(s))
        sock.disconnected.connect(sock.deleteLater)

    def _on_read(self, sock):
        data = bytes(sock.readAll()).strip()
        if data == b"show":
            self.show_requested.emit()

    def close(self):
        try:
            self._server.close()
        except Exception:
            pass


class QtTray(QtCore.QObject):
    """托盘图标。菜单:打开主界面 / 关闭窗口时收进托盘(勾选) / 退出。

    信号:
      open_requested   —— 左键单击或菜单「打开主界面」
      exit_requested   —— 菜单「退出」(是否真退由主窗口判断任务状态)
      close_to_tray_toggled(bool) —— 勾选状态变化
    """

    open_requested = QtCore.Signal()
    exit_requested = QtCore.Signal()
    close_to_tray_toggled = QtCore.Signal(bool)

    def __init__(self, icon_path=None, parent=None):
        super().__init__(parent)
        icon = QtGui.QIcon(icon_path) if icon_path else QtGui.QIcon()
        if icon.isNull():
            icon = QtWidgets.QApplication.style().standardIcon(
                QtWidgets.QStyle.SP_ComputerIcon)
        self.tray = QtWidgets.QSystemTrayIcon(icon, self)
        self.tray.setToolTip(tr("Minecraft 整合包迁移工具"))

        menu = QtWidgets.QMenu()
        act_open = menu.addAction(tr("打开主界面"))
        act_open.triggered.connect(self.open_requested.emit)
        self.act_close_to_tray = menu.addAction(tr("关闭窗口时收进托盘"))
        self.act_close_to_tray.setCheckable(True)
        self.act_close_to_tray.toggled.connect(self.close_to_tray_toggled.emit)
        menu.addSeparator()
        act_exit = menu.addAction(tr("退出"))
        act_exit.triggered.connect(self.exit_requested.emit)
        self._menu = menu               # 持有引用,防止被回收
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

    @staticmethod
    def available():
        return QtWidgets.QSystemTrayIcon.isSystemTrayAvailable()

    def show(self):
        self.tray.show()

    def hide(self):
        self.tray.hide()

    def set_close_to_tray(self, on):
        """同步勾选状态(不触发 toggled 回环)。"""
        self.act_close_to_tray.blockSignals(True)
        self.act_close_to_tray.setChecked(bool(on))
        self.act_close_to_tray.blockSignals(False)

    def set_tooltip(self, text):
        self.tray.setToolTip(text)

    def notify(self, title, msg, ms=4000):
        """托盘气泡通知(Windows 上走系统通知)。"""
        try:
            self.tray.showMessage(title, msg, QtWidgets.QSystemTrayIcon.Information, ms)
        except Exception:
            pass

    def _on_activated(self, reason):
        if reason in (QtWidgets.QSystemTrayIcon.Trigger,
                      QtWidgets.QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

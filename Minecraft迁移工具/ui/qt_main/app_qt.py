# ui/qt_main/app_qt.py
"""Qt 主界面的正式启动入口(app.py 在 main_window_backend == "qt" 时调这里)。

和 Tk 版 app.main() 的分工对应:
  - 单实例:app.py 已经用 tendo 拿过锁了;第二实例唤起走 QLocalServer(见 tray.py)
  - 语言:首次启动(配置里没有 language 键)用 Qt 小窗问一次,再 i18n.install_qt()
    (⚠ 不能用 set_language/install:那两个会往进程里装 Tk 控件钩子)
  - 闪屏:复用 ui/splash_qt 的独立进程闪屏(launch → wait_ready → close)
  - 托盘:QSystemTrayIcon(ui/qt_main/tray.py),不依赖 pywin32
  - 通知:winotify(和 Tk 版同一个 app_id),装不上就退回托盘气泡

⚠ 这个进程里**绝不 import tkinter 界面模块**:Tk 和 Qt 共享主线程就是 HANDOFF.md
  里那个 GIL 致命错误的根源。app.py 走到这里之前只 import 了 tkinter 模块本身
  (没建 Tk()),不会起 Tcl 解释器,不冲突。
"""
import sys
import time
from pathlib import Path

from utils import fx

_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

SPLASH_MIN_SEC = 1.5


def qt_available():
    """只探测不 import(和 mw_qt._qt_available 同一个原则)。"""
    try:
        import importlib.util
        return importlib.util.find_spec("PySide6") is not None
    except Exception:
        return False


def _send_toast(title, msg):
    try:
        from winotify import Notification, audio
        t = Notification(app_id="迁移工坊", title=title, msg=msg, duration="short")
        t.set_audio(audio.Default, loop=False)
        t.show()
        return True
    except Exception:
        return False


def _ask_language_qt(QtWidgets, icon_path):
    """首次启动选语言(Qt 版的 ui/lang_picker.ask_language)。返回 "zh" / "en"。"""
    from PySide6 import QtGui
    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("Language / 语言")
    if icon_path:
        dlg.setWindowIcon(QtGui.QIcon(icon_path))
    v = QtWidgets.QVBoxLayout(dlg)
    v.addWidget(QtWidgets.QLabel("请选择界面语言 / Choose your language"))
    row = QtWidgets.QHBoxLayout()
    pick = {"v": "zh"}
    for text, code in (("中文", "zh"), ("English", "en")):
        b = QtWidgets.QPushButton(text)
        b.setMinimumHeight(36)
        b.clicked.connect(lambda _=False, c=code: (pick.__setitem__("v", c), dlg.accept()))
        row.addWidget(b)
    v.addLayout(row)
    dlg.exec()
    return pick["v"]


def run(icon_path=None):
    """建 QApplication + 主窗口 + 托盘,跑事件循环。返回退出码。"""
    from PySide6 import QtWidgets, QtGui
    from utils.config import load_raw_config, save_raw_config
    from utils import i18n

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    if icon_path:
        app.setWindowIcon(QtGui.QIcon(icon_path))

    # 第二实例唤起:先挂上服务端(tendo 锁保证这里只有第一个实例能走到)
    from ui.qt_main.tray import InstanceServer, QtTray
    server = InstanceServer()

    # 首次启动先问语言 —— 必须在 install() 和界面模块 import 之前
    if "language" not in load_raw_config():
        try:
            lang = _ask_language_qt(QtWidgets, icon_path)
        except Exception:
            lang = "zh"
        try:
            i18n.set_language_qt(lang)          # 不走 install()(那是 Tk 钩子)
            save_raw_config({"language": lang})
        except Exception:
            pass
    i18n.install_qt()

    cfg = fx.from_config(load_raw_config())
    try:                                  # 特效强度：闪屏/吐司/浮标都读这份缓存
        fx.bootstrap(cfg)
    except Exception:
        pass

    # 闪屏(独立进程,失败就不显示,别挡启动)
    splash = None
    t0 = time.perf_counter()
    if bool(cfg.get("splash", True)):
        try:
            from ui import splash_qt
            handle = splash_qt.launch()
            splash = splash_qt.wait_ready(handle)
        except Exception:
            splash = None

    from ui.qt_main.window import QtMainWindow
    win = QtMainWindow()

    # 托盘:关窗不再退出,由 close_action 决定;没有系统托盘就退回"关窗即退出"
    tray = None
    if QtTray.available():
        try:
            tray = QtTray(icon_path)
            tray.show()
            win.attach_tray(tray, notify_cb=lambda t, m: _send_toast(t, m) or tray.notify(t, m))
        except Exception:
            tray = None
    if tray is not None:
        app.setQuitOnLastWindowClosed(False)   # 收进托盘时窗口全隐藏,进程得活着
    else:
        app.setQuitOnLastWindowClosed(True)

    server.show_requested.connect(win.show_from_tray)

    # 凑够闪屏最短时长,再收闪屏、显示主窗口
    if splash is not None:
        remain = SPLASH_MIN_SEC - (time.perf_counter() - t0)
        if remain > 0:
            time.sleep(remain)
        try:
            splash.close()
        except Exception:
            pass
    win.show()
    win.raise_()
    win.activateWindow()

    try:
        code = app.exec()
    finally:
        server.close()
        if tray is not None:
            tray.hide()
    return code

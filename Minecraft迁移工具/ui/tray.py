# ui/tray.py
"""系统托盘图标：关掉主窗口后程序挂在后台，点托盘图标再叫出来。

Shell_NotifyIcon 需要一个能收消息的窗口，而 Tk 不会把 WM_APP 这类消息交回
Python，所以这里自己起一个隐藏窗口 + 消息循环，跑在单独的线程里。托盘上发生的
事情（左键点图标、右键菜单选了什么）只往队列里塞一个字符串，由 Tk 主线程用
root.after 轮询后执行——Tk 的 API 只能在主线程碰。

没装 pywin32 时 available() 返回 False，调用方直接退回"关窗口就退出"。
"""
import queue
import threading

try:
    import win32api
    import win32con
    import win32gui
    _AVAILABLE = True
except Exception:                       # pywin32 没装
    _AVAILABLE = False

WM_TRAY = 0x0400 + 20                   # WM_APP + 20，托盘回调消息
_TRAY_ID = 1
_CLASS_NAME = "MCToolTrayWnd"
_SHOW_MSG_NAME = "MCTOOL_SHOW_MAIN_WINDOW"   # 跨进程：让已在跑的实例把窗口叫出来

# 菜单项 id
_MENU_OPEN = 1
_MENU_EXIT = 2
_MENU_CLOSE_TRAY = 3


def available():
    """能不能用系统托盘（需要 pywin32）。"""
    return _AVAILABLE


def request_show_existing():
    """请已经在运行的那个实例把主界面叫出来（第二个实例启动时用）。

    窗口挂在托盘里时是 withdraw 状态，靠标题 FindWindow + ShowWindow 叫不回来，
    所以直接找到托盘的隐藏窗口，给它发一条注册过的消息。
    """
    if not _AVAILABLE:
        return False
    try:
        hwnd = win32gui.FindWindow(_CLASS_NAME, None)
        if not hwnd:
            return False
        msg = win32gui.RegisterWindowMessage(_SHOW_MSG_NAME)
        win32gui.PostMessage(hwnd, msg, 0, 0)
        return True
    except Exception:
        return False


class TrayIcon:
    """托盘图标。commands 队列里会收到 "open"（打开主界面）/ "exit"（退出）。"""

    def __init__(self, tooltip="Minecraft 整合包迁移工具", icon_path=None):
        if not _AVAILABLE:
            raise RuntimeError("系统托盘需要 pywin32：pip install pywin32")
        self.tooltip = tooltip
        self.icon_path = icon_path
        self.commands = queue.Queue()
        # 右键菜单里「关闭窗口时收进托盘」的勾选状态，由主线程更新
        self.close_to_tray = True
        self._hwnd = None
        self._hicon = None
        self._thread = None
        self._ready = threading.Event()
        self._added = False

    # ------------------------------------------------------------ 对外接口
    def start(self, timeout=3.0):
        """起后台线程并把图标挂上去；成功返回 True。"""
        self._thread = threading.Thread(target=self._run, name="tray-icon",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(timeout)       # 等图标真的加上，别让关窗口时还没有托盘
        return self._added

    def stop(self):
        """撤掉图标并结束后台线程。可以重复调用。"""
        hwnd, self._hwnd = self._hwnd, None
        if hwnd:
            try:
                if self._added:
                    win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, _TRAY_ID))
            except Exception:
                pass
            self._added = False
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        self._hicon = None

    def set_tooltip(self, text):
        """改托盘提示文字（窗口显示/隐藏时切换）。"""
        self.tooltip = text
        if not (self._hwnd and self._added):
            return
        try:
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_MODIFY,
                (self._hwnd, _TRAY_ID, win32gui.NIF_TIP, WM_TRAY,
                 self._hicon or 0, text))
        except Exception:
            pass

    # ------------------------------------------------------------ 后台线程
    def _run(self):
        try:
            hinst = win32api.GetModuleHandle(None)
            wc = win32gui.WNDCLASS()
            wc.hInstance = hinst
            wc.lpszClassName = _CLASS_NAME
            wc.lpfnWndProc = self._wndproc      # 绑在 self 上，别被回收
            try:
                atom = win32gui.RegisterClass(wc)
            except Exception:
                atom = _CLASS_NAME              # 类已注册（重启托盘）就直接用名字
            self._hwnd = win32gui.CreateWindow(atom, "mctool-tray", 0,
                                               0, 0, 0, 0, 0, 0, hinst, None)
            self._add_icon()
        except Exception:
            self._added = False
            self._ready.set()
            return
        self._ready.set()
        try:
            win32gui.PumpMessages()             # 隐藏窗口的消息循环
        except Exception:
            pass

    def _add_icon(self):
        hicon = 0
        if self.icon_path:
            try:
                hicon = win32gui.LoadImage(
                    0, self.icon_path, win32con.IMAGE_ICON, 0, 0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
            except Exception:
                hicon = 0
        if not hicon:
            hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        self._hicon = hicon
        # 注意：pywin32 的 Shell_NotifyIcon 成功返回 None、失败抛异常，
        # 所以这里不能用返回值判断（bool(None) 永远是 False）。
        try:
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_ADD,
                (self._hwnd, _TRAY_ID,
                 win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                 WM_TRAY, hicon, self.tooltip))
            self._added = True
        except Exception:
            self._added = False

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
                self.commands.put("open")
            elif lparam in (win32con.WM_RBUTTONUP, win32con.WM_CONTEXTMENU):
                self._popup_menu()
            return 0
        if msg == win32gui.RegisterWindowMessage(_SHOW_MSG_NAME):
            self.commands.put("open")       # 第二个实例来敲门
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            if self._added:
                try:
                    win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, _TRAY_ID))
                except Exception:
                    pass
                self._added = False
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _popup_menu(self):
        try:
            menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(menu, win32con.MF_STRING, _MENU_OPEN, "打开主界面")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            flags = win32con.MF_STRING
            if self.close_to_tray:
                flags |= win32con.MF_CHECKED
            win32gui.AppendMenu(menu, flags, _MENU_CLOSE_TRAY,
                                "关闭窗口时收进托盘")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, _MENU_EXIT, "退出")
            # 不先把窗口设成前台，菜单点到别处不会消失（Windows 的老毛病）
            try:
                win32gui.SetForegroundWindow(self._hwnd)
            except Exception:
                pass
            x, y = win32gui.GetCursorPos()
            cmd = win32gui.TrackPopupMenu(
                menu,
                win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON |
                win32con.TPM_RETURNCMD,
                x, y, 0, self._hwnd, None)
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)
            except Exception:
                pass
            win32gui.DestroyMenu(menu)
            if cmd == _MENU_OPEN:
                self.commands.put("open")
            elif cmd == _MENU_CLOSE_TRAY:
                self.commands.put("toggle_close_to_tray")
            elif cmd == _MENU_EXIT:
                self.commands.put("exit")
        except Exception:
            pass

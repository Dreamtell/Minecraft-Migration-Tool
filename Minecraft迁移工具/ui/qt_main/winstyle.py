# ui/qt_main/winstyle.py
"""Qt 窗口的原生外观:标题栏跟随主题(Win11 深色标题栏)+ 系统圆角。

和 utils/helpers.style_window_hwnd 是同一套 DWM 属性、同样的色值,只是那个模块顶层
import tkinter,纯 Qt 进程不能碰,所以在这里重写一份。

⚠ 不要给窗口加半透明 / 无边框:窗口一旦是 layered,DWM 会跳过自己的弹出 / 关闭 / 最小化动画
(实测"窗口啪一下就没了")。所以保持原生框,只改标题栏配色。
没有 Windows / 系统不支持 / 任何异常都静默返回 False,界面退回系统默认外观。
"""
import sys


def style_window_hwnd(hwnd, dark=False, round_corners=True):
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        dwm = ctypes.windll.dwmapi
        h = ctypes.c_void_p(int(hwnd))

        def _set(attr, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(h, ctypes.c_uint(attr), ctypes.byref(v),
                                      ctypes.sizeof(v))

        # 只把 immersive dark 设回 0 不会恢复浅色标题栏(DWM 对第一次的 dark 有粘性),
        # 所以标题栏底色 / 字色要显式再写一遍
        _set(20, 1 if dark else 0)                     # DWMWA_USE_IMMERSIVE_DARK_MODE
        if dark:
            r = g = b = 0x20
            tr = tg = tb = 0xff
        else:
            r = g = b = 0xf0
            tr = tg = tb = 0x00
        _set(35, r | (g << 8) | (b << 16))             # DWMWA_CAPTION_COLOR(0x00BBGGRR)
        _set(36, tr | (tg << 8) | (tb << 16))          # DWMWA_TEXT_COLOR
        if round_corners:
            _set(33, 2)                                # DWMWA_WINDOW_CORNER_PREFERENCE=圆角
        return True
    except Exception:
        return False

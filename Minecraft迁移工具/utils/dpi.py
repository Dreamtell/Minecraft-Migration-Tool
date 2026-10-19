# utils/dpi.py
"""高 DPI 支持：进程 DPI 感知 + 统一的 UI 缩放系数。

为什么需要这个模块：
- 不声明 DPI 感知时，Windows 会把整个窗口当**位图**拉伸到缩放比例（150% 下
  就是「分辨率低、发虚」）。必须在建任何窗口之前声明。
- 声明之后 Tk 按真实像素渲染，字体（磅值）会自己按 DPI 放大，但代码里写死的
  像素尺寸（窗口 1000x1080、按钮 width=150 …）不会 —— 所以需要一个统一的
  UI_SCALE 让两边同步，否则布局会被放大的字撑爆。

用法：`from utils.dpi import UI_SCALE`，所有写死的尺寸都乘它。
调试/取舍可以用环境变量 MCTOOL_UI_SCALE 强制指定（例如 1.0 / 1.25 / 1.5）。
"""

import ctypes
import os


def enable_awareness():
    """声明进程 DPI 感知。必须在任何窗口创建之前调用；重复调用无害。"""
    try:
        # Win10 1703+：per-monitor v2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PROCESS_PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()           # Vista+：系统级
        return "system"
    except Exception:
        pass
    return "none"


# 设计稿的主窗口尺寸（app.py / main_window.py 都按这个基准缩放）
DESIGN_W, DESIGN_H = 1000, 1080


def _detect_scale():
    """UI 缩放 = min(系统 DPI 比例, 屏幕能放下设计稿的比例)。

    只按 DPI 取 1.5 的话，1080*1.5=1620 比 1600 高的屏幕还大，Tk 会硬截断，
    宽高比就变了（内容是「被压扁」的观感）。所以这里用**单一系数**同时
    满足 DPI 和屏幕尺寸 —— 等比缩放，绝不各轴单独截断。
    """
    try:
        forced = float(os.environ.get("MCTOOL_UI_SCALE", "0"))
        if forced > 0:
            return forced
    except Exception:
        pass

    scale = 1.0
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)    # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi and dpi > 0:
            scale = max(1.0, dpi / 96.0)
    except Exception:
        pass

    try:
        sw = ctypes.windll.user32.GetSystemMetrics(0)       # SM_CXSCREEN
        sh = ctypes.windll.user32.GetSystemMetrics(1)       # SM_CYSCREEN
        if sw > 0 and sh > 0:
            # 留出标题栏 / 任务栏 / 一点边距
            fit = min((sw - 40) / DESIGN_W, (sh - 90) / DESIGN_H)
            scale = min(scale, fit)
    except Exception:
        pass

    # 向下取到 2 位小数，避免出现 1.4067 这种让像素尺寸出小数的值
    return max(1.0, int(scale * 100) / 100.0)


# 导入即生效：必须先声明感知，再算缩放（顺序反了 DPI 读到的就是虚拟化后的 96）
DPI_MODE = enable_awareness()
UI_SCALE = _detect_scale()


def px(value):
    """把设计稿里的像素值换算成当前缩放下的像素值（至少 1）。"""
    try:
        return max(1, int(round(float(value) * UI_SCALE)))
    except Exception:
        return int(value)


def window_geometry(win, width, height):
    """设计稿尺寸（1000x1080 那种）→ 当前缩放下的居中 geometry 字符串。

    **等比缩放**：宽高同乘 UI_SCALE，不做单轴截断 —— 单独压高度会让内容变形
    （就是「所有东西都被压得很扁」）。屏幕放不下由 _detect_scale 提前把
    UI_SCALE 降下来解决。
    """
    w = px(width)
    h = px(height)
    try:
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    except Exception:
        return "%dx%d" % (w, h)
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    return "%dx%d+%d+%d" % (w, h, x, y)

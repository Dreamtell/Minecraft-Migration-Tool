# -*- coding: utf-8 -*-
"""窗口尺寸的统一入口：给每个 Qt 窗口一个「最小尺寸 + 初始尺寸」。

为什么要统一：这些窗口以前各写各的——有的只有 `setMinimumWidth`，没有最小高度，
被拉窄之后一列控件就叠在一起；有的干脆没设初始尺寸，Qt 按 sizeHint 开出来，
联网搜索页那种顶部一排固定高度控件的窗口会被挤成一条缝。这里集中成一个函数：

- **最小尺寸** = 窗口能正常显示的下限（各窗口自己给），但不超过屏幕可用区域的 90%。
  屏幕特别小（或缩放特别大）时，宁可让窗口略小于设计值，也不能超出屏幕。
- **初始尺寸** = 想开多大（各窗口自己给），同样夹在 [最小尺寸, 屏幕 90%] 之间。

只碰尺寸，不碰样式；主进程（Tk）不 import 本模块。
"""

from PySide6 import QtWidgets

# 窗口最多占屏幕可用区域的多少：留一圈边，别顶到任务栏
SCREEN_MARGIN = 0.90


def _available(screen=None):
    """屏幕可用区域（去掉任务栏）。拿不到就返回 None（离屏/无显示器环境）。"""
    try:
        scr = screen
        if scr is None:
            app = QtWidgets.QApplication.instance()
            scr = app.primaryScreen() if app is not None else None
        if scr is None:
            return None
        rect = scr.availableGeometry()
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        return rect
    except Exception:
        return None


def fit_window(win, min_w, min_h, want_w, want_h, screen=None):
    """给窗口设最小尺寸和初始尺寸（都按屏幕可用区域夹一遍）。

    参数是**设计值**（96dpi 下的逻辑像素，Qt 会自己按缩放折算）：
    `min_w/min_h` 是内容能正常显示的下限，`want_w/want_h` 是打开时想有多大。
    返回最终生效的 (宽, 高)，方便测试断言。
    """
    min_w = max(240, int(min_w))
    min_h = max(160, int(min_h))
    want_w = max(min_w, int(want_w))
    want_h = max(min_h, int(want_h))
    av = _available(screen)
    if av is not None:
        # 上限是"屏幕的 90%"，不是"至少跟最小尺寸一样大"——屏幕真小的时候
        # 最小尺寸也得往下压，否则窗口一开就出屏、还拖不回来
        cap_w = max(240, int(av.width() * SCREEN_MARGIN))
        cap_h = max(160, int(av.height() * SCREEN_MARGIN))
        min_w, min_h = min(min_w, cap_w), min(min_h, cap_h)
        want_w, want_h = min(max(want_w, min_w), cap_w), min(max(want_h, min_h), cap_h)
    try:
        win.setMinimumSize(min_w, min_h)
        win.resize(want_w, want_h)
    except Exception:
        pass
    return want_w, want_h

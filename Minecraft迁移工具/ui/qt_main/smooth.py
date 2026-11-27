# ui/qt_main/smooth.py
"""Qt 主界面里补上"滚轮缓动"的几种控件（Tk 版 `SmoothScroller` 的对应物）。

Tk 那边是**组合**：`SmoothScroller.for_xxx(widget)` 直接给控件绑 `<MouseWheel>`，所以任何
控件一行就能挂上。Qt 这边只能继承一个混入（`ui.qt_big_view._SmoothWheel`），所以这里把
主界面要用到的几种一次性备齐，各自的「一格滚多远」照 Tk 的数值来：

| 控件 | Tk 里的一格 | 这里的类 |
|---|---|---|
| 设置窗整页（Canvas） | 48px（`for_canvas` 默认） | `SmoothScrollArea` |
| QTreeWidget（按钮树 / 文件夹树） | 3 × 行高，一行一行滑 | `SmoothTree` |
| 消息中心列表 | 60px | `SmoothList` |
| 迁移历史日志（Consolas 9） | 3 × linespace ≈ 42px | `SmoothLogText` |

用法注意（踩过）：
  · 混入写在**前面**、唯一的 Qt 类写在后面；反过来 Qt 自带的 `wheelEvent` 会先命中。
  · `QAbstractItemView` 必须 `setVerticalScrollMode(ScrollPerPixel)`，否则滚动条的单位是
    "行"，`setValue(像素)` 会退化成逐行弹。
  · `QPlainTextEdit` **不能**套这个混入（它的滚动条单位是行，按像素算会变成一次滚几十行），
    要平滑就用 `QTextEdit` 系（`SmoothTextEdit`）。
"""
from PySide6 import QtWidgets

from ui.qt_big_view import SmoothTextEdit as _BigSmoothTextEdit
from ui.qt_big_view import _SmoothWheel


class SmoothScrollArea(_SmoothWheel, QtWidgets.QScrollArea):
    """设置窗的整页滚动（Tk 是 Canvas + `for_canvas(px_per_notch=48)`）。"""

    def wheel_px(self) -> float:
        return 48.0


class SmoothTree(_SmoothWheel, QtWidgets.QTreeWidget):
    """QTreeWidget 按像素平滑（Tk 的 `for_rows`：一格 3 行，一行一行滑过去）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 不设的话滚动条单位是"行"，缓动会变成逐行弹
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)

    def wheel_px(self) -> float:
        # 空树时 sizeHintForRow(0) 返回 -1，不能写 `or 20`（-1 是真值）
        try:
            row = float(self.sizeHintForRow(0))
        except Exception:
            row = -1.0
        return 3.0 * max(20.0, row)


class SmoothList(_SmoothWheel, QtWidgets.QListWidget):
    """消息中心那种普通列表（Tk 是一格 60px）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)

    def wheel_px(self) -> float:
        return 60.0


class SmoothLogText(_BigSmoothTextEdit):
    """等宽日志框（Tk 的 `for_text`：一格 3 行）。

    和 `SmoothTextEdit` 的区别只在行高的下限：Tk 的 `_text_notch_px` 就是 3 × linespace，
    对 Consolas 9（linespace 14）是 42px；`SmoothTextEdit` 把下限抬到 16，会变成 48px。
    """

    def wheel_px(self) -> float:
        try:
            return max(12.0, float(self.fontMetrics().lineSpacing())) * 3.0
        except Exception:
            return 42.0


class StaticTextEdit(QtWidgets.QTextEdit):
    """"静态说明块"：滚轮不改变内容（Tk 里这类概要是普通 Frame，根本滚不动）。"""

    def wheelEvent(self, e):
        e.ignore()

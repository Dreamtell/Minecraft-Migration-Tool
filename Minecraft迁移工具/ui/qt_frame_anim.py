# ui/qt_frame_anim.py
"""高刷新率下也能跑满帧的补间（Qt 版 `QVariantAnimation` 的替身）。

**为什么不用 QVariantAnimation**：它内部固定按 **16ms** 推进，与屏幕刷新率无关。
本机 240Hz 屏实测 130/150/170/190/340/600ms 的动画分别只有 9/10/11/12/22/38 次
`valueChanged`（相邻帧 p50 15.98ms）。而 Tk 版所有自绘动画都走 `utils.anim.FrameLoop`，
按 `anim.frame_ms()` 排帧 —— 240Hz 下 **4ms**、约 230fps。同样的时长，Qt 只有 1/4 的
帧数，OutBack 的过冲和 OutCubic 的收尾就变成看得见的台阶。

Qt 6 已经删掉了 `QAbstractAnimation.setUpdateInterval`（实测 `dir()` 里没有任何
interval 成员），所以只能自己排帧：内部仍用 `QVariantAnimation` **算插值**（缓动曲线
仍由 `QEasingCurve` 算，结果与原来逐帧一致），但从不 `start()` 它，只按真实经过时间
`setCurrentTime()`；真正的节拍来自一个 `PreciseTimer`。

对外接口是 `QVariantAnimation` 的一个子集，把
`QtCore.QVariantAnimation(self)` 直接换成 `FrameAnim(self)` 即可，其余调用一行不用改：

    setDuration / setEasingCurve / setStartValue / setEndValue / currentValue
    start / stop / state / duration / valueChanged / finished

要注意的两点（都踩过）：
  · `valueChanged` 不能做信号对信号连接（PySide6 6.12 直接抛 RuntimeError），只能转一道；
  · 调用方会用 `state()` 判断"动画还在跑"（停止时不要去抢滚动位置），所以必须实现。

**帧间隔取 `anim.frame_ms(widget.window())`**：`utils/anim.frame_ms` 走 `winId()`，对
一个从没创建过原生窗口的子控件调它会强制它变成原生窗口，所以一律传顶层窗口。
`utils/anim.py` 只 import ctypes/time/wintypes，Qt 进程可以直接用。
"""
import time

from PySide6 import QtCore


def _frame_ms(widget):
    """这一帧应该隔多久（毫秒）。取不到就按 60Hz 兜底。"""
    try:
        from utils import anim
        win = widget.window() if widget is not None else None
        return max(1, int(anim.frame_ms(win)))
    except Exception:
        return 16


class FrameAnim(QtCore.QObject):
    """按真实时间排帧的补间。用法同 QVariantAnimation 的子集。"""

    valueChanged = QtCore.Signal(object)
    finished = QtCore.Signal()

    def __init__(self, widget):
        super().__init__(widget if isinstance(widget, QtCore.QObject) else None)
        self._w = widget
        self._calc = QtCore.QVariantAnimation(self)     # 只借它算插值，从不 start()
        self._calc.valueChanged.connect(lambda v: self.valueChanged.emit(v))
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._step)
        self._t0 = 0.0

    # ---- 与 QVariantAnimation 同名的接口 ----
    def setDuration(self, ms):
        self._calc.setDuration(max(1, int(ms)))

    def duration(self):
        return int(self._calc.duration())

    def setEasingCurve(self, curve):
        self._calc.setEasingCurve(curve)

    def easingCurve(self):
        return self._calc.easingCurve()

    def setStartValue(self, value):
        self._calc.setStartValue(value)

    def setEndValue(self, value):
        self._calc.setEndValue(value)

    def currentValue(self):
        return self._calc.currentValue()

    def state(self):
        """Running / Stopped。调用方用它判断"动画还在跑"。"""
        if self._timer.isActive():
            return QtCore.QAbstractAnimation.Running
        return QtCore.QAbstractAnimation.Stopped

    def stop(self):
        """停下，但**保留当前值**：调用方常拿 currentValue() 当下一段的起点。"""
        self._timer.stop()

    def start(self, _delete_policy=None):
        """开始。参数只为兼容旧的 `start(DeleteWhenStopped)` 调用，一律忽略。"""
        self._calc.setCurrentTime(0)
        self._timer.setInterval(_frame_ms(self._w))
        self._t0 = time.perf_counter()
        self._timer.start()
        self._step()                 # 第一帧立刻出，不留一帧的空窗

    # ---- 内部 ----
    def _step(self):
        dur = max(1, self.duration())
        elapsed = (time.perf_counter() - self._t0) * 1000.0
        done = elapsed >= dur
        self._calc.setCurrentTime(int(dur if done else elapsed))
        if not done:
            return
        self._timer.stop()           # 定时器只在动画期间存在
        self.finished.emit()         # setCurrentTime 不会发 finished，自己发

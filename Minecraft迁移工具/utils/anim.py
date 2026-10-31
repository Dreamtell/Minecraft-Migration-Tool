# utils/anim.py
"""统一的动画帧节奏：跟随显示器刷新率（上限 240Hz），必要时降帧。

## 系统定时器精度：不 raise 就快不了（这条是踩过坑的结论）

Tk 的 after() 在**真 mainloop**（阻塞在 GetMessage 里）下被 Windows 定时器粒度卡住，
默认粒度 15.6ms。实测（本机 240Hz 屏，自链定时器跑 120 帧取 p50/p90）：

    after(16)   50.0fps  p50 16.13  p90 30.45ms      <- 一半帧滑到 30ms
    after(8)    63.4fps  p50 15.87  p90 16.28ms
    after(4)    64.3fps  p50 15.89  p90 16.12ms
    after(1)   953.6fps  p50  1.04  p90  1.08ms      <- after(1) 反而是 1ms，见下
    ---- timeBeginPeriod(1) 之后 ----
    after(16)   61.4fps  p50 16.06  p90 16.98ms
    after(8)   120.3fps  p50  8.06  p90  8.98ms
    after(4)   232.5fps  p50  4.05  p90  4.98ms

⚠ 千万别在"忙泵"（一边 root.update() 一边轮询）里量这件事：泵一直烧消息队列，定时器
根本不用等，会量出 after(4)=228fps 这种假结论（本文件第一版就是这么错的）。
真机上不 raise 的话，连现在的 16ms 动画都只有 50fps。

所以**动画期间**用 hold()/release() 把精度提到 1ms，动画一停就还回去 —— 全程 raise 会让
整机空转耗电，这个项目对 CPU 很敏感。引用计数，谁先开始谁负责还。

## 其余约定

  · 整数毫秒取不到整周期：240Hz = 4.17ms，只能给 4ms（228fps，约 95%）。
  · 帧间隔只决定"多久画下一帧"；动画速度必须按真实 dt 算，否则帧率一高动作快几倍。
  · 多显示器：按窗口所在那块屏的刷新率算，拿不到退主屏，再拿不到 60。
"""

import ctypes
from ctypes import wintypes

FPS_CAP = 240          # 再高的屏也不追：单帧成本摆在那儿，追上去只会烧 CPU
FPS_SLOW = 30          # 失焦/最小化/让路时的降帧档
_MS_MIN = 4            # 240Hz -> 4ms（228fps）
_MS_MAX = 33           # 30Hz 屏也不给比 33ms 更慢的
_DEFAULT_HZ = 60

_hz_cache = {}         # 设备名 -> Hz（同一块屏只查一次）
_hz_error = False
_hr_holders = 0        # 定时器精度的引用计数


# ---------------------------------------------------------------- 定时器精度
def hold():
    """声明"接下来要跑高帧率动画"：把系统定时器精度提到 1ms（引用计数）。"""
    global _hr_holders
    _hr_holders += 1
    if _hr_holders == 1:
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass


def release():
    """结束一次 hold()。计数回零才真的还回去。多还不会把计数搞成负数。"""
    global _hr_holders
    if _hr_holders <= 0:
        return
    _hr_holders -= 1
    if _hr_holders == 0:
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass


def holding():
    """当前是否占着高精度定时器（诊断/自检用）。"""
    return _hr_holders > 0


# ---------------------------------------------------------------- 刷新率
def _primary_hz():
    try:
        gdi = ctypes.windll.gdi32
        user = ctypes.windll.user32
        hdc = user.GetDC(0)
        if not hdc:
            return 0
        try:
            return int(gdi.GetDeviceCaps(hdc, 116))     # VERTREFRESH
        finally:
            user.ReleaseDC(0, hdc)
    except Exception:
        return 0


def _device_hz(device):
    """按 \\\\.\\DISPLAYn 取刷新率（EnumDisplaySettingsW 的 dmDisplayFrequency）。"""
    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32),
            ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
            ("dmFields", wintypes.DWORD),
            ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
            ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
            ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
            ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD),
        ]
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    ENUM_CURRENT_SETTINGS = -1
    if ctypes.windll.user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return int(dm.dmDisplayFrequency)
    return 0


def _hwnd_of(win):
    """从一个窗口句柄拿到值得用的 HWND：Tk 控件、Qt 控件、裸 int 都收。

    Tk 的 winfo_id() 给的是子窗口，要 GetParent 才是顶层；Qt 的 winId() 一般已经是顶层，
    GetParent 会返回 0，下面会退回原值。拿不到就返回 0（调用方据此退回主屏）。
    """
    if win is None:
        return 0
    if isinstance(win, int):
        return win
    for getter in ("winfo_id", "winId"):
        fn = getattr(win, getter, None)
        if fn is None:
            continue
        try:
            return int(fn())
        except Exception:
            continue
    return 0


def _monitor_device(win):
    """窗口所在那块屏的设备名（\\\\.\\DISPLAYn）。拿不到返回 None。"""
    try:
        user = ctypes.windll.user32
        user.GetParent.restype = ctypes.c_void_p
        user.GetParent.argtypes = [ctypes.c_void_p]
        user.MonitorFromWindow.restype = ctypes.c_void_p
        user.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        hwnd = _hwnd_of(win)
        if not hwnd:
            return None
        hwnd = int(user.GetParent(ctypes.c_void_p(hwnd)) or hwnd)   # 顶层 hwnd
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user.MonitorFromWindow(ctypes.c_void_p(hwnd), MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return None

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32)]

        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
            return mi.szDevice or None
    except Exception:
        pass
    return None


def refresh_hz(win=None):
    """窗口所在显示器的刷新率；查不到按主屏，再不行 60。"""
    global _hz_error
    device = _monitor_device(win) if win is not None and not _hz_error else None
    if device is not None:
        hz = _hz_cache.get(device)
        if hz:
            return hz
        try:
            hz = _device_hz(device)
        except Exception:
            hz = 0
            _hz_error = True
        if hz >= 24:
            _hz_cache[device] = hz
            return hz
    hz = _hz_cache.get("")
    if hz:
        return hz
    hz = _primary_hz() or _DEFAULT_HZ
    if hz < 24:
        hz = _DEFAULT_HZ
    _hz_cache[""] = hz
    return hz


def frame_ms(win=None):
    """一帧隔多久（毫秒）。跟随刷新率，上限 FPS_CAP。"""
    hz = min(int(refresh_hz(win) or _DEFAULT_HZ), FPS_CAP)
    ms = int(round(1000.0 / float(hz)))
    return max(_MS_MIN, min(_MS_MAX, ms))


def slow_ms():
    """降帧档的帧间隔（30fps）。"""
    return int(round(1000.0 / float(FPS_SLOW)))


def is_slow(win):
    """窗口最小化 / 不是前台（或拿不到焦点）时算降帧。查不到一律当真。"""
    try:
        top = win.winfo_toplevel()
    except Exception:
        return False
    try:
        if top.state() == "iconic":
            return True
    except Exception:
        pass
    try:
        focus = top.focus_displayof()
        if focus is None and win.winfo_exists():
            return True
    except Exception:
        pass
    return False


def delay(win=None, slow=False):
    """本帧该排多久之后。

    slow=True：调用方明确要求降帧（例如正在拖浮标，让路）；
    否则只要窗口最小化/失焦就自动降到 30fps —— 高帧率只在真的看得见时才花钱。

    ⚠ 想真的排到 4ms 必须同时在动画期间 hold()，否则被 15.6ms 粒度卡回 64fps（见文件头）。
    """
    if slow or (win is not None and is_slow(win)):
        return slow_ms()
    return frame_ms(win)


class FrameLoop:
    """按显示器刷新率排帧的小循环：**自动配平 hold/release**。

    为什么要有它：项目里已经踩过三次"想跑满帧但忘了 hold()"（消息中心滚动、
    吐司、日志跟随），每次都表现为"看着能跑，但被 15.6ms 粒度卡在 64fps"。
    统一封一层，以后新写动画直接用它，不用再各记各的规矩。

    用法：
        环 = FrameLoop(widget, 每帧函数)   # 每帧函数返回 False 表示这一轮结束
        环.start()
        环.stop()          # 也可以随时手动停（内部会 release）

    细节：
    - 每帧之间按 `delay(widget)` 排期；`slow=True` 时固定用 30fps 档；
    - 每帧函数抛异常 → 自动 `stop()`，不会留下"精度被顶住但没人还"的状态；
    - `start()` 可以重复调用：已经在跑就什么都不做。
    """

    def __init__(self, widget, step, slow=False):
        self.widget = widget
        self.step = step
        self.slow = slow
        self._job = None
        self._held = False
        self.帧数 = 0

    # ---- 内部 ----
    def _持(self):
        if not self._held:
            hold()
            self._held = True

    def _还(self):
        if self._held:
            release()
            self._held = False

    def _排(self, ms=None):
        try:
            if ms is None:
                ms = delay(self.widget, slow=self.slow)
            self._job = self.widget.after(max(1, int(ms)), self._走)
        except Exception:
            self._job = None
            self._还()

    def _走(self):
        self._job = None
        try:
            继续 = self.step()
        except Exception:
            继续 = False
        self.帧数 += 1
        if 继续 is False:
            self._还()
            return
        self._排()

    # ---- 对外 ----
    def running(self):
        return self._job is not None

    def start(self):
        if self._job is not None:
            return self
        self.帧数 = 0
        self._持()
        self._排(1)
        return self

    def stop(self):
        job, self._job = self._job, None
        if job is not None:
            try:
                self.widget.after_cancel(job)
            except Exception:
                pass
        self._还()
        return self

# utils/netmeter.py
"""本工具的联网流量计数（上行 / 下行 / 累计）。

只统计**我们自己的 HTTP 请求**：在请求发出前和响应读完后各报一次字节数。这比读系统
计数器（psutil / 网卡）更准也更有意义 —— 用户关心的是"这个工具用了多少流量"，而不是
整台机器的流量；而且不依赖任何额外库。

计数逻辑本身在子线程里被调用（搜索/取版本跑在线程池），所以所有读写都加锁。
"""

import threading
import time

_lock = threading.Lock()
_state = {"sent": 0, "recv": 0}


def add_sent(n):
    """记一次上行字节数（请求行 + 请求头 + 请求体）。"""
    try:
        n = int(n)
    except Exception:
        return
    if n <= 0:
        return
    with _lock:
        _state["sent"] += n


def add_recv(n):
    """记一次下行字节数（响应体）。"""
    try:
        n = int(n)
    except Exception:
        return
    if n <= 0:
        return
    with _lock:
        _state["recv"] += n


def snapshot():
    """当前累计值：(sent, recv)。"""
    with _lock:
        return _state["sent"], _state["recv"]


def reset():
    with _lock:
        _state["sent"] = 0
        _state["recv"] = 0


def format_bytes(n):
    """1.2 KB / 3.4 MB 这种可读写法。"""
    try:
        n = float(n)
    except Exception:
        return "0 B"
    if n < 1024:
        return "%d B" % int(n)
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    if n < 1024 * 1024 * 1024:
        return "%.1f MB" % (n / (1024.0 * 1024))
    return "%.2f GB" % (n / (1024.0 * 1024 * 1024))


def format_rate(bps):
    """字节/秒 → '12.3 KB/s'。"""
    return format_bytes(bps) + "/s"


class Sampler:
    """按固定间隔采样，算出当前速率。

    用法：每 1 秒调一次 `tick()`，它会返回 (上行速率, 下行速率, 累计上行, 累计下行)。
    """

    def __init__(self):
        self._last_t = time.monotonic()
        self._last = snapshot()

    def tick(self):
        现在 = time.monotonic()
        dt = max(0.001, 现在 - self._last_t)
        上, 下 = snapshot()
        上速 = max(0.0, (上 - self._last[0]) / dt)
        下速 = max(0.0, (下 - self._last[1]) / dt)
        self._last_t = 现在
        self._last = (上, 下)
        return 上速, 下速, 上, 下

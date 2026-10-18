# utils/netmeter.py
"""网络流量统计（上行 / 下行 / 累计），给主界面左下角用。

**这是整机的网络流量**，不是"本工具自己"的 —— 原因有三：
  · 本工具的网络请求发生在 **Qt 子进程**里，父进程读不到它的内存计数；
  · 想在两个进程之间汇总只能写文件，实测很难稳定（改了几版都没跑通）；
  · `psutil` 从 6.0 起**移除了按进程的网络统计**（`Process.net_io_counters` 已删除）。

所以用 `psutil.net_io_counters()`（系统级），效果和任务管理器 / 那些网络监控小工具一致，
"网络有没有在跑、跑了多少"这个目的完全满足。tooltip 里写明了范围。

psutil 不可用时 `snapshot()` 返回 None，界面就不显示流量 —— 而不是显示一堆 0 让人
以为坏了。计数本身仍在 `add_sent/add_recv` 里做（保留给需要的调用方，不影响显示）。
"""

import threading
import time

try:
    import psutil
except Exception:                                    # pragma: no cover
    psutil = None

_lock = threading.Lock()
_state = {"sent": 0, "recv": 0, "requests": 0}


def available():
    return psutil is not None


# ---------- 本进程的 HTTP 计数（保留：某些地方想知道"我们发了多少"） ----------
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


def own_snapshot():
    """本进程自己发起的 HTTP 字节数 (sent, recv)（只算走 mod_search 的那些）。"""
    with _lock:
        return _state["sent"], _state["recv"]


def add_request():
    """记一次 HTTP 请求（不管成功失败都算一次尝试）。"""
    with _lock:
        _state["requests"] += 1


def own_requests():
    """本进程自己发起的 HTTP 请求次数。"""
    with _lock:
        return _state["requests"]


# ---------- 整机流量（界面显示用的就是这个） ----------
def snapshot():
    """当前累计 (sent, recv) 字节；psutil 不可用时返回 None。"""
    if psutil is None:
        return None
    try:
        n = psutil.net_io_counters()
        return int(n.bytes_sent), int(n.bytes_recv)
    except Exception:
        return None


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
    """按固定间隔采样，算出当前速率。"""

    def __init__(self):
        self._last_t = time.monotonic()
        self._last = snapshot()

    def tick(self):
        """返回 (上行速率, 下行速率, 累计上行, 累计下行)；psutil 不可用时返回 None。"""
        现在 = time.monotonic()
        dt = max(0.001, 现在 - self._last_t)
        当前 = snapshot()
        self._last_t = 现在
        if 当前 is None:
            return None
        if self._last is None:
            self._last = 当前
            return 0.0, 0.0, 当前[0], 当前[1]
        上速 = max(0.0, (当前[0] - self._last[0]) / dt)
        下速 = max(0.0, (当前[1] - self._last[1]) / dt)
        self._last = 当前
        return 上速, 下速, 当前[0], 当前[1]


# ---------------- 历史事件（给折线图用） ----------------
# 每次 HTTP 请求都记一条 (时间戳, 上行字节, 下行字节)。总量很小（一次搜索才 1 条、
# 几十字节的元信息），保留最近 500 条够画很久了。
import collections

_events = collections.deque(maxlen=500)


def add_event(sent, recv):
    try:
        _events.append((time.time(), int(sent or 0), int(recv or 0)))
    except Exception:
        pass


def events():
    """最近的事件列表 [(t, sent, recv), ...]（时间升序）。"""
    with _lock:
        return list(_events)


def buckets(window_sec=60.0, bucket_sec=2.0):
    """把最近 window_sec 秒的流量按 bucket_sec 分桶。

    返回 (labels, sent_list, recv_list)：每个桶的距现在多少秒和该桶的上/下行字节。
    没有事件时返回等长的全零 —— 这样折线图能画出一条贴着底的线，而不是空图。
    """
    现在 = time.time()
    起 = 现在 - window_sec
    桶数 = max(1, int(round(window_sec / bucket_sec)))
    上 = [0] * 桶数
    下 = [0] * 桶数
    with _lock:
        for t, s, r in _events:
            if t < 起:
                continue
            位 = min(桶数 - 1, int((t - 起) / bucket_sec))
            上[位] += s
            下[位] += r
    return list(range(桶数)), 上, 下

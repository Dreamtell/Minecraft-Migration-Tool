# utils/netmeter.py
"""本工具**自己**的 HTTP 用量统计（请求次数 / 上行字节 / 下行字节）。

只统计 `core/mod_search.py` 真正发出的请求 —— 不统计整机流量。
整机那套（`psutil.net_io_counters()`）已经删掉了，原因：
  · 显示的是浏览器、系统更新、其它程序的流量，跟本工具没关系，纯误导；
  · 每次搜索才 ~12 KB，看在"整机流量"里根本分辨不出来；
  · 还得多带一个 psutil 编译依赖进打包。

现在能回答的问题是"这个工具自己用了多少"，而且问得有意义：
  · 搜索窗状态行：「本次查询 3 次请求 / 36 KB」；
  · Qt 大视图：「网络用量（本工具）」折线图。

一次搜索的真实量级（实测）：Modrinth 8 条结果 ≈ 12.4 KB，
单个项目详情 ≈ 1.2 KB，分类查询 ≈ 7–9 KB 且**有磁盘缓存**。
"""

import collections
import threading
import time

_lock = threading.Lock()
_state = {"sent": 0, "recv": 0, "requests": 0}


# ---------- 计数 ----------
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


def add_request():
    """记一次 HTTP 请求（不管成功失败都算一次尝试）。"""
    with _lock:
        _state["requests"] += 1


def own_snapshot():
    """本进程自己发起的 HTTP 字节数 (sent, recv)。"""
    with _lock:
        return _state["sent"], _state["recv"]


def own_requests():
    """本进程自己发起的 HTTP 请求次数。"""
    with _lock:
        return _state["requests"]


# ---------- 格式化 ----------
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


# ---------------- 历史事件（给折线图用） ----------------
# 每次 HTTP 请求记一条 (时间戳, 上行字节, 下行字节)。请求很少，500 条够画很久。
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

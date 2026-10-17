# utils/netmeter.py
"""本工具的联网流量计数（上行 / 下行 / 累计）。

只统计**我们自己的 HTTP 请求**：在请求发出前和响应读完后各报一次字节数。这比读系统
计数器（psutil / 网卡）更准也更有意义 —— 用户关心的是"这个工具用了多少流量"，而不是
整台机器的流量；而且不依赖任何额外库。

计数逻辑本身在子线程里被调用（搜索/取版本跑在线程池），所以所有读写都加锁。
"""

import threading
import time
from pathlib import Path

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


# ---------------- 跨进程汇总 ----------------
# 项目架构：Qt 窗口跑在**子进程**（app.py --qt-host），网络请求发生在那边。
# 父进程（主界面）读不到子进程内存里的计数器 —— 所以各自把自己的累计值写到一个
# 小文件里，谁要显示就汇总读全部。按文件修改时间过滤掉已经退出的进程（不留垃圾）。

import json
import os

_DIR = Path(os.environ.get("MCTOOL_NETIO_DIR")
            or (Path.home() / ".minecraft_migrate_netio"))
_STALE_SEC = 10.0


def publish():
    """把本进程的累计值写到自己 pid 的文件里（建议每 2 秒调一次）。"""
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        上, 下 = snapshot()
        (_DIR / ("%d.json" % os.getpid())).write_text(
            json.dumps({"pid": os.getpid(), "sent": 上, "recv": 下}),
            encoding="utf-8")
    except Exception:
        pass


def read_all():
    """汇总所有**还活着**的进程上报的 (sent, recv)。

    用修改时间判断存活：超过 10 秒没更新的视为已退出（publish 是每 2 秒一次的）。
    """
    总上 = 总下 = 0
    try:
        if not _DIR.is_dir():
            return 0, 0
        现在 = time.time()
        for f in _DIR.glob("*.json"):
            try:
                if 现在 - f.stat().st_mtime > _STALE_SEC:
                    continue
                d = json.loads(f.read_text(encoding="utf-8"))
                总上 += int(d.get("sent") or 0)
                总下 += int(d.get("recv") or 0)
            except Exception:
                continue
    except Exception:
        pass
    return 总上, 总下


def cleanup():
    """退出时删掉自己的文件。"""
    try:
        (_DIR / ("%d.json" % os.getpid())).unlink()
    except Exception:
        pass


_pub_stop = None


def start_publisher(interval_sec=2.0):
    """起一个后台线程定期 publish()。

    父子进程**都要起**：Qt 窗口是子进程，请求在那边发生；主界面是父进程，显示在这边。
    """
    global _pub_stop
    if _pub_stop is not None:
        return
    _pub_stop = threading.Event()

    def 循环():
        while not _pub_stop.is_set():
            publish()
            _pub_stop.wait(interval_sec)

    threading.Thread(target=循环, name="netio-pub", daemon=True).start()

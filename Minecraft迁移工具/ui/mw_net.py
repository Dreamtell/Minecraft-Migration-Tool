# ui/mw_net.py
"""主界面右下角的网络状态灯。

只做**TCP 连通性探测**（连 443 端口），不调任何 API —— 不消耗 CurseForge / Modrinth
的请求配额，也不需要 Key 就能判断"网络通不通"。

检测在**子线程**里跑（DNS + 连接可能要几秒），结果回到主线程只改一行字 —— 绝不阻塞界面。
"""

import collections
import queue
import socket
import threading
import time

# 两个数据源的主机名
_HOSTS = (("Modrinth", "api.modrinth.com"),
          ("CurseForge", "api.curseforge.com"))

_TIMEOUT = 3.0
# 检测间隔：别太勤（用户可能挂着窗口不动），也别太懒（断网了要能看出来）
# 5 秒一次：左下角那个迷你折线图要实时，30 秒一个点根本看不出在动。
# 代价只是每 5 秒两次 TCP 连接（不发请求、不消耗 API 配额），可以接受。
INTERVAL_MS = 5000


# ---------------- 探测延迟历史（给左下角迷你折线图用） ----------------
# 只记连通与否和连接耗时(ms)。放在父进程里 —— 网络灯本来就在这边跑，
# 所以迷你图不需要跨进程拿数据（流量数字在 Qt 子进程里，父进程读不到）。

# 只留 2 分钟（5 秒一次 → 24 条）。每条形如 (时间戳, {"Modrinth": 45 或 None, ...})
# —— **按平台分开存**，曲线图才能给两个平台各画一条线。
_rtt = collections.deque(maxlen=24)


def hosts():
    """平台名列表，顺序固定（决定曲线图里线/颜色的顺序）。"""
    return tuple(名 for 名, _ in _HOSTS)


def record(结果):
    """记一次探测：{'Modrinth': 45, 'CurseForge': None, ...}（None = 这次没连上）。"""
    try:
        _rtt.append((time.time(), dict(结果)))
    except Exception:
        pass


def rtt_history():
    """兼容旧接口：[(时间戳, 最慢的那个平台的耗时, 连通数, 总数), ...]。

    浮标和左下角迷你图用的就是这个"整体"口径。
    """
    出 = []
    for t, d in _rtt:
        值 = [v for v in d.values() if v is not None]
        出.append((t, max(值) if 值 else 0, len(值), len(d)))
    return 出


def rtt_series():
    """按平台分开的样本：{平台名: [(时间戳, 耗时ms), ...]}，只含连上的那些。"""
    出 = {名: [] for 名, _ in _HOSTS}
    for t, d in _rtt:
        for 名, v in d.items():
            if v is not None:
                出.setdefault(名, []).append((t, v))
    return 出


def probe(timeout=_TIMEOUT):
    """返回 {主机名: 连接耗时ms 或 None}。纯 TCP 连接测试，不发送任何请求。

    **每个平台单独计时**（以前记的是两次连接的合计），这样两条线才有意义。
    """
    结果 = {}
    for 名, 主机 in _HOSTS:
        t0 = time.perf_counter()
        try:
            with socket.create_connection((主机, 443), timeout=timeout):
                结果[名] = max(1, int((time.perf_counter() - t0) * 1000))
        except Exception:
            结果[名] = None
    return 结果


def summary(结果):
    """把探测结果变成一句话 + 颜色档位（ok / warn / fail）。"""
    通 = [k for k, v in 结果.items() if v]
    断 = [k for k, v in 结果.items() if not v]
    if len(通) == len(结果) and 结果:
        return "🌐 网络：通畅", "ok"
    if 通:
        return "🌐 网络：%s 不通" % "、".join(断), "warn"
    return "🌐 网络：不通", "fail"


def start_polling(root, on_result, interval_ms=INTERVAL_MS):
    """开始定期探测。

    on_result(文字, 档位, 明细) 会在**主线程**被调用。
    返回一个 stop() 函数。

    ⚠ 子线程**不能直接调 root.after** —— Tk 不是线程安全的，跨线程排 after 会静默失败
    （第一版就是这么写的，界面永远停在"检测中…"）。改用队列：子线程只往队列里放结果，
    主线程用 after 轮询取（和搜索窗同一套做法）。
    """
    停止 = threading.Event()
    结果队列 = queue.Queue()

    def 循环():
        while not 停止.is_set():
            try:
                r = probe()
                record(r)
                文字, 档位 = summary(r)
            except Exception:
                r, 文字, 档位 = {}, "🌐 网络：检测失败", "warn"
            结果队列.put((文字, 档位, r))
            停止.wait(interval_ms / 1000.0)

    threading.Thread(target=循环, name="netprobe", daemon=True).start()

    def 主线程轮询():
        try:
            while True:
                文字, 档位, r = 结果队列.get_nowait()
                try:
                    on_result(文字, 档位, r)
                except Exception:
                    pass
        except queue.Empty:
            pass
        if 停止.is_set():
            return
        try:
            root.after(400, 主线程轮询)
        except Exception:
            pass

    try:
        root.after(400, 主线程轮询)
    except Exception:
        pass

    def stop():
        停止.set()

    return stop

# ui/mw_net.py
"""主界面右下角的网络状态灯。

只做**TCP 连通性探测**（连 443 端口），不调任何 API —— 不消耗 CurseForge / Modrinth
的请求配额，也不需要 Key 就能判断"网络通不通"。

检测在**子线程**里跑（DNS + 连接可能要几秒），结果回到主线程只改一行字 —— 绝不阻塞界面。
"""

import queue
import socket
import threading

# 两个数据源的主机名
_HOSTS = (("Modrinth", "api.modrinth.com"),
          ("CurseForge", "api.curseforge.com"))

_TIMEOUT = 3.0
# 检测间隔：别太勤（用户可能挂着窗口不动），也别太懒（断网了要能看出来）
INTERVAL_MS = 30000


def probe(timeout=_TIMEOUT):
    """返回 {主机名: 是否连通}。纯 TCP 连接测试，不发送任何请求。"""
    结果 = {}
    for 名, 主机 in _HOSTS:
        try:
            with socket.create_connection((主机, 443), timeout=timeout):
                结果[名] = True
        except Exception:
            结果[名] = False
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

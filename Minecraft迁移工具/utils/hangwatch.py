# utils/hangwatch.py
"""主线程看门狗：界面被卡住时，把"卡在哪一行"记下来。

为什么需要它：用户报"迁移期间点几下鼠标就弹出未响应"，但从外部只能看到现象 ——
看不到主线程当时在跑哪段代码。事后靠猜已经浪费了好几轮排查，所以直接把证据留下来。

做法很轻：每 100ms 打一次心跳；两次心跳间隔超过阈值（默认 1 秒）就说明主线程被某个
调用占住了。这时用 `sys._current_frames()` 抓下**所有线程**的调用栈（不用 faulthandler，
它只能写 stderr），连同时间戳和间隔写进 ~/.minecraft_migrate_hang.log。

平时开销就是每 100ms 一次 `time.monotonic()`，可以忽略；文件只在真的卡了时才写。
如果怀疑卡顿，让用户复现一次，把那个文件发过来即可。
"""

import os
import sys
import time
import traceback
from pathlib import Path

# 超过这个秒数就记一笔。设 1 秒：Windows 判定"未响应"大约是 5 秒，留足余量，
# 又能在真正卡死之前就抓到线索。
THRESHOLD_SEC = 1.0
TICK_MS = 100

LOG_FILE = Path(os.environ.get("MCTOOL_HANG_LOG")
                or (Path.home() / ".minecraft_migrate_hang.log"))

_state = {"last": 0.0, "job": None, "root": None}
# 记过的次数上限，避免一次长卡之后刷屏（每 5 秒最多再记一次）
_last_written = [0.0]


def _write(间隔, root):
    try:
        现在 = time.monotonic()
        if 现在 - _last_written[0] < 5.0:
            return
        _last_written[0] = 现在
        行 = ["", "=" * 72,
              "主线程卡住 %.2f 秒 ｜ %s" % (间隔, time.strftime("%Y-%m-%d %H:%M:%S")),
              "=" * 72]
        try:
            窗口标题 = root.title() if root is not None else ""
            行.append("窗口标题：%s" % 窗口标题)
        except Exception:
            pass
        帧 = sys._current_frames()
        for 线程号, 栈 in 帧.items():
            行.append("")
            行.append("--- 线程 %s ---" % 线程号)
            行.extend("".join(traceback.format_stack(栈)).rstrip().splitlines())
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(行) + "\n")
    except Exception:
        pass


def start(root, threshold=THRESHOLD_SEC, tick_ms=TICK_MS):
    """挂上心跳。root 是主窗口（只用来写窗口标题，方便对上用户当时的界面）。"""
    _state["root"] = root
    _state["last"] = time.monotonic()

    def tick():
        try:
            now = time.monotonic()
            间隔 = now - _state["last"]
            _state["last"] = now
            if 间隔 >= threshold:
                _write(间隔, _state["root"])
        except Exception:
            pass
        try:
            _state["job"] = root.after(tick_ms, tick)
        except Exception:
            _state["job"] = None

    try:
        _state["job"] = root.after(tick_ms, tick)
    except Exception:
        return None
    return _state["job"]


def stop():
    job = _state.get("job")
    root = _state.get("root")
    if job is not None and root is not None:
        try:
            root.after_cancel(job)
        except Exception:
            pass
    _state["job"] = None

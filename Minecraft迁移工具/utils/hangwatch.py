# utils/hangwatch.py
"""主线程看门狗：界面被卡住时，把"卡在哪一行"记下来。

为什么需要它：用户报"迁移期间点几下鼠标就弹出未响应"，但从外部只能看到现象 ——
看不到主线程当时在跑哪段代码。事后靠猜已经浪费了好几轮排查，所以直接把证据留下来。

设计（踩过两次坑才定下来）：

  · **心跳在主线程**，但它只做一件事：更新一个时间戳。极轻，不会自己变成负担。
  · **检测在独立线程**，每 0.5 秒看一眼时间戳。主线程被卡住时这个线程照样能跑 ——
    这正是能抓到现场的前提。
  · 抓到超时后调 `faulthandler.dump_traceback(all_threads=True)`：它是为"异步转储
    所有线程栈"设计的，跨线程安全，能把**正卡着的主线程**那一段栈完整写下来。

⚠ 两个已经翻车的版本，别再走回头路：
  1. 用 `traceback.format_stack(sys._current_frames()[...])` 抓**别的线程**的 frame ——
     在 3.12 上访问活动 frame 会阻塞主线程，看门狗自己成了卡顿源，日志里全是它自己；
  2. 把 dump 放在**心跳回调**里 —— 那样 dump 出来的栈永远是"心跳自己"，看不到用户卡在
     哪里（检测必须来自另一个线程才有意义）。

平时开销：主线程每 100ms 一次 `time.monotonic()`，外加一个休眠线程。不卡就不写文件。
"""

import faulthandler
import os
import threading
import time
from pathlib import Path

# 超过这个秒数就记一笔。默认 0.4 秒：Windows 判定"未响应"是 5 秒，但用户嘴里的
# "卡"往往只有两三百毫秒的连击 —— 1 秒阈值会把这类全放过（用户报卡、日志里却一条
# 都没有）。想更安静就设环境变量 MCTOOL_STALL_S，比如 1.0。
THRESHOLD_SEC = float(os.environ.get("MCTOOL_STALL_S") or 0.4)
TICK_MS = 100
CHECK_SEC = 0.5

LOG_FILE = Path(os.environ.get("MCTOOL_HANG_LOG")
                or (Path.home() / ".minecraft_migrate_hang.log"))

_state = {"beat": 0.0, "root": None, "stop": False, "writing": False,
          "thread": None, "job": None}
# 同一轮长卡别刷屏：每 5 秒最多记一次
_last_written = [0.0]


def _write(间隔):
    if _state.get("writing"):
        return
    _state["writing"] = True
    try:
        现在 = time.monotonic()
        if 现在 - _last_written[0] < 5.0:
            return
        _last_written[0] = 现在
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 72 + "\n")
            f.write("主线程卡住 %.2f 秒 ｜ %s\n"
                    % (间隔, time.strftime("%Y-%m-%d %H:%M:%S")))
            try:
                f.write("窗口标题：%s\n" % _state.get("title", ""))
            except Exception:
                pass
            f.write("=" * 72 + "\n")
            f.flush()
        # ⚠ 给 faulthandler **单独开一个二进制句柄**：它绕过 Python 的缓冲直接往 fd 上写
        # **字节**。复用上面那个文本模式、带编码包装的对象会写不进去（实测既不报错、也没有
        # 栈输出，日志里只剩一行"主线程卡住 N 秒"）；文本模式在 Windows 上还有换行转换，
        # fd 位置和 Python 缓冲也不同步。
        with open(LOG_FILE, "ab") as f2:
            faulthandler.dump_traceback(file=f2, all_threads=True)
    except Exception:
        pass
    finally:
        _state["writing"] = False


def _构建指纹():
    """把几个关键文件的"大小+mtime"拼成一行写进日志开头。

    用来确认"用户跑的是不是最新一版"：踩过 —— 改完让他重启，他说还是卡，
    而日志里那条栈的行号还是旧代码的，分不清是"没生效"还是"没修好"。
    """
    本 = Path(__file__).resolve().parent.parent
    份 = []
    for 名 in ("ui/mw_log.py", "ui/mw_migration.py", "ui/mw_lock.py", "ui/mw_common.py"):
        try:
            统 = (本 / 名).stat()
            份.append("%s=%d/%d" % (Path(名).name, 统.st_size, int(统.st_mtime)))
        except Exception:
            份.append("%s=?" % Path(名).name)
    return " ".join(份)


def start(root, threshold=THRESHOLD_SEC, tick_ms=TICK_MS, check_sec=CHECK_SEC):
    """挂上心跳。root 是主窗口（只用来写窗口标题，方便对上用户当时的界面）。"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as _f:
            _f.write("\n---- 启动 %s ｜ 构建 %s ----\n"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"), _构建指纹()))
    except Exception:
        pass
    _state["root"] = root
    _state["beat"] = time.monotonic()
    _state["stop"] = False

    def tick():                                     # 主线程：只更新时间戳
        try:
            _state["beat"] = time.monotonic()
        except Exception:
            pass
        # 顺便缓存窗口标题：检测线程**绝不能碰 Tk**（那不是线程安全的调用，
        # 实测会抛异常、把后面的栈 dump 一起带掉，日志里只剩"卡住 N 秒"一行）。
        try:
            n = _state.get("n", 0) + 1
            _state["n"] = n
            if n % 5 == 1 or not _state.get("title"):
                _state["title"] = root.title()
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

    def watch():                                    # 独立线程：发现超时就留证据
        while not _state.get("stop"):
            time.sleep(check_sec)
            try:
                间隔 = time.monotonic() - _state["beat"]
                if 间隔 >= threshold:
                    _write(间隔)
            except Exception:
                pass

    t = threading.Thread(target=watch, name="hangwatch", daemon=True)
    _state["thread"] = t
    t.start()
    return t


def stop():
    _state["stop"] = True
    root = _state.get("root")
    job = _state.get("job")
    if job is not None and root is not None:
        try:
            root.after_cancel(job)
        except Exception:
            pass
    _state["job"] = None

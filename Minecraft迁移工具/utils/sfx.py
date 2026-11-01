# -*- coding: utf-8 -*-
"""按钮音效：用 Windows 自带的 MCI 直接播 mp3（零依赖，不打包任何音频）。

音源是**用户本机**的 CS2 音效文件（从 ExLoader 的 flutter_assets 里来的那两个），
所以：
- 路径只写在配置里（`sfx_hover` / `sfx_press`），**不进仓库、不进发布包**；
- 找不到文件就静默不响，功能不受影响。

实现要点（都是实测踩出来的，见 `_dctest` 里的播放探针）：
- `mciSendStringW` 打开一次成本 13~74ms → 启动时**预热**，之后只发 `play ... from 0`；
- 单次 `play` 调用要 20ms 左右且是同步的 → 全部丢到一个工作线程的队列里，
  绝不能在 Tk 主线程直接发；
- MCI 没有单条音量，只有系统音量；
- hover 音 290ms，鼠标扫过一排按钮会连成串 → 用**节流**（同一类音效在 `_最短间隔`
  内不重复），而不是截断，这样音色保持原样。
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

_别名 = {"hover": "mct_hover", "press": "mct_press"}
_文件 = {"hover": "", "press": ""}
_已开 = set()
_队列: "queue.Queue" = queue.Queue(maxsize=8)
_线程 = None
_锁 = threading.Lock()
_线程中 = threading.Event()
_开 = False
_最短间隔 = {"hover": 0.14, "press": 0.06}
_上次 = {"hover": 0.0, "press": 0.0}


def 内置目录():
    """随包自带的音效目录（assets/sfx）：开发态在产品目录，打包态在 _internal 里。"""
    import sys as _sys
    根 = getattr(_sys, "_MEIPASS", None)
    if 根:
        return Path(根) / "assets" / "sfx"
    return Path(__file__).resolve().parent.parent / "assets" / "sfx"


def 内置音效(种类):
    """返回内置音效路径（没有就返回空串）。"""
    路 = 内置目录() / ("%s.wav" % ("hover" if 种类 == "hover" else "press"))
    return str(路) if 路.is_file() else ""


def _实际路径(种类):
    """配置里指定了就用手填的；没填（或文件没了）就用内置的。"""
    自定 = _文件.get(种类) or ""
    if 自定 and Path(自定).is_file():
        return 自定
    return 内置音效(种类)


def _mci():
    import ctypes
    from ctypes import wintypes
    try:
        winmm = ctypes.WinDLL("winmm")
    except Exception:
        return None
    发 = winmm.mciSendStringW
    发.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, ctypes.c_void_p]
    发.restype = wintypes.UINT
    return 发


def _发(命令):
    发 = _mci()
    if 发 is None:
        return -1
    return 发(命令, None, 0, None)


def 可用():
    """能不能真的响：开关打开 + 至少有一个音源文件（自定或内置）。"""
    if not _开:
        return False
    return any(_实际路径(类) for 类 in ("hover", "press"))


def 设置(enabled=None, hover=None, press=None):
    """配置变更时调：开关与两个音源路径。"""
    global _开
    if enabled is not None:
        _开 = bool(enabled)
    if hover is not None:
        _文件["hover"] = str(hover or "")
        _已开.discard("hover")
    if press is not None:
        _文件["press"] = str(press or "")
        _已开.discard("press")


def 预热():
    """后台把两个文件打开好；之后 play 只要 20ms（此时也没有冷启动卡顿）。"""
    if not 可用():
        return
    _起线程()
    for 类 in ("hover", "press"):
        _队列.put(("开", 类))


def play(种类="press"):
    """播一下。在主线程调用是安全的（只入队，立刻返回）。"""
    if not 可用():
        return
    现在 = time.perf_counter()
    if 现在 - _上次.get(种类, 0.0) < _最短间隔.get(种类, 0.1):
        return                      # 扫过去不连成串
    _上次[种类] = 现在
    try:
        _队列.put_nowait(("播", 种类))
    except Exception:
        pass
    _起线程()


def 停止():
    for 类, 别 in _别名.items():
        if 类 in _已开:
            _发("stop %s" % 别)


def _起线程():
    global _线程
    with _锁:
        if _线程 is not None and _线程.is_alive():
            return
        _线程 = threading.Thread(target=_跑, name="sfx", daemon=True)
        _线程.start()


def _开一个(类):
    if 类 in _已开:
        return True
    路径 = _实际路径(类)
    if not 路径 or not Path(路径).is_file():
        return False
    别 = _别名[类]
    _发("close %s" % 别)
    码 = _发('open "%s" type mpegvideo alias %s' % (路径, 别))
    if 码 == 0:
        _已开.add(类)
        return True
    return False


def _跑():
    线程中 = True
    while 线程中:
        try:
            动作, 类 = _队列.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if 动作 == "开":
                _开一个(类)
            elif 动作 == "播":
                if _开一个(类):
                    _发("play %s from 0" % _别名[类])
        except Exception:
            pass

# -*- coding: utf-8 -*-
"""测试基建：把 MCTOOL_CONFIG 指到临时文件，并让**已经导入**的模块也认这个新路径。

背景（为什么需要它）：
    utils/config.py 里的 CONFIG_FILE 是模块级常量，只在**第一次 import** 时按
    环境变量算一次：

        CONFIG_FILE = Path(os.environ.get("MCTOOL_CONFIG") or Path.home() / "...")

    Python 里模块只 import 一次，所以全量跑测试时，"谁先 import 谁设的临时
    配置就定下来了"；后面的测试再改 os.environ 也不重算，于是读到的还是前一个
    测试的文件 —— 单独跑通过、全量跑失败，就是这么来的。

    另外不少模块是 `from utils.config import CONFIG_FILE`（导入时把值拷走），
    光改 utils.config 里那个名字也不够。

用法（在每个测试模块的 env/sys.path 设置之后、导入产品代码之前）：

    from _testcfg import use_temp_config
    CFG = use_temp_config("mctool-xxx-")     # 或直接传一个 Path

之后本进程里所有读 CONFIG_FILE 的模块都指向这个临时文件。
"""

import os
import sys
import tempfile
from pathlib import Path

_已用目录 = []          # 保住 TemporaryDirectory，别被 GC 提前清掉


def use_temp_config(prefix="mctool-test-", path=None):
    """返回一个新的临时配置路径，并把整个进程里的 CONFIG_FILE 统一到它。"""
    if path is None:
        tmp = tempfile.TemporaryDirectory(prefix=prefix)
        _已用目录.append(tmp)
        path = Path(tmp.name) / "config.json"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('{"language": "zh"}', encoding="utf-8")
    return _apply(path)


def use_config(path):
    """同 use_temp_config，但用调用方给的具体路径。"""
    return use_temp_config(path=path)


def _apply(path):
    os.environ["MCTOOL_CONFIG"] = str(path)
    try:
        from utils import config as _cfg
    except Exception:                 # 产品目录还没进 sys.path
        return path
    # 现在 utils.config.CONFIG_FILE 是"跟着环境变量走"的代理对象，
    # 这里把它还原成代理（有些测试会把它赋成具体 Path），并让各模块共用同一个。
    try:
        代理 = _cfg._ConfigPath()
    except Exception:
        代理 = path
    _cfg.CONFIG_FILE = 代理
    for mod in list(sys.modules.values()):
        if mod is _cfg:
            continue
        try:
            当前 = getattr(mod, "CONFIG_FILE", None)
            if 当前 is None:
                continue
            if isinstance(当前, (str, Path)) or type(当前).__name__ == "_ConfigPath":
                setattr(mod, "CONFIG_FILE", 代理)
        except Exception:
            pass
    return path


def reset_tk_default_root():
    """把 tkinter 的 default root 清掉。

    有些测试用例自己 destroy 了 Tk()，但 tkinter._default_root 还指着那个死掉的
    解释器；后面再 new Font()/用默认 root 就会报
    `TclError: can't invoke "font" command: application has been destroyed`。
    """
    try:
        import tkinter
        root = getattr(tkinter, "_default_root", None)
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        tkinter._default_root = None
    except Exception:
        pass

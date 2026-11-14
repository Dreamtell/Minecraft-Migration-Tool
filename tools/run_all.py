# -*- coding: utf-8 -*-
"""全量测试跑法：**每个测试文件开一个独立进程**。

为什么不用 `python -m unittest discover` 一把梭：
    好几个 Qt 测试文件各自建 `QApplication`、各建主窗，还 monkeypatch 全局模块
    （`utils.sfx`、`utils.config`、`ui.qt_main.window` 的类属性）。同进程里跑，
    前一个文件留下的窗口/timer/补丁会漏到后面，表现是"单跑通过、全量随机挂"，
    而且每次挂的用例都不太一样。这类问题逐个修要改测试架构，收益不大；
    分进程跑既隔离又简单，CI 上也就是多几百毫秒。

用法（仓库根目录）：
    python tools/run_all.py            # 跑全部
    python tools/run_all.py qt_main   # 只跑文件名里带 qt_main 的
退出码：全过 0，否则 1（CI 直接看这个）。
"""

import os
import subprocess
import sys
import time
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
测试目录 = 根 / "tools"
超时 = 300          # 单个文件最多跑这么久（秒）


def 收集(关键字=None):
    结果 = []
    for p in sorted(测试目录.glob("test_*.py")):
        if 关键字 and 关键字 not in p.name:
            continue
        结果.append(p)
    return 结果


def 跑一个(路径):
    模块 = 路径.stem
    环境 = dict(os.environ)
    环境["PYTHONIOENCODING"] = "utf-8"
    环境.setdefault("MCTOOL_LANG", "zh")
    # 老式冒烟脚本（比如 test_toast.py）没有 unittest.TestCase，直接当脚本跑
    是脚本 = "TestCase" not in 路径.read_text(encoding="utf-8", errors="replace")
    命令 = [sys.executable, str(路径)] if 是脚本 else [sys.executable, "-m", "unittest", 模块]
    t0 = time.time()
    try:
        cp = subprocess.run(
            命令,
            cwd=str(测试目录), env=环境, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=超时,
        )
        耗时 = time.time() - t0
        输出 = (cp.stdout or "") + (cp.stderr or "")
        通过 = cp.returncode == 0
    except subprocess.TimeoutExpired:
        耗时 = time.time() - t0
        输出 = f"超时（>{超时}s）"
        通过 = False
    # 摘出用例数
    用例 = ""
    for 行 in 输出.splitlines():
        if 行.startswith("Ran "):
            用例 = 行.replace("Ran ", "").replace(" tests in", " /").split(" in")[0].strip()
            break
    失败 = [l.strip() for l in 输出.splitlines()
            if l.startswith(("FAIL:", "ERROR:"))]
    return {"模块": 模块, "通过": 通过, "耗时": 耗时, "输出": 输出, "失败": 失败, "用例": 用例}


def main(argv):
    关键字 = argv[1] if len(argv) > 1 else None
    文件 = 收集(关键字)
    if not 文件:
        print("没找到匹配的测试文件")
        return 1

    print(f"共 {len(文件)} 个测试文件，逐个独立进程跑……\n")
    结果 = []
    t0 = time.time()
    for p in 文件:
        r = 跑一个(p)
        结果.append(r)
        标记 = "PASS" if r["通过"] else "FAIL"
        print(f"  [{标记}] {r['模块']:<34} {r['用例']:>8}  {r['耗时']:.1f}s")
        if not r["通过"]:
            for 行 in r["失败"][:6]:
                print(f"         {行}")
            if len(r["失败"]) > 6:
                print(f"         …… 还有 {len(r['失败']) - 6} 条")

    失败数 = sum(1 for r in 结果 if not r["通过"])
    print(f"\n合计 {len(结果)} 个文件，{len(结果) - 失败数} 通过，{失败数} 失败，"
          f"用时 {time.time() - t0:.1f}s")

    if 失败数:
        print("\n失败详情（只打印失败文件的前 40 行）：")
        for r in 结果:
            if not r["通过"]:
                print(f"\n===== {r['模块']} =====")
                print("\n".join(r["输出"].splitlines()[-40:]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

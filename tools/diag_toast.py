# tools/diag_toast.py
"""吐司动画性能体检：在你自己的机器/显示器上量，结果贴回来即可。

为什么需要它：开发机上反复测出来都是"每帧 0.5ms、最大间隔 9.5ms"，但用户机器上
看着仍不顺 —— 差异只能在实际环境里量（显示器刷新率、DWM 合成、主线程上别的回调）。

它会做这些事：
  1. 用**临时配置**启动真程序（预先写好 language，避免弹首次运行的语言选择框）；
  2. 弹 4 条和真实消息一样长的吐司，再插第 5 条（触发"挤掉最旧 + 让位动画"）；
  3. 记录每一帧的时间戳与渲染耗时，以及显示器刷新率、1ms 定时器精度是否持有；
  4. 打完报告自己退出（不会留在后台，也不改你的真实配置）。

用法：PyCharm 里右键 Run，或命令行
    python tools/diag_toast.py
然后把最后那几行报告贴回来。
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

这里 = Path(__file__).resolve().parent
源码 = 这里.parent / "Minecraft迁移工具"

# ---- 1) 临时配置：必须有 language，否则 app.py 会弹语言选择框并等点击 ----
CFG = Path(tempfile.gettempdir()) / "mctool_diag_toast.json"
try:
    CFG.write_text(json.dumps({"language": "zh", "theme": "dark"},
                              ensure_ascii=False), encoding="utf-8")
except Exception as e:
    print("写临时配置失败：", e)
os.environ["MCTOOL_CONFIG"] = str(CFG)

sys.path.insert(0, str(源码))

import tkinter as tk          # noqa: E402

import ui.main_window as MW   # noqa: E402
import utils.config as _cfg   # noqa: E402

MW.CONFIG_FILE = CFG
_cfg.CONFIG_FILE = CFG

from utils import anim         # noqa: E402

长文 = "🧪 模拟运行已开启：只显示将要执行的操作，不会修改任何文件"
采样 = []
状态 = {"t0": 0.0, "阶段": "预热"}


def _接(me):
    host = me._toast_host
    原始 = host._compose

    def 包(*a, **k):
        t = time.perf_counter()
        try:
            return 原始(*a, **k)
        finally:
            采样.append((状态["阶段"], t, (time.perf_counter() - t) * 1000.0,
                       anim.holding()))
    host._compose = 包

    def 开始():
        状态["阶段"] = "4 条入场"
        状态["t0"] = time.perf_counter()
        for i in range(4):
            me.toast("%s（%d）" % (长文, i), ["ok", "info", "warn", "error"][i], ms=20000)
        me.root.after(500, 插入)

    def 插入():
        状态["阶段"] = "第 5 条挤占"
        状态["t0"] = time.perf_counter()
        采样.clear()
        me.toast("%s（插入）" % 长文, "info", ms=20000)
        me.root.after(600, 收官)

    def 收官():
        报告(me, host)
        try:
            me.root.quit()
        except Exception:
            pass

    me.root.after(900, 开始)


def 报告(me, host):
    间 = sorted((采样[i][1] - 采样[i - 1][1]) * 1000.0 for i in range(1, len(采样)))
    耗 = sorted(s[2] for s in 采样)
    持 = sum(1 for s in 采样 if s[3])
    try:
        屏宽 = me.root.winfo_screenwidth()
        屏高 = me.root.winfo_screenheight()
    except Exception:
        屏宽 = 屏高 = 0
    try:
        刷新 = anim.refresh_hz(host.win)
    except Exception:
        刷新 = "?"
    条 = len(host._items)
    宽 = max((it.w for it in host._items), default=0)
    print("")
    print("================ 吐司体检报告 ================")
    print("屏幕 %dx%d   识别到刷新率 %s Hz   条数 %d   卡宽 %d px" %
          (屏宽, 屏高, 刷新, 条, 宽))
    print("帧数 %d   持有 1ms 定时器精度的帧 %d（应≈全部）" % (len(采样), 持))
    if 间:
        print("帧间隔  p50=%.1f  p90=%.1f  p99=%.1f  max=%.1f ms" %
              (间[len(间) // 2], 间[int(len(间) * 0.9)],
               间[int(len(间) * 0.99)], 间[-1]))
        print("        超过 20ms 的帧 = %d / %d" %
              (sum(1 for d in 间 if d > 20), len(间)))
    if 耗:
        print("单帧耗时 p50=%.2f  p90=%.2f  max=%.2f ms" %
              (耗[len(耗) // 2], 耗[int(len(耗) * 0.9)], 耗[-1]))
    print("==============================================")
    print("（把上面这几行贴回给开发者即可；临时配置在 %s，可删）" % CFG)


def main():
    原始 = MW.MigrationGUI

    class 包一层(原始):
        def __init__(self, *a, **k):
            原始.__init__(self, *a, **k)
            self.root.after(1500, lambda: _接(self))

    MW.MigrationGUI = 包一层

    主循环 = tk.Misc.mainloop

    def 带超时(self, n=0):
        self.after(12000, self.quit)        # 安全阀：12 秒后一定退出
        return 主循环(self, n)

    tk.Misc.mainloop = 带超时
    import app
    app.main()
    print("（诊断进程已退出）")


if __name__ == "__main__":
    main()

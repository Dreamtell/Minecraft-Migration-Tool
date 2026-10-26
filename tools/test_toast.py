# tools/test_toast.py
"""吐司冒烟测试：把状态机所有分支走一遍，任何回调异常都算 FAIL。

为什么需要它：吐司的渲染分**两条路径**（全部静止走"缓存底图 + 贴进度条"，
有动画走"整叠重绘"），两条路径各自持有不同的缓冲。曾经的崩溃就是这么来的 ——
只测"从动画开始"的场景碰不到"先静止、再来新消息时才分配另一半缓冲"的那条路，
真程序里点开几条等它们静止、再触发一条就 AttributeError。

用法：python tools/test_toast.py      （不需要真程序，3 秒内跑完）
"""

import sys
import traceback
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Minecraft迁移工具"))

from ui.toast import ToastHost          # noqa: E402
from utils.theme import DARK_THEME, LIGHT_THEME   # noqa: E402

长文 = "🧪 模拟运行已开启：只显示将要执行的操作，不会修改任何文件"

错误 = []


def main():
    root = tk.Tk()
    root.geometry("820x560+240+120")
    root.configure(bg="#ff8800")

    def 记(*a):
        错误.append(a)
        traceback.print_exception(*a)

    root.report_callback_exception = 记
    host = ToastHost(root, DARK_THEME)
    # ⚠ 语言/主题都用临时配置无关，这里只用控件本身，不启动真程序

    def 步(n, fn):
        root.after(n, fn)

    def s1():
        host.show(长文 + "（1）", "ok", ms=20000)
        步(700, s2)        # 等它静止，走到稳态分支

    def s2():
        host.show(长文 + "（2）", "info", ms=20000)   # 静止 → 动画分支（曾在此崩溃）
        步(400, s3)

    def s3():
        host.show(长文 + "（3）", "warn", ms=20000)
        host.show(长文 + "（4）", "error", ms=20000)
        步(500, s4)

    def s4():
        host.show(长文 + "（5）", "ok", ms=20000)     # 满栈挤占 + 让位动画
        步(500, s5)

    def s5():
        host.set_theme(LIGHT_THEME)                  # 重建全部条目
        步(400, s6)

    def s6():
        for it in list(host._items):
            it.dismiss()                             # 淡出 + 回收
        步(600, s7)

    def s7():
        host.clear()                                 # 窗口 withdraw
        host.show("再来一条", "ok", ms=20000)         # 重新映射
        步(500, s8)

    def s8():
        print("回调异常 %d 次，剩余条数 %d" % (len(错误), len(host._items)))
        print("PASS" if not 错误 else "FAIL")
        host.clear()
        root.after(100, root.quit)

    root.after(300, s1)
    root.mainloop()
    return 0 if not 错误 else 1


if __name__ == "__main__":
    sys.exit(main())

# app.py
import os
import queue
import sys
import ctypes
import time
import tkinter as tk
from tkinter import messagebox

# ---- 独立进程闪屏的子进程入口 ----
# 必须放在所有重活之前：子进程只负责画闪屏，绝不碰 Tk/单实例锁/托盘。
if "--splash-child" in sys.argv:
    try:
        from ui.splash_qt import run_child
    except Exception:
        sys.exit(2)
    sys.exit(run_child())

from tendo import singleton
from ui.main_window import MigrationGUI
from ui.dialogs import ask_close_action
from utils.helpers import (get_icon_path, warm_up_emoji_font, clear_layered_style,
                           focus_window)
from utils.config import load_raw_config
from winotify import Notification, audio

# 闪屏最短显示时长（秒）。主界面构建只要 ~0.7s，不兜底的话立方体刚起转就淡出了；
# 超过这个时间就立刻淡出，不会平白拖慢启动。
SPLASH_MIN_SEC = 1.5

# 是否用独立进程的 Qt 闪屏（PySide6 可用时默认开）。它不受主线程构建影响，
# 实测主线程连续忙 1.95s 期间仍是 59~62fps；进程内的 Tk 闪屏在那段时间只有
# ~25fps 且最大冻结 325ms（因为只有 on_stage 时才泵得到一帧）。
# 出问题时可以用环境变量 DSH_NO_QT_SPLASH=1 关掉，退回进程内闪屏。
SPLASH_QT = os.environ.get("DSH_NO_QT_SPLASH") != "1"

# 单实例锁的持有者：必须活到进程结束（被回收就释放锁，程序会变成可多开）
_LOCK_HOLDER = None


def send_toast(title, msg):
    """弹一条 Windows 系统通知（winotify，兼容 Win10/11）。"""
    try:
        toast = Notification(app_id="迁移工坊", title=title, msg=msg,
                             duration="short")
        toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception:
        pass


def main():
    # 单实例检查
    global _LOCK_HOLDER
    try:
        # 必须留着这个对象：它被回收就会释放单实例锁，程序就变成"可多开"了。
        # 放在模块级变量里而不是局部变量，就是为了让它活到进程结束
        # （顺带也不会被 IDE 当成"赋值了没用"的死变量）。
        _LOCK_HOLDER = singleton.SingleInstance()
    except singleton.SingleInstanceException:
        # 已经在跑了：优先请那个实例把主界面叫出来（窗口可能正挂在托盘里，
        # 这种状态下靠标题 FindWindow + ShowWindow 是叫不动的）
        from ui import tray as _tray_mod
        if not _tray_mod.request_show_existing():
            try:
                hwnd = ctypes.windll.user32.FindWindowW(
                    None, "Minecraft 整合包迁移工具 - 增强版 v4")
                if hwnd:
                    if ctypes.windll.user32.IsIconic(hwnd):
                        ctypes.windll.user32.ShowWindow(hwnd, 9)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        sys.exit(0)

    # 创建主窗口（先隐藏，避免瞬移）；使用 TkinterDnD 以支持文件拖拽
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    root.withdraw()
    root.title("Minecraft 整合包迁移工具 - 增强版 v4")
    root.geometry("1000x1080")

    # 设置图标
    icon_path = get_icon_path()
    if icon_path:
        try:
            root.iconbitmap(icon_path)
        except:
            pass

    # 先把 emoji 那边的一次性开销（字体回退枚举 ~270ms + 首个带 emoji 的 Label
    # 排版 ~40ms）做掉，再弹闪屏。放这儿是为了"闪屏一出现就是流畅的"——挪到闪屏
    # 出来之后做的话，用户会看到立方体先愣住 300ms 才开始转。
    # 代价是双击之后要多等这 300ms 才看到卡片，但总时长不变（闪屏最短时长照算）。
    # 设置里关掉启动动画时这笔预热照样做：界面里的 emoji 该卡还是会卡。
    # 闪屏子进程先起来（它自己启动要 ~0.4s），再去做 emoji 预热 —— 两件事并行，
    # 等主界面开始构建时闪屏正好已经画出来了。放在预热之后的话，用户会先愣一下
    # 才看到卡片。
    qt_splash = None
    splash_on = bool(load_raw_config().get("splash", True))
    splash_t0 = time.perf_counter()
    if splash_on and SPLASH_QT:
        try:
            from ui import splash_qt
            qt_splash = splash_qt.spawn()
        except Exception:
            qt_splash = None

    # 先把 emoji 那边的一次性开销（字体回退枚举 ~270ms + 首个带 emoji 的 Label
    # 排版 ~40ms）做掉，再弹闪屏。放这儿是为了"闪屏一出现就是流畅的"——挪到闪屏
    # 出来之后做的话，用户会看到立方体先愣住 300ms 才开始转。
    # 代价是双击之后要多等这 300ms 才看到卡片，但总时长不变（闪屏最短时长照算）。
    # 设置里关掉启动动画时这笔预热照样做：界面里的 emoji 该卡还是会卡。
    warm_up_emoji_font()

    # 弹出启动闪屏，盖住主界面构建期间的空窗（构建实测约 0.5~2s）
    # 优先用**独立进程**的 Qt 闪屏：构建期间主线程被占满，进程内的闪屏只有
    # on_stage 那几下能泵到帧（实测最大 325ms 完全静止）；独立进程有自己的事件
    # 循环，主线程再忙也照样 59~62fps。拿不到 PySide6 / 起不来就回落进程内 Tk 闪屏。
    splash = None
    if splash_on and qt_splash is None:
        try:
            from ui.splash import SplashScreen
            splash = SplashScreen(root, icon_path=icon_path)
            root.update()           # 让它真的画出来，而不是停在未渲染状态
        except Exception:
            splash = None

    # 主窗口的位置先算好，但**不显示**——构建期间窗口保持隐藏，等闪屏收走了
    # 再让 Tk/Windows 原生显示出来。不提前映射、不预画、不做显形动画。
    root.update_idletasks()
    width, height = 1000, 1080
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")

    # 创建主界面实例（此时窗口仍隐藏）
    # on_stage：构建途中被回调，用来让出一帧，否则主线程被占满，闪屏动画会停住。
    def on_stage(text=""):
        if splash is not None and text:
            splash.set_status(text)
        try:
            root.update()
        except Exception:
            pass

    app = MigrationGUI(root, on_stage=on_stage)

    if splash is not None:
        try:
            splash.lift()                   # 保证闪屏仍在最上层
        except Exception:
            pass

    def _make_sure_visible():
        """收尾：保证窗口显示出来，并且是普通窗口（不带 layered）。

        layered 是"Windows 会跳过关闭动画"的那个样式。现在整条启动路径都不碰
        -alpha，窗口本来就是普通的，这里只是兜底清一下。
        """
        try:
            root.deiconify()
        except Exception:
            pass
        clear_layered_style(root)
        focus_window(root)

    def reveal_main_window():
        """主界面显形——直接让 Tk / Windows 原生显示，不做动画、不提前预画。"""
        try:
            root.deiconify()
        except Exception:
            pass

    # 构建如果太快（约 0.4s），立方体刚起转就收起了，所以给闪屏一个最短显示时长；
    # 等待期间继续 update，动画照常跑。
    if qt_splash is not None:
        # 独立进程闪屏：它自己按 60fps 播，这里只需要凑够最短显示时长，
        # 然后发个 close 让它播"收起"动画（不阻塞），主界面立刻显形 —— 闪屏在最上层
        # 缩没了自然露出主界面。
        remain = SPLASH_MIN_SEC - (time.perf_counter() - splash_t0)
        while remain > 0 and time.time() < splash_t0 + SPLASH_MIN_SEC:
            time.sleep(0.01)
            remain = SPLASH_MIN_SEC - (time.perf_counter() - splash_t0)
        reveal_main_window()
        qt_splash.close()
        _make_sure_visible()
    elif splash is not None:
        remain = SPLASH_MIN_SEC - (time.perf_counter() - splash_t0)
        deadline = time.time() + remain
        while remain > 0 and time.time() < deadline:
            try:
                root.update()
            except Exception:
                break
            time.sleep(0.002)       # 让出一点点 CPU 即可，sleep 太久会把帧率拖下去
        try:
            # 主界面等卡片缩到只剩一小点（on_shrunk）才显形：显形会让主窗口做
            # 第一次整绘（实测 150ms 上下），趁卡片已经很小的时候做，看不出来。
            splash.close(on_shrunk=reveal_main_window, on_done=_make_sure_visible)
        except Exception:
            reveal_main_window()
            _make_sure_visible()
    else:
        reveal_main_window()
        _make_sure_visible()

    # ------------------------------------------------------- 系统托盘 / 后台
    # 关窗口不再退出，只是 withdraw 收进托盘，任务在后台继续跑；托盘菜单里才有
    # 真正的"退出"。没装 pywin32 时 tray 为 None，一切退回"关窗口就退出"。
    from ui import tray as tray_mod

    tray = None
    if tray_mod.available():
        try:
            tray = tray_mod.TrayIcon("Minecraft 整合包迁移工具", icon_path)
            if not tray.start():
                tray = None
        except Exception:
            tray = None

    hidden = {"v": False}          # 主界面是不是收在托盘里
    tray_tip_shown = {"v": False}
    poll_id = {"v": None}          # tray_poll 的 after id（退出时要撤掉）
    quitting = {"v": False}

    def notify_task_done(name, detail="", force=False):
        """任务跑完时弹个系统通知。

        force=True 是"后台静默执行"用的：那种模式下没有任何窗口反馈，
        所以不管界面是不是挂在托盘里都得通知一声。
        """
        if hidden["v"] or force:
            send_toast(f"✅ {name}完成", detail or "点托盘图标打开主界面查看")

    app._task_done_cb = notify_task_done
    app._in_tray_cb = lambda: hidden["v"]
    if tray is not None:
        # 托盘右键菜单里的勾选状态跟配置对齐（ask 视为"默认收进托盘"）
        tray.close_to_tray = (getattr(app, "close_action", "ask") != "exit")

    def apply_close_action(action):
        """按用户选的关闭方式收尾。"""
        if action == "tray":
            hide_to_tray()
        elif action == "exit":
            request_quit()

    def ask_and_close():
        """没在跑任务时点 ❌：问一次挂后台还是直接退出（可记住）。"""
        action = getattr(app, "close_action", "ask")
        if action == "ask":
            action, remember = ask_close_action(root, app.theme)
            if action is None:
                return                      # 取消：什么都不做
            if remember:
                app.set_close_action(action)
                if tray is not None:
                    tray.close_to_tray = (action == "tray")
        apply_close_action(action)

    def show_main_window():
        """从托盘把主界面叫回来——同样走原生显示，不做淡入/预画。"""
        if not hidden["v"]:
            try:
                root.deiconify()
                focus_window(root)
            except Exception:
                pass
            return
        hidden["v"] = False
        if tray is not None:
            tray.set_tooltip("Minecraft 整合包迁移工具")
        _make_sure_visible()
        try:
            app._flush_pending_diff()       # 挂托盘期间扫描出的窗口，这时候补开
        except Exception:
            pass

    def hide_to_tray():
        hidden["v"] = True
        if tray is not None:
            tray.set_tooltip("Minecraft 整合包迁移工具（后台运行中，点这里打开）")
        try:
            root.withdraw()                 # withdraw 会触发系统原生的隐藏动画
        except Exception:
            pass
        if not tray_tip_shown["v"]:
            tray_tip_shown["v"] = True
            send_toast("已收进系统托盘",
                       "程序仍在后台运行。点托盘图标可重新打开，右键可退出。")

    def request_quit():
        """请求退出：先跳出主循环，存盘和销毁统一放到 mainloop 之后做。

        顺手把托盘轮询的定时器撤掉——不然它会带着一个已经被销毁的 Tcl 命令
        在解释器收尾时炸出一行 "invalid command name"。
        """
        quitting["v"] = True
        if poll_id["v"] is not None:
            try:
                root.after_cancel(poll_id["v"])
            except Exception:
                pass
            poll_id["v"] = None
        try:
            root.quit()
        except Exception:
            pass

    def tray_poll():
        """Tk 主线程这边轮询托盘线程塞进来的命令。"""
        if quitting["v"]:
            return
        if tray is not None:
            try:
                while True:
                    cmd = tray.commands.get_nowait()
                    if cmd == "open":
                        show_main_window()
                    elif cmd == "toggle_close_to_tray":
                        # 右键菜单里那项是勾选式的：勾上=关闭时收进托盘，取消=直接退出
                        new = "exit" if getattr(app, "close_action", "ask") == "tray" else "tray"
                        app.set_close_action(new)
                        tray.close_to_tray = (new == "tray")
                    elif cmd == "exit":
                        busy = app.busy_task_name()
                        if busy and not messagebox.askyesno(
                                f"⚠️ {busy}进行中",
                                f"{busy}任务还在执行，现在退出会中断它，"
                                "可能导致数据损坏或程序状态异常。\n\n"
                                "确定要退出吗？（也可以选“否”，把窗口收进托盘"
                                "让它跑完）",
                                default="no", icon="warning", parent=root):
                            continue        # 不退出，继续处理后面的命令
                        request_quit()
                        return
            except queue.Empty:
                pass
        try:
            poll_id["v"] = root.after(80, tray_poll)
        except Exception:
            pass

    # 关闭事件处理：任务进行中不允许直接关掉，只能挂到托盘或取消这次关闭
    def on_closing():
        busy = app.busy_task_name()

        if busy:
            if tray is None:
                # 没有托盘就没地方"挂着跑"，只能劝住
                messagebox.showwarning(
                    f"⚠️ {busy}进行中",
                    f"{busy}任务正在执行，现在关闭会中断操作，可能导致数据损坏"
                    "或程序状态异常。\n\n请等任务结束后再关闭窗口。",
                    parent=root)
                return
            if messagebox.askyesno(
                    f"⚠️ {busy}进行中",
                    f"{busy}任务正在执行，窗口不能直接关闭。\n\n"
                    "点击「是」 → 收进系统托盘，任务在后台继续跑，跑完会弹通知\n"
                    "点击「否」 → 返回程序，等任务结束",
                    default="no", icon="warning", parent=root):
                hide_to_tray()
            return

        # 没在跑任务：有托盘就让用户选（挂后台 / 直接退出），没托盘就只能直接退出
        if tray is not None:
            ask_and_close()
        else:
            request_quit()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    if tray is not None:
        root.after(300, tray_poll)
    root.mainloop()

    # 主循环结束（不管从哪条路退出）：统一收尾
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass
    try:
        app.save_config()
    except Exception:
        pass
    # 窗口还开着（托盘菜单"退出"、或没托盘时关窗口）：同样先 withdraw，让系统把
    # 它自己那段隐藏动画播完再销毁，别"啪"地消失。
    try:
        if root.winfo_exists() and root.winfo_viewable():
            root.withdraw()
            deadline = time.time() + 0.28
            while time.time() < deadline:
                root.update()
                time.sleep(0.005)
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()

# app.py
import queue
import sys
import ctypes
import time
import tkinter as tk
from tkinter import messagebox
from tendo import singleton
from ui.main_window import MigrationGUI, _fade_in
from ui.dialogs import ask_close_action
from utils.helpers import (get_icon_path, warm_up_emoji_font, clear_layered_style,
                           focus_window)
from winotify import Notification, audio

# 闪屏最短显示时长（秒）。主界面构建只要 ~0.7s，不兜底的话立方体刚起转就淡出了；
# 超过这个时间就立刻淡出，不会平白拖慢启动。
SPLASH_MIN_SEC = 1.5


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
    try:
        me = singleton.SingleInstance()
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

    # 先弹出启动闪屏，盖住主界面构建期间的空窗（构建实测约 0.7s）
    splash = None
    splash_t0 = time.perf_counter()
    try:
        from ui.splash import SplashScreen
        splash = SplashScreen(root, icon_path=icon_path)
        root.update()               # 让它真的画出来，而不是停在未渲染状态
    except Exception:
        splash = None

    # 闪屏已经显示出来了，先把 emoji 字体回退查一次（约 0.27s）。
    # 不预热的话这笔钱会在建第一个带 emoji 的按钮时花掉，正好卡在立方体转到一半。
    warm_up_emoji_font()

    # 创建主界面实例（此时窗口仍隐藏）
    # on_stage：构建途中被回调，用来更新闪屏文字并让出一帧，
    # 否则主线程被占满，闪屏动画会整个停住。
    def on_stage(text=""):
        if splash is not None and text:
            splash.set_status(text)
        try:
            root.update()
        except Exception:
            pass

    app = MigrationGUI(root, on_stage=on_stage)

    # 主界面已经建完了。先把它以 0 透明度显示在闪屏下面，把首次整窗绘制
    # （实测 200~400 ms）在这里做掉：等闪屏淡出、卡片消失的那一瞬，露出来的
    # 就是已经画好的界面——用户看到的是"卡片没了 → 主界面直接出现"，
    # 中间既没有空白，也不会先闪一下没画完的窗口。
    root.update_idletasks()
    width, height = 1000, 1080
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")
    try:
        root.attributes("-alpha", 0.0)      # 藏起来画（失败就走下面的退路）
        hidden_by_alpha = True
    except Exception:
        hidden_by_alpha = False
    if hidden_by_alpha:
        root.deiconify()
        root.update()                       # 这一下把整窗画完，此时完全透明、看不见
    if splash is not None:
        try:
            splash.lift()                   # 保证闪屏仍在最上层
        except Exception:
            pass

    def _make_sure_visible():
        """收尾：保证窗口确实显示出来，并把 layered 样式摘掉。

        摘掉是关键——Tk 用过 -alpha 之后窗口一直带着 WS_EX_LAYERED，而 Windows
        对 layered 窗口会跳过关闭时的系统过渡动画，关窗就会"直接没了"。
        此时 alpha 已经回到 1.0，摘掉在视觉上没有任何变化。
        """
        try:
            root.attributes("-alpha", 1.0)
        except Exception:
            pass
        try:
            root.deiconify()
        except Exception:
            pass
        clear_layered_style(root)

    def reveal_main_window():
        """闪屏彻底消失之后才让主界面显形。

        显形本身也分几帧淡入——和"放大查看"那些弹窗用的是同一套 _fade_in，
        否则卡片一没、主界面"啪"地硬切出来，弹出的那一下动画就没了。
        淡入走完（on_done）再摘掉 layered 样式，让关闭时的系统动画能回来。
        """
        try:
            if not hidden_by_alpha:
                root.deiconify()
            _fade_in(root, on_done=_make_sure_visible)
        except Exception:
            _make_sure_visible()

    # 构建如果太快（约 0.4s），立方体刚起转就淡出了，所以给闪屏一个最短显示时长；
    # 等待期间继续 update，动画照常跑。
    if splash is not None:
        remain = SPLASH_MIN_SEC - (time.perf_counter() - splash_t0)
        deadline = time.time() + remain
        while remain > 0 and time.time() < deadline:
            try:
                root.update()
            except Exception:
                break
            time.sleep(0.002)       # 让出一点点 CPU 即可，sleep 太久会把帧率拖下去
        try:
            splash.close(on_done=reveal_main_window)
        except Exception:
            reveal_main_window()
    else:
        reveal_main_window()

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

    def notify_task_done(name, detail=""):
        """任务跑完时窗口若挂在托盘里，弹个系统通知提醒一下。"""
        if hidden["v"]:
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
        """从托盘把主界面叫回来（同样分帧淡入，避开 deiconify 的白闪）。"""
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
        try:
            root.attributes("-alpha", 0.0)
        except Exception:
            pass
        try:
            root.deiconify()
            root.update()
        except Exception:
            pass

        def done():
            clear_layered_style(root)       # 摘掉后系统才会给关闭动画
            focus_window(root)

        _fade_in(root, on_done=done)
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
        """请求退出：先跳出主循环，存盘和销毁统一放到 mainloop 之后做。"""
        try:
            root.quit()
        except Exception:
            pass

    def tray_poll():
        """Tk 主线程这边轮询托盘线程塞进来的命令。"""
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
            root.after(80, tray_poll)
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

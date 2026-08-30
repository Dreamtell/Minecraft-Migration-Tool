# app.py
import sys
import ctypes
import tkinter as tk
from tkinter import messagebox
from tendo import singleton
from ui.main_window import MigrationGUI
from utils.helpers import get_icon_path
from winotify import Notification, audio


def send_startup_notification():
    """发送 Windows 系统通知（winotify，兼容 Win10/11）"""
    try:
        toast = Notification(
            app_id="迁移工坊",
            title="🚀 迁移工坊",
            msg="Minecraft 迁移工具已启动，正在加载主界面...",
            duration="short"
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception:
        pass


def main():
    # 单实例检查
    try:
        me = singleton.SingleInstance()
    except singleton.SingleInstanceException:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "Minecraft 整合包迁移工具 - 增强版 v4")
            if hwnd:
                if ctypes.windll.user32.IsIconic(hwnd):
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except:
            pass
        sys.exit(0)

    # 发送启动通知（非阻塞）
    send_startup_notification()

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

    # 创建主界面实例（此时窗口仍隐藏）
    app = MigrationGUI(root)

    # 强制更新布局，获取实际尺寸
    root.update_idletasks()
    width, height = 1000, 1080
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")

    # 居中后再显示窗口（无过渡动画）
    root.deiconify()

    # 关闭事件处理
    def on_closing():
        is_running = False
        if hasattr(app, '_migration_running') and app._migration_running:
            is_running = True
            operation_name = "迁移"
        elif hasattr(app, '_scanning') and app._scanning:
            is_running = True
            operation_name = "扫描模组差异"

        if is_running:
            result = messagebox.askyesnocancel(
                f"⚠️ {operation_name}进行中",
                f"{operation_name}任务正在执行，关闭窗口将中断操作，可能导致数据损坏或程序状态异常！\n\n"
                "点击“是” → 立即强制退出（不推荐）\n"
                "点击“否” → 返回程序，等待操作完成\n"
                "点击“取消” → 取消本次关闭操作"
            )
            if result is None:
                return
            elif result is False:
                return
            else:
                app.save_config()
                root.destroy()
        else:
            app.save_config()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()

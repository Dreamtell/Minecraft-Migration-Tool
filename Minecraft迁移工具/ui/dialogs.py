# ui/dialogs.py
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
from utils.helpers import set_window_icon
from core.scanner import get_full_mod_metadata


class ProgressWindow:
    """迁移进度模态窗口"""
    def __init__(self, parent, total_files, total_size):
        self.parent = parent
        self.total_files = total_files
        self.total_size = total_size
        self.cancelled = False

        self.win = tk.Toplevel(parent)
        self.win.withdraw()

        self.win.title("迁移进度")
        self.win.geometry("500x200")
        self.win.transient(parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self.on_cancel)
        set_window_icon(self.win)

        self.file_label = tk.Label(self.win, text="准备中...", anchor="w")
        self.file_label.pack(fill="x", padx=10, pady=5)

        self.progress = ttk.Progressbar(self.win, length=460, mode='determinate')
        self.progress.pack(padx=10, pady=5)

        self.stats_label = tk.Label(self.win, text="0 / 0 个文件  |  0.0 MB / 0.0 MB", anchor="w")
        self.stats_label.pack(fill="x", padx=10, pady=5)

        self.cancel_btn = tk.Button(self.win, text="取消迁移", command=self.on_cancel, bg="lightcoral")
        self.cancel_btn.pack(pady=10)

        self.hint_label = tk.Label(
            self.win,
            text="⚠️ 迁移进行中，请勿关闭主窗口！",
            fg="red",
            font=("微软雅黑", 9, "bold")
        )
        self.hint_label.pack(pady=2)

        # 居中
        self.win.update_idletasks()
        win_width = self.win.winfo_width()
        win_height = self.win.winfo_height()
        screen_width = self.win.winfo_screenwidth()
        screen_height = self.win.winfo_screenheight()
        x = (screen_width - win_width) // 2
        y = (screen_height - win_height) // 2
        self.win.geometry(f"+{x}+{y}")

        self.win.deiconify()

    def on_cancel(self):
        self.cancelled = True
        self.cancel_btn.config(state=tk.DISABLED, text="正在取消...")

    def update_progress(self, file_index, file_name, copied_bytes):
        self.file_label.config(text=f"正在复制: {file_name}")
        if self.total_size > 0:
            percent = min(100, (copied_bytes / self.total_size) * 100)
            self.progress['value'] = percent
        copied_mb = copied_bytes / (1024 * 1024)
        total_mb = self.total_size / (1024 * 1024)
        self.stats_label.config(
            text=f"{file_index} / {self.total_files} 个文件  |  {copied_mb:.1f} MB / {total_mb:.1f} MB"
        )
        self.win.update_idletasks()

    def close(self):
        self.win.destroy()


class ScanProgressWindow:
    """扫描模组差异进度窗口"""
    def __init__(self, parent, total_files, theme):
        self.parent = parent
        self.total_files = total_files
        self.closed = False
        self.theme = theme

        self.win = tk.Toplevel(parent)
        self.win.withdraw()

        self.win.geometry("420x100")
        self.win.resizable(False, False)
        self.win.title("扫描模组差异进度")
        self.win.transient(parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.win.configure(bg=self.theme["bg"])
        set_window_icon(self.win)

        self.file_label = tk.Label(self.win, text="准备扫描...", anchor="w",
                                   bg=self.theme["bg"], fg=self.theme["fg"])
        self.file_label.pack(fill="x", padx=10, pady=5)

        style = ttk.Style()
        style.configure(
            "FixedBlue.Horizontal.TProgressbar",
            background="#4fc3f7",
            troughcolor="#3a3a3a",
            borderwidth=0,
            relief='flat'
        )
        self.progress = ttk.Progressbar(
            self.win,
            length=380,
            mode='determinate',
            style="FixedBlue.Horizontal.TProgressbar"
        )
        self.progress.pack(padx=10, pady=5)

        self.stats_label = tk.Label(self.win, text="0 / 0 个文件", anchor="w",
                                    bg=self.theme["bg"], fg=self.theme["fg"])
        self.stats_label.pack(fill="x", padx=10, pady=5)

        self.win.update_idletasks()
        self.win.tk.eval('tk::PlaceWindow %s center' % self.win.winfo_pathname(self.win.winfo_id()))
        self.win.deiconify()

    def update_progress(self, current, filename):
        self.file_label.config(text=f"正在解析: {filename}")
        if self.total_files > 0:
            percent = (current / self.total_files) * 100
            self.progress['value'] = percent
        self.stats_label.config(text=f"{current} / {self.total_files} 个文件")
        self.win.update_idletasks()

    def close(self):
        if not self.closed:
            self.closed = True
            self.win.destroy()

    def on_cancel(self):
        messagebox.showwarning("提示", "扫描正在进行，请等待完成。")


def show_mod_detail(parent, jar_path, theme):
    """显示模组详细信息窗口（独立函数）"""
    info = get_full_mod_metadata(jar_path)
    win = tk.Toplevel(parent)
    win.title(f"模组详情 - {os.path.basename(jar_path)}")
    win.geometry("600x450")
    win.transient(parent)
    set_window_icon(win)
    tk.Label(win, text=f"文件: {os.path.basename(jar_path)}", font=("微软雅黑", 10, "bold")).pack(pady=5)
    details = [
        f"模组名称: {info['name']}",
        f"Mod ID: {info['modid']}",
        f"版本: {info['version']}",
        f"类型: {info['mod_type']}",
        f"作者: {info['authors']}",
        f"描述: {info['description']}",
        f"依赖: {info['dependencies']}"
    ]
    text = scrolledtext.ScrolledText(win, wrap=tk.WORD, height=15, width=70)
    text.pack(padx=10, pady=10, fill="both", expand=True)
    text.insert(tk.END, "\n".join(details))
    text.config(state=tk.DISABLED)
    tk.Button(win, text="关闭", command=win.destroy).pack(pady=5)
# ui/mw_migration.py
"""迁移流程：统计、磁盘检查、后台线程、进度、历史与回滚、差异扫描。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import queue
import shutil
import threading
import time
import tkinter as tk
from core.migrator import (
    _is_safe_path, do_backup, do_restore, get_backup_path, load_history, mark_rollback,
    match_mod, run_migration,
)
from core.scanner import scan_mod_differences
from pathlib import Path
from tkinter import messagebox
from ui.dialogs import ProgressWindow, ScanProgressWindow, ask_migrate_confirm
from ui.mw_common import (
    _SCAN_DONE, _UI_PUMP_MS, _center_window, _file_task_lock, _grad_width,
)
from ui.virtual_table import VirtualTable
from utils.helpers import DataText, create_gradient_button, focus_window, set_window_icon
from utils.theme import apply_theme_to_widget_tree


def _打桩优先(名字, 默认):
    """取被 _dctest 验证脚本打过桩的那个函数，没打桩就用本模块 import 的那份。

    拆分前这些名字就住在 ui/main_window.py 里，脚本一直是这么打桩的：
        MW.ask_migrate_confirm = lambda *a, **k: True     # 验证迁移收尾.py
        MW.do_backup = lambda *a, **kw: True              # double_start2.py
    现在方法搬到了这个 mixin，`from ... import` 是**一次性绑定**，脚本改 main_window
    的属性就影响不到这里了（确认框会真弹、备份会真跑）。所以这两处依赖改成调用时
    再去主模块取一次 —— 脚本一行都不用改，语义和拆分前完全一致。
    """
    import ui.main_window as _mw
    return getattr(_mw, 名字, 默认)


class MigrationMixin:
    """迁移流程：统计、磁盘检查、后台线程、进度、历史与回滚、差异扫描。"""

    # ---------- 历史记录 ----------
    def action_show_history(self):
        tgt = self.target_path.get().strip()
        if not tgt:
            messagebox.showwarning("提示", "请先选择目标实例根目录")
            return
        target_path = Path(tgt)
        history = load_history(target_path)
        if not history:
            messagebox.showinfo("提示", "当前目标实例没有迁移记录。")
            return

        existing = getattr(self, "_history_win", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    focus_window(existing)      # 已有就置顶
                    return
            except Exception:
                pass
        hist_win = tk.Toplevel(self.root)
        self._history_win = hist_win
        hist_win.withdraw()     # 构建完居中后再显示，避免"闪现-跳到中间"
        hist_win.title("迁移历史记录")
        hist_win.geometry("900x500")
        hist_win.transient(self.root)
        set_window_icon(hist_win)
        # ---- 头部：图标 + 标题 + 目标实例（和模组详情窗口同一套排法） ----
        head = tk.Frame(hist_win, bg=self.theme["bg"])
        head.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(head, text="🕒", font=("微软雅黑", 20), bg=self.theme["bg"],
                 fg=self.theme.get("data_fg", "#1565c0")).pack(side="left",
                                                               padx=(0, 12), anchor="n")
        head_box = tk.Frame(head, bg=self.theme["bg"])
        head_box.pack(side="left", fill="x", expand=True)
        tk.Label(head_box, text="迁移历史记录", font=("微软雅黑", 14, "bold"),
                 bg=self.theme["bg"], fg=self.theme["fg"], anchor="w").pack(fill="x")
        DataText(head_box, self.theme,
                 [("目标实例：", "muted_fg"), (str(target_path), "data_fg")],
                 font=("微软雅黑", 9)).pack(anchor="w", pady=(3, 0))

        # ---- 汇总一行：共几次、回滚几次（数字用数据色） ----
        回滚数 = sum(1 for e in history if e.get("rolled_back", False))
        DataText(hist_win, self.theme,
                 [("共 ", "muted_fg"), (str(len(history)), "data_num_fg"),
                  (" 次迁移", "muted_fg"), ("　·　", "muted_fg"),
                  ("回滚 ", "muted_fg"), (str(回滚数), "data_num_fg"),
                  (" 次", "muted_fg")],
                 font=("微软雅黑", 9)).pack(anchor="w", padx=18, pady=(4, 6))

        # ---- 表格：自绘的 VirtualTable（和"放大查看"窗口同一套，不再是 ttk.Treeview）----
        # Treeview 是系统方角样式、改一行颜色要重绘整屏；换成自绘表后表头/行高/悬停/
        # 彩色状态圆点都跟界面其它地方统一。最新一次迁移放最上面。
        rows = list(reversed(history))
        columns = (("status", "🔵 状态", 104, "w"),
                   ("time", "🕒 迁移时间", 168, "w"),
                   ("source", "📁 来源路径", 400, "w"),
                   ("mods", "🧩 模组数", 92, "center"),
                   ("configs", "⚙️ Config数", 104, "center"),
                   ("extras", "📦 其它文件", 104, "center"))

        def _h_cell(row, key):
            e = rows[row] if 0 <= row < len(rows) else {}
            if key == "time":
                return str(e.get("timestamp", "?"))
            if key == "source":
                return str(e.get("source", "?"))
            if key == "mods":
                return str(e.get("mod_count", 0))
            if key == "configs":
                return str(e.get("config_count", 0))
            if key == "extras":
                return str(e.get("extra_count", 0))
            return ""            # 状态列的文字由 dot() 画，单元格文本留空

        def _h_dot(row):
            """状态列的 (圆点颜色, 文字)：绿=正常，橙=已回滚。"""
            e = rows[row] if 0 <= row < len(rows) else {}
            if e.get("rolled_back", False):
                return self.theme.get("log_warning_fg", "#e65100"), "已回滚"
            return self.theme.get("ok_fg", "#2e7d32"), "正常"

        def _h_tags(row):
            e = rows[row] if 0 <= row < len(rows) else {}
            return ("rolled_back",) if e.get("rolled_back", False) else ("normal",)

        class _HistModel:
            row_count = staticmethod(lambda: len(rows))
            cell = staticmethod(_h_cell)
            dot = staticmethod(_h_dot)
            tags = staticmethod(_h_tags)

        table = VirtualTable(
            hist_win, columns, self.theme,
            font=("微软雅黑", 10), row_height=26, header_height=30,
            tag_styles={
                "rolled_back": (self.theme.get("badge_rollback_bg", "#ffdddd"),
                                self.theme.get("fg", "#000000")),
                "normal": (self.theme.get("badge_normal_bg", "#ffffff"),
                           self.theme.get("ttk_fg", "#000000")),
            })
        table.set_model(_HistModel())
        table.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self._hist_table = table          # 主题切换时 apply_theme 会喂新配色

        # ---- 底部：说明 + 关闭 ----
        bottom = tk.Frame(hist_win, bg=self.theme["bg"])
        bottom.pack(fill="x", padx=14, pady=(0, 12))
        tk.Label(bottom, text="越靠上越新 · 滚轮翻阅", font=("微软雅黑", 8),
                 bg=self.theme["bg"],
                 fg=self.theme.get("muted_fg", "#808080")).pack(side="left")
        btn_close_hist = create_gradient_button(
            bottom, "✖ 关闭", hist_win.destroy,
            colors=("#e53935", "#c62828"),
            width=_grad_width("✖ 关闭"), height=30, font=("微软雅黑", 9, "bold"))
        btn_close_hist.pack(side="right")
        apply_theme_to_widget_tree(hist_win, self.theme)
        hist_win.update_idletasks()
        table.fit_now()                   # 显示前先把列宽排好，避免二次闪烁
        _center_window(hist_win, 900, 500)
        hist_win.deiconify()
        focus_window(hist_win)

    # ---------- 回滚 ----------
    @_file_task_lock("备份回滚")
    def action_rollback(self):
        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，暂不能回滚。")
            return
        self.log("=" * 50, level="INFO")
        self.log("🔄 用户请求执行回滚操作", level="INFO")

        tgt = self.target_path.get().strip()
        if not tgt:
            self.log("❌ 回滚失败：未选择目标实例根目录", level="ERROR")
            messagebox.showerror("错误", "请先选择目标实例根目录")
            return
        tgt_path = Path(tgt)
        if not tgt_path.exists():
            self.log(f"❌ 回滚失败：目标路径不存在 {tgt}", level="ERROR")
            messagebox.showerror("错误", f"目标路径不存在：{tgt}")
            return

        backup_root = get_backup_path(tgt_path)
        if not backup_root.exists():
            self.log(f"❌ 回滚失败：未找到备份目录 {backup_root}", level="ERROR")
            messagebox.showerror("回滚失败", "没有找到可用的备份，无法回滚。")
            return

        self.log(f"📁 找到备份目录：{backup_root}", level="INFO")

        if not messagebox.askyesno(
                "⚠️ 确认回滚",
                f"即将把目标实例恢复到迁移前的状态，此操作将覆盖当前所有内容！\n\n"
                f"目标路径：{tgt}\n"
                f"备份路径：{backup_root}\n\n"
                "mods / config / saves 以及「其它文件」清单里复制过的东西都会还原；\n"
                "迁移前不存在的部分会被删掉。\n\n"
                "此操作不可撤销！\n确定要继续吗？"
        ):
            self.log("❌ 用户取消了回滚操作", level="WARNING")
            return

        self.log("✅ 用户确认回滚，开始执行...", level="SUCCESS")
        success = do_restore(tgt_path, log_func=self.log)
        if success:
            if mark_rollback(tgt_path):
                self.log("📝 已标记本次回滚到历史记录", level="INFO")
            else:
                self.log("ℹ️ 历史记录里没有可标记的迁移记录（可能已经回滚过了）", level="WARNING")
            messagebox.showinfo("回滚完成", "目标实例已恢复到迁移前的状态。")
        else:
            self.log("❌ 回滚操作失败，目标实例可能处于不完整状态，"
                     "备份仍然保留，可重新执行回滚", level="ERROR")
            messagebox.showerror("回滚失败",
                                 "回滚未能完整完成，详情见日志。\n"
                                 "备份目录仍然保留，可以再次尝试回滚。")
        self.log("=" * 50, level="INFO")

    # ---------- 进度轮询 ----------
    def _poll_progress(self):
        if self.progress_queue is not None and self.progress_window is not None:
            try:
                while True:
                    msg = self.progress_queue.get_nowait()
                    if msg is None:
                        self.progress_window.close()
                        self.progress_window = None
                        self.progress_queue = None
                        if self.after_id is not None:
                            self.root.after_cancel(self.after_id)
                            self.after_id = None
                        self._migration_running = False
                        self._unlock_main_window()      # 进度窗口关掉时同步解锁
                        self._notify_task_done(
                            "迁移", "任务已结束，点托盘图标打开主界面查看日志")
                        return
                    self.progress_window.update_progress(*msg)
            except queue.Empty:
                pass
            if self.progress_window and self.progress_window.cancelled:
                if self.progress_queue:
                    self.progress_queue.put(None)
            self.after_id = self.root.after(100, self._poll_progress)
        else:
            self.after_id = None

    # ---------- 扫描相关 ----------
    def action_scan_mod_diff(self):
        # 差异窗口已经开着就直接叫回来（Qt 子进程 / 进程内 Qt / Tk 各判断一次）
        if self._qt_host_alive("diff"):
            self._send_qt_host_command("raise", kind="diff")
            return
        qt_diff = getattr(self, "diff_qt", None)
        if qt_diff is not None and qt_diff.is_alive():
            try:
                qt_diff.show()
                qt_diff.raise_()
                qt_diff.activateWindow()
            except Exception:
                pass
            return
        if hasattr(self,
                   'diff_window') and self.diff_window is not None and self.diff_window.winfo_exists():
            focus_window(self.diff_window)      # 已有就置顶
            return

        if self._scanning:
            return

        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        if not src or not tgt:
            messagebox.showerror("错误", "请先选择源和目标路径")
            return

        if Path(src).resolve() == Path(tgt).resolve():
            messagebox.showinfo("提示", "源目录和目标目录相同，无需比较。")
            return

        self._scanning = True
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="⏳ 扫描中…")
        self._refresh_busy_state()
        self.log("🔍 开始扫描模组差异，请稍候...", level="INFO")
        self.root.update_idletasks()

        total = 0
        if src:
            src_mods = Path(src) / "mods"
            if src_mods.exists():
                total += sum(1 for _ in src_mods.glob("*.jar"))
        if tgt:
            tgt_mods = Path(tgt) / "mods"
            if tgt_mods.exists():
                total += sum(1 for _ in tgt_mods.glob("*.jar"))
        self._scan_total = total

        self.scan_progress_window = ScanProgressWindow(self.root, total, self.theme)

        progress_queue = queue.Queue()
        self._scan_progress_queue = progress_queue

        def scan_task():
            try:
                data = scan_mod_differences(src, tgt, progress_queue, self._scan_total)
            except Exception as e:
                data = None
                error_msg = str(e)
            else:
                error_msg = None
            # 结果丢回队列，由主线程的轮询取走。**不要**在这里调 self.root.after()：
            # tkinter 明确声明 Tk 不是线程安全的；实测事件循环不是 mainloop 时
            # （验证脚本用 update() 泵的那种）会抛 `main thread is not in main loop`，
            # 扫描结果直接丢掉。迁移进度用的是同一条队列路线（见 _run_migration_thread）。
            progress_queue.put((_SCAN_DONE, data, error_msg))

        self._poll_scan_progress()
        threading.Thread(target=scan_task, daemon=True).start()

    # ---------- 后台线程 → 主线程的 UI 通道 ----------
    def _ui_post(self, fn, *args):
        """从**任意线程**请求主线程执行一段 UI 代码（线程安全：只碰队列）。

        为什么不直接用 `root.after`：tkinter 的文档明确说 Tk 不是线程安全的。实测在
        "事件循环不是 mainloop"的场合（验证脚本用 update() 泵的那种）从 worker 调
        `after` 会抛 `main thread is not in main loop`，那一句日志 / 那次收尾就没了
        （`log()` 里原来那段 RuntimeError 兜底注释说的就是这件事）。迁移进度本来就是
        "队列 + 主线程轮询"，这里把日志和迁移收尾也统一到这条路上。
        """
        self._ui_calls.put((fn, args))

    def _ui_pump(self):
        """主线程：执行后台线程排下的 UI 调用，然后继续等下一批。"""
        队列 = getattr(self, "_ui_calls", None)
        if 队列 is not None:
            while True:
                try:
                    fn, args = 队列.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn(*args)
                except Exception:
                    pass
        try:
            self._ui_pump_id = self.root.after(_UI_PUMP_MS, self._ui_pump)
        except Exception:
            self._ui_pump_id = None

    def _poll_scan_progress(self):
        try:
            while True:
                msg = self._scan_progress_queue.get_nowait()
                if msg is None:
                    # 扫描器报完进度会放一个 None（"进度到头了"）：关掉进度窗，
                    # 但**不能就此停止轮询** —— 结果还在后头（扫描线程跑完才放进队列）。
                    if hasattr(self, 'scan_progress_window'):
                        self.scan_progress_window.close()
                        delattr(self, 'scan_progress_window')
                    break
                if isinstance(msg, tuple) and msg[:1] == (_SCAN_DONE,):
                    # 扫描线程把结果交回来了（这里已经是主线程）—— 收尾后停止轮询，
                    # 下次扫描会重新起这个轮询，别让它空转一辈子。
                    self._finish_scan(msg[1], msg[2])
                    return
                current, filename = msg
                total = getattr(self, '_scan_total', 0)
                if total > 0:
                    self.scan_btn.itemconfig(self.scan_btn.text_id,
                                             text=f"⏳ 解析中 ({current}/{total})")
                else:
                    self.scan_btn.itemconfig(self.scan_btn.text_id, text="⏳ 解析中...")
                if hasattr(self, 'scan_progress_window'):
                    self.scan_progress_window.update_progress(current, filename)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_scan_progress)

    def _finish_scan(self, data, error_msg):
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="🔍 扫描模组差异")
        self._scanning = False
        self._refresh_busy_state()

        if hasattr(self, 'scan_progress_window'):
            self.scan_progress_window.close()
            delattr(self, 'scan_progress_window')

        if error_msg:
            self.log(f"❌ 扫描出错: {error_msg}", level="ERROR")
            if self._in_tray():
                self._notify_task_done("扫描模组差异", f"扫描出错：{error_msg}")
            else:
                messagebox.showerror("扫描错误", f"扫描过程中发生异常：{error_msg}")
            return

        if data is None:
            return

        if not data:
            self.log("📊 扫描完成：无差异", level="INFO")
            if self._in_tray():
                self._notify_task_done("扫描模组差异", "两个 mods 目录完全一致，没有差异")
            else:
                messagebox.showinfo("提示", "两个 mods 目录完全一致，没有任何差异。")
            return

        self.log(f"📊 扫描完成，发现 {len(data)} 项差异", level="SUCCESS")

        def apply_callback(selected_files):
            self.mod_text.configure(state=tk.NORMAL)
            self.mod_text.delete(1.0, tk.END)
            self.mod_text.insert(tk.END, "\n".join(selected_files))
            self.mod_text.edit_reset()
            self._update_text_states()
            self.log(f"✅ 从差异扫描中导入了 {len(selected_files)} 个模组", level="SUCCESS")
            self.save_config()
            self._notify_modlist_change()

        # 调用 show_diff_window 并保存窗口引用
        if self._in_tray():
            # 窗口挂在托盘里：别突然弹出这个窗口，先压着，等叫回主界面再开
            self._pending_diff = (data, apply_callback)
            self._notify_task_done("扫描模组差异",
                                   f"发现 {len(data)} 项差异，点托盘图标查看")
            return
        self._open_diff_window(data, apply_callback)

    # ---------- 迁移 ----------
    def _busy_task_name(self):
        """当前正在跑的文件类任务名；没有就返回 None。

        迁移、扫描差异、检查存在性、导入变更日志、回滚、大窗口检测都算 —— 它们都会碰文件，
        同时跑就可能互相踩（比如迁移正复制文件时又去回滚）。
        """
        if self._migration_running:
            return "迁移"
        if self._scanning:
            return "扫描模组差异"
        return getattr(self, "_file_task", None)

    def _begin_file_task(self, name):
        """登记一个文件类任务：期间迁移按钮会变灰，start_migration 也会直接拒绝。"""
        self._file_task = name
        self._refresh_busy_state()

    def _end_file_task(self):
        self._file_task = None
        self._refresh_busy_state()

    def start_migration(self):
        """开始迁移（按钮入口）。

        两段式：先挡住重入、立刻把按钮变灰，再走原来的准备+执行逻辑。
        为什么必须这样：准备阶段（校验清单 / 统计文件大小 / 磁盘检查）可能要几秒，
        期间只要出现过弹窗（messagebox 会开嵌套事件循环），排队的那次点击就会被派发，
        而那时 _migration_running 还没置位 —— 于是一次点击变两次迁移。
        """
        if self._migration_running or getattr(self, "_starting", False):
            self.log("⚠️ 已有迁移在进行中（或正在准备），这次点击已忽略",
                     level="WARNING", save=False)
            return
        self._starting = True
        # 立刻禁用：既是视觉反馈，也让"点了没反应"变成"按钮本来就是灰的"
        try:
            self.start_btn.state("disabled")
        except Exception:
            pass
        try:
            self._start_migration_locked()
        finally:
            self._starting = False
            if not self._migration_running:
                # 提前返回（校验没过/磁盘不足/备份失败…）：把按钮恢复
                self._refresh_busy_state()

    def _start_migration_locked(self):
        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，请勿重复启动")
            return
        # 别的文件任务在跑时禁止迁移：模拟运行也禁（它同样会读整份清单/校验路径，
        # 而且用户容易把"模拟"当成安全的并行操作，实际它和真迁移共用同一套流程）。
        task = self._busy_task_name()
        if task:
            messagebox.showwarning(
                "提示",
                f"正在执行「{task}」，为避免两个任务同时改动同一批文件，"
                "请等它结束后再开始迁移（模拟运行同样需要等待）。")
            self.log(f"⚠️ 已拦截：{task} 进行中，暂不允许启动迁移", level="WARNING")
            return

        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        world = self.world_name.get().strip()
        if not src or not tgt:
            messagebox.showerror("错误", "请选择源和目标实例根目录")
            return
        if not world:
            messagebox.showerror("错误", "请输入存档名称")
            return

        src_path = Path(src)
        tgt_path = Path(tgt)
        if not src_path.exists():
            messagebox.showerror("错误", f"源路径不存在：{src}")
            return
        if not tgt_path.exists():
            messagebox.showerror("错误", f"目标路径不存在：{tgt}")
            return

        modlist_raw = self.mod_text.get(1.0, tk.END).splitlines()
        modlist = [line.strip() for line in modlist_raw if line.strip() and not line.strip().startswith("#")]

        configlist_raw = self.config_text.get(1.0, tk.END).splitlines()
        configlist = []
        for line in configlist_raw:
            line = line.strip()
            if line and not line.startswith("#"):
                if _is_safe_path(line):
                    configlist.append(line)
                else:
                    self.log(f"⚠️ 跳过不安全 config 路径: {line}", level="WARNING")
                    messagebox.showwarning("不安全路径",
                                           f"Config 清单中的 '{line}' 包含 '..'，已自动跳过。")

        # 其它文件清单：路径相对整合包根目录，安全校验和 config 一样
        extralist_raw = self.extra_text.get(1.0, tk.END).splitlines()
        extralist = []
        for line in extralist_raw:
            line = line.strip().replace("\\", "/")
            if line and not line.startswith("#"):
                if _is_safe_path(line):
                    extralist.append(line.rstrip("/") or line)
                else:
                    self.log(f"⚠️ 跳过不安全的其它文件路径: {line}", level="WARNING")

        if not modlist and not configlist and not extralist:
            messagebox.showwarning("提示", "三个清单都是空的，没有可迁移的内容。")
            return

        # 「其它文件」清单是**唯一依据**：以前这里会把「默认携带的目录」自动并进这次
        # 迁移的临时清单（不写回用户清单），用户要求"一切都要自己选" —— 那套删了。
        # 想带常用目录，去清单页用「＋ 常用目录」加进清单：看得见、能删、有备份策略。

        # 计算要复制的文件数与总大小
        total_files, total_size = self._calculate_migration_stats(
            src_path, tgt_path, world, modlist, configlist, extralist)
        self.log(f"📦 待迁移文件 {total_files} 个，总大小 {total_size / 1024 / 1024:.1f} MB",
                 level="INFO")

        if total_files == 0:
            messagebox.showinfo("提示", "没有找到需要复制的文件，请检查清单。")
            return

        # 磁盘空间检查（目标盘需容纳 迁移数据 + 目标备份，附带余量）
        ok, free, needed = self._check_disk_space(tgt_path, total_size)
        if free < 0:
            self.log("⚠️ 无法读取目标磁盘信息，已跳过空间检查", level="WARNING")
        elif not ok:
            messagebox.showerror(
                "磁盘空间不足",
                f"目标磁盘剩余空间 {free / 1024 / 1024:.1f} MB，"
                f"本次迁移约需 {needed / 1024 / 1024:.1f} MB（含备份余量）。\n"
                "空间不足，请清理目标磁盘后重试。")
            return

        # 正式迁移前再确认一次（模拟运行不用 —— 它不改任何文件）。设置里可以关掉。
        if not self.dry_run.get() and self.confirm_migrate.get():
            确认框 = _打桩优先("ask_migrate_confirm", ask_migrate_confirm)
            if not 确认框(self.root, self.theme, {
                    "源": src, "目标": tgt, "存档": world,
                    "模组": len(modlist), "config": len(configlist),
                    "其它文件": len(extralist), "文件数": total_files,
                    "大小MB": total_size / 1024 / 1024,
                    "覆盖模组": self.overwrite_mods.get(),
                    "其它文件冲突": self.extra_conflict.get()}):
                self.log("ℹ️ 已取消：确认框里点了「取消」，没有动任何文件。", level="INFO")
                return

        # 置为"迁移中"，禁用相关按钮、并给主窗口上锁，防止重复触发/误操作
        self._migration_running = True
        self._clear_fail_marks()          # 新一次迁移，先把上次的红标和失败列表清掉
        self._mig_t0 = time.time()        # 完成态总结要算耗时
        self._mig_plan = (len(modlist), len(configlist), len(extralist))
        self._refresh_busy_state()
        self._lock_main_window("正在执行迁移任务" if not self.dry_run.get()
                              else "正在执行迁移任务（模拟运行）")

        # 模拟模式
        if self.dry_run.get():
            self.log("========== 开始迁移（模拟） ==========", level="INFO")
            self.log(f"旧版目录（源）: {src}", level="INFO")
            self.log(f"新版目录（目标）: {tgt}", level="INFO")
            self.log(f"存档名称: {world}", level="INFO")
            self.log("模拟模式: 是", level="INFO")
            self.log("不会实际修改任何文件", level="INFO")
            thread = threading.Thread(
                target=self._run_migration_thread,
                args=(src_path, tgt_path, world, modlist, configlist, True,
                      self.overwrite_mods.get(), extralist,
                      self.extra_conflict.get() != "skip")
            )
            thread.daemon = True
            thread.start()
            return

        # 实际迁移：先备份。**要把这次迁移的「其它文件」清单一起传进去** ——
        # 那些条目迁移时会被覆盖/新建，没备份的话"覆盖（先备份）"就是空话、回滚也还原不了
        try:
            _打桩优先("do_backup", do_backup)(
                tgt_path, log_func=self.log, extra_entries=extralist)
        except Exception as e:
            self.log(f"❌ 备份失败：{e}", level="ERROR")
            messagebox.showerror("备份错误", f"备份目标实例失败：{e}\n迁移已取消。")
            self._migration_running = False
            self._unlock_main_window()
            self._refresh_busy_state()
            return

        if self.silent_background:
            # 后台静默执行：不弹进度窗口，也不建队列/轮询，全过程只写日志
            self.progress_queue = None
            self.progress_window = None
            self.log("🤫 后台静默执行已开启：不显示进度窗口，完成后用系统通知提醒",
                     level="INFO")
            self._silent_notify_on_finish = True
        else:
            self.progress_queue = queue.Queue()
            self.progress_window = ProgressWindow(self.root, total_files, total_size)
            if self.after_id is None:
                self._poll_progress()

        thread = threading.Thread(
            target=self._run_migration_thread,
            args=(src_path, tgt_path, world, modlist, configlist, False,
                  self.overwrite_mods.get(), extralist,
                  self.extra_conflict.get() != "skip")
        )
        thread.daemon = True
        thread.start()

    def _calculate_migration_stats(self, src_path, tgt_path, world, modlist,
                                   configlist, extralist=None):
        total_files = 0
        total_size = 0

        src_mods = src_path / "mods"
        if src_mods.exists():
            source_files = {f.name: f for f in src_mods.glob("*.jar")}
            for mod in modlist:
                matched = match_mod(mod, source_files, {})
                if matched:
                    total_files += 1
                    total_size += (src_mods / matched).stat().st_size

        src_world = src_path / "saves" / world
        if src_world.exists():
            for f in src_world.rglob("*"):
                if f.is_file():
                    total_files += 1
                    total_size += f.stat().st_size

        src_config = src_path / "config"
        for entry in configlist:
            src_entry = src_config / entry
            if src_entry.is_file():
                total_files += 1
                total_size += src_entry.stat().st_size
            elif src_entry.is_dir():
                for f in src_entry.rglob("*"):
                    if f.is_file():
                        total_files += 1
                        total_size += f.stat().st_size

        # 其它文件清单：相对整合包根目录（shaderpacks/、options.txt、kubejs/ 这类）
        for entry in (extralist or []):
            src_entry = src_path / str(entry).replace("\\", "/")
            if src_entry.is_file():
                total_files += 1
                total_size += src_entry.stat().st_size
            elif src_entry.is_dir():
                for f in src_entry.rglob("*"):
                    if f.is_file():
                        total_files += 1
                        total_size += f.stat().st_size

        return total_files, total_size

    def _dir_size(self, path):
        """递归计算目录下所有文件大小。"""
        total = 0
        try:
            for f in Path(path).rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def _check_disk_space(self, tgt_path, total_size):
        """检查目标磁盘空间是否足够（迁移数据 + 目标备份，附加余量）。
        返回 (ok, free_bytes, needed_bytes)；无法读取磁盘时返回 (True, -1, -1)。"""
        try:
            free = shutil.disk_usage(str(tgt_path)).free
        except Exception:
            return True, -1, -1
        # 备份需容纳目标实例当前大小，迁移需写入 total_size；加 1.2 倍余量 + 100MB 缓冲。
        target_size = self._dir_size(tgt_path)
        needed = int((total_size + target_size) * 1.2) + 100 * 1024 * 1024
        return free >= needed, free, needed

    def _run_migration_thread(self, src_path, tgt_path, world, modlist, configlist,
                              dry_run, overwrite, extralist=None, extra_overwrite=True):
        """迁移工作线程。

        overwrite / extra_overwrite 由主线程读好再传进来：Tk 变量只能在主线程碰，
        子线程直接 self.overwrite_mods.get() 会抛 "main thread is not in main loop"。
        """
        def progress_callback(file_index, file_name, copied_bytes, step=None):
            if file_index is None:
                # 这是迁移结束的哨兵：以前这里直接 return 把它吞了，_poll_progress
                # 永远等不到 None —— 进度窗口（还是 grab_set 的）跑完也不关，
                # 界面就那样卡在"迁移进度"上不收。
                if self.progress_queue:
                    self.progress_queue.put(None)
                return
            if self.progress_queue:
                self.progress_queue.put((file_index, file_name, copied_bytes, step))

        def check_cancel():
            return self.progress_window and self.progress_window.cancelled

        try:
            run_migration(
                src_path=src_path,
                tgt_path=tgt_path,
                world_name=world,
                modlist=modlist,
                configlist=configlist,
                dry_run=dry_run,
                overwrite=overwrite,
                progress_callback=progress_callback,
                log_callback=self.log,
                check_cancel=check_cancel,
                add_history=True,
                # 迁移标记：只改目标文件名，加载器认的是 jar 里的 modid，不受影响
                rename_marker=(self.rename_marker
                               if getattr(self, "rename_migrated_mods", False) else None),
                # 其它文件清单（相对整合包根目录）+ 同名冲突策略
                extralist=extralist,
                extra_overwrite=bool(extra_overwrite)
            )
        finally:
            self._migration_running = False
            # 收尾交给"锁屏完成态"：写总结 → 边框红转绿 → 等用户按任意键。
            # 这里是**迁移线程**，不能直接碰 Tk —— 丢给主线程的 UI 泵执行。
            self._ui_post(self._finish_migration_lock)
            self._ui_post(self._refresh_busy_state)
            # 静默模式下没有进度窗口来宣布结束，这里补一条系统通知
            if getattr(self, "_silent_notify_on_finish", False):
                self._silent_notify_on_finish = False
                self._ui_post(lambda: self._notify_task_done(
                    "迁移", "后台静默执行已结束，点托盘图标查看日志", force=True))

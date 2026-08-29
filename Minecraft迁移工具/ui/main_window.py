# ui/main_window.py
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter.font as tkfont
import json
import time
import os
import sys
import queue
import threading
import shutil
from pathlib import Path
import subprocess
import re
from utils.config import CONFIG_FILE
from utils.theme import LIGHT_THEME, DARK_THEME, apply_theme_to_widget_tree
from utils.helpers import create_gradient_button, set_window_icon
from core.migrator import (
    run_migration,
    do_backup,
    do_restore,
    load_history,
    mark_rollback,
    _is_safe_path,
    match_mod
)
from core.scanner import scan_mod_differences, get_full_mod_metadata
from ui.dialogs import ProgressWindow, ScanProgressWindow
from ui.diff_window import show_diff_window
from ui.diff_window import show_diff_window, update_diff_theme


def _grad_width(text):
    """按文字测量渐变按钮宽度（微软雅黑 9 粗体 + 内边距）。"""
    return int(tkfont.Font(family="微软雅黑", size=9, weight="bold").measure(text)) + 26


class MigrationGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft 整合包迁移工具 - 增强版 v3")
        self.root.geometry("1000x1080")

        self.config = self.load_config()
        self.edit_mode = tk.BooleanVar(value=self.config.get("edit_enabled", False))
        self.current_theme = self.config.get("theme", "light")
        self.theme = LIGHT_THEME if self.current_theme == "light" else DARK_THEME

        self.source_path = tk.StringVar(value=self.config.get("source", ""))
        self.target_path = tk.StringVar(value=self.config.get("target", ""))
        self.world_name = tk.StringVar(value=self.config.get("world", "老子的世界"))
        self.dry_run = tk.BooleanVar(value=self.config.get("dry_run", True))
        self.overwrite_mods = tk.BooleanVar(value=self.config.get("overwrite", False))

        self.last_check_modlist_time = 0

        self.create_widgets()
        self.init_log_colors()
        self.apply_theme()

        # 实时检测存档：输入存档名/切换源路径时即时刷新"存档是否存在"状态
        self.world_name.trace_add("write", lambda *a: self._update_world_status())
        self.source_path.trace_add("write", lambda *a: self._update_world_status())
        self._update_world_status()

        self.mod_text.insert("1.0", self.config.get("mod_list", ""))
        self.config_text.insert("1.0", self.config.get("config_list", ""))
        self.mod_text.edit_reset()
        self.config_text.edit_reset()
        self.log("=" * 60, level="INFO", save=False)
        self.log("【免费声明】本工具完全免费，严禁用于商业用途或转卖。", level="WARNING", save=False)
        self.log("如有任何收费行为，请立即举报。作者不会以任何形式向你收费。", level="WARNING", save=False)
        self.log("=" * 60, level="INFO", save=False)

        self.progress_queue = None
        self.progress_window = None
        self.after_id = None
        self._migration_running = False
        self.diff_window = None
        self._scanning = False
        self.on_path_change()
        self._update_text_states()
        self._log_cache_limit = 500
        self._saved_logs = []

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {}

    def save_config(self):
        config = {
            "source": self.source_path.get(),
            "target": self.target_path.get(),
            "world": self.world_name.get(),
            "dry_run": self.dry_run.get(),
            "overwrite": self.overwrite_mods.get(),
            "theme": self.current_theme,
            "mod_list": self.mod_text.get("1.0", tk.END).strip(),
            "config_list": self.config_text.get("1.0", tk.END).strip(),
            "edit_enabled": self.edit_mode.get(),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except:
            pass
        self._check_overflow()

    def init_log_colors(self):
        self.log_text.tag_config("INFO", foreground="gray")
        self.log_text.tag_config("WARNING", foreground="orange")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("SUCCESS", foreground="green")
        self.log_text.tag_config("SIMULATE", foreground="blue")

    def apply_theme(self):
        self.root.configure(bg=self.theme["bg"])
        # 配置 ttk 样式
        style = ttk.Style()
        # 使用 'clam' 主题以支持更多自定义
        if style.theme_use() != 'clam':
            try:
                style.theme_use('clam')
            except:
                pass

        # Treeview 样式
        style.configure(
            "Treeview",
            background=self.theme["ttk_bg"],
            foreground=self.theme["ttk_fg"],
            fieldbackground=self.theme["ttk_bg"],
            selectbackground=self.theme["ttk_select_bg"],
            selectforeground=self.theme["ttk_select_fg"],
            borderwidth=0
        )
        style.map(
            "Treeview",
            background=[('selected', self.theme["ttk_select_bg"])],
            foreground=[('selected', self.theme["ttk_select_fg"])]
        )

        # Treeview.Heading（列标题）
        style.configure(
            "Treeview.Heading",
            background=self.theme["button_bg"],
            foreground=self.theme["fg"],
            relief="flat"
        )

        # Progressbar 样式
        style.configure(
            "Horizontal.TProgressbar",
            background=self.theme["ttk_progress_bg"],
            troughcolor=self.theme["ttk_trough_bg"],
            bordercolor=self.theme["bg"],
            lightcolor=self.theme["ttk_progress_bg"],
            darkcolor=self.theme["ttk_progress_bg"]
        )

        # Combobox 样式
        style.configure(
            "TCombobox",
            fieldbackground=self.theme["ttk_field_bg"],
            background=self.theme["ttk_bg"],
            foreground=self.theme["ttk_fg"],
            arrowcolor=self.theme["ttk_fg"]
        )
        style.map(
            "TCombobox",
            fieldbackground=[('readonly', self.theme["ttk_field_bg"])],
            background=[('readonly', self.theme["ttk_bg"])],
            foreground=[('readonly', self.theme["ttk_fg"])]
        )

        # Scrollbar 样式
        style.configure(
            "Vertical.TScrollbar",
            background=self.theme["button_bg"],
            troughcolor=self.theme["bg"],
            arrowcolor=self.theme["fg"],
            bordercolor=self.theme["bg"]
        )
        style.configure(
            "Horizontal.TScrollbar",
            background=self.theme["button_bg"],
            troughcolor=self.theme["bg"],
            arrowcolor=self.theme["fg"],
            bordercolor=self.theme["bg"]
        )
        apply_theme_to_widget_tree(self.root, self.theme)

        # 同步"放大查看"结果表的行标签颜色
        for bv in getattr(self, '_big_view_trees', []):
            try:
                if bv.winfo_exists():
                    bv.tag_configure("missing", background=self.theme.get("danger_bg", "#ffb3b3"),
                                     foreground=self.theme.get("danger_fg", "#8b0000"))
                    bv.tag_configure("checked", background=self.theme.get("sel_bg", "#a5d6a7"),
                                     foreground=self.theme.get("sel_fg", "#000000"))
                    bv.tag_configure("new", background=self.theme.get("warn_bg", "#ffeaa7"),
                                     foreground=self.theme.get("warn_fg", "#000000"))
                    bv.tag_configure("odd", background=self.theme.get("neutral_bg", "#f8f9fa"),
                                     foreground=self.theme.get("neutral_fg", "#000000"))
            except Exception:
                pass

        if hasattr(self, 'bottom_frame'):
            self.bottom_frame.configure(bg=self.theme["bottom_bg"])
        if hasattr(self, 'warning_frame'):
            self.warning_frame.configure(bg=self.theme["warning_bg"])
            self.warning_label.configure(bg=self.theme["warning_bg"],
                                         fg=self.theme["warning_fg"])
        if hasattr(self, 'log_text'):
            self.log_text.configure(bg=self.theme["log_bg"], fg=self.theme["log_fg"])
        if hasattr(self, 'source_status'):
            self.source_status.configure(bg=self.theme["bg"])
        if hasattr(self, 'target_status'):
            self.target_status.configure(bg=self.theme["bg"])
        if hasattr(self, 'world_status'):
            self.world_status.configure(bg=self.theme["bg"])
        self._check_overflow()

    def toggle_theme(self):
        if self.current_theme == "light":
            self.current_theme = "dark"
            self.theme = DARK_THEME
        else:
            self.current_theme = "light"
            self.theme = LIGHT_THEME
        self.apply_theme()
        self.on_path_change()
        self.save_config()
        self.log(f"主题已切换为{'深色' if self.current_theme == 'dark' else '浅色'}模式",
                 level="SUCCESS", save=False)

        # 更新已打开的差异窗口
        if hasattr(self, 'diff_window') and self.diff_window is not None:
            if self.diff_window.winfo_exists():
                from ui.diff_window import update_diff_theme
                update_diff_theme(self.diff_window, self.theme, self.current_theme)
    # ---------- 工具函数 ----------
    def create_tooltip(self, widget, text):
        def enter(event):
            self.tooltip = tk.Toplevel(widget)
            self.tooltip.wm_overrideredirect(True)
            self.tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
            label = tk.Label(self.tooltip, text=text,
                             background=self.theme["tooltip_bg"], fg=self.theme["label_fg"], relief="solid", borderwidth=1, font=("微软雅黑", 9))
            label.pack()
        def leave(event):
            if hasattr(self, 'tooltip'):
                self.tooltip.destroy()
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    def _is_valid_instance(self, path_str):
        """
        严格检查路径是否为有效的 Minecraft 整合包实例（借鉴 PCL2 验证逻辑）
        返回: (is_valid, reason, details_dict)
        """
        p = Path(path_str)
        details = {}

        # ----- 第1层：基础路径检查 -----
        if not p.exists():
            return False, "路径不存在", details
        if not p.is_dir():
            return False, "不是目录", details

        # 检查读写权限（尝试创建临时文件）
        try:
            test_file = p / ".permission_test"
            test_file.touch()
            test_file.unlink()
            details["read_write"] = True
        except:
            details["read_write"] = False
            return False, "无读写权限，请以管理员身份运行", details

        # 检查路径是否包含中文（警告级别）
        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in str(p))
        if has_chinese:
            details["has_chinese"] = True
            # 不是致命错误，但给出警告

        # ----- 第2层：核心标识文件检查 -----
        # 2.1 检查关键子目录
        has_mods = (p / "mods").exists() and (p / "mods").is_dir()
        has_config = (p / "config").exists() and (p / "config").is_dir()
        has_saves = (p / "saves").exists() and (p / "saves").is_dir()
        has_libraries = (p / "libraries").exists() and (p / "libraries").is_dir()
        has_versions = (p / "versions").exists() and (p / "versions").is_dir()

        details["has_mods"] = has_mods
        details["has_config"] = has_config
        details["has_saves"] = has_saves
        details["has_libraries"] = has_libraries

        # 2.2 检查 Minecraft 核心标识文件
        has_options = (p / "options.txt").exists()
        has_launcher_profiles = (p / "launcher_profiles.json").exists()

        details["has_options"] = has_options
        details["has_launcher_profiles"] = has_launcher_profiles

        # 2.3 检查版本目录下的核心文件
        version_dirs = []
        if has_versions:
            for v_dir in (p / "versions").iterdir():
                if v_dir.is_dir():
                    version_json = v_dir / "version.json"
                    if version_json.exists():
                        version_dirs.append(v_dir.name)
            details["valid_versions"] = version_dirs

        # 2.4 检查 Fabric/Forge 标识（如果存在 mods 目录）
        if has_mods:
            mods_dir = p / "mods"
            jar_files = list(mods_dir.glob("*.jar"))
            details["mod_count"] = len(jar_files)
            # 检查是否有 Fabric 或 Forge 模组
            fabric_mods = list(mods_dir.glob("*fabric*.jar")) + list(mods_dir.glob("*.fabric.mod.json*"))
            forge_mods = list(mods_dir.glob("*forge*.jar"))
            details["fabric_mods"] = len(fabric_mods) > 0
            details["forge_mods"] = len(forge_mods) > 0

        # 2.5 检查 PCL2 特有标识
        has_pcl_ini = (p / "PCL.ini").exists()
        details["has_pcl_ini"] = has_pcl_ini

        # ----- 第3层：综合判断 -----
        # 判断标准：
        # 1. 必须有 mods 和 config（整合包基本要素）
        if not has_mods:
            return False, "缺少 mods 目录（不是有效的整合包）", details
        if not has_config:
            return False, "缺少 config 目录（不是有效的整合包）", details

        # 2. mods 目录不能为空
        if details.get("mod_count", 0) == 0:
            return False, "mods 目录为空（没有模组文件）", details

        # 3. 必须有至少一个有效版本（有 version.json）
        if not version_dirs:
            # 如果没有 version.json，但 options.txt 存在，可能是旧版整合包
            if not has_options:
                return False, "缺少 version.json 或 options.txt，无法识别为有效实例", details

        # 通过所有检查
        details["is_valid"] = True
        return True, "✅ 有效实例目录", details

    def validate_path(self, path_str, status_label, label_text):
        """
        验证路径是否为有效的 Minecraft 整合包实例（增强版）
        """
        if not path_str:
            status_label.config(text="（未选择）", fg=self.theme["muted_fg"])
            return

        is_valid, reason, details = self._is_valid_instance(path_str)

        # 构建详细状态信息
        status_text = reason
        if is_valid:
            # 显示更多细节
            details_text = []
            if details.get("has_saves"):
                details_text.append("有存档")
            if details.get("mod_count", 0) > 0:
                details_text.append(f"{details['mod_count']}个模组")
            if details.get("valid_versions"):
                details_text.append(f"版本: {', '.join(details['valid_versions'][:3])}")
            if details.get("has_launcher_profiles"):
                details_text.append("✅ 官方启动器")
            if details.get("has_pcl_ini"):
                details_text.append("✅ PCL2")
            if details.get("fabric_mods"):
                details_text.append("Fabric")
            if details.get("forge_mods"):
                details_text.append("Forge")

            # 如果有警告信息（如中文路径），在状态标签中显示
            if details.get("has_chinese"):
                status_text = "✅ 有效（⚠️ 路径含中文，建议改为纯英文）"
            elif details_text:
                status_text = f"✅ 有效 ({', '.join(details_text[:4])})"
            else:
                status_text = "✅ 有效实例目录"

            status_label.config(text=status_text, fg=self.theme["ok_fg"])

            # 记录详细验证信息到日志（可选）
            # self.log(f"路径验证通过: {path_str}", level="INFO")
            # self.log(f"  详细信息: {details}", level="INFO")
        else:
            status_label.config(text=f"❌ {reason}", fg=self.theme["fail_fg"])

    def on_path_change(self, *args):
        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        self.validate_path(src, self.source_status, "源")
        self.validate_path(tgt, self.target_status, "目标")
        self._update_world_status()
        self.save_config()

    # ---------- 日志 ----------
    def log(self, message, level="INFO", save=True):
        def _log():
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, message + "\n", level)
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
            self.root.update_idletasks()

        if save:
            if not hasattr(self, '_saved_logs'):
                self._saved_logs = []
            self._saved_logs.append(message + "\n")
            if len(self._saved_logs) >= self._log_cache_limit:
                log_file = Path.home() / ".minecraft_migrate_last_log.txt"
                try:
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write("".join(self._saved_logs))
                    self._saved_logs = []
                except:
                    pass

        self.root.after(0, _log)

    def clear_log(self):
        if not hasattr(self, '_saved_logs') or not self._saved_logs:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")
            self.log("📋 日志已清空（无有效操作记录，不保存文件）", level="INFO", save=False)
            self.mod_text.edit_reset()
            self.mod_text.edit_modified(False)
            return

        log_file = Path.home() / ".minecraft_migrate_last_log.txt"
        try:
            mode = 'a' if log_file.exists() else 'w'
            with open(log_file, mode, encoding='utf-8') as f:
                if mode == 'a':
                    f.write("\n" + "=" * 50 + "\n")
                    f.write(f"--- 新日志记录 ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
                f.write("".join(self._saved_logs))

            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")
            self.mod_text.edit_reset()
            self.mod_text.edit_modified(False)
            self.log(f"📋 日志已清空，有效操作记录已追加至 {log_file}", level="INFO", save=False)
            self._saved_logs = []
        except Exception as e:
            self.log(f"❌ 日志保存失败：{e}", level="ERROR", save=False)

    def open_log_folder(self):
        log_file = Path.home() / ".minecraft_migrate_last_log.txt"
        folder = log_file.parent
        if not folder.exists():
            messagebox.showwarning("提示", "日志文件夹不存在，请先执行操作产生日志。")
            return

        try:
            if sys.platform == 'win32':
                if log_file.exists():
                    subprocess.Popen(['explorer', '/select,', str(log_file)])
                else:
                    os.startfile(str(folder))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder)])
            else:
                subprocess.Popen(['xdg-open', str(folder)])
            self.log(f"📂 已打开日志文件夹：{folder}", level="INFO")
        except Exception as e:
            self.log(f"❌ 打开文件夹失败：{e}", level="ERROR")
            messagebox.showerror("错误", f"无法打开文件夹：{e}")

    # ---------- 界面构建（由于太长，拆分为多个辅助方法） ----------
    def create_widgets(self):
        # 顶部栏
        top_bar = tk.Frame(self.root)
        top_bar.pack(fill="x", padx=10, pady=5)
        self.theme_btn = create_gradient_button(
            top_bar, "🌓 切换主题", self.toggle_theme,
            colors=("#8e24aa", "#ab47bc"), hover_colors=("#ab47bc", "#8e24aa"),
            width=_grad_width("🌓 切换主题"), height=30, font=("微软雅黑", 9, "bold"))
        self.theme_btn.pack(side="right", padx=5)

        # 警告横幅
        self.warning_frame = tk.Frame(self.root, relief=tk.RIDGE, bd=2)
        self.warning_frame.pack(fill="x", padx=10, pady=(5, 0))
        self.warning_label = tk.Label(self.warning_frame,
                                      text="⚠️ 本工具完全免费，请勿上当受骗！如遇收费行为，请立即举报。⚠️",
                                      font=("微软雅黑", 10, "bold"))
        self.warning_label.pack(pady=5)

        # 信息提示
        info_frame = tk.Frame(self.root)
        info_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(info_frame,
                 text="【重要】请选择实例根目录（例如 D:\\.minecraft\\versions\\游戏名），该目录下应直接包含 mods、saves、options.txt 等",
                 fg=self.theme["fg"], wraplength=950).pack()
        tk.Label(info_frame, text="👉 迁移方向：从“旧版”复制到“新版”（旧版模组 → 新版模组，保留你的自定义配置）",
                 fg=self.theme["ok_fg"], wraplength=950).pack(pady=(0, 5))

        # ---- 路径选择 ----
        self._create_path_widgets()
        # ---- 模组清单 ----
        self._create_modlist_widgets()
        # ---- Config 清单 ----
        self._create_config_widgets()
        # ---- 底部按钮 ----
        self._create_bottom_widgets()
        # ---- 日志 ----
        self._create_log_widgets()

        # 绑定事件
        self.source_path.trace_add("write", self.on_path_change)
        self.target_path.trace_add("write", self.on_path_change)
        self.world_name.trace_add("write", lambda *args: self.save_config())
        # 底部声明
        self.bottom_frame = tk.Frame(self.root)
        self.bottom_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(self.bottom_frame, text="本工具完全免费，仅供个人学习交流使用。严禁倒卖或用于商业目的。",
                 font=("微软雅黑", 8)).pack()
    def _create_path_widgets(self):
        # 源目录
        frame_source = tk.LabelFrame(self.root, text="📤 旧版整合包（要迁移出去的源）", padx=5, pady=5)
        frame_source.pack(fill="x", padx=10, pady=5)
        tk.Entry(frame_source, textvariable=self.source_path,
                 width=60).pack(side="left", padx=5)
        btn_source_browse = create_gradient_button(
            frame_source, "📂 浏览...", self.select_source,
            colors=("#607d8b", "#90a4ae"), hover_colors=("#78909c", "#b0bec5"),
            width=_grad_width("📂 浏览..."), height=30, font=("微软雅黑", 9, "bold"))
        btn_source_browse.pack(side="left", padx=5)
        btn_copy = create_gradient_button(
            frame_source, "← 使用新版路径填充", self.copy_target_to_source,
            colors=("#fb8c00", "#ffb74d"), hover_colors=("#ffa726", "#ffcc80"),
            width=_grad_width("← 使用新版路径填充"), height=30, font=("微软雅黑", 9, "bold"))
        btn_copy.pack(side="left", padx=5)
        self.create_tooltip(btn_copy, "将右侧“新版”的路径复制到左侧“旧版”栏，用于快速测试或反向操作")
        self.source_status = tk.Label(frame_source, text="", fg=self.theme["muted_fg"])
        self.source_status.pack(side="left", padx=10)

        # 目标目录
        frame_target = tk.LabelFrame(self.root, text="📥 新版整合包（迁移目的地）", padx=5, pady=5)
        frame_target.pack(fill="x", padx=10, pady=5)
        tk.Entry(frame_target, textvariable=self.target_path,
                 width=70).pack(side="left", padx=5)
        btn_target_browse = create_gradient_button(
            frame_target, "📂 浏览...", self.select_target,
            colors=("#607d8b", "#90a4ae"), hover_colors=("#78909c", "#b0bec5"),
            width=_grad_width("📂 浏览..."), height=30, font=("微软雅黑", 9, "bold"))
        btn_target_browse.pack(side="left", padx=5)
        self.target_status = tk.Label(frame_target, text="", fg=self.theme["muted_fg"])
        self.target_status.pack(side="left", padx=10)

        # 存档名称
        frame_world = tk.LabelFrame(self.root, text="存档文件夹名称", padx=5, pady=5)
        frame_world.pack(fill="x", padx=10, pady=5)
        tk.Entry(frame_world, textvariable=self.world_name, width=40).pack(side="left",
                                                                           padx=5)
        tk.Label(frame_world, text="（例如：新的世界）").pack(side="left")
        self.world_status = tk.Label(frame_world, text="", fg=self.theme["muted_fg"])
        self.world_status.pack(side="left", padx=10)

    def _create_modlist_widgets(self):
        frame_modlist = tk.LabelFrame(self.root, text="需要复制的模组清单（每行一个 .jar 文件名）",
                                      padx=5, pady=5)
        frame_modlist.pack(fill="both", expand=True, padx=10, pady=5)

        self.mod_text = scrolledtext.ScrolledText(frame_modlist, height=8, wrap=tk.NONE,
                                                  undo=True)
        self.mod_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.mod_text.bind("<Control-z>", lambda e: self._safe_undo(self.mod_text))
        self.mod_text.bind("<Control-y>", lambda e: self._safe_redo(self.mod_text))
        # 本会话新添加的模组（文件名小写），主清单用黄色高亮提示
        self._new_mod_keys = set()
        self.mod_text.tag_configure(
            "new", background=self.theme.get("warn_bg", "#ffeaa7"),
            foreground=self.theme.get("warn_fg", "#000000"))
        # 支持从资源管理器拖拽 .jar 到清单
        try:
            from tkinterdnd2 import DND_FILES
            self.mod_text.drop_target_register(DND_FILES)
            self.mod_text.dnd_bind('<<Drop>>', self._on_mod_drop)
        except Exception:
            pass

        edit_toolbar = tk.Frame(frame_modlist, bg=self.theme["edit_bg"],
                                relief=tk.RAISED, bd=2)
        edit_toolbar.pack(fill="x", padx=5, pady=2)
        cb = tk.Checkbutton(edit_toolbar, text="🔓 启用主界面编辑（直接修改清单）",
                            variable=self.edit_mode,
                            command=self.toggle_edit_mode, bg=self.theme["edit_bg"],
                            font=("微软雅黑", 10, "bold"))
        cb.pack(side="left", padx=5)
        warn_label = tk.Label(edit_toolbar, text="⚠️ 编辑模式可能造成数据损坏，请谨慎操作！",
                              fg=self.theme["fail_fg"],
                              bg=self.theme["edit_bg"], font=("微软雅黑", 9))
        warn_label.pack(side="left", padx=10)

        btn_frame = tk.Frame(frame_modlist)
        btn_frame.pack(fill="x", pady=5)

        # 统一渐变按钮（同高度/字体，语义配色，宽度按文字自适应）
        def gw(text):
            return int(tkfont.Font(family="微软雅黑", size=9, weight="bold").measure(text)) + 26

        btn_changelog = create_gradient_button(
            btn_frame, "📥 从变更日志导入（含Updated）", self.import_from_changelog,
            colors=("#00acc1", "#26c6da"), hover_colors=("#26c6da", "#00acc1"),
            width=gw("📥 从变更日志导入（含Updated）"), height=30, font=("微软雅黑", 9, "bold"))
        btn_changelog.pack(side="left", padx=5)
        self.create_tooltip(btn_changelog, "你需要提供的是“崩溃助手”模组给予的mod变更列表")

        self.scan_btn = create_gradient_button(
            btn_frame, "🔍 扫描模组差异", self.action_scan_mod_diff,
            colors=("#00bcd4", "#3f51b5"), hover_colors=("#26c6da", "#5c6bc0"),
            width=gw("🔍 扫描模组差异"), height=30, font=("微软雅黑", 9, "bold"))
        self.mod_magnify_btn = create_gradient_button(
            btn_frame, "📂 放大查看", lambda: self.open_big_view(self.mod_text, "模组清单"),
            colors=("#607d8b", "#90a4ae"), hover_colors=("#78909c", "#b0bec5"),
            width=gw("📂 放大查看"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_mods_btn = create_gradient_button(
            btn_frame, "➕ 添加模组", self.add_mods,
            colors=("#00c853", "#00e676"), hover_colors=("#00e676", "#00c853"),
            width=gw("➕ 添加模组"), height=30, font=("微软雅黑", 9, "bold"))
        self.clear_mods_btn = create_gradient_button(
            btn_frame, "🗑️ 清空清单", self.clear_mod_list,
            colors=("#757575", "#9e9e9e"), hover_colors=("#8d8d8d", "#bdbdbd"),
            width=gw("🗑️ 清空清单"), height=30, font=("微软雅黑", 9, "bold"))
        self.check_mods_btn = create_gradient_button(
            btn_frame, "🔎 检查清单模组是否存在（源目录）", self.check_modlist_existence,
            colors=("#fb8c00", "#ffb74d"), hover_colors=("#ffa726", "#ffcc80"),
            width=gw("🔎 检查清单模组是否存在（源目录）"), height=30, font=("微软雅黑", 9, "bold"))

        self.mod_magnify_btn.pack(side="left", padx=5)
        self.add_mods_btn.pack(side="left", padx=5)
        self.scan_btn.pack(side="left", padx=5)
        self.clear_mods_btn.pack(side="left", padx=5)
        self.check_mods_btn.pack(side="left", padx=5)

    def _create_config_widgets(self):
        frame_config = tk.LabelFrame(self.root,
                                     text="需要迁移的 config 内容（每行一个相对路径，相对于 config 目录）",
                                     padx=5, pady=5)
        frame_config.pack(fill="both", expand=True, padx=10, pady=5)

        warning_config = tk.Label(frame_config,
                                  text="⚠️ 注意：复制将直接覆盖目标 config 中的同名文件/文件夹，请谨慎操作！",
                                  fg=self.theme["fail_fg"], font=("微软雅黑", 9, "bold"))
        warning_config.pack(anchor="w", padx=5, pady=2)

        self.config_text = scrolledtext.ScrolledText(frame_config, height=6,
                                                     wrap=tk.NONE, undo=True)
        self.config_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.config_text.bind("<Control-z>",
                              lambda e: self._safe_undo(self.config_text))
        self.config_text.bind("<Control-y>",
                              lambda e: self._safe_redo(self.config_text))
        # 支持从资源管理器拖拽文件/文件夹到 config 清单
        try:
            from tkinterdnd2 import DND_FILES
            self.config_text.drop_target_register(DND_FILES)
            self.config_text.dnd_bind('<<Drop>>', self._on_config_drop)
        except Exception:
            pass

        btn_config_frame = tk.Frame(frame_config)
        btn_config_frame.pack(fill="x", pady=5)

        self.config_import_btn = create_gradient_button(
            btn_config_frame, "📥 从源 config 导入所有条目", self.import_config_entries,
            colors=("#00acc1", "#26c6da"), hover_colors=("#26c6da", "#00acc1"),
            width=_grad_width("📥 从源 config 导入所有条目"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.config_import_btn.pack(side="left", padx=5)
        self.config_magnify_btn = create_gradient_button(
            btn_config_frame, "📂 放大查看",
            lambda: self.open_big_view(self.config_text, "Config清单"),
            colors=("#607d8b", "#90a4ae"), hover_colors=("#78909c", "#b0bec5"),
            width=_grad_width("📂 放大查看"), height=30, font=("微软雅黑", 9, "bold"))
        self.config_magnify_btn.pack(side="left", padx=5)
        self.add_config_dir_btn = create_gradient_button(
            btn_config_frame, "📁 浏览添加文件夹", self.browse_add_config_entry,
            colors=("#00bcd4", "#3f51b5"), hover_colors=("#26c6da", "#5c6bc0"),
            width=_grad_width("📁 浏览添加文件夹"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_config_dir_btn.pack(side="left", padx=5)
        self.add_config_file_btn = create_gradient_button(
            btn_config_frame, "📄 浏览添加文件", self.browse_add_config_file,
            colors=("#00bcd4", "#3f51b5"), hover_colors=("#26c6da", "#5c6bc0"),
            width=_grad_width("📄 浏览添加文件"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_config_file_btn.pack(side="left", padx=5)
        self.clear_config_btn = create_gradient_button(
            btn_config_frame, "🗑️ 清空 config 清单", self.clear_config,
            colors=("#757575", "#9e9e9e"), hover_colors=("#8d8d8d", "#bdbdbd"),
            width=_grad_width("🗑️ 清空 config 清单"), height=30, font=("微软雅黑", 9, "bold"))
        self.clear_config_btn.pack(side="left", padx=5)

    def _create_bottom_widgets(self):
        opt_frame = tk.Frame(self.root)
        opt_frame.pack(fill="x", padx=10, pady=5)
        self.dry_run_cb = tk.Checkbutton(opt_frame, text="模拟运行（仅显示操作）",
                                         variable=self.dry_run, command=self.save_config)
        self.dry_run_cb.pack(side="left")
        self.overwrite_cb = tk.Checkbutton(opt_frame, text="覆盖已存在的模组",
                                           variable=self.overwrite_mods, command=self.save_config)
        self.overwrite_cb.pack(side="left", padx=20)

        # 右侧按钮组
        btn_group = tk.Frame(opt_frame, bg=self.theme["bg"])
        btn_group.pack(side="right")

        self.start_btn = create_gradient_button(
            parent=btn_group,
            text="🚀 开始迁移",
            command=self.start_migration,
            colors=("#00c853", "#00e676"),
            hover_colors=("#00e676", "#00c853"),
            width=180,
            height=38,
            font=("微软雅黑", 12, "bold")
        )
        self.start_btn.pack(side="right", padx=5)

        self.rollback_btn = create_gradient_button(
            parent=btn_group,
            text="⚠️ 回滚",
            command=self.action_rollback,
            colors=("#e53935", "#ff7043"),
            hover_colors=("#ef5350", "#ff7043"),
            width=160,
            height=38,
            font=("微软雅黑", 11, "bold")
        )
        self.rollback_btn.pack(side="right", padx=5)

        self.history_btn = create_gradient_button(
            parent=btn_group,
            text="📋 查看历史",
            command=self.action_show_history,
            colors=("#00acc1", "#26c6da"),
            hover_colors=("#26c6da", "#00acc1"),
            width=160,
            height=38,
            font=("微软雅黑", 11, "bold")
        )
        self.history_btn.pack(side="right", padx=5)


    def _create_log_widgets(self):
        frame_log = tk.LabelFrame(self.root, text="执行日志", padx=5, pady=5)
        frame_log.pack(fill="both", expand=True, padx=10, pady=5)

        log_toolbar = tk.Frame(frame_log)
        log_toolbar.pack(fill="x", pady=(0, 5))
        btn_clear_log = create_gradient_button(
            log_toolbar, "🗑️ 清空日志", self.clear_log,
            colors=("#757575", "#9e9e9e"), hover_colors=("#8d8d8d", "#bdbdbd"),
            width=_grad_width("🗑️ 清空日志"), height=30, font=("微软雅黑", 9, "bold"))
        btn_clear_log.pack(side="right", padx=5)
        btn_open_log = create_gradient_button(
            log_toolbar, "📂 打开日志文件夹", self.open_log_folder,
            colors=("#607d8b", "#90a4ae"), hover_colors=("#78909c", "#b0bec5"),
            width=_grad_width("📂 打开日志文件夹"), height=30, font=("微软雅黑", 9, "bold"))
        btn_open_log.pack(side="right", padx=5)
        self.log_text = scrolledtext.ScrolledText(frame_log, height=15, wrap=tk.WORD,
                                                  state="disabled")
        self.log_text.pack(fill="both", expand=True)

    # ---------- 路径选择 ----------
    def select_source(self):
        path = filedialog.askdirectory(title="选择源整合包的实例根目录")
        if path:
            self.source_path.set(path)

    def select_target(self):
        path = filedialog.askdirectory(title="选择目标整合包的实例根目录")
        if path:
            self.target_path.set(path)

    def copy_target_to_source(self):
        tgt = self.target_path.get().strip()
        if tgt:
            self.source_path.set(tgt)
            self.log("已将目标路径复制到源路径", level="INFO")
        else:
            self.root.bell()
            self.log("⚠️ 目标路径为空，无法复制", level="WARNING")

    # ---------- 检查存档（实时） ----------
    def _update_world_status(self):
        """实时检测源存档是否存在，只更新状态标签（不弹窗/不打日志，避免输入时刷屏）。"""
        src = self.source_path.get().strip()
        world = self.world_name.get().strip()
        try:
            if not src or not world:
                self.world_status.config(text="", fg=self.theme["muted_fg"])
                return
            src_path = Path(src)
            save_dir = src_path / "saves" / world
            if save_dir.is_dir():
                self.world_status.config(text="✅ 存档已存在", fg=self.theme["ok_fg"])
            else:
                self.world_status.config(text="❌ 存档不存在", fg=self.theme["fail_fg"])
        except Exception:
            self.world_status.config(text="", fg=self.theme["muted_fg"])

    # ---------- 检查模组存在性 ----------
    def check_modlist_existence(self):
        now = time.time()
        if now - self.last_check_modlist_time < 2:
            self.root.bell()
            self.log("⚠️ 请勿频繁操作！请稍后再试。", level="WARNING")
            return
        self.last_check_modlist_time = now

        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_mods = Path(src) / "mods"
        if not src_mods.exists():
            self.root.bell()
            self.log(f"❌ 源 mods 目录不存在：{src_mods}", level="ERROR")
            return

        modlist_raw = self.mod_text.get(1.0, tk.END).splitlines()
        modlist = [line.strip() for line in modlist_raw if line.strip() and not line.strip().startswith("#")]
        if not modlist:
            self.root.bell()
            self.log("⚠️ 当前模组清单为空", level="WARNING")
            return

        source_files = {f.name: f for f in src_mods.glob("*.jar")}
        name_map = {}
        for orig in source_files:
            clean = orig
            if clean.startswith("[") and "]" in clean:
                clean = clean.split("]", 1)[1].strip()
            name_map[clean] = orig
            name_map[orig] = orig

        missing = []
        found = []
        for item in modlist:
            matched = match_mod(item, source_files, name_map)
            if matched:
                found.append(item)
            else:
                missing.append(item)

        self.log(f"📊 模组清单检查结果：总清单项数 {len(modlist)}", level="INFO")
        self.log(f"✅ 存在的模组：{len(found)}", level="SUCCESS")
        self.log(f"❌ 缺失的模组：{len(missing)}", level="ERROR" if missing else "INFO")
        if missing:
            self.root.bell()
            self.log("缺失列表：", level="WARNING")
            for m in missing[:50]:
                self.log(f"  - {m}", level="ERROR")
            if len(missing) > 50:
                self.log(f"  ... 还有 {len(missing)-50} 个未显示", level="WARNING")

    # ---------- 从变更日志导入 ----------
    def import_from_changelog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("从变更日志提取模组清单")
        dialog.geometry("800x600")
        set_window_icon(dialog)
        tk.Label(dialog,
                 text="请粘贴完整的变更日志文本（包含 'Added mods:' 和 'Updated mods:' 部分）：").pack(pady=5)
        text_widget = scrolledtext.ScrolledText(dialog, wrap=tk.WORD, height=20)
        text_widget.pack(fill="both", expand=True, padx=10, pady=5)

        def extract_and_close():
            raw_text = text_widget.get("1.0", tk.END)
            added, updated = self.extract_mods_from_changelog(raw_text)
            if not added and not updated:
                messagebox.showwarning("无结果", "未能提取到模组文件名")
                return

            all_mods = []
            if updated:
                ans = messagebox.askyesnocancel(
                    "发现 Updated mods",
                    f"已提取到 {len(added)} 个 Added 模组，{len(updated)} 个 Updated 模组。\n"
                    "是否将 Updated 模组也添加到复制清单中？\n\n"
                    "点击“是” → 全部添加\n"
                    "点击“否” → 只添加 Added 模组\n"
                    "点击“取消” → 不添加任何模组"
                )
                if ans is None:
                    return
                elif ans:
                    all_mods = added + updated
                else:
                    all_mods = added
            else:
                all_mods = added

            if all_mods:
                self.mod_text.configure(state=tk.NORMAL)
                self.mod_text.delete(1.0, tk.END)
                self.mod_text.insert(tk.END, "\n".join(all_mods))
                self.mod_text.edit_reset()
                self.save_config()
                self._update_text_states()
                self.log(
                    f"从变更日志中提取了 {len(all_mods)} 个模组（Added: {len(added)}, Updated: {len(updated)}）",
                    level="SUCCESS"
                )
                self.save_config()
                self._update_text_states()
                dialog.destroy()
            else:
                messagebox.showinfo("提示", "未添加任何模组")

        btn_extract = create_gradient_button(
            dialog, "提取并应用", extract_and_close,
            colors=("#00c853", "#00e676"), hover_colors=("#00e676", "#00c853"),
            width=_grad_width("提取并应用"), height=30, font=("微软雅黑", 9, "bold"))
        btn_extract.pack(pady=10)
        apply_theme_to_widget_tree(dialog, self.theme)

    def extract_mods_from_changelog(self, text):
        lines = text.splitlines()
        added = []
        updated = []
        added_pattern = re.compile(r'^[\s]*\+[\s]*(.+\.jar)', re.IGNORECASE)
        updated_pattern = re.compile(r'^[\s]*\-[\s]*(.+\.jar)', re.IGNORECASE)
        for line in lines:
            line_stripped = line.strip()
            m = added_pattern.match(line_stripped)
            if m:
                added.append(m.group(1))
                continue
            m = updated_pattern.match(line_stripped)
            if m:
                updated.append(m.group(1))
                continue
            if re.match(r"^Added\s+mods[:：]", line_stripped, re.IGNORECASE):
                continue
            if re.match(r"^Updated\s+mods[:：]", line_stripped, re.IGNORECASE):
                continue
            if line_stripped.endswith(".jar"):
                if not (line_stripped.startswith("Added") or line_stripped.startswith("Updated") or
                        line_stripped.startswith("Removed")):
                    added.append(line_stripped)
        return added, updated

    # ---------- Config 清单相关 ----------
    def import_config_entries(self):
        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
            return

        entries = []
        for item in src_config.iterdir():
            entries.append(item.name)

        if entries:
            self.config_text.configure(state=tk.NORMAL)
            self.config_text.delete(1.0, tk.END)
            self.config_text.insert(tk.END, "\n".join(entries))
            self.log(f"✅ 已从源 config 导入 {len(entries)} 个条目（文件/文件夹）", level="SUCCESS")
            self.save_config()
            self._update_text_states()
            self.config_text.edit_reset()
            self.config_text.edit_modified(False)
            self._notify_config_change()
            messagebox.showinfo("导入成功", f"✅ 已从源 config 导入 {len(entries)} 个条目。",
                               parent=self.root)
        else:
            self.log("ℹ️ 源 config 目录为空，无条目可导入", level="INFO")
            messagebox.showinfo("提示", "源 config 目录为空，无条目可导入。", parent=self.root)

    def browse_add_config_entry(self):
        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
            return

        selected = filedialog.askdirectory(title="请选择源 config 下的文件夹",
                                           initialdir=str(src_config))
        if not selected:
            return
        selected_path = Path(selected)
        try:
            rel_path = selected_path.relative_to(src_config)
        except ValueError:
            self.log(f"❌ 选择的路径不在源 config 目录下: {selected}", level="ERROR")
            return

        if not _is_safe_path(str(rel_path)):
            self.log("❌ 拒绝：路径包含 '..' 或为绝对路径，不安全", level="ERROR")
            messagebox.showerror("不安全路径", "所选路径包含 '..'，可能越界，已拒绝。")
            return

        self.config_text.configure(state=tk.NORMAL)
        if self.config_text.get("1.0", tk.END).strip():
            self.config_text.insert(tk.END, "\n")
        self.config_text.insert(tk.END, str(rel_path) + "\n")
        self.log(f"✅ 已添加 config 条目: {rel_path}", level="SUCCESS")
        self.save_config()
        self._update_text_states()
        self._notify_config_change()
        messagebox.showinfo("添加成功", f"✅ 已添加 config 条目：{rel_path}", parent=self.root)

    def browse_add_config_file(self):
        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
            return

        selected = filedialog.askopenfilename(
            title="请选择源 config 下的文件",
            initialdir=str(src_config),
            filetypes=[("所有文件", "*.*")]
        )
        if not selected:
            return
        selected_path = Path(selected)
        try:
            rel_path = selected_path.relative_to(src_config)
        except ValueError:
            self.log(f"❌ 选择的文件不在源 config 目录下: {selected}", level="ERROR")
            return

        if not _is_safe_path(str(rel_path)):
            self.log("❌ 拒绝：路径包含 '..' 或为绝对路径，不安全", level="ERROR")
            messagebox.showerror("不安全路径", "所选路径包含 '..'，可能越界，已拒绝。")
            return

        self.config_text.configure(state=tk.NORMAL)
        if self.config_text.get("1.0", tk.END).strip():
            self.config_text.insert(tk.END, "\n")
        self.config_text.insert(tk.END, str(rel_path) + "\n")
        self.log(f"✅ 已添加 config 条目: {rel_path}", level="SUCCESS")
        self.save_config()
        self._update_text_states()
        self._notify_config_change()
        messagebox.showinfo("添加成功", f"✅ 已添加 config 条目：{rel_path}", parent=self.root)

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

        hist_win = tk.Toplevel(self.root)
        hist_win.title("迁移历史记录")
        hist_win.geometry("900x500")
        hist_win.transient(self.root)
        set_window_icon(hist_win)
        tk.Label(hist_win, text=f"目标实例：{target_path}", font=("微软雅黑", 9,
                                                             "bold")).pack(pady=5)

        columns = ("时间", "来源", "模组数", "Config数", "状态")
        tree = ttk.Treeview(hist_win, columns=columns, show="headings", height=18)
        # 配置 Treeview 样式（使用当前主题）
        style = ttk.Style()
        if style.theme_use() != 'clam':
            try:
                style.theme_use('clam')
            except:
                pass
        style.configure(
            "History.Treeview",
            background=self.theme["ttk_bg"],
            foreground=self.theme["ttk_fg"],
            fieldbackground=self.theme["ttk_bg"],
            selectbackground=self.theme["ttk_select_bg"],
            selectforeground=self.theme["ttk_select_fg"]
        )
        style.configure(
            "History.Treeview.Heading",
            background=self.theme["button_bg"],
            foreground=self.theme["fg"]
        )

        tree = ttk.Treeview(
            hist_win,
            columns=columns,
            show="headings",
            height=18,
            style="History.Treeview"
        )
        tree.heading("时间", text="迁移时间")
        tree.heading("来源", text="来源路径")
        tree.heading("模组数", text="模组数")
        tree.heading("Config数", text="Config数")
        tree.heading("状态", text="状态")

        tree.column("时间", width=160)
        tree.column("来源", width=400)
        tree.column("模组数", width=70, anchor="center")
        tree.column("Config数", width=70, anchor="center")
        tree.column("状态", width=100, anchor="center")

        scrollbar = ttk.Scrollbar(hist_win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True, padx=10, pady=5)
        scrollbar.pack(side="right", fill="y")

        for entry in reversed(history):
            status_text = "✅ 已回滚" if entry.get("rolled_back", False) else "🟢 正常"
            tags = ("rolled_back",) if entry.get("rolled_back", False) else ("normal",)
            tree.insert("", "end", values=(
                entry.get("timestamp", "?"),
                entry.get("source", "?"),
                entry.get("mod_count", 0),
                entry.get("config_count", 0),
                status_text
            ), tags=tags)

        tree.tag_configure("rolled_back", background=self.theme["badge_rollback_bg"])
        tree.tag_configure("normal", background=self.theme["badge_normal_bg"])
        btn_close_hist = create_gradient_button(
            hist_win, "关闭", hist_win.destroy,
            colors=("#757575", "#9e9e9e"), hover_colors=("#8d8d8d", "#bdbdbd"),
            width=_grad_width("关闭"), height=30, font=("微软雅黑", 9, "bold"))
        btn_close_hist.pack(pady=10)
        apply_theme_to_widget_tree(hist_win, self.theme)

    # ---------- 回滚 ----------
    def action_rollback(self):
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

        backup_root = tgt_path / ".migrate_backup"
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
                "此操作不可撤销！\n确定要继续吗？"
        ):
            self.log("❌ 用户取消了回滚操作", level="WARNING")
            return

        self.log("✅ 用户确认回滚，开始执行...", level="SUCCESS")
        success = do_restore(tgt_path, log_func=self.log)
        if success:
            mark_rollback(tgt_path)
            self.log("📝 已标记本次回滚到历史记录", level="INFO")
            messagebox.showinfo("回滚完成", "目标实例已恢复到迁移前的状态。")
        else:
            self.log("❌ 回滚操作失败，请检查日志", level="ERROR")
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
        if hasattr(self,
                   'diff_window') and self.diff_window is not None and self.diff_window.winfo_exists():
            self.diff_window.lift()
            self.diff_window.focus_force()
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
            self.root.after(0, lambda: self._finish_scan(data, error_msg))

        self._poll_scan_progress()
        threading.Thread(target=scan_task, daemon=True).start()

    def _poll_scan_progress(self):
        try:
            while True:
                msg = self._scan_progress_queue.get_nowait()
                if msg is None:
                    if hasattr(self, 'scan_progress_window'):
                        self.scan_progress_window.close()
                        delattr(self, 'scan_progress_window')
                    return
                current, filename = msg
                total = getattr(self, '_scan_total', 0)
                if total > 0:
                    self.scan_btn.itemconfig(self.scan_btn.text_id,
                                             text=f"⏳ 解析中 ({current}/{total})")
                else:
                    self.scan_btn.itemconfig(self.scan_btn.text_id, text=f"⏳ 解析中...")
                if hasattr(self, 'scan_progress_window'):
                    self.scan_progress_window.update_progress(current, filename)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_scan_progress)

    def _finish_scan(self, data, error_msg):
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="🔍 扫描模组差异")
        self._scanning = False

        if hasattr(self, 'scan_progress_window'):
            self.scan_progress_window.close()
            delattr(self, 'scan_progress_window')

        if error_msg:
            self.log(f"❌ 扫描出错: {error_msg}", level="ERROR")
            messagebox.showerror("扫描错误", f"扫描过程中发生异常：{error_msg}")
            return

        if data is None:
            return

        if not data:
            messagebox.showinfo("提示", "两个 mods 目录完全一致，没有任何差异。")
            self.log("📊 扫描完成：无差异", level="INFO")
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
        self.diff_window = show_diff_window(self.root, data, self.theme,
                                            self.current_theme, apply_callback)

    # ---------- 迁移 ----------
    def start_migration(self):
        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，请勿重复启动")
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

        if not modlist and not configlist:
            messagebox.showwarning("提示", "模组清单和 config 清单均为空，没有可迁移的内容。")
            return

        # 计算要复制的文件数与总大小
        total_files, total_size = self._calculate_migration_stats(
            src_path, tgt_path, world, modlist, configlist)
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
                args=(src_path, tgt_path, world, modlist, configlist, True)
            )
            thread.daemon = True
            thread.start()
            return

        # 实际迁移：先备份
        try:
            do_backup(tgt_path, log_func=self.log)
        except Exception as e:
            self.log(f"❌ 备份失败：{e}", level="ERROR")
            messagebox.showerror("备份错误", f"备份目标实例失败：{e}\n迁移已取消。")
            return

        self.progress_queue = queue.Queue()
        self.progress_window = ProgressWindow(self.root, total_files, total_size)
        self._migration_running = True
        if self.after_id is None:
            self._poll_progress()

        thread = threading.Thread(
            target=self._run_migration_thread,
            args=(src_path, tgt_path, world, modlist, configlist, False)
        )
        thread.daemon = True
        thread.start()

    def _calculate_migration_stats(self, src_path, tgt_path, world, modlist,
                                   configlist):
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

        src_opts = src_path / "options.txt"
        if src_opts.exists():
            total_files += 1
            total_size += src_opts.stat().st_size

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
                              dry_run):
        def progress_callback(file_index, file_name, copied_bytes, step=None):
            if file_index is None:
                return
            if self.progress_queue:
                self.progress_queue.put((file_index, file_name, copied_bytes, step))

        def check_cancel():
            return self.progress_window and self.progress_window.cancelled

        run_migration(
            src_path=src_path,
            tgt_path=tgt_path,
            world_name=world,
            modlist=modlist,
            configlist=configlist,
            dry_run=dry_run,
            overwrite=self.overwrite_mods.get(),
            progress_callback=progress_callback,
            log_callback=self.log,
            check_cancel=check_cancel,
            add_history=True
        )
        self._migration_running = False

    # ---------- 其他辅助 ----------
    def _is_text_overflow(self, text_widget):
        """
        判断文本框内容是否溢出（行数超过可视高度=竖直，或单行过长=水平）。
        """
        try:
            content = text_widget.get("1.0", tk.END).strip()
            if not content:
                return False
            text_widget.update_idletasks()

            # 竖直溢出：内容超出可视高度（yview 第二项 < 1 表示可滚动）
            try:
                first, last = text_widget.yview()
                if last < 1.0 - 1e-6:
                    return True
            except Exception:
                pass

            # 水平溢出：最后字符超出可视宽度
            was_disabled = False
            if text_widget.cget('state') == tk.DISABLED:
                was_disabled = True
                text_widget.configure(state=tk.NORMAL)

            last_line = text_widget.index("end-1c linestart")
            last_char = text_widget.index("end-1c")
            bbox = text_widget.bbox(last_char)
            if bbox is None:
                first_x, last_x = text_widget.xview()
                overflow = last_x < 1.0
            else:
                width = text_widget.winfo_width()
                overflow = (bbox[0] + bbox[2]) > width

            if was_disabled:
                text_widget.configure(state=tk.DISABLED)
            return overflow
        except Exception:
            # 任何异常都视为未溢出，避免频繁报错
            return False

    def _set_magnify_overflow(self, widget, overflow):
        """设置放大键的溢出高亮：渐变按钮整键变橙色，普通按钮橙底。"""
        try:
            if isinstance(widget, tk.Canvas) and hasattr(widget, 'set_gradient'):
                if overflow:
                    widget.set_gradient("#ff9800", "#ffb74d", "#ffa726", "#ffcc80")
                else:
                    base = getattr(widget, '_base_colors', ("#ff9800", "#ffb74d"))
                    hov = getattr(widget, '_base_hover', None)
                    if hov:
                        widget.set_gradient(base[0], base[1], hov[0], hov[1])
                    else:
                        widget.set_gradient(base[0], base[1])
            else:
                if overflow:
                    widget.config(bg='#ffa500', fg='black')
                else:
                    widget.config(bg=self.theme["button_bg"], fg=self.theme["button_fg"])
        except Exception:
            pass

    def _check_overflow(self):
        """检查并更新放大按钮高亮状态"""
        self._set_magnify_overflow(self.mod_magnify_btn,
                                   self._is_text_overflow(self.mod_text))
        self._set_magnify_overflow(self.config_magnify_btn,
                                   self._is_text_overflow(self.config_text))
    def _safe_undo(self, widget):
        try:
            widget.edit_undo()
        except tk.TclError:
            pass

    def _safe_redo(self, widget):
        try:
            widget.edit_redo()
        except tk.TclError:
            pass

    def _append_mods(self, paths):
        """把若干 .jar 完整路径追加到模组清单（自动去重）。返回新增数量。"""
        paths = [str(Path(p)) for p in paths if Path(p).suffix.lower() == ".jar"]
        if not paths:
            return 0
        content = self.mod_text.get("1.0", tk.END).rstrip("\n")
        lines = set(content.splitlines()) if content else set()
        new = []
        for p in paths:
            if p not in lines:
                lines.add(p)
                new.append(p)
        if not new:
            return 0
        merged = content
        for p in new:
            merged = (merged + "\n" if merged else "") + p
        self.mod_text.configure(state=tk.NORMAL)
        self.mod_text.delete("1.0", tk.END)
        self.mod_text.insert("1.0", merged + ("\n" if merged else ""))
        # 记录本次新添加的模组（文件名小写），主清单用黄色高亮
        self._new_mod_keys.update(Path(p).name.lower() for p in new)
        self._apply_mod_new_tags()
        self.save_config()
        self._update_text_states()
        self.log(f"✅ 已添加 {len(new)} 个模组", level="SUCCESS")
        self._notify_modlist_change()
        return len(new)

    def _apply_mod_new_tags(self):
        """给主模组清单中"本会话新添加"的行重新染上黄色高亮。"""
        try:
            self.mod_text.tag_remove("new", "1.0", tk.END)
            if not self._new_mod_keys:
                return
            lines = self.mod_text.get("1.0", tk.END).splitlines()
            for i, ln in enumerate(lines):
                base = Path(ln.strip()).name.lower()
                if base in self._new_mod_keys:
                    self.mod_text.tag_add("new", f"{i + 1}.0", f"{i + 1}.end")
        except Exception:
            pass

    def _mod_add_result(self, files, added):
        """模组添加后的成功/失败提示。"""
        non_jar = len(files) - sum(1 for f in files if Path(f).suffix.lower() == ".jar")
        if added:
            messagebox.showinfo("添加成功", f"✅ 已添加 {added} 个模组。", parent=self.root)
        else:
            messagebox.showinfo("添加提示", "所选模组已在清单中，未新增。", parent=self.root)
        if non_jar:
            messagebox.showwarning("添加提示", f"⚠️ 有 {non_jar} 个非 .jar 文件被跳过。", parent=self.root)

    def add_mods(self):
        """从文件选择器多选并批量添加模组。"""
        files = filedialog.askopenfilenames(
            title="选择要添加的模组（可多选）",
            filetypes=[("Minecraft 模组", "*.jar"), ("所有文件", "*.*")])
        if files:
            self._mod_add_result(files, self._append_mods(files))

    def _on_mod_drop(self, event):
        """从资源管理器拖入文件时，把 .jar 添加到模组清单。"""
        try:
            files = self.root.tk.splitlist(event.data)
        except Exception:
            files = event.data
        self._mod_add_result(files, self._append_mods(files))

    def _add_config_paths(self, paths):
        """把拖入的路径转成相对 config 的条目，加入 config 清单。返回 (新增数, 失败列表)。"""
        src = self.source_path.get().strip()
        if not src:
            return 0, ["尚未设置源实例根目录"]
        src_config = Path(src) / "config"
        if not src_config.exists():
            return 0, ["源 config 目录不存在"]
        lines = set(l for l in self.config_text.get("1.0", tk.END).splitlines() if l.strip())
        added = 0
        failed = []
        for p in paths:
            tp = Path(p)
            try:
                rel = tp.relative_to(src_config) if tp.is_absolute() else Path(p)
            except ValueError:
                failed.append(str(tp))
                continue
            rel_s = str(rel)
            if not _is_safe_path(rel_s):
                failed.append(str(tp))
                continue
            if rel_s not in lines:
                self.config_text.configure(state=tk.NORMAL)
                if self.config_text.get("1.0", tk.END).strip():
                    self.config_text.insert(tk.END, "\n")
                self.config_text.insert(tk.END, rel_s + "\n")
                lines.add(rel_s)
                added += 1
        self.save_config()
        self._update_text_states()
        if added:
            self._notify_config_change()
        return added, failed

    def _on_config_drop(self, event):
        """从资源管理器拖入文件/文件夹，作为 config 条目添加。"""
        try:
            files = self.root.tk.splitlist(event.data)
        except Exception:
            files = event.data
        added, failed = self._add_config_paths(files)
        if added:
            messagebox.showinfo("添加成功", f"✅ 已添加 {added} 个 config 条目。", parent=self.root)
        if failed:
            messagebox.showwarning("添加提示",
                                   f"⚠️ 有 {len(failed)} 项未添加（不在源 config 目录下或不安全）：\n"
                                   + "\n".join(failed[:5]), parent=self.root)

    def clear_mod_list(self):
        """清空模组清单。"""
        self.mod_text.configure(state=tk.NORMAL)
        self.mod_text.delete("1.0", tk.END)
        self._new_mod_keys.clear()   # 清空后不再保留"新添加"高亮
        self.save_config()
        self._update_text_states()
        self._notify_modlist_change()

    def clear_config(self):
        """清空 config 清单。"""
        self.config_text.configure(state=tk.NORMAL)
        self.config_text.delete("1.0", tk.END)
        self.save_config()
        self._update_text_states()
        self._notify_config_change()

    def _notify_modlist_change(self):
        """通知已打开的"放大查看"刷新模组清单。"""
        try:
            self.mod_text.event_generate("<<ModlistChanged>>")
        except Exception:
            pass

    def _notify_config_change(self):
        """通知已打开的"放大查看"刷新 config 清单。"""
        try:
            self.config_text.event_generate("<<ConfigChanged>>")
        except Exception:
            pass

    def open_big_view(self, source_text, title):
        """大窗口查看：可多选（勾选）+ 可排序的列表，并支持搜索、存在性检测、添加/删除、拖拽。"""
        is_mod = "模组" in title or source_text is getattr(self, 'mod_text', None)
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title(f"大窗口查看 - {title}")
        win.geometry("1060x680")
        win.minsize(820, 520)
        win.transient(self.root)
        win.configure(bg=self.theme["bg"])
        set_window_icon(win)

        if is_mod:
            columns = ("chk", "status", "name", "path", "type", "modid", "version", "size")
            headers = (("chk", "", 38, "center"), ("status", "状态", 62, "w"),
                       ("name", "文件名", 175, "w"), ("path", "完整路径", 200, "w"),
                       ("type", "类型", 62, "w"), ("modid", "Mod ID", 105, "w"),
                       ("version", "版本", 95, "w"), ("size", "大小KB", 68, "e"))
        else:
            columns = ("chk", "status", "name", "path", "type")
            headers = (("chk", "", 38, "center"), ("status", "状态", 62, "w"),
                       ("name", "名称", 170, "w"), ("path", "相对路径/完整路径", 260, "w"),
                       ("type", "类型", 70, "w"))

        tree = ttk.Treeview(win, columns=columns, show="headings", height=20)
        for col, txt, wd, anc in headers:
            tree.heading(col, text=txt, command=lambda c=col: sort_by(c))
            tree.column(col, width=wd, anchor=anc)
        tree.configure(selectmode="none")
        tree.tag_configure("missing", background=self.theme.get("danger_bg", "#ffb3b3"),
                           foreground=self.theme.get("danger_fg", "#8b0000"))
        tree.tag_configure("checked", background=self.theme.get("sel_bg", "#a5d6a7"),
                           foreground=self.theme.get("sel_fg", "#000000"))
        tree.tag_configure("new", background=self.theme.get("warn_bg", "#ffeaa7"),
                           foreground=self.theme.get("warn_fg", "#000000"))
        tree.tag_configure("odd", background=self.theme.get("neutral_bg", "#f8f9fa"),
                           foreground=self.theme.get("neutral_fg", "#000000"))
        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(win, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        win.grid_rowconfigure(0, weight=1)
        win.grid_columnconfigure(0, weight=1)

        entries = [ln.strip() for ln in source_text.get("1.0", tk.END).splitlines() if ln.strip()]
        msg_queue = queue.Queue()
        meta = {}          # entry索引 -> {status,name,path,type,modid,version,size}
        checked = {}       # 条目内容(完整路径/文件名) -> bool，用勾选做多选（按内容而非行位置，避免排序/刷新后错位）
        new_keys = set()   # 本次会话新添加的模组（文件名），染黄色高亮提示
        order = list(range(len(entries)))   # 当前显示顺序（entries 索引），含排序+过滤
        order_index = {idx: pos for pos, idx in enumerate(order)}  # idx->当前位置，供 poll 快速定位(O(1))
        sort_state = {"col": None, "rev": False}

        mods_dir = None
        config_dir = None
        if hasattr(self, 'source_path'):
            sp = self.source_path.get().strip()
            if sp:
                if is_mod:
                    mods_dir = Path(sp) / "mods"
                else:
                    config_dir = Path(sp) / "config"

        def key_of(entry):
            """勾选键：用文件名(小写)，保证完整路径/文件名两种条目能对应到同一个模组。"""
            return Path(entry).name.lower()

        def resolve(entry):
            """解析条目为存在的对象：模组=jar，config=配置目录下的相对路径。返回 dict 或 None。"""
            p = Path(entry)
            if is_mod:
                if p.is_file() and p.suffix.lower() == ".jar":
                    return {"name": p.name, "path": str(p), "obj": p, "type": None}
                if mods_dir is not None:
                    cand = mods_dir / p.name if (p.suffix == "" and p.name) else mods_dir / entry
                    if cand.is_file():
                        return {"name": cand.name, "path": str(cand), "obj": cand, "type": None}
                return None
            else:
                if config_dir is not None:
                    cand = config_dir / entry if not Path(entry).is_absolute() else Path(entry)
                    if cand.exists():
                        return {"name": cand.name, "path": str(cand), "obj": cand,
                                "type": "文件夹" if cand.is_dir() else "文件"}
                return None

        def sort_key(col):
            def key(i):
                e = entries[i]
                r = resolve(e)
                if col == "status":
                    return 0 if r is not None else 1
                if col == "name":
                    return (r["name"] if r else Path(e).name).lower()
                if col == "path":
                    return str(e).lower()
                if col == "type":
                    return (r["type"] if r else "")
                if col == "size":
                    return r["obj"].stat().st_size if r else -1
                m = meta.get(i, {})
                return str(m.get(col, ""))
            return key

        def compute_order():
            q = search_var.get().strip().lower()
            idxs = list(range(len(entries)))
            if q:
                idxs = [i for i, e in enumerate(entries)
                        if q in str(e).lower() or q in Path(e).name.lower()]
            if sort_state["col"]:
                idxs.sort(key=sort_key(sort_state["col"]), reverse=sort_state["rev"])
            return idxs

        def row_values(pos, idx):
            m = meta.get(idx)
            chk = "☑" if checked.get(key_of(entries[idx]), False) else "☐"
            name = (m.get("name") if m else None) or Path(entries[idx]).name
            path = (m.get("path") if m else entries[idx]) or entries[idx]
            if is_mod:
                if not m:
                    return (chk, "…", entries[idx], entries[idx], "…", "…", "…", "…")
                return (chk, m.get("status", "…"), name, path,
                        m.get("type", "…"), m.get("modid", "…"), m.get("version", "…"),
                        m.get("size", "…"))
            else:
                if not m:
                    return (chk, "…", name, entries[idx], "…")
                return (chk, m.get("status", "…"), name, path, m.get("type", "…"))

        def row_tags(pos, idx):
            """行配色：勾选优先(蓝) > 缺失(红) > 新添加(黄) > 奇偶斑马纹。"""
            if checked.get(key_of(entries[idx])):
                return ("checked",)
            if meta.get(idx, {}).get("status") == "❌ 缺失":
                return ("missing",)
            if key_of(entries[idx]) in new_keys:
                return ("new",)
            if pos % 2:
                return ("odd",)
            return ()

        def scan_row(idx):
            e = entries[idx]
            try:
                r = resolve(e)
                if r is None:
                    meta[idx] = {"status": "❌ 缺失", "name": Path(e).name or e,
                                 "path": str(e), "type": "?", "modid": "?", "version": "?", "size": "?"}
                else:
                    if is_mod:
                        info = get_full_mod_metadata(str(r["obj"]))
                        meta[idx] = {"status": "✅ 存在", "name": r["name"], "path": r["path"],
                                     "type": info.get("mod_type", "?"), "modid": info.get("modid", "?"),
                                     "version": info.get("version", "?"),
                                     "size": round(r["obj"].stat().st_size / 1024, 1)}
                    else:
                        size = round(r["obj"].stat().st_size / 1024, 1) if r["obj"].is_file() else ""
                        meta[idx] = {"status": "✅ 存在", "name": r["name"], "path": r["path"],
                                     "type": r["type"], "modid": "", "version": "", "size": size}
            except Exception:
                meta[idx] = {"status": "❌ 缺失", "name": Path(e).name or e,
                             "path": str(e), "type": "?", "modid": "?", "version": "?", "size": "?"}
            msg_queue.put(idx)

        # 有界扫描线程池：避免为每条目单开线程导致几千并发的线程爆炸/磁盘抖动
        scan_queue = queue.Queue()
        _SCAN_WORKERS = 6

        def scan_worker():
            while True:
                try:
                    idx = scan_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                try:
                    scan_row(idx)
                except Exception:
                    pass
                finally:
                    scan_queue.task_done()

        for _ in range(_SCAN_WORKERS):
            threading.Thread(target=scan_worker, daemon=True).start()

        def rebuild(rescan=True):
            if not win.winfo_exists():
                return
            tree.delete(*tree.get_children())
            order[:] = compute_order()
            order_index.clear()
            order_index.update({idx: pos for pos, idx in enumerate(order)})
            for pos, idx in enumerate(order):
                iid = str(pos)
                tree.insert("", "end", iid=iid, values=row_values(pos, idx),
                            tags=row_tags(pos, idx))
                if rescan and idx not in meta:
                    scan_queue.put(idx)
            total = len(entries)
            shown = len(order)
            count_lbl.config(text=(f"显示 {shown}/{total} 项"
                                   if search_var.get().strip() else f"共 {total} 项"))

        def write_back():
            content = "\n".join(entries)
            source_text.configure(state=tk.NORMAL)
            source_text.edit_separator()
            source_text.delete("1.0", tk.END)
            source_text.insert("1.0", content + ("\n" if content else ""))
            source_text.edit_separator()
            self._update_text_states()
            self.save_config()
            # 主模组清单被重写后，重新应用"新添加"黄色高亮
            if is_mod:
                self._apply_mod_new_tags()

        def add_mods():
            files = filedialog.askopenfilenames(title="选择要添加的模组（可多选）",
                                                filetypes=[("Minecraft 模组", "*.jar"), ("所有文件", "*.*")])
            if not files:
                return
            existing = set(entries)
            new_entries = [str(Path(f)) for f in files if str(Path(f)) not in existing]
            if not new_entries:
                messagebox.showinfo("提示", "所选模组已在清单中。")
                return
            entries.extend(new_entries)
            new_keys.update(key_of(e) for e in new_entries)
            rebuild(rescan=True)
            write_back()
            messagebox.showinfo("添加成功", f"✅ 已添加 {len(new_entries)} 个模组。", parent=win)

        def del_selected():
            to_del = {i for i, e in enumerate(entries) if checked.get(key_of(e))}
            if not to_del:
                messagebox.showinfo("提示", "请先勾选要删除的模组。")
                return
            entries[:] = [e for i, e in enumerate(entries) if i not in to_del]
            meta.clear()
            checked.clear()
            rebuild(rescan=True)
            write_back()

        def on_tree_drop(event):
            try:
                files = self.root.tk.splitlist(event.data)
            except Exception:
                files = event.data
            jars = [str(Path(f)) for f in files if Path(f).suffix.lower() == ".jar"]
            existing = set(entries)
            new_entries = [p for p in jars if p not in existing]
            if not new_entries:
                return
            entries.extend(new_entries)
            new_keys.update(key_of(e) for e in new_entries)
            rebuild(rescan=True)
            write_back()
            messagebox.showinfo("添加成功", f"✅ 已添加 {len(new_entries)} 个模组。", parent=win)

        def toggle_check(event):
            row_id = tree.identify_row(event.y)
            if not row_id:
                return
            pos = int(row_id)
            k = key_of(entries[order[pos]])
            checked[k] = not checked.get(k, False)
            tree.set(row_id, "chk", "☑" if checked[k] else "☐")
            # 同步行高亮（勾选->蓝色）
            try:
                idx = order[pos]
                tree.item(row_id, tags=row_tags(pos, idx))
            except Exception:
                pass

        def sort_by(col):
            if sort_state["col"] == col:
                sort_state["rev"] = not sort_state["rev"]
            else:
                sort_state["col"] = col
                sort_state["rev"] = False
            rebuild(rescan=False)

        def poll():
            # 每次尽量只处理一小批消息，避免几千条积压时一次循环卡死 UI
            batch = 40
            try:
                while batch > 0:
                    idx = msg_queue.get_nowait()
                    pos = order_index.get(idx)
                    if pos is not None:
                        iid = str(pos)
                        if tree.exists(iid):
                            tree.item(iid, values=row_values(pos, idx),
                                      tags=row_tags(pos, idx))
                    batch -= 1
            except queue.Empty:
                pass
            except Exception:
                pass
            try:
                if win.winfo_exists():
                    win.after(120, poll)
            except Exception:
                pass

        def detect():
            """强制重新检测存在性，并给出结果提示。"""
            try:
                missing = sum(1 for i, e in enumerate(entries) if resolve(e) is None)
            except Exception:
                missing = 0
            meta.clear()
            rebuild(rescan=True)
            messagebox.showinfo("检测完成",
                                f"✅ 存在性检测完成：共 {len(entries)} 项，缺失 {missing} 项。",
                                parent=win)

        # 顶部工具栏
        top = tk.Frame(win, bg=self.theme["bg"])
        top.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        count_lbl = tk.Label(top, text=f"共 {len(entries)} 项", bg=self.theme["bg"],
                             fg=self.theme["fg"])
        count_lbl.pack(side="left", padx=6)
        # 搜索（模组区/Config区都可用）
        tk.Label(top, text="搜索:", bg=self.theme["bg"], fg=self.theme["fg"]).pack(side="left")
        search_var = tk.StringVar()
        search_entry = tk.Entry(top, textvariable=search_var, width=18,
                                bg=self.theme["entry_bg"], fg=self.theme["entry_fg"],
                                insertbackground=self.theme["fg"])
        search_entry.pack(side="left", padx=4)
        # 搜索防抖：停止输入 250ms 后再重建，避免每个按键都全量重建导致卡顿
        _search_after = [None]
        def on_search_changed(*a):
            if _search_after[0] is not None:
                try:
                    win.after_cancel(_search_after[0])
                except Exception:
                    pass
            _search_after[0] = win.after(250, lambda: rebuild(rescan=False))
        search_var.trace("w", on_search_changed)
        search_entry.bind("<Return>", lambda e: rebuild(rescan=False))
        detect_btn = create_gradient_button(top, "🔍 检测存在性", detect,
                                            colors=("#43a047", "#66bb6a"),
                                            hover_colors=("#66bb6a", "#43a047"),
                                            width=118, height=30, font=("微软雅黑", 9, "bold"))
        detect_btn.pack(side="left", padx=4)
        del_btn = create_gradient_button(top, "🗑️ 删除选中", del_selected,
                                         colors=("#e53935", "#ff7043"),
                                         hover_colors=("#ef5350", "#ff7043"),
                                         width=112, height=30, font=("微软雅黑", 9, "bold"))
        del_btn.pack(side="left", padx=4)
        if is_mod:
            add_btn = create_gradient_button(top, "➕ 添加模组", add_mods,
                                             colors=("#00c853", "#00e676"),
                                             hover_colors=("#00e676", "#00c853"),
                                             width=112, height=30, font=("微软雅黑", 9, "bold"))
            add_btn.pack(side="left", padx=4)
            # 拖拽（仅模组可拖入 .jar）
            try:
                from tkinterdnd2 import DND_FILES
                tree.drop_target_register(DND_FILES)
                tree.dnd_bind('<<Drop>>', on_tree_drop)
            except Exception:
                pass
        tree.bind("<ButtonRelease-1>", toggle_check)
        btn_close_big = create_gradient_button(
            top, "关闭", win.destroy,
            colors=("#757575", "#9e9e9e"), hover_colors=("#8d8d8d", "#bdbdbd"),
            width=_grad_width("关闭"), height=30, font=("微软雅黑", 9, "bold"))
        btn_close_big.pack(side="right")

        # 当主界面清单被外部修改（如差异"应用"/config导入/浏览添加/拖拽/清空）时，自动重载并保留勾选高亮
        def reload_entries():
            if not win.winfo_exists():
                return
            entries[:] = [ln.strip() for ln in source_text.get(
                "1.0", tk.END).splitlines() if ln.strip()]
            meta.clear()
            rebuild(rescan=True)
        if is_mod:
            source_text.bind("<<ModlistChanged>>", lambda e: reload_entries())
        else:
            source_text.bind("<<ConfigChanged>>", lambda e: reload_entries())

        rebuild(rescan=True)
        poll()
        # 登记该结果表，方便主题切换时同步行标签颜色
        if is_mod:
            if not hasattr(self, '_big_view_trees'):
                self._big_view_trees = []
            self._big_view_trees.append(tree)
        apply_theme_to_widget_tree(win, self.theme)
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        x = (win.winfo_screenwidth() // 2) - (w // 2)
        y = (win.winfo_screenheight() // 2) - (h // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.deiconify()

    def _update_text_states(self):
        state = tk.NORMAL if self.edit_mode.get() else tk.DISABLED
        self.mod_text.configure(state=state)
        self.config_text.configure(state=state)
        self._check_overflow()
    def toggle_edit_mode(self):
        self._update_text_states()
        if self.edit_mode.get():
            self.log("⚠️ 警告：已启用主界面编辑模式，直接修改清单可能导致数据错误，请谨慎操作！", level="WARNING")
        else:
            self.log("ℹ️ 主界面编辑模式已关闭，清单恢复只读。", level="INFO")
        self.save_config()
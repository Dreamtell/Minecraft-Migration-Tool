# utils/theme.py
import tkinter as tk
from tkinter import scrolledtext

LIGHT_THEME = {
    "bg": "#f0f0f0",
    "fg": "#000000",
    "entry_bg": "#ffffff",
    "entry_fg": "#000000",
    "button_bg": "#e0e0e0",
    "button_fg": "#000000",
    "label_bg": "#f0f0f0",
    "label_fg": "#000000",
    "labelframe_bg": "#f0f0f0",
    "labelframe_fg": "#000000",
    "text_bg": "#ffffff",
    "text_fg": "#000000",
    "log_bg": "#ffffff",
    "log_fg": "#000000",
    "warning_bg": "#ffcccc",
    "warning_fg": "#ff0000",
    "bottom_bg": "#ffffff",
    "bottom_fg": "gray",
    "tooltip_bg": "#ffffe0",
    # ttk 样式颜色
    "ttk_bg": "#f0f0f0",
    "ttk_fg": "#000000",
    "ttk_select_bg": "#d0d0d0",
    "ttk_select_fg": "#000000",
    "ttk_field_bg": "#ffffff",
    "ttk_progress_bg": "#4fc3f7",
    "ttk_trough_bg": "#e0e0e0",
    # 语义色（按钮/标签/徽章，跟随主题统一）
    "success_bg": "#d4edda", "success_fg": "#000000",
    "warn_bg": "#ffeaa7", "warn_fg": "#000000",
    "neutral_bg": "#f8f9fa", "neutral_fg": "#000000",
    "info_bg": "#d0f0f0", "info_fg": "#000000",
    "accent_bg": "#b3d9ff", "accent_fg": "#000000",
    "highlight_bg": "#cce5ff", "highlight_fg": "#000000",
    "lightgray_bg": "#d3d3d3",
    "danger_bg": "#ffc7c7", "danger_fg": "#8b0000",
    "edit_bg": "#ff9800", "edit_fg": "#000000",
    "ok_fg": "#2e7d32", "fail_fg": "#c62828", "muted_fg": "#808080",
    "badge_rollback_bg": "#ffdddd", "badge_normal_bg": "#ffffff",
    "sel_bg": "#66bb6a", "sel_fg": "#ffffff",
    "hover_bg": "#e9eef5", "hover_fg": "#000000",
    "hover_checked_bg": "#cde8cd", "hover_checked_fg": "#000000",
    "hover_missing_bg": "#ffd9d9", "hover_missing_fg": "#8b0000",
    "hover_new_bg": "#fff2c4", "hover_new_fg": "#000000"
}

DARK_THEME = {
    "bg": "#2e2e2e",
    "fg": "#ffffff",
    "entry_bg": "#3e3e3e",
    "entry_fg": "#ffffff",
    "button_bg": "#4e4e4e",
    "button_fg": "#ffffff",
    "label_bg": "#2e2e2e",
    "label_fg": "#ffffff",
    "labelframe_bg": "#2e2e2e",
    "labelframe_fg": "#ffffff",
    "text_bg": "#3e3e3e",
    "text_fg": "#ffffff",
    "log_bg": "#1e1e1e",
    "log_fg": "#ffffff",
    "warning_bg": "#553333",
    "warning_fg": "#ff8888",
    "bottom_bg": "#2e2e2e",
    "bottom_fg": "#aaaaaa",
    "tooltip_bg": "#3e3e3e",
    # ttk 样式颜色
    "ttk_bg": "#2e2e2e",
    "ttk_fg": "#ffffff",
    "ttk_select_bg": "#3a3a3a",
    "ttk_select_fg": "#ffffff",
    "ttk_field_bg": "#3e3e3e",
    "ttk_progress_bg": "#4fc3f7",
    "ttk_trough_bg": "#3a3a3a",
    # 语义色（按钮/标签/徽章，跟随主题统一）
    "success_bg": "#2d4a2d", "success_fg": "#ffffff",
    "warn_bg": "#4a3d2d", "warn_fg": "#ffffff",
    "neutral_bg": "#3a3a3a", "neutral_fg": "#ffffff",
    "info_bg": "#2d3d4a", "info_fg": "#ffffff",
    "accent_bg": "#3a4a5a", "accent_fg": "#ffffff",
    "highlight_bg": "#4a6a8a", "highlight_fg": "#ffffff",
    "lightgray_bg": "#4e4e4e",
    "danger_bg": "#5a2d2d", "danger_fg": "#ffb3b3",
    "edit_bg": "#ff9800", "edit_fg": "#000000",
    "ok_fg": "#7ee787", "fail_fg": "#ff6b6b", "muted_fg": "#aaaaaa",
    "badge_rollback_bg": "#5a2d2d", "badge_normal_bg": "#3a3a3a",
    "sel_bg": "#2e7d32", "sel_fg": "#ffffff",
    "hover_bg": "#3a3e44", "hover_fg": "#ffffff",
    "hover_checked_bg": "#3a5a3a", "hover_checked_fg": "#ffffff",
    "hover_missing_bg": "#5a3a3a", "hover_missing_fg": "#ffb3b3",
    "hover_new_bg": "#5a4a3a", "hover_new_fg": "#ffffff"
}


def apply_theme_to_widget_tree(widget, theme):
    """将主题颜色递归应用到控件树（含 Toplevel 子窗口），供各窗口创建/切换时复用"""
    try:
        if isinstance(widget, tk.Toplevel):
            widget.configure(bg=theme["bg"])
        elif isinstance(widget, tk.LabelFrame):
            # LabelFrame 是 Frame 的子类，需先判断，否则标题颜色不会设置
            widget.configure(bg=theme["labelframe_bg"], fg=theme["labelframe_fg"])
        elif isinstance(widget, tk.Frame):
            widget.configure(bg=theme["bg"])
        elif isinstance(widget, tk.Label):
            widget.configure(bg=theme["label_bg"], fg=theme["label_fg"])
        elif isinstance(widget, tk.Button):
            widget.configure(bg=theme["button_bg"], fg=theme["button_fg"],
                             activebackground=theme["button_bg"])
        elif isinstance(widget, tk.Entry):
            widget.configure(bg=theme["entry_bg"], fg=theme["entry_fg"],
                             insertbackground=theme["fg"])
        elif isinstance(widget, scrolledtext.ScrolledText):
            widget.configure(bg=theme["text_bg"], fg=theme["text_fg"])
            widget.vbar.configure(bg=theme["button_bg"], troughcolor=theme["bg"])
        elif isinstance(widget, tk.Text):
            widget.configure(bg=theme["text_bg"], fg=theme["text_fg"])
        elif isinstance(widget, tk.Canvas):
            widget.configure(bg=theme["bg"])
        elif isinstance(widget, tk.Listbox):
            widget.configure(bg=theme["entry_bg"], fg=theme["entry_fg"])
    except Exception:
        pass
    for child in widget.winfo_children():
        apply_theme_to_widget_tree(child, theme)

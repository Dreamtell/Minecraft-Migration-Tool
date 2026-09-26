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
    # 卡片/表格的"选中"样式（模仿 PCL2：浅蓝底 + 左侧蓝条 + 蓝标题）
    "card_sel_bg": "#d4e6f8", "card_sel_fg": "#0d3d63", "card_sel_bar": "#2f7fd1",
    "hover_checked_bg": "#cde8cd", "hover_checked_fg": "#000000",
    "hover_missing_bg": "#ffd9d9", "hover_missing_fg": "#8b0000",
    "hover_new_bg": "#fff2c4", "hover_new_fg": "#000000",
    # 日志分类色（执行日志/日志放大查看，跟随主题）
    "log_info_fg": "#808080", "log_warning_fg": "#e65100",
    "log_error_fg": "#c62828", "log_success_fg": "#2e7d32",
    "log_simulate_fg": "#1565c0",
    # "数据"文本配色：界面上一眼分得清"哪个是数据、哪个是说明"
    #   data_fg     路径、文件名、存档名这类主体数据
    #   data_num_fg 数量、条数、大小这类数字
    #   data_id_fg  Mod ID、版本、哈希这类标识符
    "data_fg": "#1565c0", "data_num_fg": "#6a1b9a", "data_id_fg": "#00695c"
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
    # 卡片/表格的"选中"样式（深色版：暗蓝底 + 亮蓝条 + 亮蓝标题）
    "card_sel_bg": "#2b3b4d", "card_sel_fg": "#cfe8ff", "card_sel_bar": "#4da3f0",
    "hover_checked_bg": "#3a5a3a", "hover_checked_fg": "#ffffff",
    "hover_missing_bg": "#5a3a3a", "hover_missing_fg": "#ffb3b3",
    "hover_new_bg": "#5a4a3a", "hover_new_fg": "#ffffff",
    # 日志分类色（执行日志/日志放大查看，跟随主题，深色用更亮的前景色）
    "log_info_fg": "#9e9e9e", "log_warning_fg": "#ffb74d",
    "log_error_fg": "#ff6b6b", "log_success_fg": "#7ee787",
    "log_simulate_fg": "#64b5f6",
    # "数据"文本配色（深色主题用亮一档的同类色）
    "data_fg": "#79b8ff", "data_num_fg": "#d2a8ff", "data_id_fg": "#56d4c4"
}


def _inside_rounded_entry(widget):
    """这个控件是不是圆角输入框内部那个 tk.Entry？

    RoundedEntry 的前景色由它自己按 `fg_key` 决定（数据色），所以这里不能再
    一律刷成 entry_fg —— 否则界面上刚设好的"数据色"会被主题遍历抹掉。
    """
    p = getattr(widget, "master", None)
    for _ in range(3):
        if p is None:
            return False
        if getattr(p, "_is_rounded_entry", False):
            return True
        p = getattr(p, "master", None)
    return False


def apply_theme_to_widget_tree(widget, theme):
    """将主题颜色递归应用到控件树（含 Toplevel 子窗口），供各窗口创建/切换时复用"""
    try:
        # 自绘圆角输入框（utils.helpers.RoundedEntry）：它不是普通 Frame，
        # 填充/描边要按主题重画，所以要在 Frame 分支之前拦下来。
        # 这里用鸭子类型判断，避免 theme <-> helpers 循环导入。
        if getattr(widget, "_is_rounded_entry", False):
            widget.set_theme(theme)
        elif isinstance(widget, tk.Toplevel):
            widget.configure(bg=theme["bg"])
        elif isinstance(widget, tk.LabelFrame):
            # LabelFrame 是 Frame 的子类，需先判断，否则标题颜色不会设置
            widget.configure(bg=theme["labelframe_bg"], fg=theme["labelframe_fg"])
        elif isinstance(widget, tk.Frame):
            widget.configure(bg=theme["bg"])
        elif isinstance(widget, tk.Label):
            # 语义色标签（路径/存档状态那种绿/红字）自己管前景色：
            # 这类文字的颜色是"状态"而不是"主题"决定的，统一刷成 label_fg 的话，
            # 切主题时会先闪过一瞬"绿字变黑/白"、再被状态刷新改回绿色。
            # 所以它们只跟主题刷底色，前景色留给状态刷新逻辑。
            # _keep_colors 更彻底：底色前景都自己管（模组详情窗口的徽章就是这种）。
            # _data_key = 显示"数据"的标签（路径/数字/标识符），颜色由那个键决定，
            # 跟着主题走 —— 它不归状态逻辑管，所以这里直接刷。
            data_key = getattr(widget, "_data_key", None)
            if data_key:
                widget.configure(bg=theme["label_bg"],
                                 fg=theme.get(data_key) or theme["label_fg"])
            elif getattr(widget, "_keep_colors", False):
                pass
            elif getattr(widget, "_keep_fg", False):
                widget.configure(bg=theme["label_bg"])
            else:
                widget.configure(bg=theme["label_bg"], fg=theme["label_fg"])
        elif isinstance(widget, tk.Button):
            widget.configure(bg=theme["button_bg"], fg=theme["button_fg"],
                             activebackground=theme["button_bg"])
        elif isinstance(widget, tk.Entry):
            if not _inside_rounded_entry(widget):
                widget.configure(bg=theme["entry_bg"], fg=theme["entry_fg"],
                                 insertbackground=theme["fg"])
        elif isinstance(widget, scrolledtext.ScrolledText):
            widget.configure(bg=theme["text_bg"], fg=theme["text_fg"])
            widget.vbar.configure(bg=theme["button_bg"], troughcolor=theme["bg"])
        elif isinstance(widget, tk.Text):
            widget.configure(bg=theme["text_bg"], fg=theme["text_fg"])
        elif isinstance(widget, tk.Canvas):
            # 渐变按钮是 Canvas：它四角是透明的，得跟着"父容器的真实底色"走，
            # 不能一律用 theme["bg"]（按钮可能坐在 LabelFrame 这类容器上）
            setter = getattr(widget, "set_corner_bg", None)
            if setter is not None:
                try:
                    setter(widget.master.cget("bg"))
                except Exception:
                    widget.configure(bg=theme["bg"])
            else:
                widget.configure(bg=theme["bg"])
        elif isinstance(widget, tk.Listbox):
            widget.configure(bg=theme["entry_bg"], fg=theme["entry_fg"])
        elif isinstance(widget, tk.Checkbutton):
            # 勾选框默认是系统的浅灰底，不跟随主题会非常突兀
            widget.configure(bg=theme["bg"], fg=theme["fg"],
                             activebackground=theme["bg"],
                             activeforeground=theme["fg"],
                             selectcolor=theme.get("entry_bg", theme["bg"]),
                             highlightthickness=0)
        elif isinstance(widget, tk.Radiobutton):
            widget.configure(bg=theme["bg"], fg=theme["fg"],
                             activebackground=theme["bg"],
                             activeforeground=theme["fg"],
                             selectcolor=theme.get("entry_bg", theme["bg"]),
                             highlightthickness=0)
    except Exception:
        pass
    for child in widget.winfo_children():
        apply_theme_to_widget_tree(child, theme)

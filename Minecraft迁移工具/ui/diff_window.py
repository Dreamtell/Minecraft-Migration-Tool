# ui/diff_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import os
import time
from utils.helpers import set_window_icon, create_gradient_button
from ui.dialogs import show_mod_detail, update_mod_detail_theme


def show_diff_window(parent, data, theme, current_theme, apply_callback):
    """
    显示差异列表窗口
    parent: 父窗口
    data: 差异数据列表
    theme: 主题字典（用于 ttk 样式）
    current_theme: 字符串 "light" 或 "dark"（用于行标签颜色）
    apply_callback: 应用所选的回调函数 (selected_files)
    """
    diff_win = tk.Toplevel(parent)
    diff_win.withdraw()

    diff_win.title("智能模组差异扫描（元数据级）")
    width, height = 1200, 600
    diff_win.geometry(f"{width}x{height}")
    diff_win.transient(parent)
    diff_win.configure(bg=theme["bg"])
    set_window_icon(diff_win)

    # 记录当前主题，供主题切换后打开模组详情时使用正确的主题
    diff_win._current_theme = theme
    diff_win._current_theme_name = current_theme

    def on_diff_destroy(event):
        if hasattr(parent, 'diff_window'):
            parent.diff_window = None

    diff_win.bind("<Destroy>", on_diff_destroy)

    # ---- 顶部提示 ----
    tk.Label(diff_win, text="以下为扫描结果，勾选你希望复制到目标的模组（快速双击某行可打开模组详情）：",
             font=("微软雅黑", 10), bg=theme["bg"], fg=theme["fg"]).grid(
        row=0, column=0, columnspan=2, pady=5, sticky="w", padx=10)

    # ---- 配置 Treeview 样式（使用独立样式名） ----
    style = ttk.Style()
    if style.theme_use() != 'clam':
        try:
            style.theme_use('clam')
        except:
            pass

    # 配置 Treeview 主体样式
    style.configure(
        "Diff.Treeview",
        background=theme["ttk_bg"],
        fieldbackground=theme["ttk_bg"],  # 空行/字段背景跟随主题，避免深色下出现白色
        foreground=theme["ttk_fg"],
        selectbackground=theme["ttk_select_bg"],
        selectforeground=theme["ttk_select_fg"],
        bordercolor=theme["bg"],          # 边框跟随主题
        borderwidth=0,
        rowheight=24
    )

    # 配置 Treeview 列标题样式
    style.configure(
        "Diff.Treeview.Heading",
        background=theme["button_bg"],
        foreground=theme["fg"],
        relief="flat",
        borderwidth=0,
        font=("微软雅黑", 9, "bold")
    )
    style.map(
        "Diff.Treeview.Heading",
        background=[('active', theme["button_bg"])]
    )

    # 配置滚动条样式
    style.configure(
        "Diff.Vertical.TScrollbar",
        background=theme["button_bg"],
        troughcolor=theme["bg"],
        arrowcolor=theme["fg"],
        bordercolor=theme["bg"],
        lightcolor=theme["button_bg"],
        darkcolor=theme["button_bg"],
        relief="flat",
        borderwidth=0
    )
    style.configure(
        "Diff.Horizontal.TScrollbar",
        background=theme["button_bg"],
        troughcolor=theme["bg"],
        arrowcolor=theme["fg"],
        bordercolor=theme["bg"],
        lightcolor=theme["button_bg"],
        darkcolor=theme["button_bg"],
        relief="flat",
        borderwidth=0
    )

    # ---- 创建 Treeview（指定样式） ----
    columns = ("选择", "文件名", "状态", "类型", "Mod ID", "版本", "大小(KB)", "备注")
    tree = ttk.Treeview(
        diff_win,
        columns=columns,
        show="headings",
        height=18,
        style="Diff.Treeview"
    )
    # 禁用内置选择，完全由 tag 控制
    tree.configure(selectmode="none")
    for col in columns:
        tree.heading(col, text=col)
    tree.column("选择", width=60, anchor="center", minwidth=60)
    tree.column("文件名", width=250, minwidth=150)
    tree.column("状态", width=100, minwidth=80)
    tree.column("类型", width=80, anchor="center", minwidth=60)
    tree.column("Mod ID", width=150, minwidth=100)
    tree.column("版本", width=120, minwidth=80)
    tree.column("大小(KB)", width=90, anchor="center", minwidth=80)
    tree.column("备注", width=300, minwidth=200)

    vsb = ttk.Scrollbar(diff_win, orient="vertical", command=tree.yview,
                        style="Diff.Vertical.TScrollbar")
    hsb = ttk.Scrollbar(diff_win, orient="horizontal", command=tree.xview,
                        style="Diff.Horizontal.TScrollbar")
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=1, column=0, sticky="nsew")
    vsb.grid(row=1, column=1, sticky="ns")
    hsb.grid(row=2, column=0, sticky="ew")

    diff_win.grid_rowconfigure(1, weight=1)
    diff_win.grid_columnconfigure(0, weight=1)

    # ---- 定义高亮 tag 颜色 ----
    def update_highlight_color():
        # 根据主题设置 highlight 颜色（使用明显的高亮色）
        tree.tag_configure("highlight", background=theme["highlight_bg"],
                           foreground=theme["highlight_fg"])

    update_highlight_color()

    # ---- 数据加载 ----
    all_data = data[:]
    selection_state = {}

    for idx, item in enumerate(all_data):
        display_name, status, real_name, size_kb, note, modid, version, mod_type, file_path = item
        default_checked = (status == "新增")
        checked_char = "☑" if default_checked else "☐"
        # 基础标签（状态）
        tags = []
        if status == "新增":
            tags.append("new")
        elif status == "更新":
            tags.append("update")
        else:
            tags.append("target_only")
        # 如果默认勾选，添加高亮标签
        if default_checked:
            tags.append("highlight")

        iid = str(idx)
        tree.insert("", "end", iid=iid, values=(
            checked_char,
            display_name,
            status,
            mod_type,
            modid,
            version,
            size_kb,
            note
        ), tags=tuple(tags))
        selection_state[iid] = default_checked

    # 配置状态标签颜色（与高亮分开）
    tree.tag_configure("new", background=theme["success_bg"],
                       foreground=theme["success_fg"])
    tree.tag_configure("update", background=theme["warn_bg"],
                       foreground=theme["warn_fg"])
    tree.tag_configure("target_only", background=theme["neutral_bg"],
                       foreground=theme["neutral_fg"])

    # ---- 核心函数：同步高亮 ----
    def update_highlight():
        """根据 selection_state 更新每行的 tags，并强制刷新"""
        for iid, checked in selection_state.items():
            # 获取当前行的现有 tags
            current_tags = list(tree.item(iid, "tags"))
            # 确保 "highlight" 存在或移除
            if checked:
                if "highlight" not in current_tags:
                    current_tags.append("highlight")
            else:
                if "highlight" in current_tags:
                    current_tags.remove("highlight")
            # 更新 tags（保留其他状态标签）
            tree.item(iid, tags=tuple(current_tags))
        # 强制刷新
        tree.update_idletasks()

    # ---- 交互事件（自定义快速双击判定） ----
    DOUBLE_CLICK_SEC = 0.25  # 快速双击阈值（秒）：同一行两次点击间隔小于该值才算双击
    last_click_time = [0.0]
    last_click_row = [None]

    def apply_toggle(row_id):
        """切换并刷新勾选状态"""
        current = selection_state.get(row_id, False)
        new_state = not current
        selection_state[row_id] = new_state
        tree.set(row_id, "选择", "☑" if new_state else "☐")
        update_highlight()

    def open_mod_detail(iid):
        """打开指定 iid 行的模组详情"""
        try:
            idx = int(iid)
        except (ValueError, TypeError):
            return
        if 0 <= idx < len(all_data):
            file_path = all_data[idx][8]
            if file_path and os.path.exists(file_path):
                # 使用当前主题（切换主题后仍正确）
                show_mod_detail(diff_win, file_path, getattr(diff_win, '_current_theme',
                                                             theme))
            else:
                messagebox.showerror("错误", "找不到模组文件")

    def toggle_selection(event):
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        now = time.time()
        # 快速双击：同一行、两次点击间隔小于阈值 -> 打开模组详情
        if (now - last_click_time[0]) <= DOUBLE_CLICK_SEC and row_id == last_click_row[0]:
            # 撤销第一次点击造成的勾选切换（双击不应改变勾选状态）
            apply_toggle(row_id)
            last_click_time[0] = 0.0
            last_click_row[0] = None
            open_mod_detail(row_id)
            return
        # 普通单击：记录并切换勾选
        last_click_time[0] = now
        last_click_row[0] = row_id
        apply_toggle(row_id)

    tree.bind("<ButtonRelease-1>", toggle_selection)

    # ---- 排序功能 ----
    sort_field = tk.StringVar(value="文件名")
    sort_reverse = tk.BooleanVar(value=False)

    def get_sort_key(field):
        field_map = {
            "文件名": 0,
            "状态": 1,
            "类型": 7,
            "Mod ID": 5,
            "版本": 6,
            "大小(KB)": 3,
        }
        idx = field_map.get(field, 0)
        if field == "大小(KB)":
            def key_func(item):
                try:
                    return float(item[idx])
                except:
                    return 0.0
            return key_func
        elif field == "状态":
            status_order = {"新增": 0, "更新": 1, "目标独有": 2}
            def key_func(item):
                return status_order.get(item[idx], 999)
            return key_func
        else:
            def key_func(item):
                return str(item[idx]).lower()
            return key_func

    def sort_items():
        key_func = get_sort_key(sort_field.get())
        sorted_indices = sorted(range(len(all_data)),
                                key=lambda i: key_func(all_data[i]), reverse=sort_reverse.get())

        # 清空 Treeview
        for child in tree.get_children():
            tree.delete(child)

        # 重新插入
        for new_pos, idx in enumerate(sorted_indices):
            iid = str(idx)
            item = all_data[idx]
            display_name, status, real_name, size_kb, note, modid, version, mod_type, file_path = item
            checked = "☑" if selection_state.get(iid, False) else "☐"
            # 构建 tags
            tags = []
            if status == "新增":
                tags.append("new")
            elif status == "更新":
                tags.append("update")
            else:
                tags.append("target_only")
            if selection_state.get(iid, False):
                tags.append("highlight")
            tree.insert("", "end", iid=iid, values=(
                checked,
                display_name,
                status,
                mod_type,
                modid,
                version,
                size_kb,
                note
            ), tags=tuple(tags))

        # 强制刷新
        tree.update_idletasks()
        sort_btn.set_text("▼ 降序" if sort_reverse.get() else "▲ 升序")

    def toggle_sort_direction():
        sort_reverse.set(not sort_reverse.get())
        sort_items()

    # ---- 工具栏 ----
    toolbar_frame = tk.Frame(diff_win, bg=theme["bg"])
    toolbar_frame.grid(row=3, column=0, columnspan=2, pady=10, sticky="ew")

    # 左侧排序区域
    sort_frame = tk.Frame(toolbar_frame, bg=theme["bg"])
    sort_frame.pack(side="left", padx=10, fill="x")

    tk.Label(sort_frame, text="排序依据：", bg=theme["bg"], fg=theme["fg"]).pack(side="left")
    field_combo = ttk.Combobox(sort_frame, textvariable=sort_field,
                               values=["文件名", "状态", "类型", "Mod ID", "版本", "大小(KB)"],
                               state="readonly", width=10)
    field_combo.pack(side="left", padx=5)
    field_combo.bind("<<ComboboxSelected>>", lambda e: sort_items())
    sort_btn = create_gradient_button(sort_frame, "▲ 升序", toggle_sort_direction,
                                      colors=("#607d8b", "#90a4ae"),
                                      hover_colors=("#78909c", "#b0bec5"),
                                      width=76, height=28, font=("微软雅黑", 9, "bold"))
    sort_btn.pack(side="left", padx=5)

    # 右侧按钮区域
    btn_frame = tk.Frame(toolbar_frame, bg=theme["bg"])
    btn_frame.pack(side="right", padx=10)

    def select_by_status(status):
        for iid, item in enumerate(all_data):
            if item[1] == status:
                selection_state[str(iid)] = True
                tree.set(str(iid), "选择", "☑")
            else:
                selection_state[str(iid)] = False
                tree.set(str(iid), "选择", "☐")
        update_highlight()

    def select_all():
        for iid in selection_state.keys():
            selection_state[iid] = True
            tree.set(iid, "选择", "☑")
        update_highlight()

    def deselect_all():
        for iid in selection_state.keys():
            selection_state[iid] = False
            tree.set(iid, "选择", "☐")
        update_highlight()

    def apply_selection():
        selected_files = []
        for iid, checked in selection_state.items():
            if checked:
                values = tree.item(iid, "values")
                selected_files.append(values[1])
        if not selected_files:
            messagebox.showwarning("提示", "没有勾选任何模组")
            return
        apply_callback(selected_files)
        diff_win.destroy()

    # 按状态全选按钮（按状态配色，加回鲜艳色）
    create_gradient_button(btn_frame, "✅ 全选新增", lambda: select_by_status("新增"),
                           colors=("#43a047", "#66bb6a"),
                           hover_colors=("#66bb6a", "#43a047"),
                           width=92, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)
    create_gradient_button(btn_frame, "🔄 全选更新", lambda: select_by_status("更新"),
                           colors=("#fb8c00", "#ffb74d"),
                           hover_colors=("#ffa726", "#ffcc80"),
                           width=92, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)
    create_gradient_button(btn_frame, "📌 全选目标独有", lambda: select_by_status("目标独有"),
                           colors=("#757575", "#9e9e9e"),
                           hover_colors=("#8d8d8d", "#bdbdbd"),
                           width=122, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)

    create_gradient_button(btn_frame, "☑ 全选", select_all,
                           colors=("#607d8b", "#90a4ae"),
                           hover_colors=("#78909c", "#b0bec5"),
                           width=78, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)
    create_gradient_button(btn_frame, "☐ 取消全选", deselect_all,
                           colors=("#607d8b", "#90a4ae"),
                           hover_colors=("#78909c", "#b0bec5"),
                           width=96, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)
    create_gradient_button(btn_frame, "✅ 应用所选", apply_selection,
                           colors=("#00c853", "#00e676"),
                           hover_colors=("#00e676", "#00c853"),
                           width=104, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=10)
    create_gradient_button(btn_frame, "关闭", diff_win.destroy,
                           colors=("#757575", "#9e9e9e"),
                           hover_colors=("#8d8d8d", "#bdbdbd"),
                           width=64, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="right", padx=2)

    # ---- 底部统计 ----
    total = len(all_data)
    new_count = sum(1 for item in all_data if item[1] == "新增")
    update_count = sum(1 for item in all_data if item[1] == "更新")
    target_only_count = sum(1 for item in all_data if item[1] == "目标独有")
    tk.Label(diff_win,
             text=f"总计 {total} 项差异 | 新增 {new_count} | 更新 {update_count} | 目标独有 {target_only_count}",
             font=("微软雅黑", 9), bg=theme["bg"], fg=theme["fg"]).grid(row=4, column=0,
                                                                    columnspan=2, pady=5)

    # ---- 窗口居中 ----
    diff_win.update_idletasks()
    cur_width = diff_win.winfo_width()
    cur_height = diff_win.winfo_height()
    x = (diff_win.winfo_screenwidth() // 2) - (cur_width // 2)
    y = (diff_win.winfo_screenheight() // 2) - (cur_height // 2)
    diff_win.geometry(f"{cur_width}x{cur_height}+{x}+{y}")
    diff_win.deiconify()
    diff_win.focus_force()
    tree.focus_set()
    return diff_win


def update_diff_theme(diff_win, theme, current_theme):
    """更新已打开的差异窗口的主题"""
    # 更新当前主题记录，使后续打开的模组详情窗口使用正确主题
    diff_win._current_theme = theme
    diff_win._current_theme_name = current_theme
    diff_win.configure(bg=theme["bg"])

    def update_widgets(widget):
        try:
            if isinstance(widget, tk.Label):
                widget.configure(bg=theme["bg"], fg=theme["fg"])
            elif isinstance(widget, tk.Button):
                text = widget.cget("text")
                if text in ("✅ 全选新增", "🔄 全选更新", "📌 全选目标独有"):
                    bg_map = {"✅ 全选新增": theme["success_bg"], "🔄 全选更新": theme["warn_bg"],
                              "📌 全选目标独有": theme["neutral_bg"]}
                    widget.configure(bg=bg_map.get(text, theme["button_bg"]),
                                     fg=theme["fg"])
                elif text in ("☑ 全选", "☐ 取消全选", "关闭"):
                    widget.configure(bg=theme["button_bg"], fg=theme["button_fg"])
                elif text == "✅ 应用所选":
                    widget.configure(bg=theme["success_bg"], fg=theme["success_fg"])
                else:
                    widget.configure(bg=theme["button_bg"], fg=theme["button_fg"])
            elif isinstance(widget, tk.Frame):
                widget.configure(bg=theme["bg"])
            elif isinstance(widget, ttk.Combobox):
                style = ttk.Style()
                style.configure("TCombobox",
                                fieldbackground=theme["ttk_field_bg"],
                                background=theme["ttk_bg"],
                                foreground=theme["ttk_fg"])
            elif isinstance(widget, ttk.Treeview):
                style = ttk.Style()
                style.configure("Diff.Treeview",
                                background=theme["ttk_bg"],
                                fieldbackground=theme["ttk_bg"],
                                foreground=theme["ttk_fg"],
                                selectbackground=theme["ttk_select_bg"],
                                selectforeground=theme["ttk_select_fg"],
                                bordercolor=theme["bg"])
                style.configure("Diff.Treeview.Heading",
                                background=theme["button_bg"],
                                foreground=theme["fg"])
                # 更新高亮 tag 颜色
                widget.tag_configure("highlight", background=theme["highlight_bg"],
                                     foreground=theme["highlight_fg"])
                # 更新状态标签颜色
                widget.tag_configure("new", background=theme["success_bg"],
                                     foreground=theme["success_fg"])
                widget.tag_configure("update", background=theme["warn_bg"],
                                     foreground=theme["warn_fg"])
                widget.tag_configure("target_only", background=theme["neutral_bg"],
                                     foreground=theme["neutral_fg"])
                # 强制刷新 Treeview
                widget.update_idletasks()
            elif isinstance(widget, ttk.Scrollbar):
                style = ttk.Style()
                style_name = widget.cget("style")
                style.configure(style_name,
                                background=theme["button_bg"],
                                troughcolor=theme["bg"],
                                arrowcolor=theme["fg"],
                                bordercolor=theme["bg"],
                                lightcolor=theme["button_bg"],
                                darkcolor=theme["button_bg"],
                                relief="flat",
                                borderwidth=0)
        except:
            pass
        for child in widget.winfo_children():
            update_widgets(child)

    update_widgets(diff_win)

    # 更新顶部标签（特殊处理，在grid中）
    for child in diff_win.grid_slaves(row=0):
        if isinstance(child, tk.Label):
            child.configure(bg=theme["bg"], fg=theme["fg"])

    # 更新底部统计标签
    for child in diff_win.grid_slaves(row=4):
        if isinstance(child, tk.Label):
            child.configure(bg=theme["bg"], fg=theme["fg"])

    # 强制刷新整个窗口
    diff_win.update_idletasks()

    # 同步已打开的模组详情窗口主题
    for detail_win in getattr(diff_win, '_mod_detail_windows', []):
        try:
            if detail_win.winfo_exists():
                update_mod_detail_theme(detail_win, theme)
        except Exception:
            pass
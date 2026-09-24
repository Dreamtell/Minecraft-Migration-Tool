# ui/diff_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import os
import time
from utils.helpers import set_window_icon, create_gradient_button, lighten_color
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
    width, height = 1280, 620
    diff_win.geometry(f"{width}x{height}")
    # 工具栏是完整一行（含搜索框），别让窗口被拖窄到把按钮挤散
    diff_win.minsize(1240, 480)
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
        bordercolor=theme["bg"],  # 边框跟随主题
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
        background=[('active', lighten_color(theme["button_bg"]))]
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
    # 列名是内部 key（排序/取值都按它），只把「表头显示文字」换成带图标的版本
    _HEAD = {"选择": "☑ 选择", "文件名": "📄 文件名", "状态": "🔵 状态",
             "类型": "🧩 类型", "Mod ID": "🆔 Mod ID", "版本": "🔖 版本",
             "大小(KB)": "💾 大小(KB)", "备注": "📝 备注"}
    for col in columns:
        tree.heading(col, text=_HEAD.get(col, col))
    tree.column("选择", width=60, anchor="center", minwidth=60)
    tree.column("文件名", width=250, minwidth=150)
    tree.column("状态", width=100, minwidth=80)
    tree.column("类型", width=80, anchor="center", minwidth=60)
    tree.column("Mod ID", width=150, minwidth=100)
    tree.column("版本", width=120, minwidth=80)
    tree.column("大小(KB)", width=90, anchor="center", minwidth=80)
    tree.column("备注", width=300, minwidth=200, stretch=True)

    vsb = ttk.Scrollbar(diff_win, orient="vertical", command=tree.yview,
                        style="Diff.Vertical.TScrollbar")
    hsb = ttk.Scrollbar(diff_win, orient="horizontal", command=tree.xview,
                        style="Diff.Horizontal.TScrollbar")
    # 横向滚动条只在内容真的超出可视宽度时才出现：默认宽度下「备注」列会拉伸填满，
    # 常驻一条拖不动的滚动条只会让人困惑。
    hsb_state = {"shown": True}

    def on_xscroll(first, last):
        hsb.set(first, last)
        need = float(first) > 0.001 or float(last) < 0.999
        try:
            if need and not hsb_state["shown"]:
                hsb.grid()
                hsb_state["shown"] = True
            elif not need and hsb_state["shown"]:
                hsb.grid_remove()
                hsb_state["shown"] = False
        except Exception:
            pass

    tree.configure(yscrollcommand=vsb.set, xscrollcommand=on_xscroll)
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
            # 被搜索过滤掉的行不在树里，必须跳过，否则 tree.item 会抛 TclError
            if not tree.exists(iid):
                continue
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

    # 搜索范围 -> all_data 的字段下标
    # item = (display_name, status, real_name, size_kb, note, modid, version, mod_type, file_path)
    _SCOPE_FIELDS = {
        "全部": (0, 4, 5, 6, 7),
        "文件名": (0,),
        "Mod ID": (5,),
        "版本": (6,),
        "类型": (7,),
        "备注": (4,),
    }

    def matched_indices():
        """当前搜索条件下命中的 all_data 索引（保持原顺序）。"""
        q = search_var.get().strip().lower()
        if not q:
            return list(range(len(all_data)))
        fields = _SCOPE_FIELDS.get(search_scope.get(), (0,))
        hits = []
        for i, item in enumerate(all_data):
            for k in fields:
                if k < len(item) and q in str(item[k]).lower():
                    hits.append(i)
                    break
        return hits

    def sort_items():
        key_func = get_sort_key(sort_field.get())
        # 先按搜索条件过滤，再排序：表格里只放命中项
        visible = matched_indices()
        visible.sort(key=lambda i: key_func(all_data[i]), reverse=sort_reverse.get())

        # 清空 Treeview
        for child in tree.get_children():
            tree.delete(child)

        # 重新插入
        for new_pos, idx in enumerate(visible):
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
        # 底部统计：搜索过滤时提示「实际显示了几项」
        try:
            shown = len(tree.get_children())
            head = f"总计 {len(all_data)} 项差异"
            if search_var.get().strip():
                head += f"（已过滤，显示 {shown} 项）"
            stat_lbl.configure(
                text=f"{head} | 新增 {new_count} | 更新 {update_count} | "
                     f"目标独有 {target_only_count}")
        except Exception:
            pass

    def toggle_sort_direction():
        sort_reverse.set(not sort_reverse.get())
        sort_items()

    # ---- 工具栏 ----
    toolbar_frame = tk.Frame(diff_win, bg=theme["bg"])
    toolbar_frame.grid(row=3, column=0, columnspan=2, pady=10, sticky="ew")

    # 左侧搜索区：输入即过滤（250ms 防抖），可限定搜索范围
    search_frame = tk.Frame(toolbar_frame, bg=theme["bg"])
    search_frame.pack(side="left", padx=(10, 4))
    tk.Label(search_frame, text="🔍", bg=theme["bg"],
             fg=theme["fg"]).pack(side="left")
    search_var = tk.StringVar()
    search_entry = tk.Entry(search_frame, textvariable=search_var, width=16,
                            bg=theme["entry_bg"], fg=theme["entry_fg"],
                            insertbackground=theme["fg"])
    search_entry.pack(side="left", padx=4)
    search_scope = tk.StringVar(value="全部")
    scope_combo = ttk.Combobox(search_frame, textvariable=search_scope,
                               values=["全部", "文件名", "Mod ID", "版本", "类型", "备注"],
                               state="readonly", width=7)
    scope_combo.pack(side="left")
    scope_combo.bind("<<ComboboxSelected>>", lambda e: sort_items())

    _search_after = [None]

    def on_search_changed(*a):
        if _search_after[0] is not None:
            try:
                diff_win.after_cancel(_search_after[0])
            except Exception:
                pass
        _search_after[0] = diff_win.after(250, sort_items)

    search_var.trace("w", on_search_changed)

    # 左侧排序区域
    sort_frame = tk.Frame(toolbar_frame, bg=theme["bg"])
    sort_frame.pack(side="left", padx=(6, 10), fill="x")

    tk.Label(sort_frame, text="排序依据：", bg=theme["bg"], fg=theme["fg"]).pack(side="left")
    field_combo = ttk.Combobox(sort_frame, textvariable=sort_field,
                               values=["文件名", "状态", "类型", "Mod ID", "版本", "大小(KB)"],
                               state="readonly", width=10)
    field_combo.pack(side="left", padx=5)
    field_combo.bind("<<ComboboxSelected>>", lambda e: sort_items())
    sort_btn = create_gradient_button(sort_frame, "▲ 升序", toggle_sort_direction,
                                      colors=("#607d8b", "#90a4ae"),
                                      width=76, height=28, font=("微软雅黑", 9, "bold"))
    sort_btn.pack(side="left", padx=5)

    # 右侧按钮区域
    btn_frame = tk.Frame(toolbar_frame, bg=theme["bg"])
    btn_frame.pack(side="right", padx=10)

    def select_by_status(*statuses):
        """按一个或多个状态组合全选（例如 ("新增","更新") 即排除"目标独有"）。
        勾选状态记在 selection_state 里，与搜索过滤无关；
        但只有出现在表格里的行才能去 set，被过滤掉的行必须跳过。"""
        for iid, item in enumerate(all_data):
            on = item[1] in statuses
            key = str(iid)
            selection_state[key] = on
            if tree.exists(key):
                tree.set(key, "选择", "☑" if on else "☐")
        update_highlight()

    def select_all():
        for iid in selection_state.keys():
            selection_state[iid] = True
            if tree.exists(iid):
                tree.set(iid, "选择", "☑")
        update_highlight()

    def deselect_all():
        for iid in selection_state.keys():
            selection_state[iid] = False
            if tree.exists(iid):
                tree.set(iid, "选择", "☐")
        update_highlight()

    def apply_selection():
        """应用所选：直接按 all_data 取值，不依赖表格行——
        这样即使某行正被搜索过滤掉，它仍然会被正确应用。"""
        selected_files = []
        for iid, checked in selection_state.items():
            if not checked:
                continue
            try:
                selected_files.append(all_data[int(iid)][0])
            except Exception:
                if tree.exists(iid):
                    selected_files.append(tree.item(iid, "values")[1])
        if not selected_files:
            messagebox.showwarning("提示", "没有勾选任何模组")
            return
        apply_callback(selected_files)
        diff_win.destroy()

    # 按钮区排成一行：把 3 个组合全选收进下拉菜单，避免挤成两行
    combo_menu = tk.Menu(diff_win, tearoff=0)
    combo_menu.add_command(label="新增 + 更新（排除目标独有）",
                           command=lambda: select_by_status("新增", "更新"))
    combo_menu.add_command(label="新增 + 目标独有",
                           command=lambda: select_by_status("新增", "目标独有"))
    combo_menu.add_command(label="更新 + 目标独有",
                           command=lambda: select_by_status("更新", "目标独有"))

    def show_combo_menu():
        try:
            x = combo_btn.winfo_rootx()
            y = combo_btn.winfo_rooty() + combo_btn.winfo_height()
            combo_menu.tk_popup(x, y)
        except Exception:
            pass
        finally:
            try:
                combo_menu.grab_release()
            except Exception:
                pass

    # 按状态全选
    create_gradient_button(btn_frame, "✅ 全选新增", lambda: select_by_status("新增"),
                           colors=("#43a047", "#66bb6a"),
                           width=92, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)
    create_gradient_button(btn_frame, "🔄 全选更新", lambda: select_by_status("更新"),
                           colors=("#fb8c00", "#ffb74d"),
                           width=92, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)
    create_gradient_button(btn_frame, "📌 全选目标独有", lambda: select_by_status("目标独有"),
                           colors=("#757575", "#9e9e9e"),
                           width=122, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)
    combo_btn = create_gradient_button(btn_frame, "▾ 组合选择", show_combo_menu,
                                       colors=("#26a69a", "#4dd0e1"),
                                       width=98, height=28,
                                       font=("微软雅黑", 9, "bold"))
    combo_btn.pack(side="left", padx=2)
    # 全选 / 取消全选
    create_gradient_button(btn_frame, "☑ 全选", select_all,
                           colors=("#607d8b", "#90a4ae"),
                           width=76, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=(8, 2))
    create_gradient_button(btn_frame, "☐ 取消全选", deselect_all,
                           colors=("#607d8b", "#90a4ae"),
                           width=94, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)
    # 应用 / 关闭
    create_gradient_button(btn_frame, "✅ 应用所选", apply_selection,
                           colors=("#00c853", "#00e676"),
                           width=102, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=(8, 2))
    create_gradient_button(btn_frame, "关闭", diff_win.destroy,
                           colors=("#757575", "#9e9e9e"),
                           width=62, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)

    # ---- 底部统计 ----
    total = len(all_data)
    new_count = sum(1 for item in all_data if item[1] == "新增")
    update_count = sum(1 for item in all_data if item[1] == "更新")
    target_only_count = sum(1 for item in all_data if item[1] == "目标独有")
    stat_lbl = tk.Label(
        diff_win,
        text=f"总计 {total} 项差异 | 新增 {new_count} | 更新 {update_count} | 目标独有 {target_only_count}",
        font=("微软雅黑", 9), bg=theme["bg"], fg=theme["fg"])
    stat_lbl.grid(row=4, column=0, columnspan=2, pady=5)

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
            elif isinstance(widget, tk.Entry):
                widget.configure(bg=theme["entry_bg"], fg=theme["entry_fg"],
                                 insertbackground=theme["fg"])
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

# ui/diff_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import os
from utils.helpers import set_window_icon
from ui.dialogs import show_mod_detail


def show_diff_window(parent, data, theme, apply_callback):
    """
    显示差异列表窗口
    parent: 父窗口
    data: 差异数据列表
    theme: 主题字典
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
    parent_ref = parent
    def on_diff_destroy(event):
        if hasattr(parent, 'diff_window'):
            parent.diff_window = None

    diff_win.bind("<Destroy>", on_diff_destroy)

    tk.Label(diff_win, text="以下为扫描结果，勾选你希望复制到目标的模组：",
             font=("微软雅黑", 10), bg=theme["bg"], fg=theme["fg"]).grid(
        row=0, column=0, columnspan=2, pady=5, sticky="w", padx=10)

    columns = ("选择", "文件名", "状态", "类型", "Mod ID", "版本", "大小(KB)", "备注")
    tree = ttk.Treeview(diff_win, columns=columns, show="headings", height=18)
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

    vsb = ttk.Scrollbar(diff_win, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(diff_win, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=1, column=0, sticky="nsew")
    vsb.grid(row=1, column=1, sticky="ns")
    hsb.grid(row=2, column=0, sticky="ew")

    diff_win.grid_rowconfigure(1, weight=1)
    diff_win.grid_columnconfigure(0, weight=1)

    style = ttk.Style()
    if style.theme_use() != 'clam':
        try:
            style.theme_use('clam')
        except:
            pass
    style.configure("Treeview",
                    background=theme["bg"],
                    foreground=theme["fg"],
                    fieldbackground=theme["bg"])
    style.configure("Treeview.Heading",
                    background=theme["button_bg"],
                    foreground=theme["fg"])
    style.configure("Treeview", rowheight=24)
    tree.configure(style="Treeview")

    # 数据元组结构: (display_name, status, real_name, size_kb, note, modid, version, mod_type, file_path)
    all_data = data[:]
    selection_state = {}

    for idx, item in enumerate(all_data):
        display_name, status, real_name, size_kb, note, modid, version, mod_type, file_path = item
        default_checked = (status == "新增")
        checked_char = "☑" if default_checked else "☐"
        tags = ()
        if status == "新增":
            tags = ("new",)
        elif status == "更新":
            tags = ("update",)
        else:
            tags = ("target_only",)

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
        ), tags=tags)
        selection_state[iid] = default_checked

    tree.tag_configure("new", background="#d4edda" if theme == "light" else "#2d4a2d")
    tree.tag_configure("update", background="#fff3cd" if theme == "light" else "#4a3d2d")
    tree.tag_configure("target_only", background="#f8f9fa" if theme == "light" else "#3a3a3a")

    def toggle_selection(event):
        item_id = tree.focus()
        if not item_id:
            return
        current = selection_state.get(item_id, False)
        new_state = not current
        selection_state[item_id] = new_state
        tree.set(item_id, "选择", "☑" if new_state else "☐")

    tree.bind("<ButtonRelease-1>", toggle_selection)

    def on_double_click(event):
        item_id = tree.focus()
        if not item_id:
            return
        idx = int(item_id)
        if idx < len(all_data):
            file_path = all_data[idx][8]
            if file_path and os.path.exists(file_path):
                show_mod_detail(diff_win, file_path, theme)
            else:
                messagebox.showerror("错误", "找不到模组文件")

    tree.bind("<Double-1>", on_double_click)

    # 排序功能
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
        sorted_indices = sorted(range(len(all_data)), key=lambda i: key_func(all_data[i]), reverse=sort_reverse.get())
        for child in tree.get_children():
            tree.delete(child)
        for new_pos, idx in enumerate(sorted_indices):
            iid = str(idx)
            item = all_data[idx]
            display_name, status, real_name, size_kb, note, modid, version, mod_type, file_path = item
            checked = "☑" if selection_state.get(iid, False) else "☐"
            tags = ()
            if status == "新增":
                tags = ("new",)
            elif status == "更新":
                tags = ("update",)
            else:
                tags = ("target_only",)
            tree.insert("", "end", iid=iid, values=(
                checked,
                display_name,
                status,
                mod_type,
                modid,
                version,
                size_kb,
                note
            ), tags=tags)
            if selection_state.get(iid, False):
                tree.selection_add(iid)
        sort_btn.config(text="▼ 降序" if sort_reverse.get() else "▲ 升序")

    def toggle_sort_direction():
        sort_reverse.set(not sort_reverse.get())
        sort_items()

    # 工具栏
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
    sort_btn = tk.Button(sort_frame, text="▲ 升序", command=toggle_sort_direction,
                         bg=theme["button_bg"], fg=theme["button_fg"],
                         relief=tk.RAISED, width=8)
    sort_btn.pack(side="left", padx=5)

    # 右侧按钮区域
    btn_frame = tk.Frame(toolbar_frame, bg=theme["bg"])
    btn_frame.pack(side="right", padx=10)

    def select_by_status(status):
        for iid, item in enumerate(all_data):
            if item[1] == status:
                selection_state[str(iid)] = True
                tree.set(str(iid), "选择", "☑")
                tree.selection_add(str(iid))

    def select_all():
        for iid in selection_state.keys():
            selection_state[iid] = True
            tree.set(iid, "选择", "☑")
            tree.selection_add(iid)

    def deselect_all():
        for iid in selection_state.keys():
            selection_state[iid] = False
            tree.set(iid, "选择", "☐")
            tree.selection_remove(iid)

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

    # 添加按状态全选按钮
    tk.Button(btn_frame, text="✅ 全选新增", command=lambda: select_by_status("新增"),
              bg="#d4edda", width=10).pack(side="left", padx=2)
    tk.Button(btn_frame, text="🔄 全选更新", command=lambda: select_by_status("更新"),
              bg="#fff3cd", width=10).pack(side="left", padx=2)
    tk.Button(btn_frame, text="📌 全选目标独有", command=lambda: select_by_status("目标独有"),
              bg="#f8f9fa", width=14).pack(side="left", padx=2)

    tk.Button(btn_frame, text="☑ 全选", command=select_all, width=8,
              bg=theme["button_bg"], fg=theme["button_fg"]).pack(side="left", padx=2)
    tk.Button(btn_frame, text="☐ 取消全选", command=deselect_all, width=10,
              bg=theme["button_bg"], fg=theme["button_fg"]).pack(side="left", padx=2)
    tk.Button(btn_frame, text="✅ 应用所选", command=apply_selection,
              bg="lightgreen" if theme == "light" else "#2d6a2d", fg="white", width=12).pack(
        side="left", padx=10)
    tk.Button(btn_frame, text="关闭", command=diff_win.destroy, width=8,
              bg=theme["button_bg"], fg=theme["button_fg"]).pack(side="right", padx=2)

    # 底部统计
    total = len(all_data)
    new_count = sum(1 for item in all_data if item[1] == "新增")
    update_count = sum(1 for item in all_data if item[1] == "更新")
    target_only_count = sum(1 for item in all_data if item[1] == "目标独有")
    tk.Label(diff_win,
             text=f"总计 {total} 项差异 | 新增 {new_count} | 更新 {update_count} | 目标独有 {target_only_count}",
             font=("微软雅黑", 9), bg=theme["bg"], fg=theme["fg"]).grid(row=4, column=0, columnspan=2, pady=5)

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
    children = tree.get_children()
    if children:
        tree.selection_set(children[0])
        tree.focus(children[0])
    return diff_win
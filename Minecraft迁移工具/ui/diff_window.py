# ui/diff_window.py
import tkinter as tk
from tkinter import ttk, messagebox
import os
from utils.helpers import (set_window_icon, create_gradient_button, lighten_color,
                           RoundedEntry)
from ui.dialogs import show_mod_detail, update_mod_detail_theme
from ui.virtual_table import VirtualTable


def show_diff_window(parent, data, theme, current_theme, apply_callback, cards=False):
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

    # ---- 顶部：提示 + 视图切换（表格 / 卡片，和「放大查看」同一套） ----
    head_bar = tk.Frame(diff_win, bg=theme["bg"])
    head_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
    tk.Label(head_bar, text="以下为扫描结果，勾选你希望复制到目标的模组"
                           "（单击切勾选，双击看详情）：",
             font=("微软雅黑", 10), bg=theme["bg"], fg=theme["fg"]).pack(side="left")
    btn_view = create_gradient_button(
        head_bar, "🗂 卡片视图", lambda: toggle_view(),
        colors=("#7e57c2", "#9575cd"),
        width=118, height=28, font=("微软雅黑", 9, "bold"))
    btn_view.pack(side="right")

    # 要让 ttk 的 Combobox（搜索范围/排序依据）跟着主题走，得先在 clam 主题下
    # 配置样式 —— 原来这里还配了 Diff.Treeview / TScrollbar，换成自绘表格后不需要了
    style = ttk.Style()
    if style.theme_use() != 'clam':
        try:
            style.theme_use('clam')
        except:
            pass

    # ---- 创建自绘表格（和「放大查看」「迁移历史」同一套） ----
    # 原来这里是 ttk.Treeview：它**只能整行滚**（yview_scroll 的单位就是行），
    # 一格 3 行必然一跳一跳的，做不出编辑器那种顺滑手感。自绘表格把
    # yscrollincrement 设成 1、滚动单位变成 1 像素，才能"停在半行上"。
    columns = (("选择", "☑ 选择", 64, "center"),
               ("文件名", "📄 文件名", 250, "w"),
               ("状态", "🔵 状态", 96, "w"),
               ("类型", "🧩 类型", 84, "center"),
               ("Mod ID", "🆔 Mod ID", 150, "w"),
               ("版本", "🔖 版本", 120, "w"),
               ("大小(KB)", "💾 大小(KB)", 92, "e"),
               ("备注", "📝 备注", 300, "w"))
    列名 = [c[0] for c in columns]

    def make_tag_styles(th):
        """行底色：勾选(选中蓝) > 新增(绿) / 更新(橙) / 降级(红) / 目标独有(灰)。"""
        return {
            "highlight": (th.get("card_sel_bg", "#d4e6f8"),
                          th.get("card_sel_fg", "#0d3d63")),
            "new": (th["success_bg"], th["success_fg"]),
            "update": (th["warn_bg"], th["warn_fg"]),
            "downgrade": (th["danger_bg"], th["danger_fg"]),
            "target_only": (th["neutral_bg"], th["neutral_fg"]),
        }

    table = VirtualTable(
        diff_win, columns, theme,
        font=("微软雅黑", 10), row_height=26, header_height=30,
        status_key="状态",                  # 状态列用彩色圆点 + 文字
        tag_styles=make_tag_styles(theme),
        on_row_click=lambda row, ev: _click_row(row),
        on_row_double=lambda row, ev: _double_row(row),
        on_header_click=lambda key, ev: sort_by_column(key))
    diff_win._make_tag_styles = make_tag_styles      # 主题切换时重算行底色
    diff_win._diff_table = table
    table.grid(row=1, column=0, sticky="nsew")
    diff_win.grid_rowconfigure(1, weight=1)
    diff_win.grid_columnconfigure(0, weight=1)

    # ---- 数据加载 ----
    all_data = data[:]
    selection_state = {}          # all_data 的下标 -> 是否勾选（与搜索过滤无关）
    visible = []                  # 表格当前显示的行 -> all_data 下标

    for idx, item in enumerate(all_data):
        selection_state[idx] = (item[1] == "新增")     # 「新增」默认勾上

    def _row_item(row):
        return all_data[visible[row]] if 0 <= row < len(visible) else None

    def _status_color(status):
        return {"新增": theme.get("ok_fg", "#2e7d32"),
                "更新": theme.get("log_warning_fg", "#e65100"),
                "降级": theme.get("fail_fg", "#c62828")}.get(
                    status, theme.get("muted_fg", "#808080"))

    def _cell(row, key):
        it = _row_item(row)
        if it is None:
            return ""
        # item = (display_name, status, real_name, size_kb, note, modid, version, mod_type, file_path)
        if key == "选择":
            return "☑" if selection_state.get(visible[row]) else "☐"
        if key == "文件名":
            return str(it[0])
        if key == "状态":
            return str(it[1])
        if key == "类型":
            return str(it[7])
        if key == "Mod ID":
            return str(it[5])
        if key == "版本":
            return str(it[6])
        if key == "大小(KB)":
            return str(it[3])
        if key == "备注":
            return str(it[4])
        return ""

    def _dot(row):
        """状态列：彩色圆点 + 文字（新增=绿、更新=橙、降级=红、目标独有=灰）。"""
        it = _row_item(row)
        st = str(it[1]) if it is not None else ""
        return _status_color(st), st

    def _tags(row):
        """行底色：勾选(选中蓝) 优先于 状态底色 —— 和原来 Treeview 的 tag 顺序一致。"""
        it = _row_item(row)
        if it is None:
            return ()
        st = {"新增": "new", "更新": "update", "降级": "downgrade"}.get(
            it[1], "target_only")
        if selection_state.get(visible[row]):
            return ("highlight", st)
        return (st,)

    class _DiffModel:
        row_count = staticmethod(lambda: len(visible))
        cell = staticmethod(_cell)
        dot = staticmethod(_dot)
        tags = staticmethod(_tags)

    table.set_model(_DiffModel())

    # ---- 交互：单击切勾选，双击开详情（勾选状态不变） ----

    def _click_row(row):
        if not (0 <= row < len(visible)):
            return
        idx = visible[row]
        selection_state[idx] = not selection_state.get(idx, False)
        table.repaint_row(row)

    def _double_row(row):
        """双击：开详情，勾选保持不变。

        第二下 Tk 只发 <Double-Button-1>（<Button-1> 不再触发），所以单击那次
        切换要在这里撤回来 —— 和原 Treeview 版的逻辑一致。
        """
        if not (0 <= row < len(visible)):
            return
        idx = visible[row]
        selection_state[idx] = not selection_state.get(idx, False)
        table.repaint_row(row)
        open_mod_detail(idx)

    def open_mod_detail(idx):
        """打开第 idx 条（all_data 下标）的模组详情。"""
        try:
            idx = int(idx)
        except (ValueError, TypeError):
            return
        if 0 <= idx < len(all_data):
            file_path = all_data[idx][8]
            if file_path and os.path.exists(file_path):
                # 使用当前主题（切换主题后仍正确）
                show_mod_detail(diff_win, file_path,
                                getattr(diff_win, '_current_theme', theme))
            else:
                messagebox.showerror("错误", "找不到模组文件")

    # ---- 卡片视图（和「放大查看」同一个控件） ----
    view_state = {"cards": False}
    card_state = {"list": None}
    # 差异列表的状态没有 ✅/❌ 前缀，给卡片列表一套自己的 chip 配色
    _STATUS_CHIP = {"新增": ("#2e7d32", "#ffffff"),
                    "更新": ("#e65100", "#ffffff"),
                    "降级": ("#c62828", "#ffffff"),
                    "目标独有": ("#546e7a", "#ffffff")}

    def card_rows():
        """按当前显示顺序生成卡片数据（和表格共用 visible / selection_state）。"""
        rows_out = []
        for idx in visible:
            it = all_data[idx]
            sub = " · ".join(x for x in (str(it[5]), str(it[7])) if x and x != "?")
            rows_out.append({
                "title": str(it[0]),
                "subtitle": sub,
                "version": str(it[6]),
                "desc": str(it[4]) or "",
                "icon_key": str(it[8]),
                "status": str(it[1]),
                "checked": bool(selection_state.get(idx)),
                "dc_key": str(idx),
            })
        return rows_out

    def _card_icon(row):
        """卡片图标：从 jar 里取（解析有成本，卡片列表只在滚到时才要）。"""
        path = row.get("icon_key") or ""
        if not path or not os.path.exists(path):
            return None
        try:
            from core.scanner import get_mod_icon
            from PIL import Image
            icon_path = get_mod_icon(path)
            return Image.open(icon_path).convert("RGBA") if icon_path else None
        except Exception:
            return None

    def _card_check(i):
        """卡片上的勾选：和表格视图共用同一份状态，只重画这一张卡。"""
        if not (0 <= i < len(visible)):
            return
        idx = visible[i]
        selection_state[idx] = not selection_state.get(idx, False)
        if card_state["list"] is not None:
            card_state["list"].update_row(i, card_rows()[i])

    def _ensure_card():
        if card_state["list"] is not None:
            return card_state["list"]
        from ui.card_list import ModCardList, default_fallback_icon
        card = ModCardList(diff_win, theme, icon_provider=_card_icon,
                           fallback_icon=default_fallback_icon(),
                           status_colors=_STATUS_CHIP,
                           on_check=_card_check,
                           on_double_click=lambda i, e: open_mod_detail(
                               visible[i] if 0 <= i < len(visible) else -1))
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_remove()
        card_state["list"] = card
        diff_win._diff_card = card          # 主题切换/测试用得到
        return card

    def toggle_view():
        """表格 ⇄ 卡片。两种视图共用 visible / selection_state，切过去数据一致。"""
        try:
            view_state["cards"] = not view_state["cards"]
            if view_state["cards"]:
                card = _ensure_card()
                card.set_rows(card_rows())
                table.grid_remove()
                card.grid()
                btn_view.set_text("📋 表格视图")
            else:
                if card_state["list"] is not None:
                    card_state["list"].grid_remove()
                table.grid()
                btn_view.set_text("🗂 卡片视图")
        except Exception as exc:
            view_state["cards"] = False
            try:
                table.grid()
            except Exception:
                pass
            messagebox.showwarning("提示", f"卡片视图不可用：{exc}")

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

    def refresh_rows():
        """按当前搜索/排序条件重算「表格里显示哪些行」，然后刷新表格。

        不动 `_sorted_once`：刚打开时数据保持扫描顺序，表头也不该标个 ▲/▼。
        """
        key_func = get_sort_key(sort_field.get())
        # 先按搜索条件过滤，再排序：表格里只放命中项
        visible[:] = matched_indices()
        if _sorted_once[0]:
            visible.sort(key=lambda i: key_func(all_data[i]),
                         reverse=sort_reverse.get())
        table.refresh()
        # 卡片视图开着的话，卡片也要跟着重算（两种视图共用 visible）
        if view_state["cards"] and card_state["list"] is not None:
            card_state["list"].set_rows(card_rows())
        # 底部统计：搜索过滤时提示「实际显示了几项」
        try:
            head = f"总计 {len(all_data)} 项差异"
            if search_var.get().strip():
                head += f"（已过滤，显示 {len(visible)} 项）"
            降级 = sum(1 for item in all_data if item[1] == "降级")
            尾巴 = f" | 降级 {降级}" if 降级 else ""
            stat_lbl.configure(
                text=f"{head} | 新增 {new_count} | 更新 {update_count}"
                     f"{尾巴} | 目标独有 {target_only_count}")
        except Exception:
            pass

    def sort_items():
        _sorted_once[0] = True          # 之后表头才显示 ▲/▼
        refresh_rows()
        sort_btn.set_text("▼ 降序" if sort_reverse.get() else "▲ 升序")
        update_sort_indicators()

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
    search_entry = RoundedEntry(search_frame, theme, textvariable=search_var, chars=16,
                                height=28)
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

    # 表头点击也能排序（和"放大查看"的表格一致，那儿一直是点表头）
    _SORT_COLS = {"文件名", "状态", "类型", "Mod ID", "版本", "大小(KB)"}
    _sorted_once = [False]      # 用户手动排过序没（没排过就不标箭头）

    def update_sort_indicators():
        """把 ▲/▼ 标在当前排序列的表头上，点了哪列一眼能看出来。

        没手动排过序时不标箭头：刚打开时表格是扫描顺序，标个"▲ 文件名"会误导。
        自绘表格自己画箭头（和「放大查看」一致）。
        """
        table.set_sort(sort_field.get() if _sorted_once[0] else None,
                       sort_reverse.get())

    def sort_by_column(col):
        """点表头：换一列就按新列升序，点同一列则切换升降序。"""
        if col not in _SORT_COLS:
            return
        if sort_field.get() == col:
            sort_reverse.set(not sort_reverse.get())
        else:
            sort_field.set(col)
            sort_reverse.set(False)
        sort_items()

    update_sort_indicators()

    # 右侧按钮区域
    btn_frame = tk.Frame(toolbar_frame, bg=theme["bg"])
    btn_frame.pack(side="right", padx=10)

    def select_by_status(*statuses):
        """按一个或多个状态组合全选（例如 ("新增","更新") 即排除"目标独有"）。
        勾选状态记在 selection_state 里，与搜索过滤无关 —— 被过滤掉的行一样会被应用。"""
        for idx, item in enumerate(all_data):
            selection_state[idx] = (item[1] in statuses)
        table.refresh()

    def select_all():
        for idx in list(selection_state):
            selection_state[idx] = True
        table.refresh()

    def deselect_all():
        for idx in list(selection_state):
            selection_state[idx] = False
        table.refresh()

    def apply_selection():
        """应用所选：直接按 all_data 取值，不依赖表格行——
        这样即使某行正被搜索过滤掉，它仍然会被正确应用。"""
        selected_files = [all_data[idx][0]
                          for idx, checked in selection_state.items() if checked]
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
                           colors=("#e53935", "#c62828"),
                           width=62, height=28,
                           font=("微软雅黑", 9, "bold")).pack(side="left", padx=2)

    # ---- 底部统计 ----
    total = len(all_data)
    new_count = sum(1 for item in all_data if item[1] == "新增")
    update_count = sum(1 for item in all_data if item[1] == "更新")
    target_only_count = sum(1 for item in all_data if item[1] == "目标独有")
    _降级数 = sum(1 for item in all_data if item[1] == "降级")
    stat_lbl = tk.Label(
        diff_win,
        text=(f"总计 {total} 项差异 | 新增 {new_count} | 更新 {update_count}"
              + (f" | 降级 {_降级数}" if _降级数 else "")
              + f" | 目标独有 {target_only_count}"),
        font=("微软雅黑", 9), bg=theme["bg"], fg=theme["fg"])
    stat_lbl.grid(row=4, column=0, columnspan=2, pady=5)

    # ---- 窗口居中 ----
    diff_win.update_idletasks()
    refresh_rows()               # 首次把数据填进表格（原来 Treeview 是建表时直接 insert）
    table.fit_now()              # 显示前先按实际宽度排好列宽，避免二次闪烁
    cur_width = diff_win.winfo_width()
    cur_height = diff_win.winfo_height()
    x = (diff_win.winfo_screenwidth() // 2) - (cur_width // 2)
    y = (diff_win.winfo_screenheight() // 2) - (cur_height // 2)
    diff_win.geometry(f"{cur_width}x{cur_height}+{x}+{y}")

    # 设置里选了"打开就是卡片"：直接切过去（等价于用户点一下那个切换按钮）。
    # 放在 deiconify 之前 —— 先把视图摆好再显示，不然会看到"先表格后卡片"闪一下。
    if cards:
        try:
            toggle_view()
        except Exception:
            pass

    diff_win.deiconify()
    diff_win.focus_force()
    table.body.focus_set()
    return diff_win


def update_diff_theme(diff_win, theme, current_theme):
    """更新已打开的差异窗口的主题"""
    # 更新当前主题记录，使后续打开的模组详情窗口使用正确主题
    diff_win._current_theme = theme
    diff_win._current_theme_name = current_theme
    diff_win.configure(bg=theme["bg"])
    from ui.card_list import ModCardList       # 延迟导入（只在切主题时才需要）

    def update_widgets(widget):
        try:
            if isinstance(widget, ModCardList):
                # 卡片列表也是自绘的（Frame 子类，必须排在 tk.Frame 前面）
                widget.apply_theme(theme)
            elif isinstance(widget, VirtualTable):
                # 自绘表格：底色/表头/行都要显式喂新主题；行底色是按语义算的，
                # 得用建窗时留下的那套（apply_theme 自带的默认 tag 不够用）。
                # 注意必须排在最前 —— 它是 tk.Frame 的子类，会被 tk.Frame 分支吃掉。
                widget.apply_theme(theme)
                maker = getattr(diff_win, "_make_tag_styles", None)
                if maker is not None:
                    widget.set_tag_styles(maker(theme))
            elif isinstance(widget, tk.Label):
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

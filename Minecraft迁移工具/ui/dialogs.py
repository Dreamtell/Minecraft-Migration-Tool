# ui/dialogs.py
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
import re
import difflib
import threading
import queue
import webbrowser
from utils.helpers import set_window_icon, create_gradient_button
from core.scanner import get_full_mod_metadata
from core.mod_search import search_modrinth, fetch_project_latest, format_downloads
from utils.theme import LIGHT_THEME, apply_theme_to_widget_tree  # 新增导入


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
        self.win.geometry("500x240")  # 稍微增高一点容纳步骤标签
        self.win.transient(parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self.on_cancel)
        set_window_icon(self.win)

        # 步骤标签（显示当前操作阶段）
        self.step_label = tk.Label(
            self.win,
            text="准备中...",
            anchor="w",
            font=("微软雅黑", 9, "bold"),
            fg="#4fc3f7"
        )
        self.step_label.pack(fill="x", padx=10, pady=(10, 0))

        self.file_label = tk.Label(self.win, text="准备中...", anchor="w")
        self.file_label.pack(fill="x", padx=10, pady=5)

        # 从父窗口获取主题
        if hasattr(parent, 'theme'):
            theme = parent.theme
        else:
            theme = LIGHT_THEME

        # 配置进度条样式
        style = ttk.Style()
        style.configure(
            "Custom.Horizontal.TProgressbar",
            background=theme.get("ttk_progress_bg", "#4fc3f7"),
            troughcolor=theme.get("ttk_trough_bg", "#e0e0e0"),
            bordercolor=theme.get("bg", "#f0f0f0"),
            lightcolor=theme.get("ttk_progress_bg", "#4fc3f7"),
            darkcolor=theme.get("ttk_progress_bg", "#4fc3f7")
        )
        self.progress = ttk.Progressbar(
            self.win,
            length=460,
            mode='determinate',
            style="Custom.Horizontal.TProgressbar"
        )
        self.progress.pack(padx=10, pady=5)

        self.stats_label = tk.Label(self.win, text="0 / 0 个文件  |  0.0 MB / 0.0 MB",
                                    anchor="w")
        self.stats_label.pack(fill="x", padx=10, pady=5)

        self.cancel_btn = create_gradient_button(self.win, "取消迁移", self.on_cancel,
                                                 colors=("#e53935", "#ef5350"),
                                                 hover_colors=("#ef5350", "#e53935"),
                                                 width=104, height=30,
                                                 font=("微软雅黑", 9, "bold"))
        self.cancel_btn.pack(pady=10)

        self.hint_label = tk.Label(
            self.win,
            text="⚠️ 迁移进行中，请勿关闭主窗口！",
            fg=theme.get("fail_fg", "red"),
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
        self.cancel_btn.state(tk.DISABLED)
        self.cancel_btn.set_text("正在取消...")

    def update_progress(self, file_index, file_name, copied_bytes, step=None):
        if step is not None:
            self.step_label.config(text=f"📦 {step}")
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
            background=theme.get("ttk_progress_bg", "#4fc3f7"),
            troughcolor=theme.get("ttk_trough_bg", "#e0e0e0"),
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


def _configure_mod_detail_styles(theme):
    """配置模组详情窗口使用的 ttk 样式（Treeview/滚动条），跟随主题。"""
    style = ttk.Style()
    try:
        if style.theme_use() != 'clam':
            style.theme_use('clam')
    except Exception:
        pass
    style.configure(
        "Detail.Treeview",
        background=theme["ttk_bg"], fieldbackground=theme["ttk_bg"],
        foreground=theme["ttk_fg"],
        selectbackground=theme.get("sel_bg", "#a5d6a7"),
        selectforeground=theme.get("sel_fg", "#000000"),
        borderwidth=0, rowheight=22
    )
    style.configure(
        "Detail.Treeview.Heading",
        background=theme["button_bg"], foreground=theme["fg"],
        relief="flat", borderwidth=0
    )
    style.configure(
        "Detail.Vertical.TScrollbar",
        background=theme["button_bg"], troughcolor=theme["bg"],
        arrowcolor=theme["fg"], bordercolor=theme["bg"],
        lightcolor=theme["button_bg"], darkcolor=theme["button_bg"],
        relief="flat", borderwidth=0
    )


def update_mod_detail_theme(win, theme):
    """更新已打开的模组详情窗口的主题颜色（递归应用到所有子控件）"""
    try:
        win.configure(bg=theme["bg"])
        apply_theme_to_widget_tree(win, theme)
        _configure_mod_detail_styles(theme)
        if hasattr(win, '_search_tree'):
            st = win._search_tree
            st.tag_configure("match", background=theme.get("highlight_bg", "#cce5ff"),
                             foreground=theme.get("highlight_fg", "#000000"))
            st.tag_configure("update", background=theme.get("warn_bg", "#ffeaa7"),
                             foreground=theme.get("warn_fg", "#000000"))
            st.tag_configure("odd", background=theme.get("neutral_bg", "#f8f9fa"),
                             foreground=theme.get("neutral_fg", "#000000"))
            st.tag_configure("sel", background=theme.get("sel_bg", "#a5d6a7"),
                             foreground=theme.get("sel_fg", "#000000"))
        # 恢复在线搜索区里"特意设置了颜色"的提示标签（apply_theme_to_widget_tree 会把它们改成 label_fg）
        if hasattr(win, '_search_muted_hint'):
            win._search_muted_hint.config(fg=theme.get("muted_fg", "gray"))
        if hasattr(win, '_search_status'):
            win._search_status.config(fg=getattr(win._search_status, '_last_fg', theme.get("ok_fg", "green")))

        win.update_idletasks()
    except Exception:
        pass


def show_mod_detail(parent, jar_path, theme):
    """显示模组详细信息窗口（独立函数），带联网搜索模组/download链接功能。"""
    info = get_full_mod_metadata(jar_path)
    win = tk.Toplevel(parent)
    win.title(f"模组详情 - {os.path.basename(jar_path)}")
    win.minsize(700, 540)
    win.transient(parent)
    win.withdraw()   # 先隐藏，等构建完再居中显示，避免"闪现-居中"闪动
    set_window_icon(win)
    _configure_mod_detail_styles(theme)
    fail = theme.get("fail_fg", "red")
    ok = theme.get("ok_fg", "green")

    tk.Label(win, text=f"文件: {os.path.basename(jar_path)}", font=("微软雅黑", 10,
                                                                  "bold")).pack(pady=(8, 2))
    details = [
        f"模组名称: {info['name']}",
        f"Mod ID: {info['modid']}",
        f"版本: {info['version']}",
        f"类型: {info['mod_type']}",
        f"作者: {info['authors']}",
        f"描述: {info['description']}",
        f"依赖: {info['dependencies']}"
    ]
    text = scrolledtext.ScrolledText(win, wrap=tk.WORD, height=6, width=70)
    text.pack(padx=10, pady=5, fill="x", expand=False)
    text.insert(tk.END, "\n".join(details))
    text.config(state=tk.DISABLED)

    # ---- 联网搜索区 ----
    search_frame = tk.LabelFrame(win, text="🔍 联网搜索模组（Modrinth）", padx=5, pady=5)
    search_frame.pack(fill="both", expand=True, padx=10, pady=5)

    top = tk.Frame(search_frame)
    top.pack(fill="x", pady=(2, 4))
    tk.Label(top, text="搜索词:").pack(side="left")
    search_var = tk.StringVar(value=info["name"] or info["modid"] or "")
    search_entry = tk.Entry(top, textvariable=search_var, width=44)
    search_entry.pack(side="left", padx=5)
    search_btn = create_gradient_button(top, "🔍 联网搜索", None,
                                        colors=("#00bcd4", "#26c6da"),
                                        hover_colors=("#26c6da", "#00bcd4"),
                                        width=104, height=28,
                                        font=("微软雅黑", 9, "bold"))
    search_btn.pack(side="left", padx=5)
    muted_hint = tk.Label(top, text="（自动置顶最匹配项）",
                          fg=theme.get("muted_fg", "gray"))
    muted_hint.pack(side="left", padx=8)
    win._search_muted_hint = muted_hint

    status_lbl = tk.Label(search_frame, text="可修改搜索词后回车或点击“联网搜索”。", anchor="w", fg=ok)
    status_lbl._last_fg = ok  # 记录当前状态色，主题刷新时按此恢复（避免被重点名地改成黑色）
    status_lbl.pack(fill="x", pady=(0, 2))
    win._search_status = status_lbl

    columns = ("name", "author", "downloads", "version", "slug")
    # 结果表格放进独立子框并撑满，避免下方"操作行"被横向挤掉
    tree_frame = tk.Frame(search_frame)
    tree_frame.pack(fill="both", expand=True, pady=2)
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=7,
                        style="Detail.Treeview")
    tree.configure(selectmode="none")  # 用自定义绿色选中标签，避免 ttk 灰色选中盖掉颜色
    for col, txt, wd, anc in (("name", "名称", 230, "w"), ("author", "作者", 105, "w"),
                              ("downloads", "下载量", 80, "e"), ("version", "最新版本", 150,
                                                              "w"),
                              ("slug", "项目ID", 110, "w")):
        tree.heading(col, text=txt)
        tree.column(col, width=wd, anchor=anc)
    tree.tag_configure("match", background=theme.get("highlight_bg", "#cce5ff"),
                       foreground=theme.get("highlight_fg", "#000000"))
    tree.tag_configure("update", background=theme.get("warn_bg", "#ffeaa7"),
                       foreground=theme.get("warn_fg", "#000000"))
    tree.tag_configure("odd", background=theme.get("neutral_bg", "#f8f9fa"),
                       foreground=theme.get("neutral_fg", "#000000"))
    tree.tag_configure("sel", background=theme.get("sel_bg", "#a5d6a7"),
                       foreground=theme.get("sel_fg", "#000000"))
    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview,
                        style="Detail.Vertical.TScrollbar")
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True, padx=5)
    vsb.pack(side="right", fill="y")
    tree.bind("<Double-1>", lambda e: open_project_page())
    tree.bind("<ButtonRelease-1>", lambda e: on_row_click(e))
    win._search_tree = tree

    act = tk.Frame(search_frame)
    act.pack(fill="x", pady=3)
    local_lbl = tk.Label(act, text=f"本地版本: {info['version']}")
    local_lbl.pack(side="left", padx=5)
    copy_proj_btn = create_gradient_button(act, "🔗 复制项目链接", None,
                                           colors=("#607d8b", "#90a4ae"),
                                           hover_colors=("#78909c", "#b0bec5"),
                                           width=120, height=28,
                                           font=("微软雅黑", 9, "bold"))
    copy_link_btn = create_gradient_button(act, "📋 复制下载链接", None,
                                           colors=("#607d8b", "#90a4ae"),
                                           hover_colors=("#78909c", "#b0bec5"),
                                           width=120, height=28,
                                           font=("微软雅黑", 9, "bold"))
    open_btn = create_gradient_button(act, "🌐 打开下载页", None,
                                      colors=("#607d8b", "#90a4ae"),
                                      hover_colors=("#78909c", "#b0bec5"),
                                      width=112, height=28,
                                      font=("微软雅黑", 9, "bold"))
    open_btn.pack(side="right", padx=4)
    copy_link_btn.pack(side="right", padx=4)
    copy_proj_btn.pack(side="right", padx=4)

    create_gradient_button(win, "关闭", win.destroy,
                           colors=("#757575", "#9e9e9e"),
                           hover_colors=("#8d8d8d", "#bdbdbd"),
                           width=72, height=30, font=("微软雅黑", 9, "bold")).pack(pady=8)

    # ---- 状态与线程安全更新（队列 + 轮询） ----
    msg_queue = queue.Queue()
    search_state = {"running": False}
    result_items = {}
    base_tag = {}     # iid -> 基础标签（match/update/odd/''），供取消选中时恢复

    def set_status(msg, color=ok):
        status_lbl._last_fg = color
        status_lbl.config(text=msg, fg=color)

    def selected():
        # 用自己维护的当前选中行，避免 ttk 灰色选中态盖掉颜色
        iid = current_sel[0]
        return result_items.get(iid) if iid else None

    def on_row_click(event):
        """点击结果行 -> 该行单独标绿色选中标签，其余恢复基础标签（不触发 ttk 灰色选中态）。"""
        try:
            row = tree.identify_row(event.y)
            if not row:
                return
            current_sel[0] = row
            for iid in tree.get_children():
                if iid == row:
                    tree.item(iid, tags=("sel",))
                else:
                    bt = base_tag.get(iid, "")
                    tree.item(iid, tags=(bt,) if bt else ())
        except Exception:
            pass

    current_sel = [None]

    def _norm(s):
        """归一化：小写、去括号内容、非字母数字合并为空格，便于相似度比较。"""
        s = (str(s) or "").lower().strip()
        s = re.sub(r"[\(\[].*?[\)\]]", " ", s)
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return s.strip()

    def match_score(r):
        """给候选打分：精确 slug==本地 modid/名称 最高；否则用文本相似度衡量"最相似"。"""
        modid = _norm(info.get("modid"))
        name = _norm(info.get("name"))
        slug = _norm(r.get("slug"))
        title = _norm(r.get("title"))
        if modid and slug == modid:
            return 100
        if name and slug == name:
            return 90

        def ratio(a, b):
            if not a or not b:
                return 0.0
            return difflib.SequenceMatcher(None, a, b).ratio()

        best = 0.0
        if modid:
            best = max(best, ratio(slug, modid), ratio(title, modid))
        if name:
            best = max(best, ratio(slug, name), ratio(title, name))
        # 相似度(0~1)映射为 0~70 分；分数最高者即"最相似"
        return int(best * 70)

    def show_results(results):
        search_state["running"] = False
        search_btn.state(tk.NORMAL)
        for iid in tree.get_children():
            tree.delete(iid)
        result_items.clear()
        if not results:
            set_status("没有找到相关模组，换个关键词试试。", fail)
            return
        # 按相似度打分排序，分数最高者置顶并标绿（"最相似"）
        scored = [(match_score(r), i, r) for i, r in enumerate(results)]
        scored.sort(key=lambda t: (-t[0], t[1]))
        best = scored[0][0] if scored else 0
        MIN = 20  # 相似度阈值：达到才标记为"最相似"
        for pos, (score, orig_i, r) in enumerate(scored):
            iid = str(pos)
            is_match = (pos == 0 and best >= MIN)
            tag = "match" if is_match else ("odd" if pos % 2 else "")
            title = ("★ " + r["title"]) if is_match else r["title"]
            tree.insert("", "end", iid=iid, values=(
                title, r["author"], format_downloads(r["downloads"]),
                "获取中…", r.get("slug", "")), tags=(tag,) if tag else ())
            base_tag[iid] = tag
            result_items[iid] = r
        # 后台并发拉取各候选最新版本（使用排序后位置，与 iid 一致）
        for pos, (score, orig_i, r) in enumerate(scored):
            pid = r.get("project_id")
            threading.Thread(target=lambda pid=pid, i=pos: _fetch_version_for(pid, i),
                             daemon=True).start()
        extra = "★为最相似项，已置顶。" if best >= MIN else "未找到相似度足够的候选。"
        set_status(f"找到 {len(results)} 个结果。{extra} 版本/下载链接加载中…（🔵蓝=最相似, 🟡黄=可更新）", ok)
        # 后台并发拉取各候选的最新版本，逐行填充
        for i, r in enumerate(results):
            pid = r.get("project_id")
            threading.Thread(target=lambda pid=pid, i=i: _fetch_version_for(pid, i),
                             daemon=True).start()

    def _fetch_version_for(pid, i):
        try:
            d = fetch_project_latest(pid)
        except Exception:
            d = {"latest_version": "", "download_url": ""}
        msg_queue.put(("version", (i, d["latest_version"], d["download_url"])))

    def _apply_version(i, vnum, url):
        iid = str(i)
        if iid not in result_items:
            return
        result_items[iid]["latest_version"] = vnum
        result_items[iid]["download_url"] = url
        is_match = "match" in tree.item(iid, "tags")
        local_ver = str(info.get("version") or "")
        updatable = bool(vnum) and bool(local_ver) and vnum != local_ver
        disp = (vnum + " ⬆") if updatable else (vnum or "未知")
        tree.set(iid, "version", disp)
        # 若该行正被选中，保持绿色选中标签；否则按 最相似/可更新/斑马纹 恢复
        if current_sel[0] == iid:
            tree.item(iid, tags=("sel",))
        elif is_match:
            base_tag[iid] = "match"
            tree.item(iid, tags=("match",))
        elif updatable:
            base_tag[iid] = "update"
            tree.item(iid, tags=("update",))
        else:
            base_tag[iid] = "odd" if (i % 2) else ""
            bt = base_tag[iid]
            tree.item(iid, tags=(bt,) if bt else ())

    def show_error(msg):
        search_state["running"] = False
        search_btn.state(tk.NORMAL)
        set_status(f"❌ {msg}", fail)

    def poll_queue():
        try:
            while True:
                kind, payload = msg_queue.get_nowait()
                if kind == "results":
                    show_results(payload)
                elif kind == "error":
                    show_error(payload)
                elif kind == "version":
                    i, vnum, url = payload
                    _apply_version(i, vnum, url)
        except queue.Empty:
            pass
        except Exception:
            pass
        win.after(120, poll_queue)

    def do_search():
        q = search_var.get().strip()
        if not q:
            set_status("请输入搜索词。", fail)
            return
        if search_state["running"]:
            return
        search_state["running"] = True
        search_btn.state(tk.DISABLED)
        set_status("联网搜索中…", ok)

        def worker():
            try:
                results = search_modrinth(q, limit=8)
            except Exception as e:
                msg_queue.put(("error", str(e)))
                return
            msg_queue.put(("results", results))

        threading.Thread(target=worker, daemon=True).start()

    search_btn.set_command(do_search)

    def open_download():
        r = selected()
        if r and r.get("download_url"):
            webbrowser.open(r["download_url"])
        else:
            set_status("请先选中一条结果（需要它带下载链接）。", fail)

    def copy_download():
        r = selected()
        if r and r.get("download_url"):
            win.clipboard_clear()
            win.clipboard_append(r["download_url"])
            set_status("已复制下载链接到剪贴板。", ok)
        else:
            set_status("请先选中一条结果（需要它带下载链接）。", fail)

    def copy_project():
        r = selected()
        if r:
            win.clipboard_clear()
            win.clipboard_append(r["project_url"])
            set_status("已复制项目链接到剪贴板。", ok)
        else:
            set_status("请先选中一条结果。", fail)

    def open_project_page():
        r = selected()
        if r and r.get("project_url"):
            webbrowser.open(r["project_url"])
        else:
            set_status("请先选中一条结果。", fail)

    open_btn.set_command(open_download)
    copy_link_btn.set_command(copy_download)
    copy_proj_btn.set_command(copy_project)
    search_entry.bind("<Return>", lambda e: do_search())

    poll_queue()

    # 应用当前主题
    update_mod_detail_theme(win, theme)

    # 登记到父窗口，便于主题切换时实时同步
    if not hasattr(parent, '_mod_detail_windows'):
        parent._mod_detail_windows = []
    if win not in parent._mod_detail_windows:
        parent._mod_detail_windows.append(win)

    def _unregister(event):
        try:
            if win in parent._mod_detail_windows:
                parent._mod_detail_windows.remove(win)
        except Exception:
            pass

    win.bind("<Destroy>", _unregister)

    # 按内容自适应大小并居中显示
    try:
        win.withdraw()
        win.update_idletasks()
        w = min(win.winfo_reqwidth(), 940)
        h = min(win.winfo_reqheight(), 780)
        x = max(0, (win.winfo_screenwidth() // 2) - (w // 2))
        y = max(0, (win.winfo_screenheight() // 2) - (h // 2))
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.deiconify()
    except Exception:
        pass

    return win
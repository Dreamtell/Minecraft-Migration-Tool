# ui/dialogs.py
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
import re
import difflib
import threading
import queue
import webbrowser
from utils.helpers import set_window_icon
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

        self.cancel_btn = tk.Button(self.win, text="取消迁移", command=self.on_cancel,
                                    bg=theme.get("danger_bg", "lightcoral"))
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
        self.cancel_btn.config(state=tk.DISABLED, text="正在取消...")

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
        foreground=theme["ttk_fg"], selectbackground=theme["ttk_select_bg"],
        selectforeground=theme["ttk_select_fg"], borderwidth=0, rowheight=22
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
            st.tag_configure("sel", background=theme.get("sel_bg", "#a6d0f5"),
                             foreground=theme.get("sel_fg", "#000000"))
        win.update_idletasks()
    except Exception:
        pass


def show_mod_detail(parent, jar_path, theme):
    """显示模组详细信息窗口（独立函数），带联网搜索模组/download链接功能。"""
    info = get_full_mod_metadata(jar_path)
    win = tk.Toplevel(parent)
    win.title(f"模组详情 - {os.path.basename(jar_path)}")
    win.geometry("880x720")
    win.minsize(820, 640)
    win.transient(parent)
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
    search_btn = tk.Button(top, text="🔍 联网搜索")
    search_btn.pack(side="left", padx=5)
    tk.Label(top, text="（自动置顶最匹配项）",
             fg=theme.get("muted_fg", "gray")).pack(side="left", padx=8)

    status_lbl = tk.Label(search_frame, text="可修改搜索词后回车或点击“联网搜索”。", anchor="w", fg=ok)
    status_lbl.pack(fill="x", pady=(0, 2))

    columns = ("name", "author", "downloads", "version", "slug")
    # 结果表格放进独立子框并撑满，避免下方"操作行"被横向挤掉
    tree_frame = tk.Frame(search_frame)
    tree_frame.pack(fill="both", expand=True, pady=2)
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=7,
                        style="Detail.Treeview")
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
    tree.tag_configure("sel", background=theme.get("sel_bg", "#a6d0f5"),
                       foreground=theme.get("sel_fg", "#000000"))
    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview,
                        style="Detail.Vertical.TScrollbar")
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True, padx=5)
    vsb.pack(side="right", fill="y")
    tree.bind("<Double-1>", lambda e: open_project_page())
    tree.bind("<<TreeviewSelect>>", lambda e: on_select())
    win._search_tree = tree

    act = tk.Frame(search_frame)
    act.pack(fill="x", pady=3)
    local_lbl = tk.Label(act, text=f"本地版本: {info['version']}")
    local_lbl.pack(side="left", padx=5)
    copy_proj_btn = tk.Button(act, text="🔗 复制项目链接")
    copy_link_btn = tk.Button(act, text="📋 复制下载链接")
    open_btn = tk.Button(act, text="🌐 打开下载页")
    open_btn.pack(side="right", padx=4)
    copy_link_btn.pack(side="right", padx=4)
    copy_proj_btn.pack(side="right", padx=4)

    tk.Button(win, text="关闭", command=win.destroy, width=12).pack(pady=8)

    # ---- 状态与线程安全更新（队列 + 轮询） ----
    msg_queue = queue.Queue()
    search_state = {"running": False}
    result_items = {}

    def set_status(msg, color=ok):
        status_lbl.config(text=msg, fg=color)

    def selected():
        sel = tree.selection()
        return result_items.get(sel[0]) if sel else None

    def on_select(event=None):
        """让选中行高亮成明显的选中色，取消选中时恢复底色。"""
        try:
            sel_set = set(tree.selection())
            for iid in tree.get_children():
                tags = [t for t in tree.item(iid, "tags") if t != "sel"]
                if iid in sel_set:
                    tags.append("sel")
                tree.item(iid, tags=tuple(tags))
        except Exception:
            pass

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
        search_btn.config(state=tk.NORMAL)
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
        if is_match:
            tree.item(iid, tags=("match",))
        elif updatable:
            tree.item(iid, tags=("update",))
        else:
            tree.item(iid, tags=("odd",) if (i % 2) else ())

    def show_error(msg):
        search_state["running"] = False
        search_btn.config(state=tk.NORMAL)
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
        search_btn.config(state=tk.DISABLED)
        set_status("联网搜索中…", ok)

        def worker():
            try:
                results = search_modrinth(q, limit=8)
            except Exception as e:
                msg_queue.put(("error", str(e)))
                return
            msg_queue.put(("results", results))

        threading.Thread(target=worker, daemon=True).start()

    search_btn.config(command=do_search)

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

    open_btn.config(command=open_download)
    copy_link_btn.config(command=copy_download)
    copy_proj_btn.config(command=copy_project)
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

    return win
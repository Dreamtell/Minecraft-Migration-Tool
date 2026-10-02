# ui/mw_big_view.py
"""放大查看（Tk 版）：清单大窗口、日志大窗口、忙碌/溢出处理。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from core.scanner import get_full_mod_metadata, get_mod_icon, guess_tags, split_cn_name
from pathlib import Path
from tkinter import filedialog, messagebox
from ui.dialogs import show_mod_detail
from ui.mw_common import _center_window, _close_popup, _grad_width
from ui.virtual_table import VirtualTable
from utils.helpers import (
    DataText, RoundedEntry, RoundedTextArea, begin_bulk_scan, create_gradient_button,
    end_bulk_scan, focus_window, set_window_icon, text_delta,
)
from utils.theme import apply_theme_to_widget_tree

# 界面语言：日志/文案模板走 trp（中文模式下与原 f-string 逐字一致）
from utils.i18n import tr, trp


class BigViewMixin:
    """放大查看（Tk 版）：清单大窗口、日志大窗口、忙碌/溢出处理。"""

    # 日志配色标签见类顶部 _LOG_COLOR_KEYS

    def open_log_big_view(self):
        """打开执行日志的放大查看窗口：只读、保留语义色，并实时跟随主日志更新。"""
        # 避免重复打开多个放大窗口
        existing = getattr(self, "_log_big_view", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    focus_window(existing)      # 已有就置顶（不只是 lift：前台锁下没效果）
                    return
            except tk.TclError:
                pass

        win = tk.Toplevel(self.root)
        self._log_big_view = win
        win.withdraw()          # 先隐藏，构建完居中后再显示，避免"闪现-跳到中间"
        win.title("执行日志 - 放大查看")
        win.geometry("1000x720")
        win.minsize(660, 420)
        win.transient(self.root)
        win.configure(bg=self.theme["bg"])
        set_window_icon(win)
        _center_window(win, 1000, 720)

        toolbar = tk.Frame(win, bg=self.theme["bg"])
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        self._log_big_toolbar = toolbar
        count_lbl = tk.Label(toolbar, text="", bg=self.theme["bg"],
                             fg=self.theme["muted_fg"], font=("微软雅黑", 9))
        self._log_big_count = count_lbl
        count_lbl.pack(side="left", padx=4)
        btn_close = create_gradient_button(
            toolbar, "❌ 关闭", win.destroy,
            colors=("#e53935", "#c62828"),
            width=_grad_width("❌ 关闭"), height=28, font=("微软雅黑", 9, "bold"))
        btn_refresh = create_gradient_button(
            toolbar, "🔄 刷新",
            lambda: state.__setitem__("last", None),
            colors=("#00bcd4", "#3f51b5"),
            width=_grad_width("🔄 刷新"), height=28, font=("微软雅黑", 9, "bold"))
        # 按「界面按钮」的配置摆（隐藏的不摆、顺序照配置；这类窗口下次打开生效）
        self._pack_window_btns("logview", toolbar,
                               [("lv_refresh", btn_refresh), ("lv_close", btn_close)],
                               side="right", padx=4)

        self._log_big_box = RoundedTextArea(win, self.theme, wrap=tk.WORD,
                                           state="disabled",
                                           bg=self.theme["log_bg"],
                                           fg=self.theme["log_fg"],
                                           font=("微软雅黑", 10))
        big = self._log_big_box.text
        self._log_big_box.pack(fill="both", expand=True, padx=8, pady=8)
        self._log_big_text = big
        self._bind_log_locate(big)               # 放大的日志窗也能双击定位
        self._smooth(big)

        # 使用与主日志一致的语义色（随主题）
        self._configure_log_colors(big)

        state = {"last": None, "bottom": True}

        def sync():
            try:
                if not win.winfo_exists():
                    return
                content = self.log_text.get("1.0", tk.END)
                if content != state["last"]:
                    try:
                        state["bottom"] = big.yview()[1] > 0.999
                    except Exception:
                        state["bottom"] = True
                    big.configure(state=tk.NORMAL)
                    big.delete("1.0", tk.END)
                    big.insert("1.0", content)
                    for tag in self._LOG_TAGS:
                        ranges = self.log_text.tag_ranges(tag)
                        # 个别 tk 版本 tag_ranges 会返回非迭代的 Tcl 对象，统一转成扁平串列表
                        if not isinstance(ranges, (tuple, list)):
                            ranges = self.log_text.tk.splitlist(ranges)
                        for i in range(0, len(ranges), 2):
                            big.tag_add(tag, ranges[i], ranges[i + 1])
                    big.configure(state=tk.DISABLED)
                    if state["bottom"]:
                        big.see(tk.END)
                    state["last"] = content
                    self._roll_counter(count_lbl, trp("{0} 行", content.count(chr(10))))
            except tk.TclError:
                return
            except Exception:
                pass

        def on_log_changed(_evt=None):
            sync()

        def force_refresh():
            state["last"] = None
            sync()

        # 事件驱动：主日志新增/清空时即时同步，无需轮询或手动刷新
        # 先清理可能残留的旧绑定，避免重复打开时叠加
        old_nid = getattr(self, '_log_big_notify_id', None)
        if old_nid:
            try:
                self.log_text.unbind("<<LogChanged>>", old_nid)
            except Exception:
                pass
        notify_id = self.log_text.bind("<<LogChanged>>", on_log_changed, add="+")
        self._log_big_notify_id = notify_id

        def on_close():
            try:
                self.log_text.unbind("<<LogChanged>>", notify_id)
            except Exception:
                pass
            # 和"放大查看"一样走原生关闭（系统自己的关闭动画，不做淡化）
            _close_popup(win)

        win.protocol("WM_DELETE_WINDOW", on_close)
        btn_close.set_command(on_close)
        btn_refresh.set_command(force_refresh)

        sync()
        # 全部构建完成后才居中显示：此刻窗口仍是隐藏的，所以不会出现瞬移
        _center_window(win, 1000, 720)
        win.deiconify()
        focus_window(win)

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

    # ---------- 放大查看：按钮先变"打开中" ----------
    # 建那个窗口是同步的（大清单要几百毫秒），这期间界面不会自己重绘，
    # 点下去看着就像没反应。所以先把按钮切成"打开中"并强制刷一次。
    def _magnify_busy(self, btn, text="⏳ 打开中…"):
        if btn is None:
            return False
        try:
            btn.set_text(text)
            btn.set_gradient("#9e9e9e", "#bdbdbd")
            btn.state("disabled")            # 顺带防连点
            self.root.update_idletasks()     # 关键：不刷这一下"打开中"根本看不见
            return True
        except Exception:
            return False

    def _magnify_idle(self, btn, text="📂 放大查看"):
        if btn is None:
            return
        try:
            btn.set_text(text)
            base = getattr(btn, "_base_colors", ("#607d8b", "#90a4ae"))
            hov = getattr(btn, "_base_hover", None)
            if hov:
                btn.set_gradient(base[0], base[1], hov[0], hov[1])
            else:
                btn.set_gradient(base[0], base[1])
            btn.state("normal")
            # 恢复后再按"内容溢出"重新着色（溢出时本来是橙色的）
            self._check_overflow()
        except Exception:
            pass

    def _with_busy_btn(self, btn, func, busy_text="⏳ 打开中…",
                       idle_text="📂 放大查看"):
        """同步执行 func，期间按钮显示"打开中"，结束后恢复。"""
        started = self._magnify_busy(btn, busy_text)
        try:
            func()
        finally:
            if started:
                self._magnify_idle(btn, idle_text)

    def _open_big_view_dispatch(self, source_text, title):
        """按设置选择放大查看窗口的实现；Qt 不可用就自动回落到 Tk 版。"""
        if getattr(self, "big_view_backend", "qt") == "qt":
            ok, why = self._qt_available()
            if ok and self._open_big_view_qt(source_text, title):
                return
            if not ok:
                self.log("⚠ " + why + "，本次用经典窗口打开", level="WARNING", save=False)
        self.open_big_view(source_text, title)

    def open_mod_big_view(self):
        """模组清单 → 放大查看"""
        self._with_busy_btn(self.mod_magnify_btn,
                            lambda: self._open_big_view_dispatch(self.mod_text, "模组清单"))

    def open_config_big_view(self):
        """config 清单 → 放大查看"""
        self._with_busy_btn(self.config_magnify_btn,
                            lambda: self._open_big_view_dispatch(self.config_text, "Config清单"))

    def open_log_big_view_busy(self):
        """执行日志 → 放大查看"""
        self._with_busy_btn(getattr(self, "log_magnify_btn", None),
                            self.open_log_big_view)

    def open_big_view(self, source_text, title):
        """大窗口查看：可多选（勾选）+ 可排序的列表，并支持搜索、存在性检测、添加/删除、拖拽。"""
        is_mod = "模组" in title or source_text is getattr(self, 'mod_text', None)
        # 单实例：同标题的放大查看窗口已打开则聚焦，避免连点重复弹窗
        win_title = f"大窗口查看 - {title}"
        for w in getattr(self, '_big_view_windows', []):
            try:
                if w.winfo_exists() and w.title() == win_title:
                    focus_window(w)      # 置顶（不只是 lift：前台锁下 lift 常常没效果）
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title(win_title)
        win.geometry("1060x680")
        win.minsize(820, 520)
        win.transient(self.root)
        win.configure(bg=self.theme["bg"])
        set_window_icon(win)
        _center_window(win, 1060, 680)
        # 登记窗口，主题切换时统一重新配色（背景/文字/输入框等）
        if not hasattr(self, '_big_view_windows'):
            self._big_view_windows = []
        self._big_view_windows.append(win)

        if is_mod:
            # 没有"☑"列了：选中的行整行变蓝（和卡片视图同一套色），不用再单独放一个勾
            columns = (("status", "🔵 状态", 96, "w"),
                       ("name", "📄 文件名", 210, "w"), ("path", "📁 完整路径", 280, "w"),
                       ("type", "🧩 类型", 92, "w"), ("modid", "🆔 Mod ID", 140, "w"),
                       ("version", "🔖 版本", 120, "w"), ("size", "💾 大小KB", 92, "e"))
        else:
            columns = (("status", "🔵 状态", 96, "w"),
                       ("name", "📄 名称", 210, "w"), ("path", "📁 相对路径/完整路径", 340, "w"),
                       ("type", "🏷️ 类型", 100, "w"))

        # 用自绘的 VirtualTable 取代 ttk.Treeview：Treeview 改任意一行的颜色都会重绘整个
        # 可见区域（本机 26~39ms），悬停高亮因此严重滞后，而且无法只给某一列上色。
        # VirtualTable 只把可见行画在 Canvas 上，悬停仅重绘两行；状态列用彩色圆点单独表达。
        # 字号放大，列宽会按窗口可视宽度自动拉伸填满，不会右侧留白。
        table = VirtualTable(
            win, columns, self.theme,
            font=("微软雅黑", 12), row_height=26, header_height=32,
            on_row_click=lambda row, ev: _on_row_click(row, ev),
            on_row_double=(lambda row, ev: _on_row_double(row, ev)) if is_mod else None,
            on_header_click=lambda key, ev: sort_by(key),
            on_row_hover=lambda row, ev: _on_row_hover(row, ev),
            on_leave=lambda ev: _tip_hide(),
            on_scroll=lambda: _tip_hide())
        table.grid(row=0, column=0, sticky="nsew")
        _cell_tip = {"win": None, "label": None, "after": None, "shown": False,
                     "x": 0, "y": 0, "xroot": 0, "yroot": 0, "motion_t": 0.0}
        _tip_cell = {"row": -1, "key": None}

        def _tip_hide():
            """收起悬浮提示。窗口只 withdraw 复用，绝不 destroy——
            每次悬停都新建 Toplevel 会让鼠标扫过表格时明显卡顿。"""
            after_id = _cell_tip.get("after")
            if after_id is not None:
                try:
                    win.after_cancel(after_id)
                except Exception:
                    pass
                _cell_tip["after"] = None
            if _cell_tip.get("shown"):
                # 只有当前确实显示着才调 withdraw，省掉每次换行的无效 Tcl 调用
                tip = _cell_tip.get("win")
                if tip is not None:
                    try:
                        tip.withdraw()
                    except Exception:
                        pass
                _cell_tip["shown"] = False

        def _tip_show():
            """延迟到期：确认鼠标已停稳后，才做单元格识别并弹出提示。
            列识别/取文本这些 Tcl 调用全部集中在这里，避免拖慢鼠标移动。"""
            _cell_tip["after"] = None
            # 鼠标仍在移动 -> 再等一会儿，划动过程中绝不弹窗
            if time.time() - _cell_tip.get("motion_t", 0.0) < 0.25:
                try:
                    _cell_tip["after"] = win.after(150, _tip_show)
                except Exception:
                    pass
                return
            try:
                if not win.winfo_exists():
                    return
            except Exception:
                return
            row = _tip_cell["row"]
            cname = _tip_cell["key"]
            if row < 0 or not cname:
                return
            try:
                text = table.model.cell(row, cname)
            except Exception:
                return
            # 只在可能被截断的文本或路径列上提示，避免短文本频繁弹窗
            if not (text and (cname == "path" or len(text) >= 12)):
                return
            tip = _cell_tip.get("win")
            if tip is None:
                tip = tk.Toplevel(win)
                tip.wm_overrideredirect(True)
                try:
                    tip.attributes("-topmost", True)
                except Exception:
                    pass
                lbl = tk.Label(tip, text="", relief="solid", borderwidth=1,
                               font=("微软雅黑", 9), justify="left",
                               wraplength=520, anchor="w")
                lbl.pack()
                _cell_tip["win"] = tip
                _cell_tip["label"] = lbl
            _cell_tip["label"].configure(
                text=text,
                background=self.theme.get("tooltip_bg", "#ffffe0"),
                fg=self.theme.get("label_fg", "#000000"))
            tip.wm_geometry(f"+{_cell_tip.get('xroot', 0) + 12}+{_cell_tip.get('yroot', 0) + 12}")
            tip.deiconify()
            _cell_tip["shown"] = True

        def _on_row_hover(row: int, event):
            """悬停到某行：记录位置，并安排延迟弹出单元格完整内容。
            列识别推迟到鼠标停稳，鼠标移动时不碰表格绘制，所以划动很跟手。"""
            _cell_tip["motion_t"] = time.time()
            _cell_tip["x"] = event.x
            _cell_tip["y"] = event.y
            _cell_tip["xroot"] = event.x_root
            _cell_tip["yroot"] = event.y_root
            key = table.col_at(event.x) if row >= 0 else None
            if (row, key) != (_tip_cell["row"], _tip_cell["key"]):
                _tip_cell["row"] = row
                _tip_cell["key"] = key
                _tip_hide()          # 换了单元格，先收起旧提示
            if _cell_tip["after"] is None and row >= 0 and key:
                try:
                    _cell_tip["after"] = win.after(300, _tip_show)
                except Exception:
                    pass

        def _reset_hover_state():
            """排序/刷新/清理时清空悬停状态，避免残留提示。"""
            _tip_cell["row"] = -1
            _tip_cell["key"] = None
            _tip_hide()

        win.grid_rowconfigure(0, weight=1)
        win.grid_columnconfigure(0, weight=1)

        entries = [ln.strip() for ln in source_text.get("1.0", tk.END).splitlines() if ln.strip()]
        msg_queue = queue.Queue()
        meta: dict = {}  # entry索引(int) -> {status,name,path,type,modid,version,size}
        checked = {}  # 条目内容(完整路径/文件名) -> bool，用勾选做多选（按内容而非行位置，避免排序/刷新后错位）
        new_keys = set()  # 本次会话新添加的模组（文件名），染黄色高亮提示
        order: list = list(range(len(entries)))  # 当前显示顺序（entries 索引），含排序+过滤
        order_index = {idx: pos for pos, idx in enumerate(order)}  # idx->当前位置，供 poll 快速定位(O(1))
        sort_state = {"col": None, "rev": False}
        # 上次写回时的清单内容：写回只发"相对它的增删"（用户在编辑模式里敲的内容不受影响）
        基线 = [list(entries)]

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
            """排序键：只读内存里的扫描结果，绝不在这里碰磁盘。
            以前每个键都要 resolve() 一次（文件系统调用），排序上千条目会明显卡。"""

            def key(i):
                e = entries[i]
                m = meta.get(i) or {}
                if col == "status":
                    st = m.get("status")
                    if st == "✅ 存在":
                        return 0
                    if st == "❌ 缺失":
                        return 1
                    return 2          # 尚未检测出结果
                if col == "name":
                    return (m.get("name") or Path(e).name).lower()
                if col == "path":
                    return str(m.get("path") or e).lower()
                if col == "type":
                    return (m.get("type") or "").lower()
                if col == "size":
                    try:
                        return float(m.get("size"))
                    except (TypeError, ValueError):
                        return -1.0
                v = m.get(col)
                return "" if v is None else str(v)

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

        # ---- VirtualTable 的数据源：按需读取，不复制整表 ----
        _STATUS_TEXT = {"✅ 存在": "存在", "❌ 缺失": "缺失", "…": "检测中"}

        def _m_cell(row: int, key: str):
            """单元格文本；row 是当前显示顺序里的行号。"""
            idx = order[row]
            m = meta.get(idx)
            if key == "name":
                return (m.get("name") if m else None) or Path(entries[idx]).name
            if key == "path":
                return (m.get("path") if m else None) or entries[idx]
            if not m:
                return "…"
            v = m.get(key)
            if key == "type" and v and v != "?":
                # 类型列加图标：config 区分文件/文件夹，模组按加载器显示
                return "%s %s" % ({"文件夹": "📁", "文件": "📄"}.get(v, "🧩"), v)
            return "…" if v is None else str(v)

        def _m_dot(row: int):
            """状态列的 (颜色, 文字)：圆点是该列唯一带颜色的元素。"""
            st = meta.get(order[row], {}).get("status") or "…"
            if st == "❌ 缺失":
                color = self.theme.get("danger_fg", "#8b0000")
            elif st == "✅ 存在":
                color = self.theme.get("ok_fg", "#2e7d32")
            else:
                color = self.theme.get("muted_fg", "#808080")
            return color, _STATUS_TEXT.get(st, st)

        def _m_tags(row: int):
            """行底色：勾选(绿底) > 缺失(红底) > 新添加(黄底)。"""
            idx = order[row]
            k = key_of(entries[idx])
            if checked.get(k):
                return ("checked",)
            if meta.get(idx, {}).get("status") == "❌ 缺失":
                return ("missing",)
            if k in new_keys:
                return ("new",)
            return ()

        class _BigViewModel:
            row_count = staticmethod(lambda: len(order))
            cell = staticmethod(_m_cell)
            dot = staticmethod(_m_dot)
            tags = staticmethod(_m_tags)

        table.set_model(_BigViewModel())

        def scan_row(idx: int):
            e = entries[idx]
            try:
                r = resolve(e)
                if r is None:
                    cn, en = split_cn_name(Path(e).name or str(e))
                    meta[idx] = {"status": "❌ 缺失", "name": Path(e).name or e,
                                 "path": str(e), "type": "?", "modid": "?", "version": "?",
                                 "size": "?", "cn": cn, "desc": ""}
                else:
                    if is_mod:
                        info = get_full_mod_metadata(str(r["obj"]))
                        cn, _en = split_cn_name(Path(r["name"]).name)
                        desc = str(info.get("description", "") or "").replace("\n", " ").strip()
                        # 加载器 + 客户端/服务端（Fabric 才有）是 jar 里的事实，先放进去
                        base_tags = ([info.get("mod_type", "")]
                                     if info.get("mod_type") in ("Fabric", "Forge") else []) \
                                    + ([info["env"]] if info.get("env") else [])
                        meta[idx] = {"status": "✅ 存在", "name": r["name"], "path": r["path"],
                                     "type": info.get("mod_type", "?"), "modid": info.get("modid", "?"),
                                     "version": info.get("version", "?"),
                                     "size": round(r["obj"].stat().st_size / 1024, 1),
                                     "cn": cn,
                                     # 卡片视图要显示"模组自己的名字"，不是文件名
                                     "disp": str(info.get("name") or "").strip(),
                                     # 元数据里没描述时是"无"，卡片视图别显示这个字
                                     "desc": "" if desc in ("无", "未知") else desc,
                                     # 分类：默认关键词推测；开了联网就尽量换成 Modrinth 真实分类
                                     "tags": base_tags + guess_tags(desc, info.get("name", ""),
                                                                    info.get("modid", "")),
                                     "tags_online": False}
                        if self.online_tags:
                            try:
                                from core.mod_search import fetch_categories_cached
                                cats = fetch_categories_cached(
                                    info.get("modid") or Path(r["name"]).stem,
                                    modid=info.get("modid", ""), name=info.get("name", ""))
                                if cats:
                                    meta[idx]["tags"] = base_tags + cats
                                    meta[idx]["tags_online"] = True
                            except Exception:
                                pass
                    else:
                        size = round(r["obj"].stat().st_size / 1024, 1) if r["obj"].is_file() else ""
                        meta[idx] = {"status": "✅ 存在", "name": r["name"], "path": r["path"],
                                     "type": r["type"], "modid": "", "version": "", "size": size,
                                     "cn": "", "desc": ""}
            except Exception:
                meta[idx] = {"status": "❌ 缺失", "name": Path(e).name or e,
                             "path": str(e), "type": "?", "modid": "?", "version": "?",
                             "size": "?", "cn": "", "desc": ""}
            msg_queue.put(idx)

        # 有界扫描线程池：避免为每条目单开线程导致几千并发的线程爆炸/磁盘抖动。
        # 6 个并发在 SSD 上快不了多少，却让界面线程在 GIL 上排到第 7 位（实测 1200 条
        # 扫描时泵帧 p95 从 350ms 飙到 976ms），降到 3 个后明显跟手。
        scan_queue = queue.Queue()
        _SCAN_WORKERS = 3
        scan_gil = {"on": False}       # 大扫描期间是否已把 GIL 切换间隔调细

        def _scan_begin():
            if not scan_gil["on"]:
                scan_gil["on"] = True
                begin_bulk_scan()

        def _scan_end():
            if scan_gil["on"]:
                scan_gil["on"] = False
                end_bulk_scan()

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
            _reset_hover_state()
            order[:] = compute_order()
            order_index.clear()
            order_index.update({idx: pos for pos, idx in enumerate(order)})
            table.refresh()
            if card_state["view"] and card_state["list"] is not None:
                # 卡片视图跟着同一份数据走（排序/搜索/增删后都要重排）
                card_state["list"].set_rows(card_rows())
            if rescan:
                queued = False
                for idx in order:
                    if idx not in meta:
                        scan_queue.put(idx)
                        queued = True
                if queued:
                    _scan_begin()      # 扫描期间把 GIL 让给界面线程（见 begin_bulk_scan）
            total = len(entries)
            shown = len(order)
            self._roll_counter(count_lbl,
                               f"显示 {shown}/{total} 项"
                               if search_var.get().strip() else f"共 {total} 项")
            update_summary()

        def write_back(fade_out_lines=None, fade_in_lines=None):
            """把 entries 的**变化**写回主界面清单。

            fade_out_lines / fade_in_lines：要淡出（删除前的位置）或淡入（重写后的位置）
            的 1-based 行号。行数多（>20）就不做动画，直接重写 —— 批量操作时动画只会拖慢。

            只应用"相对上次写回的增删"，不整份覆盖：放大查看开着的时候用户在编辑模式里
            敲的内容，`entries`（打开时的快照）里是没有的，整份覆盖会把它们盖回去。
            """
            # 用户在放大查看开着的时候动过主界面吗（动过就不做行号动画了，行号对不上）
            主界面此时 = [ln.strip() for ln in source_text.get("1.0", tk.END).splitlines()
                          if ln.strip()]
            与基线一致 = (主界面此时 == 基线[0])

            def _rewrite():
                新增, 删除 = text_delta(基线[0], entries)
                基线[0] = list(entries)
                # 只应用增删（不动用户在编辑模式里敲的内容），整套文本重写交给公共方法
                self._apply_entries_delta(source_text, 新增, 删除)
                # 新加的行淡入（重写后行号才对得上）；主界面被编辑过就不做（行号对不上）
                if 与基线一致 and fade_in_lines and len(fade_in_lines) <= 20:
                    try:
                        self._fade_text_lines(
                            source_text, fade_in_lines,
                            self.theme.get("text_bg", "#ffffff"),
                            self.theme.get("text_fg", "#000000"),
                            frames=12, frame_ms=20, indent_from=40, indent_to=0)
                    except Exception:
                        pass
                # 主模组清单被重写后，重新应用"新添加"黄色高亮
                if is_mod:
                    self._apply_mod_new_tags()
                else:
                    self._clear_config_status()

            if 与基线一致 and fade_out_lines and 0 < len(fade_out_lines) <= 20:
                # 先让要被删掉的行"淡出 + 往右滑走"，淡完了再真正重写
                try:
                    self._fade_text_lines(source_text, fade_out_lines,
                                          source_text.cget("fg"),
                                          self.theme.get("text_bg", "#ffffff"),
                                          frames=12, frame_ms=20,
                                          indent_from=0, indent_to=40,
                                          on_done=_rewrite)
                    return
                except Exception:
                    pass
            _rewrite()

        big_scanning = {"flag": False}
        poll_stat = {"n": 0, "got": 0, "fin": 0}      # 只读诊断用（见 win._scan_debug）
        # 只读诊断（不参与界面逻辑，和 canvas._btn_disabled 一样只在排查时看）：
        # 扫描乱掉时一眼就能看出是"清单/顺序为空"还是"结果没回来"
        win._scan_debug = lambda: {"entries": len(entries), "order": len(order),
                                   "meta": len(meta),
                                   "keys": sorted(str(k) for k in meta.keys())[:5],
                                   "st": [str(tr(v.get("status"))) for v in list(meta.values())[:5]],
                                   "ktype": [type(k).__name__ for k in list(meta.keys())[:3]],
                                   "calc": sum(1 for i in range(len(entries))
                                               if (meta.get(i) or {}).get("status") == "✅ 存在"),
                                   "msg": msg_queue.qsize(), "polls": dict(poll_stat),
                                   "gil": scan_gil["on"],
                                   "busy": big_scanning["flag"], "queue": scan_queue.unfinished_tasks}

        def _set_busy_btns(busy):
            """扫描/处理进行中禁用并变灰相关按钮，防止连点；完成后恢复。"""
            for btn, busy_text, normal_text in (
                (detect_btn, "检测中…", "🔍 检测存在性"),
                (del_btn, None, None),
                (add_btn if is_mod else None, None, None),
            ):
                if btn is None:
                    continue
                try:
                    if busy:
                        if busy_text:
                            btn.set_text(busy_text)
                        btn.set_gradient("#9e9e9e", "#bdbdbd")
                        btn.state("disabled")
                    else:
                        if normal_text:
                            btn.set_text(normal_text)
                        btn.set_gradient(*btn._base_colors, *btn._base_hover)
                        btn.state("normal")
                except Exception:
                    pass

        def add_mods():
            if big_scanning["flag"]:
                return
            _initial = str(mods_dir) if (mods_dir is not None and mods_dir.exists()) else None
            files = filedialog.askopenfilenames(title="选择要添加的模组（可多选）",
                                                initialdir=_initial,
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
            # 新行在清单末尾，重写后让它们淡入
            write_back(fade_in_lines=range(len(entries) - len(new_entries) + 1,
                                           len(entries) + 1))
            messagebox.showinfo("添加成功", trp("✅ 已添加 {0} 个模组。", len(new_entries)),
                                parent=win)

        def del_selected():
            if big_scanning["flag"]:
                return
            to_del = {i for i, e in enumerate(entries) if checked.get(key_of(e))}
            if not to_del:
                messagebox.showinfo("提示", "请先勾选要删除的模组。")
                return
            # 清单文本的行号 = entries 下标 + 1（文本是按 entries 顺序写的）
            removed_lines = sorted(i + 1 for i in to_del)
            entries[:] = [e for i, e in enumerate(entries) if i not in to_del]
            meta.clear()
            checked.clear()
            rebuild(rescan=True)
            write_back(fade_out_lines=removed_lines)

        def on_tree_drop(event):
            if big_scanning["flag"]:
                return
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
            write_back(fade_in_lines=range(len(entries) - len(new_entries) + 1,
                                           len(entries) + 1))
            messagebox.showinfo("添加成功", trp("✅ 已添加 {0} 个模组。", len(new_entries)),
                                parent=win)

        def toggle_row(row: int):
            """切换某行勾选状态，并只重绘这一行（不整表重画）。"""
            if not (0 <= row < len(order)):
                return
            k = key_of(entries[order[row]])
            checked[k] = not checked.get(k, False)
            table.repaint_row(row)
            update_summary()

        def _path_of_row(row: int):
            """这一行对应的磁盘路径（模组清单里没有真实文件时返回 None）。"""
            try:
                idx = order[row]
                r = resolve(entries[idx])
                return (r.get("path") if r else None) or meta.get(idx, {}).get("path")
            except Exception:
                return None

        def reveal_path(path):
            """在资源管理器中定位文件（高亮选中它本身）。"""
            try:
                if not path or not os.path.exists(path):
                    messagebox.showinfo("提示", "该文件不在磁盘上，无法定位。", parent=win)
                    return
                subprocess.Popen(['explorer', '/select,', str(path)])
            except Exception as e:
                messagebox.showinfo("提示", trp("定位失败：{0}", e), parent=win)

        def set_checked_mode(mode):
            """全选 / 反选 / 清空勾选 —— 只作用于当前显示（搜索/排序后）的行。

            表格和卡片共用同一份 checked，所以改完两边一起刷。
            """
            for idx in order:
                k = key_of(entries[idx])
                if mode == "all":
                    checked[k] = True
                elif mode == "none":
                    checked[k] = False
                else:
                    checked[k] = not checked.get(k, False)
            try:
                table.refresh()
            except Exception:
                pass
            if card_state["view"] and card_state["list"] is not None:
                card_state["list"].set_rows(card_rows())
            update_summary()
            n = sum(1 for i in order if checked.get(key_of(entries[i])))
            _MODE_TEXT = {"all": "全选", "none": "清空勾选", "invert": "反选"}
            # 这几个词是**拼进模板里的值**，得单独过一遍语言层（trp 无参时等于 tr）
            self.log(trp("☑ 已{0}：当前显示 {1} 项，选中 {2} 项",
                         trp(_MODE_TEXT.get(mode, mode)), len(order), n),
                     level="INFO", save=False)

        def open_mod_detail(row: int):
            """打开指定行对应模组的详情窗口（含 Modrinth 联网搜索）。"""
            try:
                path = _path_of_row(row)
                if path and os.path.exists(path):
                    m = meta.get(order[row], {}) if 0 <= row < len(order) else {}
                    # 把扫描时拿到的分类一起带过去，详情窗口就不用再猜一遍
                    show_mod_detail(win, path, self.theme,
                                    tags_hint=m.get("tags"),
                                    tags_online=bool(m.get("tags_online")),
                                    env=self._instance_env())
                else:
                    messagebox.showinfo("提示", "该行没有可查看的模组文件。", parent=win)
            except Exception:
                pass

        # 点击规则交给 Tk 自己的事件，不再手写双击判定：
        #   单击（<Button-1>）         = 切换勾选
        #   双击（<Double-Button-1>）  = 打开详情；Tk 对第二次按下只发这一个事件
        #                              （<Double-Button-1> 比 <Button-1> 更具体），
        #                              所以这里再 toggle 一次把单击那下撤回来，勾选不变。
        def _on_row_click(row: int, event):
            """单击切换勾选。"""
            if row < 0:
                return
            toggle_row(row)

        def _on_row_double(row: int, event):
            """模组清单双击同一行 = 打开详情（勾选状态保持不变）。"""
            if row < 0:
                return
            toggle_row(row)          # 撤回双击第一次按下造成的勾选切换
            open_mod_detail(row)

        def sort_by(col):
            if sort_state["col"] == col:
                sort_state["rev"] = not sort_state["rev"]
            else:
                sort_state["col"] = col
                sort_state["rev"] = False
            table.set_sort(col, sort_state["rev"])
            rebuild(rescan=False)

        def poll():
            # 每次尽量只处理一小批消息，避免几千条积压时一次循环卡死 UI
            poll_stat["n"] += 1
            batch = 40
            got = False
            try:
                while batch > 0:
                    idx = msg_queue.get_nowait()
                    pos = order_index.get(idx)
                    if pos is not None:
                        table.repaint_row(pos)
                        if card_state["view"] and card_state["list"] is not None:
                            # 注意要"更新数据 + 重画"，只重画的话卡片还是扫描前的旧值
                            card_state["list"].update_row(pos, make_row(order[pos]))
                    got = True
                    batch -= 1
            except queue.Empty:
                pass
            except Exception:
                pass
            if got:
                poll_stat["got"] += 1
                update_summary()      # 扫描回来一批就刷新"存在/缺失"汇总
            # 扫描完成后恢复按钮（防止连点重复触发全量扫描）
            # 扫描队列排空 = 这一轮扫描结束（不管是不是"检测存在性"触发的，都要收尾：
            # 恢复 GIL 切换间隔、恢复按钮、把摘要刷成最终值）
            if scan_gil["on"] or big_scanning["flag"]:
                try:
                    if scan_queue.unfinished_tasks == 0:
                        poll_stat["fin"] += 1
                        _scan_end()
                        if big_scanning["flag"]:
                            big_scanning["flag"] = False
                            _set_busy_btns(False)
                            self._end_file_task()      # 扫描结束：解除"禁止迁移"
                            # 一条结果都没发回来时（空清单/整单命中缓存）上面那批
                            # got 一直是 False，收尾得自己刷一次，否则"检测中…"赖着不走
                            update_summary()
                            if _pending_detect["flag"]:
                                # 「检测存在性」的提示放到这里：此时扫描已全部结束，
                                # 统计只读内存，不会像以前那样在点击时卡住界面。
                                _pending_detect["flag"] = False
                                missing = sum(1 for m in meta.values()
                                              if m.get("status") == "❌ 缺失")
                                messagebox.showinfo(
                                    "检测完成",
                                    trp("✅ 存在性检测完成：共 {0} 项，缺失 {1} 项。",
                                        len(entries), missing)
                                    + ("\n\n" + tr("缺失的模组本工具不负责下载，"
                                                   "请在你的启动器（如 PCL）里补装。")
                                       if missing else ""),
                                    parent=win)
                except Exception:
                    pass
            try:
                if win.winfo_exists():
                    win.after(120, poll)
            except Exception:
                pass

        _pending_detect = {"flag": False}

        def detect():
            """强制重新检测存在性：清空缓存后交给后台线程重扫，完成后由 poll 统一提示。
            以前这里在界面线程里对每个条目 resolve() 查一次磁盘，上千个模组会卡好几秒。"""
            if big_scanning["flag"]:
                return
            if self._migration_running:
                messagebox.showwarning("提示", "迁移进行中，暂不能检测存在性。", parent=win)
                return
            big_scanning["flag"] = True
            _set_busy_btns(True)
            self._begin_file_task("检测模组是否存在")   # 期间禁止启动迁移
            meta.clear()
            _pending_detect["flag"] = True
            rebuild(rescan=True)

        def _on_big_destroy(event):
            """大窗口被关掉时，别把"禁止迁移"的标记和 GIL 设置留成永久状态。"""
            try:
                if event.widget is win:
                    _scan_end()
                    if getattr(self, "_file_task", None):
                        self._end_file_task()
            except Exception:
                pass
        win.bind("<Destroy>", _on_big_destroy, add="+")

        # 顶部工具栏分两行：第一行计数+搜索，第二行按钮。
        # 挤在一行时（7 个按钮 + 搜索框）总宽会超过窗口，尾部按钮被裁掉一半；
        # 拆开后按钮独占一行，窗口拉窄也不至于变形。
        top = tk.Frame(win, bg=self.theme["bg"])
        top.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        row_search = tk.Frame(top, bg=self.theme["bg"])
        row_search.pack(fill="x", pady=(0, 4))
        row_btns = tk.Frame(top, bg=self.theme["bg"])
        row_btns.pack(fill="x")
        _PAD = 4          # 按钮间距（原来 6，7 个按钮并排就撑爆一行）
        # 统一的按钮宽度按**翻译后**的文字量（英文更长；create_gradient_button 那边
        # 还会各自兜底撑宽，这里量准了整排才齐）
        _BTN_W = max(_grad_width(tr("🔍 检测存在性")),
                     _grad_width(tr("🗑️ 删除选中")),
                     _grad_width(tr("➕ 添加模组")))
        # "共 N 项"里那个 N 是数据，单独上数字色
        count_lbl = DataText(row_search, self.theme,
                             [("共 ", "muted_fg"),
                              (str(len(entries)), "data_num_fg"),
                              (" 项", "muted_fg")])
        count_lbl.pack(side="left", padx=(0, _PAD * 2))
        # 选中/存在性汇总：这两个数字是"我现在到底选了多少、有多少缺失"，
        # 表格和卡片视图共用（改勾选的地方都会调 update_summary）。
        # 颜色按语义走：有选中=选中蓝、存在>0=绿、缺失>0=红，为 0 的那个压成灰。
        sel_lbl = tk.Label(row_search, text="未选择", bg=self.theme["bg"],
                           fg=self.theme.get("muted_fg", "#808080"),
                           font=("微软雅黑", 9, "bold"))
        sel_lbl._keep_fg = True
        sel_lbl.pack(side="left", padx=(0, _PAD * 3))
        # 存在/缺失拆成两个标签，各自上色（原来一整条灰字，扫一眼分不出好坏）
        exist_lbl = tk.Label(row_search, text="", bg=self.theme["bg"],
                             fg=self.theme.get("muted_fg", "#808080"),
                             font=("微软雅黑", 9, "bold"))
        exist_lbl._keep_fg = True
        exist_lbl.pack(side="left", padx=(0, _PAD))
        miss_lbl = tk.Label(row_search, text="", bg=self.theme["bg"],
                            fg=self.theme.get("muted_fg", "#808080"),
                            font=("微软雅黑", 9, "bold"))
        miss_lbl._keep_fg = True
        miss_lbl.pack(side="left", padx=(0, _PAD * 3))
        scan_lbl = tk.Label(row_search, text="", bg=self.theme["bg"],
                            fg=self.theme.get("log_warning_fg", "#e65100"),
                            font=("微软雅黑", 8))
        scan_lbl._keep_fg = True
        scan_lbl.pack(side="left")
        win._sel_lbl = sel_lbl
        win._exist_lbl = exist_lbl
        win._miss_lbl = miss_lbl
        win._scan_lbl = scan_lbl

        # ⚠ 最小宽度按**实测**来定：英文按钮比中文宽，还写死 820 的话窗口一窄
        # 这排按钮/汇总就会被裁掉（和 Qt 版搜索框被挤扁是同一个毛病）。
        try:
            win.update_idletasks()
            需要 = max(row_btns.winfo_reqwidth(), row_search.winfo_reqwidth()) + 40
            屏幕宽 = win.winfo_screenwidth()
            win.minsize(max(820, min(需要, int(屏幕宽 * 0.96))),
                        max(520, win.winfo_reqheight() // 2))
        except Exception:
            pass

        def update_summary():
            """刷新"已选 N / 总数"和"存在/缺失"两个汇总（数字带滚动动画）。"""
            try:
                total = len(entries)
                picked = sum(1 for e in entries if checked.get(key_of(e)))
                accent = self.theme.get("card_sel_bar", "#2f7fd1")
                muted = self.theme.get("muted_fg", "#808080")
                sel_lbl.config(fg=accent if picked else muted)
                self._roll_counter(sel_lbl,
                                   f"已选 {picked} / {total} 项" if picked else "未选择")
                exist = miss = 0
                for i in range(total):
                    st = (meta.get(i) or {}).get("status")
                    if st == "✅ 存在":
                        exist += 1
                    elif st == "❌ 缺失":
                        miss += 1
                exist_lbl.config(fg=self.theme.get("ok_fg", "#2e7d32") if exist else muted)
                self._roll_counter(exist_lbl, f"存在 {exist}" if total else "")
                miss_lbl.config(fg=self.theme.get("fail_fg", "#c62828") if miss else muted)
                self._roll_counter(miss_lbl, f"· 缺失 {miss}" if total else "")
                scan_lbl.config(text="检测中…" if big_scanning["flag"] else "")
            except Exception:
                pass
        win._update_summary_ui = update_summary
        update_summary()
        # 搜索（模组区/Config区都可用）：单独一行，可以占满宽度，长名字也能看全
        tk.Label(row_search, text="搜索:", bg=self.theme["bg"],
                 fg=self.theme["fg"]).pack(side="left")
        search_var = tk.StringVar()
        search_entry = RoundedEntry(row_search, self.theme, textvariable=search_var,
                                    chars=18)
        search_entry.pack(side="left", fill="x", expand=True, padx=(_PAD, 0))
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
        detect_btn = create_gradient_button(row_btns, "🔍 检测存在性", detect,
                                            colors=("#43a047", "#66bb6a"),
                                            width=_BTN_W, height=30,
                                            font=("微软雅黑", 9, "bold"))
        del_btn = create_gradient_button(row_btns, "🗑️ 删除选中", del_selected,
                                         colors=("#e53935", "#ff7043"),
                                         width=_BTN_W, height=30,
                                         font=("微软雅黑", 9, "bold"))
        add_btn = None
        if is_mod:
            add_btn = create_gradient_button(row_btns, "➕ 添加模组", add_mods,
                                             colors=("#00c853", "#00e676"),
                                             width=_BTN_W, height=30,
                                             font=("微软雅黑", 9, "bold"))
            # 拖拽（仅模组可拖入 .jar）
            try:
                from tkinterdnd2 import DND_FILES
                table.body.drop_target_register(DND_FILES)
                table.body.dnd_bind('<<Drop>>', on_tree_drop)
            except Exception:
                pass
        # 视图切换：表格（可勾选/排序/编辑）↔ 卡片（PCL2 风格只读预览）
        card_state = {"view": False, "list": None}

        # 多选菜单：全选/反选/清空勾选/删除选中，表格和卡片视图共用
        sel_menu = tk.Menu(win, tearoff=0)
        sel_menu.add_command(label="✅ 全选（当前显示）",
                             command=lambda: set_checked_mode("all"))
        sel_menu.add_command(label="🔄 反选", command=lambda: set_checked_mode("invert"))
        sel_menu.add_command(label="⬜ 清空勾选", command=lambda: set_checked_mode("none"))
        sel_menu.add_separator()
        sel_menu.add_command(label="🗑️ 删除选中", command=del_selected)
        btn_sel = create_gradient_button(
            row_btns, "☑ 多选", None, colors=("#00897b", "#26a69a"),
            width=88, height=30, font=("微软雅黑", 9, "bold"))
        btn_sel.set_command(lambda: sel_menu.tk_popup(
            btn_sel.winfo_rootx(), btn_sel.winfo_rooty() + btn_sel.winfo_height()))
        # 左边这一排按钮：按「界面按钮」的配置摆（隐藏的不摆、顺序照配置；下次打开生效）
        self._pack_window_btns("bigview", row_btns,
                               [("bv_detect", detect_btn), ("bv_remove", del_btn),
                                ("bv_add", add_btn), ("bv_select", btn_sel)],
                               side="left", padx=_PAD)

        def make_row(idx: int):
            """生成一条卡片数据（图标懒加载，只有滚到的行才解压）。"""
            m = meta.get(idx) or {}
            fname = str(m.get("name") or "")
            disp = str(m.get("disp") or "").strip()
            cn = str(m.get("cn") or "").strip()
            title = cn or disp or fname
            subtitle = ""
            if cn and disp and disp != cn:
                subtitle = disp
            elif cn and not disp and fname != cn:
                subtitle = fname
            return {
                "title": title,
                "subtitle": subtitle,
                "version": str(m.get("version") or ""),
                "desc": str(m.get("desc") or ""),
                "icon_key": m.get("path") or "",
                # 稳定身份（行号会因为扫描/排序/搜索而变化；卡片列表的键盘/滚动用得到）
                "dc_key": key_of(entries[idx]) if idx < len(entries) else "",
                "tags": m.get("tags") or [],
                "tags_online": bool(m.get("tags_online")),
                # 存在性状态 + 勾选态：卡片视图跟表格共用同一份数据
                "status": str(tr(m.get("status")) if m.get("status") else "…"),
                "checked": bool(checked.get(key_of(entries[idx]))) if idx < len(entries) else False,
            }

        def card_rows():
            """按当前显示顺序生成卡片数据。"""
            return [make_row(idx) for idx in order]

        def _card_icon(row):
            path = get_mod_icon(row.get("icon_key") or "")
            if not path:
                return None
            from PIL import Image
            return Image.open(path).convert("RGBA")

        def card_check(i: int):
            """卡片上的勾选框：和表格视图共用同一份选中状态。"""
            try:
                toggle_row(i)
                if card_state["list"] is not None:
                    card_state["list"].update_row(i, make_row(order[i]))
            except Exception:
                pass

        def card_action(i: int, action: str):
            """卡片悬停图标 / 右键菜单的动作：详情 / 打开所在位置 / 从清单移除。"""
            # 变量名不要再叫 idx：这个函数和外面那个超长函数共享作用域，
            # 同名变量会让静态检查把两边推断出来的类型合并（PyCharm 会报
            # "int | list 不能当字典键"这种误报）。这里用 entry_idx 并标注类型。
            try:
                entry_idx: int = order[i]
            except Exception:
                return
            if action == "info":
                open_mod_detail(i)
            elif action == "reveal":
                reveal_path(_path_of_row(i))
            elif action == "remove":
                name = (meta.get(entry_idx) or {}).get("name") or entries[entry_idx]
                entries.pop(entry_idx)
                meta.clear()
                checked.clear()
                rebuild(rescan=True)
                # 删掉的那一行在文本里就是 entry_idx+1，让它先淡出再重写
                write_back(fade_out_lines=[entry_idx + 1])
                self.log(trp("🗑 已从清单移除：{0}", name), level="WARNING", save=False)

        def card_menu(i: int, event):
            """单行操作（详情/定位/移除）+ 批量勾选，省得去顶栏点。

            「模组详情」只有模组清单才有 —— config 清单里那一行是配置文件/文件夹，
            弹出来只会是"该行没有可查看的模组文件"。
            """
            menu = tk.Menu(win, tearoff=0)
            if i >= 0:
                if is_mod:
                    menu.add_command(label="ℹ 查看详情",
                                     command=lambda: open_mod_detail(i))
                menu.add_command(label="📂 在文件夹中定位",
                                 command=lambda: reveal_path(_path_of_row(i)))
                menu.add_separator()
                menu.add_command(label="🗑 从清单移除", command=lambda: card_action(i, "remove"))
                # 选中/取消选中：给一个不依赖时间的入口（慢手速时双击可能认不出来）
                _is_on = bool(checked.get(key_of(entries[order[i]]))) if i < len(order) else False
                menu.add_command(label=("☐ 取消选中" if _is_on else "☑ 选中"),
                                 command=lambda: toggle_row(i))
                menu.add_separator()
            menu.add_command(label="✅ 全选（当前显示）",
                             command=lambda: set_checked_mode("all"))
            menu.add_command(label="🔄 反选", command=lambda: set_checked_mode("invert"))
            menu.add_command(label="⬜ 清空勾选", command=lambda: set_checked_mode("none"))
            menu.add_separator()
            menu.add_command(label="🗑️ 删除选中", command=del_selected)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass

        def table_menu(event):
            """表格视图右键 = 和卡片视图同一套菜单。

            慢手速时 Tk 的双击窗口（系统 0.5s）可能认不出来，这是不依赖时间的入口。
            """
            try:
                row = table.row_at(event.y)
            except Exception:
                return
            if row >= 0:
                card_menu(row, event)

        table.body.bind("<Button-3>", table_menu)

        def _ensure_card():
            if card_state["list"] is not None:
                return card_state["list"]
            from ui.card_list import ModCardList, default_fallback_icon
            card = ModCardList(win, self.theme, icon_provider=_card_icon,
                               fallback_icon=default_fallback_icon(),
                               on_double_click=((lambda i, e: open_mod_detail(i))
                                                if is_mod else None),
                               on_action=card_action,
                               on_context=card_menu,
                               on_check=card_check)
            card.grid(row=0, column=0, sticky="nsew")
            card.grid_remove()
            if not hasattr(self, '_big_view_cards'):
                self._big_view_cards = []
            self._big_view_cards.append(card)
            card_state["list"] = card
            return card

        def toggle_view():
            try:
                card_state["view"] = not card_state["view"]
                if card_state["view"]:
                    card = _ensure_card()
                    card.set_rows(card_rows())
                    table.grid_remove()
                    card.grid()
                    btn_view.set_text("📋 表格视图")
                    self.log("🗂 已切到卡片视图（只读预览；勾选/编辑请切回表格）",
                             level="INFO", save=False)
                else:
                    if card_state["list"] is not None:
                        card_state["list"].grid_remove()
                    table.grid()
                    btn_view.set_text("🗂 卡片视图")
            except Exception as exc:
                card_state["view"] = False
                try:
                    table.grid()
                except Exception:
                    pass
                self.log(trp("⚠️ 卡片视图不可用：{0}", exc), level="WARNING", save=False)

        btn_view = create_gradient_button(
            row_btns, "🗂 卡片视图", toggle_view,
            colors=("#7e57c2", "#9575cd"),
            width=118, height=30, font=("微软雅黑", 9, "bold"))
        if getattr(self, "big_view_view", "table") == "cards":
            toggle_view()          # 设置里选的是"打开就是卡片"

        # 卡片视图没有表头可点，排序收进一个下拉按钮（表格视图也能用）
        sort_menu = tk.Menu(win, tearoff=0)
        for _col, _label in (("name", "按名称"), ("status", "按状态"),
                             ("version", "按版本"), ("modid", "按 Mod ID"),
                             ("size", "按大小")):
            sort_menu.add_command(label=_label, command=lambda c=_col: sort_by(c))
        btn_sort = create_gradient_button(
            row_btns, "⇅ 排序", None,
            colors=("#546e7a", "#78909c"),
            width=88, height=30, font=("微软雅黑", 9, "bold"))
        btn_sort.set_command(lambda: sort_menu.tk_popup(
            btn_sort.winfo_rootx(), btn_sort.winfo_rooty() + btn_sort.winfo_height()))
        # 单击/双击由 VirtualTable 识别出行号后回调（见 _on_row_click / _on_row_double）
        btn_close_big = create_gradient_button(
            row_btns, "✖ 关闭", lambda: _close_popup(win),
            colors=("#e53935", "#c62828"),
            width=_BTN_W, height=30, font=("微软雅黑", 9, "bold"))
        # 右边这一排：同样按配置摆（卡片视图只有模组清单才有）
        self._pack_window_btns("bigview", row_btns,
                               [("bv_view", btn_view if is_mod else None),
                                ("bv_sort", btn_sort if is_mod else None),
                                ("bv_close", btn_close_big)],
                               side="right", padx=_PAD)
        # 标题栏的 × 同样走原生关闭
        try:
            win.protocol("WM_DELETE_WINDOW", lambda: _close_popup(win))
        except Exception:
            pass

        # 当主界面清单被外部修改（如差异"应用"/config导入/浏览添加/拖拽/清空）时，自动重载并保留勾选高亮
        def reload_entries():
            if not win.winfo_exists():
                return
            entries[:] = [ln.strip() for ln in source_text.get(
                "1.0", tk.END).splitlines() if ln.strip()]
            基线[0] = list(entries)        # 刚跟主界面同步过，写回基线跟着走
            meta.clear()
            rebuild(rescan=True)

        if is_mod:
            source_text.bind("<<ModlistChanged>>", lambda e: reload_entries())
        else:
            source_text.bind("<<ConfigChanged>>", lambda e: reload_entries())

        rebuild(rescan=True)
        poll()
        # 登记该表格，方便主题切换时同步配色
        if not hasattr(self, '_big_view_tables'):
            self._big_view_tables = []
        self._big_view_tables.append(table)
        apply_theme_to_widget_tree(win, self.theme)
        win.update_idletasks()
        # 窗口映射前先把列宽按实际布局排好，否则显示出来之后才重排，会二次闪烁
        table.fit_now()
        w = win.winfo_width()
        h = win.winfo_height()
        x = (win.winfo_screenwidth() // 2) - (w // 2)
        y = (win.winfo_screenheight() // 2) - (h // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
        # 原生显示：排版、列宽、首帧都在隐藏状态下做完了，直接映射出来即可。
        # 不再用 alpha 淡入——那层 layered 样式会连系统的显示/关闭动画一起挡掉，
        # 关了会"啪"地消失；现在开和关都交给 Windows 自己。
        try:
            win.deiconify()
        except Exception:
            pass
        focus_window(win)
        self._lock_tk_mirror("bigview", win)      # 清单窗口开着 → 主界面不能编辑
        return win                                # 调用方/测试要拿它（原来返回 None）

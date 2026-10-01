# ui/mw_lists.py
"""清单管理：增删改、拖入粘贴、存在性检查、撤销、徽章。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import re
import time
import tkinter as tk
from collections import Counter
from core.migrator import _is_safe_path, match_mod
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from ui.mw_common import _EXTRA_PRESETS, _center_window, _file_task_lock, _grad_width
from utils import i18n
from utils.helpers import (
    DataText, RoundedTextArea, create_gradient_button, focus_window, set_window_icon,
)
from utils.theme import apply_theme_to_widget_tree
# 日志/文案模板：trp 按位置填值（中文模式下与原 f-string 逐字一致）
from utils.i18n import trp


class ListsMixin:
    """清单管理：增删改、拖入粘贴、存在性检查、撤销、徽章。"""

    # ---------- 清单页标签上的条数徽章 ----------
    def _quick_add_extra(self, 条目, 说明=""):
        """快捷把某个条目加进「其它文件」清单（清单是唯一依据：加了才会带）。

        和"浏览添加"的区别：这里给的是**固定名字**（options.txt、shaderpacks/…），
        所以先看源实例里到底有没有 —— 没有就别往清单里塞（塞了也会在迁移时被跳过，
        只会让人以为"我明明加了"）。
        """
        源 = self.source_path.get().strip()
        if not 源:
            self.log("⚠️ 请先选择源整合包目录，再往清单里加东西", level="WARNING")
            return 0
        if not (Path(源) / 条目.replace("\\", "/").rstrip("/")).exists():
            self.log("⚠️ 源目录里没有 %s，没加进清单" % 条目, level="WARNING")
            return 0
        添加, _失败 = self._add_extra_paths([条目])
        if 添加:
            self.log("✅ 已把 %s 加入「其它文件」清单，迁移时会一起带过去%s"
                     % (条目, ("（%s）" % 说明) if 说明 else ""), level="SUCCESS")
        else:
            self.log("ℹ️ %s 已经在清单里了" % 条目, level="INFO", save=False)
        return 添加

    def _quick_add_preset(self, 条目):
        """「＋ 常用目录」菜单里的一项 → 加进清单。"""
        说明 = next((d for _k, rel, d in _EXTRA_PRESETS if rel == 条目), "")
        self._quick_add_extra(条目, 说明)

    def _open_extra_preset_menu(self):
        """「＋ 常用目录」：点一下就把它加进清单（清单是唯一依据，加了才会带）。

        以前这些是设置页里的一组勾选框（"默认携带的目录"）：勾上之后迁移那一刻自动
        并进清单，而且**不写回**清单 —— 用户看不到、也删不掉。现在统一成"主动加进清单"。
        """
        已有 = {x.strip().replace("\\", "/").rstrip("/").lower()
                for x in self.extra_text.get("1.0", "end-1c").splitlines() if x.strip()}
        try:
            菜单 = tk.Menu(self.root, tearoff=0, bg=self.theme.get("entry_bg", "#ffffff"),
                           fg=self.theme.get("fg", "#000000"),
                           activebackground=self.theme.get("card_sel_bar", "#2f7fd1"),
                           activeforeground="#ffffff", font=("微软雅黑", 9))
        except Exception:
            菜单 = tk.Menu(self.root, tearoff=0)
        for _键, 条目, 说明 in _EXTRA_PRESETS:
            在了 = 条目.replace("\\", "/").rstrip("/").lower() in 已有
            菜单.add_command(
                label=("%s   %s%s" % (条目, 说明, "　✅ 已在清单" if 在了 else "")),
                command=(lambda e=条目: self._quick_add_preset(e)))
        try:
            x = self.add_extra_preset_btn.winfo_rootx()
            y = (self.add_extra_preset_btn.winfo_rooty()
                 + self.add_extra_preset_btn.winfo_height())
            菜单.tk_popup(x, y)
        finally:
            try:
                菜单.grab_release()
            except Exception:
                pass

    def _quick_add_options_txt(self):
        """一键把 options.txt 加进「其它文件」清单。

        options.txt 以前是迁移里**无条件复制**的（还 overwrite=True）；用户要求
        "一切都要自己选"，所以那一步删了 —— 想带就用这个按钮加进清单，
        冲突策略/备份/回滚都跟别的清单条目一样。
        """
        self._quick_add_extra("options.txt")

    def _bind_badge_refresh(self, widget):
        """文本框内容一变就刷新标签上的条数（<<Modified>> 覆盖键盘输入、粘贴、程序插入）。"""
        def _on_modified(event):
            try:
                widget.edit_modified(False)
            except Exception:
                pass
            self._schedule_badge_refresh()
        try:
            widget.bind("<<Modified>>", _on_modified, add="+")
        except Exception:
            pass

    def _schedule_badge_refresh(self):
        """防抖 300ms：连续输入时不要每敲一下都重排标签栏。"""
        old = getattr(self, "_badge_job", None)
        if old is not None:
            try:
                self.root.after_cancel(old)
            except Exception:
                pass
        try:
            self._badge_job = self.root.after(300, self._refresh_list_badges)
        except Exception:
            self._badge_job = None

    def _refresh_list_badges(self):
        """把三个清单的条数挂到标签上：🧩 模组清单 376 / ⚙️ config 清单 12 / 📦 其它文件 5。"""
        self._badge_job = None
        tabs = getattr(self, "list_tabs", None)
        if tabs is None:
            return

        def count(widget, skip_comments=False):
            try:
                lines = widget.get("1.0", tk.END).splitlines()
            except Exception:
                return 0
            n = 0
            for ln in lines:
                s = ln.strip()
                if not s:
                    continue
                if skip_comments and s.startswith("#"):
                    continue
                n += 1
            return n

        try:
            mods = count(getattr(self, "mod_text", None)) if hasattr(self, "mod_text") else 0
            cfgs = count(getattr(self, "config_text", None), True) \
                if hasattr(self, "config_text") else 0
            extras = count(getattr(self, "extra_text", None), True) \
                if hasattr(self, "extra_text") else 0
            # 带条数的页签文案是 % 拼出来的 —— 词典对不上这种动态串，
            # 所以走 trf 模板（见 utils/i18n.py 顶部说明）
            tabs.set_label(0, i18n.trf("🧩 模组清单 {n}", n=mods) if mods
                           else i18n.tr("🧩 模组清单"))
            tabs.set_label(1, i18n.trf("⚙️ config 清单 {n}", n=cfgs) if cfgs
                           else i18n.tr("⚙️ config 清单"))
            tabs.set_label(2, i18n.trf("📦 其它文件 {n}", n=extras) if extras
                           else i18n.tr("📦 其它文件"))
        except Exception:
            pass

    # ---------- 检查模组存在性 ----------
    @_file_task_lock("检查模组是否存在")
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
            self.log(trp("❌ 源 mods 目录不存在：{0}", src_mods), level="ERROR")
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

        def norm(e):
            return e.replace("\\", "/").lower()

        counts = Counter(norm(m) for m in modlist)

        missing = []
        found = []
        for item in modlist:
            matched = match_mod(item, source_files, name_map)
            if matched:
                found.append(item)
            else:
                missing.append(item)

        self.log(trp("📊 模组清单检查结果：总清单项数 {0}", len(modlist)), level="INFO")
        self.log(trp("✅ 存在的模组：{0}", len(found)), level="SUCCESS")
        self.log(trp("❌ 缺失的模组：{0}", len(missing)),
                 level="PLAIN" if missing else "INFO")
        if missing:
            self.root.bell()
            self.log("缺失列表：", level="WARNING")
            for m in missing[:50]:
                self.log(f"  - {m}", level="ERROR")
            if len(missing) > 50:
                self.log(trp("  ... 还有 {0} 个未显示", len(missing) - 50), level="WARNING")

        # 主界面临时闪烁高亮：存在=绿 / 缺失=红 / 重复=黄，1秒后自动恢复
        self._clear_mod_status()
        _flash_after = getattr(self, "_mod_flash_after", None)
        if _flash_after:
            try:
                self.root.after_cancel(_flash_after)
            except Exception:
                pass
        flash_lines = self.mod_text.get("1.0", tk.END).splitlines()
        for i, ln in enumerate(flash_lines):
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            idx, end = f"{i + 1}.0", f"{i + 1}.end"
            if not match_mod(s, source_files, name_map):
                self.mod_text.tag_add("mod_missing", idx, end)
            elif counts[norm(s)] > 1:
                self.mod_text.tag_add("mod_duplicate", idx, end)
            else:
                self.mod_text.tag_add("mod_ok", idx, end)
        self._mod_flash_after = self.root.after(1000, self._clear_mod_status)

    # ---------- 从变更日志导入 ----------
    def import_from_changelog(self):
        # 单实例：变更日志对话框已打开则聚焦，避免重复弹窗
        existing = getattr(self, "_changelog_dialog", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    focus_window(existing)      # 已有就置顶
                    return
            except Exception:
                pass
        dialog = tk.Toplevel(self.root)
        self._changelog_dialog = dialog
        dialog.withdraw()       # 构建完居中后再显示，避免"闪现-跳到中间"
        dialog.title("从变更日志提取模组清单")
        dialog.geometry("800x600")
        dialog.transient(self.root)
        set_window_icon(dialog)
        tk.Label(dialog,
                 text="请粘贴完整的变更日志文本（包含 'Added mods:' 和 'Updated mods:' 部分）：").pack(pady=5)
        text_box = RoundedTextArea(dialog, self.theme, wrap=tk.WORD, height=20)
        text_widget = text_box.text
        text_box.pack(fill="both", expand=True, padx=10, pady=5)
        self._smooth(text_widget)

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
                    trp("已提取到 {0} 个 Added 模组，{1} 个 Updated 模组。\n是否将 Updated 模组也添加到复制清单中？\n\n点击“是” → 全部添加\n点击“否” → 只添加 Added 模组\n点击“取消” → 不添加任何模组", len(added), len(updated))
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
                    trp("从变更日志中提取了 {0} 个模组（Added: {1}, Updated: {2}）", len(all_mods), len(added), len(updated)),
                    level="SUCCESS"
                )
                self.save_config()
                self._update_text_states()
                dialog.destroy()
            else:
                messagebox.showinfo("提示", "未添加任何模组")

        btn_extract = create_gradient_button(
            dialog, "提取并应用", extract_and_close,
            colors=("#00c853", "#00e676"),
            width=_grad_width("提取并应用"), height=30, font=("微软雅黑", 9, "bold"))
        btn_extract.pack(pady=10)
        apply_theme_to_widget_tree(dialog, self.theme)
        _center_window(dialog, 800, 600)
        dialog.deiconify()
        focus_window(dialog)

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
    def browse_add_config_entry(self):
        """浏览添加文件夹：优先用原生 Windows 多选文件夹对话框（comtypes）。
        原生不可用时回退到自定义树形多选对话框。"""
        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(trp("❌ 源 config 目录不存在：{0}", src_config), level="ERROR")
            return

        # 优先原生多选文件夹对话框（返回绝对路径）
        try:
            from utils.native_dialog import pick_folders
            selected = pick_folders(
                parent_hwnd=self.root.winfo_id(),
                initial_dir=str(src_config),
                title="请选择源 config 下的文件夹（可多选）")
            native_ok = True
        except Exception:
            selected = None
            native_ok = False

        if native_ok:
            if not selected:
                return
            selected_paths = [str(p) for p in selected]
        else:
            # 原生失败 → 回退自定义树形多选（返回相对路径）
            self.log("ℹ️ 原生对话框不可用，已切换到自定义多选对话框", level="INFO")
            selected = self._pick_config_folders(src_config)
            if not selected:
                return
            selected_paths = [str(src_config / s) for s in selected]

        added, failed = self._add_config_paths(selected_paths)
        if added:
            self.log(trp("✅ 已添加 {0} 个 config 子文件夹条目", added), level="SUCCESS")
        elif not failed:
            self.log("ℹ️ 所选文件夹均已在 config 清单中，未重复添加", level="INFO")
        if failed:
            messagebox.showwarning(
                "添加提示",
                f"⚠️ 有 {len(failed)} 项未添加（不在源 config 目录下或不安全）：\n"
                + "\n".join(str(f) for f in failed[:5]), parent=self.root)

    def _pick_config_folders(self, src_config):
        """树形多选对话框：可展开/折叠浏览 config 下的子文件夹，点击文件夹名即勾选。
        返回选中的相对路径列表（已去除被祖先覆盖的子项），取消则返回 None。"""
        theme = self.theme

        def child_dirs(parent_abs):
            try:
                return sorted([e for e in parent_abs.iterdir() if e.is_dir()],
                              key=lambda p: p.name.lower())
            except Exception:
                return []

        checked = set()
        folder_iids = set()  # 真实文件夹节点的 iid（= 相对路径）
        rel_abs = {}  # iid -> 绝对 Path
        loaded = set()  # 已展开加载过子目录的 iid
        captured = {"value": None}

        dlg = tk.Toplevel(self.root)
        dlg.withdraw()
        dlg.title("选择 config 下的文件夹（可多选）")
        dlg.geometry("780x640")
        dlg.minsize(600, 500)
        dlg.transient(self.root)
        dlg.configure(bg=theme["bg"])
        set_window_icon(dlg)

        hint = tk.Label(dlg, text="点击文件夹名即可勾选/取消；点击 ▸ 展开子文件夹；可同时勾选多个。",
                        bg=theme["bg"], fg=theme["muted_fg"])
        hint.pack(fill="x", padx=10, pady=(10, 2))

        tw = tk.Frame(dlg, bg=theme["bg"])
        tw.pack(fill="both", expand=True, padx=10, pady=4)
        tree = ttk.Treeview(tw, columns=("chk", "path"), show="tree headings",
                            selectmode="none")
        tree.heading("#0", text="📁 文件夹")
        tree.column("#0", width=300, anchor="w", stretch=True)
        tree.heading("chk", text="☑")
        tree.column("chk", width=40, anchor="center", stretch=False)
        tree.heading("path", text="📄 完整相对路径")
        tree.column("path", width=360, anchor="w", stretch=False)
        vsb = ttk.Scrollbar(tw, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(tw, orient="horizontal", command=tree.xview)
        self._smooth(tree, rows=True)
        # 横向滚动条只在内容真的超出可视宽度时才出现（默认宽度下 #0 列会自动拉伸填满，
        # 常驻一条拖不动的横向滚动条只会让人困惑）
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
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tw.grid_rowconfigure(0, weight=1)
        tw.grid_columnconfigure(0, weight=1)

        def sync_mark(iid):
            tree.set(iid, "chk", "☑" if iid in checked else "☐")

        def update_count():
            # 只让"数字"那一段滚起来，前后说明文字是灰的
            self._roll_counter(count_lbl.part(1), str(len(checked)))

        def populate(parent_iid, parent_abs):
            for c in tree.get_children(parent_iid):
                tree.delete(c)
            for e in child_dirs(parent_abs):
                rel_s = str(e.relative_to(src_config))
                iid = rel_s
                folder_iids.add(iid)
                rel_abs[iid] = e
                mark = "☑" if rel_s in checked else "☐"
                if child_dirs(e):
                    tree.insert(parent_iid, "end", iid=iid, text=e.name,
                                values=(mark, rel_s))
                    tree.insert(iid, "end", iid=iid + "::ph", text="…",
                                values=("", ""))
                else:
                    tree.insert(parent_iid, "end", iid=iid, text=e.name,
                                values=(mark, rel_s))

        def on_open(event):
            iid = tree.focus()
            if not iid or iid not in rel_abs or iid in loaded:
                return
            populate(iid, rel_abs[iid])
            loaded.add(iid)

        def sync_all():
            def walk(iid):
                for c in tree.get_children(iid):
                    if c in folder_iids:
                        sync_mark(c)
                    walk(c)

            walk("")

        def on_click(event):
            # 点中展开箭头 → 交给默认行为展开/折叠，不改变勾选
            if tree.identify_element(event.x, event.y) == "Treeview.indicator":
                return
            row = tree.identify_row(event.y)
            if not row or row.endswith("::ph") or row not in folder_iids:
                return
            if row in checked:
                checked.discard(row)
            else:
                checked.add(row)
            sync_mark(row)
            update_count()
            return "break"

        tree.bind("<Button-1>", on_click)
        tree.bind("<<TreeviewOpen>>", on_open)

        count_lbl = DataText(dlg, theme, [("已勾选 ", "muted_fg"),
                                          ("0", "data_num_fg"),
                                          (" 个文件夹", "muted_fg")])
        count_lbl.pack(fill="x", padx=10, pady=(0, 4))

        def mk_button(parent, text, cmd, colors=("#546e7a", "#78909c"), guard_ms=300):
            """和程序里其它按钮同一款"灵动"渐变按钮（悬停浮起+扫光、按下弹回）。

            冷却期内的重复点击直接忽略（这一点和渐变按钮一致）。
            """
            def _run():
                now = time.time()
                if guard_ms and (now - click_at["t"]) * 1000 < guard_ms:
                    return
                click_at["t"] = now
                cmd()

            click_at = {"t": 0.0}
            return create_gradient_button(parent, text, _run, colors=colors,
                                          width=_grad_width(text), height=30,
                                          font=("微软雅黑", 9, "bold"))

        def select_top():
            checked.clear()
            for e in child_dirs(src_config):
                checked.add(str(e.relative_to(src_config)))
            sync_all()
            update_count()

        def clear_all():
            checked.clear()
            sync_all()
            update_count()

        btnbar = tk.Frame(dlg, bg=theme["bg"])
        btnbar.pack(fill="x", padx=10, pady=(8, 10))
        mk_button(btnbar, "全选顶层", select_top).pack(side="left", padx=4)
        mk_button(btnbar, "全不选", clear_all).pack(side="left", padx=4)

        def confirm():
            picked = [Path(r) for r in checked]
            # 子项若已被选中的祖先覆盖（其下整棵子树都会被迁移），无需重复添加
            norm = [str(p) for p in sorted(picked)
                    if not any(q != p and p.is_relative_to(q) for q in picked)]
            captured["value"] = norm
            dlg.destroy()

        def cancel():
            captured["value"] = None
            dlg.destroy()

        mk_button(btnbar, "确定添加", confirm).pack(side="right", padx=4)
        mk_button(btnbar, "取消", cancel).pack(side="right", padx=4)

        populate("", src_config)
        update_count()
        _center_window(dlg, 780, 640)
        dlg.deiconify()
        focus_window(dlg)
        dlg.wait_window()
        return captured.get("value")

    def browse_add_config_file(self):
        """浏览添加文件：一次可多选多个 config 下的文件。"""
        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(trp("❌ 源 config 目录不存在：{0}", src_config), level="ERROR")
            return

        selected = filedialog.askopenfilenames(
            title="请选择源 config 下的文件（可多选）",
            initialdir=str(src_config),
            filetypes=[("所有文件", "*.*")]
        )
        if not selected:
            return
        added, failed = self._add_config_paths(selected)
        if added:
            self.log(trp("✅ 已添加 {0} 个 config 文件条目", added), level="SUCCESS")
        elif not failed:
            self.log("ℹ️ 所选文件均已在 config 清单中，未重复添加", level="INFO")
        if failed:
            messagebox.showwarning(
                "添加提示",
                f"⚠️ 有 {len(failed)} 项未添加（不在源 config 目录下或不安全）：\n"
                + "\n".join(str(f) for f in failed[:5]), parent=self.root)

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

    def _setup_custom_undo(self, widget, kind):
        """用自定义撤销栈替代 Tk 原生撤销。

        Tk 的 Text 原生撤销会把"连续删除"合并成一步，无法逐个还原；
        这里在每次内容修改后快照上一个状态，撤销时逐个恢复。kind 为
        "mod" 或 "config"，用于撤销后刷新对应的清单/保存配置。
        """
        widget._undo_stack = []
        widget._redo_stack = []
        widget._last = widget.get("1.0", "end-1c")
        widget._custom_undo_kind = kind

        def _snapshot():
            if getattr(widget, "_skip_snapshot", False):
                widget.edit_modified(False)
                return
            cur = widget.get("1.0", "end-1c")
            if cur != widget._last:
                widget._undo_stack.append(widget._last)
                if len(widget._undo_stack) > 200:
                    widget._undo_stack.pop(0)
                widget._last = cur
                widget._redo_stack.clear()
                # 内容变化后，存在性闪烁高亮已失效，清除以免误读
                try:
                    if kind == "config":
                        self._clear_config_status()
                    elif kind == "mod":
                        self._clear_mod_status()
                    elif kind == "extra":
                        self._clear_extra_status()
                except Exception:
                    pass
            widget.edit_modified(False)

        def _on_modified(event):
            _snapshot()

        def _after_edit():
            try:
                self.save_config()
            except Exception:
                pass
            if kind == "mod":
                try:
                    self._apply_mod_new_tags()
                    self._notify_modlist_change()
                except Exception:
                    pass
            elif kind == "config":
                try:
                    self._notify_config_change()
                except Exception:
                    pass
            # 其它文件清单没有对应的"放大查看"窗口，撤销后保存一下就够了

        def _set_content(text):
            # 先记住滚动位置和光标：delete+insert 会把两者都甩回开头，
            # 撤销后视图突然"置顶"就是这么来的。
            try:
                first = widget.yview()[0]
            except Exception:
                first = 0.0
            try:
                caret = widget.index(tk.INSERT)
            except Exception:
                caret = "1.0"
            widget.configure(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert("1.0", text)
            try:
                widget.mark_set(tk.INSERT, caret)
            except Exception:
                pass
            try:
                widget.yview_moveto(first)
            except Exception:
                pass
            widget._last = text
            widget.edit_modified(False)

        def _undo(event=None):
            if not widget._undo_stack:
                return "break"
            cur = widget.get("1.0", "end-1c")
            widget._redo_stack.append(cur)
            prev = widget._undo_stack.pop()
            _set_content(prev)
            _after_edit()
            return "break"

        def _redo(event=None):
            if not widget._redo_stack:
                return "break"
            cur = widget.get("1.0", "end-1c")
            widget._undo_stack.append(cur)
            nxt = widget._redo_stack.pop()
            _set_content(nxt)
            _after_edit()
            return "break"

        widget.bind("<<Modified>>", _on_modified, add="+")
        widget.bind("<Control-z>", _undo)
        widget.bind("<Control-y>", _redo)

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
        self.log(trp("✅ 已添加 {0} 个模组", len(new)), level="SUCCESS")
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
            messagebox.showinfo("添加成功", trp("✅ 已添加 {0} 个模组。", added), parent=self.root)
        else:
            messagebox.showinfo("添加提示", "所选模组已在清单中，未新增。", parent=self.root)
        if non_jar:
            messagebox.showwarning("添加提示", trp("⚠️ 有 {0} 个非 .jar 文件被跳过。", non_jar), parent=self.root)

    def add_mods(self):
        """从文件选择器多选并批量添加模组（默认定位到源实例的 mods 目录）。"""
        src = self.source_path.get().strip()
        initial = None
        if src:
            sp = Path(src)
            mods_dir = sp / "mods"
            initial = str(mods_dir if mods_dir.exists() else sp)
        files = filedialog.askopenfilenames(
            title="选择要添加的模组（可多选）",
            initialdir=initial,
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

    def _add_paths_to(self, text_widget, base_dir, paths):
        """把绝对/相对路径转成"相对 base_dir"的条目，追加进某个清单文本框。

        返回 (新增数, 失败列表)。config 清单和其它文件清单共用它，只是 base_dir 不同：
        config 是"源实例/config"，其它文件是"源实例根目录"（shaderpacks、options.txt…）。
        文件/文件夹都收；文件夹条目末尾加 "/" 与文件区分（去重按去掉末尾 "/" 归一化）；
        安全校验沿用 _is_safe_path（挡 ".." 和绝对路径）。
        """
        if not str(base_dir or "").strip():
            return 0, ["尚未设置源整合包实例根目录"]
        base = Path(base_dir)
        if not base.exists():
            return 0, [f"基准目录不存在：{base}"]
        lines = {l.strip().replace("\\", "/").rstrip("/")
                 for l in text_widget.get("1.0", tk.END).splitlines() if l.strip()}
        added, failed = 0, []
        for p in paths:
            tp = Path(p)
            try:
                rel = tp.relative_to(base) if tp.is_absolute() else Path(p)
            except ValueError:
                failed.append(str(tp))
                continue
            rel_s = str(rel).replace("\\", "/")     # 清单里统一用正斜杠，好读也好比
            if not _is_safe_path(rel_s):
                failed.append(str(tp))
                continue
            if (base / rel_s).is_dir() and not rel_s.endswith("/"):
                rel_s = rel_s.rstrip("/") + "/"
            if rel_s.rstrip("/") not in lines:
                text_widget.configure(state=tk.NORMAL)
                cur = text_widget.get("1.0", tk.END)
                # 只有"已经有内容且末尾不是换行"时才补分隔换行，否则连续添加会出现空行
                if cur.strip() and not cur.endswith("\n"):
                    text_widget.insert(tk.END, "\n")
                text_widget.insert(tk.END, rel_s + "\n")
                lines.add(rel_s.rstrip("/"))
                added += 1
        return added, failed

    def _add_config_paths(self, paths):
        """（config 清单）路径相对 源实例/config。"""
        src = self.source_path.get().strip()
        if not src:
            return 0, ["尚未设置源实例根目录"]
        added, failed = self._add_paths_to(self.config_text, Path(src) / "config", paths)
        if added:
            self.save_config()
            self._update_text_states()
            # 内容已变化，之前的存在性高亮随之失效
            self._clear_config_status()
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
            messagebox.showinfo("添加成功", trp("✅ 已添加 {0} 个 config 条目。", added), parent=self.root)
        if failed:
            messagebox.showwarning("添加提示",
                                   f"⚠️ 有 {len(failed)} 项未添加（不在源 config 目录下或不安全）：\n"
                                   + "\n".join(failed[:5]), parent=self.root)

    def clear_mod_list(self):
        """清空模组清单。"""
        self.mod_text.configure(state=tk.NORMAL)
        self.mod_text.delete("1.0", tk.END)
        self._new_mod_keys.clear()  # 清空后不再保留"新添加"高亮
        self.save_config()
        self._update_text_states()
        self._notify_modlist_change()

    def clear_config(self):
        """清空 config 清单。"""
        self.config_text.configure(state=tk.NORMAL)
        self.config_text.delete("1.0", tk.END)
        self._clear_config_status()
        self.save_config()
        self._update_text_states()
        self._notify_config_change()

    # ---------- 其它文件清单（路径相对**整合包根目录**） ----------
    # 用途：mods / config / saves 之外、但你想一起带走的东西，
    # 例：shaderpacks/、resourcepacks/、options.txt、servers.dat、kubejs/、scripts/
    def _extra_base(self):
        """其它文件清单的基准目录 = 源整合包根目录（没填就返回 None）。"""
        src = self.source_path.get().strip()
        return Path(src) if src else None

    def _add_extra_paths(self, paths):
        src = self.source_path.get().strip()
        if not src:
            return 0, ["尚未设置源整合包实例根目录"]
        added, failed = self._add_paths_to(self.extra_text, Path(src), paths)
        if added:
            self.save_config()
            self._update_text_states()
            self._clear_extra_status()
        return added, failed

    def browse_add_extra_entry(self):
        """浏览添加文件夹（可多选）：相对整合包根目录，例如 shaderpacks/。"""
        base = self._extra_base()
        if base is None:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        if not base.exists():
            self.root.bell()
            self.log(trp("❌ 源整合包目录不存在：{0}", base), level="ERROR")
            return

        selected, native_ok = None, False
        try:
            from utils.native_dialog import pick_folders

            selected = pick_folders(
                parent_hwnd=self.root.winfo_id(), initial_dir=str(base),
                title="请选择要一起带走的文件夹（相对整合包根目录，可多选）")
            native_ok = True
        except Exception:
            native_ok = False
        if not native_ok:
            one = filedialog.askdirectory(title="请选择要一起带走的文件夹",
                                          initialdir=str(base))
            selected = [one] if one else []
        if not selected:
            return

        added, failed = self._add_extra_paths([str(p) for p in selected])
        if added:
            self.log(trp("✅ 已添加 {0} 个其它文件条目（相对整合包根目录）", added), level="SUCCESS")
        elif not failed:
            self.log("ℹ️ 所选文件夹均已在其它文件清单中，未重复添加", level="INFO")
        if failed:
            messagebox.showwarning(
                "添加提示",
                f"⚠️ 有 {len(failed)} 项未添加（不在整合包目录下或不安全）：\n"
                + "\n".join(str(f) for f in failed[:5]), parent=self.root)

    def browse_add_extra_file(self):
        """浏览添加文件（可多选）：相对整合包根目录，例如 options.txt。"""
        base = self._extra_base()
        if base is None:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        if not base.exists():
            self.root.bell()
            self.log(trp("❌ 源整合包目录不存在：{0}", base), level="ERROR")
            return
        selected = filedialog.askopenfilenames(
            title="请选择要一起带走的文件（可多选，相对整合包根目录）",
            initialdir=str(base), filetypes=[("所有文件", "*.*")])
        if not selected:
            return
        added, failed = self._add_extra_paths(list(selected))
        if added:
            self.log(trp("✅ 已添加 {0} 个其它文件条目", added), level="SUCCESS")
        elif not failed:
            self.log("ℹ️ 所选文件均已在其它文件清单中，未重复添加", level="INFO")
        if failed:
            messagebox.showwarning(
                "添加提示",
                f"⚠️ 有 {len(failed)} 项未添加（不在整合包目录下或不安全）：\n"
                + "\n".join(str(f) for f in failed[:5]), parent=self.root)

    def _on_extra_drop(self, event):
        """从资源管理器拖入文件/文件夹 → 其它文件清单（自动算成相对整合包根目录）。"""
        try:
            files = self.root.tk.splitlist(event.data)
        except Exception:
            files = event.data
        added, failed = self._add_extra_paths(list(files))
        if added:
            messagebox.showinfo("添加成功", trp("✅ 已添加 {0} 个其它文件条目。", added),
                               parent=self.root)
        if failed:
            messagebox.showwarning(
                "添加提示",
                f"⚠️ 有 {len(failed)} 项未添加（不在整合包目录下或不安全）：\n"
                + "\n".join(str(f) for f in failed[:5]), parent=self.root)

    def clear_extra_list(self):
        """清空其它文件清单。"""
        self.extra_text.configure(state=tk.NORMAL)
        self.extra_text.delete("1.0", tk.END)
        self._clear_extra_status()
        self.save_config()
        self._update_text_states()

    def _clear_extra_status(self):
        """清掉其它文件清单的存在性高亮。"""
        try:
            for tag in ("extra_ok", "extra_missing", "extra_duplicate"):
                self.extra_text.tag_remove(tag, "1.0", tk.END)
            self._extra_status_applied = False
        except Exception:
            pass

    def check_extralist_existence(self):
        """检查其它文件清单里每个条目（相对整合包根目录）在源目录是否存在并高亮。"""
        now = time.time()
        if now - getattr(self, "last_check_extra_time", 0) < 2:
            self.root.bell()
            self.log("⚠️ 请勿频繁操作！请稍后再试。", level="WARNING")
            return
        self.last_check_extra_time = now

        base = self._extra_base()
        if base is None:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        if not base.exists():
            self.root.bell()
            self.log(trp("❌ 源整合包目录不存在：{0}", base), level="ERROR")
            return

        entries = [l.strip() for l in self.extra_text.get("1.0", tk.END).splitlines()
                   if l.strip() and not l.strip().startswith("#")]
        if not entries:
            self.root.bell()
            self.log("⚠️ 当前其它文件清单为空", level="WARNING")
            self._clear_extra_status()
            return

        def norm(e):
            return e.replace("\\", "/").lower().rstrip("/")

        counts = Counter(norm(e) for e in entries)
        self._clear_extra_status()
        ok = duplicate = missing = 0
        missing_samples = []
        for i, ln in enumerate(self.extra_text.get("1.0", tk.END).splitlines()):
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            idx, end = f"{i + 1}.0", f"{i + 1}.end"
            if not (base / s.replace("\\", "/")).exists():
                self.extra_text.tag_add("extra_missing", idx, end)
                missing += 1
                if len(missing_samples) < 60:
                    missing_samples.append(s)
            elif counts[norm(s)] > 1:
                self.extra_text.tag_add("extra_duplicate", idx, end)
                duplicate += 1
            else:
                self.extra_text.tag_add("extra_ok", idx, end)
                ok += 1
        self._extra_status_applied = True

        self.log(trp("📊 其它文件清单检查结果：总条目 {0}，去重后 {1} 个", len(entries), len(counts)),
                 level="INFO")
        self.log(trp("✅ 存在的条目：{0}", ok), level="SUCCESS")
        if duplicate:
            self.log(trp("⚠️ 重复条目：{0} 行", duplicate), level="WARNING")
        if missing:
            self.log(trp("❌ 缺失的条目：{0}", missing), level="PLAIN")
            self.root.bell()
            for m in missing_samples[:50]:
                self.log(f"  - {m}", level="ERROR")
            if missing > 50:
                self.log(trp("  ... 还有 {0} 条未显示", missing - 50), level="WARNING")
        elif not duplicate:
            self.log("✅ 其它文件条目均存在且无重复。", level="SUCCESS")

        old = getattr(self, "_extra_flash_after", None)
        if old:
            try:
                self.root.after_cancel(old)
            except Exception:
                pass
        self._extra_flash_after = self.root.after(1000, self._clear_extra_status)

    def _notify_modlist_change(self):
        """通知已打开的"放大查看"刷新模组清单。"""
        try:
            self.mod_text.event_generate("<<ModlistChanged>>")
        except Exception:
            pass
        self._push_big_view_entries(self.mod_text)

    def _notify_config_change(self):
        """通知已打开的"放大查看"刷新 config 清单。"""
        try:
            self.config_text.event_generate("<<ConfigChanged>>")
        except Exception:
            pass
        self._push_big_view_entries(self.config_text)

    def _on_text_modified(self, text_widget):
        """文本框内容变了 —— 防抖之后把清单推给 Qt 放大查看。

        `<<Modified>>` 是唯一能同时覆盖"手打"和"程序改"的信号（Tk 的虚拟事件
        `<<ModlistChanged>>` 只在 import/应用/清空那几条流程里手动发）。
        它只会报一次，处理完必须 edit_modified(False) 才能再收到下一次。
        """
        try:
            text_widget.edit_modified(False)
        except Exception:
            return
        if not self._qt_host_alive("bigview"):
            return
        任务 = getattr(self, "_entries_push_jobs", None)
        if 任务 is None:
            self._entries_push_jobs = 任务 = {}
        旧 = 任务.get(id(text_widget))
        if 旧:
            try:
                self.root.after_cancel(旧)
            except Exception:
                pass
        try:
            任务[id(text_widget)] = self.root.after(
                300, lambda w=text_widget: self._push_big_view_entries(w))
        except Exception:
            pass

    def _parse_config_lines(self):
        """读取 config 清单中非空、非注释的行（保持顺序，去除首尾空白）。"""
        raw = self.config_text.get("1.0", tk.END).splitlines()
        return [ln.strip() for ln in raw if ln.strip() and not ln.strip().startswith("#")]

    def _mark_config_folders(self):
        """按源 config 目录检测：文件夹条目在末尾加 "/"（便于与文件条目区分）。

        幂等：已带 "/" 的行跳过；源路径未设置或 config 目录不存在时不处理。
        只在内容加载、源路径变化、config 检查等安全时机调用（不做逐键实时改写，
        避免与用户输入/撤销冲突）。
        """
        try:
            src = self.source_path.get().strip()
            if not src:
                return
            src_config = Path(src) / "config"
            if not src_config.exists():
                return
        except Exception:
            return

        widget = self.config_text
        try:
            raw = widget.get("1.0", "end-1c")
        except Exception:
            return

        new_lines = []
        changed = False
        for ln in raw.split("\n"):
            s = ln.strip()
            if s and not s.startswith("#") and not s.endswith("/"):
                try:
                    if (src_config / s).is_dir():
                        new_lines.append(ln.rstrip() + "/")
                        changed = True
                        continue
                except Exception:
                    pass
            new_lines.append(ln)

        if not changed:
            return

        new_content = "\n".join(new_lines) + "\n"
        try:
            widget._skip_snapshot = True
            widget.configure(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert("1.0", new_content)
            widget._last = widget.get("1.0", "end-1c")
            widget.edit_modified(False)
        except Exception:
            pass
        finally:
            widget._skip_snapshot = False

        # 恢复清单的读写状态（避免在编辑模式关闭时误保持为可编辑）
        self._update_text_states()

        # 内容已变化，旧的存在性高亮失效
        self._clear_config_status()
        self.save_config()
        self._notify_config_change()

    def _clear_mod_status(self):
        """移除主模组清单上所有存在性闪烁高亮标签。"""
        try:
            self.mod_text.tag_remove("mod_ok", "1.0", tk.END)
            self.mod_text.tag_remove("mod_missing", "1.0", tk.END)
            self.mod_text.tag_remove("mod_duplicate", "1.0", tk.END)
        except Exception:
            pass

    def _clear_config_status(self):
        """移除 config 清单上所有存在性高亮标签。"""
        try:
            self.config_text.tag_remove("cfg_ok", "1.0", tk.END)
            self.config_text.tag_remove("cfg_missing", "1.0", tk.END)
            self.config_text.tag_remove("cfg_duplicate", "1.0", tk.END)
            self._config_status_applied = False
        except Exception:
            pass

    def check_configlist_existence(self):
        """检查 config 清单中每个条目（相对 config 目录的路径）在源目录是否存在，
        并逐行用颜色高亮：存在=绿、缺失=红、重复=黄。"""
        now = time.time()
        if now - self.last_check_config_time < 2:
            self.root.bell()
            self.log("⚠️ 请勿频繁操作！请稍后再试。", level="WARNING")
            return
        self.last_check_config_time = now

        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(trp("❌ 源 config 目录不存在：{0}", src_config), level="ERROR")
            return

        # 先按源目录把文件夹条目补上末尾 "/"（便于区分，且与高亮/重复判定一致）
        self._mark_config_folders()

        entries = self._parse_config_lines()
        if not entries:
            self.root.bell()
            self.log("⚠️ 当前 config 清单为空", level="WARNING")
            self._clear_config_status()
            return

        def norm(e):
            return e.replace("\\", "/").lower().rstrip("/")

        counts = Counter(norm(e) for e in entries)

        self._clear_config_status()
        ok = duplicate = missing = 0
        missing_samples = []
        lines = self.config_text.get("1.0", tk.END).splitlines()
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            idx, end = f"{i + 1}.0", f"{i + 1}.end"
            if not (src_config / s).exists():
                self.config_text.tag_add("cfg_missing", idx, end)
                missing += 1
                if len(missing_samples) < 60:
                    missing_samples.append(s)
            elif counts[norm(s)] > 1:
                self.config_text.tag_add("cfg_duplicate", idx, end)
                duplicate += 1
            else:
                self.config_text.tag_add("cfg_ok", idx, end)
                ok += 1
        self._config_status_applied = True

        self.log(trp("📊 config 清单检查结果：总条目 {0}，去重后 {1} 个", len(entries), len(counts)), level="INFO")
        self.log(trp("✅ 存在的条目：{0}", ok), level="SUCCESS")
        if duplicate:
            self.log(trp("⚠️ 重复条目：{0} 行", duplicate), level="WARNING")
        if missing:
            self.log(trp("❌ 缺失的条目：{0}", missing), level="PLAIN")
            self.root.bell()
            self.log("缺失条目：", level="WARNING")
            for m in missing_samples[:50]:
                self.log(f"  - {m}", level="ERROR")
            if missing > 50:
                self.log(trp("  ... 还有 {0} 条未显示", missing - 50), level="WARNING")
        elif not duplicate:
            self.log("✅ 所有 config 条目均存在且无重复。", level="SUCCESS")

        # 主界面临时闪烁高亮：1秒后自动恢复（清除颜色，回到普通文本）
        _flash_after = getattr(self, "_config_flash_after", None)
        if _flash_after:
            try:
                self.root.after_cancel(_flash_after)
            except Exception:
                pass
        self._config_flash_after = self.root.after(1000, self._clear_config_status)

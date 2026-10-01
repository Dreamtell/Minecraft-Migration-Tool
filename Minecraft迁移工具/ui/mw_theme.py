# ui/mw_theme.py
"""深浅主题：切换动画、重绘、图标。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import tkinter as tk
from tkinter import ttk
from ui.dialogs import update_mod_detail_theme
from ui.mw_common import _ICON_SIZE
from utils.helpers import circular_reveal, lighten_color, make_theme_icon
from utils.theme import DARK_THEME, LIGHT_THEME, apply_theme_to_widget_tree


class ThemeMixin:
    """深浅主题：切换动画、重绘、图标。"""

    def apply_theme(self):
        self.root.configure(bg=self.theme["bg"])
        # 配置 ttk 样式
        style = ttk.Style()
        # 使用 'clam' 主题以支持更多自定义
        if style.theme_use() != 'clam':
            try:
                style.theme_use('clam')
            except:
                pass

        # Treeview 样式（放大查看的列表用大一号字体，更清晰）
        style.configure(
            "Treeview",
            background=self.theme["ttk_bg"],
            foreground=self.theme["ttk_fg"],
            fieldbackground=self.theme["ttk_bg"],
            selectbackground=self.theme["ttk_select_bg"],
            selectforeground=self.theme["ttk_select_fg"],
            borderwidth=0,
            font=("微软雅黑", 11)
        )
        style.map(
            "Treeview",
            background=[('selected', self.theme["ttk_select_bg"])],
            foreground=[('selected', self.theme["ttk_select_fg"])]
        )

        # Treeview.Heading（列标题）
        style.configure(
            "Treeview.Heading",
            background=self.theme["button_bg"],
            foreground=self.theme["fg"],
            relief="flat",
            font=("微软雅黑", 10, "bold")
        )
        # 列标题能点（点一下排序），所以鼠标悬停也给同样的高亮反馈
        style.map(
            "Treeview.Heading",
            background=[("active", lighten_color(self.theme["button_bg"]))]
        )

        # Progressbar 样式
        style.configure(
            "Horizontal.TProgressbar",
            background=self.theme["ttk_progress_bg"],
            troughcolor=self.theme["ttk_trough_bg"],
            bordercolor=self.theme["bg"],
            lightcolor=self.theme["ttk_progress_bg"],
            darkcolor=self.theme["ttk_progress_bg"]
        )

        # Combobox 样式
        style.configure(
            "TCombobox",
            fieldbackground=self.theme["ttk_field_bg"],
            background=self.theme["ttk_bg"],
            foreground=self.theme["ttk_fg"],
            arrowcolor=self.theme["ttk_fg"]
        )
        style.map(
            "TCombobox",
            fieldbackground=[('readonly', self.theme["ttk_field_bg"])],
            background=[('readonly', self.theme["ttk_bg"])],
            foreground=[('readonly', self.theme["ttk_fg"])]
        )

        # Scrollbar 样式
        style.configure(
            "Vertical.TScrollbar",
            background=self.theme["button_bg"],
            troughcolor=self.theme["bg"],
            arrowcolor=self.theme["fg"],
            bordercolor=self.theme["bg"]
        )
        style.configure(
            "Horizontal.TScrollbar",
            background=self.theme["button_bg"],
            troughcolor=self.theme["bg"],
            arrowcolor=self.theme["fg"],
            bordercolor=self.theme["bg"]
        )
        apply_theme_to_widget_tree(self.root, self.theme)

        # 同步"放大查看"结果表的配色
        alive_tables = []
        for tv in getattr(self, '_big_view_tables', []):
            try:
                if tv.winfo_exists():
                    tv.apply_theme(self.theme)
                    alive_tables.append(tv)
            except Exception:
                pass
        self._big_view_tables = alive_tables

        # 迁移历史窗口的自绘表格（VirtualTable 不是普通控件，得显式喂新主题）
        _ht = getattr(self, "_hist_table", None)
        if _ht is not None:
            try:
                if _ht.winfo_exists():
                    _ht.apply_theme(self.theme)
                else:
                    self._hist_table = None
            except Exception:
                self._hist_table = None

        # 放大查看的"卡片视图"也一样要跟着主题换色（自绘控件，得显式调用）
        alive_cards = []
        for cdl in getattr(self, '_big_view_cards', []):
            try:
                if cdl.winfo_exists():
                    cdl.apply_theme(self.theme)
                    alive_cards.append(cdl)
            except Exception:
                pass
        self._big_view_cards = alive_cards

        # 同步"放大查看"窗口的整体配色（背景/文字/输入框等，渐变按钮不受影响）
        alive_big = []
        for bww in getattr(self, '_big_view_windows', []):
            try:
                if bww.winfo_exists():
                    apply_theme_to_widget_tree(bww, self.theme)
                    alive_big.append(bww)
                    # 汇总标签是自己管颜色（_keep_fg，按"有选中/有缺失"动态变色），
                    # 主题切换时得让它们按新主题重算一遍，不能只喂一个固定色
                    updater = getattr(bww, "_update_summary_ui", None)
                    if updater is not None:
                        try:
                            updater()
                        except Exception:
                            pass
                    else:
                        for attr, key in (("_sel_lbl", "muted_fg"),):
                            lbl = getattr(bww, attr, None)
                            if lbl is not None:
                                try:
                                    lbl.config(fg=self.theme.get(key, self.theme["fg"]))
                                except Exception:
                                    pass
                    # 顺带同步该放大查看窗口打开的模组详情窗口
                    for detail_win in getattr(bww, '_mod_detail_windows', []):
                        if detail_win.winfo_exists():
                            update_mod_detail_theme(detail_win, self.theme)
            except Exception:
                pass
        self._big_view_windows = alive_big

        # 同步 PySide6 窗口：它们现在跑在**独立子进程**里，只能发命令过去
        # （以前这里是遍历进程内的 `_qt_views` 直接 set_theme —— Qt 搬进子进程之后
        #   那个列表永远是空的，表现就是"切了主题，Qt 窗口纹丝不动"）
        self._send_qt_host_command("theme", 附加={"theme": dict(self.theme)})
        # 兜底：万一某个 Qt 窗口还开在本进程里（老路径/降级），照样喂主题
        alive_qt = []
        for qv in getattr(self, '_qt_views', []):
            try:
                if qv.is_alive():
                    qv.set_theme(self.theme)
                    alive_qt.append(qv)
            except Exception:
                pass
        self._qt_views = alive_qt

        # 同步主 config 清单的存在性状态标签颜色（跟随主题切换）
        if hasattr(self, 'config_text'):
            try:
                self.config_text.tag_configure(
                    "cfg_ok", background=self.theme.get("success_bg", "#d4edda"),
                    foreground=self.theme.get("success_fg", "#000000"))
                self.config_text.tag_configure(
                    "cfg_missing", background=self.theme.get("danger_bg", "#ffc7c7"),
                    foreground=self.theme.get("danger_fg", "#8b0000"))
                self.config_text.tag_configure(
                    "cfg_duplicate", background=self.theme.get("warn_bg", "#ffeaa7"),
                    foreground=self.theme.get("warn_fg", "#000000"))
            except Exception:
                pass

        # 同步主模组清单的存在性闪烁标签颜色（跟随主题切换）
        if hasattr(self, 'mod_text'):
            try:
                self.mod_text.tag_configure(
                    "mod_ok", background=self.theme.get("success_bg", "#d4edda"),
                    foreground=self.theme.get("success_fg", "#000000"))
                self.mod_text.tag_configure(
                    "mod_missing", background=self.theme.get("danger_bg", "#ffc7c7"),
                    foreground=self.theme.get("danger_fg", "#8b0000"))
                self.mod_text.tag_configure(
                    "mod_duplicate", background=self.theme.get("warn_bg", "#ffeaa7"),
                    foreground=self.theme.get("warn_fg", "#000000"))
                # "本次新添加的模组"黄色高亮：以前只在建界面时配过一次，切主题
                # 从不重配，深色主题下会一直留着浅黄底
                self.mod_text.tag_configure(
                    "new", background=self.theme.get("warn_bg", "#ffeaa7"),
                    foreground=self.theme.get("warn_fg", "#000000"))
            except Exception:
                pass

        if hasattr(self, 'bottom_frame'):
            self.bottom_frame.configure(bg=self.theme["bottom_bg"])
        if hasattr(self, 'warning_frame'):
            self.warning_frame.configure(bg=self.theme["warning_bg"])
            self.warning_label.configure(bg=self.theme["warning_bg"],
                                         fg=self.theme["warning_fg"])
        if hasattr(self, 'log_text'):
            self.log_text.configure(bg=self.theme["log_bg"], fg=self.theme["log_fg"])
            try:
                self._configure_log_colors(self.log_text)
            except Exception:
                pass
        # 圆角文本框的填充/描边跟着主题重画（模组清单 / config 清单 / 其它文件 / 执行日志 / 日志放大查看）
        for _name in ("mod_text_box", "config_text_box", "extra_text_box",
                      "log_text_box", "_log_big_box"):
            _box = getattr(self, _name, None)
            if _box is not None:
                try:
                    _box.refresh()
                except Exception:
                    pass
        # 清单下面那条液态进度条也要跟着换色
        for _name in ("mod_progress", "config_progress", "extra_progress"):
            _bar = getattr(self, _name, None)
            if _bar is not None:
                try:
                    _bar.set_theme(self.theme)
                except Exception:
                    pass
        if hasattr(self, 'log_hint_label'):
            try:
                self.log_hint_label.configure(
                    bg=self.theme["bg"],
                    fg=self.theme.get("muted_fg", self.theme["fg"]))
            except Exception:
                pass
        # 清单里"出错标红"的配色也跟着主题走
        for _框 in (getattr(self, "mod_text", None), getattr(self, "config_text", None),
                   getattr(self, "extra_text", None)):
            if _框 is None:
                continue
            try:
                _框.tag_configure("migrate_fail",
                                  background=self.theme.get("danger_bg", "#ffc7c7"),
                                  foreground=self.theme.get("danger_fg", "#8b0000"))
            except Exception:
                pass
        # 清单区的标签页（自绘圆角药丸）也要跟着换色
        _tabs = getattr(self, "list_tabs", None)
        if _tabs is not None:
            try:
                _tabs.set_theme(self.theme)
            except Exception:
                pass
        if hasattr(self, 'source_status'):
            self.source_status.configure(bg=self.theme["bg"])
        if hasattr(self, 'target_status'):
            self.target_status.configure(bg=self.theme["bg"])
        if hasattr(self, 'world_status'):
            self.world_status.configure(bg=self.theme["bg"])
        # 状态标签的语义色立刻换成本主题的（绿/红/灰），不等重新校验——
        # 校验要扫实例目录，几百毫秒里旧颜色会一直挂着，看着就是"闪一下"
        self._apply_status_semantic_colors()
        # （迁移方向箭头现在是"⬇ 新版整合包"标题里的一个字符，没有独立控件要刷色）

        # 主题按钮上的图标跟着主题走（深色时显示太阳 = 点它回浅色，反之给月亮）
        if hasattr(self, 'theme_btn'):
            try:
                self.theme_btn.set_icon(self._theme_icon())
            except Exception:
                pass
        # 设置窗开着的话，里面的主题单选框也同步过来
        try:
            if getattr(self, "settings_theme_var", None) is not None:
                self.settings_theme_var.set(self.current_theme)
            self._theme_settings_tree()
        except Exception:
            pass
        # 这些区域用的是专用配色，通用刷新会把它们刷成普通背景，这里逐个补回来
        if hasattr(self, 'opt_frame'):
            self.opt_frame.configure(bg=self.theme["bg"])
        for _名字 in ("dry_run_sw", "overwrite_sw", "extra_conflict_seg"):
            _sw = getattr(self, _名字, None)
            if _sw is not None:
                try:
                    _sw.set_theme(self.theme)
                except Exception:
                    pass
        # 设置里那批自绘选项控件（分段选择 / 选择卡片 / 紧凑开关）也要跟着换肤
        for _列表 in ("_settings_segs", "_settings_cards", "_settings_sws"):
            for _sw in (getattr(self, _列表, None) or []):
                try:
                    _sw.set_theme(self.theme)
                except Exception:
                    pass
        if hasattr(self, 'edit_switch'):
            try:
                self.edit_switch.set_theme(self.theme)     # 开关卡片是自绘的，重出图
            except Exception:
                pass
        self._check_overflow()

        # 同步"执行日志放大查看"窗口的配色与主题
        try:
            bv = getattr(self, '_log_big_view', None)
            if bv is not None and bv.winfo_exists():
                bv.configure(bg=self.theme["bg"])
                tb = getattr(self, '_log_big_toolbar', None)
                if tb is not None and tb.winfo_exists():
                    tb.configure(bg=self.theme["bg"])
                cl = getattr(self, '_log_big_count', None)
                if cl is not None and cl.winfo_exists():
                    cl.configure(bg=self.theme["bg"], fg=self.theme["muted_fg"])
                bt = getattr(self, '_log_big_text', None)
                if bt is not None and bt.winfo_exists():
                    bt.configure(bg=self.theme["log_bg"], fg=self.theme["log_fg"])
                    self._configure_log_colors(bt)
        except Exception:
            pass

        # 兜底：其余已打开的弹窗（变更日志、迁移历史等）也一并跟随主题，
        # 避免出现"主界面变了、某个子窗口还是旧配色"。
        # 跳过主题过渡用的覆盖层，它的内容是一张截图，不该被重新配色。
        for child in self.root.winfo_children():
            try:
                if getattr(child, "_is_theme_overlay", False):
                    continue
                if isinstance(child, tk.Toplevel) and child.winfo_exists():
                    apply_theme_to_widget_tree(child, self.theme)
            except Exception:
                pass

        # 把重绘在"覆盖层还盖着"的时候一次性冲掉。否则这些彩色文字（日志分类色、
        # 清单高亮）的重新着色会被推迟到圆形揭示把它们露出来的那一刻，用户就会
        # 看到"新底色 + 旧字色"闪一下——因为此刻窗口被截图覆盖层挡着，冲掉是看不见的。
        try:
            self.root.update_idletasks()
        except Exception:
            pass

        # 原生外观（暗色标题栏 + 圆角）必须放在**最后**：DWM 属性一改，Windows 会
        # 立刻强制窗口重绘一次；如果这时彩色 tag 还没重配，屏幕上就会闪过
        # "新底色 + 旧字色"的一帧（日志区的彩色行最明显）。放到全部配色都刷完之后
        # 再动窗口样式，那一次重绘就已经是新配色了。
        self._style_all_windows()

    def toggle_theme(self, from_widget=None):
        """切换主题：先做一个从触发点向外扩散的圆形过渡，动画结束再真正换配色。"""
        if getattr(self, "_theme_animating", False):
            return                      # 动画进行中，忽略重复点击
        new_name = "dark" if self.current_theme == "light" else "light"
        new_theme = DARK_THEME if new_name == "dark" else LIGHT_THEME

        def do_switch():
            self.current_theme = new_name
            self.theme = new_theme
            self.apply_theme()
            self.on_path_change()
            self.save_config()
            self.log(f"主题已切换为{'深色' if new_name == 'dark' else '浅色'}模式",
                     level="SUCCESS", save=False)
            # 更新已打开的差异窗口（Qt 版是自绘控件，得显式喂新主题）
            qt_diff = getattr(self, "diff_qt", None)
            if qt_diff is not None and qt_diff.is_alive():
                try:
                    qt_diff.apply_theme(self.theme)
                except Exception:
                    pass
            if hasattr(self, 'diff_window') and self.diff_window is not None:
                if self.diff_window.winfo_exists():
                    from ui.diff_window import update_diff_theme
                    update_diff_theme(self.diff_window, self.theme, self.current_theme)

        def done():
            try:
                do_switch()
            finally:
                self._theme_animating = False

        # 扩散圆心：优先用触发它的按钮，其次鼠标位置，最后窗口中心
        cx = cy = None
        try:
            src = from_widget if from_widget is not None else getattr(self, "theme_btn", None)
            if src is not None and src.winfo_exists():
                cx = src.winfo_rootx() + src.winfo_width() // 2
                cy = src.winfo_rooty() + src.winfo_height() // 2
        except Exception:
            cx = cy = None
        if cx is None or cy is None:
            try:
                cx, cy = self.root.winfo_pointerx(), self.root.winfo_pointery()
            except Exception:
                cx = self.root.winfo_rootx() + self.root.winfo_width() // 2
                cy = self.root.winfo_rooty() + self.root.winfo_height() // 2

        self._theme_animating = True
        # 覆盖层把旧界面盖住之后，才真正切换主题（详见 circular_reveal 的说明）
        circular_reveal(self.root, cx, cy, on_switch=do_switch, on_done=done)

    # ---------- 工具函数 ----------
    # ---------- 设置 ----------
    def _theme_icon(self):
        """主题按钮上的图标：显示"点了会变成什么"——浅色时给月亮，深色时给太阳。

        月亮/太阳都是自己用 Pillow 画的（见 helpers.make_theme_icon）：☀️ 这种 emoji
        在 15pt 下就是一团圆点，看不出是太阳。
        """
        kind = "sun" if self.current_theme == "dark" else "moon"
        return make_theme_icon(kind, size=_ICON_SIZE)

    def choose_theme(self, name):
        """从设置窗里指定主题（只有浅/深两种，所以和目标不同就等于切换）。"""
        if name != self.current_theme:
            self.toggle_theme(from_widget=getattr(self, "theme_btn", None))

    def _persistable(self, level, message):
        """判断该条记录是否值得写入日志文件：关键等级始终保存，INFO 过滤明显无用的提示。"""
        if level in ("ERROR", "WARNING", "SUCCESS", "SIMULATE"):
            return True
        return not any(t in message for t in self._LOG_TRIVIAL)

    # ------------------------------------------------------------ 文字动效
    # Tk 的 Text 没有"逐行透明度"，所以淡入淡出只能靠把 foreground 从底色插值到
    # 目标色（纯色背景下看着就是淡入/淡出；日志和清单都是纯色背景）。
    @staticmethod
    def _lerp_color(c0, c1, t):
        try:
            a = [int(c0[i:i + 2], 16) for i in (1, 3, 5)]
            b = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
            return "#%02x%02x%02x" % tuple(
                max(0, min(255, int(a[i] + (b[i] - a[i]) * t))) for i in range(3))
        except Exception:
            return c1

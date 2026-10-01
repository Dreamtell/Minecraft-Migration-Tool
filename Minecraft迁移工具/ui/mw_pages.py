# ui/mw_pages.py
"""界面搭建：主框架、三个清单页、底栏、日志区、窗口样式。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import tkinter as tk
from tkinter import messagebox
from ui.mw_common import _ICON_BTN, _grad_width
from ui.rounded_tabs import RoundedTabs
from utils.helpers import (
    LiquidProgress, RoundedTextArea, SegmentedControl, SmoothScroller, SwitchRow,
    bind_text_scroll, create_gradient_button, is_dark_theme, style_window,
)


class PagesMixin:
    """界面搭建：主框架、三个清单页、底栏、日志区、窗口样式。"""

    def open_link(self, url):
        """打开外部链接（官网 / GitHub 仓库）。"""
        try:
            import webbrowser
            webbrowser.open_new_tab(url)
            self.log(f"🔗 已打开链接：{url}", level="INFO", save=False)
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开链接：{e}", parent=self.settings_win)

    def create_tooltip(self, widget, text):
        """给控件挂悬停提示。

        text 既可以是固定字符串，也可以是"每次悬停现取"的函数 —— 状态标签的内容
        是会变的（✅ / ⚠️ / ❌），用函数取才不会一直停在绑定时的那一句上。
        """
        def enter(event):
            try:
                msg = text() if callable(text) else text
            except Exception:
                msg = ""
            tip = getattr(widget, "_tooltip", None)
            if not msg:
                self._hide_tooltip(widget)
                return
            # 同一个控件重复 Enter（Tk 偶尔会补发）：只刷内容和位置，别把窗口拆了重建，
            # 否则提示会闪一下
            if tip is not None and tip.winfo_exists():
                try:
                    kids = tip.winfo_children()
                    if kids:
                        kids[0].config(text=msg)
                    tip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 18}")
                    return
                except Exception:
                    pass
            self._hide_tooltip()        # 从别的控件直接滑过来时先收掉上一个，别叠窗
            self.tooltip = tk.Toplevel(widget)
            self.tooltip.wm_overrideredirect(True)
            # 落在指针右下：贴太近会把被解释的那个图标（就十几个像素大）盖住
            self.tooltip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 18}")
            label = tk.Label(self.tooltip, text=msg,
                             background=self.theme["tooltip_bg"], fg=self.theme["label_fg"], relief="solid",
                             borderwidth=1, font=("微软雅黑", 9),
                             justify="left", wraplength=420)
            label.pack()
            widget._tooltip = self.tooltip
            self._tip_owners = [widget]

        def leave(event):
            self._hide_tooltip(widget)

        # 必须 add="+"：tkinter 的 bind 默认是覆盖，直接绑会把按钮自己那套
        # 悬停高亮顶掉——之前"从变更日志导入""← 使用新版路径填充"这两个带提示的
        # 按钮鼠标放上去毫无反应，就是被这里吃掉的。
        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")

    def _hide_tooltip(self, widget=None):
        """收起悬停提示；不给 widget 就收掉当前挂着的那一个。"""
        owners = [widget] if widget is not None else list(getattr(self, "_tip_owners", ()))
        for w in owners:
            tip = getattr(w, "_tooltip", None)
            if tip is not None:
                try:
                    tip.destroy()
                except Exception:
                    pass
                w._tooltip = None
        left = [w for w in getattr(self, "_tip_owners", ()) if w not in owners]
        self._tip_owners = left
        self.tooltip = getattr(left[-1], "_tooltip", None) if left else None

    # ---------- 界面构建（由于太长，拆分为多个辅助方法） ----------
    def create_widgets(self):
        # 顶部栏：两个正方形图标按钮。设置在最右，主题切换在它左边（都用图标，不放文字）
        top_bar = tk.Frame(self.root)
        top_bar.pack(fill="x", padx=10, pady=5)
        self.settings_btn = create_gradient_button(
            top_bar, "⚙", self.open_settings,
            colors=("#546e7a", "#78909c"),
            width=_ICON_BTN, height=_ICON_BTN, font=("微软雅黑", 15))
        self.settings_btn.pack(side="right", padx=(6, 0))
        self.theme_btn = create_gradient_button(
            top_bar, "", self.toggle_theme,
            colors=("#8e24aa", "#ab47bc"),
            width=_ICON_BTN, height=_ICON_BTN, font=("微软雅黑", 15))
        self.theme_btn.set_icon(self._theme_icon())
        self.theme_btn.pack(side="right", padx=5)
        self.create_tooltip(self.theme_btn, "切换浅色 / 深色主题")
        self.create_tooltip(self.settings_btn, "设置")
        # 这两个也登记进「界面按钮」表（分组：主界面右上角），显示/隐藏与顺序跟着设置走
        self._btn_widgets["settings_btn"] = self.settings_btn
        self._btn_widgets["theme_btn"] = self.theme_btn
        self._stage()

        # 警告横幅
        self.warning_frame = tk.Frame(self.root, relief=tk.RIDGE, bd=2)
        self.warning_frame.pack(fill="x", padx=10, pady=(5, 0))
        self.warning_label = tk.Label(self.warning_frame,
                                      text="⚠️ 本工具完全免费，请勿上当受骗！如遇收费行为，请立即举报。⚠️",
                                      font=("微软雅黑", 10, "bold"))
        self.warning_label.pack(pady=5)
        self._stage()

        # ---- 路径选择 ----
        self._create_path_widgets()
        self._stage()               # 每建完一块就让出一帧，闪屏动画才不会被卡死
        # ---- 清单区（三个清单：模组 / config / 其它文件，共用一个区）----
        self._create_modlist_widgets()
        self._stage()
        # ---- 底部按钮 ----
        self._create_bottom_widgets()
        self._stage()
        # ---- 日志 ----
        self._create_log_widgets()
        self._stage()

        # 绑定事件
        self.source_path.trace_add("write", self.on_path_change)
        self.target_path.trace_add("write", self.on_path_change)
        self.world_name.trace_add("write", lambda *args: self.save_config())
        # 点窗口空白处 = 退出主界面编辑模式（不用专门去够那个开关）
        self.root.bind("<Button-1>", self._on_blank_click, add="+")
        # 底部声明
        self.bottom_frame = tk.Frame(self.root)
        self.bottom_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(self.bottom_frame, text="本工具完全免费，仅供个人学习交流使用。严禁倒卖或用于商业目的。",
                 font=("微软雅黑", 8)).pack()

        # 全部按钮建完，最后按配置摆一遍（显示/隐藏 + 自定义顺序）
        self._apply_button_layout()

    def _create_modlist_widgets(self):
        """清单区：三个清单**共用一个区**（标签页），不再各占一大块地方。

        以前是"模组清单"和"config 清单"上下两块 LabelFrame、都被 expand 拉到 ~240px；
        再加一个"其它文件"就会把窗口顶到屏幕外（1080 的窗口只剩 400px 余量）。
        现在三页共用一块：净增高 0，标签上还能直接看到每页多少条。
        """
        area = tk.LabelFrame(self.root, text="清单（三个页共用一个区，点标签切换）",
                             padx=5, pady=5)
        area.pack(fill="both", expand=True, padx=10, pady=5)
        self.list_area = area

        # 编辑模式对三个清单都生效，所以这一行放在页外面（公共一行）。
        # 做成"开关卡片"（自绘：圆角底 + iOS 那种开关）：开启时整条染成强调色、
        # 说明文字换成警示语气，比原来那个橙色方块勾选框干净。
        self.edit_switch = SwitchRow(
            area, self.theme, title="主界面编辑",
            desc="直接改动清单文字 · 谨慎使用",
            command=self.on_edit_switch)
        self.edit_switch.pack(fill="x", padx=5, pady=(2, 4))
        self.edit_switch.set(self.edit_mode.get())
        self.edit_toolbar = self.edit_switch      # 兼容旧引用（主题同步等）
        self._stage()

        self.list_tabs = RoundedTabs(area, self.theme)
        self.list_tabs.pack(fill="both", expand=True)
        page_mod = self.list_tabs.page("🧩 模组清单")
        page_cfg = self.list_tabs.page("⚙️ config 清单")
        page_extra = self.list_tabs.page("📦 其它文件")
        self._create_modlist_page(page_mod)
        self._create_config_page(page_cfg)
        self._create_extra_page(page_extra)
        # 出错条目在清单里标红（迁移日志报错时自动打上；配置也放这儿，三个页共用）
        for _框 in (self.mod_text, self.config_text, self.extra_text):
            try:
                _框.tag_configure("migrate_fail",
                                  background=self.theme.get("danger_bg", "#ffc7c7"),
                                  foreground=self.theme.get("danger_fg", "#8b0000"))
            except Exception:
                pass
        self._refresh_list_badges()

    def _create_modlist_page(self, parent):
        """模组清单页：每行一个 .jar 文件名。"""
        tk.Label(parent,
                 text=("每行一个 .jar 文件名（可从变更日志导入、点「添加模组」选，或直接把 jar 拖进来）。"
                       "同名匹配不区分大小写，改过名的也能对上。"),
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left").pack(anchor="w", padx=5, pady=(2, 0))
        self.mod_text_box = RoundedTextArea(parent, self.theme, height=8,
                                            wrap=tk.NONE, undo=True,
                                            font=("微软雅黑 Light", 10))
        self.mod_text_box.pack(fill="both", expand=True, padx=5, pady=5)
        self.mod_text = self.mod_text_box.text
        self._smooth(self.mod_text)
        # 文本框下面一条液态进度条：滑到哪儿了一眼能看到（跟着这个框的滚动走）
        self.mod_progress = LiquidProgress(parent, self.theme)
        self.mod_progress.pack(fill="x", padx=5, pady=(0, 4))
        bind_text_scroll(self.mod_text, self.mod_progress.set_fraction)
        self.mod_text.bind("<Control-z>", lambda e: self._safe_undo(self.mod_text))
        self.mod_text.bind("<Control-y>", lambda e: self._safe_redo(self.mod_text))
        # 内容一变就（防抖后）推给开着的 Qt 放大查看 —— 手打也能实时同步过去
        self.mod_text.bind("<<Modified>>",
                           lambda e: self._on_text_modified(self.mod_text), add="+")
        # 本会话新添加的模组（文件名小写），主清单用黄色高亮提示
        self._new_mod_keys = set()
        self.mod_text.tag_configure(
            "new", background=self.theme.get("warn_bg", "#ffeaa7"),
            foreground=self.theme.get("warn_fg", "#000000"))
        # 存在性检查的临时闪烁标签（存在=绿 / 缺失=红 / 重复=黄），1秒后自动恢复
        self.mod_text.tag_configure(
            "mod_ok", background=self.theme.get("success_bg", "#d4edda"),
            foreground=self.theme.get("success_fg", "#000000"))
        self.mod_text.tag_configure(
            "mod_missing", background=self.theme.get("danger_bg", "#ffc7c7"),
            foreground=self.theme.get("danger_fg", "#8b0000"))
        self.mod_text.tag_configure(
            "mod_duplicate", background=self.theme.get("warn_bg", "#ffeaa7"),
            foreground=self.theme.get("warn_fg", "#000000"))
        # 支持从资源管理器拖拽 .jar 到清单
        try:
            from tkinterdnd2 import DND_FILES
            self.mod_text.drop_target_register(DND_FILES)
            self.mod_text.dnd_bind('<<Drop>>', self._on_mod_drop)
        except Exception:
            pass
        self._bind_badge_refresh(self.mod_text)
        self._stage()               # ScrolledText 建一个要一百来毫秒，建完先让一帧

        btn_frame = tk.Frame(parent)
        btn_frame.pack(fill="x", pady=5)

        # 统一渐变按钮（同高度/字体，语义配色，宽度按文字自适应）
        gw = _grad_width      # 用缓存了 Font 的量宽函数，别再就地 new 一个 Font

        self.btn_changelog = create_gradient_button(
            btn_frame, "📥 从变更日志导入（含Updated）", self.import_from_changelog,
            colors=("#00acc1", "#26c6da"),
            width=gw("📥 从变更日志导入（含Updated）"), height=30, font=("微软雅黑", 9, "bold"))
        self.btn_changelog.pack(side="left", padx=5)
        self._btn_widgets["changelog"] = self.btn_changelog
        self.create_tooltip(self.btn_changelog, "你需要提供的是“崩溃助手”模组给予的mod变更列表")

        self.scan_btn = create_gradient_button(
            btn_frame, "🔍 扫描模组差异", self.action_scan_mod_diff,
            colors=("#00bcd4", "#3f51b5"),
            width=gw("🔍 扫描模组差异"), height=30, font=("微软雅黑", 9, "bold"))
        self.mod_magnify_btn = create_gradient_button(
            btn_frame, "📂 放大查看", self.open_mod_big_view,
            colors=("#607d8b", "#90a4ae"),
            width=gw("📂 放大查看"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_mods_btn = create_gradient_button(
            btn_frame, "➕ 添加模组", self.add_mods,
            colors=("#00c853", "#00e676"),
            width=gw("➕ 添加模组"), height=30, font=("微软雅黑", 9, "bold"))
        self._stage()               # 这批有 6 个按钮，中间让一次
        self.clear_mods_btn = create_gradient_button(
            btn_frame, "🗑️ 清空清单", self.clear_mod_list,
            colors=("#e53935", "#c62828"),
            width=gw("🗑️ 清空清单"), height=30, font=("微软雅黑", 9, "bold"))
        self.check_mods_btn = create_gradient_button(
            btn_frame, "🔎 检查清单模组是否存在（源目录）", self.check_modlist_existence,
            colors=("#fb8c00", "#ffb74d"),
            width=gw("🔎 检查清单模组是否存在（源目录）"), height=30, font=("微软雅黑", 9, "bold"))
        self._stage()

        self.mod_magnify_btn.pack(side="left", padx=5)
        self.add_mods_btn.pack(side="left", padx=5)
        self.scan_btn.pack(side="left", padx=5)
        self._stage()
        self.clear_mods_btn.pack(side="left", padx=5)
        self.check_mods_btn.pack(side="left", padx=5)
        self._btn_widgets.update({
            "mod_magnify": self.mod_magnify_btn,
            "add_mods": self.add_mods_btn,
            "scan_diff": self.scan_btn,
            "clear_mods": self.clear_mods_btn,
            "check_mods": self.check_mods_btn,
        })
        self._stage()

    def _create_config_page(self, parent):
        """config 清单页：每行一个相对路径，相对源实例的 config 目录。"""
        tk.Label(parent,
                 text="每行一个相对路径，「相对源实例的 config 目录」（例：jei/jei.toml、sodium-options.json）。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left").pack(anchor="w", padx=5, pady=(2, 0))
        warning_config = tk.Label(parent,
                                  text="⚠️ 注意：复制将直接覆盖目标 config 中的同名文件/文件夹，请谨慎操作！",
                                  fg=self.theme["fail_fg"], font=("微软雅黑", 9, "bold"))
        warning_config.pack(anchor="w", padx=5, pady=2)

        self.config_text_box = RoundedTextArea(parent, self.theme, height=6,
                                               wrap=tk.NONE, undo=True,
                                               font=("微软雅黑 Light", 10))
        self.config_text_box.pack(fill="both", expand=True, padx=5, pady=5)
        self.config_text = self.config_text_box.text
        self._smooth(self.config_text)
        self.config_progress = LiquidProgress(parent, self.theme)
        self.config_progress.pack(fill="x", padx=5, pady=(0, 4))
        bind_text_scroll(self.config_text, self.config_progress.set_fraction)
        self.config_text.bind("<Control-z>",
                              lambda e: self._safe_undo(self.config_text))
        self.config_text.bind("<Control-y>",
                              lambda e: self._safe_redo(self.config_text))
        self.config_text.bind("<<Modified>>",
                              lambda e: self._on_text_modified(self.config_text), add="+")
        # config 条目存在性检查的状态标签（正常=绿 / 缺失=红 / 重复=黄）
        self.config_text.tag_configure(
            "cfg_ok", background=self.theme.get("success_bg", "#d4edda"),
            foreground=self.theme.get("success_fg", "#000000"))
        self.config_text.tag_configure(
            "cfg_missing", background=self.theme.get("danger_bg", "#ffc7c7"),
            foreground=self.theme.get("danger_fg", "#8b0000"))
        self.config_text.tag_configure(
            "cfg_duplicate", background=self.theme.get("warn_bg", "#ffeaa7"),
            foreground=self.theme.get("warn_fg", "#000000"))
        # 支持从资源管理器拖拽文件/文件夹到 config 清单
        try:
            from tkinterdnd2 import DND_FILES
            self.config_text.drop_target_register(DND_FILES)
            self.config_text.dnd_bind('<<Drop>>', self._on_config_drop)
        except Exception:
            pass
        self._stage()

        btn_config_frame = tk.Frame(parent)
        btn_config_frame.pack(fill="x", pady=5)

        self.config_magnify_btn = create_gradient_button(
            btn_config_frame, "📂 放大查看", self.open_config_big_view,
            colors=("#607d8b", "#90a4ae"),
            width=_grad_width("📂 放大查看"), height=30, font=("微软雅黑", 9, "bold"))
        self.config_magnify_btn.pack(side="left", padx=5)
        self.add_config_dir_btn = create_gradient_button(
            btn_config_frame, "📁 浏览添加文件夹", self.browse_add_config_entry,
            colors=("#00c853", "#00e676"),
            width=_grad_width("📁 浏览添加文件夹"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_config_dir_btn.pack(side="left", padx=5)
        self.add_config_file_btn = create_gradient_button(
            btn_config_frame, "📄 浏览添加文件", self.browse_add_config_file,
            colors=("#00c853", "#00e676"),
            width=_grad_width("📄 浏览添加文件"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_config_file_btn.pack(side="left", padx=5)
        self.clear_config_btn = create_gradient_button(
            btn_config_frame, "🗑️ 清空 config 清单", self.clear_config,
            colors=("#e53935", "#c62828"),
            width=_grad_width("🗑️ 清空 config 清单"), height=30, font=("微软雅黑", 9, "bold"))
        self.clear_config_btn.pack(side="left", padx=5)
        self.config_check_btn = create_gradient_button(
            btn_config_frame, "🔎 检查 config 是否存在（源目录）", self.check_configlist_existence,
            colors=("#fb8c00", "#ffb74d"),
            width=_grad_width("🔎 检查 config 是否存在（源目录）"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.config_check_btn.pack(side="left", padx=5)
        self._btn_widgets.update({
            "cfg_magnify": self.config_magnify_btn,
            "add_cfg_dir": self.add_config_dir_btn,
            "add_cfg_file": self.add_config_file_btn,
            "clear_cfg": self.clear_config_btn,
            "check_cfg": self.config_check_btn,
        })
        self._create_check_legend(btn_config_frame)
        self._bind_badge_refresh(self.config_text)
        self._stage()

    def _create_extra_page(self, parent):
        """其它文件页：带走 mods / config / saves 之外的东西。

        路径相对**整合包根目录**（不是 config）：options.txt、servers.dat、
        shaderpacks/、resourcepacks/、kubejs/、scripts/ 这类都走这一页。

        **空清单 = 什么都不多带**（包括 options.txt —— 它以前是被无条件复制的，
        现在也得用户自己勾）。
        """
        tk.Label(parent,
                 text=("每行一个路径，「相对整合包根目录」（文件或文件夹；文件夹会递归复制）。\n"
                       "不会自动带任何东西 —— 想要的自己加。\n"
                       "例：options.txt   servers.dat   shaderpacks/   resourcepacks/   kubejs/"),
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left").pack(anchor="w", padx=5, pady=(2, 0))

        row_rule = tk.Frame(parent, bg=self.theme["bg"])
        row_rule.pack(fill="x", padx=5, pady=(4, 2))
        tk.Label(row_rule, text="目标已有同名文件时：", bg=self.theme["bg"],
                 fg=self.theme["fg"], font=("微软雅黑", 9)).pack(side="left",
                                                                padx=(0, 8))
        # 原来是一对 tk.Radiobutton（系统小圆点，跟这套自绘 UI 不搭）；
        # 换成自绘的分段选择：选中的那段用滑动的色块浮起来
        self.extra_conflict_seg = SegmentedControl(
            row_rule, self.theme,
            [("overwrite", "覆盖（先备份）"), ("skip", "跳过（目标保持不变）")],
            command=self._set_extra_conflict, value=self.extra_conflict.get())
        self.extra_conflict_seg.pack(side="left")

        self.extra_text_box = RoundedTextArea(parent, self.theme, height=6,
                                              wrap=tk.NONE, undo=True,
                                              font=("微软雅黑 Light", 10))
        self.extra_text_box.pack(fill="both", expand=True, padx=5, pady=5)
        self.extra_text = self.extra_text_box.text
        self._smooth(self.extra_text)
        self.extra_progress = LiquidProgress(parent, self.theme)
        self.extra_progress.pack(fill="x", padx=5, pady=(0, 4))
        bind_text_scroll(self.extra_text, self.extra_progress.set_fraction)
        self.extra_text.bind("<Control-z>", lambda e: self._safe_undo(self.extra_text))
        self.extra_text.bind("<Control-y>", lambda e: self._safe_redo(self.extra_text))
        # 存在性检查的临时高亮（存在=绿 / 缺失=红 / 重复=黄），1 秒后自动恢复
        self.extra_text.tag_configure(
            "extra_ok", background=self.theme.get("success_bg", "#d4edda"),
            foreground=self.theme.get("success_fg", "#000000"))
        self.extra_text.tag_configure(
            "extra_missing", background=self.theme.get("danger_bg", "#ffc7c7"),
            foreground=self.theme.get("danger_fg", "#8b0000"))
        self.extra_text.tag_configure(
            "extra_duplicate", background=self.theme.get("warn_bg", "#ffeaa7"),
            foreground=self.theme.get("warn_fg", "#000000"))
        # 从资源管理器拖入文件/文件夹 → 自动算成相对整合包根目录的条目
        try:
            from tkinterdnd2 import DND_FILES
            self.extra_text.drop_target_register(DND_FILES)
            self.extra_text.dnd_bind('<<Drop>>', self._on_extra_drop)
        except Exception:
            pass
        self._bind_badge_refresh(self.extra_text)
        self._stage()

        btn_extra_frame = tk.Frame(parent)
        btn_extra_frame.pack(fill="x", pady=5)
        self.add_extra_dir_btn = create_gradient_button(
            btn_extra_frame, "📁 浏览添加文件夹", self.browse_add_extra_entry,
            colors=("#00c853", "#00e676"),
            width=_grad_width("📁 浏览添加文件夹"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_extra_dir_btn.pack(side="left", padx=5)
        self.add_extra_file_btn = create_gradient_button(
            btn_extra_frame, "📄 浏览添加文件", self.browse_add_extra_file,
            colors=("#00c853", "#00e676"),
            width=_grad_width("📄 浏览添加文件"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_extra_file_btn.pack(side="left", padx=5)
        # 一键把最常见的那个加进来：options.txt 以前是**默认复制**的，现在也得用户自己勾，
        # 但没必要让人手打文件名/去文件夹里翻
        self.add_extra_options_btn = create_gradient_button(
            btn_extra_frame, "＋ options.txt", self._quick_add_options_txt,
            colors=("#3949ab", "#5c6bc0"),
            width=_grad_width("＋ options.txt"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.add_extra_options_btn.pack(side="left", padx=5)
        # 常用目录（原来的「默认携带的目录」勾选框已删，统一到这里"主动加进清单"）：
        # 菜单里点一下就写进清单 —— 看得见、能改、能删，备份/回滚也跟着清单走
        self.add_extra_preset_btn = create_gradient_button(
            btn_extra_frame, "＋ 常用目录 ▾", self._open_extra_preset_menu,
            colors=("#5e35b1", "#7e57c2"),
            width=_grad_width("＋ 常用目录 ▾"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.add_extra_preset_btn.pack(side="left", padx=5)
        self.clear_extra_btn = create_gradient_button(
            btn_extra_frame, "🗑️ 清空其它文件清单", self.clear_extra_list,
            colors=("#e53935", "#c62828"),
            width=_grad_width("🗑️ 清空其它文件清单"), height=30, font=("微软雅黑", 9, "bold"))
        self.clear_extra_btn.pack(side="left", padx=5)
        self.extra_check_btn = create_gradient_button(
            btn_extra_frame, "🔎 检查是否存在（源目录）", self.check_extralist_existence,
            colors=("#fb8c00", "#ffb74d"),
            width=_grad_width("🔎 检查是否存在（源目录）"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.extra_check_btn.pack(side="left", padx=5)
        self._btn_widgets.update({
            "add_extra_dir": self.add_extra_dir_btn,
            "add_extra_file": self.add_extra_file_btn,
            "clear_extra": self.clear_extra_btn,
            "check_extra": self.extra_check_btn,
        })
        self._stage()

    def _create_bottom_widgets(self):
        self.opt_frame = tk.Frame(self.root, bg=self.theme["bg"])
        self.opt_frame.pack(fill="x", padx=10, pady=5)
        # 这两个原来也是方框勾选框，跟上面的编辑开关统一成自绘开关（紧凑形态：只有
        # 小开关 + 一行文字，不铺卡片底）。强调色用主题的选中蓝，别用橙 —— 它们不是危险操作。
        self.dry_run_sw = SwitchRow(
            self.opt_frame, self.theme, "模拟运行（仅显示操作）",
            command=self._on_dry_run_switch, compact=True, accent="switch_on")
        self.dry_run_sw.pack(side="left")
        self.overwrite_sw = SwitchRow(
            self.opt_frame, self.theme, "覆盖已存在的模组",
            command=self._on_overwrite_switch, compact=True, accent="switch_on")
        self.overwrite_sw.pack(side="left", padx=(20, 0))
        self.dry_run_sw.set(self.dry_run.get())
        self.overwrite_sw.set(self.overwrite_mods.get())
        self.dry_run_cb = self.dry_run_sw        # 兼容旧引用
        self.overwrite_cb = self.overwrite_sw
        self._stage()

        # 右侧按钮组
        btn_group = tk.Frame(self.opt_frame, bg=self.theme["bg"])
        btn_group.pack(side="right")

        self.start_btn = create_gradient_button(
            parent=btn_group,
            text="🚀 开始迁移",
            command=self.start_migration,
            colors=("#00c853", "#00e676"),
            width=180,
            height=38,
            font=("微软雅黑", 12, "bold")
        )
        self.start_btn.pack(side="right", padx=5)
        self._stage()

        self.rollback_btn = create_gradient_button(
            parent=btn_group,
            text="⚠️ 回滚",
            command=self.action_rollback,
            colors=("#e53935", "#ff7043"),
            width=160,
            height=38,
            font=("微软雅黑", 11, "bold")
        )
        self.rollback_btn.pack(side="right", padx=5)
        self._stage()

        self.history_btn = create_gradient_button(
            parent=btn_group,
            text="📋 查看历史",
            command=self.action_show_history,
            colors=("#00acc1", "#26c6da"),
            width=160,
            height=38,
            font=("微软雅黑", 11, "bold")
        )
        self.history_btn.pack(side="right", padx=5)
        self._btn_widgets.update({
            "start": self.start_btn,
            "rollback": self.rollback_btn,
            "history": self.history_btn,
        })
        self._stage()

    def _create_log_widgets(self):
        frame_log = tk.LabelFrame(self.root, text="执行日志", padx=5, pady=5)
        frame_log.pack(fill="both", expand=True, padx=10, pady=5)

        log_toolbar = tk.Frame(frame_log)
        log_toolbar.pack(fill="x", pady=(0, 5))
        btn_big_log = create_gradient_button(
            log_toolbar, "📂 放大查看", self.open_log_big_view_busy,
            colors=("#607d8b", "#90a4ae"),
            width=_grad_width("📂 放大查看"), height=30, font=("微软雅黑", 9, "bold"))
        btn_big_log.pack(side="left", padx=5)
        self.log_magnify_btn = btn_big_log      # 打开中要改它的文字
        # 提示：日志里出错的行可以直接双击定位过去
        self.log_hint_label = tk.Label(
            log_toolbar, text="💡 双击日志行可定位到清单里的那条",
            bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
            font=("微软雅黑", 8))
        self.log_hint_label.pack(side="left", padx=(10, 0))
        btn_clear_log = create_gradient_button(
            log_toolbar, "🗑️ 清空日志", self.clear_log,
            colors=("#e53935", "#c62828"),
            width=_grad_width("🗑️ 清空日志"), height=30, font=("微软雅黑", 9, "bold"))
        btn_clear_log.pack(side="right", padx=5)
        btn_open_log = create_gradient_button(
            log_toolbar, "📂 打开日志文件夹", self.open_log_folder,
            colors=("#607d8b", "#90a4ae"),
            width=_grad_width("📂 打开日志文件夹"), height=30, font=("微软雅黑", 9, "bold"))
        btn_open_log.pack(side="right", padx=5)
        # 定位错误：一次点击就跳到下一个出错的清单条目（比在日志里找、双击都快）
        self.btn_fail_locate = create_gradient_button(
            log_toolbar, "📍 定位错误", self._goto_next_fail,
            colors=("#e53935", "#ff7043"),
            width=_grad_width("📍 定位错误") + 26, height=30,
            font=("微软雅黑", 9, "bold"))
        self.btn_fail_locate.pack(side="left", padx=(8, 0))
        self._btn_widgets["log_fail"] = self.btn_fail_locate
        self._btn_widgets.update({
            "log_big": btn_big_log,
            "log_open": btn_open_log,
            "log_clear": btn_clear_log,
        })
        self._refresh_fail_button()      # 启动时没有错误 → 一开始就是灰的
        self._stage()               # 下面这个日志文本框也要建一百来毫秒
        # 顶部提示区已移除，执行日志相应加高，占住释放出来的空间
        self.log_text_box = RoundedTextArea(frame_log, self.theme, height=22,
                                            wrap=tk.WORD, state="disabled",
                                            bg=self.theme["log_bg"],
                                            fg=self.theme["log_fg"])
        self.log_text_box.pack(fill="both", expand=True, padx=2, pady=2)
        self.log_text = self.log_text_box.text
        self._bind_log_locate(self.log_text)     # 双击日志行 → 定位到清单条目
        # 试验：日志区平滑滚动。手动往上滚时暂停"自动跟到底"，滚回底部再恢复，
        # 否则日志一边涌入、一边把你拽回底部，根本翻不上去。
        self._log_follow = True
        self._log_scroller = SmoothScroller.for_text(
            self.log_text,
            on_user_scroll=self._on_log_user_scroll,
            on_settle=self._on_log_scroll_settle)
        try:
            self.log_text.vbar.bind(
                "<ButtonRelease-1>", lambda e: self._on_log_scroll_settle())
        except Exception:
            pass
        self._stage()

    # ---------- 原生窗口外观（暗色标题栏 + Win11 圆角）----------
    def _bind_window_styling(self):
        """任何 Toplevel 一被显示出来就给它上原生外观。

        用 <Map> 统一兜住：主界面、设置、历史、放大查看、进度窗、差异窗……
        以及以后新加的窗口都自动生效，不用去每处建窗代码补一行。
        """
        def on_map(event):
            try:
                if isinstance(event.widget, tk.Toplevel):
                    self._style_window(event.widget)
            except Exception:
                pass
        try:
            self.root.bind_all("<Map>", on_map, add="+")
        except Exception:
            pass

    def _style_window(self, win, force=False):
        """给窗口上暗色标题栏（跟随主题）+ 圆角；同一窗口只刷一次。"""
        key = str(win)
        if not force and key in getattr(self, "_styled_windows", set()):
            return
        try:
            if style_window(win, dark=is_dark_theme(self.theme)):
                self._styled_windows.add(key)
        except Exception:
            pass

    def _style_all_windows(self):
        """切主题后把所有已开的窗口重刷一遍（标题栏颜色要跟着变）。"""
        self._styled_windows.clear()
        self._style_window(self.root, force=True)
        try:
            for c in self.root.winfo_children():
                if isinstance(c, tk.Toplevel):
                    self._style_window(c, force=True)
        except Exception:
            pass

    def _create_check_legend(self, parent):
        """在检查按钮旁显示"存在/缺失/重复"三种颜色的图例（紧凑版，随按钮一行）。"""
        try:
            legend = tk.Frame(parent, bg=self.theme.get("labelframe_bg", self.theme["bg"]))
            legend.pack(side="left", padx=4)
            self._check_legend = legend      # 重排 config 按钮时要用它当插入锚点

            def chip(text, bg, fg):
                lb = tk.Label(legend, text=text, bg=bg, fg=fg,
                              font=("微软雅黑", 9, "bold"), padx=2, pady=1)
                lb.pack(side="left", padx=1)

            chip("✅ 存在", self.theme.get("success_bg", "#c6e0b4"),
                 self.theme.get("success_fg", "#000000"))
            chip("❌ 缺失", self.theme.get("danger_bg", "#ffc7c7"),
                 self.theme.get("danger_fg", "#8b0000"))
            chip("⚠️ 重复", self.theme.get("warn_bg", "#ffeaa7"),
                 self.theme.get("warn_fg", "#000000"))
        except Exception:
            pass

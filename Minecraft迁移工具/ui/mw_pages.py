# ui/mw_pages.py
"""界面搭建：主框架、三个清单页、底栏、日志区、窗口样式。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import ctypes
import math
import time
import tkinter as tk
from tkinter import messagebox
from ui.mw_common import _ICON_BTN, _grad_width, WrapRow
from ui.rounded_tabs import RoundedTabs
from utils.dpi import UI_SCALE, px, window_geometry
from utils.helpers import (
    LiquidProgress, RoundedTextArea, SegmentedControl, SmoothScroller, SwitchRow,
    bind_text_scroll, create_gradient_button, is_dark_theme, style_window,
)


from utils import i18n as _i18n
# 日志/文案模板：trp 按位置填值（中文模式下与原 f-string 逐字一致）
from utils.i18n import trp



class PagesMixin:
    """界面搭建：主框架、三个清单页、底栏、日志区、窗口样式。"""

    def open_link(self, url):
        """打开外部链接（官网 / GitHub 仓库）。"""
        try:
            import webbrowser
            webbrowser.open_new_tab(url)
            self.log(trp("🔗 已打开链接：{0}", url), level="INFO", save=False)
        except Exception as e:
            messagebox.showerror("打开失败", trp("无法打开链接：{0}", e), parent=self.settings_win)

    def create_tooltip(self, widget, text):
        """给控件挂悬停提示。

        text 既可以是固定字符串，也可以是"每次悬停现取"的函数 —— 状态标签的内容
        是会变的（✅ / ⚠️ / ❌），用函数取才不会一直停在绑定时的那一句上。
        """
        def enter(event):
            try:
                # 悬停提示的文案也要过语言层（这里不是控件构造器，包装层拦不到）
                msg = _i18n.tr(text() if callable(text) else text)
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
        # 左下角：网络状态**圆点 + 文字**。光一个点用户看不懂，得有字。
        # **必须先 pack**（pack 先到先得）：下面那行声明是居中占满整行的，
        # 后 pack 的话这一块会被挤出窗口。
        self.net_status = tk.Label(
            self.bottom_frame, text="● 正在检测网络…", font=("微软雅黑", 8),
            bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]))
        self.net_status._keep_fg = True        # 颜色由网络状态决定，别被主题统一刷掉
        self.net_status.pack(side="left", padx=(2, 4))
        self.create_tooltip(self.net_status,
                            lambda: getattr(self, "_net_tip", "正在检测网络连通性…"))
        # 迷你折线图：最近几十次探测的连接延迟。**双击**打开完整版。
        # 数据在父进程（网络灯本来就在这边探测），所以不需要跨进程 —— 而流量数字在
        # Qt 子进程里，父进程读不到，所以这里画的是延迟而不是流量。
        # 尺寸必须走 px()：画布写死 84x16 的话，高 DPI 下字号放大了、图还是原尺寸，
        # 看起来就是「一条贴在右边的小斜线」。
        self.net_spark = tk.Canvas(
            self.bottom_frame, width=px(84), height=px(16), bg=self.theme["bg"],
            highlightthickness=0, bd=0, cursor="hand2")
        self.net_spark.pack(side="left", padx=(0, 8))
        self.net_spark.bind("<Double-Button-1>",
                            lambda e: self.show_net_chart())
        self.create_tooltip(self.net_spark, "最近几次探测的连接延迟（双击看完整折线图）")
        self._draw_net_spark()
        # 浮标按钮：把延迟曲线浮到桌面（无边框、置顶、可拖动）
        try:
            self.net_float_btn = create_gradient_button(
                self.bottom_frame, "📌", lambda: self.toggle_net_float(),
                colors=("#90a4ae", "#607d8b"), width=px(30), height=px(20),
                font=("微软雅黑", 8))
            self.net_float_btn.pack(side="left", padx=(0, 6))
            self.create_tooltip(self.net_float_btn,
                                "在桌面显示延迟浮标（点浮标打开曲线图，可拖动）")
        except Exception:
            self.net_float_btn = None
        # （左下角原来还有一个"整机流量"标签，已去掉：本工具一次搜索才 10KB，
        #   整机流量计按用户的实际用法永远看不出变化，留着反而像坏了。
        #   每次查询的请求数/流量改在搜索结果的状态行显示，那个才是精确且有意义的。）
        # 声明文字**用 place 居中**：左边 pack 了网络灯，用 pack 的话它只能在"剩余空间"里
        # 居中 —— 实际效果是偏右（用户截图证实）。
        self.bottom_frame.configure(height=px(22))
        self.bottom_frame.pack_propagate(False)
        声明 = tk.Label(self.bottom_frame, text="本工具完全免费，仅供个人学习交流使用。严禁倒卖或用于商业目的。",
                        font=("微软雅黑", 8))
        声明.place(relx=0.5, rely=0.5, anchor="center")

        # 全部按钮建完，最后按配置摆一遍（显示/隐藏 + 自定义顺序）
        self._apply_button_layout()

        # 窗口最小宽：由"最宽的那一行"决定。这时所有区都建完了，量出来才准。
        #   · 日志工具栏：英文下四个按钮合计 605px，拉到 640 会互相压掉（用户报过）；
        #   · 路径区那两行：输入框是**固定字符宽**（两行才等长），窗口太窄会把右边的
        #     状态标签顶出去（实测 720 时中文只剩「选」、英文剩 `e selec`）。
        try:
            需要 = 0
            条 = getattr(self, "log_toolbar", None)
            if 条 is not None:
                需要 = 条.winfo_reqwidth() + 90
            for 控件 in (getattr(self, "source_entry", None),
                        getattr(self, "target_entry", None)):
                行 = getattr(控件, "master", None)
                if 行 is not None:
                    需要 = max(需要, 行.winfo_reqwidth() + 60)
            self.root.minsize(max(720, min(需要, 1400)), 520)
        except Exception:
            self.root.minsize(800, 520)
        # 窗口宽度变了要重摆：按钮是 place 出来的，不重算就一直是旧宽度下的一行 ——
        # 英文按钮更宽，拉窄窗口就会溢出（用户报过 config / other files 三处）。
        # 防抖 260ms：拖动窗口时别每一帧都重排。
        def _reflow(_e=None):
            旧 = getattr(self, "_reflow_job", None)
            if 旧 is not None:
                try:
                    self.root.after_cancel(旧)
                except Exception:
                    pass
            try:
                self._reflow_job = self.root.after(
                    260, lambda: self._apply_button_layout(animate=False))
            except Exception:
                self._reflow_job = None

        self._reflow_job = None
        self.root.bind("<Configure>", _reflow, add="+")

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

        btn_frame = WrapRow(parent)
        btn_frame.pack(fill="x", pady=5)

        # 统一渐变按钮（同高度/字体，语义配色，宽度按文字自适应）
        gw = _grad_width      # 用缓存了 Font 的量宽函数，别再就地 new 一个 Font

        self.btn_changelog = create_gradient_button(
            btn_frame, "📥 从变更日志导入", self.import_from_changelog,
            colors=("#00acc1", "#26c6da"),
            height=30, font=("微软雅黑", 9, "bold"))
        self.btn_changelog.pack(side="left", padx=5)
        self._btn_widgets["changelog"] = self.btn_changelog
        self.create_tooltip(self.btn_changelog, "你需要提供的是“崩溃助手”模组给予的mod变更列表")

        self.scan_btn = create_gradient_button(
            btn_frame, "🔍 扫描差异", self.action_scan_mod_diff,
            colors=("#00bcd4", "#3f51b5"),
            height=30, font=("微软雅黑", 9, "bold"))
        self.mod_magnify_btn = create_gradient_button(
            btn_frame, "📂 放大查看", self.open_mod_big_view,
            colors=("#607d8b", "#90a4ae"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_mods_btn = create_gradient_button(
            btn_frame, "➕ 添加模组", self.add_mods,
            colors=("#00c853", "#00e676"), height=30, font=("微软雅黑", 9, "bold"))
        self._stage()               # 这批有 6 个按钮，中间让一次
        self.clear_mods_btn = create_gradient_button(
            btn_frame, "🗑️ 清空清单", self.clear_mod_list,
            colors=("#e53935", "#c62828"), height=30, font=("微软雅黑", 9, "bold"))
        self.check_mods_btn = create_gradient_button(
            btn_frame, "🔎 检查存在", self.check_modlist_existence,
            colors=("#fb8c00", "#ffb74d"),
            height=30, font=("微软雅黑", 9, "bold"))
        # 按钮上写短句，限定语放 tooltip（照原文案"（源目录）"的意思）
        self.create_tooltip(self.check_mods_btn, "检查清单里的模组在“源目录”里是否存在")
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

        btn_config_frame = WrapRow(parent)
        btn_config_frame.pack(fill="x", pady=5)

        self.config_magnify_btn = create_gradient_button(
            btn_config_frame, "📂 放大查看", self.open_config_big_view,
            colors=("#607d8b", "#90a4ae"), height=30, font=("微软雅黑", 9, "bold"))
        self.config_magnify_btn.pack(side="left", padx=5)
        self.add_config_dir_btn = create_gradient_button(
            btn_config_frame, "📁 添加文件夹", self.browse_add_config_entry,
            colors=("#00c853", "#00e676"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_config_dir_btn.pack(side="left", padx=5)
        self.add_config_file_btn = create_gradient_button(
            btn_config_frame, "📄 添加文件", self.browse_add_config_file,
            colors=("#00c853", "#00e676"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_config_file_btn.pack(side="left", padx=5)
        self.clear_config_btn = create_gradient_button(
            btn_config_frame, "🗑️ 清空 config 清单", self.clear_config,
            colors=("#e53935", "#c62828"), height=30, font=("微软雅黑", 9, "bold"))
        self.clear_config_btn.pack(side="left", padx=5)
        self.config_check_btn = create_gradient_button(
            btn_config_frame, "🔎 检查存在", self.check_configlist_existence,
            colors=("#fb8c00", "#ffb74d"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.config_check_btn.pack(side="left", padx=5)
        self.create_tooltip(self.config_check_btn, "检查 config 清单里的条目在“源目录”里是否存在")
        self._btn_widgets.update({
            "cfg_magnify": self.config_magnify_btn,
            "add_cfg_dir": self.add_config_dir_btn,
            "add_cfg_file": self.add_config_file_btn,
            "clear_cfg": self.clear_config_btn,
            "check_cfg": self.config_check_btn,
        })
        # ⚠ 图例**不能**和按钮混在同一个容器里：`mw_buttons._placeable_row` 只接
        # "容器里全是按钮"的排，混着别的控件就退回单行 pack —— 英文按钮更宽时会溢出
        # （用户报过 config / other files 两页）。挪到按钮排下面的独立一行，
        # 按钮排变成"纯按钮排"，才能走那套带自动换行的 place 布局。
        legend_row = tk.Frame(parent)
        legend_row.pack(fill="x", padx=5, pady=(0, 2))
        self._create_check_legend(legend_row)
        self._bind_badge_refresh(self.config_text)
        self._stage()

    def _create_extra_page(self, parent):
        """其它文件页：带走 mods / config / saves 之外的东西。

        路径相对**整合包根目录**（不是 config）：options.txt、servers.dat、
        shaderpacks/、resourcepacks/、kubejs/、scripts/ 这类都走这一页。

        **空清单 = 什么都不多带**（包括 options.txt —— 它以前是被无条件复制的，
        现在也得用户自己勾）。
        """
        # 说明文字：**占满可用宽度 + 自动换行**（原来文案里写死 `\n`、又 anchor="w"，
        # 于是英文下每行长短不一、右边一大片空白 —— 用户："右边有空为什么不用"）。
        # wraplength 跟着容器宽度走，中英文都能铺满。
        _tip = tk.Label(parent,
                        text=("每行一个路径，「相对整合包根目录」（文件或文件夹；文件夹会递归复制）。"
                              "不会自动带任何东西 —— 想要的自己加。"
                              "例：options.txt   servers.dat   shaderpacks/   resourcepacks/   kubejs/"),
                        bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                        font=("微软雅黑", 8), justify="left", anchor="w")
        _tip.pack(fill="x", padx=5, pady=(2, 0))

        def _wrap_tip(_e=None):
            try:
                _tip.configure(wraplength=max(120, parent.winfo_width() - 16))
            except Exception:
                pass

        parent.bind("<Configure>", _wrap_tip, add="+")
        parent.after(120, _wrap_tip)

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

        btn_extra_frame = WrapRow(parent)
        btn_extra_frame.pack(fill="x", pady=5)
        self.add_extra_dir_btn = create_gradient_button(
            btn_extra_frame, "📁 添加文件夹", self.browse_add_extra_entry,
            colors=("#00c853", "#00e676"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_extra_dir_btn.pack(side="left", padx=5)
        self.add_extra_file_btn = create_gradient_button(
            btn_extra_frame, "📄 添加文件", self.browse_add_extra_file,
            colors=("#00c853", "#00e676"), height=30, font=("微软雅黑", 9, "bold"))
        self.add_extra_file_btn.pack(side="left", padx=5)
        # 一键把最常见的那个加进来：options.txt 以前是**默认复制**的，现在也得用户自己勾，
        # 但没必要让人手打文件名/去文件夹里翻
        self.add_extra_options_btn = create_gradient_button(
            btn_extra_frame, "＋ options.txt", self._quick_add_options_txt,
            colors=("#3949ab", "#5c6bc0"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.add_extra_options_btn.pack(side="left", padx=5)
        # 常用目录（原来的「默认携带的目录」勾选框已删，统一到这里"主动加进清单"）：
        # 菜单里点一下就写进清单 —— 看得见、能改、能删，备份/回滚也跟着清单走
        self.add_extra_preset_btn = create_gradient_button(
            btn_extra_frame, "＋ 常用目录 ▾", self._open_extra_preset_menu,
            colors=("#5e35b1", "#7e57c2"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.add_extra_preset_btn.pack(side="left", padx=5)
        self.clear_extra_btn = create_gradient_button(
            btn_extra_frame, "🗑️ 清空清单", self.clear_extra_list,
            colors=("#e53935", "#c62828"), height=30, font=("微软雅黑", 9, "bold"))
        self.clear_extra_btn.pack(side="left", padx=5)
        self.extra_check_btn = create_gradient_button(
            btn_extra_frame, "🔎 检查存在", self.check_extralist_existence,
            colors=("#fb8c00", "#ffb74d"), height=30,
            font=("微软雅黑", 9, "bold"))
        self.extra_check_btn.pack(side="left", padx=5)
        self.create_tooltip(self.extra_check_btn, "检查其它文件清单里的条目在“源目录”里是否存在")
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

        # 右侧按钮组（普通 Frame：三个按钮横排、贴右，维持原样）
        btn_group = tk.Frame(self.opt_frame, bg=self.theme["bg"])
        btn_group.pack(side="right")

        self.start_btn = create_gradient_button(
            parent=btn_group,
            text="🚀 开始迁移",
            command=self.start_migration,
            colors=("#00c853", "#00e676"),
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

        log_toolbar = WrapRow(frame_log)
        log_toolbar.pack(fill="x", pady=(0, 5))
        self.log_toolbar = log_toolbar          # 自适应/测试要用到它
        btn_big_log = create_gradient_button(
            log_toolbar, "📂 放大查看", self.open_log_big_view_busy,
            colors=("#607d8b", "#90a4ae"), height=30, font=("微软雅黑", 9, "bold"))
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
            colors=("#e53935", "#c62828"), height=30, font=("微软雅黑", 9, "bold"))
        btn_clear_log.pack(side="right", padx=5)
        btn_open_log = create_gradient_button(
            log_toolbar, "📂 打开日志文件夹", self.open_log_folder,
            colors=("#607d8b", "#90a4ae"), height=30, font=("微软雅黑", 9, "bold"))
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
        # 窗口一窄，left / right 两组按钮会互相压掉（英文按钮更宽：完整提示句 41 字符
        # 约 280px，加 4 个按钮就超出窗口，用户报过 "Open log folder 被裁"）。
        # 这里做三级自适应：完整句 → 短句 → 整条收起来。它只是辅助说明，按钮才是要点的。
        # 用 `before=` 插回原位，所以不会跳位置，也不会和 left/right 的排布打架。
        _长句 = "💡 双击日志行可定位到清单里的那条"
        _短句 = "💡 双击日志行可定位"

        def _fit_hint(_e=None):
            try:
                import tkinter.font as _tkfont
                档 = _tkfont.Font(font=self.log_hint_label.cget("font"))
                其他 = sum(c.winfo_reqwidth() + 10
                           for c in log_toolbar.winfo_children()
                           if c.winfo_manager() and c is not self.log_hint_label)
                宽 = log_toolbar.winfo_width()
                长宽 = 档.measure(_i18n.tr(_长句)) + 10
                短宽 = 档.measure(_i18n.tr(_短句)) + 10
                已显示 = bool(self.log_hint_label.winfo_manager())
                if 其他 + 长宽 <= 宽:
                    目标 = _长句
                elif 其他 + 短宽 <= 宽:
                    目标 = _短句
                else:
                    目标 = None
                if 目标 is None:
                    if 已显示:
                        self.log_hint_label.pack_forget()
                else:
                    if not 已显示:
                        self.log_hint_label.pack(side="left", padx=(10, 0),
                                                 before=self.btn_fail_locate)
                    self.log_hint_label.configure(text=目标)
            except Exception:
                pass

        log_toolbar.bind("<Configure>", _fit_hint)
        self.root.after(200, _fit_hint)          # 首帧也判一次（<Configure> 未必触发）
        # 窗口能缩到多窄，由这排按钮决定：主窗口以前**没设过 minsize**，拉到 640 时
        # ⚠ 最小宽度的计算挪到**所有区都建完**之后（见 _apply_button_layout 之前那处）：
        # 放这里的话路径区还没建，`self.source_entry` 根本不存在，算出来偏小。
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


    # ---------------- 网络延迟迷你图 / 完整版 ----------------
    def _draw_net_spark(self):
        """左下角的迷你延迟曲线（Pillow 3 倍超采样绘制）。

        为什么不再用 canvas 原生折线：
          · Tk 的坐标是**整数像素**，105px 宽要装 60 秒 → 1px ≈ 0.57s，
            曲线只能「每半秒跳一格」，帧率再高也没用（这就是「不流畅」的真因）；
          · 原生折线没有抗锯齿，1px 的线在高 DPI 屏上看着发毛。
        改成在 3 倍画布上用 Pillow 画、再 LANCZOS 缩回来：亚像素平滑 + 抗锯齿。
        每帧约 0.9ms（20fps 下可忽略），而且**线端点和圆点在同一次绘制里**，
        位置天然对齐（以前圆点画在顶点上、线是 B 样条，会看见它"脱开"）。

        数据仍然按**真实时间**映射（第几个点排会整体跳动），1px 级别的位置由
        超采样负责平滑，不再受 canvas 整数坐标限制。
        """
        try:
            from ui import mw_net
            from PIL import Image, ImageDraw, ImageTk
            c = self.net_spark
            w = int(c.cget("width"))
            h = int(c.cget("height"))
            现在 = time.time()
            窗口秒 = 60.0
            历史 = mw_net.rtt_history()
            原始 = [(x[0], x[1]) for x in 历史 if 现在 - x[0] <= 窗口秒]

            if len(原始) < 2:
                c.delete("all")
                c.create_text(w // 2, h // 2, text="…", fill="#888888",
                              font=("微软雅黑", 8))
            else:
                最大 = max(1, max(v for _, v in 原始))
                # 上限带 15% 余量 + **缓降**：否则每来一个新峰值，整条曲线会重新
                # 缩放一次，视觉上就是"猛地一跳"。涨的时候立刻跟上，落的时候慢慢降。
                目标 = max(50.0, 最大 * 1.15)
                旧 = getattr(self, "_net_y_max", None)
                if 旧 is None or 目标 > 旧:
                    上限 = 目标
                else:
                    上限 = 旧 * 0.9 + 目标 * 0.1
                self._net_y_max = 上限

                S = 3                       # 超采样倍数
                边 = max(2, px(2))          # 留边，端点圆点不贴边
                可用宽 = max(1, w - 2 * 边)
                可用高 = max(1, h - 2 * 边)
                坐标 = []
                for ts, v in 原始:
                    frac = min(1.0, max(0.0, (现在 - ts) / 窗口秒))
                    x = 边 + 可用宽 * (1.0 - frac)
                    y = h - 边 - 可用高 * min(1.0, v / 上限)
                    坐标.append((x * S, y * S))

                通 = 历史[-1][2] > 0
                色 = (129, 199, 132, 255) if 通 else (198, 40, 40, 255)

                img = Image.new("RGBA", (w * S, h * S), self.theme["bg"])
                d = ImageDraw.Draw(img)
                d.line(坐标, fill=色, width=max(2, int(round(1.4 * S))), joint="curve")
                lx, ly = 坐标[-1]
                r = max(1.6, 1.2 * UI_SCALE) * S
                d.ellipse([lx - r, ly - r, lx + r, ly + r], fill=色)
                img = img.resize((w, h), Image.LANCZOS)

                photo = ImageTk.PhotoImage(img)
                c.delete("all")
                c.create_image(0, 0, anchor="nw", image=photo)
                self._net_spark_photo = photo      # 必须留引用，否则会被回收成空白
        except Exception:
            pass
        try:
            # 50ms（20fps）：Pillow 每帧 ~0.9ms，够便宜；再快肉眼也分不出来
            self._net_spark_job = self.root.after(50, self._draw_net_spark)
        except Exception:
            pass

    def show_net_chart(self):
        """完整版：最近探测延迟的曲线图 + 统计（双击迷你图打开）。

        三个要点：
        1. **平滑曲线**：采样点之间用 PCHIP（单调三次 Hermite）插值，不是直线段。
           PCHIP 不会像普通样条那样在峰谷之间乱冲，延迟数据不会画出负值。
        2. **连续滑动**：曲线只在"数据变了"时重算一次（有缓存的密集采样点），
           之后每帧只把 x 平移 (now - t_base) —— 纯减法，60fps 也扛得住。
        3. **blitting**：坐标轴/网格画一次缓存成位图，每帧只 restore + 重画曲线。
           注意时间窗必须**量化**（10/15/20/30/45/60 秒跳档），否则窗口每帧都在变，
           背景每帧重建（20ms+），帧率立刻掉到 20 多。

        悬停：鼠标移上去吸附最近采样点，竖线 + 提示框显示「时间 · 延迟」。
        """
        try:
            from ui import mw_net
            历史 = list(mw_net.rtt_history())
        except Exception:
            历史 = []

        顶 = tk.Toplevel(self.root)
        顶.title("📈 网络延迟（最近 %d 次探测）" % len(历史))
        try:
            顶.geometry(window_geometry(顶, 760, 420))
        except Exception:
            顶.geometry("%dx%d" % (px(760), px(420)))
        顶.minsize(px(520), px(300))
        顶.configure(bg=self.theme["bg"])
        顶.transient(self.root)
        try:
            set_window_icon(顶)
        except Exception:
            try:
                from utils.helpers import set_window_icon as _swi
                _swi(顶)
            except Exception:
                pass

        try:
            import matplotlib
            matplotlib.use("TkAgg")
            matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                                      "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            import numpy as np
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except Exception as _e:
            tk.Label(顶, text="绘图需要 matplotlib：\n\npip install matplotlib\n\n(%s)" % _e,
                     bg=self.theme["bg"], fg="#c62828", justify="left",
                     font=("微软雅黑", 10)).pack(padx=px(16), pady=px(16))
            create_gradient_button(顶, "关闭", lambda: 顶.destroy()).pack(pady=(0, px(10)))
            return

        深色 = is_dark_theme(self.theme)
        底色 = self.theme["bg"]
        前景 = "#c8c8c8" if 深色 else "#444444"
        网格色 = "#3f3f3f" if 深色 else "#e3e3e3"
        提示底色 = "#2b2b2b" if 深色 else "#ffffff"
        线色 = "#42a5f5"
        点色 = "#90caf9"
        尾色 = "#bbdefb" if 深色 else "#0d47a1"

        信息 = tk.Label(顶, bg=self.theme["bg"],
                        fg=self.theme.get("muted_fg", "#888888"),
                        font=("微软雅黑", 9), anchor="w", justify="left")
        信息.pack(side="bottom", fill="x", padx=px(12), pady=(0, px(8)))
        # lambda 里引用后面才定义的 _关闭：点击时才解析名字，所以这样写没问题，
        # 而且能保证关窗时还原定时器精度（timeEndPeriod）。
        关闭钮 = create_gradient_button(顶, "关闭", lambda: _关闭())
        关闭钮.pack(side="bottom", pady=(0, px(10)))

        图 = Figure(figsize=(px(760) / 100.0, px(380) / 100.0), dpi=100, facecolor=底色)
        轴 = 图.add_subplot(111, facecolor=底色)
        画布 = FigureCanvasTkAgg(图, master=顶)
        画布.get_tk_widget().pack(side="top", fill="both", expand=True,
                                  padx=px(10), pady=(px(10), px(4)))

        # ---- 静态样式 ----
        轴.set_facecolor(底色)
        for s in 轴.spines.values():
            s.set_color(网格色)
        轴.tick_params(colors=前景, labelsize=9)
        轴.grid(True, color=网格色, linewidth=0.8)
        轴.set_ylabel("ms", color=前景, fontsize=9)
        try:
            图.subplots_adjust(left=0.11, right=0.98, top=0.97, bottom=0.16)
        except Exception:
            pass
        轴.set_xlabel("秒（右端 = 现在）", color=前景, fontsize=9)

        # ---- 持久图元 ----
        线, = 轴.plot([], [], color=线色, linewidth=1.8,
                      solid_capstyle="round", solid_joinstyle="round",
                      antialiased=True, zorder=3)
        点, = 轴.plot([], [], "o", color=点色, markersize=3.2, zorder=4)
        尾点, = 轴.plot([], [], "o", color=尾色, markersize=6.5, zorder=5)
        空提示 = 轴.text(0.5, 0.5, "还没有探测数据（等几秒）", ha="center", va="center",
                        color=前景, fontsize=10, transform=轴.transAxes,
                        zorder=2, visible=False)
        十字 = 轴.axvline(0, color=前景, linewidth=0.8, alpha=0.55,
                          zorder=6, visible=False)
        # 用普通 Text 而不是 Annotation：Annotation 在 textcoords="offset points" 下
        # 每帧改 .xy 的定位不可靠（实测会跑到图顶并被 blit 区域裁掉）。
        提示 = 轴.text(0, 0, "", color=前景, fontsize=9, zorder=10, visible=False,
                      ha="left", va="bottom", clip_on=False,
                      bbox=dict(boxstyle="round,pad=0.35", fc=提示底色,
                                ec=网格色, lw=0.8, alpha=0.96))

        动态 = (线, 点, 尾点, 空提示, 十字, 提示)
        数据 = {"ts": [], "span": 60.0, "hover_ts": None,
                "dx": None, "dy": None, "t_base": 0.0, "sig": None,
                "end_y": None, "end_from": 0.0, "end_target": 0.0, "end_t0": None}
        过渡秒 = 0.35      # 新采样点到达时，末端值用这么久缓动过去
        状态 = {"bg": None, "span": None, "ylim": None, "stop": False}
        帧间隔 = 10          # 目标 60fps（每帧开销 ~4ms，10ms 间隔 + 调度抖动 ≈ 16ms/帧）

        # Windows 默认定时器精度 15.6ms：after(16) 实际会落到 ~31ms（测出来就是 32fps）。
        # 临时提到 1ms，帧才能真正跑满 —— 关窗时再还原，避免长期影响系统功耗。
        _定时器 = {"on": False}
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            _定时器["on"] = True
        except Exception:
            pass

        窗口固定 = 60.0     # 标准监控图：右端永远是"现在"，整条曲线持续左流

        def _窗口(跨度):
            """固定 60 秒 —— 窗口不变，背景缓存就永远不失效，滚动最稳。"""
            return 窗口固定

        def _取数据():
            """(xs, ys, ts, 窗口, 现在t, 基准)。

            x 轴固定 0..窗口，**右端 = 现在**：
              · 新点从右边(x≈窗口)进入，随时间整体向左流动；
              · 左端(x=0)是 60 秒前的数据，流出去就丢弃。
            """
            现在t = time.time()
            时刻 = [h[0] for h in 历史]
            跨度 = max(0.0, 现在t - 时刻[0]) if 时刻 else 0.0
            窗口 = _窗口(跨度) if 跨度 > 0 else 10.0
            基准 = 窗口
            xs, ys, ts = [], [], []
            for t0, v in zip(时刻, [h[1] for h in 历史]):
                if (现在t - t0) > 窗口:
                    continue
                xs.append(基准 - (现在t - t0))
                ys.append(v)
                ts.append(t0)
            return xs, ys, ts, 窗口, 现在t, 基准

        def _整数档(ys):
            """把上限取整到 500 的档位，避免几十毫秒的波动就触发重绘/重缩放。"""
            m = max(ys) if ys else 1
            return max(500.0, math.ceil(m * 1.2 / 500.0) * 500.0)

        def _上限(ys):
            """带滞回：涨了立刻跟上，只有掉到当前档位的 60% 以下才缩回去。"""
            目标 = _整数档(ys)
            当前 = 状态.get("cap")
            if 当前 is None or 目标 > 当前:
                当前 = 目标
            elif 目标 < 当前 * 0.6:
                当前 = 目标
            状态["cap"] = 当前
            return 当前

        def _pchip(x, y):
            """单调三次 Hermite 插值（Fritsch–Carlson）。返回密集的 (x, y)。"""
            x = np.asarray(x, dtype=float)
            y = np.asarray(y, dtype=float)
            m = len(x)
            if m < 3:
                return x, y
            h = np.diff(x)
            if np.any(h <= 0):
                return x, y                       # 时间戳非严格递增就不插值，宁可直接画
            d = np.diff(y) / h
            g = np.zeros(m)
            g[0], g[-1] = d[0], d[-1]
            for i in range(1, m - 1):
                if d[i - 1] * d[i] <= 0:
                    g[i] = 0.0                    # 极值点处切线归零，避免过冲
                else:
                    w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
                    g[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
            t = np.linspace(0.0, 1.0, 8, endpoint=False)
            h00 = 2 * t ** 3 - 3 * t ** 2 + 1
            h10 = t ** 3 - 2 * t ** 2 + t
            h01 = -2 * t ** 3 + 3 * t ** 2
            h11 = t ** 3 - t ** 2
            xs, ys = [], []
            for i in range(m - 1):
                xs.append(x[i] + t * h[i])
                ys.append(h00 * y[i] + h10 * h[i] * g[i]
                          + h01 * y[i + 1] + h11 * h[i] * g[i + 1])
            xs.append(x[-1:])
            ys.append(y[-1:])
            return np.concatenate(xs), np.concatenate(ys)

        def 缓存背景(xs, ys, 窗口, 上限):
            轴.set_xlim(0, 窗口)
            轴.set_ylim(0, 上限)
            for a in 动态:
                a.set_visible(False)
            画布.draw()
            # 缓存**整张图**而不只是坐标轴：提示框允许画到轴外（clip_on=False），
            # 只恢复轴区域的话，画到外面的那部分会永远留在画布上（残影）。
            状态["bg"] = 画布.copy_from_bbox(图.bbox)
            状态["span"], 状态["ylim"] = 窗口, 上限
            for a in (线, 点, 尾点):
                a.set_visible(True)

        def _藏提示():
            数据["hover_ts"] = None
            if 十字.get_visible() or 提示.get_visible():
                十字.set_visible(False)
                提示.set_visible(False)

        def 帧():
            if 状态["stop"]:
                return
            try:
                if not 顶.winfo_exists():
                    return
                from ui import mw_net
                历史.clear()
                历史.extend(mw_net.rtt_history())

                xs, ys, ts, 窗口, 现在t, 基准 = _取数据()
                数据.update(ts=ts, span=窗口, base=基准)
                上限 = _上限(ys)

                # ---- 新数据到达：记下"从哪过渡到哪"，末端值用缓动追上去 ----
                sig = (len(ys), ts[-1] if ts else 0.0)
                过渡中 = False
                if 数据["sig"] != sig or 数据["dx"] is None:
                    旧值 = 数据["end_y"] if 数据["end_y"] is not None else (ys[-1] if ys else 0.0)
                    数据["end_from"] = 旧值
                    数据["end_target"] = ys[-1] if ys else 0.0
                    数据["end_t0"] = 现在t
                    数据["sig"] = sig
                    if len(xs) >= 3:
                        数据["dx"], 数据["dy"] = _pchip(xs, ys)
                    else:
                        数据["dx"] = np.asarray(xs, dtype=float)
                        数据["dy"] = np.asarray(ys, dtype=float)
                    数据["t_base"] = 现在t
                    数据["base_base"] = 基准

                # 过渡进度（easeOutCubic）：末端值从旧值滑到新值，
                # 这样 5 秒一次的新采样不会让曲线"啪"地变形。
                p = 1.0
                if 数据["end_t0"] is not None and 数据["end_target"] is not None:
                    p = min(1.0, (现在t - 数据["end_t0"]) / 过渡秒)
                if p < 1.0 and len(xs) >= 2:
                    缓 = 1.0 - (1.0 - p) ** 3
                    y末 = 数据["end_from"] + (数据["end_target"] - 数据["end_from"]) * 缓
                    ys2 = list(ys)
                    ys2[-1] = y末
                    if len(xs) >= 3:
                        dx, dy = _pchip(xs, ys2)
                    else:
                        dx, dy = np.asarray(xs, dtype=float), np.asarray(ys2, dtype=float)
                    数据["dx_t"], 数据["dy_t"] = dx, dy
                    数据["t_base_t"] = 现在t
                    数据["end_y"] = y末
                    过渡中 = True
                else:
                    数据["end_y"] = 数据["end_target"] if 数据["end_target"] is not None else \
                        (ys[-1] if ys else 0.0)

                if (状态["bg"] is None or 窗口 != 状态["span"]
                        or abs(上限 - (状态["ylim"] or 0)) > 1e-9):
                    缓存背景(xs, ys, 窗口, 上限)

                # 每帧只做一次减法：曲线整体左移 (现在 - t_base)
                if 过渡中:
                    基线, 平移 = 数据["t_base_t"], 0.0
                    tx = 数据["dx_t"] - 平移
                    ty = 数据["dy_t"]
                else:
                    # 时间流逝让曲线左移；基准（还没填满时=跨度）增长让它右移
                    tx = (数据["dx"] - (现在t - 数据["t_base"])
                          + (基准 - 数据.get("base_base", 基准)))
                    ty = 数据["dy"]
                if len(tx):
                    # 末端补一段到"现在"的位置，表示当前值，看着不断流
                    tx = np.append(tx, 基准)
                    ty = np.append(ty, ty[-1])
                线.set_data(tx, ty)

                # 采样点也跟着平移
                mx = np.asarray(数据["ts"], dtype=float) - 现在t
                if len(mx) and len(mx) <= 40:
                    点.set_data(mx, np.asarray(ys, dtype=float))
                else:
                    点.set_data([], [])
                if len(mx):
                    尾点.set_data([mx[-1:], [数据["end_y"]]])
                else:
                    尾点.set_data([], [])
                空提示.set_visible(not len(mx))

                画布.restore_region(状态["bg"])
                轴.draw_artist(线)
                轴.draw_artist(点)
                轴.draw_artist(尾点)
                if not len(mx):
                    轴.draw_artist(空提示)

                # 悬停提示：按"悬停的那个时间戳"每帧重新定位
                hts = 数据.get("hover_ts")
                j = None
                if hts is not None:
                    try:
                        j = 数据["ts"].index(hts)
                    except ValueError:
                        j = None
                if j is not None and 0 <= j < len(ys):
                    # 新坐标系：x = 基准 - 数据年龄（0..窗口，从左往右）
                    hx = 基准 - (现在t - 数据["ts"][j])
                    十字.set_xdata([hx, hx])
                    十字.set_visible(True)
                    # 摆在点的右上方，并夹在绘图区内（blit 只更新坐标轴区域，
                    # 画到区外的部分会被切掉）
                    px_off = 数据["span"] * 0.012
                    py_off = (状态["ylim"] or 上限) * 0.05
                    # 夹在绘图区内：右边界要留出提示框自身的宽度
                    提示.set_position((max(0.0, min(hx + px_off,
                                                    数据["span"] * 0.72)),
                                      min(ys[j] + py_off, (状态["ylim"] or 上限) * 0.93)))
                    提示.set_text("%s  ·  %d ms" % (
                        time.strftime("%H:%M:%S", time.localtime(hts)), ys[j]))
                    提示.set_visible(True)
                    轴.draw_artist(十字)
                    轴.draw_artist(提示)
                else:
                    十字.set_visible(False)
                    提示.set_visible(False)
                画布.blit(图.bbox)

                # ⚠ 别每帧都改 Label：Tk 换文字会重新布局 + 重绘，一次好几毫秒，
                # 60fps 的预算就这么没了。只在数据真的变了的时候刷新。
                if 数据.get("sig_text") != sig:
                    数据["sig_text"] = sig
                    if ys:
                        值 = [h[1] for h in 历史]
                        信息.configure(
                            text="最近 %d 次探测 ｜ 当前 %d ms ｜ 平均 %.0f ms ｜ 最高 %d ms"
                                 % (len(值), 值[-1], sum(值) / max(1, len(值)), max(值)))
                    else:
                        信息.configure(text="")
            except Exception:
                pass
            顶.after(帧间隔, 帧)

        def _鼠标移动(ev):
            try:
                if ev.inaxes is not 轴 or not 数据["ts"] or ev.xdata is None:
                    _藏提示()
                    return
                现在t = time.time()
                时刻 = 数据["ts"]
                基准 = 数据.get("base", 0.0)
                xs = [基准 - (现在t - t0) for t0 in 时刻]
                import bisect
                i = bisect.bisect_left(xs, ev.xdata)
                候选 = [j for j in (i - 1, i) if 0 <= j < len(xs)]
                if not 候选:
                    _藏提示()
                    return
                j = min(候选, key=lambda k: abs(xs[k] - ev.xdata))
                if abs(xs[j] - ev.xdata) > max(1.0, 数据["span"] * 0.06):
                    _藏提示()
                    return
                数据["hover_ts"] = 时刻[j]
            except Exception:
                pass

        画布.mpl_connect("motion_notify_event", _鼠标移动)
        画布.mpl_connect("axes_leave_event", lambda _e: _藏提示())
        画布.get_tk_widget().bind("<Configure>", lambda _e: 状态.update(bg=None), add="+")

        def _关闭():
            状态["stop"] = True
            if _定时器["on"]:
                try:
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass
                _定时器["on"] = False
            try:
                顶.destroy()
            except Exception:
                pass

        顶.protocol("WM_DELETE_WINDOW", _关闭)

        try:
            缓存背景([], [], 10.0, 50.0)
        except Exception:
            pass
        帧()

    # ---------------- 桌面浮标（网络延迟小窗） ----------------
    def toggle_net_float(self):
        """开 / 关桌面浮标。"""
        try:
            if self._net_float is not None and self._net_float.alive():
                self._net_float.close()
            else:
                self.open_net_float()
        except Exception:
            pass

    def open_net_float(self):
        """把延迟曲线浮到桌面：无边框、置顶、可拖、位置记忆。"""
        try:
            from ui.net_float import NetFloat
        except Exception:
            return
        if self._net_float is not None and self._net_float.alive():
            return
        x = getattr(self, "net_float_x", None)
        y = getattr(self, "net_float_y", None)
        try:
            if x is not None and int(x) < 0:
                x = None
            if y is not None and int(y) < 0:
                y = None
        except Exception:
            x = y = None

        def 历史():
            from ui import mw_net
            return mw_net.rtt_history()

        self._net_float = NetFloat(self.root, 历史, self.show_net_chart,
                                   on_close=self._on_net_float_event, x=x, y=y)
        self.net_float_on = True
        try:
            self.save_config()
        except Exception:
            pass

    def _on_net_float_event(self, flt, closed):
        """浮标关闭 / 拖动结束时调用：记住位置和开关状态。"""
        try:
            pos = flt.position()
            if pos:
                self.net_float_x, self.net_float_y = int(pos[0]), int(pos[1])
        except Exception:
            pass
        self.net_float_on = not closed
        if closed:
            self._net_float = None
        try:
            self.save_config()
        except Exception:
            pass

# ui/mw_pages.py
"""界面搭建：主框架、三个清单页、底栏、日志区、窗口样式。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import ctypes
from ctypes import wintypes
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
            self.toast(trp("🔗 已打开链接：{0}", url), level="info")
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
        # 消息中心入口：铃铛（有未读时显示条数）。吐司会消失，这里能回看。
        try:
            self.msg_bell = tk.Label(
                self.bottom_frame, text="🔔", bg=self.theme["bg"],
                fg=self.theme.get("muted_fg", "#888888"), cursor="hand2",
                font=("微软雅黑", 9))
            self.msg_bell.pack(side="left", padx=(0, 6))
            self.msg_bell.bind("<Button-1>", lambda e: self.open_message_center())
            self.create_tooltip(self.msg_bell, "消息中心：最近的提示消息（Ctrl+M）")
        except Exception:
            self.msg_bell = None
        try:
            self.net_float_btn = create_gradient_button(
                self.bottom_frame, "📌", lambda: self.toggle_net_float(),
                colors=("#90a4ae", "#607d8b"), width=px(30), height=px(20),
                font=("微软雅黑", 8))
            self.net_float_btn.pack(side="left", padx=(0, 6))
            self.create_tooltip(self.net_float_btn,
                                "在主窗口内显示延迟浮标（点浮标打开曲线图，可拖动）")
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

    # ---------- 整合包更新提醒 ----------
    def _build_pack_warning(self):
        """在模组清单上方显示一条提醒：整合包更新可能删/换模组，差异扫描靠不住。

        为什么值得单独提醒：「扫描差异」比的是**清单里的文件名和目标目录里的文件**，
        作者在更新整合包时如果悄悄删掉某个模组，我们的清单里根本不会有它 —— 扫出来是
        "没有差异"，实际却少了东西。所以这里让用户务必核对更新日志，并提供一键复制
        一段"让 AI 分析更新日志"的提示词。
        """
        父 = getattr(self, "_pack_warning_parent", None)
        if 父 is None or not getattr(self, "pack_warning", True):
            return
        旧 = getattr(self, "_pack_warning_frame", None)
        if 旧 is not None:
            try:
                旧.destroy()
            except Exception:
                pass
            self._pack_warning_frame = None

        深 = self.theme.get("bg", "#ffffff").lower() in (
            "#2e2e2e", "#1e1e1e", "#252525", "#202020", "#2b2b2b")
        底 = "#3a3020" if 深 else "#fff8e1"
        边 = "#8a6d1f" if 深 else "#e0a800"
        字 = "#e8d9a8" if 深 else "#5c4400"
        主 = self.theme.get("data_fg", "#1565c0") if not 深 else "#79b8ff"
        弱色 = "#8a7a4a" if not 深 else "#9d8f6a"

        框 = tk.Frame(父, bg=底, highlightthickness=1, highlightbackground=边)
        框.pack(fill="x", padx=5, pady=(4, 2))
        self._pack_warning_frame = 框

        tk.Label(框, text="⚠️", font=("微软雅黑", 13), bg=底, fg=边).pack(
            side="left", padx=(10, 8), anchor="n")

        文本 = ("整合包更新时，作者可能会删除或替换模组 —— 这种情况下「扫描差异」只能比出"
              "文件名和版本的不同，被作者删掉的模组是发现不了的。\n"
              "请务必核对更新日志里的模组变更；也可以把更新日志交给 AI 帮你逐条分析。")
        tk.Label(框, text=文本, justify="left", anchor="w", bg=底, fg=字,
                 font=("微软雅黑", 9), wraplength=px(560)).pack(
            side="left", fill="x", expand=True, pady=8)

        右 = tk.Frame(框, bg=底)
        右.pack(side="right", padx=(8, 10))

        def _链接(文字, 命令):
            标 = tk.Label(右, text=文字, bg=底, fg=主, cursor="hand2",
                         font=("微软雅黑", 9, "underline"))
            标.pack(side="left", padx=(0, 12))
            标.bind("<Button-1>", lambda e: 命令())
            return 标

        def _复制提示词():
            源 = self.source_path.get().strip() or "（未选择）"
            标 = self.target_path.get().strip() or "（未选择）"
            名 = 源.replace("\\", "/").rstrip("/").split("/")[-1] or "整合包"
            提示 = (
                "我在把一个 Minecraft 整合包从旧版本迁移到新版本，需要你帮我从下面的"
                "更新日志里找出【与模组相关的变更】。\n\n"
                "整合包：%s\n旧实例目录：%s\n新实例目录：%s\n\n"
                "请按这个格式输出：\n"
                "1. 新增的模组（尽量给出 modid 或中文名）\n"
                "2. 移除的模组\n"
                "3. 被替换/合并的模组（A 换成了 B）\n"
                "4. 依赖或前置发生变化的模组\n"
                "5. 需要我手动处理的注意点（配置重置、存档兼容性、存档版本）\n\n"
                "更新日志如下：\n（把更新日志粘贴在这里）\n" % (名, 源, 标))
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(提示)
                self.root.update_idletasks()
                _链接反馈()
            except Exception:
                pass

        def _链接反馈():
            try:
                复制标.config(text="已复制 ✓", fg=self.theme.get("ok_fg", "#2e7d32"))
                self.root.after(1600, lambda: 复制标.config(text="复制 AI 提示词", fg=主))
            except Exception:
                pass

        复制标 = _链接("复制 AI 提示词", _复制提示词)

        def _不再提示():
            self.pack_warning = False
            try:
                self.save_config()
            except Exception:
                pass
            try:
                框.destroy()
            except Exception:
                pass
            self._pack_warning_frame = None

        # ⚠ 这里原来是个只有「⚠️」的小方块按钮，作用是**永久关掉这条安全提醒**
        # （还会写进配置）。它和横幅自己的警告图标长得一模一样，谁也猜不到含义，
        # 又紧挨着「复制 AI 提示词」—— 手一抖就把提醒永久点没了。
        # 改成明确的文字链接：说清"点它会关掉提醒"，且与复制按钮在视觉上分开。
        关闭钮 = tk.Label(右, text="不再提示", bg=底, fg=弱色, cursor="hand2",
                       font=("微软雅黑", 8, "underline"), padx=px(4), pady=px(2),
                       bd=0, highlightthickness=0)
        关闭钮.pack(side="left")
        _悬停色 = "#f0e2b8" if not 深 else "#4a3d24"

        def _进入(_e):
            try:
                关闭钮.config(bg=_悬停色)
            except Exception:
                pass

        def _离开(_e):
            try:
                关闭钮.config(bg=底)
            except Exception:
                pass

        关闭钮.bind("<Button-1>", lambda e: _不再提示())
        关闭钮.bind("<Enter>", _进入, add="+")
        关闭钮.bind("<Leave>", _离开, add="+")
        try:
            self.create_tooltip(关闭钮, "不再提示（设置 → 外观与启动 里可以重新打开）")
        except Exception:
            pass

    def set_pack_warning(self, 开):
        """设置里开关：重新显示 / 隐藏这条提醒（立即生效）。"""
        self.pack_warning = bool(开)
        try:
            self.save_config()
        except Exception:
            pass
        self._build_pack_warning()

    def _create_modlist_page(self, parent):
        """模组清单页：每行一个 .jar 文件名。"""
        tk.Label(parent,
                 text=("每行一个 .jar 文件名（可从变更日志导入、点「添加模组」选，或直接把 jar 拖进来）。"
                       "同名匹配不区分大小写，改过名的也能对上。"),
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left").pack(anchor="w", padx=5, pady=(2, 0))
        self._pack_warning_parent = parent
        self._pack_warning_frame = None
        self._build_pack_warning()

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
        self._refresh_scan_btn_state()      # 清单为空时「扫描差异」置灰

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
            窗口秒 = 120.0
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
                # 和曲线图一致：**先填满再滚动**。数据不够一个窗口时，第一个点
                # 钉在左边（起点），曲线往右长；攒满之后才开始整体左移。
                跨度 = max(0.0, 现在 - 原始[0][0])
                基准 = min(跨度, 窗口秒)
                for ts, v in 原始:
                    位置 = (基准 - (现在 - ts)) / 窗口秒
                    x = 边 + 可用宽 * min(1.0, max(0.0, 位置))
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
            # 250ms（4fps）：曲线满窗后大约每秒左移 1px，一帧只动 0.25px，
            # 4fps 完全看不出跳；原来 50ms(20fps) 实测白吃 ~4.8% CPU
            self._net_spark_job = self.root.after(250, self._draw_net_spark)
        except Exception:
            pass

    @staticmethod
    def _window_in_front(win):
        """这个窗口此刻是不是"露在最上面"：取它中心点，看那一层的顶层窗口是不是它。

        和焦点无关，所以点浮标（把焦点抢走）也不影响判断。
        """
        try:
            u = ctypes.windll.user32
            hwnd = int(win.wm_frame(), 16)
            r = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right <= r.left or r.bottom <= r.top:
                return False
            if not u.IsWindowVisible(hwnd):
                return False
            x = (r.left + r.right) // 2
            y = (r.top + r.bottom) // 2
            at = u.WindowFromPoint(wintypes.POINT(x, y))
            if not at:
                return False
            top = u.GetAncestor(at, 2)      # GA_ROOT
            return int(top) == hwnd
        except Exception:
            return False

    def show_net_chart(self):
        """网络延迟曲线：**Modrinth 和 CurseForge 各画一条线**。

        再点一次不是"又开一个"，而是三态切换：
          · 没开 → 打开；
          · 开着但被挡住 → 提到最前（不重建，不闪）；
          · 已经在最上面 → 关掉（连点两次就是开关，不会堆窗口）。

        ⚠ 判断"是否在最上面"不能用 GetForegroundWindow()：点浮标的那一刻，浮标
        自己变成了前台窗口，图永远被判成"不在前台"，于是第二次点只会反复把它提
        起来、永远关不掉。这里用"图中心点上最上层的窗口是不是它"来判断。

        绘制要点：
          · 两个平台各一条线，各自 PCHIP 插值 + 末端缓动，互不影响；
          · 时间基准（x 轴）**两条线共用**，否则采样时刻对不齐；
          · x 轴先填满再滚动：数据不够一个窗口时左端固定在起点，攒满后整体左移；
          · 坐标轴/网格/图例画一次缓存成位图，每帧只 restore + 重画曲线。
        """
        # ---- 三态切换 ----
        try:
            win = getattr(self, "_net_chart_win", None)
            if win is not None and win.winfo_exists():
                if self._window_in_front(win):
                    win.destroy()
                    self._net_chart_win = None
                else:
                    win.deiconify()
                    win.lift()
                    try:
                        win.focus_force()
                    except Exception:
                        pass
                return
        except Exception:
            pass

        顶 = tk.Toplevel(self.root)
        self._net_chart_win = 顶
        try:
            def _清掉引用(e=None):
                if e is None or getattr(e, "widget", None) is 顶:
                    self._net_chart_win = None
            顶.bind("<Destroy>", _清掉引用, add="+")
        except Exception:
            pass

        try:
            from ui import mw_net
            平台 = list(mw_net.hosts())
        except Exception:
            平台 = ["Modrinth", "CurseForge"]

        顶.title("📈 网络延迟（Modrinth / CurseForge 分开统计）")
        try:
            顶.geometry(window_geometry(顶, 760, 430))
        except Exception:
            顶.geometry("%dx%d" % (px(760), px(430)))
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
        # (浅色主题, 深色主题) 各一套：绿 = Modrinth，橙 = CurseForge
        配色 = (("#2e7d32", "#66bb6a"), ("#ef6c00", "#ffb74d"))

        信息 = tk.Label(顶, bg=self.theme["bg"],
                        fg=self.theme.get("muted_fg", "#888888"),
                        font=("微软雅黑", 9), anchor="w", justify="left")
        信息.pack(side="bottom", fill="x", padx=px(12), pady=(0, px(8)))
        关闭钮 = create_gradient_button(顶, "关闭", lambda: _关闭())
        关闭钮.pack(side="bottom", pady=(0, px(10)))

        图 = Figure(figsize=(px(760) / 100.0, px(390) / 100.0), dpi=100, facecolor=底色)
        轴 = 图.add_subplot(111, facecolor=底色)
        画布 = FigureCanvasTkAgg(图, master=顶)
        画布.get_tk_widget().pack(side="top", fill="both", expand=True,
                                  padx=px(10), pady=(px(10), px(4)))

        轴.set_facecolor(底色)
        for s in 轴.spines.values():
            s.set_color(网格色)
        轴.tick_params(colors=前景, labelsize=9)
        轴.grid(True, color=网格色, linewidth=0.8)
        轴.set_ylabel("ms", color=前景, fontsize=9)
        try:
            图.subplots_adjust(left=0.11, right=0.98, top=0.95, bottom=0.16)
        except Exception:
            pass
        轴.set_xlabel("秒（先填满，再滚动）", color=前景, fontsize=9)

        # ---- 每个平台一条线 ----
        系列 = []
        for i, 名 in enumerate(平台):
            色 = 配色[i % len(配色)][1 if 深色 else 0]
            系列.append({
                "名": 名, "色": 色,
                "线": 轴.plot([], [], color=色, linewidth=2.2, label=名,
                              solid_capstyle="round", solid_joinstyle="round",
                              antialiased=True, zorder=3)[0],
                "填": 轴.fill_between([], [], color=色, alpha=0.10, linewidth=0,
                                     edgecolor="none", antialiased=False, zorder=2),
                "点": 轴.plot([], [], "o", color=色, markersize=3.0, zorder=4)[0],
                "尾点": 轴.plot([], [], "o", color=色, markersize=6.0, zorder=5)[0],
                "dx": None, "dy": None, "t_base": 0.0, "base_base": 0.0,
                "dx_t": None, "dy_t": None, "t_base_t": 0.0,
                "sig": None, "end_y": None, "end_from": 0.0, "end_target": None,
                "end_t0": None, "ts": [], "ys": [],
            })
        try:
            轴.legend(loc="lower left", bbox_to_anchor=(0.0, 1.005), ncol=len(平台),
                      fontsize=8, frameon=False, labelcolor=前景,
                      handlelength=2.0, borderaxespad=0.0, columnspacing=1.6)
        except Exception:
            pass

        空提示 = 轴.text(0.5, 0.5, "还没有探测数据（等几秒）", ha="center", va="center",
                        color=前景, fontsize=10, transform=轴.transAxes,
                        zorder=2, visible=False)
        十字 = 轴.axvline(0, color=前景, linewidth=0.8, alpha=0.55,
                          zorder=6, visible=False)
        提示 = 轴.text(0, 0, "", color=前景, fontsize=9, zorder=10, visible=False,
                      ha="left", va="top", clip_on=False, linespacing=1.5,
                      bbox=dict(boxstyle="round,pad=0.35", fc=提示底色,
                                ec=网格色, lw=0.8, alpha=0.96))

        动态 = tuple(a for s in 系列 for a in (s["线"], s["填"], s["点"], s["尾点"])) + \
            (空提示, 十字, 提示)
        过渡秒 = 0.35
        状态 = {"bg": None, "span": None, "ylim": None, "stop": False,
                "cap": None, "hover_x": None}
        帧间隔 = 10
        _定时器 = {"on": False}
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            _定时器["on"] = True
        except Exception:
            pass
        窗口固定 = 120.0

        def _整数档(值们):
            m = max(值们) if 值们 else 1
            return max(500.0, math.ceil(m * 1.2 / 500.0) * 500.0)

        def _上限(值们):
            """带滞回：涨了立刻跟上，只有掉到当前档位 60% 以下才缩回去。"""
            目标 = _整数档(值们)
            当前 = 状态.get("cap")
            if 当前 is None or 目标 > 当前:
                当前 = 目标
            elif 目标 < 当前 * 0.6:
                当前 = 目标
            状态["cap"] = 当前
            return 当前

        def _pchip(x, y):
            """单调三次 Hermite 插值（Fritsch–Carlson）。"""
            x = np.asarray(x, dtype=float)
            y = np.asarray(y, dtype=float)
            m = len(x)
            if m < 3:
                return x, y
            h = np.diff(x)
            if np.any(h <= 0):
                return x, y
            d = np.diff(y) / h
            g = np.zeros(m)
            g[0], g[-1] = d[0], d[-1]
            for i in range(1, m - 1):
                if d[i - 1] * d[i] <= 0:
                    g[i] = 0.0
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

        def 缓存背景(窗口, 上限):
            轴.set_xlim(0, 窗口)
            轴.set_ylim(0, 上限)
            for a in 动态:
                a.set_visible(False)
            画布.draw()
            # 缓存整张图（提示框允许画到轴外，只恢复轴区域会留残影）
            状态["bg"] = 画布.copy_from_bbox(图.bbox)
            状态["span"], 状态["ylim"] = 窗口, 上限
            for a in 动态:
                a.set_visible(False)
            for s in 系列:
                for k in ("线", "填", "点", "尾点"):
                    s[k].set_visible(True)

        def _藏提示():
            状态["hover_x"] = None

        def 帧():
            if 状态["stop"]:
                return
            # 正在拖浮标：让路（曲线图推迟 16ms，肉眼看不出来，但拖动会顺很多 ——
            # 两边都在抢 60fps 主循环时，实测拖动帧间隔 p95 会明显变差）
            if getattr(self, "_net_float_dragging", False):
                try:
                    顶.after(16, 帧)
                except Exception:
                    pass
                return
            try:
                if not 顶.winfo_exists():
                    return
                from ui import mw_net
                现在t = time.time()
                表 = mw_net.rtt_series()

                # 公共时间基准：所有平台里最早的那个样本，保证两条线时刻对齐
                最早 = None
                for s in 系列:
                    样本 = 表.get(s["名"]) or []
                    s["样本"] = 样本
                    if 样本:
                        最早 = 样本[0][0] if 最早 is None else min(最早, 样本[0][0])
                跨度 = (现在t - 最早) if 最早 is not None else 0.0
                基准 = min(跨度, 窗口固定)

                所有y = []
                for s in 系列:
                    xs, ys, ts = [], [], []
                    for t0, v in s["样本"]:
                        if 现在t - t0 > 窗口固定:
                            continue
                        xs.append(基准 - (现在t - t0))
                        ys.append(v)
                        ts.append(t0)
                    s["ts"], s["ys"] = ts, ys
                    if ys:
                        所有y.extend(ys)
                    sig = (len(ys), ts[-1] if ts else 0.0)
                    if ys and (s["sig"] != sig or s["dx"] is None):
                        s["end_from"] = s["end_y"] if s["end_y"] is not None else ys[-1]
                        s["end_target"] = ys[-1]
                        s["end_t0"] = 现在t
                        s["sig"] = sig
                        if len(xs) >= 3:
                            s["dx"], s["dy"] = _pchip(xs, ys)
                        else:
                            s["dx"] = np.asarray(xs, dtype=float)
                            s["dy"] = np.asarray(ys, dtype=float)
                        s["t_base"], s["base_base"] = 现在t, 基准

                if (状态["bg"] is None or 窗口固定 != 状态["span"]
                        or abs(上限_now() - (状态["ylim"] or 0)) > 1e-9):
                    缓存背景(窗口固定, 上限_now())

                画布.restore_region(状态["bg"])
                有数据 = False
                for s in 系列:
                    ys, ts = s["ys"], s["ts"]
                    if not ys:
                        s["线"].set_data([], [])
                        s["点"].set_data([], [])
                        s["尾点"].set_data([], [])
                        try:
                            s["填"].set_verts([])
                        except Exception:
                            pass
                        continue
                    有数据 = True
                    p = 1.0 if s["end_t0"] is None else min(
                        1.0, (现在t - s["end_t0"]) / 过渡秒)
                    过渡中 = p < 1.0 and len(ts) >= 2
                    if 过渡中:
                        缓 = 1.0 - (1.0 - p) ** 3
                        y末 = s["end_from"] + (s["end_target"] - s["end_from"]) * 缓
                        ys2 = list(ys)
                        ys2[-1] = y末
                        xs2 = [基准 - (现在t - t0) for t0 in ts]
                        if len(xs2) >= 3:
                            s["dx_t"], s["dy_t"] = _pchip(xs2, ys2)
                        else:
                            s["dx_t"] = np.asarray(xs2, dtype=float)
                            s["dy_t"] = np.asarray(ys2, dtype=float)
                        s["end_y"] = y末
                    else:
                        s["end_y"] = s["end_target"] if s["end_target"] is not None else ys[-1]

                    if 过渡中:
                        tx, ty = s["dx_t"], s["dy_t"]
                    else:
                        tx = (s["dx"] - (现在t - s["t_base"])
                              + (基准 - s.get("base_base", 基准)))
                        ty = s["dy"]
                    if len(tx):
                        tx = np.append(tx, 基准)
                        ty = np.append(ty, ty[-1])
                    s["线"].set_data(tx, ty)
                    # 面积填充：曲线和 x 轴之间（同 netmon 的 fill="tozeroy"）
                    try:
                        if len(tx) >= 2:
                            s["填"].set_verts([np.column_stack([
                                np.concatenate(([tx[0]], tx, [tx[-1]])),
                                np.concatenate(([0.0], ty, [0.0]))])])
                        else:
                            s["填"].set_verts([])
                    except Exception:
                        pass

                    mx = np.asarray(ts, dtype=float) - 现在t
                    if len(mx) <= 40:
                        s["点"].set_data(mx, np.asarray(ys, dtype=float))
                    else:
                        s["点"].set_data([], [])
                    if len(mx):
                        s["尾点"].set_data([mx[-1:], [s["end_y"]]])
                    else:
                        s["尾点"].set_data([], [])

                    轴.draw_artist(s["填"])
                    轴.draw_artist(s["线"])
                    轴.draw_artist(s["点"])
                    轴.draw_artist(s["尾点"])

                空提示.set_visible(not 有数据)
                if not 有数据:
                    轴.draw_artist(空提示)

                # ---- 悬停：竖线 + 两个平台的当前值 ----
                hx = 状态.get("hover_x")
                if hx is not None and 有数据:
                    时刻 = 现在t - (基准 - hx)
                    行 = []
                    for s in 系列:
                        ts, ys = s["ts"], s["ys"]
                        if not ts:
                            continue
                        j = min(range(len(ts)), key=lambda i: abs(ts[i] - 时刻))
                        if abs(ts[j] - 时刻) <= 4.0:
                            行.append("%s  %d ms" % (s["名"], int(ys[j])))
                    十字.set_xdata([hx, hx])
                    十字.set_visible(True)
                    轴.draw_artist(十字)
                    if 行:
                        提示.set_text(time.strftime("%H:%M:%S", time.localtime(时刻))
                                     + "\n" + "\n".join(行))
                        提示.set_position((min(hx + 窗口固定 * 0.012, 窗口固定 * 0.60),
                                          (状态["ylim"] or 500.0) * 0.95))
                        提示.set_visible(True)
                        轴.draw_artist(提示)
                    else:
                        提示.set_visible(False)
                else:
                    十字.set_visible(False)
                    提示.set_visible(False)

                画布.blit(图.bbox)

                # 统计行：只在数据变了的时候刷新（每帧改 Label 要几毫秒）
                sig_text = tuple((s["名"], s["ys"][-1] if s["ys"] else -1) for s in 系列)
                if 状态.get("sig_text") != sig_text:
                    状态["sig_text"] = sig_text
                    段 = []
                    for s in 系列:
                        ys = s["ys"]
                        if not ys:
                            段.append("%s 无数据" % s["名"])
                        elif len(ys) < 2:
                            段.append("%s 当前 %d ms" % (s["名"], int(ys[-1])))
                        else:
                            段.append("%s 当前 %d · 平均 %d · 最高 %d" % (
                                s["名"], int(ys[-1]),
                                int(sum(ys) / len(ys)), int(max(ys))))
                    信息.config(text="    ｜    ".join(段))

                间隔 = 帧间隔
                try:
                    if int(ctypes.windll.user32.GetForegroundWindow()) != int(顶.wm_frame(), 16):
                        间隔 = 33      # 不在前台时 30fps，省一半 CPU
                except Exception:
                    pass
                顶.after(间隔, 帧)
            except Exception:
                try:
                    顶.after(200, 帧)
                except Exception:
                    pass

        def 上限_now():
            """当前应有的 y 上限（带滞回），同时更新状态。"""
            值们 = [v for s in 系列 for v in s["ys"]]
            return _上限(值们) if 值们 else 500.0

        def _鼠标移动(e):
            try:
                if e.inaxes is not 轴 or e.xdata is None:
                    _藏提示()
                    return
                状态["hover_x"] = max(0.0, min(窗口固定, float(e.xdata)))
            except Exception:
                _藏提示()

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

        画布.mpl_connect("motion_notify_event", _鼠标移动)
        画布.mpl_connect("axes_leave_event", lambda _e: _藏提示())
        画布.get_tk_widget().bind("<Configure>", lambda _e: 状态.update(bg=None), add="+")
        顶.protocol("WM_DELETE_WINDOW", _关闭)

        try:
            缓存背景(窗口固定, 500.0)
        except Exception:
            pass
        帧()

    # ---------------- 桌面浮标（网络延迟小窗） ----------------
    def toggle_net_float(self):
        """开 / 关桌面浮标。"""
        try:
            if self._net_float is not None and self._net_float.alive():
                self._net_float.close()
                self.toast("📌 桌面浮标已关闭", level="ok")
            else:
                self.open_net_float()
                if self._net_float is not None and self._net_float.alive():
                    self.toast("📌 桌面浮标已显示（可拖动，双击看曲线）", level="ok")
                else:
                    self.toast("⚠️ 浮标打不开，详见日志", level="warn")
        except Exception as e:
            # 原来整段是 try/except pass：失败时连日志都没有，用户只会觉得按钮坏了
            try:
                self.log("❌ 切换浮标失败：%s" % e, level="ERROR")
            except Exception:
                pass
            self.toast("⚠️ 浮标打不开，详见日志", level="warn")

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

        self._net_float = NetFloat(
            self.root, 历史, self.show_net_chart,
            on_close=self._on_net_float_event, x=x, y=y,
            size=getattr(self, "net_float_size", 56),
            on_drag_state=self._on_float_drag_state,
            edge_fx=getattr(self, "float_edge_fx", True),
            shadow=getattr(self, "net_float_shadow", True),
            dock=getattr(self, "net_float_dock", True),
            bg=self.theme.get("bg", "#f0f0f0"))
        self.net_float_on = True
        try:
            self.save_config()
        except Exception:
            pass

    def set_float_edge_fx(self, 开):
        """设置开关：贴边时的边缘特效（光带 + 粒子）是否启用，立即生效。"""
        self.float_edge_fx = bool(开)
        f = getattr(self, "_net_float", None)
        if f is not None:
            f.edge_fx = bool(开)
            if not 开:
                try:
                    if getattr(f, "_glow", None) is not None:
                        f._glow.hide()
                except Exception:
                    pass
        try:
            self.save_config()
        except Exception:
            pass

    def _on_float_drag_state(self, 在拖):
        """拖动浮标时让曲线图暂停重绘：两边都在 60fps 抢主循环，拖动会顿。"""
        self._net_float_dragging = bool(在拖)

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

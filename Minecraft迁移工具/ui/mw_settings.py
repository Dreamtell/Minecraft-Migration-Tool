# ui/mw_settings.py
"""设置窗口：构建与所有选项回调。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from ui import button_prefs
from ui.mw_common import (
    LINK_GITHUB, LINK_MINECRAFT, _BIG_VIEW_BACKENDS, _BIG_VIEW_VIEWS, _BUTTON_GROUPS,
    _BUTTON_LABELS, _DIFF_VIEWS, _EXTRA_PRESETS, _GROUP_COLORS, _GROUP_ICONS, _LOCK_MODES,
    _RENAME_MARKERS, _SECTION_STYLE, _mix,
)
from utils import i18n
from utils import secrets
from utils.helpers import (
    DataText, OptionCards, RoundedEntry, SegmentedControl, SmoothScroller, SwitchRow,
    center_window, create_gradient_button, focus_window, set_window_icon,
)
# 日志/文案模板：trp 按位置填值（中文模式下与原 f-string 逐字一致）
from utils.i18n import trp


class SettingsMixin:
    """设置窗口：构建与所有选项回调。"""

    def open_settings(self):
        """⚙ 设置：程序级偏好按用途分成几个标签页。

        标签页：外观与启动 / 迁移与分类 / 放大查看 / 界面按钮 / 后台与退出。
        以前全塞一页，条目一多就挤成一条长条，所以改成标签页（自己画的圆角药丸标签，
        见 ui/rounded_tabs.py：选中块滑动 + 页面滑入）。
        """
        win = getattr(self, "settings_win", None)
        if win is not None and win.winfo_exists():
            focus_window(win)
            return

        win = tk.Toplevel(self.root)
        self.settings_win = win
        win.withdraw()
        win.title("⚙ 设置")
        win.configure(bg=self.theme["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        set_window_icon(win)

        # ---- 标签页容器（自己画的圆角药丸标签：ttk.Notebook 的标签是方的、也没法做动画）----
        from ui.rounded_tabs import RoundedTabs
        tabs = RoundedTabs(win, self.theme)
        tabs.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        self.settings_tabs = tabs
        内容 = []

        def 滚动页(page):
            """把标签页做成"装不下就自己滚"，返回可以塞内容的那层容器。

            为什么改成滚：窗口原来是按**最高那页**撑开的（迁移那页 816 高），于是
            设置窗变成 700x926 的一条窄柱（用户：设置窗口做成正常尺寸）。窗口给正常
            大小、页内容高了就滚，比"按内容撑"更经得住折腾 —— 窗口被拉小也不会把选项裁掉。

            滚轮走 `SmoothScroller.for_canvas`：和列表/文本框同一套逐帧插值，
            所以 `yscrollincrement` 要设成 1（一步 1 像素才能停在半行上）。
            """
            画布 = tk.Canvas(page, bg=self.theme["bg"], highlightthickness=0, bd=0)
            画布.configure(yscrollincrement=1)           # 1 像素一步：平滑滚动的前提
            条 = ttk.Scrollbar(page, orient="vertical", command=画布.yview)
            inner = tk.Frame(画布, bg=self.theme["bg"])
            窗 = 画布.create_window(0, 0, window=inner, anchor="nw")

            def 同步滚动条(第一, 最后):
                # 装得下就把滚动条收起来：短页不该白拖一条灰杠
                if float(第一) <= 0.0 and float(最后) >= 1.0:
                    if 条.winfo_ismapped():
                        条.pack_forget()
                elif not 条.winfo_ismapped():
                    条.pack(side="right", fill="y")
                条.set(第一, 最后)

            画布.configure(yscrollcommand=同步滚动条)
            画布.pack(side="left", fill="both", expand=True)
            画布.bind("<Configure>", lambda e: 画布.itemconfigure(窗, width=e.width))
            inner.bind("<Configure>", lambda e: 画布.configure(scrollregion=画布.bbox("all")))
            page._滚动画布 = 画布                          # 滚轮处理器按页找它
            # 只绑在画布本身上：内容（子控件）上的滚轮由顶层那个处理器按"当前页"转发，
            # 落在画布空白处的才由它自己接 —— 见 滚轮()，两边不会各滚一格
            page._滚动器 = SmoothScroller.for_canvas(画布, bind_widgets=[画布])
            return inner

        def tab(label):
            page = tabs.page(label)
            inner = 滚动页(page)
            内容.append(inner)                # 窗口宽度按内容算才准
            return inner

        page_look = tab("🎨 外观与启动")
        page_mig = tab("🚚 迁移与分类")
        page_view = tab("🗂 放大查看")
        page_btn = tab("🔘 界面按钮")
        page_close = tab("🚪 后台与退出")

        def section(key, parent):
            """带主色描边 + 图标的分区：返回可以往里塞内容的容器。"""
            title, accent, icon = _SECTION_STYLE[key]
            wrapper = tk.Frame(parent, bg=self.theme["bg"])
            wrapper.pack(fill="x", padx=4, pady=(10, 0))
            head = tk.Frame(wrapper, bg=self.theme["bg"])
            head.pack(fill="x")
            # 左边一个主色小色块当"图标底"，右边是主色标题
            tk.Label(head, text="  ", bg=accent, fg=accent,
                     font=("微软雅黑", 10, "bold")).pack(side="left", padx=(0, 6))
            tk.Label(head, text=f"{icon} {title}", bg=self.theme["bg"], fg=accent,
                     font=("微软雅黑", 10, "bold")).pack(side="left")
            body = tk.Frame(wrapper, bg=self.theme["bg"],
                            highlightbackground=accent, highlightthickness=1)
            body.pack(fill="x", pady=(4, 0))
            inner = tk.Frame(body, bg=self.theme["bg"])
            inner.pack(fill="x", padx=8, pady=6)
            return inner

        # ---------- 现代化选项控件（替掉 tk.Radiobutton / tk.Checkbutton）----------
        # 系统那套小圆点/小方块是原生渲染（灰底、老式字重），跟这里自绘的 UI 明显不搭。
        # 分工：
        #   · 短标签（2~9 个字的互斥项）→ SegmentedControl：一条药丸，选中段滑过去；
        #     文案里"（……）"那一截太长，塞进分段条会撑爆，所以拆出来放到下面说明行，
        #     跟着选中项变。
        #   · 整句话那么长的互斥项 → OptionCards：一行一张卡（后面 3 个锁屏模式、
        #     3 个关闭方式就是这种）。
        #   · 是/否 → SwitchRow(compact=True)，和设置里其它开关同一个样式。
        self._settings_segs = []
        self._settings_cards = []
        self._settings_sws = []
        # 名字 → 控件：同一个选项组（比如 _BIG_VIEW_BACKENDS 被两处复用）光按"值"找
        # 会找错，所以起个名，验证脚本和自己要回头取的时候都靠它。
        self._settings_seg_map = {}
        self._settings_card_map = {}
        self._settings_sw_map = {}
        self._settings_note_map = {}        # 分段组下面那行"选中项说明"

        def _拆名(文案):
            """'📋 表格视图（打开就是列表…）' → ('📋 表格视图', '打开就是列表…')"""
            文案 = str(文案)
            if "（" in 文案:
                短, _, 余 = 文案.partition("（")
                return 短.strip(), 余.rstrip("）").strip()
            return 文案, ""

        def 分段(parent, 变量, 选项, 命令, 说明_前缀="", 名=""):
            """短标签的互斥选项：分段选择 +（文案够长时）一行"选中项说明"。"""
            条 = [""]
            行 = tk.Frame(parent, bg=self.theme["bg"])
            行.pack(fill="x", pady=(4, 0))
            段 = []
            说明表 = {}
            for 值, 文案 in 选项:
                短, 长 = _拆名(文案)
                段.append((值, 短))
                说明表[值] = 长
            有说明 = any(v for v in 说明表.values())
            说明标签 = None
            if 有说明:
                说明标签 = tk.Label(parent, text="", bg=self.theme["bg"],
                                    fg=self.theme.get("muted_fg", self.theme["fg"]),
                                    font=("微软雅黑", 8), justify="left", wraplength=580)
                说明标签.pack(anchor="w", pady=(3, 0))

            def 刷新说明(值=None):
                if 说明标签 is None:
                    return
                说明标签.configure(text=(说明_前缀 + 说明表.get(值 if 值 is not None
                                                          else 变量.get(), "")))

            def 选了(值):
                变量.set(值)
                刷新说明(值)
                命令(值)

            控件 = SegmentedControl(行, self.theme, 段, value=变量.get(), command=选了)
            控件.pack(side="left")
            self._settings_segs.append(控件)
            if 名:
                self._settings_seg_map[名] = 控件
                if 说明标签 is not None:
                    self._settings_note_map[名] = 说明标签
            刷新说明(变量.get())
            条[0] = 控件
            return 控件

        def 卡片(parent, 变量, 选项, 命令, 名=""):
            """一列选择卡片。

            选项两种形状都收：
              · `(值, "标题：说明")` —— 老写法，标题取"："前那截；
              · `(值, "标题", "说明")` —— 推荐写法：标题和说明分开，**各成一整条**，
                语言层能分别翻（合成一整句的话自绘卡片劈不开，英文里还得留全角冒号）。
            """
            三列 = []
            for 项 in 选项:
                if len(项) >= 3:
                    三列.append((项[0], str(项[1]), str(项[2] or "")))
                    continue
                值, 文案 = 项[0], str(项[1])
                if "：" in 文案:
                    标题, _, 说明 = 文案.partition("：")
                else:
                    标题, 说明 = 文案, ""
                三列.append((值, 标题.strip(), 说明.strip()))

            def 选了(值):
                变量.set(值)
                命令(值)

            控件 = OptionCards(parent, self.theme, 三列, value=变量.get(), command=选了)
            控件.pack(fill="x", pady=(4, 0))
            self._settings_cards.append(控件)
            if 名:
                self._settings_card_map[名] = 控件
            return 控件

        def 开关(parent, 文字, 变量, 命令, desc="", 名=""):
            """紧凑开关，替原来的 tk.Checkbutton。"""
            壳 = {}

            def 切了():
                变量.set(壳["sw"].get())
                命令()

            控件 = SwitchRow(parent, self.theme, 文字, desc=desc, command=切了,
                             compact=True, accent="switch_on")
            壳["sw"] = 控件
            控件.set(bool(变量.get()))
            self._settings_sws.append(控件)
            if 名:
                self._settings_sw_map[名] = 控件
            return 控件

        # ---------- 外观与启动 ----------
        box1 = section("look", page_look)
        self.settings_theme_var = tk.StringVar(value=self.current_theme)
        row_theme = tk.Frame(box1, bg=self.theme["bg"])
        row_theme.pack(fill="x")
        tk.Label(row_theme, text="主题：", bg=self.theme["bg"],
                 fg=self.theme["fg"], font=("微软雅黑", 9)).pack(side="left")
        分段(row_theme, self.settings_theme_var,
             (("light", "浅色"), ("dark", "深色")),
             lambda v: self.choose_theme(v), 名="theme")

        # 界面语言（中文 / English）。文案是**启动时定下来**的（见 utils/i18n.py），
        # 这里只负责记下选择，并提示重启 —— 界面是一次性建好的，运行中换不干净。
        self.settings_lang_var = tk.StringVar(value=i18n.language())
        row_lang = tk.Frame(box1, bg=self.theme["bg"])
        row_lang.pack(fill="x", pady=(10, 0))
        tk.Label(row_lang, text="界面语言：", bg=self.theme["bg"],
                 fg=self.theme["fg"], font=("微软雅黑", 9)).pack(side="left")
        分段(row_lang, self.settings_lang_var, i18n.CHOICES,
             lambda v: self._set_language(v), 名="lang")

        self.settings_splash_var = tk.BooleanVar(value=self.splash_enabled)
        开关(box1, "启用启动动画（下次启动程序生效）", self.settings_splash_var,
             self._toggle_splash, 名="splash").pack(fill="x", pady=(6, 0))

        # 点窗口空白处要不要顺手退出「主界面编辑」（默认开）。
        # 开着：编辑时随手点一下背景就回只读；关掉：只能用编辑开关自己关，
        # 免得手滑点到背景就把编辑状态丢了。
        self.settings_blank_exit_sw = SwitchRow(
            box1, self.theme, "点空白处退出主界面编辑",
            desc="关闭后只能用编辑开关自己关",
            command=self._toggle_blank_exit_edit, compact=True,
            accent="switch_on")
        self.settings_blank_exit_sw.pack(fill="x", pady=(10, 0))
        self.settings_blank_exit_sw.set(self.blank_exit_edit.get())

        # ---------- 迁移行为 ----------
        box_m = section("migrate", page_mig)
        self.settings_rename_var = tk.BooleanVar(value=self.rename_migrated_mods)
        开关(box_m, "复制过去的模组加标记前缀（方便在目标 mods 里一眼认出）",
             self.settings_rename_var, self._toggle_rename_marker, 名="rename").pack(fill="x")
        row_mark = tk.Frame(box_m, bg=self.theme["bg"])
        row_mark.pack(fill="x", pady=(4, 0))
        tk.Label(row_mark, text="标记：", bg=self.theme["bg"], fg=self.theme["fg"],
                 font=("微软雅黑", 9)).pack(side="left")
        self.settings_marker_var = tk.StringVar(value=self.rename_marker)
        分段(row_mark, self.settings_marker_var,
             tuple((mark, mark) for mark in _RENAME_MARKERS),
             self._set_rename_marker, 名="marker")
        self.settings_marker_preview = tk.Label(
            box_m, text="", bg=self.theme["bg"],
            fg=self.theme.get("muted_fg", self.theme["fg"]), font=("微软雅黑", 8))
        self.settings_marker_preview.pack(anchor="w", pady=(4, 0))
        self._update_marker_preview()

        # 正式迁移前的二次确认（默认开；关掉就点了按钮直接开跑）
        self.settings_confirm_sw = SwitchRow(
            box_m, self.theme, "正式迁移前再确认一次",
            command=self._toggle_confirm_migrate, compact=True, accent="switch_on")
        self.settings_confirm_sw.pack(fill="x", pady=(10, 0))
        self.settings_confirm_sw.set(self.confirm_migrate.get())

        # ---------- 模组分类标签 ----------
        box_t = section("tags", page_mig)
        self.settings_tags_var = tk.BooleanVar(value=getattr(self, "online_tags", False))
        开关(box_t, "联网获取真实分类（Modrinth；默认关闭）",
             self.settings_tags_var, self._toggle_online_tags, 名="tags").pack(fill="x")
        tk.Label(box_t,
                 text="关闭时按关键词推测（标注“推测”的就是它）。开启后扫描会在后台联网查询，"
                      "结果缓存到本地；断网或匹配不到时自动沿用推测结果。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w", pady=(4, 0))
        self.settings_tag_cache_lbl = DataText(
            box_t, self.theme, [("本地分类缓存：", "muted_fg"),
                                ("0", "data_num_fg"),
                                (" 条", "muted_fg")],
            font=("微软雅黑", 8))
        self.settings_tag_cache_lbl.pack(anchor="w", pady=(2, 0))
        self._update_tag_cache_label()

        # ---------- CurseForge API Key（可选；key 由用户自己填，程序不携带）----------
        # 说明写这么多是有意的：用户得知道 ① 不填也能用 ② key 存在哪儿
        # ③ 程序不会把它写进日志 / 报错 / 配置文件 —— 这三点决定了他敢不敢填。
        tk.Label(box_t, text="CurseForge API Key（可选）",
                 bg=self.theme["bg"], fg=self.theme.get("accent", self.theme["fg"]),
                 font=("微软雅黑", 9, "bold")).pack(anchor="w", pady=(14, 0))
        cf_row = tk.Frame(box_t, bg=self.theme["bg"])
        cf_row.pack(fill="x", pady=(4, 0))
        self.settings_cf_key_var = tk.StringVar(value="")
        # 留个引用：验证脚本要确认它是 `show="•"`（截屏时也只会看到点，看不到 key）
        self.settings_cf_entry = RoundedEntry(cf_row, self.theme, chars=34, height=30,
                                              textvariable=self.settings_cf_key_var,
                                              show="•")
        self.settings_cf_entry.pack(side="left")
        create_gradient_button(cf_row, "保存", self._save_cf_key,
                               colors=("#00897b", "#26a69a"), width=64, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left", padx=(8, 0))
        create_gradient_button(cf_row, "测试", self._test_cf_key,
                               colors=("#42a5f5", "#64b5f6"), width=64, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left", padx=6)
        create_gradient_button(cf_row, "清除", self._clear_cf_key,
                               colors=("#e53935", "#c62828"), width=64, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left", padx=6)
        self.settings_cf_lbl = DataText(box_t, self.theme, [("", "muted_fg")],
                                        font=("微软雅黑", 8))
        self.settings_cf_lbl.pack(anchor="w", pady=(4, 0))
        tk.Label(box_t,
                 text=i18n.trf(
                     "不填也能用：程序内置了一把共享 Key 时直接用它；填了就用你自己的配额"
                     "（更稳，共享那把被限流时你还能用）。\n"
                     "你填的 Key 只存在这里：{path}，和主配置分开。\n"
                     "它不会进日志、报错或临时文件；输入框粘过就清空，保存后只显示末 4 位。",
                     path=secrets.where_text()),
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w",
                                                                          pady=(2, 0))
        self._update_cf_key_label()

        # ---------- 任务与锁定 ----------
        box_lock = section("lock", page_mig)
        self.settings_lock_var = tk.StringVar(value=getattr(self, "lock_mode", "all"))
        卡片(box_lock, self.settings_lock_var, _LOCK_MODES, self._set_lock_mode, 名="lock")
        tk.Label(box_lock,
                 text="三个选项都只是「盖不盖遮罩」的区别：迁移期间所有操作按钮一律禁用，"
                      "执行日志始终留着，方便看进度。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w",
                                                                          pady=(4, 0))
        # 跑完之后的完成态：总结 + 边框转绿，是按键关闭还是自动关闭
        self.settings_lock_key_sw = SwitchRow(
            box_lock, self.theme, "迁移完成后按任意键关闭",
            command=self._toggle_lock_wait_key, compact=True, accent="switch_on")
        self.settings_lock_key_sw.pack(fill="x", pady=(10, 0))
        self.settings_lock_key_sw.set(getattr(self, "lock_wait_key", True))
        tk.Label(box_lock,
                 text="跑完不会“啪”地消失：锁屏里先打印迁移总结、边框从红渐变成绿"
                      "（有失败则变橙）。开启时停在完成态等你按任意键（或点一下）；"
                      "关闭则 2 秒后自动收起。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w",
                                                                          pady=(4, 0))

        # ---------- 携带什么，全在「其它文件」清单里说了算 ----------
        # 以前这里有一组「默认携带的目录」勾选框：勾上的目录在迁移那一刻被**自动**
        # 并进临时清单。用户要求"一切都要自己选"，所以那套删了 —— 现在清单是唯一依据，
        # 常带的目录去「其它文件」页用「＋ 常用目录」加进去（看得见、删得掉、带备份策略）。
        box_extra = section("extras", page_mig)
        tk.Label(box_extra,
                 text="迁移只带「其它文件」清单里列出的东西（还有模组 / config / 存档这三个清单）。\n"
                      "清单是唯一依据：不在这里的一律不动。常用目录在「其它文件」页用"
                      "「＋ 常用目录」加，加完能看见、能改、能删。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w")
        self.settings_extra_lbl = tk.Label(
            box_extra, text="", bg=self.theme["bg"],
            fg=self.theme.get("muted_fg", self.theme["fg"]),
            font=("微软雅黑", 8), justify="left", wraplength=580)
        self.settings_extra_lbl.pack(anchor="w", pady=(6, 0))
        self._update_extra_defaults_label()

        # ---------- 放大查看窗口用什么实现 ----------
        box_view = section("view", page_view)
        # 总开关：关掉之后主进程完全不加载 Qt（出问题时先把这条打开来定位）
        self.settings_qt_sw = SwitchRow(
            box_view, self.theme, "启用 PySide6 窗口",
            desc="关掉 = 纯 Tk 模式：主进程不再加载 Qt（重启后生效）",
            command=self._on_qt_switch, accent="switch_on")
        self.settings_qt_sw.pack(fill="x", pady=(0, 8))
        self.settings_qt_sw.set(getattr(self, "qt_enabled", True))
        self.settings_view_var = tk.StringVar(value=getattr(self, "big_view_backend", "qt"))
        _qt_ok, _qt_why = self._qt_available()
        分段(box_view, self.settings_view_var, _BIG_VIEW_BACKENDS,
             self._set_big_view_backend, 名="view")
        tk.Label(box_view,
                 text=i18n.trf("PySide6 当前{state}。{why}",
                               state=i18n.tr("可用" if _qt_ok else "不可用"),
                               why=_qt_why),
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w",
                                                                          pady=(4, 0))
        tk.Label(box_view, text="打开时用哪个视图：", bg=self.theme["bg"],
                 fg=self.theme["fg"], font=("微软雅黑", 9)).pack(anchor="w", pady=(10, 2))
        self.settings_view_mode_var = tk.StringVar(
            value=getattr(self, "big_view_view", "table"))
        分段(box_view, self.settings_view_mode_var, _BIG_VIEW_VIEWS,
             self._set_big_view_view, 名="view_mode")

        # ---------- 差异窗口用哪个实现 ----------
        tk.Label(box_view, text="差异扫描窗口用哪个实现：", bg=self.theme["bg"],
                 fg=self.theme["fg"], font=("微软雅黑", 9)).pack(anchor="w", pady=(10, 2))
        self.settings_diff_backend_var = tk.StringVar(
            value=getattr(self, "diff_backend", "qt"))
        分段(box_view, self.settings_diff_backend_var, _BIG_VIEW_BACKENDS,
             self._set_diff_backend, 名="diff_backend")
        tk.Label(box_view, text="差异窗口打开时用哪个视图：", bg=self.theme["bg"],
                 fg=self.theme["fg"], font=("微软雅黑", 9)).pack(anchor="w", pady=(10, 2))
        self.settings_diff_view_var = tk.StringVar(
            value=getattr(self, "diff_view", "table"))
        分段(box_view, self.settings_diff_view_var, _DIFF_VIEWS, self._set_diff_view, 名="diff_view")
        tk.Label(box_view,
                 text="两个窗口默认都用 PySide6；如果遇到窗口相关的异常，可以把它们切回"
                      "经典 Tk 实现（功能和数据完全一样，只是观感旧一些）。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w",
                                                                          pady=(2, 0))

        # ---------- 界面按钮 ----------
        box2 = section("buttons", page_btn)
        tree_wrap = tk.Frame(box2, bg=self.theme["bg"])
        tree_wrap.pack(fill="both", expand=True)
        self.settings_btn_tree = ttk.Treeview(
            tree_wrap, columns=("状态",), show="tree headings", height=8,
            selectmode="browse")
        self.settings_btn_tree.heading("#0", text="按钮")
        self.settings_btn_tree.heading("状态", text="状态")
        self.settings_btn_tree.column("#0", width=380, anchor="w")
        self.settings_btn_tree.column("状态", width=90, anchor="center")
        sb = ttk.Scrollbar(tree_wrap, orient="vertical",
                           command=self.settings_btn_tree.yview)
        self.settings_btn_tree.configure(yscrollcommand=sb.set)
        self.settings_btn_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.settings_btn_tree.bind("<Double-1>", self._on_button_tree_double_click)
        self._smooth(self.settings_btn_tree, rows=True)

        row_btn = tk.Frame(box2, bg=self.theme["bg"])
        row_btn.pack(fill="x", pady=(6, 0))
        create_gradient_button(row_btn, "显示 / 隐藏", self._toggle_selected_button,
                               colors=("#00acc1", "#26c6da"), width=110, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left", padx=(0, 6))
        create_gradient_button(row_btn, "↑ 上移", lambda: self._move_selected_button(-1),
                               colors=("#42a5f5", "#64b5f6"), width=80, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left", padx=6)
        create_gradient_button(row_btn, "↓ 下移", lambda: self._move_selected_button(1),
                               colors=("#42a5f5", "#64b5f6"), width=80, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left", padx=6)
        create_gradient_button(row_btn, "恢复默认", self.reset_button_layout,
                               colors=("#757575", "#9e9e9e"), width=100, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left", padx=6)
        tk.Label(box2, text="双击一行也能切换显示/隐藏；顺序只在同一排内调整。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8)).pack(anchor="w", pady=(4, 0))
        tk.Label(box2, text="主界面的按钮改完立刻生效；放大查看 / 日志放大查看这些窗口里的"
                            "按钮，是下次打开那个窗口时生效。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=520).pack(anchor="w",
                                                                           pady=(2, 0))

        # ---------- 关闭与后台 ----------
        box3 = section("close", page_close)
        self.settings_close_var = tk.StringVar(
            value=getattr(self, "close_action", "ask"))
        卡片(box3, self.settings_close_var,
             # 同上：三元组，标题/说明分开，免得自绘卡片劈不开或英文里留全角冒号
             (("tray", "收进系统托盘", "程序继续在后台跑"),
              ("exit", "直接退出程序", ""), ("ask", "每次问我", "")),
             self.set_close_action, 名="close")
        self.settings_silent_var = tk.BooleanVar(value=self.silent_background)
        开关(box3, "后台静默执行任务（不弹进度/结果窗口，完成后系统通知）",
             self.settings_silent_var, self._toggle_silent, 名="silent").pack(fill="x", pady=(6, 0))

        # ---------- 快捷链接（放"外观与启动"页最下面）----------
        box4 = section("links", page_look)
        row_link = tk.Frame(box4, bg=self.theme["bg"])
        row_link.pack(fill="x")
        create_gradient_button(row_link, "🌐 Minecraft 官网",
                               lambda: self.open_link(LINK_MINECRAFT),
                               colors=("#43a047", "#66bb6a"), width=150, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left", padx=(0, 8))
        create_gradient_button(row_link, "🐙 GitHub 仓库",
                               lambda: self.open_link(LINK_GITHUB),
                               colors=("#455a64", "#78909c"), width=150, height=28,
                               font=("微软雅黑", 9, "bold")).pack(side="left")
        tk.Label(box4, text=LINK_GITHUB + i18n.tr("\n欢迎反馈问题或提交建议。"),
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left").pack(anchor="w", pady=(4, 0))

        row = tk.Frame(win, bg=self.theme["bg"])
        row.pack(fill="x", padx=14, pady=14)
        create_gradient_button(row, "关闭", win.destroy,
                               colors=("#e53935", "#c62828"),
                               width=90, height=30,
                               font=("微软雅黑", 9, "bold")).pack(side="right")

        self._refresh_button_tree()
        self._theme_settings_tree()

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.update_idletasks()

        # 滚轮：滚当前页的内容（走 SmoothScroller 逐帧插值，和列表一个手感）。
        # 列表/文本框自己有滚动的，让给它们；落在画布空白处的由画布自己的滚动器接，
        # 这里就不要再喂一次（否则一格滚两格）。
        def 滚轮(ev):
            try:
                if ev.widget.winfo_class() in ("Treeview", "Text", "Listbox"):
                    return
            except Exception:
                pass
            page = tabs.current_page()
            if page is None:
                return
            画布 = getattr(page, "_滚动画布", None)
            if 画布 is not None and ev.widget is 画布:
                return
            滚动器 = getattr(page, "_滚动器", None)
            if 滚动器 is not None:
                滚动器.wheel(ev)

        win.bind("<MouseWheel>", 滚轮)

        # 正常尺寸：宽度按最宽的那页内容给，高度给一个正常值（原来按"最高那页"撑成
        # 700x926 的窄柱 —— 迁移那页 816 高，比别的窗口都高）；装不下的页自己滚。
        # 同时钳到屏幕内：center_window 只居中不裁剪，太高就会顶出屏幕下沿。
        try:
            pw = max(w.winfo_reqwidth() for w in 内容) + 100       # 内容 + 边距 + 滚动条
        except Exception:
            pw = 0
        # ⚠ 页签那排是**自绘**的（画布药丸），宽度只按标签文字算 —— 英文标签更长，
        # 不算进来的话窗口会按内容定宽、把页签裁掉（用户报过"设置界面标签溢出"）
        try:
            win.update_idletasks()
            条宽 = tabs.bar.winfo_reqwidth() + 24 + 28        # 左右 padx + 余量
            pw = max(pw, 条宽)
        except Exception:
            pass
        屏宽, 屏高 = win.winfo_screenwidth(), win.winfo_screenheight()
        窗宽 = max(660, min(max(pw, 700), 屏宽 - 120))
        窗高 = max(480, min(640, 屏高 - 140))
        win.resizable(True, True)
        win.minsize(min(660, 窗宽), 460)
        center_window(win, 窗宽, 窗高)
        win.deiconify()
        focus_window(win)
        return win                                  # 调用方/测试要拿它

    # ---------- 设置窗口里的按钮列表 ----------
    def _refresh_button_tree(self):
        """重建按钮列表：分组行 + 每个按钮一行（含显示状态）。"""
        tree = getattr(self, "settings_btn_tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
        except Exception:
            return
        hidden = set(self.hidden_buttons or ())
        selected = None
        try:
            sel = tree.selection()
            if sel:
                selected = sel[0]
        except Exception:
            pass
        tree.delete(*tree.get_children())
        for gkey, glabel, _side in _BUTTON_GROUPS:
            # ⚠ 树行文字是**拼出来的**（缩进 + 标签 / 图标 + 组名），语言层按整串查表
            # 对不上 —— 得在拼之前把"部件"过一遍 tr（用户报过这页整棵没翻）
            parent = tree.insert("", "end", iid=f"grp:{gkey}",
                                 text=" %s %s" % (_GROUP_ICONS.get(gkey, ""),
                                                  i18n.tr(glabel)),
                                 values=("",), open=True, tags=(f"grp_{gkey}",))
            for key in self._resolved_order(gkey):
                # 主界面那几排看控件登记表；窗口工具栏的按钮（放大查看/日志放大查看）
                # 不在登记表里 —— 它们是那个窗口打开时现建的，按分组认就行
                if key not in self._btn_widgets and button_prefs.group_of(key) != gkey:
                    continue
                is_hidden = key in hidden
                tree.insert(parent, "end", iid=f"btn:{key}",
                            text="   " + i18n.tr(_BUTTON_LABELS.get(key, key)),
                            values=(i18n.tr("☐ 隐藏") if is_hidden else i18n.tr("☑ 显示"),),
                            tags=("hidden",) if is_hidden else ())
        if selected:
            try:
                tree.selection_set(selected)
                tree.see(selected)
            except Exception:
                pass

    def _theme_settings_tree(self):
        """列表配色：每一排分组自己的主色，隐藏行标红。

        Treeview 的 tag 颜色不跟着主题走，所以换主题时要重算一遍。
        浅色主题往主题底色上混出淡彩，深色主题混出暗彩，同一套规则。
        """
        tree = getattr(self, "settings_btn_tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
            base = self.theme.get("ttk_bg", self.theme["bg"])
            dark = (self.current_theme == "dark")
            for gkey, _glabel, _side in _BUTTON_GROUPS:
                color = _GROUP_COLORS.get(gkey, "#78909c")
                # 深色主题往后混出暗彩、前景调亮；浅色主题混出淡彩、前景压暗
                bg = _mix(color, base, 0.78 if dark else 0.80)
                fg = _mix(color, "#ffffff" if dark else "#000000",
                          0.35 if dark else 0.30)
                tree.tag_configure(f"grp_{gkey}", background=bg, foreground=fg,
                                   font=("微软雅黑", 9, "bold"))
            tree.tag_configure("hidden", foreground=self.theme["fail_fg"])
        except Exception:
            pass

    def _selected_button_key(self):
        tree = getattr(self, "settings_btn_tree", None)
        if tree is None:
            return None
        try:
            sel = tree.selection()
        except Exception:
            return None
        if not sel:
            return None
        iid = sel[0]
        if not iid.startswith("btn:"):
            return None
        return iid[4:]

    def _toggle_selected_button(self):
        key = self._selected_button_key()
        if key is None:
            messagebox.showinfo("提示", "请先在列表里选中一个按钮。", parent=self.settings_win)
            return
        self.set_button_hidden(key, key not in set(self.hidden_buttons or ()))

    def _move_selected_button(self, delta):
        key = self._selected_button_key()
        if key is None:
            messagebox.showinfo("提示", "请先在列表里选中一个按钮。", parent=self.settings_win)
            return
        if not self.move_button(key, delta):
            self.log("ℹ️ 已经在所在那一排的边界了，无法继续移动", level="INFO", save=False)

    def _on_button_tree_double_click(self, event):
        key = self._selected_button_key()
        if key is not None:
            self.set_button_hidden(key, key not in set(self.hidden_buttons or ()))

    def _set_language(self, 值):
        """切换界面语言：写进配置 + 提示重启。

        界面是一次性建好的（见 utils/i18n.py 顶部说明），运行中切不干净，所以这里只
        记住选择、并把**之后新开的窗口**（对话框等）变成新语言 —— 主界面要重启才换。
        """
        旧 = i18n.language()
        try:
            值 = i18n.set_language(值)
        except Exception:
            return
        # 只有真的变了才落盘：验证脚本会逐个点设置里的控件（包括这一段），
        # 没变也写一遍等于平白改动配置。
        if 值 != 旧:
            try:
                self.save_config()
            except Exception:
                pass
        名 = dict(i18n.CHOICES).get(值, 值)
        self.log(trp("🌐 界面语言已设为「{0}」—— 重启程序后完全生效（此后新打开的窗口会立刻用新语言）", 名), level="INFO", save=False)

    def _toggle_splash(self):
        """启动动画开关：app.py 在创建闪屏前会直接读配置文件。"""
        self.splash_enabled = bool(self.settings_splash_var.get())
        self.save_config()
        self.log(trp("🎬 启动动画已{0}（下次启动程序生效）",
                     i18n.tr('启用' if self.splash_enabled else '关闭')),
                 level="INFO", save=False)

    # ---------- 迁移标记 ----------
    def _toggle_rename_marker(self):
        """是否给复制过去的模组加前缀标记。"""
        self.rename_migrated_mods = bool(self.settings_rename_var.get())
        self._update_marker_preview()
        self.save_config()
        # 注意这里两段是拼起来的：f-string 那一段没法机械改成 trp，
        # 而且「开启/关闭」这种**拼进模板里的词**也得自己过一遍 i18n.tr。
        self.log(trp("🏷️ 模组迁移标记已{0}",
                     i18n.tr('开启' if self.rename_migrated_mods else '关闭'))
                 + (trf("（前缀「{marker}」）", marker=self.rename_marker)
                    if self.rename_migrated_mods else ""),
                 level="INFO", save=False)

    def _set_rename_marker(self, mark):
        """换一个标记符号。"""
        self.rename_marker = mark or "★"
        self._update_marker_preview()
        self.save_config()
        self.log(trp("🏷️ 迁移标记符号已改为「{0}」", self.rename_marker), level="INFO", save=False)

    def _update_marker_preview(self):
        """给用户看一眼实际效果（关着的时候也显示，方便先挑）。"""
        lbl = getattr(self, "settings_marker_preview", None)
        if lbl is None:
            return
        try:
            state = i18n.tr("已开启" if getattr(self, "rename_migrated_mods", False)
                            else "未开启")
            # 动态文案：**模板**进词典（f-string 拼出来的串在词典里对不上，见 utils/i18n.py）
            lbl.config(text=i18n.trf("效果（{state}）：{marker} create-1.20.1-6.0.9.jar",
                                     state=state, marker=self.rename_marker))
        except Exception:
            pass

    def _set_lock_mode(self, value):
        """迁移时怎么锁主界面（只影响"盖不盖遮罩"，按钮一律禁用）。"""
        self.lock_mode = value if value in ("all", "real", "off") else "all"
        self.save_config()
        text = dict(_LOCK_MODES).get(self.lock_mode, self.lock_mode)
        self.log(trp("🔒 迁移锁定方式已改为：{0}", text), level="INFO", save=False)

    def _toggle_lock_wait_key(self):
        """完成态是"按任意键关闭"还是"自动关闭"。"""
        try:
            self.lock_wait_key = bool(self.settings_lock_key_sw.get())
        except Exception:
            self.lock_wait_key = bool(getattr(self, "lock_wait_key", True))
        self.save_config()
        self.log("⌨️ 迁移完成后的收尾方式已改为：%s"
                 % ("按任意键关闭" if self.lock_wait_key else "2 秒后自动关闭"),
                 level="INFO", save=False)

    # ---- 「默认携带的目录」（设置里勾选，迁移时自动并进清单） ----

    # ---------- 「其它文件」清单 = 唯一依据 ----------
    def _并入旧的默认携带目录(self):
        """把老配置里勾过的「默认携带的目录」并进「其它文件」清单（只做一次）。

        那套勾选机制（迁移时自动并进临时清单）已经删了，统一成"清单里有什么就带什么"。
        但用户原本会带的东西不能因为改机制就悄悄不带了 —— 所以第一次跑新版本时把它们
        写进清单，并清掉 extra_defaults；之后用户手动删掉的条目不会又被塞回来。
        """
        旧的 = self.config.get("extra_defaults") or []
        合法 = {k: 条目 for k, 条目, _ in _EXTRA_PRESETS}
        条目们 = [合法[k] for k in 旧的 if k in 合法]
        if not 条目们:
            return
        已有 = {x.strip().replace("\\", "/").rstrip("/").lower()
                for x in self.extra_text.get("1.0", "end-1c").splitlines() if x.strip()}
        要加 = [e for e in 条目们
                if e.replace("\\", "/").rstrip("/").lower() not in 已有]
        现有 = self.extra_text.get("1.0", "end-1c")
        if 要加:
            if 现有 and not 现有.endswith("\n"):
                现有 += "\n"
            self.extra_text.delete("1.0", tk.END)
            self.extra_text.insert("1.0", 现有 + "".join(e + "\n" for e in 要加))
            self.extra_text.edit_reset()
            try:
                self._update_text_states()
            except Exception:
                pass
        self.config["extra_defaults"] = []
        self.save_config()
        self.log("📦 原先勾选的「默认携带目录」已并入「其它文件」清单（%d 项：%s）。"
                 "以后**清单就是唯一依据** —— 从清单里删掉就不会再带。"
                 % (len(要加), "、".join(要加) if 要加 else "都在清单里了"),
                 level="INFO", save=False)

    # 以前这里还有一套「默认携带的目录」：勾选框里的目录会在迁移那一刻被自动并进临时
    # 清单（刻意不写回用户清单）。用户要求"之前默认带的东西现在都要自己选"，所以那套
    # 删了 —— 现在**清单里有什么就带什么**，常带的目录去清单页用「＋ 常用目录」加。
    def _extra_list_status(self, src=None):
        """清单里哪些条目在源实例里真实存在。返回 (存在, 缺失)。"""
        条目们 = [x.strip() for x in
                 self.extra_text.get("1.0", "end-1c").splitlines() if x.strip()]
        源 = src if src is not None else self.source_path.get().strip()
        if not 源:
            return [], 条目们
        base = Path(源)
        有, 缺 = [], []
        for 条目 in 条目们:
            try:
                在 = (base / 条目.replace("\\", "/").rstrip("/")).exists()
            except Exception:
                在 = False
            (有 if 在 else 缺).append(条目)
        return 有, 缺

    def _update_extra_defaults_label(self):
        """设置页那行状态：清单里现在有几条、源目录里找得到几条。"""
        lbl = getattr(self, "settings_extra_lbl", None)
        if lbl is None:
            return
        条目们 = [x.strip() for x in
                 self.extra_text.get("1.0", "end-1c").splitlines() if x.strip()]
        if not 条目们:
            文本 = i18n.tr("当前清单是空的：迁移时只带模组 / config / 存档，别的一律不动。")
        elif not self.source_path.get().strip():
            文本 = i18n.trf("清单里 {n} 条；选了源整合包目录后才能显示哪些实际存在。",
                            n=len(条目们))
        else:
            有, 缺 = self._extra_list_status()
            文本 = i18n.trf("清单里 {n} 条，源目录里找得到 {m} 条",
                            n=len(条目们), m=len(有))
            if 缺:
                文本 += i18n.trf("（找不到：{names}{more}）",
                                 names=("、".join(缺[:4])),
                                 more=i18n.tr(" 等") if len(缺) > 4 else "")
            else:
                文本 += i18n.tr("，这次都会带上 ✅")
        try:
            lbl.configure(text=文本)
        except Exception:
            pass

    def _set_qt_enabled(self, value):
        """总开关：关掉之后主进程完全不碰 Qt（放大查看/差异窗口都走 Tk 版）。

        排查用：如果关掉后那个 GIL 致命错误不再出现，问题就锁定在"Tk 与 Qt 同进程"。
        """
        self.qt_enabled = bool(value)
        self._qt_ok_cache = None
        self.save_config()
        self.log("🅆 PySide6 %s" % ("已启用" if self.qt_enabled else
                                    "已关闭（纯 Tk 模式，重启后生效）"),
                 level="INFO", save=False)

    def _on_qt_switch(self):
        """设置页那个总开关：先同步变量再应用（自绘开关只翻自己的状态）。"""
        try:
            self._set_qt_enabled(self.settings_qt_sw.get())
        except Exception:
            return

    def _set_big_view_backend(self, value):
        """放大查看窗口换实现（下一次打开生效）。"""
        self.big_view_backend = value if value in ("qt", "tk") else "qt"
        self.save_config()
        used_qt = self.big_view_backend == "qt"
        self.log("🗂 放大查看窗口已切换为：%s" % ("PySide6 试点窗口" if used_qt else "经典 Tk 窗口"),
                 level="INFO", save=False)
        if used_qt:
            ok, why = self._qt_available()
            if not ok:
                self.log("⚠ " + why, level="WARNING", save=False)

    def _set_diff_backend(self, value):
        """差异窗口用哪个实现（下一次扫描生效）。"""
        self.diff_backend = value if value in ("qt", "tk") else "qt"
        self.save_config()
        self.log("🧩 差异窗口已切换为：%s"
                 % ("PySide6 窗口" if self.diff_backend == "qt" else "经典 Tk 窗口"),
                 level="INFO", save=False)
        if self.diff_backend == "qt":
            ok, why = self._qt_available()
            if not ok:
                self.log("⚠ " + why, level="WARNING", save=False)

    def _set_big_view_view(self, value):
        """放大查看窗口默认用哪个视图（下一次打开生效）。"""
        self.big_view_view = value if value in ("table", "cards") else "table"
        self.save_config()
        self.log("🗂 放大查看窗口默认视图：%s"
                 % ("卡片视图" if self.big_view_view == "cards" else "表格视图"),
                 level="INFO", save=False)

    def _set_diff_view(self, value):
        """模组差异窗口默认用哪个视图（下一次打开生效）。"""
        self.diff_view = value if value in ("table", "cards") else "table"
        self.save_config()
        self.log("🧩 模组差异窗口默认视图：%s"
                 % ("卡片视图" if self.diff_view == "cards" else "列表视图"),
                 level="INFO", save=False)

    def _toggle_online_tags(self):
        """联网分类开关：开启后扫描会在后台去 Modrinth 取真实分类。"""
        self.online_tags = bool(self.settings_tags_var.get())
        self.save_config()
        self._update_tag_cache_label()
        if self.online_tags:
            self.log("🌐 联网分类已开启：重开一次“放大查看”就会在后台查询真实分类"
                     "（结果会缓存到本地）", level="INFO", save=False)
        else:
            self.log("🌐 联网分类已关闭：改回按关键词推测分类", level="INFO", save=False)

    def _update_tag_cache_label(self):
        """显示本地分类缓存条数，让用户知道「查过一次就不会再联网」。"""
        lbl = getattr(self, "settings_tag_cache_lbl", None)
        if lbl is None:
            return
        try:
            from core.mod_search import tag_cache_size, TAG_CACHE_FILE
            # 数据段单独着色：条数是数字（紫），路径是路径（蓝），说明文字压灰
            lbl.set_all([("本地分类缓存：", "muted_fg"),
                         (f"{tag_cache_size()}", "data_num_fg"),
                         (" 条（", "muted_fg"),
                         (f"{TAG_CACHE_FILE}", "data_fg"),
                         ("，删掉它会重新联网查）", "muted_fg")])
        except Exception:
            pass

    # ---------- CurseForge API Key（可选）----------
    # 存 / 取 / 脱敏全在 utils/secrets.py；这里只负责界面，不碰明文之外的东西。
    def _update_cf_key_label(self):
        """刷新那一行 Key 状态：只显示"末 4 位 + 指纹"，不显示 key 本身。"""
        lbl = getattr(self, "settings_cf_lbl", None)
        if lbl is None:
            return
        try:
            文本 = secrets.status_text()
            色 = "ok_fg" if secrets.has_key() else "muted_fg"
        except Exception:
            文本, 色 = "读取失败", "fail_fg"
        try:
            lbl.set_all([("CurseForge：", "muted_fg"), (文本, 色)])
        except Exception:
            pass

    def _save_cf_key(self):
        """把输入框里的 key 存进密钥文件（不进主配置、不进日志、不进仓库）。"""
        框 = getattr(self, "settings_cf_key_var", None)
        原始 = 框.get() if 框 is not None else ""
        if not secrets.clean(原始):
            self.log("ℹ️ 输入框是空的：没有保存任何东西（要删掉已保存的 Key 请点「清除」）。",
                     level="INFO", save=False)
            return
        if secrets.set_key(原始):
            # 存完立刻清空输入框：明文没必要一直摆在界面上，也没必要留在内存里
            框.set("")
            self.log("🔑 CurseForge API Key 已保存：%s（指纹 %s）"
                     % (secrets.mask(), secrets.fingerprint()), level="INFO")
            self._update_cf_key_label()
        else:
            self.log("❌ CurseForge API Key 保存失败：写不了 %s" % secrets.where_text(),
                     level="ERROR")

    def _clear_cf_key(self):
        """删掉本机保存的 key。"""
        框 = getattr(self, "settings_cf_key_var", None)
        if 框 is not None:
            框.set("")
        if secrets.clear_key():
            self.log("🗑 已删除本机保存的 CurseForge API Key（联网搜索仍走 Modrinth）。",
                     level="INFO")
        self._update_cf_key_label()

    def _test_cf_key(self):
        """测一下 Key 能不能用：后台线程发一次最小请求，别卡住设置窗口。"""
        框 = getattr(self, "settings_cf_key_var", None)
        待测 = secrets.clean(框.get()) if 框 is not None else ""
        if not 待测 and not secrets.has_key():
            self.log("ℹ️ 先在输入框里粘一个 Key，或者先「保存」一个再测。",
                     level="INFO", save=False)
            return
        self.log("🌐 正在测试 CurseForge API Key…（首次联网可能要几秒）",
                 level="INFO", save=False)

        def 跑():
            try:
                from core.mod_search import test_curseforge_key
                ok, 说明 = test_curseforge_key(待测 or None)
            except Exception as e:
                ok, 说明 = False, secrets.redact_exc(e)

            def 收尾():
                self.log(("✅ CurseForge Key 可用：" if ok else "❌ CurseForge Key 不可用：")
                         + 说明, level="INFO" if ok else "ERROR")
                self._update_cf_key_label()

            try:
                self._ui_post(收尾)        # 回主线程再碰界面
            except Exception:
                pass

        try:
            threading.Thread(target=跑, daemon=True).start()
        except Exception as e:
            self.log("❌ 起不了测试线程：%s" % secrets.redact_exc(e), level="ERROR")

    def _toggle_silent(self):
        """后台静默执行开关。"""
        self.silent_background = bool(self.settings_silent_var.get())
        self.save_config()
        self.log(trp("🤫 后台静默执行已{0}（跑任务时不再弹进度/结果窗口）",
                     i18n.tr('开启' if self.silent_background else '关闭')),
                 level="INFO", save=False)

    def _set_extra_conflict(self, value):
        """「其它文件」遇到目标已有同名文件时怎么办：overwrite（先备份）/ skip。

        分段选择控件的回调（替掉原来那对 Radiobutton）。
        """
        try:
            self.extra_conflict.set(value)
        except Exception:
            pass
        self.save_config()

    def _toggle_confirm_migrate(self):
        """设置页里那个"迁移前再确认"开关：同步变量 + 存配置（开关只翻自己的状态）。"""
        try:
            self.confirm_migrate.set(self.settings_confirm_sw.get())
        except Exception:
            return
        self.save_config()

    def _toggle_blank_exit_edit(self):
        """设置页里"点空白处退出主界面编辑"开关：同步变量 + 存配置。"""
        try:
            self.blank_exit_edit.set(self.settings_blank_exit_sw.get())
        except Exception:
            return
        self.save_config()

    def _on_dry_run_switch(self):
        """开关只翻了自己的状态，这里同步到业务变量再照旧保存配置。"""
        self.dry_run.set(self.dry_run_sw.get())
        self.save_config()

    def _on_overwrite_switch(self):
        self.overwrite_mods.set(self.overwrite_sw.get())
        self.save_config()

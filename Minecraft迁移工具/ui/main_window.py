# ui/main_window.py
"""主窗口：MigrationGUI 的组装点 + 核心生命周期/配置读写。

具体的功能方法按职责拆到了 ui/mw_*.py 的 mixin 里（日志、主题、按钮布局、
设置、Qt 宿主、路径、界面搭建、清单、编辑模式、迁移、锁屏、放大查看）。
这里只留 __init__ / 配置读写 / 关闭策略这些"窗口本体"的东西，以及类声明。

⚠ `from ui.mw_common import *` 是**故意**的：_dctest 里 103 个验证脚本会
`from ui import main_window as MW` 然后改 `MW.CONFIG_FILE`、`MW.messagebox`、
`MW.do_backup`…，这个 star 导入把拆分前的老接口面原样搬回来，脚本一行不用改。
"""
from ui.mw_common import *          # noqa: F401,F403 —— 见上面 docstring
import json
import os
from utils import i18n
from utils.dpi import window_geometry
from ui.mw_log import LogMixin
from ui.mw_theme import ThemeMixin
from ui.mw_buttons import ButtonsMixin
from ui.mw_settings import SettingsMixin
from ui.mw_qt import QtMixin
from ui.mw_paths import PathsMixin
from ui.mw_pages import PagesMixin
from ui.mw_lists import ListsMixin
from ui.mw_edit import EditMixin
from ui.mw_migration import MigrationMixin
from ui.mw_lock import LockMixin
from ui.mw_big_view import BigViewMixin
# 右下角的吐司消息（ui/toast.py 是定稿模块）。缺了/导入失败都不许把主窗口带崩，
# 吐司只是补充通道，主功能不依赖它，所以这里兜一层、后面全程按可选处理。
try:
    from ui.toast import ToastHost
except Exception:
    ToastHost = None


class MigrationGUI(
    LogMixin,
    ThemeMixin,
    ButtonsMixin,
    SettingsMixin,
    QtMixin,
    PathsMixin,
    PagesMixin,
    ListsMixin,
    EditMixin,
    MigrationMixin,
    LockMixin,
    BigViewMixin,
):
    """主窗口。功能方法见 ui/mw_*.py 的各个 mixin。"""

    def __init__(self, root, on_stage=None):
        """on_stage(text)：可选的阶段回调。

        构建过程有几百毫秒，期间 Tk 主线程被占满，启动闪屏的动画会停住。
        传入这个回调就能在每个阶段之间让出一帧（app.py 里用它刷新闪屏）。
        """
        self.root = root
        self._on_stage = on_stage
        self.root.title("Minecraft 整合包迁移工具 - 增强版 v5")
        # 尺寸/位置统一走 utils.dpi：DPI 感知之后写死 1000x1080 会被缩放后的字撑爆，
        # 而 1080 * 1.5 又比屏幕高，需要一起适配（见 utils/dpi.py）。
        self.root.geometry(window_geometry(self.root, 1000, 1080))

        self.config = self.load_config()
        # 上次停在三份清单的哪一页（0 模组 / 1 config / 2 其它）。
        # **必须在这儿读**：界面建完之后、正式配置段之前，别处可能已经调过
        # save_config()，那时这个键还不存在，会把用户上次的位置写成 0。
        try:
            self.list_tab = max(0, min(2, int(self.config.get("list_tab", 0))))
        except Exception:
            self.list_tab = 0
        # 整合包更新提醒只能收起、不能关：只记形态（False = 整条横幅，True = 仅 ⚠ 图标）。
        # **同样必须在这儿读** —— 提醒条是建界面时按这个值摆位的（mw_pages 里
        # 建条时读一次、随后又调一次 _pack_warning_layout），读晚了就永远是展开态。
        self.pack_warning_collapsed = bool(self.config.get("pack_warning_collapsed", False))
        # 回滚默认还原哪些（设置页可改；「记住我的选择」勾上时由回滚对话框写这里）
        try:
            from core.migrator import _RESTORE_PARTS as _RP
            存的 = self.config.get("rollback_parts")
            self.rollback_parts = [p for p in (存的 or _RP) if p in _RP] or list(_RP)
        except Exception:
            self.rollback_parts = ["mods", "config", "saves", "files", "extras"]
        self.rollback_parts_remember = bool(self.config.get("rollback_parts_remember", False))
        self.edit_mode = tk.BooleanVar(value=self.config.get("edit_enabled", False))
        # 点窗口空白处是否顺手退出「主界面编辑」（设置 →「🎨 外观与启动」可关）
        self.blank_exit_edit = tk.BooleanVar(
            value=self.config.get("blank_exit_edit", True))
        # 消息中心（右侧抽屉）开着时，点主界面任意位置是否就把它收回。
        # 默认关：抽屉一直留着，只有 ✕ / Esc / 再点铃铛能收（用户报「太敏感」）。
        self.msg_click_outside_close = tk.BooleanVar(
            value=self.config.get("msg_click_outside_close", False))
        # 「其它文件」清单遇到目标已有同名文件时怎么办：overwrite（先备份）/ skip
        self.extra_conflict = tk.StringVar(
            value=str(self.config.get("extra_conflict", "overwrite") or "overwrite"))
        if self.extra_conflict.get() not in ("overwrite", "skip"):
            self.extra_conflict.set("overwrite")
        self.current_theme = self.config.get("theme", "light")
        self.theme = LIGHT_THEME if self.current_theme == "light" else DARK_THEME

        self.source_path = tk.StringVar(value=self.config.get("source", ""))
        self.target_path = tk.StringVar(value=self.config.get("target", ""))
        self.world_name = tk.StringVar(value=self.config.get("world", "老子的世界"))
        self.dry_run = tk.BooleanVar(value=self.config.get("dry_run", True))
        self.overwrite_mods = tk.BooleanVar(value=self.config.get("overwrite", False))
        # 「开始迁移」按钮的翻转手感：crisp（干脆）/ standard（标准）/ heavy（沉）。
        # 参数表在 ui/flip_button.py 的 PRESETS；设置 →「🎨 外观与启动」里能改。
        self.start_btn_feel = tk.StringVar(
            value=str(self.config.get("start_btn_feel", "standard") or "standard"))
        if self.start_btn_feel.get() not in ("crisp", "standard", "heavy"):
            self.start_btn_feel.set("standard")
        # 正式迁移前再确认一次（默认开；设置里可以关）
        self.confirm_migrate = tk.BooleanVar(value=self.config.get("confirm_migrate", True))
        # 关闭窗口时的行为：ask（每次问）/ tray（收进托盘）/ exit（直接退出）
        self.close_action = self.config.get("close_action", "ask")
        # 启动动画：设置里可关（app.py 启动时直接读配置文件，这里只负责保存）
        self.splash_enabled = bool(self.config.get("splash", True))
        # 右下角消息提示（吐司）的总开关：关掉后 self.toast() 直接返回；
        # 容器 _toast_host 要等主窗口建好才创建（见 create_widgets 之后那块）
        self.toast_on = bool(self.config.get("toast_on", True))
        self._toast_host = None
        # 后台静默执行：跑任务时不弹进度窗/结果窗，只写日志 + 系统通知
        self.silent_background = bool(self.config.get("silent_background", False))
        # 按钮显示/隐藏 与 自定义顺序
        self.hidden_buttons = list(self.config.get("buttons_hidden", []) or [])
        self.button_order = dict(self.config.get("button_order", {}) or {})
        # 把"显示/隐藏 + 顺序"喂给窗口工具栏那边（放大查看 / 日志放大查看建工具栏时来查）
        button_prefs.update(self.hidden_buttons, self.button_order)
        # 迁移标记：复制过去的模组加前缀，方便在目标 mods 里辨认（默认关，不改老行为）
        self.rename_migrated_mods = bool(self.config.get("rename_migrated_mods", False))
        self.rename_marker = str(self.config.get("rename_marker", "★") or "★")
        # 分类标签：默认用关键词推测；开启后去 Modrinth 取真实分类（有本地缓存）
        self.online_tags = bool(self.config.get("online_tags", False))
        # 迁移时怎么锁主界面：all=正式+模拟都盖遮罩 / real=只锁正式 / off=不盖遮罩（按钮一律禁用）
        self.lock_mode = str(self.config.get("lock_mode", "all") or "all")
        if self.lock_mode not in ("all", "real", "off"):
            self.lock_mode = "all"
        # 迁移跑完的完成态怎么收：True=按任意键关闭 / False=2 秒后自动关
        self.lock_wait_key = bool(self.config.get("lock_wait_key", True))
        # 注：以前这里有个 `extra_defaults`（"默认携带的目录"勾选）—— 那套在迁移那一刻
        # 自动往清单里并东西，用户要求"一切都要自己选"，已经删掉、统一到「其它文件」清单。
        # 老配置里勾过的条目由 _并入旧的默认携带目录() 一次性并进清单，不会悄悄丢掉。
        # 放大查看窗口用哪个实现：qt=PySide6 试点（缺库时自动回落）/ tk=经典 Tk
        self.big_view_backend = str(self.config.get("big_view_backend", "qt") or "qt")
        if self.big_view_backend not in ("qt", "tk"):
            self.big_view_backend = "qt"
        # 差异窗口用哪个实现（同样的 qt / tk 二选一；出问题时可以切回 Tk 版）
        self.diff_backend = str(self.config.get("diff_backend", "qt") or "qt")
        if self.diff_backend not in ("qt", "tk"):
            self.diff_backend = "qt"
        # 总开关：关掉后主进程完全不加载/不使用 Qt（排查"Tk 与 Qt 同进程"用）
        self.qt_enabled = bool(self.config.get("qt_enabled", True))
        # 放大查看窗口打开时用哪个视图：table=表格 / cards=卡片（设置里能选）
        self.big_view_view = str(self.config.get("big_view_view", "table") or "table")
        if self.big_view_view not in ("table", "cards"):
            self.big_view_view = "table"
        # 模组差异窗口打开时用哪个视图（同样是 table / cards，设置里能选）
        self.diff_view = str(self.config.get("diff_view", "table") or "table")
        if self.diff_view not in ("table", "cards"):
            self.diff_view = "table"
        # 双击已经全部交给 Tk / Qt 原生事件（间隔 = 系统设置里的鼠标双击速度），
        # 程序里不再有判定阈值。double_click_sec / double_click_auto 这两个键只是
        # "双击间隙测试"留下的历史记录，读进来是为了保存设置时原样写回去、不丢数据。
        self.double_click_sec_cfg = float(self.config.get("double_click_sec", 0) or 0)
        self.double_click_auto_cfg = bool(self.config.get("double_click_auto", False))

        # 可自定义按钮的登记表：key -> 控件（在 create_widgets 里逐个登记）
        self._btn_widgets = {}
        # 平滑滚动器：留住引用，不然会被回收（滚动就失效了）
        self._scrollers = []
        # 已经上过原生外观的窗口（避免 <Map> 每次重映射都刷一遍）
        self._styled_windows = set()

        self.last_check_modlist_time = 0
        self.last_check_config_time = 0
        self._config_status_applied = False
        # 出错的清单条目：(页索引, 条目名, 页名)；必须先于 create_widgets ——
        # 日志工具条那颗「📍 定位错误」在建造时就要按"有没有错误"决定灰不灰
        self._failed_items = []
        self._fail_cursor = 0

        self._stage("正在构建界面…")
        self.create_widgets()

        # 主窗口建好之后才建吐司容器：ToastHost 是个挂在 self.root 名下的无边框
        # Toplevel（真透明圆角，见 ui/toast.py 开头那段），得等主窗口存在、能取到
        # 它的位置才摆得对；此刻 self.theme 已是字典（__init__ 按 current_theme 选好的）
        # 建失败也不抛：吐司永远不该弄崩调用方。
        # 消息存档：吐司消失后还能在「消息中心」回看（只把重要的写盘）
        try:
            from ui.message_center import MessageStore
            self._msg_store = MessageStore()
        except Exception:
            self._msg_store = None
        try:
            self.root.bind("<Control-m>", lambda e: self.open_message_center())
        except Exception:
            pass

        if ToastHost is not None:
            try:
                self._toast_host = ToastHost(self.root, self.theme)
            except Exception:
                self._toast_host = None

        # 后台线程 → 主线程的 UI 通道（worker 只往队列里丢，由主线程的泵执行）。
        # tkinter 的 after 不是线程安全的：从 worker 里调，事件循环不是 mainloop 时
        # （验证脚本用 update() 泵的那种）会抛 `main thread is not in main loop`，
        # 那条日志 / 那次收尾就悄悄丢了。队列 + 主线程泵是唯一稳的写法。
        self._ui_calls = queue.Queue()
        self._ui_pump_id = self.root.after(_UI_PUMP_MS, self._ui_pump)

        self.init_log_colors()
        self._stage("正在应用主题…")
        self.apply_theme()
        # 之后新开的窗口（设置/历史/放大查看/进度/差异…）全靠这个统一上样式，
        # 省得去每个建窗的地方补一行
        self._bind_window_styling()

        # 实时检测存档：输入存档名/切换源路径时即时刷新"存档是否存在"状态
        self.world_name.trace_add("write", lambda *a: self._update_world_status())
        self.source_path.trace_add("write", lambda *a: self._update_world_status())
        self._update_world_status()

        self._stage("正在载入清单…")
        self.mod_text.insert("1.0", self.config.get("mod_list", ""))
        self.config_text.insert("1.0", self.config.get("config_list", ""))
        self.extra_text.insert("1.0", self.config.get("extra_list", ""))
        self.mod_text.edit_reset()
        self.config_text.edit_reset()
        self.extra_text.edit_reset()
        # 老配置里如果还留着「默认携带的目录」勾选（那套已删）：把它并进清单一次，
        # 免得用户原本会带的东西因为改机制就不带了
        self._并入旧的默认携带目录()
        # 用自定义撤销栈替代 Tk 原生撤销（Tk 会把连续删除合并为一步撤销）
        self._setup_custom_undo(self.mod_text, "mod")
        self._setup_custom_undo(self.config_text, "config")
        self._setup_custom_undo(self.extra_text, "extra")
        self._refresh_list_badges()
        self.log("=" * 60, level="INFO", save=False)
        self.log(i18n.tr("【免费声明】本工具完全免费，严禁用于商业用途或转卖。"),
                 level="WARNING", save=False)
        self.log(i18n.tr("如有任何收费行为，请立即举报。作者不会以任何形式向你收费。"),
                 level="WARNING", save=False)
        self.log("=" * 60, level="INFO", save=False)

        self.progress_queue = None
        self.progress_window = None
        self.after_id = None
        self._migration_running = False
        # "准备阶段"（校验清单 / 统计文件 / 磁盘检查）也在跑：这期间主线程可能被弹窗
        # 带着转过事件循环，用户再点一下就会重入 start_migration，而此刻
        # _migration_running 还没置位 —— 所以要有这个更早的标记挡住第二次。
        self._starting = False
        self.diff_window = None
        self.diff_qt = None          # 进程内 Qt 差异窗口（老路径，现在默认走子进程）
        self._qt_hosts = {}          # Qt 窗口的独立子进程：kind -> {proc, req, res, cmd, err, ...}
        self._qt_host_poll = None
        self._scanning = False
        # 其它"动文件"的任务（检查存在性/导入变更日志/回滚/大窗口检测…）跑起来时登记名字，
        # 期间禁止启动迁移（模拟运行也禁），避免两个任务同时改同一批文件。
        self._file_task = None
        # 锁屏遮罩已整体移除（2026-10-31）：这里只留"收尾是否还在等日志"这类
        # 仍然有用的状态，遮罩/流动边框/内嵌日志那几个状态位一并删掉了。
        self._mig_finish_pending = False
        self._ending_wait_log = False
        # 下面两个由 app.py 注入：窗口挂在托盘里的时候，任务跑完要弹系统通知，
        # 扫描出来的差异窗口也要先压着，等窗口叫回来再开。
        self._task_done_cb = None
        self._in_tray_cb = None
        self._pending_diff = None
        self._stage("正在检查实例路径…")
        self.on_path_change()
        self._stage()
        self._update_text_states()
        self._log_cache_limit = 500
        self._saved_logs = []
        self._log_file_max_bytes = 2 * 1024 * 1024  # 日志文件超过 2MB 时轮转，避免无限增长
        self._last_log_key = None
        # 桌面浮标（网络延迟小窗）：状态与位置都记在配置里，下次启动回到原处
        self._net_float = None
        try:                      # 启动时按当前上限裁剪一次档案
            from utils import history_store
            history_store.prune(getattr(self, "history_limit", 10))
        except Exception:
            pass
        self.net_float_on = bool(self.config.get("net_float_on", False))
        self.net_float_x = self.config.get("net_float_x")
        self.net_float_y = self.config.get("net_float_y")
        try:
            self.net_float_size = max(40, min(96, int(self.config.get("net_float_size", 56))))
            try:
                self.history_limit = max(1, min(15, int(self.config.get("history_limit", 10))))
            except Exception:
                self.history_limit = 10
        except Exception:
            self.net_float_size = 56
            self.history_limit = 10
        # 点「开始迁移」后先倒数几秒（1~10，默认 3）。误点有后悔窗口，
        # 倒计时期间再点一下按钮就取消。模拟运行走同一套。
        try:
            self.start_countdown = max(1, min(10, int(self.config.get("start_countdown", 5))))
        except Exception:
            self.start_countdown = 5
        # 界面已经建好了，把页签跳回上次那一页
        try:
            self._restore_list_tab()
        except Exception:
            pass
        # 平滑滚动速度档位（1 最慢 … 5 最快，默认 3 = 原版标定）
        try:
            from utils.helpers import SmoothScroller
            self.scroll_speed = max(1, min(5, int(self.config.get("scroll_speed", 3))))
            SmoothScroller.set_speed(self.scroll_speed)
        except Exception:
            self.scroll_speed = 3
        self.float_edge_fx = bool(self.config.get("float_edge_fx", True))
        self.net_float_shadow = bool(self.config.get("net_float_shadow", True))
        self.net_float_dock = bool(self.config.get("net_float_dock", True))
        self._stage("就绪")
        self._start_net_status()
        if self.net_float_on:
            try:
                self.open_net_float()
            except Exception:
                pass

    def _start_net_status(self):
        """右下角的网络状态灯：子线程探测，结果回主线程改字。"""
        try:
            from ui import mw_net
        except Exception:
            return

        def 更新(文字, 档位, 明细):
            try:
                # 档位/明细留着：切语言时没法从"● 网络已连接"这种拼好的串反查，
                # 只能靠这里的原料重算一遍（见 _刷新网络状态文字）
                self._net_档位, self._net_明细 = 档位, dict(明细)
                self._刷新网络状态文字()
            except Exception:
                pass

        try:
            self._net_stop = mw_net.start_polling(self.root, 更新)
        except Exception:
            self._net_stop = None

    def _刷新网络状态文字(self):
        """按当前语言重写左下角那行网络状态（切语言 / 探测结果到达时都调）。"""
        档位 = getattr(self, "_net_档位", None)
        明细 = getattr(self, "_net_明细", None) or {}
        try:
            色 = {"ok": "#81c784", "warn": "#e65100", "fail": "#c62828"}.get(
                档位, self.theme.get("muted_fg", "#888888"))
            文案 = {"ok": "网络已连接", "warn": "网络部分不通",
                    "fail": "网络未连接"}.get(档位, "正在检测网络…")
            self.net_status.configure(text="● " + i18n.tr(文案), fg=色)
            # 这行是"拼出来的派生文字"，本函数就是它的权威来源：把语言层记的原文
            # 撤掉，免得翻树拿英文当原文、和这里互相覆盖
            try:
                getattr(self.net_status, "_i18n_原文", {}).pop("text", None)
            except Exception:
                pass
            明细文字 = "｜".join(
                i18n.trp("{0}：{1}", k, i18n.trp("通") if v else i18n.trp("不通"))
                for k, v in 明细.items())
            底 = i18n.trp("网络通畅\n") if 档位 == "ok" else (
                i18n.tr("网络部分不通\n") if 档位 == "warn" else
                i18n.tr("网络未连接\n") if 档位 == "fail" else
                i18n.tr("正在检测网络…\n"))
            self._net_tip = 底 + 明细文字
        except Exception as e:
            trace_exc("main_window/刷新网络状态", e)

    def _stage(self, text=""):
        """向构建阶段的观察者（启动闪屏）报告进度，并让出一帧。

        传入回调时调用它；没传就什么都不做，所以对正常启动没有影响。
        """
        if not self._on_stage:
            return
        try:
            # 闪屏那几行状态字是画在画布上的（不走 Tk 控件），语言层拦不到，这里显式过一遍
            self._on_stage(i18n.tr(text))
        except Exception:
            pass

    # ---------- 托盘（后台运行）相关 ----------
    def set_close_action(self, action):
        """记住"关闭窗口"的偏好：ask（每次问）/ tray（收进托盘）/ exit（直接退出）。"""
        if action not in ("ask", "tray", "exit"):
            return
        self.close_action = action
        # 设置窗开着的话，把单选框同步过去（托盘右键菜单也能改这个值）
        try:
            if getattr(self, "settings_close_var", None) is not None:
                self.settings_close_var.set(action)
        except Exception:
            pass
        try:
            self.save_config()
        except Exception:
            pass

    def busy_task_name(self):
        """当前正在跑的任务名；没有任务就返回 None。

        关闭窗口（app.py 的 on_closing）和托盘退出都问这一句，所以它必须覆盖
        **所有**正在读写文件的状态。这里以前另写了一份判断，只认迁移和扫描，
        于是「检查模组是否存在」「回滚」这类同样在动文件的任务跑着的时候点 ❌，
        会被当成"没任务"，直接问"退出还是收托盘" —— 选退出就把线程 kill 在半路。

        现在统一走 _busy_task_name()（迁移 / 扫描模组差异 / 其它文件任务），
        再补两件它不管、但关闭时必须拦住的事：正在准备迁移、开始前倒计时。
        """
        try:
            task = self._busy_task_name()
        except Exception:
            task = None
        if not task:
            if getattr(self, "_starting", False):
                task = "迁移准备"
            elif getattr(self, "_countdown_running", False):
                task = "开始前倒计时"
        return i18n.tr(task) if task else None

    def _in_tray(self):
        """窗口现在是不是收在系统托盘里。"""
        cb = self._in_tray_cb
        try:
            return bool(cb()) if cb else False
        except Exception:
            return False

    def _notify_task_done(self, name, detail="", force=False):
        """任务跑完时弹个系统通知。

        force=True：静默模式下即使窗口没挂在托盘里也要通知（否则用户完全收不到反馈）。
        """
        if not force and not self._in_tray():
            return
        cb = self._task_done_cb
        if cb is None:
            return
        try:
            cb(name, detail, force)
        except TypeError:
            try:
                cb(name, detail)     # 兼容只有一个参数位的旧回调
            except Exception:
                pass
        except Exception:
            pass

    # ---------- 消息提示（吐司）----------
    def toast(self, 文本, level="info", ms=None, sticky=None, 弹入=True, key=None):
        """整个程序统一的消息入口：右下角弹一条吐司（不写日志、不弹 messagebox）。

        文本先过 i18n.tr —— 和 log() 一样，词条表里有的才翻得到；
        开关关着 / 容器没建起来 / 已经关掉 / 任何意外，一律安静返回，
        绝不抛异常：吐司是锦上添花，不能把调用它的功能弄崩。
        """
        文 = i18n.tr(文本)
        # 停留时长：调用方没指定就按字数算（长文案多赖一会儿，才读得完）。
        # 指定了 ms 的（有些提示刻意要短/要长）尊重原值。
        try:
            from ui.toast import 停留时长
            ms = 停留时长(文, ms)
        except Exception:
            if ms is None:
                ms = 3000
        # 文案开头自带 ⚠️/✅/❌ 这类符号时，会和左侧的等级图标**重复画成两个图标**
        # （用户报"警告图标左边还有个奇怪的符号"）。等级本来就由图标+色条表达，
        # 所以在这个唯一入口把开头那几个符号剥掉；消息中心存的是同一份干净文案。
        try:
            from ui.toast import _去级别符号
            文 = _去级别符号(文)
        except Exception:
            pass
        # 先记档再判断开关：消息中心是第二条通道，即使吐司被关掉也照样留一份记录
        try:
            _存 = getattr(self, "_msg_store", None)
            if _存 is not None:
                _存.add(文, level, key=key)
                self._refresh_msg_badge()
        except Exception:
            pass
        if not bool(getattr(self, "toast_on", True)):
            return None
        host = getattr(self, "_toast_host", None)
        if host is None:
            return None
        try:
            if not host.alive():
                return None
            return host.show(文, level=level, ms=ms, sticky=sticky, 弹入=弹入,
                            key=key)
        except Exception:
            return None

    # ---------- 消息中心 ----------
    def open_message_center(self):
        """打开消息中心（铃铛按钮 / Ctrl+M 都走这里）。"""
        _存 = getattr(self, "_msg_store", None)
        if _存 is None:
            return None
        # 铃铛上的悬停提示是个独立的深灰小窗，正好落在抽屉要滑出的那片区域；
        # 不先收掉，它就会压在抽屉上，鼠标一动又消失 —— 看起来就是"闪了个暗块"。
        try:
            self._hide_tooltip()
        except Exception:
            pass
        # 还在屏幕上飘着的吐司立即回收：抽屉一开，它们要么被卡片压住、要么飘在
        # 卡片旁边，看着像两个人同时说话。消息本身在 store 里已经记档了，抽屉
        # 打开就能看到，所以这里直接清掉不丢内容（badge 下面会跟着刷新）。
        try:
            _吐司 = getattr(self, "_toast_host", None)
            if _吐司 is not None:
                _吐司.clear()
        except Exception:
            pass
        try:
            # 抽屉里只有根窗口，读不到主窗口的属性；「点外部收回」开关先挂上去
            try:
                self.root.msg_click_outside_close = self.msg_click_outside_close
            except Exception:
                pass
            from ui.message_center import open_message_center as _开
            窗 = _开(self.root, _存, self.theme)
            self._refresh_msg_badge()
            return 窗
        except Exception:
            return None

    def _再翻一遍界面(self):
        """补翻一遍：切语言时晚建出来的控件（延迟布局的路径行、提示行）也能跟上。"""
        try:
            i18n.翻树(self.root)
        except Exception:
            pass
        try:
            i18n.翻自绘()
        except Exception:
            pass

    def 重新翻译界面(self):
        """运行中切语言：把主界面整棵树按当前语言重刷一遍。

        · 普通控件（Label/Button/复选框/树）走 i18n.翻树 —— 造的时候把中文原文
          挂在控件上了，这里拿原文重新 tr 一次，来回切不会越翻越歪；
        · 自绘控件（页签栏、渐变按钮、开关、翻转按钮）各自登记了重画回调，走翻自绘；
        · 拼出来的状态行（"网络通畅 · Modrinth：通"之类）由 configure(text=) 记过原文，
          也会跟着刷。
        返回改了多少处，方便探针断言。
        """
        改 = 0
        try:
            改 = i18n.翻树(self.root)
        except Exception as e:
            trace_exc("main_window/重翻界面-翻树", e)
        # 有些控件是"晚一步建出来的"（比如窗口第一次 Configure 才布局的路径行、
        # 延迟创建的提示行）：一次翻树扫不到它们，等一两帧再补两遍。
        for 延迟 in (150, 420):
            try:
                self.root.after(延迟, self._再翻一遍界面)
            except Exception:
                pass
        try:
            i18n.翻自绘()
        except Exception as e:
            trace_exc("main_window/重翻界面-自绘", e)
        # 有些"文案"是带缓存的探测结果（Qt 可用性那段就是）：缓存里存的是**当时语言**
        # 的成品，不清掉的话设置页重建了也还是旧语言
        for 缓存 in ("_qt_ok_cache",):
            try:
                setattr(self, 缓存, None)
            except Exception:
                pass
        # Qt 子进程是另一个进程，Tk 这边重翻够不着它：推一条命令过去
        推 = getattr(self, "_push_language_to_qt", None)
        if callable(推):
            try:
                推(i18n.language())
            except Exception:
                pass
        for 名 in ("_刷新网络状态文字", "_refresh_msg_badge"):
            方 = getattr(self, 名, None)
            if callable(方):
                try:
                    方()
                except Exception:
                    pass
        return 改

    def _refresh_msg_badge(self):
        """刷新状态栏铃铛上的未读数（没建出来就安静跳过）。"""
        钮 = getattr(self, "msg_bell", None)
        if 钮 is None:
            return
        try:
            _存 = getattr(self, "_msg_store", None)
            未读 = int(getattr(_存, "未读", 0) or 0)
            钮.config(text=("🔔 %d" % 未读) if 未读 else "🔔",
                      fg=(self.theme.get("warn_fg") or "#ffa000") if 未读
                      else self.theme.get("muted_fg", "#888888"))
        except Exception:
            pass

    def _flush_pending_diff(self):
        """窗口从托盘叫回来时，把之前压着的差异窗口补开出来。"""
        item, self._pending_diff = self._pending_diff, None
        if not item:
            return
        data, apply_callback = item
        try:
            self._open_diff_window(data, apply_callback)
        except Exception:
            pass

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {}

    def save_config(self):
        # ⚠ 这里**故意没有** CurseForge API Key：key 存在 ~/.minecraft_migrate_secret.json
        # （见 utils/secrets.py），和这份配置分开存 —— 用户为了报障会把这份配置贴进 issue，
        # 里面已经有模组清单和路径了，不能再多一个 key。新加的设置项照常加在下面。
        config = {
            "source": self.source_path.get(),
            "target": self.target_path.get(),
            "world": self.world_name.get(),
            "dry_run": self.dry_run.get(),
            "overwrite": self.overwrite_mods.get(),
            "start_btn_feel": self.start_btn_feel.get(),
            "confirm_migrate": self.confirm_migrate.get(),
            "theme": self.current_theme,
            # 界面语言（下次启动生效）。i18n 是"文案入口查表"，打包层不持有语言状态，
            # 所以这里读的是当前生效值。
            "language": str(i18n.language()),
            "mod_list": self.mod_text.get("1.0", tk.END).strip(),
            "config_list": self.config_text.get("1.0", tk.END).strip(),
            "extra_list": self.extra_text.get("1.0", tk.END).strip(),
            "extra_conflict": self.extra_conflict.get(),
            "edit_enabled": self._edit_enabled_for_config(),
            "blank_exit_edit": self.blank_exit_edit.get(),
            "msg_click_outside_close": self.msg_click_outside_close.get(),
            "close_action": self.close_action,
            "splash": bool(getattr(self, "splash_enabled", True)),
            "silent_background": bool(getattr(self, "silent_background", False)),
            "buttons_hidden": list(getattr(self, "hidden_buttons", []) or []),
            "button_order": dict(getattr(self, "button_order", {}) or {}),
            # 注：extra_defaults（老的"默认携带的目录"）不写了 —— 那套已删，
            # 老值在 __init__ 里并进清单后就会被清掉
            "rename_migrated_mods": bool(getattr(self, "rename_migrated_mods", False)),
            "rename_marker": str(getattr(self, "rename_marker", "★")),
            "online_tags": bool(getattr(self, "online_tags", False)),
            "lock_mode": str(getattr(self, "lock_mode", "all")),
            "lock_wait_key": bool(getattr(self, "lock_wait_key", True)),
            # 回滚默认还原哪些（键见 core.migrator._RESTORE_PARTS）
            "rollback_parts": list(getattr(self, "rollback_parts", []) or []),
            "rollback_parts_remember": bool(getattr(self, "rollback_parts_remember", False)),
            "big_view_backend": str(getattr(self, "big_view_backend", "qt")),
            "diff_backend": str(getattr(self, "diff_backend", "qt")),
            "qt_enabled": bool(getattr(self, "qt_enabled", True)),
            "big_view_view": str(getattr(self, "big_view_view", "table")),
            "diff_view": str(getattr(self, "diff_view", "table")),
            # 实时读盘：不要用启动时的缓存值，否则外部改过的窗口会被这里覆盖回去
            "double_click_sec": float(_dc_now()[0]),
            "double_click_auto": bool(_dc_now()[1]),
            # 桌面浮标：开关 + 上次位置（-1 表示没记过，用默认右下角）
            "net_float_on": bool(getattr(self, "net_float_on", False)),
            "net_float_x": int(getattr(self, "net_float_x", -1) or -1),
            "net_float_y": int(getattr(self, "net_float_y", -1) or -1),
            # 浮标直径（设计像素，40~96）；界面里按 DPI 再乘一次
            "net_float_size": int(getattr(self, "net_float_size", 56) or 56),
            "history_limit": int(getattr(self, "history_limit", 10) or 10),
            # 开始前倒计时秒数（1~10）。不写进来，下次改任何设置都会把它抹回默认
            "start_countdown": int(getattr(self, "start_countdown", 5) or 5),
            # 上次停在三份清单的哪一页（0/1/2）
            "list_tab": int(getattr(self, "list_tab", 0) or 0),
            "pack_warning_collapsed": bool(getattr(self, "pack_warning_collapsed", False)),
            # 右下角吐司消息的总开关（缺省开）。不写进来，用户下次改任何设置都会把它抹掉
            "toast_on": bool(getattr(self, "toast_on", True)),
            "scroll_speed": int(getattr(self, "scroll_speed", 3) or 3),
            "float_edge_fx": bool(getattr(self, "float_edge_fx", True)),
            "net_float_shadow": bool(getattr(self, "net_float_shadow", True)),
            "net_float_dock": bool(getattr(self, "net_float_dock", True)),
        }
        try:
            # 原子写：直接 "w" 打开会先截断原文件，写到一半崩溃/断电就只剩半个 JSON，
            # 而 load_config 对坏文件是静默回退默认值 —— 用户的清单/路径/设置全没。
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".mctmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_FILE)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
        self._check_overflow()

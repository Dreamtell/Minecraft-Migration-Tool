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
from utils import i18n
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
        self.root.title("Minecraft 整合包迁移工具 - 增强版 v4")
        self.root.geometry("1000x1080")

        self.config = self.load_config()
        self.edit_mode = tk.BooleanVar(value=self.config.get("edit_enabled", False))
        # 点窗口空白处是否顺手退出「主界面编辑」（设置 →「🎨 外观与启动」可关）
        self.blank_exit_edit = tk.BooleanVar(
            value=self.config.get("blank_exit_edit", True))
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
        # 正式迁移前再确认一次（默认开；设置里可以关）
        self.confirm_migrate = tk.BooleanVar(value=self.config.get("confirm_migrate", True))
        # 关闭窗口时的行为：ask（每次问）/ tray（收进托盘）/ exit（直接退出）
        self.close_action = self.config.get("close_action", "ask")
        # 启动动画：设置里可关（app.py 启动时直接读配置文件，这里只负责保存）
        self.splash_enabled = bool(self.config.get("splash", True))
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
        # 迁移期间盖在主窗口上的"锁屏"遮罩
        self._lock_overlay = None
        self._mig_watch_job = None
        self._lock_pulse_job = None
        self._lock_pulse_on = False
        self._lock_cfg_bind = None
        self._lock_log_text = None      # 锁屏里那份执行日志（第二个视图）
        self._flow_job = None           # 红边流动动画的定时器
        self._flow_canvas = None
        self._flow_items = []
        self._flow_tiles = {}           # 渐变瓦片（水平/垂直各一张，缓存）
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
        self._stage("就绪")

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
        """当前正在跑的任务名："迁移" / "扫描模组差异"；没有任务就返回 None。"""
        if getattr(self, "_migration_running", False):
            return "迁移"
        if getattr(self, "_scanning", False):
            return "扫描模组差异"
        return None

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
            "big_view_backend": str(getattr(self, "big_view_backend", "qt")),
            "diff_backend": str(getattr(self, "diff_backend", "qt")),
            "qt_enabled": bool(getattr(self, "qt_enabled", True)),
            "big_view_view": str(getattr(self, "big_view_view", "table")),
            "diff_view": str(getattr(self, "diff_view", "table")),
            # 实时读盘：不要用启动时的缓存值，否则外部改过的窗口会被这里覆盖回去
            "double_click_sec": float(_dc_now()[0]),
            "double_click_auto": bool(_dc_now()[1]),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except:
            pass
        self._check_overflow()

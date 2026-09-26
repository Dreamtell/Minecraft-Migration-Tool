# ui/main_window.py
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont
import json
import time
import os
import sys
import queue
import threading
import shutil
import functools
from pathlib import Path
import subprocess
import re
from collections import Counter
from utils.config import CONFIG_FILE
from utils.theme import LIGHT_THEME, DARK_THEME, apply_theme_to_widget_tree
from ui import button_prefs
def _dc_now():
    """读配置里的双击间隙遗留键（双击已改走原生事件，这里只负责保存时不丢数据）。"""
    try:
        from utils.config import load_raw_config
        cfg = load_raw_config()
        return (float(cfg.get("double_click_sec", 0) or 0),
                bool(cfg.get("double_click_auto", False)))
    except Exception:
        return (0.0, False)


from utils.helpers import (create_gradient_button, set_window_icon, center_window,
                           RoundedEntry, RoundedTextArea, circular_reveal, focus_window,
                           lighten_color,
                           make_theme_icon, clear_layered_style, SmoothScroller,
                           tree_row_px, style_window, is_dark_theme)
from core.migrator import (
    run_migration,
    do_backup,
    do_restore,
    get_backup_path,
    load_history,
    mark_rollback,
    _is_safe_path,
    match_mod
)
from core.scanner import (scan_mod_differences, get_full_mod_metadata,
                          split_cn_name, get_mod_icon, guess_tags)
from ui.dialogs import ProgressWindow, ScanProgressWindow, show_mod_detail, update_mod_detail_theme
from ui.diff_window import show_diff_window
from ui.virtual_table import VirtualTable


_GRAD_FONT = None
_GRAD_PAD = 26

# Tk 量一个 emoji/符号会去查一次字体回退，进程里第一次要 260 ms 以上，后面每个新
# 字符几毫秒；界面上十几个按钮的文案都带 emoji，累计约 300 ms 全卡在启动那一刻。
# 而回退字形在 Windows 下几乎等宽——实测 emoji 一律 17 px、箭头 12 px（🗑️ 那种
# 带变体选择符的也还是 17），所以先摘掉再按常量补回来，宽度与真实测量完全一致。
_GRAD_SYM_RE = re.compile(
    "[\u2190-\u21ff\u2300-\u23ff\u25a0-\u27bf\u2b00-\u2bff\ufe0f"
    "\U0001F000-\U0001FAFF]")
_GRAD_ARROW_RE = re.compile("[\u2190-\u21ff\u2b00-\u2bff]")
_GRAD_SYM_W = 17
_GRAD_ARROW_W = 12
# 顶栏两个图标按钮（⚙ / 月亮太阳）的边长：正方形，图标大小与相邻按钮高度协调
_ICON_BTN = 34
# 主题按钮里那张手绘图标（月亮/太阳）的边长
_ICON_SIZE = 22

# ---- 可以在设置里显示/隐藏、调顺序的按钮 ------------------------------------
# 一组 = 同一个容器里的一排按钮；组内顺序就是"从左到右"看到的样子。
# side="right" 的那两排（靠右对齐）应用顺序时会反过来 pack，视觉顺序仍按列表走。
_BUTTON_GROUPS = (
    ("path",        "路径区（来源）",    "left"),
    ("path_target", "路径区（目标）",    "left"),
    ("mods",        "模组清单区",        "left"),
    ("config",      "Config 清单区",     "left"),
    ("action",      "动作按钮区",        "right"),
    ("log_left",    "执行日志区（左）",  "left"),
    ("log_right",   "执行日志区（右）",  "right"),
) + tuple((g[0], g[1], g[4]) for g in button_prefs.GROUPS)

# 每组的默认顺序 = 现在的界面顺序；用户改过就用配置里的
_DEFAULT_BUTTON_ORDER = {
    "path":        ["browse_source", "copy_target"],
    "path_target": ["browse_target"],
    "mods":        ["changelog", "mod_magnify", "add_mods", "scan_diff",
                    "clear_mods", "check_mods"],
    "config":      ["cfg_magnify", "add_cfg_dir", "add_cfg_file", "clear_cfg",
                    "check_cfg"],
    "action":      ["history", "rollback", "start"],
    "log_left":    ["log_big"],
    "log_right":   ["log_open", "log_clear"],
}
# 窗口工具栏按钮（放大查看 / 日志放大查看 / 右上角那两个）的定义在 ui/button_prefs.py，
# 那份表是唯一的真源：设置窗的分组、默认顺序、标签都从它来
_DEFAULT_BUTTON_ORDER.update(button_prefs.DEFAULTS)

_BUTTON_LABELS = {
    "browse_source": "📂 浏览…（来源路径）",
    "copy_target": "← 使用新版路径填充",
    "browse_target": "📂 浏览…（目标路径）",
    "changelog": "📥 从变更日志导入",
    "mod_magnify": "📂 放大查看（模组清单）",
    "add_mods": "➕ 添加模组",
    "scan_diff": "🔍 扫描模组差异",
    "clear_mods": "🗑️ 清空清单（模组）",
    "check_mods": "🔎 检查模组是否存在",
    "cfg_magnify": "📂 放大查看（config 清单）",
    "add_cfg_dir": "📁 浏览添加文件夹",
    "add_cfg_file": "📄 浏览添加文件",
    "clear_cfg": "🗑️ 清空 config 清单",
    "check_cfg": "🔎 检查 config 是否存在",
    "history": "📋 查看历史",
    "rollback": "⚠️ 回滚",
    "start": "🚀 开始迁移",
    "log_big": "📂 放大查看（日志）",
    "log_open": "📂 打开日志文件夹",
    "log_clear": "🗑️ 清空日志",
}
_BUTTON_LABELS.update(button_prefs.LABELS)

# 外部链接（设置窗口里的快捷入口）
LINK_MINECRAFT = "https://www.minecraft.net/zh-hans"
LINK_GITHUB = "https://github.com/Dreamtell/Minecraft-Migration-Tool"

# 设置窗口的分区配色 / 图标（标题色、描边色、图标都是同一支主色）
_SECTION_STYLE = {
    "look":    ("外观与启动", "#8e24aa", "🎨"),
    "migrate": ("迁移行为", "#7e57c2", "🏷️"),
    "tags":    ("模组分类标签", "#00897b", "🌐"),
    "lock":    ("任务与锁定", "#e53935", "🔒"),
    "view":    ("放大查看窗口", "#00838f", "🗂"),
    "buttons": ("界面按钮（勾选显示 / 上下调整顺序）", "#00acc1", "🧩"),
    "close":   ("关闭与后台", "#fb8c00", "🚪"),
    "links":   ("快捷链接", "#43a047", "🔗"),
}

# 迁移时如何锁定主界面：不管选哪个，"所有操作按钮都会禁用"，区别只在盖不盖遮罩
_LOCK_MODES = (
    ("all",  "迁移时锁定界面：盖一层遮罩（正式迁移和模拟运行都锁）"),
    ("real", "只锁正式迁移：模拟运行不盖遮罩（按钮照样禁用）"),
    ("off",  "不锁屏：不盖遮罩，只把按钮全部禁用"),
)

# 迁移标记可选的符号：都是微软雅黑里有字形、且文件名安全的（不含 \ / : * ? " < > |）
_RENAME_MARKERS = ("★", "☆", "▶", "◆", "●", "✦", "✚", "【新】", "NEW_")

# 放大查看窗口的实现方式。PySide6 试点：圆角/阴影/逐帧动画是原生能力；
# 缺库或想用回老窗口时切 tk。
_BIG_VIEW_BACKENDS = (
    ("qt", "🗂 PySide6 试点窗口（圆角卡片 + 原生动画，需已安装 PySide6）"),
    ("tk", "🪟 经典 Tk 窗口（无需额外依赖）"),
)

# 「放大查看」打开时用哪个视图（表格 / 卡片）。两边都可以随时点按钮切换，
# 这里只是定"刚打开时是哪个"。
_BIG_VIEW_VIEWS = (
    ("table", "📋 表格视图（打开就是列表，能改勾选/编辑）"),
    ("cards", "🗂 卡片视图（打开就是卡片，只读预览）"),
)

# 按钮列表里每一排的主色 + 图标，用来给分组行上色
_GROUP_COLORS = {
    "path":        "#42a5f5",
    "path_target": "#26c6da",
    "mods":        "#66bb6a",
    "config":      "#ffa726",
    "action":      "#ef5350",
    "log_left":    "#ab47bc",
    "log_right":   "#8d6e63",
}
_GROUP_ICONS = {
    "path":        "📤",
    "path_target": "📥",
    "mods":        "🧩",
    "config":      "⚙️",
    "action":      "🚀",
    "log_left":    "📜",
    "log_right":   "🗂️",
}
_GROUP_ICONS.update({g[0]: g[2] for g in button_prefs.GROUPS})
_GROUP_COLORS.update({g[0]: g[3] for g in button_prefs.GROUPS})


def _mix(color_a, color_b, t):
    """把两个 #rrggbb 按比例混合（t=0 全取 a，t=1 全取 b）。

    分组的浅/深底色都靠它算：往主题自己的背景色上混，浅色主题出淡彩、
    深色主题出暗彩，一套规则两边都好看。
    """
    try:
        a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(
            max(0, min(255, int(round(a[i] + (b[i] - a[i]) * t)))) for i in range(3))
    except Exception:
        return color_a



def _grad_width(text):
    """按文字测量渐变按钮宽度（微软雅黑 9 粗体 + 内边距）。

    Font 对象缓存起来：每调一次就 new 一个 Font 是 tkinter 里出了名的慢，
    而按钮宽度在启动时要算几十次，累计能有几百毫秒。emoji 按上面说的常量补。
    """
    global _GRAD_FONT
    if _GRAD_FONT is None:
        _GRAD_FONT = tkfont.Font(family="微软雅黑", size=9, weight="bold")
    syms = _GRAD_SYM_RE.findall(text)
    plain = _GRAD_SYM_RE.sub("", text)
    extra = sum(_GRAD_ARROW_W if _GRAD_ARROW_RE.match(c) else _GRAD_SYM_W
                for c in syms)
    return int(_GRAD_FONT.measure(plain)) + extra + _GRAD_PAD


def _center_window(win, w, h):
    """把窗口在屏幕中央显示（w/h 为该窗口的目标尺寸）。"""
    center_window(win, w, h)


def _destroy_after_callback(win):
    """用一个"下一帧 + 父窗口"的回调来销毁 win，别在回调里直接 destroy 自己。

    after 回调收尾时 tkinter 还要 `self.deletecommand(name)`，而窗口已经销毁、
    `_tclCommands` 被置成 None，会抛 AttributeError：既往控制台吐一段红字，
    又会把同一时刻新登记的回调一起带走（实测能干掉主界面的淡入）。
    挂到父窗口（root）上做就没这个问题。
    """
    parent = getattr(win, "master", None) or win
    try:
        parent.after(1, win.destroy)
    except Exception:
        try:
            win.destroy()
        except Exception:
            pass


def _close_popup(win):
    """按"原版弹窗"的方式关窗：不做 alpha 淡化，交给 Windows 自己的关闭动画。

    窗口现在打开时也不再上 alpha，所以本来就不带 WS_EX_LAYERED；这里仍然先兜底
    摘一次——系统对 layered 窗口会跳过自己的隐藏动画（withdraw 之后直接消失），
    万一以后哪里又给窗口加了 -alpha，也不至于悄悄把这个效果弄没了。
    """
    try:
        if not win.winfo_exists():
            return
    except Exception:
        return
    try:
        clear_layered_style(win)
    except Exception:
        pass
    try:
        win.withdraw()
        win.after(200, win.destroy)
    except Exception:
        _destroy_after_callback(win)


def _file_task_lock(name):
    """装饰器：把整个方法登记成"文件类任务"。

    作用有两条：
    1. 期间禁止启动迁移（模拟运行也禁）—— 两个任务同时改同一批文件会互相踩；
    2. 结束后自动解除，方法里有多少个 return 都不会漏（用 try/finally）。
    """
    def deco(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if self._migration_running:
                messagebox.showwarning("提示", f"迁移进行中，暂不能{name}。")
                return None
            self._begin_file_task(name)
            try:
                return func(self, *args, **kwargs)
            finally:
                self._end_file_task()
        return wrapper
    return deco


class MigrationGUI:
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
        self.current_theme = self.config.get("theme", "light")
        self.theme = LIGHT_THEME if self.current_theme == "light" else DARK_THEME

        self.source_path = tk.StringVar(value=self.config.get("source", ""))
        self.target_path = tk.StringVar(value=self.config.get("target", ""))
        self.world_name = tk.StringVar(value=self.config.get("world", "老子的世界"))
        self.dry_run = tk.BooleanVar(value=self.config.get("dry_run", True))
        self.overwrite_mods = tk.BooleanVar(value=self.config.get("overwrite", False))
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
        # 放大查看窗口用哪个实现：qt=PySide6 试点（缺库时自动回落）/ tk=经典 Tk
        self.big_view_backend = str(self.config.get("big_view_backend", "qt") or "qt")
        if self.big_view_backend not in ("qt", "tk"):
            self.big_view_backend = "qt"
        # 放大查看窗口打开时用哪个视图：table=表格 / cards=卡片（设置里能选）
        self.big_view_view = str(self.config.get("big_view_view", "table") or "table")
        if self.big_view_view not in ("table", "cards"):
            self.big_view_view = "table"
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

        self._stage("正在构建界面…")
        self.create_widgets()
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
        self.mod_text.edit_reset()
        self.config_text.edit_reset()
        # 用自定义撤销栈替代 Tk 原生撤销（Tk 会把连续删除合并为一步撤销）
        self._setup_custom_undo(self.mod_text, "mod")
        self._setup_custom_undo(self.config_text, "config")
        self.log("=" * 60, level="INFO", save=False)
        self.log("【免费声明】本工具完全免费，严禁用于商业用途或转卖。", level="WARNING", save=False)
        self.log("如有任何收费行为，请立即举报。作者不会以任何形式向你收费。", level="WARNING", save=False)
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
            self._on_stage(text)
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
            self.diff_window = show_diff_window(self.root, data, self.theme,
                                                self.current_theme, apply_callback)
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
        config = {
            "source": self.source_path.get(),
            "target": self.target_path.get(),
            "world": self.world_name.get(),
            "dry_run": self.dry_run.get(),
            "overwrite": self.overwrite_mods.get(),
            "theme": self.current_theme,
            "mod_list": self.mod_text.get("1.0", tk.END).strip(),
            "config_list": self.config_text.get("1.0", tk.END).strip(),
            "edit_enabled": self.edit_mode.get(),
            "close_action": self.close_action,
            "splash": bool(getattr(self, "splash_enabled", True)),
            "silent_background": bool(getattr(self, "silent_background", False)),
            "buttons_hidden": list(getattr(self, "hidden_buttons", []) or []),
            "button_order": dict(getattr(self, "button_order", {}) or {}),
            "rename_migrated_mods": bool(getattr(self, "rename_migrated_mods", False)),
            "rename_marker": str(getattr(self, "rename_marker", "★")),
            "online_tags": bool(getattr(self, "online_tags", False)),
            "lock_mode": str(getattr(self, "lock_mode", "all")),
            "big_view_backend": str(getattr(self, "big_view_backend", "qt")),
            "big_view_view": str(getattr(self, "big_view_view", "table")),
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

    # 日志分类文字色 -> 主题键
    _LOG_COLOR_KEYS = {
        "INFO": "log_info_fg",
        "WARNING": "log_warning_fg",
        "ERROR": "log_error_fg",
        "SUCCESS": "log_success_fg",
        "SIMULATE": "log_simulate_fg",
    }
    _LOG_TAGS = tuple(_LOG_COLOR_KEYS)

    def init_log_colors(self):
        """按当前主题设置日志分类颜色（INFO/警告/错误/成功/模拟）。"""
        self._configure_log_colors(self.log_text)

    def _configure_log_colors(self, widget):
        """把日志分类色应用到指定控件的 tag 上（跟随主题，主日志与放大日志共用）。"""
        default = {"INFO": "gray", "WARNING": "orange", "ERROR": "red",
                   "SUCCESS": "green", "SIMULATE": "blue"}
        for tag, key in self._LOG_COLOR_KEYS.items():
            color = self.theme.get(key, default.get(tag, "gray"))
            try:
                widget.tag_config(tag, foreground=color)
            except Exception:
                pass

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
                    # 汇总标签是自己管颜色（_keep_fg），主题切换时得手动补一次
                    for attr, key in (("_sel_lbl", "ok_fg"), ("_stat_lbl", "muted_fg")):
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

        # 同步 PySide6 试点窗口（自绘 Qt 控件，主题得显式喂过去）
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
        # 圆角文本框的填充/描边跟着主题重画（模组清单 / config 清单 / 执行日志 / 日志放大查看）
        for _name in ("mod_text_box", "config_text_box", "log_text_box", "_log_big_box"):
            _box = getattr(self, _name, None)
            if _box is not None:
                try:
                    _box.refresh()
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
        if hasattr(self, 'edit_toolbar'):
            try:
                self.edit_toolbar.configure(bg=self.theme["bg"])
                self.edit_mode_cb.configure(
                    bg=self.theme["edit_bg"], fg=self.theme["fg"],
                    activebackground=self.theme["edit_bg"],
                    activeforeground=self.theme["fg"],
                    selectcolor=self.theme["edit_bg"])
                self.edit_warn_label.configure(bg=self.theme["bg"],
                                               fg=self.theme["fail_fg"])
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
            # 更新已打开的差异窗口
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

    # ---------- 按钮的显示/隐藏 与 排序 ----------
    def _group_of(self, key):
        for gkey, _label, _side in _BUTTON_GROUPS:
            if key in _DEFAULT_BUTTON_ORDER[gkey]:
                return gkey
        return None

    def _group_anchor(self, gkey):
        """这一排按钮后面还跟着别的控件时返回那个控件。

        必须用它当 pack 的 before= 锚点：pack_forget 之后再 pack 会排到队尾，
        不指定锚点的话按钮会跑到状态标签/图例的右边去。
        """
        return {
            "path": getattr(self, "source_status", None),
            "path_target": getattr(self, "target_status", None),
            "config": getattr(self, "_check_legend", None),
        }.get(gkey)

    def _resolved_order(self, gkey):
        """某一排的最终顺序：配置里的顺序 + 补上配置里还没有的新按钮。"""
        default = _DEFAULT_BUTTON_ORDER[gkey]
        keys = [k for k in (self.button_order.get(gkey) or []) if k in default]
        keys += [k for k in default if k not in keys]
        return keys

    # ---------- 按钮排布：place + 插值动画 ----------
    _ANIM_MS = 12              # 过渡帧间隔
    _ANIM_MAX_FRAMES = 40      # 兜底：万一位置算不收敛，最多跑这么多帧

    def _placeable_row(self, widgets):
        """这一排能不能用 place 摆？

        容器里混着别的控件（状态标签 / 图例）时不能：place 出来的按钮不占 pack
        的位置，那些兄弟控件会当按钮不存在，直接压到它们身上。
        """
        try:
            rows = {w.master for _k, w in widgets}
            if len(rows) != 1:
                return None
            row = rows.pop()
            group = {w for _k, w in widgets}
            if set(row.winfo_children()) - group:
                return None
            return row
        except Exception:
            return None

    def _place_row(self, row, entries, side, hidden, animate):
        """把一排按钮用 place 摆好；可见的按钮从当前位置平滑滑到目标位置。

        pack 是排不动的（要么原地要么跳），所以显隐/换序想有过渡只能用 place。
        """
        pad = 5
        try:
            row.update_idletasks()
        except Exception:
            pass
        vis_seq = [(k, w) for k, w in entries if k not in hidden]
        if side != "left":
            vis_seq = list(reversed(vis_seq))
        offsets, off = {}, pad
        for k, w in vis_seq:
            offsets[k] = off
            off += w.winfo_reqwidth() + pad * 2
        h = max([w.winfo_reqheight() for _k, w in entries] or [30])
        # 关掉 propagate 后容器不再按子控件算尺寸，宽高都得自己给：
        # fill 了 x/both 的排，宽度由父容器决定；其余（side="right" 那种）只能自己算，
        # 否则容器宽度塌成 1px，靠右对齐的按钮会被 place 到负数坐标上去。
        try:
            fill = str(row.pack_info().get("fill", "none"))
        except Exception:
            fill = "none"
        try:
            row.pack_propagate(False)
            if fill in ("x", "both"):
                row.configure(height=h)
            else:
                row.configure(height=h, width=max(1, off))
        except Exception:
            pass
        starts = {}
        for k, w in entries:
            try:
                starts[k] = w.winfo_x() if w.winfo_ismapped() else None
            except Exception:
                starts[k] = None
        for _k, w in entries:
            try:
                w.pack_forget()
            except Exception:
                pass
        # 动画待办**按排**存：以前是一个全局列表，每摆一排就把别排还没跑完的帧全取消掉，
        # 于是"最后摆的那排"正常、前面的排停在半路（表现就是按钮重叠/被裁）。
        # _apply_button_layout 是按组顺序摆的，排在后面的组会把前面组的动画掐死。
        anim_jobs = getattr(self, "_btn_anim_jobs", None)
        if not isinstance(anim_jobs, dict):
            anim_jobs = self._btn_anim_jobs = {}
        row_key = str(row)
        for j in anim_jobs.pop(row_key, []):
            try:
                self.root.after_cancel(j)
            except Exception:
                pass
        jobs = anim_jobs[row_key] = []

        def place_at(w, x):
            y = max(0, (h - w.winfo_reqheight()) // 2)
            if side == "left":
                w.place(x=x, y=y, anchor="nw")
            else:
                # 靠右对齐：用 relx=1.0 定位，窗口拉宽拉窄都跟着右边缘走
                w.place(relx=1.0, x=-(x + w.winfo_reqwidth()), y=y, anchor="nw")

        for k, w in entries:
            if k in hidden:
                try:
                    w.place_forget()
                except Exception:
                    pass
        if not animate or not vis_seq or not row.winfo_ismapped():
            # 还没显示出来时（启动阶段）直接摆到位，别在后台空跑一遍动画
            for k, w in vis_seq:
                try:
                    place_at(w, offsets[k])
                except Exception:
                    pass
            return
        pos = {}
        for k, w in vis_seq:
            start = starts.get(k)
            if start is None:      # 新出现的：从旁边 26px 滑进来，而不是凭空冒出
                start = offsets[k] - 26 if side == "left" else offsets[k] + 26
            pos[k] = float(start)

        def step(n):
            done = True
            for k, w in vis_seq:
                tgt = float(offsets[k])
                cur = pos[k]
                if abs(cur - tgt) < 1.0:
                    cur = tgt
                else:
                    cur += (tgt - cur) * 0.34
                    done = False
                pos[k] = cur
                try:
                    place_at(w, cur)
                except Exception:
                    pass
            if not done and n < self._ANIM_MAX_FRAMES:
                try:
                    jobs.append(self.root.after(self._ANIM_MS, lambda: step(n + 1)))
                except Exception:
                    pass
            elif done:
                anim_jobs.pop(row_key, None)      # 这一排跑完了，登记表里不留空的
        step(0)

    def _apply_button_layout(self, animate=True):
        """按"显示/隐藏 + 自定义顺序"重新摆各排按钮。

        容器里全是本排按钮的排走 place+插值（显隐/换序有一段平滑滑动）；
        混着状态标签的那种排仍用 pack，行为跟以前完全一样。
        """
        hidden = set(self.hidden_buttons or ())
        for gkey, _label, side in _BUTTON_GROUPS:
            order = self._resolved_order(gkey)
            entries = [(k, self._btn_widgets[k]) for k in order if k in self._btn_widgets]
            if not entries:
                continue
            # 先把这一排全部撤下来（含被隐藏的）：只重新 pack 显示的那些是不够的，
            # 之前已经摆上去的隐藏按钮会原地不动，"隐藏"等于没生效。
            for _k, w in entries:
                try:
                    w.pack_forget()
                except Exception:
                    pass
            row = self._placeable_row(entries)
            if row is not None:
                try:
                    self._place_row(row, entries, side, hidden, animate)
                    continue
                except Exception:
                    # place 这条路出问题就地回滚到 pack，别让界面摆不正
                    for _k, w in entries:
                        try:
                            w.place_forget()
                        except Exception:
                            pass
                    try:
                        row.pack_propagate(True)
                    except Exception:
                        pass
            shown = [k for k, _w in entries if k not in hidden]
            # 靠右对齐的那两排：反过来摆，列表顺序就是从左到右看到的顺序
            seq = shown if side == "left" else list(reversed(shown))
            anchor = self._group_anchor(gkey)
            for key in seq:
                w = self._btn_widgets[key]
                kwargs = {"side": side, "padx": 5}
                try:
                    if anchor is not None and anchor.winfo_exists():
                        kwargs["before"] = anchor
                    w.pack(**kwargs)
                except Exception:
                    try:
                        w.pack(side=side, padx=5)
                    except Exception:
                        pass

    def set_button_hidden(self, key, hidden):
        """在设置里勾/取消某个按钮的显示。"""
        keys = set(self.hidden_buttons or ())
        keys.add(key) if hidden else keys.discard(key)
        self.hidden_buttons = sorted(keys)
        button_prefs.update(self.hidden_buttons, self.button_order)
        self._apply_button_layout()
        self.save_config()
        self._refresh_button_tree()

    def move_button(self, key, delta):
        """在所属那一排里把按钮上移/下移一格（整表顺序里含已隐藏的项）。"""
        gkey = self._group_of(key)
        if gkey is None:
            return False
        order = self._resolved_order(gkey)
        i = order.index(key)
        j = i + delta
        if j < 0 or j >= len(order):
            return False
        order[i], order[j] = order[j], order[i]
        self.button_order[gkey] = order
        button_prefs.update(self.hidden_buttons, self.button_order)
        self._apply_button_layout()
        self.save_config()
        self._refresh_button_tree()
        return True

    def reset_button_layout(self):
        """恢复默认：全部显示 + 默认顺序。"""
        self.hidden_buttons = []
        self.button_order = {}
        button_prefs.update(self.hidden_buttons, self.button_order)
        self._apply_button_layout()
        self.save_config()
        self._refresh_button_tree()
        self.log("🧩 界面按钮已恢复默认显示与顺序", level="INFO", save=False)

    def _pack_window_btns(self, gkey, row, entries, side="left", padx=5):
        """按「界面按钮」的配置摆一个窗口工具栏：隐藏的不摆、顺序照配置。

        entries 是 [(key, widget)]，按**默认顺序**给；widget 为 None 的直接跳过
        （例如 config 清单里没有"添加模组"那个按钮）。
        这类窗口每次打开现建，所以设置改完是下次打开生效。
        """
        table = {k: w for k, w in entries if w is not None}
        seq = [k for k in button_prefs.keys(gkey) if k in table]
        # 兜底：完全没登记进表的按钮也得摆出来（但不能把"被隐藏"的又加回来）
        known = set(button_prefs.DEFAULTS.get(gkey, ()))
        seq += [k for k in table if k not in known]
        if side != "left":
            seq = list(reversed(seq))                  # 靠右摆：先摆的最靠右
        for key in seq:
            try:
                table[key].pack(side=side, padx=padx)
            except Exception:
                pass
        return seq

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
        pages = []

        def tab(label):
            page = tabs.page(label)
            pages.append(page)
            return page

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

        def radio(parent, text, value, var, command, **kw):
            return tk.Radiobutton(
                parent, text=text, value=value, variable=var, command=command,
                bg=self.theme["bg"], fg=self.theme["fg"],
                activebackground=self.theme["bg"], activeforeground=self.theme["fg"],
                selectcolor=self.theme.get("entry_bg", self.theme["bg"]),
                highlightthickness=0, bd=0, font=("微软雅黑", 9),
                anchor="w", **kw)

        def check(parent, text, var, command):
            return tk.Checkbutton(
                parent, text=text, variable=var, command=command,
                bg=self.theme["bg"], fg=self.theme["fg"],
                activebackground=self.theme["bg"], activeforeground=self.theme["fg"],
                selectcolor=self.theme.get("entry_bg", self.theme["bg"]),
                highlightthickness=0, bd=0, font=("微软雅黑", 9), anchor="w")

        # ---------- 外观与启动 ----------
        box1 = section("look", page_look)
        self.settings_theme_var = tk.StringVar(value=self.current_theme)
        row_theme = tk.Frame(box1, bg=self.theme["bg"])
        row_theme.pack(fill="x")
        tk.Label(row_theme, text="主题：", bg=self.theme["bg"],
                 fg=self.theme["fg"], font=("微软雅黑", 9)).pack(side="left")
        for value, text in (("light", "浅色"), ("dark", "深色")):
            radio(row_theme, text, value, self.settings_theme_var,
                  lambda v=value: self.choose_theme(v)).pack(side="left", padx=(0, 18))

        self.settings_splash_var = tk.BooleanVar(value=self.splash_enabled)
        check(box1, "启用启动动画（下次启动程序生效）", self.settings_splash_var,
              self._toggle_splash).pack(fill="x", pady=(6, 0))

        # ---------- 迁移行为 ----------
        box_m = section("migrate", page_mig)
        self.settings_rename_var = tk.BooleanVar(value=self.rename_migrated_mods)
        check(box_m, "复制过去的模组加标记前缀（方便在目标 mods 里一眼认出）",
              self.settings_rename_var, self._toggle_rename_marker).pack(fill="x")
        row_mark = tk.Frame(box_m, bg=self.theme["bg"])
        row_mark.pack(fill="x", pady=(4, 0))
        tk.Label(row_mark, text="标记：", bg=self.theme["bg"], fg=self.theme["fg"],
                 font=("微软雅黑", 9)).pack(side="left")
        self.settings_marker_var = tk.StringVar(value=self.rename_marker)
        for mark in _RENAME_MARKERS:
            radio(row_mark, mark, mark, self.settings_marker_var,
                  lambda m=mark: self._set_rename_marker(m)).pack(side="left", padx=1)
        self.settings_marker_preview = tk.Label(
            box_m, text="", bg=self.theme["bg"],
            fg=self.theme.get("muted_fg", self.theme["fg"]), font=("微软雅黑", 8))
        self.settings_marker_preview.pack(anchor="w", pady=(4, 0))
        self._update_marker_preview()

        # ---------- 模组分类标签 ----------
        box_t = section("tags", page_mig)
        self.settings_tags_var = tk.BooleanVar(value=getattr(self, "online_tags", False))
        check(box_t, "联网获取真实分类（Modrinth；默认关闭）",
              self.settings_tags_var, self._toggle_online_tags).pack(fill="x")
        tk.Label(box_t,
                 text="关闭时按关键词推测（标注“推测”的就是它）。开启后扫描会在后台联网查询，"
                      "结果缓存到本地；断网或匹配不到时自动沿用推测结果。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w", pady=(4, 0))
        self.settings_tag_cache_lbl = tk.Label(
            box_t, text="", bg=self.theme["bg"],
            fg=self.theme.get("muted_fg", self.theme["fg"]), font=("微软雅黑", 8))
        self.settings_tag_cache_lbl.pack(anchor="w", pady=(2, 0))
        self._update_tag_cache_label()

        # ---------- 任务与锁定 ----------
        box_lock = section("lock", page_mig)
        self.settings_lock_var = tk.StringVar(value=getattr(self, "lock_mode", "all"))
        for value, text in _LOCK_MODES:
            radio(box_lock, text, value, self.settings_lock_var,
                  lambda v=value: self._set_lock_mode(v)).pack(fill="x")
        tk.Label(box_lock,
                 text="三个选项都只是「盖不盖遮罩」的区别：迁移期间所有操作按钮一律禁用，"
                      "执行日志始终留着，方便看进度。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w",
                                                                          pady=(4, 0))

        # ---------- 放大查看窗口用什么实现 ----------
        box_view = section("view", page_view)
        self.settings_view_var = tk.StringVar(value=getattr(self, "big_view_backend", "qt"))
        _qt_ok, _qt_why = self._qt_available()
        for value, text in _BIG_VIEW_BACKENDS:
            radio(box_view, text, value, self.settings_view_var,
                  lambda v=value: self._set_big_view_backend(v)).pack(fill="x")
        tk.Label(box_view,
                 text=("PySide6 当前%s。%s"
                       % ("可用" if _qt_ok else "不可用", _qt_why)),
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left", wraplength=580).pack(anchor="w",
                                                                          pady=(4, 0))
        tk.Label(box_view, text="打开时用哪个视图：", bg=self.theme["bg"],
                 fg=self.theme["fg"], font=("微软雅黑", 9)).pack(anchor="w", pady=(10, 2))
        self.settings_view_mode_var = tk.StringVar(
            value=getattr(self, "big_view_view", "table"))
        for value, text in _BIG_VIEW_VIEWS:
            radio(box_view, text, value, self.settings_view_mode_var,
                  lambda v=value: self._set_big_view_view(v)).pack(fill="x")

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
        for value, text in (("tray", "收进系统托盘，程序继续在后台跑"),
                            ("exit", "直接退出程序"),
                            ("ask", "每次问我")):
            radio(box3, text, value, self.settings_close_var,
                  lambda v=value: self.set_close_action(v)).pack(fill="x")
        self.settings_silent_var = tk.BooleanVar(value=self.silent_background)
        check(box3, "后台静默执行任务（不弹进度/结果窗口，完成后系统通知）",
              self.settings_silent_var, self._toggle_silent).pack(fill="x", pady=(6, 0))

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
        tk.Label(box4, text=f"{LINK_GITHUB}\n欢迎反馈问题或提交建议。",
                 bg=self.theme["bg"], fg=self.theme.get("muted_fg", self.theme["fg"]),
                 font=("微软雅黑", 8), justify="left").pack(anchor="w", pady=(4, 0))

        row = tk.Frame(win, bg=self.theme["bg"])
        row.pack(fill="x", padx=14, pady=14)
        create_gradient_button(row, "关闭", win.destroy,
                               colors=("#757575", "#9e9e9e"),
                               width=90, height=30,
                               font=("微软雅黑", 9, "bold")).pack(side="right")

        self._refresh_button_tree()
        self._theme_settings_tree()

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.update_idletasks()
        # 按**最大的那一页**定窗口尺寸：换页时窗口不跳、内容也不会被裁
        # （Notebook 只会请求当前页的尺寸，只看它会把别的页裁掉）
        try:
            pw = max(p.winfo_reqwidth() for p in pages) + 70
            ph = max(p.winfo_reqheight() for p in pages) + 110
        except Exception:
            pw = ph = 0
        center_window(win, max(pw, 700), max(ph, 520))
        win.deiconify()
        focus_window(win)

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
            parent = tree.insert("", "end", iid=f"grp:{gkey}",
                                 text=f" {_GROUP_ICONS.get(gkey, '')} {glabel}",
                                 values=("",), open=True, tags=(f"grp_{gkey}",))
            for key in self._resolved_order(gkey):
                # 主界面那几排看控件登记表；窗口工具栏的按钮（放大查看/日志放大查看）
                # 不在登记表里 —— 它们是那个窗口打开时现建的，按分组认就行
                if key not in self._btn_widgets and button_prefs.group_of(key) != gkey:
                    continue
                is_hidden = key in hidden
                tree.insert(parent, "end", iid=f"btn:{key}",
                            text="   " + _BUTTON_LABELS.get(key, key),
                            values=("☐ 隐藏" if is_hidden else "☑ 显示",),
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

    def _toggle_splash(self):
        """启动动画开关：app.py 在创建闪屏前会直接读配置文件。"""
        self.splash_enabled = bool(self.settings_splash_var.get())
        self.save_config()
        self.log(f"🎬 启动动画已{'启用' if self.splash_enabled else '关闭'}"
                 f"（下次启动程序生效）", level="INFO", save=False)

    # ---------- 迁移标记 ----------
    def _toggle_rename_marker(self):
        """是否给复制过去的模组加前缀标记。"""
        self.rename_migrated_mods = bool(self.settings_rename_var.get())
        self._update_marker_preview()
        self.save_config()
        self.log(f"🏷️ 模组迁移标记已{'开启' if self.rename_migrated_mods else '关闭'}"
                 + (f"（前缀「{self.rename_marker} 」）" if self.rename_migrated_mods else ""),
                 level="INFO", save=False)

    def _set_rename_marker(self, mark):
        """换一个标记符号。"""
        self.rename_marker = mark or "★"
        self._update_marker_preview()
        self.save_config()
        self.log(f"🏷️ 迁移标记符号已改为「{self.rename_marker}」", level="INFO", save=False)

    def _update_marker_preview(self):
        """给用户看一眼实际效果（关着的时候也显示，方便先挑）。"""
        lbl = getattr(self, "settings_marker_preview", None)
        if lbl is None:
            return
        try:
            state = "已开启" if getattr(self, "rename_migrated_mods", False) else "未开启"
            lbl.config(text=f"效果（{state}）：{self.rename_marker} "
                            f"create-1.20.1-6.0.9.jar")
        except Exception:
            pass

    def _set_lock_mode(self, value):
        """迁移时怎么锁主界面（只影响"盖不盖遮罩"，按钮一律禁用）。"""
        self.lock_mode = value if value in ("all", "real", "off") else "all"
        self.save_config()
        text = dict(_LOCK_MODES).get(self.lock_mode, self.lock_mode)
        self.log(f"🔒 迁移锁定方式已改为：{text}", level="INFO", save=False)

    # ------------------------------------------------------------------ #
    # 放大查看：PySide6 试点窗口（Tk 主窗口 + root.after 驱动 Qt 事件循环）
    # ------------------------------------------------------------------ #
    def _qt_available(self):
        """PySide6 是否可用。返回 (bool, 给用户看的原因)。"""
        cached = getattr(self, "_qt_ok_cache", None)
        if cached is not None:
            return cached
        try:
            from ui import qt_big_view as _q      # 模块顶层 import PySide6
            _q.available()
            reason = "窗口是 Qt 无边框圆角窗；关掉它不会影响主窗口。"
            cached = (True, reason)
        except Exception as e:
            # 失败不缓存：用户中途 pip install 了 PySide6，下次打开就能直接用上
            return (False, "装好 PySide6 后可切到试点窗口：pip install PySide6"
                           f"（{type(e).__name__}: {e}）")
        self._qt_ok_cache = cached
        return cached

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

    def _set_big_view_view(self, value):
        """放大查看窗口默认用哪个视图（下一次打开生效）。"""
        self.big_view_view = value if value in ("table", "cards") else "table"
        self.save_config()
        self.log("🗂 放大查看窗口默认视图：%s"
                 % ("卡片视图" if self.big_view_view == "cards" else "表格视图"),
                 level="INFO", save=False)

    def _qt_apply_entries(self, text_widget, entries):
        """Qt 窗口关闭时把清单写回主界面（等价于 Tk 版的 write_back）。"""
        content = "\n".join(entries)
        try:
            first = text_widget.yview()[0]
        except Exception:
            first = 0.0
        text_widget.configure(state=tk.NORMAL)
        text_widget.edit_separator()
        text_widget.delete("1.0", tk.END)
        text_widget.insert("1.0", content + ("\n" if content else ""))
        text_widget.edit_separator()
        try:
            text_widget.yview_moveto(first)
        except Exception:
            pass
        self._update_text_states()
        self.save_config()

    def _pump_qt(self):
        """Tk 的 after 循环里驱动 Qt 事件。

        两个事件循环同线程共存：Qt 的所有回调（点击/动画/绘制）都在这个
        after 回调里被调用，所以它们跑在主线程，从里面改 Tk 控件是安全的。
        """
        views = [v for v in getattr(self, "_qt_views", []) if v.is_alive()]
        self._qt_views = views
        for v in views:
            try:
                v.pump()
            except Exception as e:
                self.log(f"⚠ PySide6 窗口事件循环异常：{e}", level="ERROR", save=False)
        if views:
            self._qt_pump_after = self.root.after(12, self._pump_qt)

    def _open_big_view_qt(self, source_text, title):
        """用 PySide6 打开"放大查看"。返回 True 表示已接管。"""
        try:
            from ui import qt_big_view as Q
        except Exception as e:
            self.log(f"⚠ 无法加载 PySide6 窗口（{e}），已回退到经典窗口", level="ERROR", save=False)
            return False
        is_mod = "模组" in title or source_text is getattr(self, "mod_text", None)
        entries = [ln.strip() for ln in source_text.get("1.0", tk.END).splitlines() if ln.strip()]
        sp = self.source_path.get().strip() if hasattr(self, "source_path") else ""
        try:
            view = Q.show_big_view(
                entries, is_mod, sp, title, self.theme,
                online_tags=bool(getattr(self, "online_tags", False)),
                cards=(getattr(self, "big_view_view", "table") == "cards"),
                hooks={"write_back": lambda texts: self._qt_apply_entries(source_text, texts)})
        except Exception as e:
            self.log(f"⚠ PySide6 窗口创建失败：{e}", level="ERROR", save=False)
            return False
        if not hasattr(self, "_qt_views"):
            self._qt_views = []
        self._qt_views.append(view)
        if not self._qt_views[:-1]:
            self._pump_qt()          # 首个窗口才需要启动泵
        self.log(f"🗂 已打开 PySide6 试点窗口：{title}（{len(entries)} 项）", level="INFO", save=False)
        return True

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
            lbl.config(text=f"本地分类缓存：{tag_cache_size()} 条"
                            f"（{TAG_CACHE_FILE}，删掉它会重新联网查）")
        except Exception:
            pass

    def _toggle_silent(self):
        """后台静默执行开关。"""
        self.silent_background = bool(self.settings_silent_var.get())
        self.save_config()
        self.log(f"🤫 后台静默执行已{'开启' if self.silent_background else '关闭'}"
                 f"（跑任务时不再弹进度/结果窗口）", level="INFO", save=False)

    def open_link(self, url):
        """打开外部链接（官网 / GitHub 仓库）。"""
        try:
            import webbrowser
            webbrowser.open_new_tab(url)
            self.log(f"🔗 已打开链接：{url}", level="INFO", save=False)
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开链接：{e}", parent=self.settings_win)

    def create_tooltip(self, widget, text):
        def enter(event):
            self.tooltip = tk.Toplevel(widget)
            self.tooltip.wm_overrideredirect(True)
            self.tooltip.wm_geometry(f"+{event.x_root + 10}+{event.y_root + 10}")
            label = tk.Label(self.tooltip, text=text,
                             background=self.theme["tooltip_bg"], fg=self.theme["label_fg"], relief="solid",
                             borderwidth=1, font=("微软雅黑", 9))
            label.pack()

        def leave(event):
            if hasattr(self, 'tooltip'):
                self.tooltip.destroy()

        # 必须 add="+"：tkinter 的 bind 默认是覆盖，直接绑会把按钮自己那套
        # 悬停高亮顶掉——之前"从变更日志导入""← 使用新版路径填充"这两个带提示的
        # 按钮鼠标放上去毫无反应，就是被这里吃掉的。
        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")

    def _is_valid_instance(self, path_str):
        """
        严格检查路径是否为有效的 Minecraft 整合包实例（借鉴 PCL2 验证逻辑）
        返回: (is_valid, reason, details_dict)
        """
        p = Path(path_str)
        details = {}

        # ----- 第1层：基础路径检查 -----
        if not p.exists():
            return False, "路径不存在", details
        if not p.is_dir():
            return False, "不是目录", details

        # 检查读写权限（尝试创建临时文件）
        try:
            test_file = p / ".permission_test"
            test_file.touch()
            test_file.unlink()
            details["read_write"] = True
        except:
            details["read_write"] = False
            return False, "无读写权限，请以管理员身份运行", details

        # 检查路径是否包含中文（警告级别）
        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in str(p))
        if has_chinese:
            details["has_chinese"] = True
            # 不是致命错误，但给出警告

        # ----- 第2层：核心标识文件检查 -----
        # 2.1 检查关键子目录
        has_mods = (p / "mods").exists() and (p / "mods").is_dir()
        has_config = (p / "config").exists() and (p / "config").is_dir()
        has_saves = (p / "saves").exists() and (p / "saves").is_dir()
        has_libraries = (p / "libraries").exists() and (p / "libraries").is_dir()
        has_versions = (p / "versions").exists() and (p / "versions").is_dir()

        details["has_mods"] = has_mods
        details["has_config"] = has_config
        details["has_saves"] = has_saves
        details["has_libraries"] = has_libraries

        # 2.2 检查 Minecraft 核心标识文件
        has_options = (p / "options.txt").exists()
        has_launcher_profiles = (p / "launcher_profiles.json").exists()

        details["has_options"] = has_options
        details["has_launcher_profiles"] = has_launcher_profiles

        # 2.3 检查版本目录下的核心文件
        version_dirs = []
        if has_versions:
            for v_dir in (p / "versions").iterdir():
                if v_dir.is_dir():
                    version_json = v_dir / "version.json"
                    if version_json.exists():
                        version_dirs.append(v_dir.name)
            details["valid_versions"] = version_dirs

        # 2.4 检查 Fabric/Forge 标识（如果存在 mods 目录）
        if has_mods:
            mods_dir = p / "mods"
            jar_files = list(mods_dir.glob("*.jar"))
            details["mod_count"] = len(jar_files)
            # 检查是否有 Fabric 或 Forge 模组
            fabric_mods = list(mods_dir.glob("*fabric*.jar")) + list(mods_dir.glob("*.fabric.mod.json*"))
            forge_mods = list(mods_dir.glob("*forge*.jar"))
            details["fabric_mods"] = len(fabric_mods) > 0
            details["forge_mods"] = len(forge_mods) > 0

        # 2.5 检查 PCL2 特有标识
        has_pcl_ini = (p / "PCL.ini").exists()
        details["has_pcl_ini"] = has_pcl_ini

        # ----- 第3层：综合判断 -----
        # 判断标准：
        # 1. 必须有 mods 和 config（整合包基本要素）
        if not has_mods:
            return False, "缺少 mods 目录（不是有效的整合包）", details
        if not has_config:
            return False, "缺少 config 目录（不是有效的整合包）", details

        # 2. mods 目录不能为空
        if details.get("mod_count", 0) == 0:
            return False, "mods 目录为空（没有模组文件）", details

        # 3. 必须有至少一个有效版本（有 version.json）
        if not version_dirs:
            # 如果没有 version.json，但 options.txt 存在，可能是旧版整合包
            if not has_options:
                return False, "缺少 version.json 或 options.txt，无法识别为有效实例", details

        # 通过所有检查
        details["is_valid"] = True
        return True, "✅ 有效实例目录", details

    # 状态标签的语义色：颜色由"状态"决定，但主题一变就得立刻换成新主题里的那支色。
    _SEMANTIC_FG = {"ok": "ok_fg", "fail": "fail_fg", "muted": "muted_fg"}

    def _set_status_semantic(self, label, kind, text=None):
        """给状态标签上语义色，并记住是哪一种。

        为什么要记：切主题时应用新颜色**不能等重新校验**——validate_path 要扫
        实例目录（模组多的实例要好几百毫秒），那期间标签会一直挂着旧主题的颜色，
        看起来就是"有颜色的文字闪一下"。记住状态后，apply_theme 里可以直接换色。
        """
        try:
            label._semantic = kind
            color = self.theme.get(self._SEMANTIC_FG.get(kind, "muted_fg"),
                                   self.theme["fg"])
            if text is None:
                label.config(fg=color)
            else:
                label.config(text=text, fg=color)
        except Exception:
            pass

    def _apply_status_semantic_colors(self):
        """切主题时按上次记住的状态，立刻把三个状态标签的颜色换成新主题的。"""
        for name in ("source_status", "target_status", "world_status"):
            label = getattr(self, name, None)
            if label is None:
                continue
            self._set_status_semantic(label, getattr(label, "_semantic", "muted"))

    def validate_path(self, path_str, status_label, label_text):
        """
        验证路径是否为有效的 Minecraft 整合包实例（增强版）
        """
        if not path_str:
            self._set_status_semantic(status_label, "muted", "（未选择）")
            return

        is_valid, reason, details = self._is_valid_instance(path_str)

        # 构建详细状态信息
        status_text = reason
        if is_valid:
            # 显示更多细节
            details_text = []
            if details.get("has_saves"):
                details_text.append("有存档")
            if details.get("mod_count", 0) > 0:
                details_text.append(f"{details['mod_count']}个模组")
            if details.get("valid_versions"):
                details_text.append(f"版本: {', '.join(details['valid_versions'][:3])}")
            if details.get("has_launcher_profiles"):
                details_text.append("✅ 官方启动器")
            if details.get("has_pcl_ini"):
                details_text.append("✅ PCL2")
            if details.get("fabric_mods"):
                details_text.append("Fabric")
            if details.get("forge_mods"):
                details_text.append("Forge")

            # 如果有警告信息（如中文路径），在状态标签中显示
            if details.get("has_chinese"):
                status_text = "✅ 有效（⚠️ 路径含中文，建议改为纯英文）"
            elif details_text:
                status_text = f"✅ 有效 ({', '.join(details_text[:4])})"
            else:
                status_text = "✅ 有效实例目录"

            status_label.config(text=status_text, fg=self.theme["ok_fg"])
            status_label._semantic = "ok"

            # 记录详细验证信息到日志（可选）
            # self.log(f"路径验证通过: {path_str}", level="INFO")
            # self.log(f"  详细信息: {details}", level="INFO")
        else:
            self._set_status_semantic(status_label, "fail", f"❌ {reason}")

    def on_path_change(self, *args):
        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        self.validate_path(src, self.source_status, "源")
        self.validate_path(tgt, self.target_status, "目标")
        self._update_world_status()
        # 源路径变化后重新按目录识别 config 文件夹条目（末尾加 "/"）
        try:
            self._mark_config_folders()
        except Exception:
            pass
        self.save_config()

    # ---------- 日志 ----------
    # 明显无用的提示（纯 UI 反馈，不算操作记录），保存到日志文件时过滤掉；屏幕仍会显示。
    _LOG_TRIVIAL = (
        "已将目标路径复制到源路径",
        "目标路径为空，无法复制",
        "主题已切换为",
        "路径验证通过",
        "ℹ️ 原生对话框不可用",
        "📋 日志已清空",
    )

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

    def _fade_text(self, widget, ranges, from_color, to_color, frames=12, frame_ms=20,
                   indent_from=None, indent_to=None, on_done=None):
        """给若干段文字做动画（ranges = [(起, 止), …] 的 Text 索引对）。

        两个可插值的量：
        - foreground：底色 ↔ 目标色，看着就是淡入/淡出；
        - lmargin1/lmargin2：左边距，看着就是"滑进来/滑出去"。
        实测**位移比变色显眼得多**，所以两样一起做；只改颜色 6 帧 72ms 那版基本看不出来。
        动画跑完把临时 tag 撤掉，让原本的着色（如日志级别色）重新生效。
        """
        ranges = [(a, b) for a, b in (ranges or []) if a and b]
        if not ranges or (str(from_color) == str(to_color) and indent_from is None):
            if on_done:
                on_done()
            return
        self._fade_seq = getattr(self, "_fade_seq", 0) + 1
        tag = "fadeanim%d" % self._fade_seq
        try:
            widget.tag_configure(tag, foreground=from_color)
            if indent_from is not None:
                widget.tag_configure(tag, lmargin1=int(indent_from),
                                     lmargin2=int(indent_from))
            for a, b in ranges:
                widget.tag_add(tag, a, b)
        except Exception:
            if on_done:
                on_done()
            return
        state = {"i": 0}

        def step():
            i = state["i"]
            if i >= frames:
                try:
                    widget.tag_remove(tag, "1.0", tk.END)
                except Exception:
                    pass
                if on_done:
                    on_done()
                return
            state["i"] = i + 1
            t = state["i"] / frames
            try:
                opts = {"foreground": self._lerp_color(from_color, to_color, t)}
                if indent_from is not None:
                    off = int(indent_from + (indent_to - indent_from) * t)
                    opts["lmargin1"] = off
                    opts["lmargin2"] = off
                widget.tag_configure(tag, **opts)
            except Exception:
                pass
            try:
                self.root.after(frame_ms, step)
            except Exception:
                pass
        try:
            # 延后一帧再开始，确保"透明态"先真正显示出来，否则会先正常显示再跳回透明
            self.root.after(1, step)
        except Exception:
            pass

    def _fade_text_lines(self, widget, lines, from_color, to_color, **kw):
        """按整行淡入/淡出（lines 是 1-based 行号）。"""
        ranges = []
        for n in sorted({int(x) for x in (lines or [])}):
            ranges.append(("%d.0" % n, "%d.end" % n))
        self._fade_text(widget, ranges, from_color, to_color, **kw)

    def _roll_counter(self, label, text):
        """让"共 N 项"这类计数滚到新值（格式没变才滚，否则直接换文本）。"""
        try:
            old = label.cget("text") or ""
        except Exception:
            return
        if old == text:
            return
        pat = re.compile(r"\d+")
        old_t = pat.sub("{}", old)
        new_t = pat.sub("{}", text)
        old_n = pat.findall(old)
        new_n = pat.findall(text)
        if old_t != new_t or not old_n or len(old_n) != len(new_n):
            try:
                label.config(text=text)
            except Exception:
                pass
            return
        parts = re.split(r"(\d+)", text)
        slots = [i for i, p in enumerate(parts) if p.isdigit()]
        steps = 12          # 12 步 × 30ms ≈ 360ms：8 步 208ms 太快，像直接跳过去
        tick = 30

        def render(t):
            out = list(parts)
            for slot, a, b in zip(slots, old_n, new_n):
                out[slot] = str(int(int(a) + (int(b) - int(a)) * t))
            return "".join(out)

        def step(i):
            try:
                if i >= steps:
                    label.config(text=text)
                    return
                label.config(text=render(i / steps))
                self.root.after(tick, lambda: step(i + 1))
            except Exception:
                pass
        step(1)

    def _fade_log_line(self, start, end, level):
        """日志新行淡入。

        只在"慢速零散输出"时做：迁移时日志会成批涌入（一次十几行），
        每行都跑动画既积压又晃眼 —— 最近 200ms 超过 3 行就直接显示。
        """
        now = time.time()
        recent = getattr(self, "_log_recent", None)
        if recent is None:
            recent = self._log_recent = []
        recent.append(now)
        del recent[:-10]
        if sum(1 for t in recent if now - t <= 0.2) > 3:
            return
        if getattr(self, "_log_fading", False):
            return
        self._log_fading = True
        color = self.theme.get(self._LOG_COLOR_KEYS.get(level, "log_info_fg"),
                              self.theme.get("log_fg", "#000000"))
        # 9 帧 × 14ms ≈ 126ms：原来 14×18≈250ms 太慢，日志一行一行冒出来时明显拖沓。
        # 位移同步从 44px 收到 26px —— 时长减半、速度（px/ms）基本不变，才不会"闪一下"。
        self._fade_text(self.log_text, [(start, end)],
                        self.theme.get("log_bg", "#ffffff"), color,
                        frames=9, frame_ms=14,
                        indent_from=26, indent_to=0,
                        on_done=lambda: setattr(self, "_log_fading", False))

    def log(self, message, level="INFO", save=True):
        def _log():
            self.log_text.configure(state="normal")
            start = self.log_text.index("end-1c")
            self.log_text.insert(tk.END, message + "\n", level)
            end = self.log_text.index("end-1c")
            # 用户手动往上翻的时候别把他拽回底部（滚回底部会自动恢复跟随）
            if getattr(self, "_log_follow", True):
                self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
            # 锁屏里那份日志是"第二个视图"：主日志照常记，这里同步追加一份
            lock_log = getattr(self, "_lock_log_text", None)
            if lock_log is not None:
                try:
                    lock_log.configure(state="normal")
                    lock_log.insert(tk.END, message + "\n", level)
                    lock_log.configure(state="disabled")
                    lock_log.see(tk.END)
                except Exception:
                    self._lock_log_text = None
            try:
                self._fade_log_line(start, end, level)
            except Exception:
                pass
            self.root.update_idletasks()
            # 通知监听方（如"日志放大查看"窗口）即时同步，避免轮询/手动刷新
            try:
                self.log_text.event_generate("<<LogChanged>>")
            except Exception:
                pass

        # 抑制连续完全相同的记录（避免"请勿频繁操作"等警告/重复信息刷屏）
        key = (level, message)
        if key == getattr(self, '_last_log_key', None):
            return
        self._last_log_key = key

        if save and self._persistable(level, message):
            if not hasattr(self, '_saved_logs'):
                self._saved_logs = []
            self._saved_logs.append(message + "\n")
            if len(self._saved_logs) >= self._log_cache_limit:
                try:
                    self._flush_logs_to_file()
                except Exception:
                    pass

        self.root.after(0, _log)

    def _flush_logs_to_file(self, include_header=False):
        """把缓存的日志写入本地文件；文件过大时先轮转（改名），避免无限增长。"""
        log_file = Path.home() / ".minecraft_migrate_last_log.txt"
        # 超过上限则把旧日志改名，重新开始记录
        if log_file.exists() and log_file.stat().st_size > self._log_file_max_bytes:
            bak = log_file.with_suffix(".old.txt")
            if bak.exists():
                bak.unlink()
            log_file.rename(bak)
        mode = 'a' if log_file.exists() else 'w'
        with open(log_file, mode, encoding='utf-8') as f:
            if include_header:
                if mode == 'a':
                    f.write("\n" + "=" * 50 + "\n")
                    f.write(f"--- 新日志记录 ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
                f.write("".join(self._saved_logs))
            else:
                f.write("".join(self._saved_logs))
        self._saved_logs = []

    def clear_log(self):
        # 清空后内容不满一屏，顺手把"跟到底"恢复成跟随
        try:
            self._log_follow = True
        except Exception:
            pass
        if not hasattr(self, '_saved_logs') or not self._saved_logs:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")
            self.log("📋 日志已清空（无有效操作记录，不保存文件）", level="INFO", save=False)
            self.mod_text.edit_reset()
            self.mod_text.edit_modified(False)
            return

        try:
            self._flush_logs_to_file(include_header=True)
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")
            self.mod_text.edit_reset()
            self.mod_text.edit_modified(False)
            self.log(f"📋 日志已清空，有效操作记录已追加至 {Path.home() / '.minecraft_migrate_last_log.txt'}",
                     level="INFO", save=False)
        except Exception as e:
            self.log(f"❌ 日志保存失败：{e}", level="ERROR", save=False)

    def open_log_folder(self):
        log_file = Path.home() / ".minecraft_migrate_last_log.txt"
        folder = log_file.parent
        if not folder.exists():
            messagebox.showwarning("提示", "日志文件夹不存在，请先执行操作产生日志。")
            return

        try:
            if sys.platform == 'win32':
                if log_file.exists():
                    subprocess.Popen(['explorer', '/select,', str(log_file)])
                else:
                    os.startfile(str(folder))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder)])
            else:
                subprocess.Popen(['xdg-open', str(folder)])
            self.log(f"📂 已打开日志文件夹：{folder}", level="INFO")
        except Exception as e:
            self.log(f"❌ 打开文件夹失败：{e}", level="ERROR")
            messagebox.showerror("错误", f"无法打开文件夹：{e}")

    # 日志配色标签见类顶部 _LOG_COLOR_KEYS

    def open_log_big_view(self):
        """打开执行日志的放大查看窗口：只读、保留语义色，并实时跟随主日志更新。"""
        # 避免重复打开多个放大窗口
        existing = getattr(self, "_log_big_view", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
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
            colors=("#757575", "#9e9e9e"),
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
                    self._roll_counter(count_lbl, f"{content.count(chr(10))} 行")
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
        # ---- 模组清单 ----
        self._create_modlist_widgets()
        self._stage()
        # ---- Config 清单 ----
        self._create_config_widgets()
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
        # 底部声明
        self.bottom_frame = tk.Frame(self.root)
        self.bottom_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(self.bottom_frame, text="本工具完全免费，仅供个人学习交流使用。严禁倒卖或用于商业目的。",
                 font=("微软雅黑", 8)).pack()

        # 全部按钮建完，最后按配置摆一遍（显示/隐藏 + 自定义顺序）
        self._apply_button_layout()

    def _create_path_widgets(self):
        # 源目录
        frame_source = tk.LabelFrame(self.root, text="📤 旧版整合包（要迁移出去的源）", padx=5, pady=5)
        frame_source.pack(fill="x", padx=10, pady=5)
        self.source_entry = RoundedEntry(frame_source, self.theme,
                                         textvariable=self.source_path, chars=58)
        self.source_entry.pack(side="left", padx=5)
        btn_source_browse = create_gradient_button(
            frame_source, "📂 浏览...", self.select_source,
            colors=("#607d8b", "#90a4ae"),
            width=_grad_width("📂 浏览..."), height=30, font=("微软雅黑", 9, "bold"))
        btn_source_browse.pack(side="left", padx=5)
        self._btn_widgets["browse_source"] = btn_source_browse
        self._stage()
        btn_copy = create_gradient_button(
            frame_source, "← 使用新版路径填充", self.copy_target_to_source,
            colors=("#fb8c00", "#ffb74d"),
            width=_grad_width("← 使用新版路径填充"), height=30, font=("微软雅黑", 9, "bold"))
        btn_copy.pack(side="left", padx=5)
        self._btn_widgets["copy_target"] = btn_copy
        self.create_tooltip(btn_copy, "将右侧“新版”的路径复制到左侧“旧版”栏，用于快速测试或反向操作")
        self.source_status = tk.Label(frame_source, text="", fg=self.theme["muted_fg"])
        self.source_status._keep_fg = True      # 颜色由状态决定，别被主题统一刷掉
        self.source_status.pack(side="left", padx=10)
        self._stage()

        # 目标目录
        # 迁移方向箭头直接写进标题（⬇ 表示上面「旧版」的数据往下流到这里）。
        # 以前它自己占一整行，20pt 的箭头把那一行撑到 40 多像素；放进标题既不占
        # 高度，也不会把这一框的路径框挤得和上面那框不对齐。
        frame_target = tk.LabelFrame(self.root, text="⬇ 新版整合包（迁移目的地）",
                                     padx=5, pady=5)
        frame_target.pack(fill="x", padx=10, pady=5)
        self.target_entry = RoundedEntry(frame_target, self.theme,
                                         textvariable=self.target_path, chars=66)
        self.target_entry.pack(side="left", padx=5)
        btn_target_browse = create_gradient_button(
            frame_target, "📂 浏览...", self.select_target,
            colors=("#607d8b", "#90a4ae"),
            width=_grad_width("📂 浏览..."), height=30, font=("微软雅黑", 9, "bold"))
        btn_target_browse.pack(side="left", padx=5)
        self._btn_widgets["browse_target"] = btn_target_browse
        self.target_status = tk.Label(frame_target, text="", fg=self.theme["muted_fg"])
        self.target_status._keep_fg = True      # 同上
        self.target_status.pack(side="left", padx=10)

        # 存档名称
        frame_world = tk.LabelFrame(self.root, text="存档文件夹名称", padx=5, pady=5)
        frame_world.pack(fill="x", padx=10, pady=5)
        self.world_entry = RoundedEntry(frame_world, self.theme,
                                        textvariable=self.world_name, chars=30)
        self.world_entry.pack(side="left", padx=5)
        tk.Label(frame_world, text="（例如：新的世界）").pack(side="left")
        self.world_status = tk.Label(frame_world, text="", fg=self.theme["muted_fg"])
        self.world_status._keep_fg = True       # 同上
        self.world_status.pack(side="left", padx=10)

    def _create_modlist_widgets(self):
        frame_modlist = tk.LabelFrame(self.root, text="需要复制的模组清单（每行一个 .jar 文件名）",
                                      padx=5, pady=5)
        frame_modlist.pack(fill="both", expand=True, padx=10, pady=5)

        self.mod_text_box = RoundedTextArea(frame_modlist, self.theme, height=8,
                                            wrap=tk.NONE, undo=True,
                                            font=("微软雅黑 Light", 10))
        self.mod_text_box.pack(fill="both", expand=True, padx=5, pady=5)
        self.mod_text = self.mod_text_box.text
        self._smooth(self.mod_text)
        self.mod_text.bind("<Control-z>", lambda e: self._safe_undo(self.mod_text))
        self.mod_text.bind("<Control-y>", lambda e: self._safe_redo(self.mod_text))
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
        self._stage()               # ScrolledText 建一个要一百来毫秒，建完先让一帧

        # 橙色（edit_bg）只用在勾选框那一小块，整栏和后面的警告文字都用普通背景——
        # 这样既能突出"编辑模式"，又不会整条都在喊。
        self.edit_toolbar = tk.Frame(frame_modlist, bg=self.theme["bg"],
                                     relief=tk.RAISED, bd=2)
        self.edit_toolbar.pack(fill="x", padx=5, pady=2)
        self.edit_mode_cb = tk.Checkbutton(
            self.edit_toolbar, text="🔓 启用主界面编辑（直接修改清单）",
            variable=self.edit_mode, command=self.toggle_edit_mode,
            bg=self.theme["edit_bg"], fg=self.theme["fg"],
            activebackground=self.theme["edit_bg"],
            activeforeground=self.theme["fg"],
            selectcolor=self.theme["edit_bg"],
            highlightthickness=0, font=("微软雅黑", 10, "bold"))
        self.edit_mode_cb.pack(side="left", padx=5)
        self._stage()
        self.edit_warn_label = tk.Label(
            self.edit_toolbar, text="⚠️ 编辑模式可能造成数据损坏，请谨慎操作！",
            fg=self.theme["fail_fg"], bg=self.theme["bg"],
            font=("微软雅黑", 9))
        self.edit_warn_label.pack(side="left", padx=10)
        self._stage()

        btn_frame = tk.Frame(frame_modlist)
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
            colors=("#757575", "#9e9e9e"),
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

    def _create_config_widgets(self):
        frame_config = tk.LabelFrame(self.root,
                                     text="需要迁移的 config 内容（每行一个相对路径，相对于 config 目录）",
                                     padx=5, pady=5)
        frame_config.pack(fill="both", expand=True, padx=10, pady=5)

        warning_config = tk.Label(frame_config,
                                  text="⚠️ 注意：复制将直接覆盖目标 config 中的同名文件/文件夹，请谨慎操作！",
                                  fg=self.theme["fail_fg"], font=("微软雅黑", 9, "bold"))
        warning_config.pack(anchor="w", padx=5, pady=2)

        self.config_text_box = RoundedTextArea(frame_config, self.theme, height=6,
                                               wrap=tk.NONE, undo=True,
                                               font=("微软雅黑 Light", 10))
        self.config_text_box.pack(fill="both", expand=True, padx=5, pady=5)
        self.config_text = self.config_text_box.text
        self._smooth(self.config_text)
        self.config_text.bind("<Control-z>",
                              lambda e: self._safe_undo(self.config_text))
        self.config_text.bind("<Control-y>",
                              lambda e: self._safe_redo(self.config_text))
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

        btn_config_frame = tk.Frame(frame_config)
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
            colors=("#757575", "#9e9e9e"),
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
        self._stage()

    def _create_bottom_widgets(self):
        self.opt_frame = tk.Frame(self.root, bg=self.theme["bg"])
        self.opt_frame.pack(fill="x", padx=10, pady=5)
        # 勾选框要显式上色：默认的系统浅灰和深色主题摆一起非常违和
        cb_style = dict(bg=self.theme["bg"], fg=self.theme["fg"],
                        activebackground=self.theme["bg"],
                        activeforeground=self.theme["fg"],
                        selectcolor=self.theme.get("entry_bg", self.theme["bg"]),
                        highlightthickness=0, bd=0)
        self.dry_run_cb = tk.Checkbutton(self.opt_frame, text="模拟运行（仅显示操作）",
                                         variable=self.dry_run, command=self.save_config,
                                         **cb_style)
        self.dry_run_cb.pack(side="left")
        self.overwrite_cb = tk.Checkbutton(self.opt_frame, text="覆盖已存在的模组",
                                           variable=self.overwrite_mods,
                                           command=self.save_config, **cb_style)
        self.overwrite_cb.pack(side="left", padx=20)
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
        btn_clear_log = create_gradient_button(
            log_toolbar, "🗑️ 清空日志", self.clear_log,
            colors=("#757575", "#9e9e9e"),
            width=_grad_width("🗑️ 清空日志"), height=30, font=("微软雅黑", 9, "bold"))
        btn_clear_log.pack(side="right", padx=5)
        btn_open_log = create_gradient_button(
            log_toolbar, "📂 打开日志文件夹", self.open_log_folder,
            colors=("#607d8b", "#90a4ae"),
            width=_grad_width("📂 打开日志文件夹"), height=30, font=("微软雅黑", 9, "bold"))
        btn_open_log.pack(side="right", padx=5)
        self._btn_widgets.update({
            "log_big": btn_big_log,
            "log_open": btn_open_log,
            "log_clear": btn_clear_log,
        })
        self._stage()               # 下面这个日志文本框也要建一百来毫秒
        # 顶部提示区已移除，执行日志相应加高，占住释放出来的空间
        self.log_text_box = RoundedTextArea(frame_log, self.theme, height=22,
                                            wrap=tk.WORD, state="disabled",
                                            bg=self.theme["log_bg"],
                                            fg=self.theme["log_fg"])
        self.log_text_box.pack(fill="both", expand=True, padx=2, pady=2)
        self.log_text = self.log_text_box.text
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

    # ---------- 日志区平滑滚动联动 ----------
    def _smooth(self, widget, rows=False, bind_widgets=None, on_render=None, **kw):
        """给一个可滚动控件装上平滑滚动。

        rows=False：Text 类，像素级真平滑。
        rows=True ：Treeview / 自绘表格，只能整行走，由引擎攒零头做出动画。
        """
        try:
            if rows:
                sc = SmoothScroller.for_rows(
                    widget, kw.pop("row_px", None) or tree_row_px(),
                    on_render=on_render, bind_widgets=bind_widgets, **kw)
            else:
                sc = SmoothScroller.for_text(widget, bind_widgets=bind_widgets, **kw)
            self._scrollers.append(sc)
            return sc
        except Exception:
            return None

    def _at_log_bottom(self):
        try:
            return self.log_text.yview()[1] >= 0.999
        except Exception:
            return True

    def _on_log_user_scroll(self, going_up):
        if going_up:
            self._log_follow = False
        elif self._at_log_bottom():
            self._log_follow = True

    def _on_log_scroll_settle(self):
        if self._at_log_bottom():
            self._log_follow = True

    # ---------- 路径选择 ----------
    def select_source(self):
        path = filedialog.askdirectory(title="选择源整合包的实例根目录")
        if path:
            self.source_path.set(path)

    def select_target(self):
        path = filedialog.askdirectory(title="选择目标整合包的实例根目录")
        if path:
            self.target_path.set(path)

    def copy_target_to_source(self):
        tgt = self.target_path.get().strip()
        if tgt:
            self.source_path.set(tgt)
            self.log("已将目标路径复制到源路径", level="INFO")
        else:
            self.root.bell()
            self.log("⚠️ 目标路径为空，无法复制", level="WARNING")

    # ---------- 检查存档（实时） ----------
    def _update_world_status(self):
        """实时检测源存档是否存在，只更新状态标签（不弹窗/不打日志，避免输入时刷屏）。"""
        src = self.source_path.get().strip()
        world = self.world_name.get().strip()
        try:
            if not src or not world:
                self._set_status_semantic(self.world_status, "muted", "")
                return
            src_path = Path(src)
            save_dir = src_path / "saves" / world
            if save_dir.is_dir():
                self._set_status_semantic(self.world_status, "ok", "✅ 存档已存在")
            else:
                self._set_status_semantic(self.world_status, "fail", "❌ 存档不存在")
        except Exception:
            self._set_status_semantic(self.world_status, "muted", "")

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
            self.log(f"❌ 源 mods 目录不存在：{src_mods}", level="ERROR")
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

        self.log(f"📊 模组清单检查结果：总清单项数 {len(modlist)}", level="INFO")
        self.log(f"✅ 存在的模组：{len(found)}", level="SUCCESS")
        self.log(f"❌ 缺失的模组：{len(missing)}", level="ERROR" if missing else "INFO")
        if missing:
            self.root.bell()
            self.log("缺失列表：", level="WARNING")
            for m in missing[:50]:
                self.log(f"  - {m}", level="ERROR")
            if len(missing) > 50:
                self.log(f"  ... 还有 {len(missing) - 50} 个未显示", level="WARNING")

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
                    existing.lift()
                    existing.focus_force()
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
                    f"已提取到 {len(added)} 个 Added 模组，{len(updated)} 个 Updated 模组。\n"
                    "是否将 Updated 模组也添加到复制清单中？\n\n"
                    "点击“是” → 全部添加\n"
                    "点击“否” → 只添加 Added 模组\n"
                    "点击“取消” → 不添加任何模组"
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
                    f"从变更日志中提取了 {len(all_mods)} 个模组（Added: {len(added)}, Updated: {len(updated)}）",
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
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
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
            self.log(f"✅ 已添加 {added} 个 config 子文件夹条目", level="SUCCESS")
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
            self._roll_counter(count_lbl, f"已勾选 {len(checked)} 个文件夹")

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

        count_lbl = tk.Label(dlg, bg=theme["bg"], fg=theme["fg"], text="")
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
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
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
            self.log(f"✅ 已添加 {added} 个 config 文件条目", level="SUCCESS")
        elif not failed:
            self.log("ℹ️ 所选文件均已在 config 清单中，未重复添加", level="INFO")
        if failed:
            messagebox.showwarning(
                "添加提示",
                f"⚠️ 有 {len(failed)} 项未添加（不在源 config 目录下或不安全）：\n"
                + "\n".join(str(f) for f in failed[:5]), parent=self.root)

    # ---------- 历史记录 ----------
    def action_show_history(self):
        tgt = self.target_path.get().strip()
        if not tgt:
            messagebox.showwarning("提示", "请先选择目标实例根目录")
            return
        target_path = Path(tgt)
        history = load_history(target_path)
        if not history:
            messagebox.showinfo("提示", "当前目标实例没有迁移记录。")
            return

        existing = getattr(self, "_history_win", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        hist_win = tk.Toplevel(self.root)
        self._history_win = hist_win
        hist_win.withdraw()     # 构建完居中后再显示，避免"闪现-跳到中间"
        hist_win.title("迁移历史记录")
        hist_win.geometry("900x500")
        hist_win.transient(self.root)
        set_window_icon(hist_win)
        tk.Label(hist_win, text=f"目标实例：{target_path}", font=("微软雅黑", 9,
                                                                 "bold")).pack(pady=5)

        columns = ("时间", "来源", "模组数", "Config数", "状态")
        # 配置 Treeview 样式（使用当前主题）
        style = ttk.Style()
        if style.theme_use() != 'clam':
            try:
                style.theme_use('clam')
            except:
                pass
        style.configure(
            "History.Treeview",
            background=self.theme["ttk_bg"],
            foreground=self.theme["ttk_fg"],
            fieldbackground=self.theme["ttk_bg"],
            selectbackground=self.theme["ttk_select_bg"],
            selectforeground=self.theme["ttk_select_fg"]
        )
        style.configure(
            "History.Treeview.Heading",
            background=self.theme["button_bg"],
            foreground=self.theme["fg"]
        )
        style.map(
            "History.Treeview.Heading",
            background=[("active", lighten_color(self.theme["button_bg"]))]
        )

        tree = ttk.Treeview(
            hist_win,
            columns=columns,
            show="headings",
            height=18,
            style="History.Treeview"
        )
        self._smooth(tree, rows=True)
        tree.heading("时间", text="🕒 迁移时间")
        tree.heading("来源", text="📁 来源路径")
        tree.heading("模组数", text="🧩 模组数")
        tree.heading("Config数", text="⚙️ Config数")
        tree.heading("状态", text="✅ 状态")

        tree.column("时间", width=160)
        tree.column("来源", width=400)
        tree.column("模组数", width=70, anchor="center")
        tree.column("Config数", width=70, anchor="center")
        tree.column("状态", width=100, anchor="center")

        scrollbar = ttk.Scrollbar(hist_win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True, padx=10, pady=5)
        scrollbar.pack(side="right", fill="y")

        for entry in reversed(history):
            status_text = "✅ 已回滚" if entry.get("rolled_back", False) else "🟢 正常"
            tags = ("rolled_back",) if entry.get("rolled_back", False) else ("normal",)
            tree.insert("", "end", values=(
                entry.get("timestamp", "?"),
                entry.get("source", "?"),
                entry.get("mod_count", 0),
                entry.get("config_count", 0),
                status_text
            ), tags=tags)

        tree.tag_configure("rolled_back", background=self.theme["badge_rollback_bg"])
        tree.tag_configure("normal", background=self.theme["badge_normal_bg"])
        btn_close_hist = create_gradient_button(
            hist_win, "关闭", hist_win.destroy,
            colors=("#757575", "#9e9e9e"),
            width=_grad_width("关闭"), height=30, font=("微软雅黑", 9, "bold"))
        btn_close_hist.pack(pady=10)
        apply_theme_to_widget_tree(hist_win, self.theme)
        _center_window(hist_win, 900, 500)
        hist_win.deiconify()
        focus_window(hist_win)

    # ---------- 回滚 ----------
    @_file_task_lock("备份回滚")
    def action_rollback(self):
        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，暂不能回滚。")
            return
        self.log("=" * 50, level="INFO")
        self.log("🔄 用户请求执行回滚操作", level="INFO")

        tgt = self.target_path.get().strip()
        if not tgt:
            self.log("❌ 回滚失败：未选择目标实例根目录", level="ERROR")
            messagebox.showerror("错误", "请先选择目标实例根目录")
            return
        tgt_path = Path(tgt)
        if not tgt_path.exists():
            self.log(f"❌ 回滚失败：目标路径不存在 {tgt}", level="ERROR")
            messagebox.showerror("错误", f"目标路径不存在：{tgt}")
            return

        backup_root = get_backup_path(tgt_path)
        if not backup_root.exists():
            self.log(f"❌ 回滚失败：未找到备份目录 {backup_root}", level="ERROR")
            messagebox.showerror("回滚失败", "没有找到可用的备份，无法回滚。")
            return

        self.log(f"📁 找到备份目录：{backup_root}", level="INFO")

        if not messagebox.askyesno(
                "⚠️ 确认回滚",
                f"即将把目标实例恢复到迁移前的状态，此操作将覆盖当前所有内容！\n\n"
                f"目标路径：{tgt}\n"
                f"备份路径：{backup_root}\n\n"
                "mods / config / saves 以及根目录的 options.txt 都会还原；\n"
                "迁移前不存在的部分会被删掉。\n\n"
                "此操作不可撤销！\n确定要继续吗？"
        ):
            self.log("❌ 用户取消了回滚操作", level="WARNING")
            return

        self.log("✅ 用户确认回滚，开始执行...", level="SUCCESS")
        success = do_restore(tgt_path, log_func=self.log)
        if success:
            if mark_rollback(tgt_path):
                self.log("📝 已标记本次回滚到历史记录", level="INFO")
            else:
                self.log("ℹ️ 历史记录里没有可标记的迁移记录（可能已经回滚过了）", level="WARNING")
            messagebox.showinfo("回滚完成", "目标实例已恢复到迁移前的状态。")
        else:
            self.log("❌ 回滚操作失败，目标实例可能处于不完整状态，"
                     "备份仍然保留，可重新执行回滚", level="ERROR")
            messagebox.showerror("回滚失败",
                                 "回滚未能完整完成，详情见日志。\n"
                                 "备份目录仍然保留，可以再次尝试回滚。")
        self.log("=" * 50, level="INFO")

    # ---------- 进度轮询 ----------
    def _poll_progress(self):
        if self.progress_queue is not None and self.progress_window is not None:
            try:
                while True:
                    msg = self.progress_queue.get_nowait()
                    if msg is None:
                        self.progress_window.close()
                        self.progress_window = None
                        self.progress_queue = None
                        if self.after_id is not None:
                            self.root.after_cancel(self.after_id)
                            self.after_id = None
                        self._migration_running = False
                        self._unlock_main_window()      # 进度窗口关掉时同步解锁
                        self._notify_task_done(
                            "迁移", "任务已结束，点托盘图标打开主界面查看日志")
                        return
                    self.progress_window.update_progress(*msg)
            except queue.Empty:
                pass
            if self.progress_window and self.progress_window.cancelled:
                if self.progress_queue:
                    self.progress_queue.put(None)
            self.after_id = self.root.after(100, self._poll_progress)
        else:
            self.after_id = None

    # ---------- 扫描相关 ----------
    def action_scan_mod_diff(self):
        if hasattr(self,
                   'diff_window') and self.diff_window is not None and self.diff_window.winfo_exists():
            self.diff_window.lift()
            self.diff_window.focus_force()
            return

        if self._scanning:
            return

        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        if not src or not tgt:
            messagebox.showerror("错误", "请先选择源和目标路径")
            return

        if Path(src).resolve() == Path(tgt).resolve():
            messagebox.showinfo("提示", "源目录和目标目录相同，无需比较。")
            return

        self._scanning = True
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="⏳ 扫描中…")
        self._refresh_busy_state()
        self.log("🔍 开始扫描模组差异，请稍候...", level="INFO")
        self.root.update_idletasks()

        total = 0
        if src:
            src_mods = Path(src) / "mods"
            if src_mods.exists():
                total += sum(1 for _ in src_mods.glob("*.jar"))
        if tgt:
            tgt_mods = Path(tgt) / "mods"
            if tgt_mods.exists():
                total += sum(1 for _ in tgt_mods.glob("*.jar"))
        self._scan_total = total

        self.scan_progress_window = ScanProgressWindow(self.root, total, self.theme)

        progress_queue = queue.Queue()
        self._scan_progress_queue = progress_queue

        def scan_task():
            try:
                data = scan_mod_differences(src, tgt, progress_queue, self._scan_total)
            except Exception as e:
                data = None
                error_msg = str(e)
            else:
                error_msg = None
            self.root.after(0, lambda: self._finish_scan(data, error_msg))

        self._poll_scan_progress()
        threading.Thread(target=scan_task, daemon=True).start()

    def _poll_scan_progress(self):
        try:
            while True:
                msg = self._scan_progress_queue.get_nowait()
                if msg is None:
                    if hasattr(self, 'scan_progress_window'):
                        self.scan_progress_window.close()
                        delattr(self, 'scan_progress_window')
                    return
                current, filename = msg
                total = getattr(self, '_scan_total', 0)
                if total > 0:
                    self.scan_btn.itemconfig(self.scan_btn.text_id,
                                             text=f"⏳ 解析中 ({current}/{total})")
                else:
                    self.scan_btn.itemconfig(self.scan_btn.text_id, text="⏳ 解析中...")
                if hasattr(self, 'scan_progress_window'):
                    self.scan_progress_window.update_progress(current, filename)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_scan_progress)

    def _finish_scan(self, data, error_msg):
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="🔍 扫描模组差异")
        self._scanning = False
        self._refresh_busy_state()

        if hasattr(self, 'scan_progress_window'):
            self.scan_progress_window.close()
            delattr(self, 'scan_progress_window')

        if error_msg:
            self.log(f"❌ 扫描出错: {error_msg}", level="ERROR")
            if self._in_tray():
                self._notify_task_done("扫描模组差异", f"扫描出错：{error_msg}")
            else:
                messagebox.showerror("扫描错误", f"扫描过程中发生异常：{error_msg}")
            return

        if data is None:
            return

        if not data:
            self.log("📊 扫描完成：无差异", level="INFO")
            if self._in_tray():
                self._notify_task_done("扫描模组差异", "两个 mods 目录完全一致，没有差异")
            else:
                messagebox.showinfo("提示", "两个 mods 目录完全一致，没有任何差异。")
            return

        self.log(f"📊 扫描完成，发现 {len(data)} 项差异", level="SUCCESS")

        def apply_callback(selected_files):
            self.mod_text.configure(state=tk.NORMAL)
            self.mod_text.delete(1.0, tk.END)
            self.mod_text.insert(tk.END, "\n".join(selected_files))
            self.mod_text.edit_reset()
            self._update_text_states()
            self.log(f"✅ 从差异扫描中导入了 {len(selected_files)} 个模组", level="SUCCESS")
            self.save_config()
            self._notify_modlist_change()

        # 调用 show_diff_window 并保存窗口引用
        if self._in_tray():
            # 窗口挂在托盘里：别突然弹出这个窗口，先压着，等叫回主界面再开
            self._pending_diff = (data, apply_callback)
            self._notify_task_done("扫描模组差异",
                                   f"发现 {len(data)} 项差异，点托盘图标查看")
            return
        self.diff_window = show_diff_window(self.root, data, self.theme,
                                            self.current_theme, apply_callback)

    # ---------- 迁移 ----------
    def _busy_task_name(self):
        """当前正在跑的文件类任务名；没有就返回 None。

        迁移、扫描差异、检查存在性、导入变更日志、回滚、大窗口检测都算 —— 它们都会碰文件，
        同时跑就可能互相踩（比如迁移正复制文件时又去回滚）。
        """
        if self._migration_running:
            return "迁移"
        if self._scanning:
            return "扫描模组差异"
        return getattr(self, "_file_task", None)

    def _begin_file_task(self, name):
        """登记一个文件类任务：期间迁移按钮会变灰，start_migration 也会直接拒绝。"""
        self._file_task = name
        self._refresh_busy_state()

    def _end_file_task(self):
        self._file_task = None
        self._refresh_busy_state()

    # ---------- 锁屏遮罩：流动红边 + 内嵌执行日志 ----------
    _BORDER_W = 4          # 边框厚度
    _TILE = 240            # 渐变瓦片长度（定长 → 窗口缩放只要增减瓦片，不用重渲图）

    def _flow_tile(self, vertical=False):
        """一段“暗红→亮红→暗红”的渐变瓦片（做流动边框用）。

        用定长瓦片首尾相接 + 每帧整体平移，而不是逐帧改每个格子的颜色：
        前者每帧只有十几次 canvas.move，后者要上百次 itemconfigure。
        """
        cache = getattr(self, "_flow_tiles", None)
        if cache is None:
            cache = self._flow_tiles = {}
        key = bool(vertical)
        if key in cache:
            return cache[key]
        try:
            from PIL import Image as _I, ImageDraw as _D, ImageTk as _IT
            import math as _m
            W, H = self._BORDER_W, self._TILE
            im = _I.new("RGB", (W if vertical else H, H if vertical else W))
            d = _D.Draw(im)
            dark, bright = (0x8e, 0x00, 0x00), (0xff, 0x17, 0x44)
            for i in range(H):
                t = 0.5 - 0.5 * _m.cos(2 * _m.pi * i / H)      # 0→1→0 平滑
                c = tuple(int(dark[k] + (bright[k] - dark[k]) * t) for k in range(3))
                if vertical:
                    d.line([(0, i), (self._BORDER_W - 1, i)], fill=c)
                else:
                    d.line([(i, 0), (i, self._BORDER_W - 1)], fill=c)
            photo = _IT.PhotoImage(im)
        except Exception:
            photo = None
        cache[key] = photo
        return photo

    def _build_flow_border(self, cv, w, h):
        """围着窗口铺一圈流动红边（四边首尾相接，绕一圈同向流动）。"""
        cv.delete("border")
        horiz, vert = self._flow_tile(False), self._flow_tile(True)
        items = []
        bw, t = self._BORDER_W, self._TILE

        def lay(anchor_x, anchor_y, length, horizontal, forward):
            img = horiz if horizontal else vert
            if img is None:
                return
            n = int(length // t) + 2
            for i in range(n):
                x = anchor_x + (i * t if horizontal else 0)
                y = anchor_y + (0 if horizontal else i * t)
                iid = cv.create_image(x, y, anchor="nw", image=img, tags="border")
                items.append({"id": iid, "horizontal": horizontal, "forward": forward,
                              "anchor": anchor_x if horizontal else anchor_y,
                              "length": length, "span": n * t})

        # 上边往右、右边往下、下边往左、左边往上 = 顺时针绕一圈
        lay(0, 0, w, True, True)
        lay(0, h - bw, w, True, False)
        lay(w - bw, 0, h, False, True)
        lay(0, 0, h, False, False)
        self._flow_canvas = cv
        self._flow_items = items

    def _flow_step(self):
        """每帧把瓦片整体平移几像素，越界的绕回另一端 —— 看着就是红光在边框里流动。"""
        cv = getattr(self, "_flow_canvas", None)
        items = getattr(self, "_flow_items", None)
        if cv is None or not items:
            self._flow_job = None
            return
        step = 3
        try:
            for it in items:
                if it["horizontal"]:
                    cv.move(it["id"], step if it["forward"] else -step, 0)
                else:
                    cv.move(it["id"], 0, step if it["forward"] else -step)
                x, y = cv.coords(it["id"])
                pos = x if it["horizontal"] else y
                anchor, span, length = it["anchor"], it["span"], it["length"]
                if it["forward"]:
                    if pos > anchor + length - 1:                 # 从尾部绕回头部
                        cv.move(it["id"], -span if it["horizontal"] else 0,
                                0 if it["horizontal"] else -span)
                else:
                    if pos + self._TILE < anchor:                 # 从头部绕回尾部
                        cv.move(it["id"], span if it["horizontal"] else 0,
                                0 if it["horizontal"] else span)
        except Exception:
            pass
        self._flow_job = self.root.after(32, self._flow_step)

    def _lock_main_window(self, text="正在执行迁移任务"):
        """迁移期间给主窗口盖一层遮罩（锁屏）：流动红边 + 内嵌执行日志。

        盖不盖由设置里的 lock_mode 决定（all/real/off）；**不管盖不盖，操作按钮都会禁用**
        （那是 _refresh_busy_state 的职责）。内嵌日志是“第二个视图”：主日志照常记录，
        这里同步追加，锁屏期间不用去关窗口也能看进度。
        """
        if getattr(self, "_lock_overlay", None) is not None:
            return
        mode = getattr(self, "lock_mode", "all")
        if mode == "off" or (mode == "real" and self.dry_run.get()):
            self.log("🔓 按设置未锁定界面（操作按钮仍全部禁用）", level="INFO", save=False)
            return
        try:
            bg = self.theme.get("bg", "#f0f0f0")
            bw = self._BORDER_W
            ov = tk.Frame(self.root, bg=bg)
            ov.place(x=0, y=0, relwidth=1, relheight=1)
            cv = tk.Canvas(ov, bg="#8e0000", highlightthickness=0, bd=0)
            cv.place(x=0, y=0, relwidth=1, relheight=1)
            inner = tk.Frame(ov, bg=bg)
            inner.place(x=bw, y=bw, relwidth=1, relheight=1,
                        width=-2 * bw, height=-2 * bw)

            head = tk.Frame(inner, bg=bg)
            head.pack(fill="x", pady=(16, 4))
            tk.Label(head, text="🔒", font=("微软雅黑", 28), bg=bg,
                     fg="#ff1744").pack()
            tk.Label(head, text=text, font=("微软雅黑", 15, "bold"), bg=bg,
                     fg=self.theme.get("fg", "#000000")).pack(pady=(4, 2))
            tk.Label(head, text="主界面已锁定（操作按钮全部禁用）；下面是执行日志，"
                               "不用关窗口也能看进度",
                     font=("微软雅黑", 9), bg=bg,
                     fg=self.theme.get("muted_fg", "#808080")).pack()

            log_box = tk.Frame(inner, bg=bg)
            log_box.pack(fill="both", expand=True, padx=26, pady=(10, 18))
            lt = tk.Text(log_box, wrap="word", state="normal", relief="flat", bd=0,
                         padx=10, pady=8, font=("微软雅黑", 9),
                         bg=self.theme.get("log_bg", "#ffffff"),
                         fg=self.theme.get("log_fg", "#000000"),
                         highlightthickness=1,
                         highlightbackground=self.theme.get("border", "#c8c8c8"))
            sb = tk.Scrollbar(log_box, orient="vertical", command=lt.yview)
            lt.configure(yscrollcommand=sb.set)
            lt.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            try:
                self._configure_log_colors(lt)       # 级别配色和主日志保持一致
                self._smooth(lt)                     # 平滑滚动
                tail = self.log_text.get("1.0", tk.END).splitlines()[-200:]
                if tail:
                    lt.insert("1.0", "\n".join(tail) + "\n")
                lt.see(tk.END)
            except Exception:
                pass
            lt.configure(state="disabled")
            self._lock_log_text = lt

            ov.lift()
            self._lock_overlay = ov
            self._build_flow_border(cv, max(2, ov.winfo_width()), max(2, ov.winfo_height()))
            self._flow_job = self.root.after(32, self._flow_step)
            # 兜底：迁移线程结束时会在主线程里解锁，这里再盯一道 —— 万一那次跨线程
            # after 没排上（Tk 在“没进 mainloop”的驱动方式下会直接拒绝跨线程调用），
            # 遮罩也不该一直盖着。
            self._mig_watch_job = self.root.after(250, self._watch_migration_end)
            self._lock_cfg_bind = self.root.bind("<Configure>", self._on_lock_configure, add="+")
        except Exception:
            self._lock_overlay = None
            self._lock_log_text = None

    def _on_lock_configure(self, event=None):
        """窗口大小变了：重铺一圈瓦片（瓦片图定长，不用重渲）。"""
        ov = getattr(self, "_lock_overlay", None)
        cv = getattr(self, "_flow_canvas", None)
        if ov is None or cv is None:
            return
        try:
            self._build_flow_border(cv, max(2, ov.winfo_width()),
                                   max(2, ov.winfo_height()))
        except Exception:
            pass

    def _watch_migration_end(self):
        """盯迁移结束：结束了就解锁（幂等，和线程侧的解锁互为保险）。"""
        self._mig_watch_job = None
        if self._migration_running:
            self._mig_watch_job = self.root.after(250, self._watch_migration_end)
            return
        self._unlock_main_window()
        self._refresh_busy_state()

    def _unlock_main_window(self):
        for attr in ("_mig_watch_job", "_lock_pulse_job", "_flow_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._flow_canvas = None
        self._flow_items = []
        self._lock_log_text = None
        if getattr(self, "_lock_cfg_bind", None) is not None:
            try:
                self.root.unbind("<Configure>", self._lock_cfg_bind)
            except Exception:
                pass
            self._lock_cfg_bind = None
        ov = getattr(self, "_lock_overlay", None)
        self._lock_overlay = None
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass

    def _refresh_busy_state(self):
        """迁移/扫描进行中时禁用主界面所有操作/内容变更类按钮，防止连点或误操作。

        注意 `_starting` 也要算"忙"：准备阶段（统计文件那几秒）按钮必须一直是灰的，
        否则轮询收尾的 _watch_migration_end 会把它当成"没在跑"而重新点亮。
        """
        busy = bool(self._busy_task_name()) or bool(getattr(self, "_starting", False))
        state = "disabled" if busy else "normal"
        btns = (
            self.start_btn, self.rollback_btn, self.scan_btn,
            self.btn_changelog, self.mod_magnify_btn, self.add_mods_btn,
            self.clear_mods_btn, self.check_mods_btn, self.config_magnify_btn,
            self.add_config_dir_btn, self.add_config_file_btn, self.clear_config_btn,
            self.config_check_btn,
            # 路径区那三个：迁移期间改来源/目标路径同样是误操作，一起禁掉
            self._btn_widgets.get("browse_source"), self._btn_widgets.get("copy_target"),
            self._btn_widgets.get("browse_target"),
            # 右上角的设置/主题
            getattr(self, "settings_btn", None), getattr(self, "theme_btn", None),
            # 日志区那三个（遮罩不盖日志，这里靠禁用挡住）
            getattr(self, "log_magnify_btn", None),
            self._btn_widgets.get("log_open"), self._btn_widgets.get("log_clear"),
        )
        for btn in btns:
            if btn is None:
                continue
            try:
                btn.state(state)
            except Exception:
                pass

    def start_migration(self):
        """开始迁移（按钮入口）。

        两段式：先挡住重入、立刻把按钮变灰，再走原来的准备+执行逻辑。
        为什么必须这样：准备阶段（校验清单 / 统计文件大小 / 磁盘检查）可能要几秒，
        期间只要出现过弹窗（messagebox 会开嵌套事件循环），排队的那次点击就会被派发，
        而那时 _migration_running 还没置位 —— 于是一次点击变两次迁移。
        """
        if self._migration_running or getattr(self, "_starting", False):
            self.log("⚠️ 已有迁移在进行中（或正在准备），这次点击已忽略",
                     level="WARNING", save=False)
            return
        self._starting = True
        # 立刻禁用：既是视觉反馈，也让"点了没反应"变成"按钮本来就是灰的"
        try:
            self.start_btn.state("disabled")
        except Exception:
            pass
        try:
            self._start_migration_locked()
        finally:
            self._starting = False
            if not self._migration_running:
                # 提前返回（校验没过/磁盘不足/备份失败…）：把按钮恢复
                self._refresh_busy_state()

    def _start_migration_locked(self):
        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，请勿重复启动")
            return
        # 别的文件任务在跑时禁止迁移：模拟运行也禁（它同样会读整份清单/校验路径，
        # 而且用户容易把"模拟"当成安全的并行操作，实际它和真迁移共用同一套流程）。
        task = self._busy_task_name()
        if task:
            messagebox.showwarning(
                "提示",
                f"正在执行「{task}」，为避免两个任务同时改动同一批文件，"
                "请等它结束后再开始迁移（模拟运行同样需要等待）。")
            self.log(f"⚠️ 已拦截：{task} 进行中，暂不允许启动迁移", level="WARNING")
            return

        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        world = self.world_name.get().strip()
        if not src or not tgt:
            messagebox.showerror("错误", "请选择源和目标实例根目录")
            return
        if not world:
            messagebox.showerror("错误", "请输入存档名称")
            return

        src_path = Path(src)
        tgt_path = Path(tgt)
        if not src_path.exists():
            messagebox.showerror("错误", f"源路径不存在：{src}")
            return
        if not tgt_path.exists():
            messagebox.showerror("错误", f"目标路径不存在：{tgt}")
            return

        modlist_raw = self.mod_text.get(1.0, tk.END).splitlines()
        modlist = [line.strip() for line in modlist_raw if line.strip() and not line.strip().startswith("#")]

        configlist_raw = self.config_text.get(1.0, tk.END).splitlines()
        configlist = []
        for line in configlist_raw:
            line = line.strip()
            if line and not line.startswith("#"):
                if _is_safe_path(line):
                    configlist.append(line)
                else:
                    self.log(f"⚠️ 跳过不安全 config 路径: {line}", level="WARNING")
                    messagebox.showwarning("不安全路径",
                                           f"Config 清单中的 '{line}' 包含 '..'，已自动跳过。")

        if not modlist and not configlist:
            messagebox.showwarning("提示", "模组清单和 config 清单均为空，没有可迁移的内容。")
            return

        # 计算要复制的文件数与总大小
        total_files, total_size = self._calculate_migration_stats(
            src_path, tgt_path, world, modlist, configlist)
        self.log(f"📦 待迁移文件 {total_files} 个，总大小 {total_size / 1024 / 1024:.1f} MB",
                 level="INFO")

        if total_files == 0:
            messagebox.showinfo("提示", "没有找到需要复制的文件，请检查清单。")
            return

        # 磁盘空间检查（目标盘需容纳 迁移数据 + 目标备份，附带余量）
        ok, free, needed = self._check_disk_space(tgt_path, total_size)
        if free < 0:
            self.log("⚠️ 无法读取目标磁盘信息，已跳过空间检查", level="WARNING")
        elif not ok:
            messagebox.showerror(
                "磁盘空间不足",
                f"目标磁盘剩余空间 {free / 1024 / 1024:.1f} MB，"
                f"本次迁移约需 {needed / 1024 / 1024:.1f} MB（含备份余量）。\n"
                "空间不足，请清理目标磁盘后重试。")
            return

        # 置为"迁移中"，禁用相关按钮、并给主窗口上锁，防止重复触发/误操作
        self._migration_running = True
        self._refresh_busy_state()
        self._lock_main_window("正在执行迁移任务" if not self.dry_run.get()
                              else "正在执行迁移任务（模拟运行）")

        # 模拟模式
        if self.dry_run.get():
            self.log("========== 开始迁移（模拟） ==========", level="INFO")
            self.log(f"旧版目录（源）: {src}", level="INFO")
            self.log(f"新版目录（目标）: {tgt}", level="INFO")
            self.log(f"存档名称: {world}", level="INFO")
            self.log("模拟模式: 是", level="INFO")
            self.log("不会实际修改任何文件", level="INFO")
            thread = threading.Thread(
                target=self._run_migration_thread,
                args=(src_path, tgt_path, world, modlist, configlist, True,
                      self.overwrite_mods.get())
            )
            thread.daemon = True
            thread.start()
            return

        # 实际迁移：先备份
        try:
            do_backup(tgt_path, log_func=self.log)
        except Exception as e:
            self.log(f"❌ 备份失败：{e}", level="ERROR")
            messagebox.showerror("备份错误", f"备份目标实例失败：{e}\n迁移已取消。")
            self._migration_running = False
            self._unlock_main_window()
            self._refresh_busy_state()
            return

        if self.silent_background:
            # 后台静默执行：不弹进度窗口，也不建队列/轮询，全过程只写日志
            self.progress_queue = None
            self.progress_window = None
            self.log("🤫 后台静默执行已开启：不显示进度窗口，完成后用系统通知提醒",
                     level="INFO")
            self._silent_notify_on_finish = True
        else:
            self.progress_queue = queue.Queue()
            self.progress_window = ProgressWindow(self.root, total_files, total_size)
            if self.after_id is None:
                self._poll_progress()

        thread = threading.Thread(
            target=self._run_migration_thread,
            args=(src_path, tgt_path, world, modlist, configlist, False,
                  self.overwrite_mods.get())
        )
        thread.daemon = True
        thread.start()

    def _calculate_migration_stats(self, src_path, tgt_path, world, modlist,
                                   configlist):
        total_files = 0
        total_size = 0

        src_mods = src_path / "mods"
        if src_mods.exists():
            source_files = {f.name: f for f in src_mods.glob("*.jar")}
            for mod in modlist:
                matched = match_mod(mod, source_files, {})
                if matched:
                    total_files += 1
                    total_size += (src_mods / matched).stat().st_size

        src_opts = src_path / "options.txt"
        if src_opts.exists():
            total_files += 1
            total_size += src_opts.stat().st_size

        src_world = src_path / "saves" / world
        if src_world.exists():
            for f in src_world.rglob("*"):
                if f.is_file():
                    total_files += 1
                    total_size += f.stat().st_size

        src_config = src_path / "config"
        for entry in configlist:
            src_entry = src_config / entry
            if src_entry.is_file():
                total_files += 1
                total_size += src_entry.stat().st_size
            elif src_entry.is_dir():
                for f in src_entry.rglob("*"):
                    if f.is_file():
                        total_files += 1
                        total_size += f.stat().st_size

        return total_files, total_size

    def _dir_size(self, path):
        """递归计算目录下所有文件大小。"""
        total = 0
        try:
            for f in Path(path).rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def _check_disk_space(self, tgt_path, total_size):
        """检查目标磁盘空间是否足够（迁移数据 + 目标备份，附加余量）。
        返回 (ok, free_bytes, needed_bytes)；无法读取磁盘时返回 (True, -1, -1)。"""
        try:
            free = shutil.disk_usage(str(tgt_path)).free
        except Exception:
            return True, -1, -1
        # 备份需容纳目标实例当前大小，迁移需写入 total_size；加 1.2 倍余量 + 100MB 缓冲。
        target_size = self._dir_size(tgt_path)
        needed = int((total_size + target_size) * 1.2) + 100 * 1024 * 1024
        return free >= needed, free, needed

    def _run_migration_thread(self, src_path, tgt_path, world, modlist, configlist,
                              dry_run, overwrite):
        """迁移工作线程。

        overwrite 由主线程读好再传进来：Tk 变量只能在主线程碰，子线程直接
        self.overwrite_mods.get() 会抛 "main thread is not in main loop"。
        """
        def progress_callback(file_index, file_name, copied_bytes, step=None):
            if file_index is None:
                # 这是迁移结束的哨兵：以前这里直接 return 把它吞了，_poll_progress
                # 永远等不到 None —— 进度窗口（还是 grab_set 的）跑完也不关，
                # 界面就那样卡在"迁移进度"上不收。
                if self.progress_queue:
                    self.progress_queue.put(None)
                return
            if self.progress_queue:
                self.progress_queue.put((file_index, file_name, copied_bytes, step))

        def check_cancel():
            return self.progress_window and self.progress_window.cancelled

        try:
            run_migration(
                src_path=src_path,
                tgt_path=tgt_path,
                world_name=world,
                modlist=modlist,
                configlist=configlist,
                dry_run=dry_run,
                overwrite=overwrite,
                progress_callback=progress_callback,
                log_callback=self.log,
                check_cancel=check_cancel,
                add_history=True,
                # 迁移标记：只改目标文件名，加载器认的是 jar 里的 modid，不受影响
                rename_marker=(self.rename_marker
                               if getattr(self, "rename_migrated_mods", False) else None)
            )
        finally:
            self._migration_running = False
            try:
                self.root.after(0, self._unlock_main_window)     # 解掉主窗口遮罩
                self.root.after(0, self._refresh_busy_state)
            except Exception:
                pass
            # 静默模式下没有进度窗口来宣布结束，这里补一条系统通知
            if getattr(self, "_silent_notify_on_finish", False):
                self._silent_notify_on_finish = False
                try:
                    self.root.after(0, lambda: self._notify_task_done(
                        "迁移", "后台静默执行已结束，点托盘图标查看日志", force=True))
                except Exception:
                    pass

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
            else:
                try:
                    self._notify_config_change()
                except Exception:
                    pass

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
        self.log(f"✅ 已添加 {len(new)} 个模组", level="SUCCESS")
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
            messagebox.showinfo("添加成功", f"✅ 已添加 {added} 个模组。", parent=self.root)
        else:
            messagebox.showinfo("添加提示", "所选模组已在清单中，未新增。", parent=self.root)
        if non_jar:
            messagebox.showwarning("添加提示", f"⚠️ 有 {non_jar} 个非 .jar 文件被跳过。", parent=self.root)

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

    def _add_config_paths(self, paths):
        """把拖入的路径转成相对 config 的条目，加入 config 清单。返回 (新增数, 失败列表)。"""
        src = self.source_path.get().strip()
        if not src:
            return 0, ["尚未设置源实例根目录"]
        src_config = Path(src) / "config"
        if not src_config.exists():
            return 0, ["源 config 目录不存在"]
        lines = set(l.strip().rstrip("/") for l in self.config_text.get("1.0", tk.END).splitlines() if l.strip())
        added = 0
        failed = []
        for p in paths:
            tp = Path(p)
            try:
                rel = tp.relative_to(src_config) if tp.is_absolute() else Path(p)
            except ValueError:
                failed.append(str(tp))
                continue
            rel_s = str(rel)
            if not _is_safe_path(rel_s):
                failed.append(str(tp))
                continue
            # 文件夹条目在末尾加 "/"（与文件区分）；去重按去掉末尾 "/" 归一化
            if (src_config / rel_s).is_dir() and not rel_s.endswith("/"):
                rel_s = rel_s.rstrip("/") + "/"
            if rel_s.rstrip("/") not in lines:
                self.config_text.configure(state=tk.NORMAL)
                if self.config_text.get("1.0", tk.END).strip():
                    self.config_text.insert(tk.END, "\n")
                self.config_text.insert(tk.END, rel_s + "\n")
                lines.add(rel_s.rstrip("/"))
                added += 1
        self.save_config()
        self._update_text_states()
        if added:
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
            messagebox.showinfo("添加成功", f"✅ 已添加 {added} 个 config 条目。", parent=self.root)
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

    def _notify_modlist_change(self):
        """通知已打开的"放大查看"刷新模组清单。"""
        try:
            self.mod_text.event_generate("<<ModlistChanged>>")
        except Exception:
            pass

    def _notify_config_change(self):
        """通知已打开的"放大查看"刷新 config 清单。"""
        try:
            self.config_text.event_generate("<<ConfigChanged>>")
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
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
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

        self.log(f"📊 config 清单检查结果：总条目 {len(entries)}，去重后 {len(counts)} 个", level="INFO")
        self.log(f"✅ 存在的条目：{ok}", level="SUCCESS")
        if duplicate:
            self.log(f"⚠️ 重复条目：{duplicate} 行", level="WARNING")
        if missing:
            self.log(f"❌ 缺失的条目：{missing}", level="ERROR")
            self.root.bell()
            self.log("缺失条目：", level="WARNING")
            for m in missing_samples[:50]:
                self.log(f"  - {m}", level="ERROR")
            if missing > 50:
                self.log(f"  ... 还有 {missing - 50} 条未显示", level="WARNING")
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

    def open_big_view(self, source_text, title):
        """大窗口查看：可多选（勾选）+ 可排序的列表，并支持搜索、存在性检测、添加/删除、拖拽。"""
        is_mod = "模组" in title or source_text is getattr(self, 'mod_text', None)
        # 单实例：同标题的放大查看窗口已打开则聚焦，避免连点重复弹窗
        win_title = f"大窗口查看 - {title}"
        for w in getattr(self, '_big_view_windows', []):
            try:
                if w.winfo_exists() and w.title() == win_title:
                    w.lift()
                    w.focus_force()
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

        # 有界扫描线程池：避免为每条目单开线程导致几千并发的线程爆炸/磁盘抖动
        scan_queue = queue.Queue()
        _SCAN_WORKERS = 6

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
                for idx in order:
                    if idx not in meta:
                        scan_queue.put(idx)
            total = len(entries)
            shown = len(order)
            self._roll_counter(count_lbl,
                               f"显示 {shown}/{total} 项"
                               if search_var.get().strip() else f"共 {total} 项")
            update_summary()

        def write_back(fade_out_lines=None, fade_in_lines=None):
            """把 entries 写回主界面清单。

            fade_out_lines / fade_in_lines：要淡出（删除前的位置）或淡入（重写后的位置）
            的 1-based 行号。行数多（>20）就不做动画，直接重写 —— 批量操作时动画只会拖慢。
            """
            def _rewrite():
                content = "\n".join(entries)
                # 主清单是整体重写的：先记住滚动位置，写完再恢复，
                # 否则关闭放大查看后会发现主界面清单自己跳回了顶部。
                try:
                    first = source_text.yview()[0]
                except Exception:
                    first = 0.0
                source_text.configure(state=tk.NORMAL)
                source_text.edit_separator()
                source_text.delete("1.0", tk.END)
                source_text.insert("1.0", content + ("\n" if content else ""))
                source_text.edit_separator()
                try:
                    source_text.yview_moveto(first)
                except Exception:
                    pass
                self._update_text_states()
                self.save_config()
                # 新加的行淡入（重写后行号才对得上）
                if fade_in_lines and len(fade_in_lines) <= 20:
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

            if fade_out_lines and 0 < len(fade_out_lines) <= 20:
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
            messagebox.showinfo("添加成功", f"✅ 已添加 {len(new_entries)} 个模组。", parent=win)

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
            messagebox.showinfo("添加成功", f"✅ 已添加 {len(new_entries)} 个模组。", parent=win)

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
                messagebox.showinfo("提示", f"定位失败：{e}", parent=win)

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
            self.log(f"☑ 已{_MODE_TEXT.get(mode, mode)}：当前显示 {len(order)} 项，"
                     f"选中 {n} 项", level="INFO", save=False)

        def open_mod_detail(row: int):
            """打开指定行对应模组的详情窗口（含 Modrinth 联网搜索）。"""
            try:
                path = _path_of_row(row)
                if path and os.path.exists(path):
                    m = meta.get(order[row], {}) if 0 <= row < len(order) else {}
                    # 把扫描时拿到的分类一起带过去，详情窗口就不用再猜一遍
                    show_mod_detail(win, path, self.theme,
                                    tags_hint=m.get("tags"),
                                    tags_online=bool(m.get("tags_online")))
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
                update_summary()      # 扫描回来一批就刷新"存在/缺失"汇总
            # 扫描完成后恢复按钮（防止连点重复触发全量扫描）
            if big_scanning["flag"]:
                try:
                    if scan_queue.unfinished_tasks == 0:
                        big_scanning["flag"] = False
                        _set_busy_btns(False)
                        self._end_file_task()      # 扫描结束：解除"禁止迁移"
                        if _pending_detect["flag"]:
                            # 「检测存在性」的提示放到这里：此时扫描已全部结束，
                            # 统计只读内存，不会像以前那样在点击时卡住界面。
                            _pending_detect["flag"] = False
                            missing = sum(1 for m in meta.values()
                                          if m.get("status") == "❌ 缺失")
                            messagebox.showinfo(
                                "检测完成",
                                f"✅ 存在性检测完成：共 {len(entries)} 项，缺失 {missing} 项。",
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
            """大窗口被关掉时，别把"禁止迁移"的标记留成永久状态。"""
            try:
                if event.widget is win and getattr(self, "_file_task", None):
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
        _BTN_W = max(_grad_width("🔍 检测存在性"),
                     _grad_width("🗑️ 删除选中"),
                     _grad_width("➕ 添加模组"))
        count_lbl = tk.Label(row_search, text=f"共 {len(entries)} 项", bg=self.theme["bg"],
                             fg=self.theme["fg"])
        count_lbl.pack(side="left", padx=(0, _PAD * 2))
        # 选中/存在性汇总：这两个数字是"我现在到底选了多少、有多少缺失"，
        # 表格和卡片视图共用（改勾选的地方都会调 update_summary）。
        sel_lbl = tk.Label(row_search, text="未选择", bg=self.theme["bg"],
                           fg=self.theme.get("ok_fg", "#2e7d32"),
                           font=("微软雅黑", 9, "bold"))
        sel_lbl._keep_fg = True
        sel_lbl.pack(side="left", padx=(0, _PAD * 3))
        stat_lbl = tk.Label(row_search, text="", bg=self.theme["bg"],
                            fg=self.theme.get("muted_fg", "#808080"),
                            font=("微软雅黑", 8))
        stat_lbl._keep_fg = True
        stat_lbl.pack(side="left", padx=(0, _PAD * 3))
        win._sel_lbl = sel_lbl
        win._stat_lbl = stat_lbl

        def update_summary():
            """刷新"已选 N / 总数"和"存在/缺失"两个汇总（数字带滚动动画）。"""
            try:
                total = len(entries)
                picked = sum(1 for e in entries if checked.get(key_of(e)))
                self._roll_counter(sel_lbl,
                                   f"已选 {picked} / {total} 项" if picked else "未选择")
                exist = miss = 0
                for i in range(total):
                    st = (meta.get(i) or {}).get("status")
                    if st == "✅ 存在":
                        exist += 1
                    elif st == "❌ 缺失":
                        miss += 1
                self._roll_counter(stat_lbl,
                                   f"存在 {exist} · 缺失 {miss}" if (exist or miss) else "")
            except Exception:
                pass
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
                "status": str(m.get("status") or "…"),
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
                self.log(f"🗑 已从清单移除：{name}", level="WARNING", save=False)

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
                self.log(f"⚠️ 卡片视图不可用：{exc}", level="WARNING", save=False)

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
            colors=("#757575", "#9e9e9e"),
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

    def _update_text_states(self):
        state = tk.NORMAL if self.edit_mode.get() else tk.DISABLED
        self.mod_text.configure(state=state)
        self.config_text.configure(state=state)
        self._check_overflow()

    def toggle_edit_mode(self):
        self._update_text_states()
        if self.edit_mode.get():
            self.log("⚠️ 警告：已启用主界面编辑模式，直接修改清单可能导致数据错误，请谨慎操作！", level="WARNING")
        else:
            self.log("ℹ️ 主界面编辑模式已关闭，清单恢复只读。", level="INFO")
        self.save_config()



# ui/mw_common.py
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
from ui.rounded_tabs import RoundedTabs
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
                           lighten_color, begin_bulk_scan, end_bulk_scan, LiquidProgress,
                           bind_text_scroll, SwitchRow, DataText, data_label,
                           make_theme_icon, clear_layered_style, SmoothScroller,
                           tree_row_px, style_window, is_dark_theme, trace_exc,
                           text_delta, SegmentedControl, OptionCards)
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
                          split_cn_name, get_mod_icon, guess_tags,
                          detect_instance_env)
from ui.dialogs import (ProgressWindow, ScanProgressWindow, show_mod_detail,
                        update_mod_detail_theme, ask_migrate_confirm)
from ui.diff_window import show_diff_window
from ui.virtual_table import VirtualTable


_GRAD_FONT = None
_GRAD_PAD = 26

# 扫描线程 → 主线程的"干完了"哨兵。进度的消息是 (序号:int, 文件名:str)，
# 所以拿字符串当哨兵不会撞（见 _poll_scan_progress）。
_SCAN_DONE = "§扫描完成§"

# 后台线程 → 主线程 UI 调用队列的泵间隔（ms）。只做"取空队列"这件事，
# 空转开销可以忽略；50ms 的延迟人眼看不出来。
_UI_PUMP_MS = 50

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
    ("extra",       "其它文件区",        "left"),
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
    # 「其它文件」清单：相对整合包根目录（不是相对 config），
    # 用来带走路径不在 mods/config/saves 里的东西：shaderpacks / resourcepacks /
    # options.txt / servers.dat / kubejs / scripts 之类
    "extra":       ["add_extra_dir", "add_extra_file", "clear_extra", "check_extra"],
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
    "add_extra_dir": "📁 浏览添加文件夹（其它文件）",
    "add_extra_file": "📄 浏览添加文件（其它文件）",
    "clear_extra": "🗑️ 清空其它文件清单",
    "check_extra": "🔎 检查其它文件是否存在",
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
    "extras":  ("默认携带的目录", "#3949ab", "📦"),
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

# 「默认携带的目录」候选：(配置键, 条目（相对整合包根目录）, 中文说明)
# 勾上之后每次迁移都自动带上 —— 只并进这一次的迁移清单，**不改用户的「其它文件」清单**，
# 免得上一次删掉的条目下一次又被塞回来。源实例里不存在的会被跳过（不报错）。
_EXTRA_PRESETS = (
    ("shaderpacks",    "shaderpacks/",    "光影包（Iris / OptiFine）"),
    ("resourcepacks",  "resourcepacks/",  "资源包（材质包）"),
    ("schematics",     "schematics/",     "投影 / 蓝图（Litematica、WorldEdit）"),
    ("XaeroWorldMap",  "XaeroWorldMap/",  "Xaero 世界地图数据"),
    ("XaeroWaypoints", "XaeroWaypoints/", "Xaero 路径点"),
    ("xaero",          "xaero/",          "Xaero 小地图（旧版目录）"),
    ("journeymap",     "journeymap/",     "JourneyMap 地图数据"),
    ("kubejs",         "kubejs/",         "KubeJS 脚本"),
    ("defaultconfigs", "defaultconfigs/", "整合包默认配置（Forge / NeoForge）"),
    ("local",          "local/",          "本地数据（部分模组自建）"),
    ("screenshots",    "screenshots/",    "截图"),
    ("servers.dat",    "servers.dat",     "服务器列表"),
    ("optionsof.txt",  "optionsof.txt",   "OptiFine 视频设置"),
    ("iris.properties", "iris.properties", "Iris 光影设置"),
)

# 迁移这种会改文件的活儿，不该有"偷偷多带几样"的默认值：**清单是唯一依据**，
# 要带什么由用户自己加（清单页的「＋ 常用目录」提供现成条目）。
# （这里原先还有 _EXTRA_DEFAULT_KEYS：老配置没这个键时默认勾上的目录 —— 已删）

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

# 「模组差异」窗口打开时用哪个视图（同样只是定"刚打开时是哪个"）
_DIFF_VIEWS = (
    ("table", "📋 列表视图（一行一条差异，信息密度高）"),
    ("cards", "🗂 卡片视图（带图标的大卡片，看得清描述）"),
)

# 按钮列表里每一排的主色 + 图标，用来给分组行上色
_GROUP_COLORS = {
    "path":        "#42a5f5",
    "path_target": "#26c6da",
    "mods":        "#66bb6a",
    "config":      "#ffa726",
    "extra":       "#26a69a",
    "action":      "#ef5350",
    "log_left":    "#ab47bc",
    "log_right":   "#8d6e63",
}
_GROUP_ICONS = {
    "path":        "📤",
    "path_target": "📥",
    "mods":        "🧩",
    "config":      "⚙️",
    "extra":       "📦",
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


# 这些名字会被 `from ui.mw_common import *` 原样搬进 ui/main_window.py，
# 保持拆分前 `MW.<名字>` 的老接口面（_dctest 的验证脚本靠它打桩）。
__all__ = [
    "CONFIG_FILE", "Counter", "DARK_THEME", "DataText", "LIGHT_THEME", "LINK_GITHUB",
    "LINK_MINECRAFT", "LiquidProgress", "OptionCards", "Path", "ProgressWindow",
    "RoundedEntry", "RoundedTabs", "RoundedTextArea", "ScanProgressWindow",
    "SegmentedControl", "SmoothScroller", "SwitchRow", "VirtualTable", "_BIG_VIEW_BACKENDS",
    "_BIG_VIEW_VIEWS", "_BUTTON_GROUPS", "_BUTTON_LABELS", "_DEFAULT_BUTTON_ORDER",
    "_DIFF_VIEWS", "_EXTRA_PRESETS", "_GRAD_ARROW_RE", "_GRAD_ARROW_W", "_GRAD_FONT",
    "_GRAD_PAD", "_GRAD_SYM_RE", "_GRAD_SYM_W", "_GROUP_COLORS", "_GROUP_ICONS", "_ICON_BTN",
    "_ICON_SIZE", "_LOCK_MODES", "_RENAME_MARKERS", "_SCAN_DONE", "_SECTION_STYLE",
    "_UI_PUMP_MS", "_center_window", "_close_popup", "_dc_now", "_destroy_after_callback",
    "_file_task_lock", "_grad_width", "_is_safe_path", "_mix", "apply_theme_to_widget_tree",
    "ask_migrate_confirm", "begin_bulk_scan", "bind_text_scroll", "button_prefs",
    "center_window", "circular_reveal", "clear_layered_style", "create_gradient_button",
    "data_label", "detect_instance_env", "do_backup", "do_restore", "end_bulk_scan",
    "filedialog", "focus_window", "functools", "get_backup_path", "get_full_mod_metadata",
    "get_mod_icon", "guess_tags", "is_dark_theme", "json", "lighten_color", "load_history",
    "make_theme_icon", "mark_rollback", "match_mod", "messagebox", "os", "queue", "re",
    "run_migration", "scan_mod_differences", "set_window_icon", "show_diff_window",
    "show_mod_detail", "shutil", "split_cn_name", "style_window", "subprocess", "sys",
    "text_delta", "threading", "time", "tk", "tkfont", "trace_exc", "tree_row_px", "ttk",
    "update_mod_detail_theme",
]

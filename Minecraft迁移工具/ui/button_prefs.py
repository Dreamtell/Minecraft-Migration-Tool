# ui/button_prefs.py
"""窗口工具栏按钮的「显示 / 隐藏 + 顺序」偏好。

主界面那几排按钮由 `MigrationGUI._apply_button_layout` 直接摆（改完**实时**带动画），
而放大查看、日志放大查看这类窗口是**每次打开现建**的，所以这里的偏好是给它们建工具栏时
查的：`keys(gkey)` 返回"这一组该显示哪些按钮、什么顺序"（已过滤掉隐藏的）。
因此对这类窗口，设置改完是**下次打开那个窗口生效**。

主界面右上角那两个（设置/主题）也登记在这里 —— 它们虽然在主界面上、走主界面那套 place
动画（实时生效），但归到同一张表里，设置窗列在一起更好找。

只登记"功能按钮"：确定/取消/关闭这种对话框自带的按钮不参与隐藏，免得把自己关在门外。
"""
# (gkey, 分组标题, 图标, 主色, side, [(key, 标签), ...])
#   side 只对主界面那一排有意义（topbar 靠右）；窗口自己摆的时候不用
GROUPS = (
    ("topbar",  "主界面右上角",   "🧷", "#5c6bc0", "right", (
        ("settings_btn", "⚙ 设置"),
        ("theme_btn",    "🎨 主题"),
    )),
    ("bigview", "放大查看窗口",   "🔎", "#26a69a", "left", (
        ("bv_detect", "🔍 检测存在性"),
        ("bv_remove", "🗑️ 移出清单 / 删除选中"),
        ("bv_add",    "➕ 添加模组"),
        # 多选那组：Tk 版是一个「☑ 多选」下拉，Qt 版是三个按钮，同一组 key 各取所需
        ("bv_select", "☑ 多选（Tk）"),
        ("bv_all",    "☑ 全选（Qt）"),
        ("bv_invert", "⇄ 反选（Qt）"),
        ("bv_none",   "⬜ 清空勾选（Qt）"),
        ("bv_online", "🌐 联网搜索（Qt）"),
        ("bv_view",   "🗂 卡片 / 表格视图"),
        ("bv_sort",   "⇅ 排序（Tk）"),
        ("bv_close",  "✖ 关闭（Tk）"),
    )),
    ("logview", "日志放大查看",   "📜", "#8d6e63", "left", (
        ("lv_refresh", "🔄 刷新"),
        ("lv_close",   "❌ 关闭"),
    )),
)

DEFAULTS = {g[0]: [k for k, _t in g[5]] for g in GROUPS}
LABELS = {k: t for g in GROUPS for k, t in g[5]}
_GROUP_TITLES = {g[0]: g[1] for g in GROUPS}
_GROUP_ICONS = {g[0]: g[2] for g in GROUPS}
_GROUP_COLORS = {g[0]: g[3] for g in GROUPS}
_GROUP_SIDES = {g[0]: g[4] for g in GROUPS}

_state = {"hidden": set(), "order": {}}


def update(hidden=None, order=None):
    """主界面把配置里的偏好同步过来（启动时 + 每次改动后）。"""
    if hidden is not None:
        _state["hidden"] = set(hidden)
    if order is not None:
        _state["order"] = {k: list(v) for k, v in dict(order).items()}


def hidden(key):
    return key in _state["hidden"]


def ordered(gkey):
    """这一组的最终顺序：配置里的顺序 + 补上配置里还没有的新按钮。"""
    default = DEFAULTS[gkey]
    if gkey not in _state["order"]:
        return list(default)
    keys = [k for k in _state["order"][gkey] if k in default]
    keys += [k for k in default if k not in keys]
    return keys


def keys(gkey):
    """这一组"该显示"的按钮 key，按顺序返回（建工具栏时用它）。"""
    return [k for k in ordered(gkey) if k not in _state["hidden"]]


def label(key):
    return LABELS.get(key, key)


def group_of(key):
    for gkey, default in DEFAULTS.items():
        if key in default:
            return gkey
    return None


def groups():
    """给设置窗列列表用：(gkey, 标题, side, 图标, 主色, [(key, 标签), ...])。"""
    return [(g[0], g[1], g[4], g[2], g[3], g[5]) for g in GROUPS]

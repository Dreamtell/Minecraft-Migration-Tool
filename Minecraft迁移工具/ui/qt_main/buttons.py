# ui/qt_main/buttons.py
"""Qt 主界面的「按钮显示/隐藏 + 顺序」。

和 Tk 版共用同一组配置键:
  buttons_hidden : [key, ...]          隐藏的按钮
  button_order   : {组名: [key, ...]}  每组从左到右的顺序
按钮 key 和组名也沿用 Tk 版(ui/mw_common._DEFAULT_BUTTON_ORDER / _BUTTON_LABELS),
所以在一边藏掉的按钮,切到另一边也是藏着的。Tk 版有、Qt 版没有的按钮(检查存在性、
回滚、日志放大查看…)在这里不出现,但它们的配置值原样保留,不会被抹掉。

⚠ 不能 import ui.mw_common:那个模块顶层 import tkinter,Qt 进程不能碰 Tk。
"""
import sys
from pathlib import Path

_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtWidgets, QtCore  # noqa: E402

from ui.qt_win import fit_window
from utils.i18n import tr  # noqa: E402

# Qt 主界面里实际有的按钮。组名/key 与 Tk 版一致;顺序 = Qt 版默认顺序
GROUPS = (
    ("mods",   tr("模组清单区")),
    ("config", tr("Config 清单区")),
    ("extra",  tr("其它文件区")),
    ("action", tr("底部动作按钮")),
    ("log",    tr("执行日志区")),
)
DEFAULT_ORDER = {
    "mods":   ["add_mods", "changelog", "scan_diff", "mod_magnify", "clear_mods", "check_mods"],
    "config": ["add_cfg_dir", "add_cfg_file", "cfg_magnify", "clear_cfg", "check_cfg"],
    "extra":  ["add_extra_options", "add_extra_preset", "add_extra_dir", "add_extra_file",
               "clear_extra", "check_extra"],
    "action": ["history", "start"],
    "log":    ["log_big", "log_open", "fail_locate", "log_clear"],
}
LABELS = {
    "add_mods": tr("➕ 添加模组"),
    "changelog": tr("📥 从变更日志导入"),
    "scan_diff": tr("🔍 扫描差异"),
    "mod_magnify": tr("📂 放大查看(模组清单)"),
    "clear_mods": tr("🗑️ 清空(模组)"),
    "add_cfg_dir": tr("📁 添加文件夹(config)"),
    "add_cfg_file": tr("📄 添加文件(config)"),
    "cfg_magnify": tr("📂 放大查看(config 清单)"),
    "clear_cfg": tr("🗑️ 清空(config)"),
    "add_extra_options": tr("＋ options.txt"),
    "add_extra_preset": tr("＋ 常用目录 ▾"),
    "add_extra_dir": tr("📁 添加文件夹(其它文件)"),
    "add_extra_file": tr("📄 添加文件(其它文件)"),
    "clear_extra": tr("🗑️ 清空(其它文件)"),
    "check_mods": tr("🔎 检查模组是否存在"),
    "check_cfg": tr("🔎 检查 config 是否存在"),
    "check_extra": tr("🔎 检查其它文件是否存在"),
    "history": tr("📜 查看历史"),
    "start": tr("🚀 开始迁移"),
    "log_big": tr("📂 放大查看（日志）"),
    "log_open": tr("📂 打开日志文件夹"),
    "fail_locate": tr("📍 定位错误"),
    "log_clear": tr("🧹 清空日志"),
}
# 隐藏了会让主流程走不下去的按钮:不许隐藏(Tk 版同样不让藏「开始迁移」)
LOCKED = {"start"}


def merge_into_config(cfg_hidden, cfg_order, qt_hidden, qt_order):
    """把 Qt 设置窗的结果并回配置,**不碰 Qt 版不认识的 key**(Tk 版的回滚/检查存在性…)。

    返回 (新 buttons_hidden 列表, 新 button_order 字典)。
      · hidden:Qt 管得到的 key 以这次勾选为准;其余 key 原样保留
      · order :同一组里 Qt 认识的 key 按新顺序重排,Tk 独有的 key 留在原来的相对位置
               (Qt 的 "log" 组是自己的,和 Tk 的 log_left/log_right 不冲突)
    """
    qt_keys = set(LABELS)
    hidden = [k for k in (cfg_hidden or []) if k not in qt_keys] + sorted(qt_hidden)
    order = {g: list(v) for g, v in (cfg_order or {}).items()}
    for gid, keys in (qt_order or {}).items():
        old = order.get(gid) or []
        if not old:
            order[gid] = list(keys)
            continue
        # 旧序列里 Qt 认识的那些"槽位",按新顺序依次填回;Tk 独有的 key 不动
        it = iter([k for k in keys])
        new_seq = [next(it) if k in qt_keys and k in keys else k for k in old]
        for k in it:                       # 旧序列里没有的(Qt 新加的按钮)补在后面
            new_seq.append(k)
        order[gid] = new_seq
    return hidden, order


def order_for(group, saved_order):
    """按配置排一组 key:配置里有的先按配置顺序,配置里没有的(新按钮)按默认顺序补在后面。"""
    default = DEFAULT_ORDER.get(group, [])
    saved = [k for k in (saved_order or {}).get(group, []) if k in default]
    return saved + [k for k in default if k not in saved]


class ButtonPrefsDialog(QtWidgets.QDialog):
    """勾选 = 显示;选中一项后用 ↑ ↓ 调整组内顺序。"""

    def __init__(self, parent, hidden, order):
        super().__init__(parent)
        self.setWindowTitle(tr("界面按钮"))
        fit_window(self, 400, 440, 460, 520)
        self._hidden = set(hidden or [])
        self._order = dict(order or {})

        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel(tr("勾选 = 显示。选中一项后用右边的按钮调整顺序。")))
        row = QtWidgets.QHBoxLayout()
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        row.addWidget(self.tree, 1)
        side = QtWidgets.QVBoxLayout()
        up = QtWidgets.QPushButton(tr("↑ 上移"))
        down = QtWidgets.QPushButton(tr("↓ 下移"))
        reset = QtWidgets.QPushButton(tr("恢复默认"))
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        reset.clicked.connect(self._reset)
        for b in (up, down):
            side.addWidget(b)
        side.addStretch()
        side.addWidget(reset)
        row.addLayout(side)
        v.addLayout(row, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self._fill()

    def _fill(self):
        self.tree.clear()
        for gid, gname in GROUPS:
            top = QtWidgets.QTreeWidgetItem([gname])
            top.setFlags(QtCore.Qt.ItemIsEnabled)
            top.setData(0, QtCore.Qt.UserRole, ("group", gid))
            self.tree.addTopLevelItem(top)
            for key in order_for(gid, self._order):
                it = QtWidgets.QTreeWidgetItem([LABELS.get(key, key)])
                it.setData(0, QtCore.Qt.UserRole, ("btn", key))
                flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                if key not in LOCKED:
                    flags |= QtCore.Qt.ItemIsUserCheckable
                it.setFlags(flags)
                it.setCheckState(0, QtCore.Qt.Unchecked if key in self._hidden
                                 else QtCore.Qt.Checked)
                top.addChild(it)
            top.setExpanded(True)

    def _move(self, delta):
        it = self.tree.currentItem()
        if it is None or it.parent() is None:
            return
        top = it.parent()
        i = top.indexOfChild(it)
        j = i + delta
        if not (0 <= j < top.childCount()):
            return
        top.takeChild(i)
        top.insertChild(j, it)
        self.tree.setCurrentItem(it)

    def _reset(self):
        self._hidden = set()
        self._order = {}
        self._fill()

    def values(self):
        """返回 (hidden_set_仅Qt管的key, order_dict_仅Qt管的组)。合并进配置由调用方做。"""
        hidden, order = set(), {}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            gid = top.data(0, QtCore.Qt.UserRole)[1]
            keys = []
            for c in range(top.childCount()):
                it = top.child(c)
                key = it.data(0, QtCore.Qt.UserRole)[1]
                keys.append(key)
                if key not in LOCKED and it.checkState(0) != QtCore.Qt.Checked:
                    hidden.add(key)
            order[gid] = keys
        return hidden, order

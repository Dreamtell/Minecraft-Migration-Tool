# ui/qt_main/folder_picker.py
"""可多选的文件夹选择框(Qt 版)。

Tk 版「浏览添加文件夹」先试 Windows 原生多选对话框(comtypes),不行再退到自绘的树形多选。
Qt 自带的 getExistingDirectory 只能选一个,所以这里直接做树形多选:点文件夹名勾选/取消,
点 ▸ 展开子文件夹,「全选顶层」「全不选」「确定添加」,和 Tk 的回退对话框是同一套操作。

返回的是**相对 base 的路径**(正斜杠),已去掉被祖先覆盖的子项:勾了 `a` 又勾了 `a/b`,
迁移 `a` 时 `a/b` 本来就会带上,不用重复加。
"""
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from ui.qt_win import fit_window
from ui.qt_main.smooth import SmoothTree
from utils.i18n import tr, trp

_REL_ROLE = QtCore.Qt.UserRole
_PLACEHOLDER = "::placeholder"


def child_dirs(parent):
    """parent 下的一级子文件夹,按名字不分大小写排序;读不了就当没有。"""
    try:
        return sorted((e for e in Path(parent).iterdir() if e.is_dir()),
                      key=lambda p: p.name.lower())
    except OSError:
        return []


def drop_covered(rels):
    """去掉被已选祖先覆盖的子项:选了 a 就不再留 a/b。保持字母序,结果稳定。"""
    picked = sorted({str(r).replace("\\", "/").strip("/") for r in rels if str(r).strip("/\\")})
    out = []
    for r in picked:
        if any(r.startswith(q + "/") for q in picked if q != r):
            continue
        out.append(r)
    return out


class FolderPickerDialog(QtWidgets.QDialog):
    """base 下的文件夹树。树是惰性展开的:点开某个文件夹时才读它的子目录。"""

    def __init__(self, parent, base, title=None):
        super().__init__(parent)
        self._base = Path(base)
        self._checked = set()
        self.setWindowTitle(title or tr("选择 config 下的文件夹（可多选）"))
        fit_window(self, 780, 640, 820, 700)

        root = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(tr("点击文件夹名即可勾选/取消；点击 ▸ 展开子文件夹；可同时勾选多个。"))
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.tree = SmoothTree()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([tr("📁 文件夹"), tr("📄 完整相对路径")])
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 320)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(self._on_expanded)
        root.addWidget(self.tree, 1)

        self.count_label = QtWidgets.QLabel()
        root.addWidget(self.count_label)

        row = QtWidgets.QHBoxLayout()
        self.top_btn = QtWidgets.QPushButton(tr("全选顶层"))
        self.none_btn = QtWidgets.QPushButton(tr("全不选"))
        self.cancel_btn = QtWidgets.QPushButton(tr("取消"))
        self.ok_btn = QtWidgets.QPushButton(tr("确定添加"))
        self.ok_btn.setDefault(True)
        self.top_btn.clicked.connect(self.select_top)
        self.none_btn.clicked.connect(self.clear_all)
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept)
        row.addWidget(self.top_btn)
        row.addWidget(self.none_btn)
        row.addStretch(1)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.ok_btn)
        root.addLayout(row)

        self._loading = False
        self._populate(self.tree.invisibleRootItem(), self._base)
        self._update_count()

    # ---- 建树 ----
    def _make_item(self, folder):
        rel = folder.relative_to(self._base).as_posix()
        it = QtWidgets.QTreeWidgetItem([folder.name, rel])
        it.setData(0, _REL_ROLE, rel)
        it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
        it.setCheckState(0, QtCore.Qt.Checked if rel in self._checked else QtCore.Qt.Unchecked)
        if child_dirs(folder):
            ph = QtWidgets.QTreeWidgetItem(["…", ""])
            ph.setData(0, _REL_ROLE, _PLACEHOLDER)
            ph.setFlags(QtCore.Qt.ItemIsEnabled)
            it.addChild(ph)
        return it

    def _populate(self, parent_item, folder):
        self._loading = True
        try:
            for f in child_dirs(folder):
                parent_item.addChild(self._make_item(f))
        finally:
            self._loading = False

    def _on_expanded(self, item):
        # 第一次展开:把占位换成真正的子文件夹
        if item.childCount() == 1 and item.child(0).data(0, _REL_ROLE) == _PLACEHOLDER:
            item.takeChild(0)
            self._populate(item, self._base / item.data(0, _REL_ROLE))

    # ---- 勾选 ----
    def _on_item_changed(self, item, column):
        if self._loading or column != 0:
            return
        rel = item.data(0, _REL_ROLE)
        if not rel or rel == _PLACEHOLDER:
            return
        if item.checkState(0) == QtCore.Qt.Checked:
            self._checked.add(rel)
        else:
            self._checked.discard(rel)
        self._update_count()

    def _set_all_visible(self, state):
        self._loading = True
        try:
            def walk(parent):
                for i in range(parent.childCount()):
                    c = parent.child(i)
                    if c.data(0, _REL_ROLE) != _PLACEHOLDER:
                        c.setCheckState(0, QtCore.Qt.Checked if c.data(0, _REL_ROLE) in self._checked
                                        else QtCore.Qt.Unchecked)
                        walk(c)
            walk(self.tree.invisibleRootItem())
        finally:
            self._loading = False

    def select_top(self):
        self._checked = {f.relative_to(self._base).as_posix() for f in child_dirs(self._base)}
        self._set_all_visible(None)
        self._update_count()

    def clear_all(self):
        self._checked.clear()
        self._set_all_visible(None)
        self._update_count()

    def _update_count(self):
        self.count_label.setText(trp("已勾选 {0} 个文件夹", len(self._checked)))

    def picked(self):
        """相对 base 的路径列表(正斜杠),被祖先覆盖的子项已去掉。"""
        return drop_covered(self._checked)


def pick_folders(parent, base, title=None):
    """弹出多选框。返回相对路径列表;取消或什么都没勾返回 None。"""
    dlg = FolderPickerDialog(parent, base, title)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return None
    return dlg.picked() or None

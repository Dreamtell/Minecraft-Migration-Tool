# ui/qt_diff_view.py
"""模组差异扫描窗口（PySide6 版）。

为什么另写一个：Tk 版差异窗口的表格是 `VirtualTable`、卡片是早期的 `card_list`
（那套是"放大查看 Tk 后端"时代的代码，问题多）。现在直接复用 `qt_big_view` 里
放大查看用的那套控件 —— `SmoothTable` / `SmoothCards` / `TableDelegate` /
`CardDelegate` / `AnimButton` / `ScrollProgress`，两个窗口的观感、滚动、动效完全一致。

由 Tk 侧的 after 泵驱动（`pump()` / `is_alive()`），和放大窗口同一个机制；
所有"改 Tk 控件"的动作都要走 `hooks["defer"]`（从 Qt 回调里直接改 Tk 会让两套
消息循环打架）。
"""
import os
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ui import qt_big_view as Q
from ui.qt_big_view import (AnimButton, CardDelegate, CardModel, DetailDialog,
                            Entry, ScrollProgress, SmoothCards, SmoothTable,
                            TableDelegate, _header_qss, _mix, _style_view_palette,
                            ensure_app)

ROLE_ITEM = Q.ROLE_ITEM

# 列：(key, 标题, 宽度)
_COLS = (("check", "☑ 选择", 68),
         ("name", "📄 文件名", 240),
         ("status", "🔵 状态", 92),
         ("type", "🧩 类型", 92),
         ("modid", "🆔 Mod ID", 150),
         ("version", "🔖 版本", 112),
         ("size", "💾 大小(KB)", 92),
         ("note", "📝 备注", 280))

_STATUS_ORDER = {"新增": 0, "更新": 1, "目标独有": 2}
_STATUS_KEY = {"新增": "ok_fg", "更新": "log_warning_fg", "目标独有": "muted_fg"}
_STATUS_BG = {"新增": "success_bg", "更新": "warn_bg", "目标独有": "neutral_bg"}
_SORT_KEYS = ("name", "status", "type", "modid", "version", "size")


class DiffStore(QtCore.QObject):
    """差异数据 + 当前显示顺序 + 勾选集合。

    只实现 CardModel/CardDelegate/TableDelegate 用到的那部分接口（order/at/
    is_mod/reset/row_data），所以放大窗口那几个控件可以直接拿来用。
    """

    reset = QtCore.Signal()
    row_data = QtCore.Signal(int)

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.is_mod = True                  # 卡片兜底图标画 "M"、操作图标给全
        self.data = list(data or [])
        self.source_path = ""
        self.items = []
        self._jar_of = {}                   # id(Entry) -> jar 路径（卡片图标用）
        for d in self.data:
            it = Entry(str(d[0]))
            it.name = str(d[0])             # 文件名
            it.status = str(d[1])           # 新增 / 更新 / 目标独有
            it.size = str(d[3])             # 大小(KB)
            it.desc = str(d[4])             # 备注
            it.path = str(d[4])             # 表格「备注」列读的就是 path
            it.modid = str(d[5])
            it.version = str(d[6])
            it.type = str(d[7])
            it.checked = (it.status == "新增")   # 「新增」默认勾上（和 Tk 版一致）
            it.scanned = True
            it.icon_path = None
            self._jar_of[id(it)] = str(d[8]) if len(d) > 8 else ""
            self.items.append(it)
        self.order = list(range(len(self.items)))
        self._pos = {id(it): i for i, it in enumerate(self.items)}
        self.query = ""
        self.sort_col = None
        self.sort_rev = False

    # ---- 查询 ----
    def at(self, row):
        return self.items[self.order[row]]

    def row_of(self, item):
        return self._pos.get(id(item), -1)

    def jar_of(self, item):
        return self._jar_of.get(id(item), "")

    def checked_data(self):
        """所有勾选条目的"显示名"（应用时给主界面写清单用）。"""
        out = []
        for it in self.items:
            if it.checked:
                idx = self.row_of(it)
                src = self.data[self.order[idx]] if idx >= 0 else None
                out.append(str(src[0]) if src else it.name)
        return out

    # ---- 顺序 / 排序 / 搜索 ----
    def rebuild(self):
        q = self.query.strip().lower()
        idxs = list(range(len(self.items)))
        if q:
            idxs = [i for i in idxs if q in self.items[i].name.lower()
                    or q in str(self.items[i].modid).lower()
                    or q in str(self.items[i].version).lower()
                    or q in str(self.items[i].type).lower()
                    or q in str(self.items[i].path).lower()]
        if self.sort_col is not None:
            idxs.sort(key=self._sort_key(self.sort_col), reverse=self.sort_rev)
        self.order = idxs
        self._pos = {id(self.items[i]): r for r, i in enumerate(idxs)}
        self.reset.emit()

    def _sort_key(self, col):
        def key(i):
            it = self.items[i]
            if col == "status":
                return _STATUS_ORDER.get(it.status, 9)
            if col == "size":
                try:
                    return float(it.size)
                except (TypeError, ValueError):
                    return -1.0
            if col == "check":
                return 0 if it.checked else 1
            return str(getattr(it, col, "") or "").lower()
        return key

    def set_sort(self, col, rev):
        self.sort_col, self.sort_rev = col, rev
        self.rebuild()

    def set_query(self, text):
        self.query = text or ""
        self.rebuild()

    def toggle(self, row):
        if 0 <= row < len(self.order):
            it = self.at(row)
            it.checked = not it.checked
            self.row_data.emit(row)

    def set_checked(self, pred):
        for it in self.items:
            it.checked = bool(pred(it))
        self.reset.emit()


class DiffTableModel(QtCore.QAbstractTableModel):
    """差异表：勾选框 / 文件名 / 状态(彩色圆点) / 类型 / Mod ID / 版本 / 大小 / 备注。"""

    def __init__(self, store, theme, parent=None):
        super().__init__(parent)
        self.store = store
        self.theme = theme
        self.cols = _COLS
        self.hover_row = -1
        self._bg_cache = {}
        self._dot_cache = {}
        store.reset.connect(self._on_reset)
        store.row_data.connect(self._on_row)

    def _on_reset(self):
        self.beginResetModel()
        self.endResetModel()

    def _on_row(self, row):
        if 0 <= row < len(self.store.order):
            self.dataChanged.emit(self.index(row, 0),
                                  self.index(row, self.columnCount() - 1))

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.store.order)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return self.cols[section][1]
        return None

    def _dot(self, status):
        hit = self._dot_cache.get(status)
        if hit is not None:
            return hit
        color = self.theme.get(_STATUS_KEY.get(status, "muted_fg"), "#808080")
        pm = QtGui.QPixmap(12, 12)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(color))
        p.drawEllipse(1, 1, 10, 10)
        p.end()
        self._dot_cache[status] = pm
        return pm

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        cname = self.cols[index.column()][0]
        it = self.store.at(row)
        if role == ROLE_ITEM:
            return it
        if role == QtCore.Qt.DisplayRole:
            if cname == "check":
                return "☑" if it.checked else "☐"
            if cname == "name":
                return it.name
            if cname == "status":
                return it.status
            if cname == "note":
                return it.path
            if cname == "type":
                v = it.type if it.type and it.type != "?" else "…"
                return "🧩 " + v if v != "…" else v
            v = getattr(it, cname, "")
            return "…" if v in (None, "") else str(v)
        if role == QtCore.Qt.DecorationRole and cname == "status":
            return self._dot(it.status)
        if role == QtCore.Qt.TextAlignmentRole:
            if cname in ("check", "size"):
                return int(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter) \
                    if cname == "check" else int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if role == QtCore.Qt.ToolTipRole:
            return "%s\n%s\n%s" % (it.name, it.path, it.status)
        if role == QtCore.Qt.BackgroundRole:
            key = (bool(it.checked), it.status, row == self.hover_row)
            c = self._bg_cache.get(key)
            if c is None:
                c = self._make_bg(it, row == self.hover_row)
                self._bg_cache[key] = c
            return c
        if role == QtCore.Qt.ForegroundRole and it.checked:
            return QtGui.QColor(self.theme.get("card_sel_fg", "#0d3d63"))
        return None

    def _make_bg(self, it, hovered):
        th = self.theme
        if it.checked:                      # 勾选：选中蓝（和放大窗口一致）
            return QtGui.QColor(_mix(th.get("bg", "#ffffff"),
                                     th.get("card_sel_bg", "#d4e6f8"), 0.85))
        bg = th.get(_STATUS_BG.get(it.status, ""), "")
        if bg:
            return QtGui.QColor(bg)         # 新增=绿底、更新=橙底、目标独有=灰底
        if hovered:
            return QtGui.QColor(th.get("hover_bg", "#eef3f8"))
        return None


class QtDiffView(QtWidgets.QWidget):
    """差异扫描窗口（由 Tk 侧 after 泵驱动）。"""

    def __init__(self, data, theme, hooks=None, apply_callback=None, parent=None):
        super().__init__(parent)
        self.theme = dict(theme)
        self.hooks = hooks or {}
        self.apply_callback = apply_callback
        self._alive = True
        self.store = DiffStore(data, parent=self)
        self.setWindowTitle("智能模组差异扫描（元数据级）")
        self.setMinimumSize(1120, 520)
        self.resize(1280, 640)
        self._dc_row = None
        self._dialogs = []
        self._build()
        self._icon_queue = list(range(len(self.store.items)))
        self._icon_timer = QtCore.QTimer(self)
        self._icon_timer.setInterval(25)     # 图标懒解析：每 25ms 解一个，不卡界面
        self._icon_timer.timeout.connect(self._icon_tick)
        if self._icon_queue:
            self._icon_timer.start()

    # ------------------------------------------------------------------ UI
    def _build(self):
        th = self.theme
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QtWidgets.QFrame()
        self.frame = frame
        frame.setObjectName("root")
        outer.addWidget(frame)
        lay = QtWidgets.QVBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(8)

        # ---- 标题 ----
        bar = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("🧩 模组差异扫描")
        f = self.title_label.font()
        f.setPointSize(11)
        f.setBold(True)
        self.title_label.setFont(f)
        bar.addWidget(self.title_label)
        self.tag_label = QtWidgets.QLabel("PySide6")
        bar.addWidget(self.tag_label)
        bar.addStretch(1)
        self.hint_tag = QtWidgets.QLabel("🪟 系统原生窗口")
        bar.addWidget(self.hint_tag)
        lay.addLayout(bar)

        # ---- 工具条：选择类 + 视图 + 搜索 ----
        tools = QtWidgets.QHBoxLayout()
        tools.setSpacing(6)
        self.btn_new = AnimButton("✅ 全选新增", "#43a047", "#2e7d32", th)
        self.btn_upd = AnimButton("🔄 全选更新", "#fb8c00", "#e65100", th)
        self.btn_only = AnimButton("📌 全选目标独有", "#6b7280", "#4b5563", th)
        self.btn_combo = AnimButton("▾ 组合选择", "#26a69a", "#00838f", th)
        self.btn_all = AnimButton("☑ 全选", "#7c6cf0", "#5b4bd6", th)
        self.btn_none = AnimButton("⬜ 清空勾选", "#6b7280", "#4b5563", th)
        self.btn_view = AnimButton("🗂 卡片视图", "#0ea5a4", "#0b7f7f", th)
        for b in (self.btn_new, self.btn_upd, self.btn_only, self.btn_combo,
                  self.btn_all, self.btn_none):
            tools.addWidget(b)
        tools.addStretch(1)
        tools.addWidget(self.btn_view)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("🔍 搜索（文件名 / Mod ID / 版本 / 类型 / 备注）")
        self.search.setFixedHeight(30)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(260)
        tools.addWidget(self.search)
        lay.addLayout(tools)

        self.btn_new.clicked.connect(lambda: self._by_status("新增"))
        self.btn_upd.clicked.connect(lambda: self._by_status("更新"))
        self.btn_only.clicked.connect(lambda: self._by_status("目标独有"))
        self.btn_all.clicked.connect(lambda: self._set_checked(lambda it: True))
        self.btn_none.clicked.connect(lambda: self._set_checked(lambda it: False))
        self.btn_view.clicked.connect(self._toggle_view)

        combo_menu = QtWidgets.QMenu(self)
        combo_menu.addAction("新增 + 更新（排除目标独有）",
                             lambda: self._by_status("新增", "更新"))
        combo_menu.addAction("新增 + 目标独有",
                             lambda: self._by_status("新增", "目标独有"))
        combo_menu.addAction("更新 + 目标独有",
                             lambda: self._by_status("更新", "目标独有"))
        self._combo_menu = combo_menu
        self.btn_combo.clicked.connect(
            lambda: combo_menu.exec(self.btn_combo.mapToGlobal(
                QtCore.QPoint(0, self.btn_combo.height()))))
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)          # 输入即过滤，250ms 防抖
        self._search_timer.timeout.connect(
            lambda: self.store.set_query(self.search.text()))
        self.search.textChanged.connect(lambda *_: self._search_timer.start())

        # ---- 摘要 + 排序 + 应用/关闭 ----
        mid = QtWidgets.QHBoxLayout()
        mid.setSpacing(6)
        self.summary = QtWidgets.QLabel("")
        self.summary.setTextFormat(QtCore.Qt.RichText)
        mid.addWidget(self.summary)
        mid.addStretch(1)
        mid.addWidget(QtWidgets.QLabel("排序依据："))
        self.sort_combo = QtWidgets.QComboBox()
        for key, label in (("name", "文件名"), ("status", "状态"), ("type", "类型"),
                           ("modid", "Mod ID"), ("version", "版本"), ("size", "大小(KB)")):
            self.sort_combo.addItem(label, key)
        self.sort_combo.setFixedHeight(28)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        mid.addWidget(self.sort_combo)
        self.btn_dir = AnimButton("▲ 升序", "#607d8b", "#455a64", th)
        mid.addWidget(self.btn_dir)
        self.btn_apply = AnimButton("✅ 应用所选", "#00c853", "#00a344", th)
        self.btn_close = AnimButton("✖ 关闭", "#757575", "#5a5a5a", th)
        mid.addWidget(self.btn_apply)
        mid.addWidget(self.btn_close)
        lay.addLayout(mid)
        self.btn_dir.clicked.connect(self._toggle_dir)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_close.clicked.connect(self.close)

        # ---- 滚动进度条（液态，和放大窗口同一套）----
        self.scroll_progress = ScrollProgress(th, self)
        self.scroll_pct = QtWidgets.QLabel("100%")
        self.scroll_pct.setFixedWidth(44)
        self.scroll_pct.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        行 = QtWidgets.QHBoxLayout()
        行.setContentsMargins(2, 0, 2, 0)
        行.setSpacing(8)
        行.addWidget(self.scroll_progress, 1)
        行.addWidget(self.scroll_pct)
        lay.addLayout(行)
        self.scroll_progress.set_pump(self.hooks.get("after"),
                                      self.hooks.get("after_cancel"))

        # ---- 表格 ----
        self.table_model = DiffTableModel(self.store, th, self)
        self.table = SmoothTable()
        self.table.setModel(self.table_model)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.table.setFocusPolicy(QtCore.Qt.NoFocus)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.table.setWordWrap(False)
        self.table.setMouseTracking(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        hh = self.table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.setHighlightSections(False)
        hh.setFixedHeight(30)
        for i, (_k, _t, w) in enumerate(self.table_model.cols):
            self.table.setColumnWidth(i, w)
            hh.setSectionResizeMode(i, QtWidgets.QHeaderView.Interactive)
        note_col = next((i for i, c in enumerate(self.table_model.cols)
                         if c[0] == "note"), 7)
        hh.setSectionResizeMode(note_col, QtWidgets.QHeaderView.Stretch)
        hh.sectionClicked.connect(self._on_header_click)
        hh.installEventFilter(self)
        _style_view_palette(self.table, th)
        hh.setStyleSheet(_header_qss(th))
        self.table.setItemDelegate(TableDelegate(self.table))
        self.table.clicked.connect(self._on_table_click)
        self.table.doubleClicked.connect(self._on_table_double)
        self.table.mouseMoveEvent = self._table_mouse_move
        self.table.leaveEvent = self._table_leave

        # ---- 卡片 ----
        self.card_model = CardModel(self.store, self)
        self.cards = SmoothCards()
        self.cards.setModel(self.card_model)
        self.cards.setUniformItemSizes(True)
        self.cards.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.cards.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.cards.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.cards.setMouseTracking(True)
        self.card_delegate = CardDelegate(self.store, th, self.cards)
        self.cards.setItemDelegate(self.card_delegate)
        self.cards.clicked.connect(self._on_card_click)
        self.cards.doubleClicked.connect(self._on_card_double)
        self.cards.mouseMoveEvent = self._card_mouse_move
        self.cards.leaveEvent = self._card_leave

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.cards)
        lay.addWidget(self.stack, 1)
        for sb in (self.table.verticalScrollBar(), self.cards.verticalScrollBar()):
            sb.valueChanged.connect(self._update_progress)
            sb.rangeChanged.connect(lambda *_a: self._update_progress())
        self.stack.currentChanged.connect(lambda *_a: self._update_progress())

        # ---- 底栏 ----
        bottom = QtWidgets.QHBoxLayout()
        self.hint = QtWidgets.QLabel(
            "单击=勾选 · 双击=模组详情 · Ctrl+A=全选 · Ctrl+F=搜索 · Esc=关闭")
        bottom.addWidget(self.hint)
        bottom.addStretch(1)
        lay.addLayout(bottom)

        self._apply_style()
        self._update_summary()
        self.store.rebuild()

    # ------------------------------------------------------------------ 主题
    def _apply_style(self):
        th = self.theme
        self.frame.setStyleSheet("#root{background:%s;}" % th.get("bg", "#ffffff"))
        self.title_label.setStyleSheet("color:%s;" % th.get("fg"))
        self.tag_label.setStyleSheet("color:%s;background:%s;border-radius:7px;"
                                     "padding:1px 8px;"
                                     % (th.get("accent_fg"), th.get("accent_bg")))
        self.hint_tag.setStyleSheet("color:%s;" % th.get("muted_fg"))
        self.hint.setStyleSheet("color:%s;" % th.get("muted_fg"))
        self.summary.setStyleSheet("color:%s;font-size:10pt;" % th.get("muted_fg"))
        self.scroll_pct.setStyleSheet("color:%s;" % th.get("muted_fg"))
        self.search.setStyleSheet(
            "QLineEdit{background:%s;color:%s;border:1px solid %s;border-radius:8px;"
            "padding:0 10px;}"
            "QLineEdit:focus{border:2px solid %s;}"
            % (th.get("entry_bg"), th.get("fg"), th.get("muted_fg"),
               th.get("card_sel_bar")))
        self.sort_combo.setStyleSheet(
            "QComboBox{background:%s;color:%s;border:1px solid %s;border-radius:6px;"
            "padding:0 8px;}"
            % (th.get("entry_bg"), th.get("fg"), th.get("muted_fg")))
        _style_view_palette(self.table, th)
        self.table.horizontalHeader().setStyleSheet(_header_qss(th))
        self.scroll_progress.set_theme(th)

    def apply_theme(self, theme):
        """主界面切主题时调用（Qt 控件不吃 Tk 的 apply_theme_to_widget_tree）。"""
        self.theme = dict(theme)
        self.table_model.theme = self.theme
        self.table_model._bg_cache.clear()
        self.table_model._dot_cache.clear()
        self.card_delegate.theme = self.theme
        self.card_delegate._pix.clear()
        self.scroll_progress.theme = self.theme
        self._apply_style()
        self._update_summary()
        self.table.viewport().update()
        self.cards.viewport().update()

    def set_theme(self, theme):
        """放大窗口那边的接口名（主界面 apply_theme 里统一调 set_theme）。"""
        self.apply_theme(theme)

    # ------------------------------------------------------------------ 视图
    def _toggle_view(self):
        cards = self.stack.currentIndex() == 0
        self.stack.setCurrentIndex(1 if cards else 0)
        self.btn_view.setText("📋 表格视图" if cards else "🗂 卡片视图")
        self._update_progress()

    # ------------------------------------------------------------------ 交互
    def _on_table_click(self, index):
        row = index.row()
        if self._dc_row == row:          # 双击的第二下：吃掉，别来回切
            self._dc_row = None
            return
        self.store.toggle(row)
        self._update_summary()

    def _on_table_double(self, index):
        row = index.row()
        self.store.toggle(row)           # 撤回第一下造成的勾选变化
        self._dc_row = row
        QtCore.QTimer.singleShot(0, lambda: setattr(self, "_dc_row", None))
        self._update_summary()
        self._show_detail(row)

    def _on_card_click(self, index):
        self.store.toggle(index.row())
        self._update_summary()

    def _on_card_double(self, index):
        self.store.toggle(index.row())   # 撤回第一下
        self._update_summary()
        self._show_detail(index.row())

    def _table_mouse_move(self, ev):
        row = self.table.indexAt(ev.position().toPoint()).row()
        if row != self.table_model.hover_row:
            self.table_model.hover_row = row
            self.table.viewport().update()

    def _table_leave(self, ev):
        if self.table_model.hover_row != -1:
            self.table_model.hover_row = -1
            self.table.viewport().update()

    def _card_mouse_move(self, ev):
        idx = self.cards.indexAt(ev.position().toPoint())
        row = idx.row() if idx.isValid() else -1
        if row != self.card_delegate.hover_row:
            self.card_delegate.hover_row = row
            self.cards.viewport().update()

    def _card_leave(self, ev):
        if self.card_delegate.hover_row != -1:
            self.card_delegate.hover_row = -1
            self.cards.viewport().update()

    def _on_header_click(self, col):
        key = self.table_model.cols[col][0]
        if key not in _SORT_KEYS:
            return
        if self.store.sort_col == key:
            self.store.set_sort(key, not self.store.sort_rev)
        else:
            self.store.set_sort(key, False)
        self._sync_sort_ui()

    def _on_sort_changed(self, _i):
        key = self.sort_combo.currentData()
        if key:
            self.store.set_sort(key, self.store.sort_rev)
        self._sync_sort_ui()

    def _toggle_dir(self):
        self.store.set_sort(self.store.sort_col or self.sort_combo.currentData(),
                            not self.store.sort_rev)
        self._sync_sort_ui()

    def _sync_sort_ui(self):
        self.btn_dir.setText("▼ 降序" if self.store.sort_rev else "▲ 升序")
        key = self.store.sort_col
        idx = self.sort_combo.findData(key) if key else -1
        if idx >= 0 and idx != self.sort_combo.currentIndex():
            self.sort_combo.blockSignals(True)
            self.sort_combo.setCurrentIndex(idx)
            self.sort_combo.blockSignals(False)
        hh = self.table.horizontalHeader()
        if key:
            col = next((i for i, c in enumerate(self.table_model.cols) if c[0] == key), -1)
            if col >= 0:
                hh.setSortIndicator(col, QtCore.Qt.DescendingOrder
                                    if self.store.sort_rev else QtCore.Qt.AscendingOrder)

    def _by_status(self, *statuses):
        self.store.set_checked(lambda it: it.status in statuses)
        self._update_summary()

    def _set_checked(self, pred):
        self.store.set_checked(pred)
        self._update_summary()

    def _show_detail(self, row):
        if not (0 <= row < len(self.store.order)):
            return
        it = self.store.at(row)
        jar = self.store.jar_of(it)

        def _reveal(path):
            """在资源管理器里定位文件（Qt 的 QProcess，不碰 Windows 消息层）。"""
            try:
                QtCore.QProcess.startDetached("explorer", ["/select,", str(path)])
            except Exception:
                pass

        dlg = DetailDialog(it, self.theme, _reveal, self)
        self._dialogs.append(dlg)
        dlg.finished.connect(lambda _r, d=dlg: self._dialogs.remove(d)
                             if d in self._dialogs else None)
        dlg.show()

    # ------------------------------------------------------------------ 应用
    def _apply(self):
        files = self.store.checked_data()
        if not files:
            QtWidgets.QMessageBox.warning(self, "提示", "没有勾选任何模组")
            return
        cb = self.apply_callback
        defer = self.hooks.get("defer")
        if cb is not None:
            if defer is not None:
                defer(lambda: cb(files))     # 改 Tk 控件必须回 Tk 的 after 里做
            else:
                cb(files)
        self.close()

    # ------------------------------------------------------------------ 杂项
    def _update_summary(self):
        """摘要：总数 + 三种状态 + 已选（数字按语义上色）。"""
        th = self.theme
        total = len(self.store.items)
        n_new = sum(1 for it in self.store.items if it.status == "新增")
        n_upd = sum(1 for it in self.store.items if it.status == "更新")
        n_only = sum(1 for it in self.store.items if it.status == "目标独有")
        picked = sum(1 for it in self.store.items if it.checked)
        shown = len(self.store.order)
        parts = ["总计 %d 项差异" % total]
        if self.store.query.strip():
            parts.append("已过滤，显示 %d 项" % shown)
        parts += ["新增 %d" % n_new, "更新 %d" % n_upd, "目标独有 %d" % n_only,
                  "已选 %d" % picked]
        self.summary.setText(" ｜ ".join(parts))
        self.title_label.setText("🧩 模组差异扫描 · %d 项" % total)

    def _update_progress(self):
        view = self.table if self.stack.currentIndex() == 0 else self.cards
        sb = view.verticalScrollBar()
        frac = self.scroll_progress.set_range(sb.value(), sb.maximum(), sb.pageStep())
        self.scroll_pct.setText("%d%%" % round(frac * 100))

    def _icon_tick(self):
        """懒解析卡片图标：一次一个，解完通知那一行重画。"""
        if not self._icon_queue:
            self._icon_timer.stop()
            return
        row = self._icon_queue.pop(0)
        it = self.store.items[row] if 0 <= row < len(self.store.items) else None
        if it is not None:
            try:
                jar = self.store.jar_of(it)
                if jar and os.path.exists(jar):
                    from core.scanner import get_mod_icon
                    path = get_mod_icon(jar)
                    if path:
                        it.icon_path = path
            except Exception:
                pass
            r = self.store.row_of(it)
            if r >= 0:
                self.store.row_data.emit(r)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == QtCore.Qt.Key_Escape:
            self.close()
        elif ev.modifiers() & QtCore.Qt.ControlModifier:
            if k == QtCore.Qt.Key_A:
                self._set_checked(lambda it: True)
            elif k == QtCore.Qt.Key_F:
                self.search.setFocus()
        super().keyPressEvent(ev)

    # ---- 泵接口（和 QtBigView 一致）----
    def pump(self):
        now = time.perf_counter()
        if getattr(self.table, "_sw_active", False):
            self.table.tick_scroll(now)
        if getattr(self.cards, "_sw_active", False):
            self.cards.tick_scroll(now)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents(QtCore.QEventLoop.AllEvents, 8)

    def is_alive(self):
        return self._alive

    def show_centered(self):
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, ev):
        self._alive = False
        try:
            self.scroll_progress.stop()
            self._icon_timer.stop()
        except Exception:
            pass
        for d in list(self._dialogs):
            try:
                d.close()
            except Exception:
                pass
        self._dialogs = []
        super().closeEvent(ev)


def available():
    """PySide6 能不能用。"""
    return Q.available()


def show_diff_view(data, theme, hooks=None, apply_callback=None, parent=None):
    """创建并显示差异窗口，返回 QtDiffView（Tk 侧拿它做 pump）。"""
    app = ensure_app(theme)
    view = QtDiffView(data, theme, hooks=hooks, apply_callback=apply_callback,
                      parent=parent)
    view.show_centered()
    app.processEvents(QtCore.QEventLoop.AllEvents, 10)
    return view

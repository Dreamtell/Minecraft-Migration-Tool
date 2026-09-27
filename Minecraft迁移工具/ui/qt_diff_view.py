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
                            Entry, OverviewBar, ScrollProgress, SmoothCards,
                            SmoothTable, TableDelegate, _header_qss, _mix,
                            _style_view_palette, ensure_app)
from core.scanner import detect_instance_env

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

_STATUS_ORDER = {"新增": 0, "更新": 1, "降级": 2, "目标独有": 3}
# 状态 → 主题色键 / 行底色键。降级用红：那一条复制过去是倒退，得显眼
_STATUS_KEY = {"新增": "ok_fg", "更新": "log_warning_fg", "降级": "fail_fg",
               "目标独有": "muted_fg"}
_STATUS_BG = {"新增": "success_bg", "更新": "warn_bg", "降级": "danger_bg",
              "目标独有": "neutral_bg"}
_SORT_KEYS = ("name", "status", "type", "modid", "version", "size")


class DiffStore(QtCore.QObject):
    """差异数据 + 当前显示顺序 + 勾选集合。

    只实现 CardModel/CardDelegate/TableDelegate 用到的那部分接口（order/at/
    is_mod/reset/row_data），所以放大窗口那几个控件可以直接拿来用。
    """

    reset = QtCore.Signal()
    row_data = QtCore.Signal(int)
    # 卡片上给哪些悬停操作图标（差异窗口没有"移出清单"，只给详情/打开位置）
    card_actions = ("info", "reveal")

    def __init__(self, data, theme, parent=None):
        super().__init__(parent)
        self.is_mod = True                  # 卡片兜底图标画 "M"、操作图标给全
        self.theme = dict(theme or {})
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
            # sel_t 是卡片委托画"选中蓝底 + 左侧高亮条"用的进度，默认勾上的要有
            it.sel_t = 1.0 if it.checked else 0.0
            it.tags = [it.status]                # 卡片上的状态 chip（新增/更新/目标独有）
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

    def status_color(self, item):
        """状态圆点色（CardDelegate 的状态色钩子）：新增=绿、更新=橙、目标独有=灰。"""
        return self.theme.get(_STATUS_KEY.get(getattr(item, "status", ""), "muted_fg"),
                              "#808080")

    def overview_colors(self):
        """右侧总览条的配色：**每个当前显示的行**一个状态色块（顺序 = 屏幕顺序）。

        刻意不掺"已勾选"：差异窗口里「新增」默认就是全勾的，一掺进去整条都是蓝的，
        反而看不出状态分布了。
        「新增」= 正常状态 → 半透明淡下去；更新/降级/目标独有保持原色，才显眼。
        """
        淡 = OverviewBar._ALPHA_DIM
        出 = []
        for i in self.order:
            it = self.items[i]
            色 = self.status_color(it)
            出.append((色, 淡) if getattr(it, "status", "") == "新增" else 色)
        return 出

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
        self._want_top = True          # 重新排序/过滤 → 列表回到顶部
        self.reset.emit()

    def take_want_top(self):
        """取一次"这次 reset 要不要把列表滚回顶部"，取完就清掉。"""
        v = getattr(self, "_want_top", False)
        self._want_top = False
        return v

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
            return it
        return None

    def set_checked(self, pred):
        for it in self.items:
            it.checked = bool(pred(it))
            it.sel_t = 1.0 if it.checked else 0.0     # 批量改直接到位，不逐个动画
        self._want_top = False        # 批量全选/清空不该把列表拽回顶部
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

    def __init__(self, data, theme, hooks=None, apply_callback=None, parent=None,
                 cards=False, source_path=""):
        super().__init__(parent)
        self.theme = dict(theme)
        self.hooks = hooks or {}
        self.apply_callback = apply_callback
        self._alive = True
        # 源实例的环境（MC 版本 + 加载器）：详情窗里的联网搜索拿它过滤候选池
        self.env = detect_instance_env(source_path) if source_path else \
            {"mc": "", "loader": "", "src": ""}
        # 打开时用表格还是卡片：默认视图在设置里选（_DIFF_VIEWS），这里只管摆好初始状态
        self._start_cards = bool(cards)
        self.store = DiffStore(data, theme, parent=self)
        self.setWindowTitle("智能模组差异扫描（元数据级）")
        self.setMinimumSize(1120, 520)
        self.resize(1280, 640)
        self._dc_row = None
        self._card_hit = (-1, None)   # 卡片上悬停图标的命中结果（点击时复用）
        self._dialogs = []
        self._sel_anims = {}          # id(Entry) -> Tk after job（卡片选中动画）
        self._build()
        # 图标懒解析：**用 Tk 的 after 驱动，不用 QTimer**。
        # 解析一个图标要解压 jar + PIL 解码（主线程上 ~9ms），放在 Qt 的定时器回调里
        # 就是"在 Qt 的事件处理内部再回调进 Python"—— 那正是 shiboken 线程状态
        # 保存/恢复最容易出错的地方。改到 Tk 的 after 里做，和别的 Tk 操作同一上下文；
        # 间隔也放宽到 120ms（413 个约 50 秒铺完，反正图标是慢慢出现的）。
        self._icon_job = None
        self._icon_queue = list(range(len(self.store.items)))
        if self._icon_queue:
            self._schedule_icon()

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
        # 清空勾选是"破坏性"操作（勾好了手一抖就全没了），用红色
        self.btn_none = AnimButton("⬜ 清空勾选", "#e53935", "#c62828", th)
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
        # 用 popup 而不是 exec，而且丢回 Tk 的 after 里弹：exec 会开一层**嵌套事件循环**，
        # 而这段代码跑在 Tk 的 after → processEvents 回调里，嵌套事件循环 + 在 Qt 栈上
        # 显示窗口是这类"两套循环共存"崩溃的高发区。
        self.btn_combo.clicked.connect(
            lambda: self._defer(lambda: combo_menu.popup(
                self.btn_combo.mapToGlobal(
                    QtCore.QPoint(0, self.btn_combo.height())))))
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
        self.btn_close = AnimButton("✖ 关闭", "#e53935", "#c62828", th)
        mid.addWidget(self.btn_apply)
        mid.addWidget(self.btn_close)
        lay.addLayout(mid)
        self.btn_dir.clicked.connect(self._toggle_dir)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_close.clicked.connect(self.close)

        # ---- 滚动进度条（液态，和放大窗口同一套）----
        self.scroll_progress = ScrollProgress(th, self)
        self.scroll_pct = QtWidgets.QLabel("0%")
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
        # 悬停高亮靠 viewport 事件过滤器，不能替换视图的 mouseMoveEvent/leaveEvent
        # （Qt 不会把 Leave 转给视图的 leaveEvent —— 详见 qt_big_view.QtBigView.eventFilter）
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)

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
        self.cards.viewport().setMouseTracking(True)
        self.cards.viewport().installEventFilter(self)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.cards)
        # 列表 + 右侧总览条（VSCode minimap 那种：整份差异压成一条竖缩略图）
        列表行 = QtWidgets.QHBoxLayout()
        列表行.setContentsMargins(0, 0, 0, 0)
        列表行.setSpacing(0)            # 紧贴滚动条：中间留缝会让左边看着多出几像素
        列表行.addWidget(self.stack, 1)
        self.overview = OverviewBar(th, self)
        列表行.addWidget(self.overview)
        lay.addLayout(列表行, 1)
        self.overview.jumped.connect(self._jump_to_overview)
        for sb in (self.table.verticalScrollBar(), self.cards.verticalScrollBar()):
            sb.valueChanged.connect(self._update_progress)
            sb.rangeChanged.connect(lambda *_a: self._update_progress())
        self.stack.currentChanged.connect(lambda *_a: self._update_progress())
        if self._start_cards:                 # 设置里选了"打开就是卡片"
            self.stack.setCurrentIndex(1)
            self.btn_view.setText("📋 表格视图")

        # ---- 底栏 ----
        bottom = QtWidgets.QHBoxLayout()
        self.hint = QtWidgets.QLabel(
            "单击=勾选 · 双击=模组详情 · Ctrl+A=全选 · Ctrl+F=搜索 · Esc=关闭")
        bottom.addWidget(self.hint)
        bottom.addStretch(1)
        lay.addLayout(bottom)

        self._apply_style()
        self._update_summary()
        self.store.reset.connect(self._on_store_reset)
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
        if getattr(self, "overview", None) is not None:
            self.overview.set_theme(th)
            self._refresh_overview()

    def apply_theme(self, theme):
        """主界面切主题时调用（Qt 控件不吃 Tk 的 apply_theme_to_widget_tree）。"""
        self.theme = dict(theme)
        self.table_model.theme = self.theme
        self.table_model._bg_cache.clear()
        self.table_model._dot_cache.clear()
        self.store.theme = self.theme          # 卡片状态色/表格圆点都从这儿取
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
    def _on_store_reset(self):
        """排序 / 搜索过滤之后，把列表滚回顶部。

        不然卡片会停在原来的滚动位置（实测：换排序依据后滚动条仍是 1325、顶部可见第 12
        条），看着就和表格"对不上"—— 用户反馈的正是这个。批量全选不跳（那不算重新排序）。
        """
        self._update_summary()          # 排序/过滤后摘要与总览条的颜色要跟着重出
        if not self.store.take_want_top():
            return
        self._t("排序/过滤后回顶部")
        for sb in (self.table.verticalScrollBar(), self.cards.verticalScrollBar()):
            try:
                sb.setValue(0)
            except Exception:
                pass
        self._update_progress()

    def _toggle_view(self):
        cards = self.stack.currentIndex() == 0
        # 切视图时按"当前第一行"把滚动位置对齐：表格一行 26px、卡片一张 104px，
        # 直接沿用像素值会跳位置（看着像"两个视图显示的不是同一批"）
        row_h = max(1, self.table.verticalHeader().defaultSectionSize())
        if cards:
            row = self.table.verticalScrollBar().value() // row_h
            target = row * Q.CARD_H
        else:
            row = self.cards.verticalScrollBar().value() // max(1, Q.CARD_H)
            target = row * row_h
        self.stack.setCurrentIndex(1 if cards else 0)
        self.btn_view.setText("📋 表格视图" if cards else "🗂 卡片视图")
        self._t("切到%s" % ("卡片" if cards else "表格"))
        view = self.cards if cards else self.table
        sb = view.verticalScrollBar()
        try:
            sb.setValue(max(0, min(int(target), sb.maximum())))
        except Exception:
            pass
        self._update_progress()

    # ------------------------------------------------------------------ 交互
    def _animate_sel(self, it):
        """勾选/取消时让卡片的选中效果"长出来 / 收回去"（和放大窗口同一套）。

        CardDelegate 画的是 `sel_t` 这个 0..1 的进度，不是 checked —— 只改 checked 的话
        卡片上一点变化都看不到（表格那行倒是立刻变色）。动画由 Tk 的 after 驱动。
        """
        if it is None:
            return
        to_t = 1.0 if it.checked else 0.0
        after = self.hooks.get("after")
        cancel = self.hooks.get("after_cancel")
        old = self._sel_anims.pop(id(it), None)
        if old is not None and cancel is not None:
            try:
                cancel(old)
            except Exception:
                pass
        if after is None:                       # 没有泵就直接到位
            it.sel_t = to_t
            r = self.store.row_of(it)
            if r >= 0:
                self.store.row_data.emit(r)
            return
        from_t = float(it.sel_t or 0.0)
        state = {"n": 0}

        def step():
            state["n"] += 1
            p = min(1.0, state["n"] / 10.0)     # 10 帧 × 16ms ≈ 160ms
            it.sel_t = from_t + (to_t - from_t) * (1 - (1 - p) ** 3)
            r = self.store.row_of(it)
            if r >= 0:
                self.store.row_data.emit(r)
            if p >= 1.0:
                self._sel_anims.pop(id(it), None)
                return
            self._sel_anims[id(it)] = after(16, step)

        self._sel_anims[id(it)] = after(16, step)

    def _on_table_click(self, index):
        row = index.row()
        if self._dc_row == row:          # 双击的第二下：吃掉，别来回切
            self._dc_row = None
            return
        self._animate_sel(self.store.toggle(row))
        self._update_summary()

    def _on_table_double(self, index):
        row = index.row()
        self._animate_sel(self.store.toggle(row))   # 撤回第一下造成的勾选变化
        self._dc_row = row
        QtCore.QTimer.singleShot(0, lambda: setattr(self, "_dc_row", None))
        self._update_summary()
        self._show_detail(row)

    def _card_action(self, row, action):
        """点了卡片上的悬停图标：ℹ 开详情、📂 打开文件所在位置。"""
        if not (0 <= row < len(self.store.order)):
            return
        it = self.store.at(row)
        if action == "info":
            self._show_detail(row)
        elif action == "reveal":
            jar = self.store.jar_of(it) or it.path
            try:
                QtCore.QProcess.startDetached("explorer", ["/select,", str(jar)])
            except Exception:
                pass

    def _on_card_click(self, index):
        row, act = getattr(self, "_card_hit", (-1, None))
        if act and row == index.row():
            self._card_action(row, act)     # 点的是图标：只执行图标动作，不改勾选
            return
        self._animate_sel(self.store.toggle(index.row()))
        self._update_summary()

    def _on_card_double(self, index):
        row, act = getattr(self, "_card_hit", (-1, None))
        if act and row == index.row():
            return                          # 连点悬停图标不算"双击开详情"
        self._animate_sel(self.store.toggle(index.row()))   # 撤回第一下
        self._update_summary()
        self._show_detail(index.row())

    def eventFilter(self, obj, ev):
        """表头滚轮转发 + 表格/卡片的悬停（都盯 viewport，原因见 qt_big_view 里的说明）。"""
        t = ev.type()
        if t == QtCore.QEvent.Wheel and obj is self.table.horizontalHeader():
            self.table.wheelEvent(ev)
            return True
        if obj is self.table.viewport():
            if t == QtCore.QEvent.MouseMove:
                self._table_mouse_move(ev)
            elif t == QtCore.QEvent.Leave:
                self._table_leave(ev)
        else:
            # getattr：表格的过滤器装得比卡片早，建窗口期间就可能进来事件
            cards = getattr(self, "cards", None)
            if cards is not None and obj is cards.viewport():
                if t == QtCore.QEvent.MouseMove:
                    self._card_mouse_move(ev)
                elif t == QtCore.QEvent.Leave:
                    self._card_leave(ev)
        return super().eventFilter(obj, ev)

    def _table_mouse_move(self, ev):
        row = self.table.indexAt(ev.position().toPoint()).row()
        if row != self.table_model.hover_row:
            self.table_model.hover_row = row
            self.table.viewport().update()

    def _table_leave(self, ev=None):
        if self.table_model.hover_row != -1:
            self.table_model.hover_row = -1
            self.table.viewport().update()

    def _card_mouse_move(self, ev):
        """卡片上的悬停操作图标（ℹ 详情 / 📂 打开位置）也要能点中。

        命中在鼠标移动时算好存进 `_card_hit`，点击时直接复用 —— 和放大窗口同一套。
        """
        pos = ev.position().toPoint()
        idx = self.cards.indexAt(pos)
        row = idx.row() if idx.isValid() else -1
        action = None
        if row >= 0:
            rect = self.cards.visualRect(idx)
            for i, (name, _g) in enumerate(self.card_delegate.actions):
                if self.card_delegate.action_rect(rect, i).contains(pos):
                    action = name
                    break
        if (row, action) != (self.card_delegate.hover_row,
                             self.card_delegate.hover_action):
            self.card_delegate.hover_row = row
            self.card_delegate.hover_action = action
            self.cards.viewport().update()
        self._card_hit = (row, action)
        self.cards.setCursor(QtCore.Qt.PointingHandCursor if action
                             else QtCore.Qt.ArrowCursor)

    def _card_leave(self, ev=None):
        if self.card_delegate.hover_row != -1:
            self.card_delegate.hover_row = -1
            self.card_delegate.hover_action = None
            self._card_hit = (-1, None)
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

    def _t(self, msg):
        """记一行操作时间线（排查崩溃用：`.minecraft_migrate_clicks.log`）。"""
        try:
            from utils.helpers import trace_line
            trace_line("差异窗口 %s" % msg)
        except Exception:
            pass

    def _defer(self, fn):
        """把"建窗 / 弹菜单 / 弹框"这类重操作丢回 Tk 的 after，不在 Qt 事件栈上做。

        实测：在 `processEvents` 回调栈里显示新窗口会让 shiboken 重入它的线程状态
        保存/恢复，出致命的 `PyEval_RestoreThread ... the GIL is released`。
        """
        defer = self.hooks.get("defer")
        if defer is None:
            fn()
            return
        try:
            defer(fn)
        except Exception:
            fn()

    def _prune_dialogs(self):
        """清掉已经关掉的详情窗。

        **释放动作要延后到 Tk 的 after**：这里是 Qt 回调（processEvents）栈里，
        在这个栈上让 wrapper 引用归零会让 shiboken 在事件处理中析构 Qt 对象，
        踩到它的 tstate 保存/恢复 —— 就是那个致命的 PyEval_RestoreThread。
        """
        live, dead = [], []
        for d in getattr(self, "_dialogs", []):
            try:
                (live if d.isVisible() else dead).append(d)
            except RuntimeError:
                dead.append(d)
        self._dialogs = live
        if not dead:
            return

        def _drop():
            for d in list(dead):
                try:
                    d.deleteLater()
                except Exception:
                    pass
            dead.clear()

        defer = self.hooks.get("defer")
        if defer is not None:
            try:
                defer(_drop)
                return
            except Exception:
                pass
        _drop()

    def _show_detail(self, row):
        """打开模组详情。

        **建窗/显示这一步要回到 Tk 的 after 里做**：双击/点 ℹ 都跑在 Qt 的
        `processEvents` 回调栈上，在事件处理中途 `show()` 一个新窗口会让 shiboken
        重入线程状态的保存/恢复 —— 那就是致命的 `PyEval_RestoreThread`（用户实测崩过）。
        """
        defer = self.hooks.get("defer")
        if defer is None:
            return self._show_detail_now(row)
        结果 = {"v": None}
        try:
            defer(lambda: 结果.__setitem__("v", self._show_detail_now(row)))
        except Exception:
            pass
        return 结果["v"]          # 真实 after 是异步的，这里通常是 None

    def _show_detail_now(self, row):
        if not (0 <= row < len(self.store.order)):
            return None
        self._prune_dialogs()
        it = self.store.at(row)
        # 同一个模组只开一个窗：连点 ℹ 不该堆窗口
        for d in self._dialogs:
            try:
                if getattr(d, "_detail_key", None) == it.key and d.isVisible():
                    d.raise_()
                    d.activateWindow()
                    return d
            except RuntimeError:
                continue

        def _reveal(path):
            """在资源管理器里定位文件（Qt 的 QProcess，不碰 Windows 消息层）。"""
            try:
                QtCore.QProcess.startDetached("explorer", ["/select,", str(path)])
            except Exception:
                pass

        dlg = DetailDialog(it, self.theme, _reveal, self, env=getattr(self, "env", None))
        dlg._detail_key = it.key
        self._dialogs.append(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        try:
            from utils.helpers import trace_line
            trace_line("差异窗口打开详情 row=%d %s" % (row, it.name))
        except Exception:
            pass
        return dlg

    # ------------------------------------------------------------------ 应用
    def _apply(self):
        files = self.store.checked_data()
        self._t("应用所选 %d 个" % len(files))
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
        """摘要：总数 + 各状态计数 + 已选。"""
        total = len(self.store.items)
        计数 = []
        for 名 in ("新增", "更新", "降级", "目标独有"):
            n = sum(1 for it in self.store.items if it.status == 名)
            if n or 名 != "降级":            # 没有降级就别占地方
                计数.append((名, n))
        picked = sum(1 for it in self.store.items if it.checked)
        shown = len(self.store.order)
        parts = ["总计 %d 项差异" % total]
        if self.store.query.strip():
            parts.append("已过滤，显示 %d 项" % shown)
        for 名, n in 计数:
            parts.append("%s %d" % (名, n))
        parts.append("已选 %d" % picked)
        self.summary.setText(" ｜ ".join(parts))
        self.title_label.setText("🧩 模组差异扫描 · %d 项" % total)
        self._refresh_overview()

    def _update_progress(self):
        view = self.table if self.stack.currentIndex() == 0 else self.cards
        sb = view.verticalScrollBar()
        # 0 条差异（或过滤后一条不剩）时进度条要是空的，不能画满
        frac = self.scroll_progress.set_range(sb.value(), sb.maximum(), sb.pageStep(),
                                              rows=len(self.store.order))
        self.scroll_pct.setText("%d%%" % round(frac * 100))
        self._sync_overview_viewport(sb)

    def _sync_overview_viewport(self, sb=None):
        """总览条上的视口框：跟着滚动条走。"""
        try:
            if sb is None:
                sb = (self.table if self.stack.currentIndex() == 0
                      else self.cards).verticalScrollBar()
            总 = float(sb.maximum() + sb.pageStep())
            if 总 <= 0:
                self.overview.set_viewport(0.0, 1.0)
                return
            self.overview.set_viewport(sb.value() / 总,
                                       (sb.value() + sb.pageStep()) / 总)
        except Exception:
            pass

    def _refresh_overview(self):
        """总览条的颜色：整个（当前显示的）差异列表一行一个状态色块。"""
        try:
            self.overview.set_colors(self.store.overview_colors())
            self._sync_overview_viewport()
        except Exception:
            trace_exc("qt_diff_view", "刷新总览条")

    def _jump_to_overview(self, 比例):
        """在总览条上点/拖：把那个位置对到视口中间。"""
        try:
            view = self.table if self.stack.currentIndex() == 0 else self.cards
            sb = view.verticalScrollBar()
            page = float(sb.pageStep())
            总 = float(sb.maximum()) + page
            目标 = int(比例 * 总 - page / 2.0)
            sb.setValue(max(sb.minimum(), min(sb.maximum(), 目标)))
        except Exception:
            trace_exc("qt_diff_view", "总览条跳转")

    def _schedule_icon(self):
        """排下一张图标的解析（走 Tk 的 after；拿不到就退回 QTimer 单次触发）。"""
        if not self._icon_queue or not self._alive:
            return
        after = self.hooks.get("after")
        if after is not None:
            try:
                self._icon_job = after(120, self._icon_tick)
                return
            except Exception:
                pass
        try:
            QtCore.QTimer.singleShot(120, self._icon_tick)
            self._icon_job = None
        except Exception:
            self._icon_job = None

    def _icon_tick(self):
        """懒解析卡片图标：一次一个，解完通知那一行重画。"""
        self._icon_job = None
        if not self._icon_queue or not self._alive:
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
        self._schedule_icon()

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
        self._t("关闭")
        try:
            self.scroll_progress.stop()
        except Exception:
            pass
        job, self._icon_job = getattr(self, "_icon_job", None), None
        cancel = self.hooks.get("after_cancel")
        if job is not None and cancel is not None:
            try:
                cancel(job)
            except Exception:
                pass
        try:
            self._icon_queue = []
        except Exception:
            pass
        cancel = self.hooks.get("after_cancel")
        for job in list(getattr(self, "_sel_anims", {}).values()):
            if cancel is not None:
                try:
                    cancel(job)
                except Exception:
                    pass
        self._sel_anims = {}
        for d in list(self._dialogs):
            try:
                d.close()
            except Exception:
                pass
        # 引用归零也放到 Tk 的 after 里做（closeEvent 同样是 Qt 的处理栈）
        dialogs, self._dialogs = self._dialogs, []
        if dialogs:
            defer = self.hooks.get("defer")

            def _drop():
                for d in list(dialogs):
                    try:
                        d.deleteLater()
                    except Exception:
                        pass
                dialogs.clear()

            if defer is not None:
                try:
                    defer(_drop)
                except Exception:
                    _drop()
            else:
                _drop()
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

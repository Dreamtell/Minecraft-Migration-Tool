"""放大查看窗口 —— PySide6 试点实现。

为什么试点：圆角、投影、逐帧动画、平滑滚动在 Tk 里都要手工画图和缓存
（card_list.py 里那一堆 rounded_image / _corner_photo 就是为此存在的），
Qt 里是原生能力。

运行方式（关键）：
- 主窗口仍然是 Tk，QApplication 与 Tk mainloop 同进程共存；
- Tk 用 root.after 定时调用本窗口的 pump()，里面只做 QApplication.processEvents()；
- 所以 Qt 的所有回调（按钮点击、动画、绘制）都是在 Tk 的 after 回调里被调用的，
  也就是说它们跑在主线程、且不处于 Tk 的绘制过程中 —— 从这些回调里改 Tk 控件是安全的。

不依赖 Tk：本文件只通过 hooks 里的 write_back 与主窗口交互。
"""

from __future__ import annotations

import difflib
import importlib.util
import re
import sys
import time
import webbrowser
from pathlib import Path

# 开发期兜底：项目目录或仓库根目录下解压的 PySide6（正式使用应当 pip install PySide6）
try:
    _HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None
except Exception:                            # pragma: no cover
    _HAS_PYSIDE = False
if not _HAS_PYSIDE:                          # pragma: no cover
    for _cand in (Path(__file__).resolve().parent.parent / "_qt",
                  Path(__file__).resolve().parent.parent.parent / "_qt"):
        if (_cand / "PySide6").is_dir() and str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
            break

from PySide6 import QtCore, QtGui, QtWidgets   # noqa: E402

from core.scanner import (get_full_mod_metadata, get_mod_icon,   # noqa: E402
                          guess_tags, split_cn_name)
from core.migrator import _is_safe_path                          # noqa: E402
from core.mod_search import (fetch_project_latest, format_downloads,   # noqa: E402
                             search_modrinth)
from utils.helpers import (begin_bulk_scan, end_bulk_scan,          # noqa: E402
                           get_icon_path, style_window_hwnd, trace_exc, trace_line)

ROLE_ITEM = QtCore.Qt.UserRole + 1     # 让委托直接拿到 Entry 对象
CARD_H = 104                            # 卡片高度（固定，才能开启 uniformItemSizes）
CARD_MARGIN = 5
ICON_PX = 44
RADIUS = 9

# 双击间隔：直接用 Qt 自己的双击事件（clicked / doubleClicked），不再手写判定。
# 唯一要调的就是这个数（用户在手感上一点点试出来的，改一个数即可）。
# 现在的值：两次单击绝不会被并成双击，只有"哒哒"很快的两下才算双击；
# 慢一点的双击认不出来，那种情况走右键菜单「ℹ 详情」（表格/卡片视图都有）。
DOUBLE_CLICK_MS = 175


# ---- 关于拖入：Qt 窗口上做不了（三种接法都实测会偶发崩解释器） ----
# 1) Qt 自带的 OLE 拖放（setAcceptDrops + dragEnterEvent/dropEvent）：一拖就崩
#    （Fatal Python error: PyEval_RestoreThread ... GIL is released）。
# 2) QWidget.nativeEvent + WM_DROPFILES：连跑 6 次崩 3 次。
# 3) QAbstractNativeEventFilter + WM_DROPFILES：连跑 8 次崩 6 次。
# 共同病根：这些回调都是 Windows 消息层**直接**调进 Python，绕过了 Tk 的 after 泵 ——
# 而这个进程里 Qt 的所有回调本来都靠那个泵在 Tk 主线程里派发。
# 所以 Qt 窗口改成「Ctrl+V 粘贴文件」（纯 QClipboard API，不碰消息层，见 keyPressEvent），
# 拖入继续由 Tk 侧提供：主界面三个清单区、Tk 版放大窗口。

# 加载器 / 环境标签配色（与 card_list.TAG_COLORS 保持同一套观感）
TAG_COLORS = {
    "Fabric": ("#dbb69b", "#3b2c22"),
    "Quilt": ("#8b5cf6", "#ffffff"),
    "NeoForge": ("#f16436", "#ffffff"),
    "Forge": ("#5b6e7f", "#ffffff"),
    "客户端": ("#2f7fd1", "#ffffff"),
    "服务端": ("#2e7d32", "#ffffff"),
    "通用": ("#6b7280", "#ffffff"),
}
_TAG_FALLBACK = [("#7c6cf0", "#ffffff"), ("#0ea5a4", "#ffffff"), ("#c2410c", "#ffffff"),
                 ("#4d7c0f", "#ffffff"), ("#a21caf", "#ffffff"), ("#0369a1", "#ffffff")]


def available() -> bool:
    """PySide6 是否可用（本模块能 import 就说明可用）。"""
    return True


# --------------------------------------------------------------------------- #
# 颜色工具
# --------------------------------------------------------------------------- #
def _rgb(color) -> tuple:
    c = QtGui.QColor(color)
    return c.red(), c.green(), c.blue()


def _hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(v))) for v in rgb)
    return "#%02x%02x%02x" % (r, g, b)


def _mix(c1, c2, t: float) -> str:
    """把 c2 按比例 t 混进 c1（t=0 全是 c1，t=1 全是 c2）。"""
    a, b = _rgb(c1), _rgb(c2)
    return _hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def _qcolor(color, alpha: int = 255) -> QtGui.QColor:
    c = QtGui.QColor(color)
    c.setAlpha(alpha)
    return c


def _is_dark(theme: dict) -> bool:
    """主题是不是深色（按背景亮度算，和 Tk 侧 is_dark_theme 同一判据）。"""
    try:
        c = QtGui.QColor(theme.get("bg", "#ffffff"))
        return (c.red() * 0.299 + c.green() * 0.587 + c.blue() * 0.114) < 128
    except Exception:
        return False


def _tag_color(tag: str) -> tuple:
    if tag in TAG_COLORS:
        return TAG_COLORS[tag]
    return _TAG_FALLBACK[sum(map(ord, str(tag))) % len(_TAG_FALLBACK)]


# --------------------------------------------------------------------------- #
# 扫描（在工作线程里跑，只碰磁盘和纯函数）
# --------------------------------------------------------------------------- #
def resolve_entry(entry: str, is_mod: bool, mods_dir, config_dir):
    """解析清单条目为磁盘对象：模组=jar，config=配置目录下的相对路径。"""
    p = Path(entry)
    if is_mod:
        if p.is_file() and p.suffix.lower() == ".jar":
            return p
        if mods_dir is not None:
            cand = mods_dir / p.name if (p.suffix == "" and p.name) else mods_dir / entry
            if cand.is_file():
                return cand
        return None
    if config_dir is not None:
        cand = config_dir / entry if not p.is_absolute() else p
        if cand.exists():
            return cand
    return None


def scan_entry(entry: str, is_mod: bool, mods_dir, config_dir, online_tags: bool,
               source_path: str) -> dict:
    """扫描单条目，返回元数据 dict（字段与 Tk 版一致）。"""
    base = {"status": "❌ 缺失", "name": Path(entry).name or entry, "path": str(entry),
            "type": "?", "modid": "?", "version": "?", "size": "?", "cn": "", "desc": "",
            "disp": "", "tags": [], "tags_online": False, "icon_path": None}
    try:
        obj = resolve_entry(entry, is_mod, mods_dir, config_dir)
    except Exception:
        obj = None
    if obj is None:
        if is_mod:
            cn, _en = split_cn_name(Path(entry).name or entry)
            base["cn"] = cn
        elif not is_mod and source_path:
            # config 条目不存在时也给个可信的相对路径
            base["path"] = str(Path(source_path) / "config" / entry)
        return base
    try:
        if is_mod:
            info = get_full_mod_metadata(str(obj))
            cn, _en = split_cn_name(obj.name)
            desc = str(info.get("description", "") or "").replace("\n", " ").strip()
            base_tags = ([info.get("mod_type", "")]
                         if info.get("mod_type") in ("Fabric", "Forge") else []) \
                        + ([info["env"]] if info.get("env") else [])
            tags = base_tags + guess_tags(desc, info.get("name", ""), info.get("modid", ""))
            online = False
            if online_tags:
                try:
                    from core.mod_search import fetch_categories_cached
                    cats = fetch_categories_cached(info.get("modid") or obj.stem,
                                                   modid=info.get("modid", ""),
                                                   name=info.get("name", ""))
                    if cats:
                        tags, online = base_tags + cats, True
                except Exception:
                    pass
            try:
                icon_path = get_mod_icon(str(obj))
            except Exception:
                icon_path = None
            base.update({"status": "✅ 存在", "name": obj.name, "path": str(obj),
                         "type": info.get("mod_type", "?"), "modid": info.get("modid", "?"),
                         "version": info.get("version", "?"),
                         "size": round(obj.stat().st_size / 1024, 1),
                         "cn": cn, "disp": str(info.get("name") or "").strip(),
                         "desc": "" if desc in ("无", "未知") else desc,
                         "tags": tags, "tags_online": online, "icon_path": icon_path})
        else:
            size = round(obj.stat().st_size / 1024, 1) if obj.is_file() else ""
            base.update({"status": "✅ 存在", "name": obj.name, "path": str(obj),
                         "type": "文件夹" if obj.is_dir() else "文件", "modid": "",
                         "version": "", "size": size, "cn": "", "desc": "",
                         "icon_path": None})
    except Exception:
        pass
    return base


class _ScanSignals(QtCore.QObject):
    """QRunnable 不是 QObject，用一个信号载体把结果送回主线程。"""
    done = QtCore.Signal(object, dict)     # (Entry, meta)


class _ScanTask(QtCore.QRunnable):
    def __init__(self, item, ctx: dict, signals: _ScanSignals):
        super().__init__()
        self.item = item
        self.ctx = ctx
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):                          # 在 QThreadPool 的工作线程里执行
        try:
            meta = scan_entry(self.item.entry, self.ctx["is_mod"], self.ctx["mods_dir"],
                              self.ctx["config_dir"], self.ctx["online_tags"],
                              self.ctx["source_path"])
        except Exception:
            meta = {"status": "❌ 缺失"}
        try:
            self.signals.done.emit(self.item, meta)
        except RuntimeError:
            pass


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #
class Entry:
    """一条清单条目。sel_t = 选中动画进度(0..1)，pop_t = 进场/退场进度(0..1)，都由委托绘制使用。"""
    __slots__ = ("entry", "key", "is_new", "checked", "sel_t", "pop_t", "status", "name",
                 "path", "type", "modid", "version", "size", "cn", "disp", "desc", "tags",
                 "tags_online", "icon_path", "scanned")

    def __init__(self, entry: str, is_new: bool = False):
        self.entry = entry
        self.key = Path(entry).name.lower()
        self.is_new = is_new
        self.checked = False
        self.sel_t = 0.0
        self.pop_t = 1.0            # 1=完全就位；新加的会先被设成 0，再动画到 1
        self.status = "…"
        self.name = Path(entry).name or entry
        self.path = str(entry)
        self.type = "?"
        self.modid = "?"
        self.version = "?"
        self.size = "?"
        self.cn = ""
        self.disp = ""
        self.desc = ""
        self.tags = []
        self.tags_online = False
        self.icon_path = None
        self.scanned = False

    def apply(self, meta: dict):
        for k, v in meta.items():
            if k in Entry.__slots__ or k in ("status", "name", "path", "type", "modid",
                                              "version", "size", "cn", "disp", "desc",
                                              "tags", "tags_online", "icon_path"):
                setattr(self, k, v)
        self.scanned = True

    # ---- 卡片显示用的派生字段 ----
    @property
    def title(self) -> str:
        return (self.cn or "").strip() or (self.disp or "").strip() or self.name

    @property
    def subtitle(self) -> str:
        cn = (self.cn or "").strip()
        disp = (self.disp or "").strip()
        if cn and disp and disp != cn:
            return disp
        if cn and not disp and self.name != cn:
            return self.name
        return ""


class Store(QtCore.QObject):
    """清单数据 + 当前显示顺序 + 选中集合。"""
    reset = QtCore.Signal()             # 顺序/搜索/排序变化：整表重载
    row_data = QtCore.Signal(int)       # 显示行号，某行数据变了

    def __init__(self, entries, is_mod, source_path, online_tags, parent=None):
        super().__init__(parent)
        self.is_mod = is_mod
        self.source_path = source_path or ""
        self.online_tags = online_tags
        self.items = [Entry(e) for e in entries]
        self.order = list(range(len(self.items)))
        self._pos = {id(it): i for i, it in enumerate(self.items)}
        self.query = ""
        self.sort_col = None
        self.sort_rev = False
        sp = Path(self.source_path) if self.source_path else None
        self.mods_dir = (sp / "mods") if (sp and is_mod) else None
        self.config_dir = (sp / "config") if (sp and not is_mod) else None
        self._pool = QtCore.QThreadPool()
        # 6 个并发在 SSD 上换不来多少速度，却让界面线程在 GIL 上排到第 7 位；
        # 3 个线程总耗时差不多（扫描大部分时间在等磁盘），界面明显更跟手
        self._pool.setMaxThreadCount(3)
        self._signals = _ScanSignals()
        self._signals.done.connect(self._on_scanned)
        self._pending = set()
        # 扫描结果先攒着，由窗口按「可见行」节流刷新（见 BigView._flush_rows）
        self._dirty = set()
        self._gil_lent = False          # 是否已经把 GIL 切换间隔调细（配对 end_bulk_scan）

    # ---- 查询 ----
    def ctx(self) -> dict:
        return {"is_mod": self.is_mod, "mods_dir": self.mods_dir,
                "config_dir": self.config_dir, "online_tags": self.online_tags,
                "source_path": self.source_path}

    def at(self, row: int) -> Entry:
        return self.items[self.order[row]]

    def row_of(self, item) -> int:
        """item 在当前显示顺序里的行号（-1 = 被搜索/过滤隐藏了）。
        O(1)：扫描结果是一条一条回来的，用 list.index 的话三千条模组就是平方级。"""
        return self._pos.get(id(item), -1)

    # ---- 顺序 / 排序 / 搜索 ----
    def rebuild(self):
        q = self.query.strip().lower()
        idxs = list(range(len(self.items)))
        if q:
            idxs = [i for i in idxs
                    if q in self.items[i].entry.lower() or q in self.items[i].name.lower()]
        if self.sort_col is not None:
            idxs.sort(key=self._sort_key(self.sort_col), reverse=self.sort_rev)
        self.order = idxs
        self._pos = {id(self.items[i]): r for r, i in enumerate(idxs)}
        self._dirty.clear()             # reset 会把整个模型刷一遍，攒着的就没用了
        self.reset.emit()

    def _sort_key(self, col):
        def key(i):
            it = self.items[i]
            if col == "status":
                return {"✅ 存在": 0, "❌ 缺失": 1}.get(it.status, 2)
            if col == "size":
                try:
                    return float(it.size)
                except (TypeError, ValueError):
                    return -1.0
            v = getattr(it, col, "") or ""
            return str(v).lower()
        return key

    def set_sort(self, col, rev):
        self.sort_col, self.sort_rev = col, rev
        self.rebuild()

    def set_query(self, text):
        self.query = text or ""     # 只改显示范围，选中态一律保留
        self.rebuild()

    # ---- 扫描 ----
    def _start_scan(self, item):
        """起一条扫描任务，并保证"有一批在扫"期间 GIL 是让出来的。"""
        self._pending.add(id(item))
        if not self._gil_lent:
            self._gil_lent = True
            begin_bulk_scan()
        self._pool.start(_ScanTask(item, self.ctx(), self._signals))

    def scan_all(self):
        for it in self.items:
            it.scanned = False
        self._pending = {id(it) for it in self.items}
        self._dirty.clear()
        if not self.items:
            return
        if not self._gil_lent:
            self._gil_lent = True
            begin_bulk_scan()
        for it in self.items:
            self._pool.start(_ScanTask(it, self.ctx(), self._signals))

    def _on_scanned(self, item, meta):
        item.apply(meta)
        self._pending.discard(id(item))
        if not self._pending and self._gil_lent:
            self._gil_lent = False
            end_bulk_scan()
        # 这里**不**直接发 dataChanged：一千条就是一千次重绘请求，扫描还没跑完时
        # 这些请求全叠在滚动/搜索上，用户感觉就是"检测没完之前窗口特别卡"。
        # 先按 id 攒着（行号会被排序/搜索改掉），由窗口每 200ms 只刷看得见的那几行。
        self._dirty.add(id(item))

    def take_dirty(self) -> set:
        """取走这轮攒下的"数据变了"的条目 id（取完即清空）。"""
        out = self._dirty
        self._dirty = set()
        return out

    @property
    def scanning(self) -> bool:
        return bool(self._pending)

    # ---- 选中 ----
    def toggle(self, row: int) -> bool:
        if not (0 <= row < len(self.order)):
            return False
        it = self.items[self.order[row]]
        self.set_checked(row, not it.checked)
        return it.checked

    def set_checked(self, row: int, value: bool):
        """直接设定选中态（同文件名条目共享，与 Tk 版一致）。"""
        if not (0 <= row < len(self.order)):
            return
        it = self.items[self.order[row]]
        it.checked = bool(value)
        for other in self.items:
            if other is not it and other.key == it.key:
                other.checked = it.checked

    def checked_all(self, on: bool, visible_only: bool = False):
        rng = self.order if visible_only else range(len(self.items))
        for i in rng:
            self.items[i].checked = on

    def invert(self, visible_only: bool = True):
        rng = self.order if visible_only else range(len(self.items))
        for i in rng:
            self.items[i].checked = not self.items[i].checked

    def selected_items(self) -> list:
        return [it for it in self.items if it.checked]

    def counts(self) -> tuple:
        total = len(self.items)
        sel = sum(1 for it in self.items if it.checked)
        ok = sum(1 for it in self.items if it.status == "✅ 存在")
        miss = sum(1 for it in self.items if it.status == "❌ 缺失")
        return total, len(self.order), sel, ok, miss

    # ---- 增删 ----
    def remove_selected(self) -> int:
        keep = [it for it in self.items if not it.checked]
        n = len(self.items) - len(keep)
        self.items = keep
        self.entries_changed()
        return n

    def remove_one(self, row: int) -> bool:
        if not (0 <= row < len(self.order)):
            return False
        self.items.pop(self.order[row])
        self.entries_changed()
        return True

    def add_entries(self, paths) -> int:
        have = {it.entry for it in self.items}
        new = [p for p in paths if p not in have]
        if not new:
            return 0
        self.items.extend(Entry(p, is_new=True) for p in new)
        self.entries_changed()
        for it in self.items[-len(new):]:
            self._start_scan(it)
        return len(new)

    def entries_changed(self):
        self.rebuild()

    def entry_texts(self) -> list:
        return [it.entry for it in self.items]


# --------------------------------------------------------------------------- #
# 控件：带动画的按钮
# --------------------------------------------------------------------------- #
def _style_view_palette(view, th: dict):
    """给表格/列表配色走调色板。

    **绝不能给视图设 QSS**：实测 `QTableView` 一旦有样式表，Qt 就用不了"位图滚动"
    （blit 已画好的视口、只补新露出的那条）快路径，滚一步整块重绘 —— 26px 一步从
    5.2ms 变成 35.4ms（6.8 倍），滚轮动画直接被拖到 ~20fps。表头自己的 QSS 是安全的。
    """
    pal = view.palette()
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor(th.get("bg", "#ffffff")))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor(th.get("fg", "#000000")))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(th.get("card_sel_bg", "#d4e6f8")))
    pal.setColor(QtGui.QPalette.HighlightedText,
                 QtGui.QColor(th.get("card_sel_fg", "#0d3d63")))
    view.setPalette(pal)


def _header_qss(th: dict) -> str:
    return ("QHeaderView::section{background:%s;color:%s;border:none;"
            "border-right:1px solid %s;padding:4px 6px;}"
            "QHeaderView::section:hover{background:%s;}"
            % (th.get("ttk_bg", "#eaeaea"), th.get("fg"), th.get("muted_fg"),
               th.get("hover_bg")))


class AnimButton(QtWidgets.QPushButton):
    """自绘渐变按钮：悬停会浮起来 + 扫过一道光，按下会沉下去、松手弹回来。

    QSS 的 `:hover` 是瞬变、没有惯性，这里用三个 QVariantAnimation 驱动自绘：
    - _a  悬停进度：190ms OutBack（末端带过冲，就是"灵动"的来源）
    - _sweep 扫光：进悬停时从左到右扫一次（320ms），只在自绘里画一条渐变亮带
    - _press 按下：80ms 沉下去，松开 200ms OutBack 弹回来
    全程只重绘这一个按钮（30×~90 px），实测单帧 0.1ms 级。
    """

    def __init__(self, text, color1, color2, theme, text_color="#ffffff", parent=None):
        super().__init__(text, parent)
        self.c1, self.c2 = color1, color2
        self.fg = text_color
        self.theme = theme
        self._t = 0.0        # 悬停进度（可能 >1，OutBack 的过冲）
        self._sweep = -1.0   # 扫光位置，-1 = 不画
        self._press = 0.0    # 按下进度
        self.setFixedHeight(30)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
        self._a = QtCore.QVariantAnimation(self)
        self._a.setDuration(190)
        self._a.setEasingCurve(QtCore.QEasingCurve.OutBack)
        self._a.valueChanged.connect(self._on_val)
        self._sw = QtCore.QVariantAnimation(self)
        self._sw.setDuration(340)
        self._sw.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._sw.valueChanged.connect(self._on_sweep)
        self._sw.finished.connect(self._sweep_done)
        self._pa = QtCore.QVariantAnimation(self)
        self._pa.setDuration(90)
        self._pa.valueChanged.connect(self._on_press)

    # ---- 动画进度 ----
    def _on_val(self, v):
        self._t = float(v)
        self.update()

    def _on_sweep(self, v):
        self._sweep = float(v)
        self.update()

    def _sweep_done(self):
        self._sweep = -1.0
        self.update()

    def _on_press(self, v):
        self._press = float(v)
        self.update()

    def _animate(self, anim, target, duration=None, curve=None):
        if duration is not None:
            anim.setDuration(duration)
        if curve is not None:
            anim.setEasingCurve(curve)
        cur = anim.currentValue()
        anim.stop()
        anim.setStartValue(0.0 if cur is None else float(cur))
        anim.setEndValue(float(target))
        anim.start()

    def enterEvent(self, ev):
        if self.isEnabled():
            self._animate(self._a, 1.0)
            self._animate(self._sw, 1.0)          # 扫光每次进入都重来一遍
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._animate(self._a, 0.0, 150, QtCore.QEasingCurve.OutCubic)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        self._animate(self._pa, 1.0)
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._animate(self._pa, 0.0, 200, QtCore.QEasingCurve.OutBack)
        super().mouseReleaseEvent(ev)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        t = self._t
        press = self._press
        # 悬停浮起 1.6px、按下沉 1.4px（按下时浮起量收掉一半，像真的被按下去）
        lift = -1.6 * min(1.0, max(0.0, t)) * (1.0 - press * 0.5) + 1.4 * press
        r = QtCore.QRectF(1.5, 2.0 + lift, self.width() - 3, self.height() - 4)
        if not self.isEnabled():
            c1, c2, fg = "#9e9e9e", "#bdbdbd", "#f0f0f0"
        else:
            over = min(1.18, max(0.0, t))          # OutBack 过冲削顶
            c1 = _mix(self.c1, _mix(self.c1, "#ffffff", 0.30), over)
            c2 = _mix(self.c2, _mix(self.c2, "#ffffff", 0.30), over)
            fg = self.fg
        g = QtGui.QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QtGui.QColor(c1))
        g.setColorAt(1.0, QtGui.QColor(c2))
        p.setPen(QtCore.Qt.NoPen)
        # 阴影：悬停变大变深、按下收窄（浮起来/按下去的空间感全靠它）
        if self.isEnabled():
            for dy, a in ((3.6, 20), (2.4, 30), (1.2, 42)):
                k = (0.4 + 0.6 * min(1.0, max(0.0, t))) * (1.0 - 0.55 * press)
                p.setBrush(_qcolor("#000000", int(a * k)))
                p.drawRoundedRect(r.adjusted(-0.6, dy, 0.6, dy), 8, 8)
        p.setBrush(QtGui.QBrush(g))
        p.drawRoundedRect(r, 8, 8)
        # 扫光：一条斜向亮带扫过按钮面
        if self.isEnabled() and self._sweep >= 0.0:
            path = QtGui.QPainterPath()
            path.addRoundedRect(r, 8, 8)
            p.setClipPath(path)
            x = r.left() - r.width() * 1.1 + self._sweep * (r.width() * 2.2)
            band = QtGui.QLinearGradient(x, r.top(), x + r.width() * 0.55, r.bottom())
            band.setColorAt(0.0, _qcolor("#ffffff", 0))
            band.setColorAt(0.5, _qcolor("#ffffff", 44))
            band.setColorAt(1.0, _qcolor("#ffffff", 0))
            p.setBrush(QtGui.QBrush(band))
            p.drawRect(r)
            p.setClipping(False)
        p.setPen(QtGui.QColor(fg))
        p.setFont(self.font())
        p.drawText(r, QtCore.Qt.AlignCenter, self.text())
        p.end()


# --------------------------------------------------------------------------- #
# 表格视图
# --------------------------------------------------------------------------- #
_COLS = (("status", "🔵 状态", 88), ("name", "📄 文件名", 220), ("path", "📁 完整路径", 320),
         ("type", "🧩 类型", 92), ("modid", "🆔 Mod ID", 150), ("version", "🔖 版本", 110),
         ("size", "💾 大小KB", 84))
_COLS_CFG = (("status", "🔵 状态", 88), ("name", "📄 名称", 220),
             ("path", "📁 相对路径/完整路径", 380), ("type", "🏷️ 类型", 100))


class TableModel(QtCore.QAbstractTableModel):
    def __init__(self, store: Store, theme: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.theme = theme
        self.cols = _COLS if store.is_mod else _COLS_CFG
        self.hover_row = -1
        # 行底色/前景色按"状态组合"缓存：一帧要画 100 多个单元格，每个都现算
        # _mix() + QColor() 的话光这一项就 1ms 起（滚动时每帧都算）。组合只有几十种。
        self._bg_cache = {}
        self._fg_cache = {}
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

    def _status_pixmap(self, status):
        cache = getattr(self, "_status_cache", None)
        if cache is None:
            cache = self._status_cache = {}
        if status in cache:
            return cache[status]
        color = {"✅ 存在": self.theme.get("ok_fg", "#2e7d32"),
                 "❌ 缺失": self.theme.get("danger_fg", "#8b0000")}.get(
                     status, self.theme.get("muted_fg", "#808080"))
        pm = QtGui.QPixmap(12, 12)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(color))
        p.drawEllipse(1, 1, 10, 10)
        p.end()
        cache[status] = pm
        return pm

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        row, cname = index.row(), self.cols[index.column()][0]
        it = self.store.at(row)
        if role == ROLE_ITEM:
            return it
        if role == QtCore.Qt.DisplayRole:
            if cname == "status":
                return {"✅ 存在": "存在", "❌ 缺失": "缺失"}.get(it.status, "检测中")
            if cname == "name":
                return it.name
            if cname == "path":
                return it.path
            if cname == "type":
                v = it.type if it.type and it.type != "?" else "…"
                if v in ("文件夹", "文件"):
                    v = ("📁 " if v == "文件夹" else "📄 ") + v
                elif v != "…":
                    v = "🧩 " + v
                return v
            v = getattr(it, cname, "")
            return "…" if v in (None, "") else str(v)
        if role == QtCore.Qt.DecorationRole and cname == "status":
            return self._status_pixmap(it.status)
        if role == QtCore.Qt.TextAlignmentRole and cname == "size":
            return int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if role == QtCore.Qt.ToolTipRole:
            return "%s\n%s" % (it.title, it.path)
        if role == QtCore.Qt.BackgroundRole:
            key = (bool(it.checked), it.status, bool(it.is_new), row == self.hover_row)
            c = self._bg_cache.get(key)
            if c is None:
                c = self._make_bg(it, row == self.hover_row)
                self._bg_cache[key] = c
            return c
        if role == QtCore.Qt.ForegroundRole and it.checked:
            c = self._fg_cache.get("sel")
            if c is None:
                c = self._fg_cache["sel"] = QtGui.QColor(
                    self.theme.get("card_sel_fg", "#0d3d63"))
            return c
        return None

    def _make_bg(self, it, hovered: bool):
        th = self.theme
        if it.checked:
            return QtGui.QColor(_mix(th.get("bg", "#ffffff"),
                                     th.get("card_sel_bg", "#d4e6f8"), 0.85))
        if it.status == "❌ 缺失":
            return QtGui.QColor(th.get("hover_missing_bg", "#ffe0e0"))
        if it.is_new:
            return QtGui.QColor(_mix(th.get("bg", "#ffffff"),
                                     th.get("hover_new_bg", "#fff6c9"), 0.9))
        if hovered:
            return QtGui.QColor(th.get("hover_bg", "#eef3f8"))
        return None


class _SmoothWheel:
    """滚轮缓动混入：由泵每帧推进一步，和 Tk 版 helpers.SmoothScroller 同一套手感。

    Qt 的 `ScrollPerPixel` 只是"滚动单位是像素"，滚轮本身还是一次跳到位。

    **为什么不用 QTimer**：本窗口的 Qt 事件是靠 Tk 的 `after` 泵推的（实测泵 62.7 次/秒、
    间隔 16.16ms）。如果动画自己跑 12ms 的 QTimer，就比泵还快 —— 两次 tick 之间才画一帧，
    帧被吞掉、相位还会漂，看着就是掉帧。改成 `pump()` 每进来一次推进一步，一个泵帧
    正好一帧画面；缓动系数按**真实经过时间**换算（`1-(1-0.3)^(dt/12ms)`），所以泵快泵慢
    滚动速度都一样，只是帧数不同。
    """
    WHEEL_FRAME_S = 0.012      # Tk 版的一帧 = 12ms（"每帧走 30%"就是这个节奏）
    WHEEL_EASE = 0.30
    WHEEL_SNAP_PX = 0.6        # 离目标不足这么多像素就吸附收工

    def wheel_px(self) -> float:
        """一格滚轮滚多少像素（子类覆盖）。"""
        return 78.0

    # ---- 状态（懒初始化，免得跟 Shiboken 的多继承 MRO 打架）----
    def _sw_setup(self):
        self._sw_active = False
        self._sw_cur = float(self.verticalScrollBar().value())
        self._sw_target = self._sw_cur
        self._sw_last = time.perf_counter()
        bar = self.verticalScrollBar()
        bar.sliderPressed.connect(self._sw_cancel)
        bar.actionTriggered.connect(lambda *_: self._sw_cancel())

    def _sw_cancel(self, *_a):
        """用户自己拖了滚动条/翻页：动画让位，重新对齐位置。"""
        self._sw_active = False
        self._sw_cur = float(self.verticalScrollBar().value())
        self._sw_target = self._sw_cur

    def tick_scroll(self, now=None) -> bool:
        """泵每帧调一次，推进一步。返回是否还在动（用于省 CPU）。"""
        if not getattr(self, "_sw_active", False):
            return False
        now = time.perf_counter() if now is None else now
        dt = min(0.10, max(0.0, now - self._sw_last))     # 卡顿再久也只按 100ms 算
        self._sw_last = now
        bar = self.verticalScrollBar()
        left = self._sw_target - self._sw_cur
        if abs(left) < self.WHEEL_SNAP_PX or dt <= 0.0:
            self._sw_cur = self._sw_target
            bar.setValue(int(round(self._sw_cur)))
            self._sw_active = False
            return False
        k = 1.0 - (1.0 - self.WHEEL_EASE) ** (dt / self.WHEEL_FRAME_S)
        self._sw_cur += left * k
        bar.setValue(int(round(self._sw_cur)))
        return True

    def wheelEvent(self, ev):
        if not hasattr(self, "_sw_active"):
            self._sw_setup()
        bar = self.verticalScrollBar()
        pix = ev.pixelDelta()
        ang = ev.angleDelta()
        if not pix.isNull() and pix.y():          # 触控板：本身就是连续小步，直接跟手
            delta = float(pix.y())
            self._sw_cancel()
            self._sw_target = max(float(bar.minimum()),
                                  min(float(bar.maximum()), bar.value() + delta))
            bar.setValue(int(round(self._sw_target)))
            self._sw_cur = self._sw_target
            ev.accept()
            return
        if not ang.y():
            ev.ignore()
            return
        notches = ang.y() / 120.0                 # 一格 = 120
        delta = -notches * self.wheel_px()        # 滚轮向上 = 内容往上 = value 变小
        if not self._sw_active:
            self._sw_cur = float(bar.value())
            self._sw_target = self._sw_cur
            self._sw_last = time.perf_counter()
        self._sw_target = max(float(bar.minimum()),
                              min(float(bar.maximum()), self._sw_target + delta))
        self._sw_active = True
        ev.accept()


class SmoothTable(_SmoothWheel, QtWidgets.QTableView):
    def wheel_px(self) -> float:
        try:
            return max(18, self.verticalHeader().defaultSectionSize()) * 3.0
        except Exception:
            return 78.0


class SmoothCards(_SmoothWheel, QtWidgets.QListView):
    def wheel_px(self) -> float:
        return float(CARD_H)      # 一格滚一张卡（Tk 版卡片列表也是这么定的）


class CardModel(QtCore.QAbstractListModel):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        store.reset.connect(self._on_reset)
        store.row_data.connect(self._on_row)

    def _on_reset(self):
        self.beginResetModel()
        self.endResetModel()

    def _on_row(self, row):
        if 0 <= row < len(self.store.order):
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx)

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.store.order)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if index.isValid() and role == ROLE_ITEM:
            return self.store.at(index.row())
        return None


class TableDelegate(QtWidgets.QStyledItemDelegate):
    """表格视图的默认绘制 + 一点"进场/退场"效果。

    表格本来用的是 Qt 默认委托（直接画文本），要动透明度就得自己包一层：
    pop_t < 1 时整行半透明、并从左边 12px 滑到位。**不动其它任何绘制逻辑**，
    pop_t 到 1 就直接交给父类，零额外开销。
    """

    def paint(self, painter, option, index):
        it = index.data(ROLE_ITEM)
        pop = float(getattr(it, "pop_t", 1.0) or 1.0) if it is not None else 1.0
        if pop >= 0.999:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setOpacity(0.10 + 0.90 * pop)
        painter.translate(-(1.0 - pop) * 12, 0)
        super().paint(painter, option, index)
        painter.restore()


class CardDelegate(QtWidgets.QStyledItemDelegate):
    """自绘卡片：圆角 + 伪阴影 + 选中蓝底 + 左侧高亮条 + 悬停操作图标。"""
    ACTIONS = (("info", "ℹ"), ("reveal", "📂"), ("remove", "🗑"))
    ACTION_W = 26

    def __init__(self, store, theme, parent=None):
        super().__init__(parent)
        self.store = store
        self.theme = theme
        # 操作图标：config 清单不是模组，没有"模组详情"这回事，所以那个图标不出现
        self.actions = (CardDelegate.ACTIONS if store.is_mod
                        else tuple(a for a in CardDelegate.ACTIONS if a[0] != "info"))
        self.hover_row = -1
        self.hover_action = None
        self._pix = {}
        self._f_title = QtGui.QFont("Microsoft YaHei UI", 10)
        self._f_title.setBold(True)
        self._f_small = QtGui.QFont("Microsoft YaHei UI", 8)
        self._f_tiny = QtGui.QFont("Microsoft YaHei UI", 8)
        self._f_icon = QtGui.QFont("Microsoft YaHei UI", 10)

    def sizeHint(self, option, index):
        return QtCore.QSize(200, CARD_H)

    def action_rect(self, rect, i) -> QtCore.QRect:
        """第 i 个操作图标的矩形（从右往左排）。"""
        x = rect.right() - 8 - (len(self.actions) - i) * self.ACTION_W
        return QtCore.QRect(x, rect.center().y() - 12, self.ACTION_W, 24)

    def _icon(self, it):
        key = it.icon_path
        if not key:
            return None
        if key in self._pix:
            return self._pix[key]
        pm = QtGui.QPixmap(key)
        if pm.isNull():
            self._pix[key] = None
            return None
        pm = pm.scaled(ICON_PX, ICON_PX, QtCore.Qt.KeepAspectRatio,
                       QtCore.Qt.SmoothTransformation)
        if len(self._pix) > 200:
            self._pix.clear()
        self._pix[key] = pm
        return pm

    def _fallback_icon(self):
        """没图标时画的兜底方块：模组是 M，config 清单是 C。

        （config 里那些是配置文件/文件夹，"M" 会让人以为是模组）
        """
        letter = "M" if self.store.is_mod else "C"
        key = ("fb", letter)
        hit = self._pix.get(key)
        if hit is not None:
            return hit
        pm = QtGui.QPixmap(ICON_PX, ICON_PX)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(self.theme.get("muted_fg", "#7a7a7a")))
        p.drawRoundedRect(0, 0, ICON_PX, ICON_PX, 8, 8)
        p.setPen(QtGui.QColor("#ffffff"))
        f = QtGui.QFont("Microsoft YaHei UI", 14)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), QtCore.Qt.AlignCenter, letter)
        p.end()
        # 和图标的缓存放一起（换主题时 _pix.clear() 会把兜底图一起丢掉重画）
        self._pix[key] = pm
        self._fb = pm
        return pm

    def paint(self, painter, option, index):
        it = index.data(ROLE_ITEM)
        if it is None:
            return
        th = self.theme
        row = index.row()
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(option.rect).adjusted(CARD_MARGIN, CARD_MARGIN // 2 + 1,
                                                   -CARD_MARGIN, -CARD_MARGIN // 2 - 1)
        sel = float(it.sel_t or 0.0)
        # 进场/退场：半透明 + 从左边滑进来（pop_t 到 1 时这两步都是零成本）
        pop = float(getattr(it, "pop_t", 1.0) or 1.0)
        if pop < 0.999:
            painter.setOpacity(0.10 + 0.90 * pop)
            painter.translate(-(1.0 - pop) * 16, 0)
        hovered = (row == self.hover_row)
        card_bg = th.get("entry_bg", "#f7f7f7")
        fill = card_bg
        if hovered:
            fill = _mix(card_bg, th.get("hover_bg", "#eef3f8"), 0.75)
        if sel > 0.0:
            fill = _mix(fill, th.get("card_sel_bg", "#d4e6f8"), sel)
        # 伪阴影：3 层递减 alpha，比 QGraphicsDropShadowEffect 便宜得多（每帧少一次模糊）
        if hovered or sel > 0.0:
            painter.setPen(QtCore.Qt.NoPen)
            for i, (dy, a) in enumerate(((3, 26), (2, 34), (1, 44))):
                painter.setBrush(_qcolor("#000000", int(a * max(sel, 0.45 if hovered else 0))))
                painter.drawRoundedRect(rect.adjusted(-1 + i, dy, 1 - i, dy), RADIUS, RADIUS)
        painter.setPen(QtGui.QPen(QtGui.QColor(_mix(fill, "#000000", 0.10)), 1))
        painter.setBrush(QtGui.QColor(fill))
        painter.drawRoundedRect(rect, RADIUS, RADIUS)
        # 左侧高亮条：选中时"长出来"
        if sel > 0.01:
            bar = QtCore.QRectF(rect.left() + 1, rect.top() + 5 + (1 - sel) * 8, 3.5,
                                (rect.height() - 10) * sel)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(th.get("card_sel_bar", "#2f7fd1")))
            painter.drawRoundedRect(bar, 1.7, 1.7)

        fg = QtGui.QColor(th.get("card_sel_fg" if sel > 0.5 else "fg", "#222222"))
        muted = QtGui.QColor(th.get("card_sel_fg" if sel > 0.5 else "muted_fg", "#777777"))

        # ---- 状态圆点 + 图标 ----
        dot_c = {"✅ 存在": th.get("ok_fg", "#2e7d32"),
                 "❌ 缺失": th.get("danger_fg", "#8b0000")}.get(it.status,
                                                               th.get("muted_fg", "#999999"))
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(dot_c))
        painter.drawEllipse(QtCore.QPointF(rect.left() + 11, rect.top() + 11), 3.5, 3.5)

        ix = rect.left() + 14
        iy = rect.center().y() - ICON_PX / 2
        pm = self._icon(it) or self._fallback_icon()
        painter.drawPixmap(QtCore.QRectF(ix, iy, ICON_PX, ICON_PX), pm,
                           QtCore.QRectF(pm.rect()))
        tx = ix + ICON_PX + 12
        right_reserved = 8 + len(self.actions) * self.ACTION_W + 8
        tw = rect.right() - tx - right_reserved

        # ---- 标题 / 副标题 ----
        painter.setFont(self._f_title)
        painter.setPen(fg)
        title = self._elide(painter, it.title, tw)
        painter.drawText(QtCore.QRectF(tx, rect.top() + 8, tw, 20),
                         int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter), title)
        painter.setFont(self._f_small)
        painter.setPen(muted)
        sub_parts = [p for p in (it.subtitle, it.name if it.subtitle != it.name else "") if p]
        sub = " · ".join(dict.fromkeys(sub_parts))
        if it.version and it.version != "?":
            sub = (sub + "  " if sub else "") + "v" + str(it.version)
        painter.drawText(QtCore.QRectF(tx, rect.top() + 27, tw, 16),
                         int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                         self._elide(painter, sub, tw))

        # ---- 标签 chip ----
        chip_x = tx
        chip_y = rect.top() + 48
        painter.setFont(self._f_tiny)
        fm = painter.fontMetrics()
        for tag in list(it.tags or [])[:5]:
            label = str(tag)
            w = fm.horizontalAdvance(label) + 12
            if chip_x + w > tx + tw:
                break
            bgc, fgc = _tag_color(label)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(bgc))
            painter.drawRoundedRect(QtCore.QRectF(chip_x, chip_y, w, 15), 7.5, 7.5)
            painter.setPen(QtGui.QColor(fgc))
            painter.drawText(QtCore.QRectF(chip_x, chip_y, w, 15),
                             int(QtCore.Qt.AlignCenter), label)
            chip_x += w + 4

        # ---- 描述 ----
        if it.desc:
            painter.setPen(muted)
            painter.drawText(QtCore.QRectF(tx, rect.top() + 68, tw, 16),
                             int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                             self._elide(painter, it.desc, tw))

        # ---- 悬停操作图标 ----
        if hovered:
            painter.setFont(self._f_icon)
            for i, (name, glyph) in enumerate(self.actions):
                r = self.action_rect(option.rect, i)
                active = (name == self.hover_action)
                if active:
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(QtGui.QColor(_mix(fill, "#000000", 0.12)))
                    painter.drawRoundedRect(QtCore.QRectF(r), 6, 6)
                painter.setPen(fg if active else muted)
                painter.drawText(QtCore.QRectF(r), int(QtCore.Qt.AlignCenter), glyph)
        painter.restore()

    @staticmethod
    def _elide(painter, text, width) -> str:
        return painter.fontMetrics().elidedText(str(text), QtCore.Qt.ElideRight,
                                               max(10, int(width)))


# --------------------------------------------------------------------------- #
# 详情窗口（Qt 版）
# --------------------------------------------------------------------------- #
class DetailDialog(QtWidgets.QDialog):
    def __init__(self, it: Entry, theme: dict, on_reveal, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("模组详情")
        self.setMinimumSize(560, 380)
        self.setStyleSheet("QDialog{background:%s;} QLabel{color:%s;}"
                           % (theme.get("bg"), theme.get("fg")))
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        head = QtWidgets.QHBoxLayout()
        pm = QtGui.QPixmap(it.icon_path) if it.icon_path else QtGui.QPixmap()
        icon = QtWidgets.QLabel()
        icon.setFixedSize(56, 56)
        if not pm.isNull():
            icon.setPixmap(pm.scaled(56, 56, QtCore.Qt.KeepAspectRatio,
                                     QtCore.Qt.SmoothTransformation))
        head.addWidget(icon)
        box = QtWidgets.QVBoxLayout()
        t = QtWidgets.QLabel(it.title)
        f = t.font()
        f.setPointSize(13)
        f.setBold(True)
        t.setFont(f)
        box.addWidget(t)
        sub = QtWidgets.QLabel(it.name)
        sub.setStyleSheet("color:%s" % theme.get("muted_fg"))
        box.addWidget(sub)
        head.addLayout(box, 1)
        lay.addLayout(head)
        info = QtWidgets.QLabel(
            "Mod ID：%s\n版本：%s\n类型：%s\n大小：%s KB\n状态：%s\n路径：%s"
            % (it.modid, it.version, it.type, it.size, it.status, it.path))
        info.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        info.setWordWrap(True)
        lay.addWidget(info)
        if it.tags:
            tag_row = QtWidgets.QHBoxLayout()
            tag_row.addWidget(QtWidgets.QLabel("分类："))
            for tag in it.tags[:8]:
                lb = QtWidgets.QLabel(str(tag))
                bgc, fgc = _tag_color(str(tag))
                lb.setStyleSheet("background:%s;color:%s;border-radius:7px;padding:1px 8px;"
                                 % (bgc, fgc))
                tag_row.addWidget(lb)
            tag_row.addStretch(1)
            lay.addLayout(tag_row)
        desc = QtWidgets.QTextEdit()
        desc.setReadOnly(True)
        desc.setPlainText(it.desc or "（该模组未提供描述）")
        desc.setStyleSheet("QTextEdit{background:%s;color:%s;border:1px solid %s;"
                           "border-radius:8px;padding:6px;}"
                           % (theme.get("entry_bg"), theme.get("fg"),
                              theme.get("muted_fg")))
        lay.addWidget(desc, 1)
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        reveal = QtWidgets.QPushButton("📂 打开所在位置")
        reveal.clicked.connect(lambda: on_reveal(it.path))
        close = QtWidgets.QPushButton("关闭")
        close.clicked.connect(self.accept)
        btns.addWidget(reveal)
        btns.addWidget(close)
        lay.addLayout(btns)

    def showEvent(self, ev):
        """详情窗也上原生深色标题栏（和 Tk 版详情窗一致）。"""
        super().showEvent(ev)
        if not getattr(self, "_native_styled", False):
            self._native_styled = True
            style_window_hwnd(int(self.winId()), _is_dark(self.theme))


# --------------------------------------------------------------------------- #
# 联网搜索（Modrinth）—— 和 Tk 版详情窗口同一套打分/置顶逻辑
# --------------------------------------------------------------------------- #
def _norm(s) -> str:
    """归一化：小写、去括号内容、非字母数字合并为空格，便于相似度比较。"""
    s = (str(s) or "").lower().strip()
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def match_score(r: dict, modid: str, name: str) -> int:
    """给小候选打分：slug 与本地 modid 完全相同 100 分，名称相同 90 分，其余按文本相似度。"""
    mid, nm = _norm(modid), _norm(name)
    slug, title = _norm(r.get("slug")), _norm(r.get("title"))
    if mid and slug == mid:
        return 100
    if nm and slug == nm:
        return 90

    def ratio(a, b):
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a, b).ratio()

    best = 0.0
    if mid:
        best = max(best, ratio(slug, mid), ratio(title, mid))
    if nm:
        best = max(best, ratio(slug, nm), ratio(title, nm))
    return int(best * 70)


def guess_query(it: Entry) -> str:
    """给一条清单条目猜联网搜索词：优先模组自己的名字 / Mod ID，其次文件名去掉版本号。"""
    for cand in (getattr(it, "disp", ""), getattr(it, "modid", "")):
        cand = str(cand or "").strip()
        if cand and cand != "?":
            return cand
    stem = Path(it.name).stem
    base = re.sub(r"[-_ ]?v?\d[\w.\-+]*$", "", stem).strip("-_ ")   # 粗略剥掉版本尾巴
    return base or stem


class _SearchSignals(QtCore.QObject):
    results = QtCore.Signal(object, str, str)      # 结果列表, modid, 名称（用于打分）
    error = QtCore.Signal(str)
    version = QtCore.Signal(int, str, str)         # 行号, 最新版本, 下载链接


class _SearchTask(QtCore.QRunnable):
    def __init__(self, query: str, limit: int, signals: _SearchSignals, modid="", name=""):
        super().__init__()
        self.query, self.limit = query, limit
        self.signals = signals
        self.modid, self.name = modid, name
        self.setAutoDelete(True)

    def run(self):                                  # 工作线程：只做网络，不碰界面
        try:
            res = search_modrinth(self.query, limit=self.limit)
        except Exception as e:
            self.signals.error.emit(str(e))
            return
        try:
            self.signals.results.emit(res, self.modid, self.name)
        except RuntimeError:
            pass


class _VersionTask(QtCore.QRunnable):
    def __init__(self, row: int, project_id: str, signals: _SearchSignals):
        super().__init__()
        self.row, self.pid, self.signals = row, project_id, signals
        self.setAutoDelete(True)

    def run(self):
        try:
            d = fetch_project_latest(self.pid)
        except Exception:
            d = {"latest_version": "", "download_url": ""}
        try:
            self.signals.version.emit(self.row, d.get("latest_version", ""),
                                      d.get("download_url", ""))
        except RuntimeError:
            pass


class SearchModel(QtCore.QAbstractTableModel):
    """搜索结果表：0 列是名称，最相似项置顶并标绿；本地版本不一致的标黄（可更新）。"""
    COLS = (("name", "📄 名称", 240), ("author", "👤 作者", 110),
            ("downloads", "⬇️ 下载量", 90), ("version", "🔖 最新版本", 160),
            ("slug", "🆔 项目ID", 120))

    def __init__(self, theme: dict, local_version: str = "", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.local_version = local_version
        self.rows = []          # dict：原始结果 + score/is_match/updatable/latest_version

    def set_results(self, results, modid: str, name: str):
        self.beginResetModel()
        scored = [(match_score(r, modid, name), i, r) for i, r in enumerate(results)]
        scored.sort(key=lambda t: (-t[0], t[1]))
        self.rows = []
        best = scored[0][0] if scored else 0
        for pos, (score, _i, r) in enumerate(scored):
            r = dict(r)
            r["score"] = score
            r["is_match"] = (pos == 0 and best >= 20)     # 阈值和 Tk 版一致
            self.rows.append(r)
        self.endResetModel()

    def apply_version(self, row: int, vnum: str, url: str):
        if not (0 <= row < len(self.rows)):
            return
        r = self.rows[row]
        r["latest_version"], r["download_url"] = vnum, url
        r["updatable"] = bool(vnum) and bool(self.local_version) and \
            vnum != self.local_version
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, self.index(row, self.columnCount() - 1))

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.COLS)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return self.COLS[section][1]
        return None

    def item(self, row: int) -> dict:
        return self.rows[row] if 0 <= row < len(self.rows) else {}

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        key = self.COLS[index.column()][0]
        if role == QtCore.Qt.DisplayRole:
            if key == "name":
                return ("★ " + r.get("title", "")) if r.get("is_match") else r.get("title", "")
            if key == "author":
                return r.get("author", "")
            if key == "downloads":
                return format_downloads(r.get("downloads", 0))
            if key == "version":
                v = r.get("latest_version", "")
                return (v + " ⬆ 可更新") if r.get("updatable") else (v or "获取中…")
            return r.get("slug", "")
        if role == QtCore.Qt.ToolTipRole:
            return "%s\n%s\n%s" % (r.get("title", ""), r.get("description", ""),
                                   r.get("project_url", ""))
        if role == QtCore.Qt.BackgroundRole:
            th = self.theme
            if r.get("is_match"):
                return QtGui.QColor(th.get("highlight_bg", "#cce5ff"))
            if r.get("updatable"):
                return QtGui.QColor(th.get("warn_bg", "#ffeaa7"))
            return None
        if role == QtCore.Qt.ForegroundRole:
            th = self.theme
            if r.get("is_match"):
                return QtGui.QColor(th.get("highlight_fg", "#000000"))
            if r.get("updatable"):
                return QtGui.QColor(th.get("warn_fg", "#000000"))
        return None


class OnlineSearchDialog(QtWidgets.QDialog):
    """联网搜索窗口（非模态）：搜 Modrinth、最相似置顶、双击打开项目页。

    非模态是硬要求：本窗口的 Qt 事件由 Tk 的 after 泵推着跑，一旦 exec() 开嵌套事件循环，
    Tk 主界面会整块冻住（详情窗踩过这个坑，实测 549ms 里 Tk 心跳 0 次）。
    """

    def __init__(self, title: str, query: str, local_version: str, modid: str,
                 theme: dict, parent=None):
        super().__init__(parent)
        self.theme = dict(theme)
        self.modid = modid
        self.setWindowTitle("联网搜索 - %s" % title)
        self.resize(880, 520)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self._signals = _SearchSignals()
        self._signals.results.connect(self._on_results)
        self._signals.error.connect(self._on_error)
        self._signals.version.connect(self._on_version)
        self._build(title, query, local_version)

    # ---------------- UI ----------------
    def _build(self, title, query, local_version):
        th = self.theme
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        head = QtWidgets.QLabel("🌐 联网搜索（Modrinth）· %s" % title)
        f = head.font()
        f.setPointSize(11)
        f.setBold(True)
        head.setFont(f)
        head.setStyleSheet("color:%s;" % th.get("fg"))
        lay.addWidget(head)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.entry = QtWidgets.QLineEdit(query)
        self.entry.setPlaceholderText("搜索词（模组名 / Mod ID）")
        self.entry.setFixedHeight(30)
        self.entry.setClearButtonEnabled(True)
        self.entry.setMinimumWidth(320)
        self.btn = AnimButton("🔍 联网搜索", "#00bcd4", "#0097a7", th)
        self.btn.setFixedWidth(120)
        row.addWidget(QtWidgets.QLabel("搜索词:"))
        row.addWidget(self.entry, 1)
        row.addWidget(self.btn)
        lay.addLayout(row)

        self.status = QtWidgets.QLabel("回车或点「联网搜索」开始；最相似的会置顶并标 ★。"
                                       "双击一行打开项目主页。")
        self.status.setStyleSheet("color:%s;" % th.get("muted_fg"))
        lay.addWidget(self.status)

        self.model = SearchModel(th, local_version, self)
        self.table = SmoothTable()
        self.table.setModel(self.model)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.table.setFocusPolicy(QtCore.Qt.NoFocus)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        _style_view_palette(self.table, th)
        hh = self.table.horizontalHeader()
        hh.setStyleSheet(_header_qss(th))
        hh.setFixedHeight(30)
        hh.setHighlightSections(False)
        for i, (_k, _t, w) in enumerate(SearchModel.COLS):
            self.table.setColumnWidth(i, w)
            hh.setSectionResizeMode(i, QtWidgets.QHeaderView.Interactive)
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._open_project_page)
        lay.addWidget(self.table, 1)

        act = QtWidgets.QHBoxLayout()
        act.setSpacing(6)
        self.btn_page = AnimButton("🌍 打开项目页", "#43a047", "#2e7d32", th)
        self.btn_dl = AnimButton("⬇️ 打开下载页", "#3f8ae0", "#2f6fd0", th)
        self.btn_copy = AnimButton("📋 复制下载链接", "#6b7280", "#4b5563", th)
        self.btn_close = AnimButton("关闭", "#757575", "#616161", th)
        for b in (self.btn_page, self.btn_dl, self.btn_copy, self.btn_close):
            act.addWidget(b)
        act.addStretch(1)
        lay.addLayout(act)

        self.btn.clicked.connect(self.search)
        self.entry.returnPressed.connect(self.search)
        self.btn_page.clicked.connect(self._open_project_page)
        self.btn_dl.clicked.connect(self._open_download_page)
        self.btn_copy.clicked.connect(self._copy_download)
        self.btn_close.clicked.connect(self.close)

    def showEvent(self, ev):
        super().showEvent(ev)
        if not getattr(self, "_native_styled", False):
            self._native_styled = True
            style_window_hwnd(int(self.winId()), _is_dark(self.theme))
        if not getattr(self, "_auto_searched", False):
            self._auto_searched = True
            QtCore.QTimer.singleShot(200, self.search)   # 打开就直接搜一次

    # ---------------- 搜索 ----------------
    def search(self):
        q = self.entry.text().strip()
        if not q:
            self.status.setText("请输入搜索词。")
            return
        self.status.setText("搜索中…（首次联网可能要几秒）")
        self.btn.setEnabled(False)
        QtCore.QThreadPool.globalInstance().start(
            _SearchTask(q, 8, self._signals, self.modid, q))

    def _on_results(self, results, modid, name):
        self.btn.setEnabled(True)
        self.model.set_results(results, modid, name)
        n = len(self.model.rows)
        if not n:
            self.status.setText("没有找到相关模组，换个关键词试试。")
            return
        best = self.model.rows[0].get("score", 0)
        extra = "★为最相似项，已置顶。" if best >= 20 else "未找到相似度足够的候选。"
        self.status.setText("找到 %d 个结果。%s 版本/下载链接加载中…" % (n, extra))
        pool = QtCore.QThreadPool.globalInstance()
        for i, r in enumerate(self.model.rows):
            if r.get("project_id"):
                pool.start(_VersionTask(i, r["project_id"], self._signals))

    def _on_error(self, msg):
        self.btn.setEnabled(True)
        self.status.setText("❌ %s" % msg)

    def _on_version(self, row, vnum, url):
        self.model.apply_version(row, vnum, url)
        self._versions_done = getattr(self, "_versions_done", 0) + 1
        total = sum(1 for r in self.model.rows if r.get("project_id"))
        if total and self._versions_done >= total:
            self.status.setText("找到 %d 个结果。版本/下载链接已全部就绪（⬆ = 比本地新）。"
                                % len(self.model.rows))

    # ---------------- 动作 ----------------
    def _current(self) -> dict:
        idx = self.table.currentIndex()
        row = idx.row() if idx.isValid() else 0
        if not self.model.rows:
            return {}
        return self.model.item(max(0, min(row, len(self.model.rows) - 1)))

    def _open_project_page(self, *_a):
        r = self._current()
        if r.get("project_url"):
            webbrowser.open(r["project_url"])
        else:
            self.status.setText("先搜出结果、再点一行。")

    def _open_download_page(self):
        r = self._current()
        url = r.get("download_url")
        if url:
            webbrowser.open(url)
        else:
            self.status.setText("这一项还没拿到下载链接（或该项目没有可用文件）。")

    def _copy_download(self):
        r = self._current()
        url = r.get("download_url")
        if url:
            QtWidgets.QApplication.clipboard().setText(url)
            self.status.setText("已复制下载链接：%s" % url)
        else:
            self.status.setText("这一项还没拿到下载链接。")


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class QtBigView(QtWidgets.QWidget):
    """放大查看（PySide6 试点）。由 Tk 侧定时调用 pump() 驱动。"""

    def __init__(self, entries, is_mod, source_path, title, theme, online_tags=True,
                 hooks=None, parent=None, cards=False):
        super().__init__(parent)
        self.theme = dict(theme)
        self.hooks = hooks or {}
        self.title_text = title
        self._alive = True
        self.store = Store(entries, is_mod, source_path, online_tags, parent=self)
        self.setWindowTitle("放大查看 - %s" % title)
        self.setMinimumSize(880, 560)
        self.resize(1060, 680)
        # 打开时用表格还是卡片：默认视图在设置里选（_BIG_VIEW_VIEWS），这里只管摆好初始状态
        self._start_cards = bool(cards)
        # 原生窗口框（不是无边框 + 半透明）：
        # layered 窗口会被 DWM 跳过弹出/关闭/最小化动画，换成原生框后系统动画、原生
        # 阴影、四边拖拽缩放全都回来了；圆角和深色标题栏走 DWM 属性（见 showEvent）。
        self._build()

    # ---------------- UI ----------------
    def _build(self):
        th = self.theme
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)         # 原生窗口自带边框/阴影，不用留白
        frame = QtWidgets.QFrame()
        self.frame = frame
        frame.setObjectName("root")
        outer.addWidget(frame)
        lay = QtWidgets.QVBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(8)

        # ---- 标题栏 ----
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(6)
        self.title_label = QtWidgets.QLabel("🗂 放大查看 · %s" % self.title_text)
        f = self.title_label.font()
        f.setPointSize(11)
        f.setBold(True)
        self.title_label.setFont(f)
        bar.addWidget(self.title_label)
        tag = QtWidgets.QLabel("PySide6 试点")
        self.tag_label = tag
        bar.addWidget(tag)
        bar.addStretch(1)
        self.hint_tag = QtWidgets.QLabel("🪟 系统原生窗口")
        self.hint_tag.setStyleSheet("color:%s;" % th.get("muted_fg"))
        bar.addWidget(self.hint_tag)
        lay.addLayout(bar)

        # ---- 工具条 ----
        tools = QtWidgets.QHBoxLayout()
        tools.setSpacing(6)
        is_mod = self.store.is_mod
        self.btn_detect = AnimButton("🔍 检测存在性", "#3f8ae0", "#2f6fd0", th)
        self.btn_del = AnimButton("🗑 移出清单", "#e0574f", "#c62828", th)
        self.btn_add = AnimButton("➕ 添加模组", "#3fb27f", "#2e8b57", th) if is_mod else None
        self.btn_all = AnimButton("☑ 全选", "#7c6cf0", "#5b4bd6", th)
        self.btn_inv = AnimButton("⇄ 反选", "#7c6cf0", "#5b4bd6", th)
        # 不要写"清空"：它清的是**勾选**（选中态），不是清空清单 —— 那有"移出清单"负责，
        # 两个都叫清空会让人以为清单被删了
        self.btn_none = AnimButton("⬜ 清空勾选", "#6b7280", "#4b5563", th)
        self.btn_all.setToolTip("勾选当前显示的所有条目（不会改清单内容）")
        self.btn_inv.setToolTip("把已勾选 / 未勾选反过来（只作用于当前显示）")
        self.btn_none.setToolTip("取消所有勾选（只清选中态，清单内容不动）")
        self.btn_online = (AnimButton("🌐 联网搜索", "#7e57c2", "#5e35b1", th)
                           if is_mod else None)
        self.btn_view = AnimButton("🗂 卡片视图", "#0ea5a4", "#0b7f7f", th)
        # 工具条按钮按「界面按钮」的配置摆：隐藏的不加、顺序照配置（和 Tk 版共用一套 key，
        # 两个窗口各自有哪几个按钮就摆哪几个）。改完设置后下次打开这个窗口生效。
        _btns = {"bv_detect": self.btn_detect, "bv_remove": self.btn_del,
                 "bv_add": self.btn_add, "bv_all": self.btn_all,
                 "bv_invert": self.btn_inv, "bv_none": self.btn_none,
                 "bv_online": self.btn_online, "bv_view": self.btn_view}
        try:
            from ui import button_prefs as _bp
            _seq = [k for k in _bp.keys("bigview") if k in _btns]
            _known = set(_bp.DEFAULTS.get("bigview", ()))
            _seq += [k for k in _btns if k not in _known]
        except Exception:
            _seq = list(_btns)
        for _k in _seq:
            if _btns.get(_k) is not None:
                tools.addWidget(_btns[_k])
        tools.addStretch(1)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("🔍 搜索（文件名 / 完整路径）")
        self.search.setFixedHeight(30)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        tools.addWidget(self.search)
        lay.addLayout(tools)

        self.btn_detect.clicked.connect(self._on_detect)
        self.btn_del.clicked.connect(self._on_remove)
        if self.btn_add is not None:
            self.btn_add.clicked.connect(self._on_add)
        self.btn_all.clicked.connect(lambda: self._on_check("all"))
        self.btn_inv.clicked.connect(lambda: self._on_check("invert"))
        self.btn_none.clicked.connect(lambda: self._on_check("none"))
        self.btn_view.clicked.connect(self._toggle_view)
        if self.btn_online is not None:
            self.btn_online.clicked.connect(self._on_online_search)
        self.search.textChanged.connect(self._on_search)

        # ---- 摘要 ----
        # 富文本：每段数字按语义单独上色（见 _update_summary），别再整行一个灰白
        self.summary = QtWidgets.QLabel("")
        self.summary.setTextFormat(QtCore.Qt.RichText)
        lay.addWidget(self.summary)

        # ---- 内容：表格 / 卡片 ----
        self.table_model = TableModel(self.store, th, self)
        self.table = SmoothTable()
        self.table.setModel(self.table_model)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QtWidgets.QFrame.NoFrame)
        # 表格不拿焦点：省掉单元格虚线焦点框，也让 Esc/Ctrl+A 这些快捷键直接落到窗口上
        self.table.setFocusPolicy(QtCore.Qt.NoFocus)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.table.setWordWrap(False)
        self.table.setMouseTracking(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        # 固定行高：不固定的话 QTableView 每次都要逐行问 sizeHintForRow 才能算出滚动范围，
        # 上千行时滚动会一卡一卡
        self.table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        hh = self.table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.setHighlightSections(False)
        hh.setFixedHeight(30)
        for i, (_k, _t, w) in enumerate(self.table_model.cols):
            self.table.setColumnWidth(i, w)
            hh.setSectionResizeMode(i, QtWidgets.QHeaderView.Interactive)
        path_col = next((i for i, c in enumerate(self.table_model.cols) if c[0] == "path"), 2)
        hh.setSectionResizeMode(path_col, QtWidgets.QHeaderView.Stretch)
        hh.sectionClicked.connect(self._on_header_click)
        hh.installEventFilter(self)          # 鼠标停在表头上也能滚（Tk 版也绑了表头）
        self.table.clicked.connect(self._on_table_click)
        self.table.doubleClicked.connect(self._on_table_double)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_menu)
        self.table.mouseMoveEvent = self._table_mouse_move
        self.table.leaveEvent = self._table_leave
        self.table.setItemDelegate(TableDelegate(self.table))

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
        self.cards.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.cards.customContextMenuRequested.connect(self._on_card_menu)
        self.cards.mouseMoveEvent = self._card_mouse_move
        self.cards.leaveEvent = self._card_leave

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.cards)
        lay.addWidget(self.stack, 1)
        if self._start_cards:                 # 设置里选了"打开就是卡片"
            self.stack.setCurrentIndex(1)
            self.btn_view.setText("📋 表格视图")

        # ---- 底栏 ----
        # 原生窗口框自己就能从四边拖拽缩放，不需要 QSizeGrip
        bottom = QtWidgets.QHBoxLayout()
        self.hint = QtWidgets.QLabel("单击=选中 · 双击=详情 · 右键=菜单 · Ctrl+V=粘贴文件 · Esc=关闭")
        bottom.addWidget(self.hint)
        bottom.addStretch(1)
        lay.addLayout(bottom)

        self._sel_anims = {}
        self._pop_anims = {}            # 进场/退场动画（键 = id(item)）
        self._pending_remove = None     # 退场动画播完要真删的那批条目
        self._remove_left = 0           # 还有几条退场动画没播完
        self._dialogs = []              # 非模态对话框/菜单的引用（防回收）
        self._card_hit = (-1, None)
        self._dc_row = None             # 双击后要吃掉一次 clicked 的行号（见 _eat_click）
        # 摘要常驻 200ms 刷一次：扫描结果是一条一条回来的，如果只在 reset 时统计，
        # 扫描期间计数会一直停在 0。200ms 一次全量统计，三千条也只要 0.3ms。
        self._summary_text = ""
        self._sum_timer = QtCore.QTimer(self)
        self._sum_timer.setInterval(200)
        self._sum_timer.timeout.connect(self._on_sum_tick)
        self._sum_timer.start()
        self.store.reset.connect(self._on_reset_view)
        self._apply_style()
        self._update_summary()
        self.store.scan_all()

    # ---------------- 主题 ----------------
    def _apply_style(self):
        """所有 QSS 集中在这里，主题切换时重跑一遍即可（自绘控件读 self.theme）。"""
        th = self.theme
        self.frame.setStyleSheet("#root{background:%s;}" % th.get("bg", "#ffffff"))
        self.title_label.setStyleSheet("color:%s;" % th.get("fg"))
        self.tag_label.setStyleSheet("color:%s;background:%s;border-radius:7px;padding:1px 8px;"
                                     % (th.get("accent_fg"), th.get("accent_bg")))
        self.search.setStyleSheet(
            "QLineEdit{background:%s;color:%s;border:1px solid %s;border-radius:8px;"
            "padding:0 10px;}"
            "QLineEdit:focus{border:2px solid %s;}"
            % (th.get("entry_bg"), th.get("fg"), th.get("muted_fg"), th.get("card_sel_bar")))
        # 摘要行是富文本（每段数字单独染色），这里只管字号和行距，颜色写在 HTML 里
        self.summary.setStyleSheet("color:%s;font-size:10pt;padding:2px 0;"
                                  % th.get("muted_fg"))
        # 摘要行是富文本，换主题后颜色写在 HTML 里，得立刻重出一遍（否则要等 200ms 那次刷新）
        if hasattr(self, "_summary_text"):
            self._update_summary()
        self.hint.setStyleSheet("color:%s;" % th.get("muted_fg"))
        self.hint_tag.setStyleSheet("color:%s;" % th.get("muted_fg"))
        # 表格配色走调色板，**绝不能**给它设 QSS（见 _style_view_palette 的注释）
        _style_view_palette(self.table, th)
        self.table.horizontalHeader().setStyleSheet(_header_qss(th))
        self.cards.setStyleSheet("QListView{background:%s;border:none;outline:none;}"
                                 % th.get("bg"))
        self.update()

    def set_theme(self, theme: dict):
        """主界面切主题时同步配色（窗口不关、选中态不丢）。"""
        self.theme = dict(theme)
        self.table_model.theme = self.theme
        self.card_delegate.theme = self.theme
        self.card_delegate._pix.clear()
        self.card_delegate._fb = None
        self.table_model._status_cache = {}
        ensure_app(self.theme)
        self._apply_style()
        if self._native_styled:
            # 标题栏也跟着换深浅（DWM 对第一次的 dark 有粘性，所以每次都显式写一遍）
            style_window_hwnd(int(self.winId()), self._is_dark())
        for w in (self.table.viewport(), self.cards.viewport()):
            w.update()
        self.table_model.layoutChanged.emit()
        self.card_model.layoutChanged.emit()

    # ---------------- 交互：表格 ----------------
    def eventFilter(self, obj, ev):
        """表头上的滚轮转发给表格本体（否则鼠标划到表头就滚不动了）。"""
        if ev.type() == QtCore.QEvent.Wheel and obj is self.table.horizontalHeader():
            self.table.wheelEvent(ev)
            return True
        return super().eventFilter(obj, ev)

    def _table_mouse_move(self, ev):
        idx = self.table.indexAt(ev.pos())
        row = idx.row() if idx.isValid() else -1
        self._set_hover(row)
        QtWidgets.QTableView.mouseMoveEvent(self.table, ev)

    def _table_leave(self, ev):
        self._set_hover(-1)
        QtWidgets.QTableView.leaveEvent(self.table, ev)

    def _set_hover(self, row):
        old = self.table_model.hover_row
        if old == row:
            return
        self.table_model.hover_row = row
        for r in filter(lambda x: x >= 0, (old, row)):
            self.table_model.dataChanged.emit(self.table_model.index(r, 0),
                                              self.table_model.index(
                                                  r, self.table_model.columnCount() - 1))

    # 交互规则就两条，全部交给 Qt 自己的事件：
    #   单击（clicked）       = 选中/取消选中
    #   双击（doubleClicked） = 打开详情，且**不改选中态**
    # Qt 的事件顺序是：单击按下/抬起 → clicked；双击按下 → doubleClicked；双击抬起 → clicked。
    # 所以双击实际会翻两次选中态（净变化为 0），但中间会闪一下高亮。为了让它"保持原状态"
    # 干净利落，doubleClicked 里直接把第一下造成的选中变化瞬间撤回，并记下这一行，让紧接着
    # 那次 clicked 不再翻转（_eat_click）。判定本身没有任何手写逻辑，全是 Qt 原生事件。
    # 间隔由 ensure_app() 里的 setDoubleClickInterval(DOUBLE_CLICK_MS) 统一设定。
    def _on_table_click(self, index):
        if not index.isValid():
            return
        if self._eat_click(index.row()):
            return
        self._toggle_row(index.row())

    def _on_table_double(self, index):
        if not index.isValid():
            return
        self._double_row(index.row())

    def _double_row(self, row):
        """双击：撤回第一下的选中变化 → 开详情 → 吃掉紧随其后的那次 clicked。

        config 清单没有"模组详情"这回事（那是模组才有的），所以那边双击等于两次单击。
        """
        self._restore_row_instant(row)
        self._dc_row = row
        QtCore.QTimer.singleShot(DOUBLE_CLICK_MS, self._clear_dc_row)
        if self.store.is_mod:
            self._open_detail(row)

    def _eat_click(self, row):
        """双击抬手那次 clicked 不再翻选中态（只吃同一行的那一次）。"""
        if self._dc_row == row:
            self._dc_row = None
            return True
        self._dc_row = None
        return False

    def _clear_dc_row(self):
        self._dc_row = None

    def _on_header_click(self, col):
        name = self.table_model.cols[col][0]
        if self.store.sort_col == name:
            self.store.sort_rev = not self.store.sort_rev
        else:
            self.store.sort_col, self.store.sort_rev = name, False
        self.table.horizontalHeader().setSortIndicator(
            col, QtCore.Qt.DescendingOrder if self.store.sort_rev else QtCore.Qt.AscendingOrder)
        self.store.rebuild()

    # ---------------- 交互：卡片 ----------------
    def _card_mouse_move(self, ev):
        idx = self.cards.indexAt(ev.pos())
        row = idx.row() if idx.isValid() else -1
        action = None
        if row >= 0:
            rect = self.cards.visualRect(idx)
            for i, (name, _g) in enumerate(self.card_delegate.actions):
                if self.card_delegate.action_rect(rect, i).contains(ev.pos()):
                    action = name
                    break
        if (row, action) != (self.card_delegate.hover_row, self.card_delegate.hover_action):
            self.card_delegate.hover_row = row
            self.card_delegate.hover_action = action
            self.cards.viewport().update()
        self._card_hit = (row, action)      # 点击时直接复用这里算出的命中结果
        self.cards.setCursor(QtCore.Qt.PointingHandCursor if action else QtCore.Qt.ArrowCursor)
        QtWidgets.QListView.mouseMoveEvent(self.cards, ev)

    def _card_leave(self, ev):
        self.card_delegate.hover_row = -1
        self.card_delegate.hover_action = None
        self._card_hit = (-1, None)
        self.cards.viewport().update()
        QtWidgets.QListView.leaveEvent(self.cards, ev)

    def _on_card_click(self, index):
        if not index.isValid():
            return
        row, act = getattr(self, "_card_hit", (-1, None))
        if act and row == index.row():
            self._card_action(row, act)      # 点的是悬停图标：只执行图标动作，不改选中
            return
        if self._eat_click(index.row()):
            return
        self._toggle_row(index.row())

    def _on_card_double(self, index):
        if not index.isValid():
            return
        row, act = getattr(self, "_card_hit", (-1, None))
        if act and row == index.row():
            return                       # 连点悬停图标不算"双击开详情"
        self._double_row(index.row())

    def _on_card_menu(self, pos):
        idx = self.cards.indexAt(pos)
        if not idx.isValid():
            return
        self.cards.setCurrentIndex(idx)
        self._row_menu(self.cards, idx.row(), self.cards.viewport().mapToGlobal(pos))

    def _on_table_menu(self, pos):
        """表格视图右键 = 卡片视图同一套菜单（开详情/切换选中不依赖双击，这里全都有）。"""
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        self.table.setCurrentIndex(idx)
        self._row_menu(self.table, idx.row(), self.table.viewport().mapToGlobal(pos))

    def _row_menu(self, view, row, global_pos):
        menu = QtWidgets.QMenu(view)
        menu.setStyleSheet("QMenu{background:%s;color:%s;border:1px solid %s;}"
                           "QMenu::item:selected{background:%s;}"
                           % (self.theme.get("bg"), self.theme.get("fg"),
                              self.theme.get("muted_fg"), self.theme.get("hover_bg")))
        act = {"info": "ℹ 详情", "reveal": "📂 打开所在位置", "remove": "🗑 移出清单"}
        self._cur_row = row
        for name, _g in self.card_delegate.actions:      # config 清单里没有"详情"
            menu.addAction(act[name]).setData(name)
        # 选中/取消选中给一个不依赖时间的入口：双击窗口很小（见 DOUBLE_CLICK_MS），
        # 靠双击切换选中本来就不靠谱，这里补一条
        it_here = self.store.at(row)
        menu.addSeparator()
        menu.addAction("☐ 取消选中" if it_here.checked else "☑ 选中").setData("toggle")
        if self.store.is_mod:
            menu.addSeparator()
            menu.addAction("🌐 联网搜索（Modrinth）").setData("online")

        def _picked(pick):
            if pick is not None:
                self._card_action(row, pick.data())

        menu.triggered.connect(_picked)
        self._track(menu)
        menu.popup(global_pos)               # 非阻塞：Tk 那边继续跑

    def _card_action(self, row, action):
        it = self.store.at(row)
        if action == "info":
            self._open_detail(row)
        elif action == "online":
            self._cur_row = row
            self._on_online_search()
        elif action == "reveal":
            self._reveal(it.path)
        elif action == "toggle":
            self._toggle_row(row)          # 不依赖双击窗口的选中/取消选中
        elif action == "remove":
            # 单条移除也播一下退场：播完再真删
            it = self.store.at(row)
            self._pending_remove = lambda r=row: self._finish_remove_one(r)
            self._remove_left = 1
            self._animate_pop(it, 0.0)

    # ---------------- 通用动作 ----------------
    def _toggle_row(self, row):
        it = self.store.at(row)
        want = self.store.toggle(row)
        self._animate_sel(it, 1.0 if want else 0.0)
        self._update_summary()

    def _restore_row_instant(self, row):
        """瞬间把这一行恢复成"第一下点击之前"的状态（不走动画）。

        双击的第二下本来就会把选中态翻回去，但若让它跑 170ms 动画，用户看到的是
        "高亮亮起来又收回去"。双击应该完全不动高亮：取消正在跑的动画、直接把进度打到终值。
        """
        it = self.store.at(row)
        anim = self._sel_anims.pop(id(it), None)
        if anim is not None:
            anim.stop()
        want = not it.checked                 # 第一下已经翻过一次，翻回来就是原状态
        self.store.set_checked(row, want)
        self._on_sel_frame(it, 1.0 if want else 0.0)
        self._update_summary()

    def _animate_sel(self, it, target):
        key = id(it)
        old = self._sel_anims.get(key)
        if old is not None:
            old.stop()
        anim = QtCore.QVariantAnimation(self)
        anim.setDuration(170)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        anim.setStartValue(float(it.sel_t or 0.0))
        anim.setEndValue(float(target))
        anim.valueChanged.connect(lambda v, item=it: self._on_sel_frame(item, v))
        anim.finished.connect(lambda k=key: self._sel_anims.pop(k, None))
        self._sel_anims[key] = anim
        anim.start()

    def _on_sel_frame(self, it, v):
        it.sel_t = float(v)
        row = self.store.row_of(it)
        if row >= 0:
            idx = self.card_model.index(row, 0)
            self.card_model.dataChanged.emit(idx, idx)
            self.table_model.dataChanged.emit(self.table_model.index(row, 0),
                                              self.table_model.index(row,
                                                                     self.table_model.columnCount() - 1))

    # ---------------- 加/删的进场退场动画 ----------------
    def _animate_pop(self, it, target, duration=190):
        """条目进场（0→1）/ 退场（1→0）。

        **逐帧用 Tk 的 after 推，不用 QVariantAnimation**：退场播完要立刻重建模型，
        而 QVariantAnimation 的收尾和 beginResetModel 撞在一起会让解释器崩
        （实测 0xC0000409 / PyEval_RestoreThread）。逐帧推的收尾就落在 Tk 的 after
        回调里，和别的 Tk 操作同一个上下文，安全；顺带也和窗口里别的动画（平滑滚动）
        用同一套驱动方式。

        进场：新加的条目从半透明、略偏左滑到位；
        退场：先播完再真正从清单里删（见 _do_remove_selected），不然行会「啪」地消失。
        """
        after = self.hooks.get("after")
        key = id(it)
        old = self._pop_anims.pop(key, None)
        cancel = self.hooks.get("after_cancel")
        if old is not None and cancel is not None:
            try:
                cancel(old)
            except Exception:
                pass
        if after is None:                   # 没有 after 钩子（独立使用本模块时）：直接到位
            it.pop_t = float(target)
            self._on_pop_frame(it, target)
            self._on_pop_done(key, it, float(target))
            return
        起点 = float(it.pop_t)
        开始 = time.perf_counter()
        秒 = max(0.001, duration / 1000.0)

        def 帧():
            k = min(1.0, max(0.0, (time.perf_counter() - 开始) / 秒))
            缓动 = (1.0 - (1.0 - k) ** 3) if target >= 起点 else (k ** 3)
            v = 起点 + (target - 起点) * 缓动
            it.pop_t = v
            self._on_pop_frame(it, v)
            if k >= 1.0:
                self._pop_anims.pop(key, None)
                self._on_pop_done(key, it, float(target))
                return
            try:
                self._pop_anims[key] = after(16, 帧)
            except Exception:
                self._pop_anims.pop(key, None)

        帧()

    def _on_pop_frame(self, it, v):
        it.pop_t = float(v)
        row = self.store.row_of(it)
        if row < 0:
            return
        idx = self.card_model.index(row, 0)
        self.card_model.dataChanged.emit(idx, idx)
        self.table_model.dataChanged.emit(self.table_model.index(row, 0),
                                          self.table_model.index(
                                              row, self.table_model.columnCount() - 1))

    def _on_pop_done(self, key, it, target):
        self._pop_anims.pop(key, None)
        it.pop_t = float(target)
        if target > 0.001:                  # 进场播完，没事了
            return
        self._remove_left = max(0, self._remove_left - 1)
        if self._remove_left == 0 and self._pending_remove is not None:
            fn, self._pending_remove = self._pending_remove, None
            self._run_pending_remove(fn)    # 这会儿在 Tk 的 after 回调里，可以放心重建模型

    def _run_pending_remove(self, fn):
        try:
            fn()                            # 动画播完才真删
        except Exception:
            trace_exc("qt_big_view", "退场动画结束后执行删除")

    def _add_with_pop(self, paths) -> int:
        """加条目，并给新加的那几条播进场动画（返回新增数量）。"""
        before = len(self.store.items)
        n = self.store.add_entries(paths)
        for it in self.store.items[before:before + n]:
            it.pop_t = 0.0
            self._animate_pop(it, 1.0)
        return n

    def _on_detect(self):
        self.store.scan_all()

    def _on_check(self, mode):
        if mode == "all":
            self.store.checked_all(True, visible_only=True)
        elif mode == "none":
            self.store.checked_all(False, visible_only=True)
        else:
            self.store.invert(visible_only=True)
        for i in self.store.order:
            self._on_sel_frame(self.store.items[i], 1.0 if self.store.items[i].checked else 0.0)
        self._update_summary()

    def _on_search(self, text):
        self.store.set_query(text)

    def _write_back(self):
        """把清单写回主界面（增删后立刻同步，和 Tk 版行为一致；关窗时再补一次）。

        **必须交给 Tk 的 after 去写**：这里有时是从 Qt 的事件回调里进来的
        （比如退场动画结束后那条 QTimer），而那时主线程正卡在 `processEvents()` 里。
        在那里面直接改 Tk 控件会让两套消息循环打架 —— 实测删除时必崩
        （`PyEval_RestoreThread ... the GIL is released`）。hooks 里给了 defer
        就走它，没有就退化成直接调用（老调用方仍然能用）。
        """
        cb = self.hooks.get("write_back")
        if cb is None:
            return
        defer = self.hooks.get("defer")

        def 执行():
            try:
                cb(self.store.entry_texts())
            except Exception:
                pass

        if defer is not None:
            try:
                defer(执行)
                return
            except Exception:
                pass
        执行()

    # ---------------- 添加条目（Ctrl+V 粘贴 / 工具栏选择文件） ----------------
    def _drop_paths(self, paths):
        """把（粘贴或拖进来的）路径变成清单条目。

        和 Tk 版一个口径：
          · 模组窗口只收 `.jar`，存**完整路径**；
          · config 窗口收文件/文件夹，存**相对 config 目录**的路径（用 / 分隔，
            文件夹结尾加 /），带 `_is_safe_path` 校验，越界/不在 config 下的丢掉。
        """
        收, 跳过 = [], 0
        if self.store.is_mod:
            for p in paths:
                if Path(p).suffix.lower() == ".jar":
                    # 统一成本机风格的反斜杠路径：Qt 的 toLocalFile() 给的是正斜杠，
                    # 和 Tk 版（拖入/选择文件）写进去的格式不一致，看着别扭也不好比对
                    收.append(str(Path(p)))
                else:
                    跳过 += 1
        else:
            base = self.store.config_dir
            if base is None:
                self._message("添加提示", "请先在主界面设置源整合包目录，再往这里拖。")
                return
            for p in paths:
                tp = Path(p)
                try:
                    rel = tp.relative_to(base)
                except ValueError:
                    跳过 += 1
                    continue
                rel_s = str(rel).replace("\\", "/")
                if not _is_safe_path(rel_s):
                    跳过 += 1
                    continue
                if (base / rel_s).is_dir():
                    rel_s = rel_s.rstrip("/") + "/"
                收.append(rel_s)
        if not 收:
            self._message("添加提示",
                          "只支持拖入 .jar 模组文件。" if self.store.is_mod
                          else "拖入的文件不在源整合包的 config 目录下。")
            return
        n = self._add_with_pop(收)
        if not n:
            self._message("添加提示", "这些条目已经在清单里了。")
            return
        self._write_back()
        提示 = ("已添加 %d 个模组。" % n) if self.store.is_mod else ("已添加 %d 个条目。" % n)
        if 跳过:
            提示 += "（另有 %d 项被跳过）" % 跳过
        self._message("添加成功", 提示)

    def _on_remove(self):
        if self._pending_remove is not None:
            return                          # 上一批的退场动画还没播完
        n = len(self.store.selected_items())
        if not n:
            self._message("提示", "请先选中要移出的条目（单击行/卡片即选中）。")
            return
        self._confirm("移出清单", "把选中的 %d 项从清单里移出？（不会删除磁盘文件）" % n,
                      self._do_remove_selected)

    def _do_remove_selected(self):
        """先让「看得见的那几条」淡出，动画播完再真删；看不见的直接删掉就行。"""
        items = self.store.selected_items()
        可见 = [it for it in items if self.store.row_of(it) >= 0]
        if not 可见:
            self.store.remove_selected()
            self._write_back()
            return
        self._pending_remove = self._finish_remove_selected
        self._remove_left = len(可见)
        for it in 可见:
            self._animate_pop(it, 0.0)

    def _finish_remove_selected(self):
        self.store.remove_selected()
        self._write_back()

    def _finish_remove_one(self, row):
        if self.store.remove_one(row):
            self._write_back()

    def _on_add(self):
        dlg = QtWidgets.QFileDialog(self, "选择要添加的模组（可多选）")
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFiles)
        dlg.setNameFilters(["Minecraft 模组 (*.jar)", "所有文件 (*.*)"])
        if self.store.source_path:
            dlg.setDirectory(self.store.source_path)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)

        def _picked(files):
            if not files:
                return
            n = self._add_with_pop(list(files))
            if n:
                self._write_back()
                self._message("添加成功", "已添加 %d 个模组。" % n)

        dlg.filesSelected.connect(_picked)
        self._track(dlg)
        dlg.open()                       # 非阻塞对话框：Tk 主界面在这期间照样能响应

    def _toggle_view(self):
        to_cards = self.stack.currentIndex() == 0
        self.stack.setCurrentIndex(1 if to_cards else 0)
        self.btn_view.setText("📋 表格视图" if to_cards else "🗂 卡片视图")

    def _reveal(self, path):
        if not path or not Path(path).exists():
            self._message("提示", "该文件不在磁盘上，无法定位。")
            return
        import subprocess
        subprocess.Popen(["explorer", "/select,", str(path)])

    # ---------------- 对话框（一律非阻塞） ----------------
    # 关键：Qt 的 exec() 会开一个嵌套事件循环，而我们的 Qt 事件是由 Tk 的 after
    # 回调推着跑的 —— 一旦 exec()，那个 after 就回不来，整个 Tk 主界面会冻住
    # （实测 549ms 的对话框期间 Tk 心跳 0 次）。所以全部改成 open()/popup() + 回调。
    def _track(self, widget):
        """登记非模态窗口，防止被 Python 回收；顺手清掉已经销毁的。"""
        live = []
        for d in getattr(self, "_dialogs", []):
            try:
                d.isVisible()
                live.append(d)
            except RuntimeError:          # C++ 对象已销毁
                pass
        live.append(widget)
        self._dialogs = live
        return widget

    def _open_detail(self, row):
        """打开详情窗（非模态）：主界面和放大查看窗口都能继续用。

        「模组详情」只对模组清单成立 —— config 清单里那行是配置文件，这里直接挡掉，
        免得以后哪条路又把它放出来（菜单/双击/悬停图标都各自拦了一道）。
        """
        if not self.store.is_mod:
            return None
        try:
            it = self.store.at(row)
            dlg = DetailDialog(it, self.theme, self._reveal, self)
            dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            self._track(dlg)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            trace_line("detail row=%d key=%s visible=%s active=%s geom=%d,%d %dx%d"
                       % (row, it.entry, dlg.isVisible(), dlg.isActiveWindow(),
                          dlg.x(), dlg.y(), dlg.width(), dlg.height()))
            return dlg
        except Exception as e:                    # 别让它静默变成"点了没反应"
            trace_exc("_open_detail", e)
            return None

    def _on_online_search(self):
        """联网搜索当前行（和 Tk 版详情窗口同一套 Modrinth 逻辑）。

        非模态窗口 + Qt 线程池联网，主界面在这期间照常能点、能滚。
        """
        if not self.store.items:
            return
        row = getattr(self, "_cur_row", -1)
        if not (0 <= row < len(self.store.order)):
            sel = [i for i, it in enumerate(self.store.items) if it.checked]
            row = self.store.row_of(self.store.items[sel[0]]) if sel else 0
        it = self.store.at(max(0, row))
        dlg = OnlineSearchDialog(it.title, guess_query(it), str(it.version or ""),
                                 str(it.modid or ""), self.theme, self)
        self._track(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _message(self, title, text):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self._track(box)
        box.open()

    def _confirm(self, title, text, on_yes):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.Yes)
        box.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        box.finished.connect(lambda r: on_yes() if r == QtWidgets.QMessageBox.Yes else None)
        self._track(box)
        box.open()

    # ---------------- 状态 ----------------
    def _on_reset_view(self):
        self._update_summary()

    def _active_view(self):
        """当前露在外面的是表格还是卡片（哪张看得见就按哪张算可见行）。"""
        return self.cards if self.stack.currentIndex() == 1 else self.table

    def _on_sum_tick(self):
        """200ms 一次：先把攒下的扫描结果刷给"看得见的行"，再更新摘要。"""
        self._flush_rows()
        self._update_summary()

    def _flush_rows(self):
        """只给可见行发 dataChanged（扫描期间一条一条全发会把窗口拖卡）。

        不可见的行干脆不发：滚过去的时候视图本来就要重画它，那时直接从数据里读到的
        已经是最新状态，所以不需要补发，也不用记账。
        """
        dirty = self.store.take_dirty()
        if not dirty:
            return
        view = self._active_view()
        pos = self.store._pos
        try:
            top = view.rowAt(0)
            bot = view.rowAt(max(0, view.viewport().height() - 1))
        except Exception:
            top, bot = 0, len(self.store.order) - 1
        count = len(self.store.order)
        if top < 0:
            top = 0
        if bot < 0:
            bot = count - 1
        for i in dirty:
            r = pos.get(i)
            if r is not None and top <= r <= bot:
                self.store.row_data.emit(r)

    def _update_summary(self):
        """顶部摘要行：按语义上色（整行一个灰白色太素，看不出哪个数字要紧）。

        QLabel 支持富文本，就把每段数字单独染色：
        总数=主题前景色加粗、已选>0=选中蓝、存在>0=绿、缺失>0=红（为 0 的那个压成灰，
        免得"存在 0"顶着绿色看着像好消息）、正在扫描=橙。主题换了也会跟着重出
        （_apply_style 里会立刻调一次）。
        """
        total, shown, sel, ok, miss = self.store.counts()
        th = self.theme
        fg = th.get("fg", "#eeeeee")
        muted = th.get("muted_fg", "#9a9a9a")
        accent = th.get("card_sel_bar", "#2f7fd1")
        good = th.get("ok_fg") or th.get("log_success_fg", "#2e7d32")
        bad = th.get("fail_fg", "#c62828")
        warn = th.get("log_warning_fg", "#e65100")
        dot = '<span style="color:%s;"> · </span>' % muted

        def num(text, color, bold=False):
            body = "<b>%s</b>" % text if bold else text
            return '<span style="color:%s;">%s</span>' % (color, body)

        def label(text, color=muted):
            return '<span style="color:%s;">%s</span>' % (color, text)

        parts = [num("共 %d 项" % total, fg, True)]
        if shown != total:
            # 搜索/过滤把显示数压下来了，值得单独提示一句
            parts.append(num("显示 %d 项" % shown, accent if shown else muted, True))
        parts.append(num("已选 %d 项" % sel, accent if sel else muted, bool(sel)))
        parts.append(
            label("存在 ") + num("%d" % ok, good if ok else muted, bool(ok))
            + label(" / 缺失 ") + num("%d" % miss, bad if miss else muted, bool(miss)))
        if self.store.scanning:
            parts.append(label("检测中…", warn))

        html = dot.join(parts)
        if html != self._summary_text:          # 内容没变就不 setText，省掉重绘
            self._summary_text = html
            self.summary.setText(html)
        title = "🗂 放大查看 · %s（%d）" % (self.title_text, total)
        if title != getattr(self, "_title_shown", ""):
            self._title_shown = title
            self.title_label.setText(title)

    # ---------------- Tk 侧驱动 ----------------
    def pump(self):
        """Tk 的 after 回调里调用（每个泵帧一次）。

        - 先推进滚轮缓动：一个泵帧正好一步，不会被 QTimer 抢帧（见 _SmoothWheel 注释）
        - 再把 Qt 的事件（绘制/动画/输入）跑一轮
        """
        now = time.perf_counter()
        if getattr(self.table, "_sw_active", False):
            self.table.tick_scroll(now)
        if getattr(self.cards, "_sw_active", False):
            self.cards.tick_scroll(now)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents(QtCore.QEventLoop.AllEvents, 8)

    def is_alive(self) -> bool:
        return self._alive

    def show_centered(self):
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, ev):
        """窗口一出来就上原生外观：深色/浅色标题栏 + Win11 圆角。

        这里必须是 showEvent：HWND 到这一步才真正存在；而且**不能**用无边框+半透明，
        那样 DWM 会跳过弹出/关闭/最小化的原生动画（用户看到的就是"啪一下没了"）。
        """
        super().showEvent(ev)
        if not getattr(self, "_native_styled", False):
            self._native_styled = True
            style_window_hwnd(int(self.winId()), self._is_dark())
            # 预热首帧：字体/样式/绘制路径的首次初始化很贵（实测首次滚动单帧 33ms），
            # 开窗时先把两张视图整块画一遍，用户第一次滚轮就不会撞上这一下。
            QtCore.QTimer.singleShot(150, self._warm_paint)

    def _warm_paint(self):
        for w in (getattr(self, "table", None), getattr(self, "cards", None)):
            if w is None:
                continue
            try:
                w.viewport().grab()
            except Exception:
                pass

    def _is_dark(self) -> bool:
        """主题是不是深色（和 Tk 侧 is_dark_theme 同一判据：比背景色亮度）。"""
        return _is_dark(self.theme)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == QtCore.Qt.Key_Escape:
            self.close()
        elif k == QtCore.Qt.Key_Delete:
            self._on_remove()
        elif ev.modifiers() & QtCore.Qt.ControlModifier:
            if k == QtCore.Qt.Key_V:
                # 把资源管理器里 Ctrl+C 复制的文件加进清单。
                # 拖放在 Qt 窗口上做不了（三种接法都实测会偶发崩解释器，见文件头注释）；
                # 粘贴走 QClipboard，纯 Qt API、不碰 Windows 消息层。
                self._paste_files()
            elif k == QtCore.Qt.Key_A:
                self._on_check("all")
            elif k == QtCore.Qt.Key_F:
                self.search.setFocus()
        super().keyPressEvent(ev)

    def _paste_files(self):
        """剪贴板里如果是"复制的文件"，就按添加条目处理（和拖入同一个入口）。"""
        try:
            md = QtWidgets.QApplication.clipboard().mimeData()
            if md is None or not md.hasUrls():
                return
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self._drop_paths(paths)
        except Exception:
            trace_exc("qt_big_view", "处理 Ctrl+V 粘贴")

    def closeEvent(self, ev):
        self._alive = False
        for d in list(getattr(self, "_dialogs", [])):
            try:
                d.close()
            except RuntimeError:
                pass
        self._dialogs = []
        self.store._pool.waitForDone(1500)
        self._write_back()
        super().closeEvent(ev)


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def ensure_app(theme: dict = None):
    """确保 QApplication 存在并把字体/调色板设成应用主题。"""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)      # 关掉 Qt 窗口不能退出进程（Tk 还在跑）
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    try:
        # 窗口图标：Qt 不设的话标题栏/任务栏是 Windows 那个通用占位图标
        # （和主窗口的 1.ico 完全不是一个东西）。设成应用级默认图标，Qt 的所有
        # 顶层窗口（放大查看、详情、联网搜索…）都跟着用。
        _ico = get_icon_path()
        if _ico:
            app.setWindowIcon(QtGui.QIcon(_ico))
    except Exception:
        pass
    try:
        # 双击间隔就靠它：显式写死 DOUBLE_CLICK_MS（免得用户改了系统双击速度后
        # 这个窗口跟着变）。数值含义见模块顶部说明。
        app.setDoubleClickInterval(DOUBLE_CLICK_MS)
    except Exception:
        pass
    if theme:
        try:
            app.setStyle("Fusion")
            pal = QtGui.QPalette()
            pal.setColor(QtGui.QPalette.Window, QtGui.QColor(theme.get("bg", "#202020")))
            pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(theme.get("fg", "#eeeeee")))
            pal.setColor(QtGui.QPalette.Base, QtGui.QColor(theme.get("entry_bg", "#2a2a2a")))
            pal.setColor(QtGui.QPalette.Text, QtGui.QColor(theme.get("fg", "#eeeeee")))
            pal.setColor(QtGui.QPalette.Button, QtGui.QColor(theme.get("ttk_bg", "#333333")))
            pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(theme.get("fg", "#eeeeee")))
            pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(theme.get("card_sel_bar",
                                                                         "#2f7fd1")))
            pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
            app.setPalette(pal)
        except Exception:
            pass
    return app


def show_big_view(entries, is_mod, source_path, title, theme, online_tags=True, hooks=None,
                  cards=False):
    """创建窗口并显示，返回 QtBigView（Tk 侧需要拿它做 pump 循环）。"""
    app = ensure_app(theme)
    view = QtBigView(entries, is_mod, source_path, title, theme, online_tags, hooks,
                     cards=cards)
    view.show_centered()
    app.processEvents(QtCore.QEventLoop.AllEvents, 10)
    return view

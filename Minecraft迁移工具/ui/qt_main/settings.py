# ui/qt_main/settings.py
"""Qt 设置窗：**原样对标 Tk**（ui/mw_settings.py）。

五条契约（和 Tk 一致，别再退回旧做法）：
  · 五个标签页：🎨 外观与启动 / 🚚 迁移与分类 / 🗂 放大查看 / 🔘 界面按钮 / 🚪 后台与退出
  · 每页分区描边卡（SettingsSection），页内独立滚动
  · 分段条 / 开关 / 滑杆 / 步进 / 选择卡全部自绘，不用 QComboBox / QCheckBox / QSpinBox
  · **没有「确定/取消」**：只有一个「✕ 关闭」，任何改动立即写配置并刷新界面
  · 「界面按钮」是设置窗的第 4 页，不是主界面上的独立对话框
"""
import sys
import threading
import webbrowser
from pathlib import Path

_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from utils import i18n  # noqa: E402
from ui.qt_win import fit_window
from utils.i18n import tr, trp  # noqa: E402
from utils import history_store  # noqa: E402
from utils import fx  # noqa: E402
from utils import secrets  # noqa: E402
from ui.qt_main.dialog_style import apply_button_styles, set_button_kind, set_muted_label, theme_for  # noqa: E402
from ui.qt_main.switch_row import SwitchRow  # noqa: E402
from ui.qt_main.settings_widgets import (  # noqa: E402
    MutedLabel, OptionCards, SegmentedControl, SettingsSection, SliderRow, StepperRow, make_row)

LINK_MINECRAFT = "https://www.minecraft.net/zh-hans"
LINK_GITHUB = "https://github.com/Dreamtell/Minecraft-Migration-Tool"
LINK_MCMOD = "https://www.mcmod.cn/"
LINK_PCL = "https://pcl.ruanmao.net/"
_LINKS = (
    ("minecraft", "🌐 Minecraft 官网", LINK_MINECRAFT, ("#43a047", "#66bb6a")),
    ("mcmod", "📚 MC百科", LINK_MCMOD, ("#2e7d32", "#43a047")),
    ("pcl", "🚀 PCL 启动器", LINK_PCL, ("#1565c0", "#42a5f5")),
    ("github", "🐙 GitHub 仓库", LINK_GITHUB, ("#455a64", "#78909c")),
)

# 分区配色（ui/mw_common.py 的 _SECTION_STYLE 同值）
SECTION_COLORS = {
    "look": "#8e24aa", "migrate": "#7e57c2", "tags": "#00897b", "extra": "#3949ab",
    "view": "#00838f", "buttons": "#00acc1", "close": "#fb8c00", "links": "#43a047",
}


def extra_status_text(lines, source):
    """「默认携带的目录」那行状态（同 Tk 的 _extra_list_status）。"""
    entries = [x.strip() for x in (lines or []) if str(x).strip()]
    source = str(source or "").strip()
    if not entries:
        return tr("当前清单是空的：迁移时只带模组 / config / 存档，别的一律不动。")
    if not source:
        return i18n.trf("清单里 {n} 条；选了源整合包目录后才能显示哪些实际存在。",
                        n=len(entries))
    base = Path(source)
    found, missing = [], []
    for e in entries:
        try:
            ok = (base / e.replace("\\", "/").rstrip("/")).exists()
        except Exception:
            ok = False
        (found if ok else missing).append(e)
    text = i18n.trf("清单里 {n} 条，源目录里找得到 {m} 条", n=len(entries), m=len(found))
    if missing:
        text += i18n.trf("（找不到：{names}{more}）", names="、".join(missing[:4]),
                         more=tr(" 等") if len(missing) > 4 else "")
    else:
        text += tr("，这次都会带上 ✅")
    return text


def ask_close_action(parent):
    """点 ❌ 时问一次：收进托盘 / 直接退出 / 取消。返回 (action|None, remember)。"""
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(tr("关闭窗口"))
    v = QtWidgets.QVBoxLayout(dlg)
    v.addWidget(QtWidgets.QLabel(tr("要把程序收进系统托盘继续运行,还是直接退出?")))
    remember = QtWidgets.QCheckBox(tr("记住我的选择,以后不再询问"))
    v.addWidget(remember)
    row = QtWidgets.QHBoxLayout()
    btn_tray = QtWidgets.QPushButton(tr("收进托盘继续运行"))
    btn_exit = QtWidgets.QPushButton(tr("直接退出"))
    btn_cancel = QtWidgets.QPushButton(tr("取消"))
    for b in (btn_tray, btn_exit, btn_cancel):
        row.addWidget(b)
    v.addLayout(row)
    result = {"action": None}

    def pick(action):
        result["action"] = action
        dlg.accept()

    btn_tray.clicked.connect(lambda: pick("tray"))
    btn_exit.clicked.connect(lambda: pick("exit"))
    btn_cancel.clicked.connect(dlg.reject)
    set_button_kind(btn_tray, "add")
    set_button_kind(btn_exit, "danger")
    set_button_kind(btn_cancel, "neutral")
    apply_button_styles(dlg)
    btn_tray.setDefault(True)
    dlg.exec()
    return result["action"], remember.isChecked()


# 页签条参数：和 Tk 的 ui/rounded_tabs.py 逐项对过（改一边记得改另一边）
_TAB_BAR_H = 44          # 标签条高度
_TAB_PILL_H = 34         # 药丸高度
_TAB_PILL_Y = 5          # 药丸上边距
_TAB_PAD_X = 22          # 药丸左右内边距
_TAB_GAP = 4             # 药丸之间的间隙
_TAB_RADIUS = 12         # 药丸圆角
_TAB_LEFT = 6            # 第一个药丸的左边距（Tk _layout_bar 里写死的 x = 6）
_TAB_SLIDE_MS = 170      # 选中块滑动时长
_PAGE_SLIDE_MS = 150     # 页面滑入时长
_PAGE_SLIDE_DY = 10      # 页面从下方 10px 滑到位（Tk 的 place(y=10)）


def _lighten(color, amount=40):
    """整体调亮（同 Tk utils/helpers.lighten_color，页签悬停用）。"""
    c = QtGui.QColor(color)
    return QtGui.QColor(min(255, c.red() + amount),
                        min(255, c.green() + amount),
                        min(255, c.blue() + amount))


class _PillTabBar(QtWidgets.QTabBar):
    """圆角药丸页签条（对齐 Tk 的 ui/rounded_tabs.RoundedTabs 的标签条）。

    QTabBar 默认是直角标签，样式表也做不出「选中块滑过去」，所以整条自绘：
      · 未选中底 button_bg、悬停 lighten(button_bg)、选中 card_sel_bar + 白字；
      · 药丸高 34、圆角 12、左右内边距 22、间隙 4，整条高 44；
      · 切页时选中块 170ms OutCubic 滑到新位置（参数同 Tk）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setUsesScrollButtons(False)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedHeight(_TAB_BAR_H)
        f = self.font()
        f.setPointSizeF(10.0)              # 同 ui/qt_tabs.PillTabs（Tk 那边是 9 号粗体）
        f.setBold(True)
        self.setFont(f)
        self._hover = -1
        self._sel_rect = QtCore.QRectF()
        self._theme = {}
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setDuration(_TAB_SLIDE_MS)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.currentChanged.connect(self._slide_to_current)
        self.set_theme({})

    # ---- 配色 ----
    def set_theme(self, theme):
        th = dict(theme or {})
        self._theme = th
        self._bg = th.get("bg") or "#f0f0f0"
        self._fg = th.get("fg") or "#000000"
        base = QtGui.QColor(self._bg)
        深 = base.isValid() and (0.299 * base.red() + 0.587 * base.green()
                                + 0.114 * base.blue()) < 140
        base = th.get("button_bg") or ("#3a3a3a" if 深 else "#e0e0e0")
        self._pill = QtGui.QColor(base)          # 未选中：button_bg
        self._hover_pill = _lighten(base)        # 悬停：lighten(button_bg)
        sel = th.get("card_sel_bar") or th.get("switch_on") or th.get("accent_bg") or "#2f7fd1"
        self._sel = QtGui.QColor(sel)
        c = self._sel
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() if c.isValid() else 0
        self._sel_fg = "#202020" if lum > 170 else "#ffffff"     # 同 Tk 的 _sel_fg()
        if not self._anim.state() == QtCore.QAbstractAnimation.Running:
            self._sel_rect = self._current_pill()
        self.update()

    # ---- 几何 ----
    def tabSizeHint(self, index):
        fm = QtGui.QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.tabText(index)) + _TAB_PAD_X * 2 + _TAB_GAP
        if index == 0:
            w += _TAB_LEFT
        return QtCore.QSize(w, _TAB_PILL_H)

    def _pill_rect(self, i):
        r = self.tabRect(i)
        x, w = r.x(), r.width() - _TAB_GAP
        if i == 0:
            x += _TAB_LEFT                  # Tk 的第一个药丸从 x = 6 起
            w -= _TAB_LEFT
        return QtCore.QRectF(x, _TAB_PILL_Y, max(8, w), _TAB_PILL_H)

    def _current_pill(self):
        i = self.currentIndex()
        if 0 <= i < self.count():
            return self._pill_rect(i)
        return QtCore.QRectF()

    def _on_anim(self, 值):
        try:
            self._sel_rect = QtCore.QRectF(值)
        except Exception:
            pass
        self.update()

    def _slide_to_current(self, index):
        """选中块滑过去；第一次/还没露出来就直接摆好（同 Tk 的 select(animate=False)）。"""
        if index < 0 or index >= self.count():
            return
        目标 = self._pill_rect(index)
        if self._sel_rect.isEmpty() or not self.isVisible():
            self._anim.stop()
            self._sel_rect = 目标
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(QtCore.QRectF(self._sel_rect))
        self._anim.setEndValue(QtCore.QRectF(目标))
        self._anim.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._anim.state() == QtCore.QAbstractAnimation.Running:
            self._sel_rect = self._current_pill()

    # ---- 事件 ----
    def mouseMoveEvent(self, e):
        i = self.tabAt(e.position().toPoint())
        if i != self._hover:
            self._hover = i
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(e)

    # ---- 绘制 ----
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        p.fillRect(self.rect(), QtGui.QColor(self._bg))
        p.setFont(self.font())
        cur = self.currentIndex()
        p.setPen(QtCore.Qt.NoPen)
        # 先铺所有未选中的药丸（选中的那块单独按动画位置画，滑动时才能"飞"过去）
        for i in range(self.count()):
            if i == cur:
                continue
            p.setBrush(self._hover_pill if i == self._hover else self._pill)
            p.drawRoundedRect(self._pill_rect(i), _TAB_RADIUS, _TAB_RADIUS)
        if cur >= 0 and not self._sel_rect.isEmpty():
            p.setBrush(self._sel)
            p.drawRoundedRect(self._sel_rect, _TAB_RADIUS, _TAB_RADIUS)
        # 字全部压在药丸上面（边画边写的话，滑动中的选中块会盖住隔壁的字）
        for i in range(self.count()):
            p.setPen(QtGui.QColor(self._sel_fg if i == cur else self._fg))
            p.drawText(self._pill_rect(i), QtCore.Qt.AlignCenter, self.tabText(i))
        p.end()


class SettingsDialog(QtWidgets.QDialog):
    """五页设置窗。改任何一项都立刻经主窗口落地（同 Tk），没有确定/取消。"""

    _cf_test_done = QtCore.Signal(bool, str)
    _ROLLBACK_ORDER = ("mods", "config", "saves", "files", "extras")

    def __init__(self, parent, config=None):
        super().__init__(parent)
        self.setWindowTitle(tr("⚙ 设置"))
        cfg = dict(config if config is not None else getattr(parent, "config", {}) or {})
        self._cfg = cfg
        self._parent_win = parent
        self._theme = theme_for(self, str(cfg.get("theme", "light") or "light"))
        self._building = True                 # 构建期间不要触发「改了就落地」
        self._applying = False                # 防止 _live → 控件回写 → _live 递归
        fit_window(self, 660, 460, 760, 620)

        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setTabBar(_PillTabBar(self._tabs))     # 圆角药丸页签（同 Tk）
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._tabs, 1)
        bar = QtWidgets.QWidget()
        bar_lay = QtWidgets.QHBoxLayout(bar)
        bar_lay.setContentsMargins(14, 8, 14, 10)
        bar_lay.addStretch(1)
        self.close_btn = QtWidgets.QPushButton(tr("✕ 关闭"))
        set_button_kind(self.close_btn, "danger")
        self.close_btn.clicked.connect(self.close)
        bar_lay.addWidget(self.close_btn)
        root.addWidget(bar)

        self._build_look_tab()
        self._build_migrate_tab()
        self._build_view_tab()
        self._build_buttons_tab()
        self._build_close_tab()
        self._building = False
        apply_button_styles(self, self._theme)
        self._tab_style()
        self._page_anim = None
        self._tabs.currentChanged.connect(self._on_tab_changed)

    # ================= 公共小工具 =================
    def _page(self):
        """一页 = 滚动区 + 竖排容器。"""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        holder = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(holder)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addStretch(1)
        scroll.setWidget(holder)
        return scroll, lay

    def _add_section(self, lay, key, icon, title):
        sec = SettingsSection(icon, title, SECTION_COLORS.get(key, "#607d8b"), self._theme)
        lay.insertWidget(lay.count() - 1, sec)
        return sec

    def _switch(self, sec, title, desc, key, default=False, spacing=0, after=None):
        sw = SwitchRow(self._theme, tr(title), tr(desc) if desc else "", compact=True,
                       accent="switch_on")
        sw.setChecked(bool(self._cfg.get(key, default)))
        sw.toggled = getattr(sw, "toggled", None)
        sec.add(sw, spacing)
        # SwitchRow 没有 toggled 信号：包一层。after 用于「按完还要顺手刷新别处」的开关
        # （比如改名开关要更新下面的效果预览行，同 Tk 的 _toggle_rename_marker）。
        sw.toggle = self._wrap_toggle(sw, key, after)
        return sw

    def _wrap_toggle(self, sw, key, after=None):
        orig = sw.toggle

        def toggle():
            if sw.isEnabled():
                orig()
                self._live({key: sw.isChecked()})
                if after is not None:
                    after()
        return toggle

    def _live(self, values):
        """把一项改动立刻落到主窗口 + 配置（Tk 的「改完即生效」）。"""
        if self._building or self._applying:
            return
        self._applying = True
        try:
            self._cfg.update(values)
            fn = getattr(self._parent_win, "_apply_settings", None)
            if callable(fn):
                fn(self._values_full(), silent=True)
        finally:
            self._applying = False

    def _muted(self, sec, text, spacing=0):
        lab = MutedLabel(text, self._theme)
        sec.add(lab, spacing)
        return lab

    def _tab_style(self):
        """页签条配色/尺寸都交给自绘的 _PillTabBar（样式表做不出滑动的选中块）。

        Tk 的页签条：未选中 button_bg、悬停 lighten(button_bg)、选中 card_sel_bar 白字，
        药丸高 34 / 圆角 12 / 左右内边距 22，整条高 44。
        """
        bar = self._tabs.tabBar()
        if isinstance(bar, _PillTabBar):
            bar.set_theme(self._theme)
        self._tabs.setStyleSheet("QTabWidget::pane { border: none; }")

    def _on_tab_changed(self, index):
        """切页时页面从下方 10px 滑到位（150ms OutCubic），同 Tk 的 _slide_page。"""
        if self._building or index < 0:
            return
        page = self._tabs.widget(index)
        if page is None:
            return
        anim = self._page_anim
        if anim is None:
            anim = QtCore.QPropertyAnimation(self)
            anim.setDuration(_PAGE_SLIDE_MS)
            anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            self._page_anim = anim
        anim.stop()
        page.move(0, _PAGE_SLIDE_DY)          # 先压到下方 10px，再滑回 0
        anim.setTargetObject(page)
        anim.setPropertyName(b"pos")
        anim.setStartValue(QtCore.QPoint(0, _PAGE_SLIDE_DY))
        anim.setEndValue(QtCore.QPoint(0, 0))
        anim.start()

    # ================= 页 1：外观与启动 =================
    def _build_look_tab(self):
        page, lay = self._page()
        sec = self._add_section(lay, "look", "🎨", tr("外观与启动"))

        self.theme_seg = SegmentedControl(
            [(tr("浅色"), "light"), (tr("深色"), "dark")],
            str(self._cfg.get("theme", "light") or "light"), self._theme)
        self.theme_seg.valueChanged.connect(lambda v: self._live({"theme": v}))
        row, _ = make_row(tr("主题："), self.theme_seg, self._theme)
        sec.add(row)

        self.fx_seg = SegmentedControl(
            [(tr(fx.LABELS[lv]), lv) for lv in fx.LEVELS],
            fx.normalize(self._cfg.get("fx_level")), self._theme)
        self.fx_seg.valueChanged.connect(lambda v: self._live({"fx_level": v}))
        row, _ = make_row(tr("特效强度："), self.fx_seg, self._theme)
        sec.add(row)

        # 开始前倒计时：标题行 + 说明 + 整宽滑杆（同 Tk 的三段结构）
        head = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hsl = QtWidgets.QLabel(tr("开始前倒计时"))
        f = hsl.font()
        f.setPointSizeF(9.0)
        hsl.setFont(f)
        hl.addWidget(hsl)
        hl.addStretch(1)
        hl.addWidget(MutedLabel(tr("秒（1–10，点开始后再点一下可取消）"), self._theme))
        sec.add(head, spacing=6)
        try:
            cd = max(1, min(10, int(self._cfg.get("start_countdown", 5))))
        except Exception:
            cd = 5
        self.countdown_slider = SliderRow(1, 10, cd, self._theme)
        self.countdown_slider.valueChanged.connect(lambda v: self._live({"start_countdown": v}))
        sec.add(self.countdown_slider)

        self.lang_seg = SegmentedControl(
            [(tr("中文"), "zh", "cn"), ("English", "en", "us")],
            i18n.language(), self._theme)
        self.lang_seg.valueChanged.connect(lambda v: self._change_language(v))
        row, _ = make_row(tr("界面语言："), self.lang_seg, self._theme)
        sec.add(row, spacing=6)

        # 音效：开关 + 试听同一行
        sfx_row = QtWidgets.QWidget()
        sl = QtWidgets.QHBoxLayout(sfx_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(12)
        self.sfx_switch = SwitchRow(self._theme, tr("🔊 按钮音效（悬停 / 按下）"), "",
                                    compact=True, accent="switch_on")
        self.sfx_switch.setChecked(bool(self._cfg.get("sfx_on", False)))
        self.sfx_switch.toggle = self._wrap_toggle(self.sfx_switch, "sfx_on")
        sl.addWidget(self.sfx_switch)
        self.sfx_test_btn = QtWidgets.QPushButton(tr("▶ 试听"))
        set_button_kind(self.sfx_test_btn, "neutral")
        self.sfx_test_btn.clicked.connect(self._test_sfx)
        sl.addWidget(self.sfx_test_btn)
        sl.addStretch(1)
        sec.add(sfx_row, spacing=6)
        self._sfx_hover = str(self._cfg.get("sfx_hover") or "")
        self._sfx_press = str(self._cfg.get("sfx_press") or "")

        try:
            fs = max(40, min(96, int(self._cfg.get("net_float_size", 56))))
        except Exception:
            fs = 56
        self.float_size_seg = SegmentedControl(
            [(tr("小"), "44"), (tr("中"), "56"), (tr("大"), "72")], str(fs), self._theme)
        self.float_size_seg.valueChanged.connect(
            lambda v: self._live({"net_float_size": int(v)}))
        row, _ = make_row(tr("延迟浮标大小："), self.float_size_seg, self._theme)
        sec.add(row, spacing=6)

        self.float_shadow_switch = self._switch(sec, "浮标带阴影", "", "net_float_shadow", True, 6)
        self.float_dock_switch = self._switch(
            sec, "贴边时收纳浮标（只露一条边，鼠标移上去展开）", "", "net_float_dock", True, 6)

        cfg_row = QtWidgets.QWidget()
        cl = QtWidgets.QHBoxLayout(cfg_row)
        cl.setContentsMargins(0, 0, 0, 0)
        self.open_cfg_btn = QtWidgets.QPushButton(tr("📝 打开配置文件"))
        set_button_kind(self.open_cfg_btn, "neutral")
        self.open_cfg_btn.clicked.connect(self.open_config_file)
        cl.addWidget(self.open_cfg_btn)
        cl.addWidget(MutedLabel(tr("改完要重启程序才生效"), self._theme), 1)
        sec.add(cfg_row, spacing=10)

        self.blank_exit_switch = self._switch(
            sec, "点空白处取消清单文本框焦点", "关闭后只能用编辑开关自己关", "blank_exit_edit", True, 10)
        self.msg_outside_switch = self._switch(
            sec, "点消息中心以外就收回", "关闭后只有 ✕ / Esc / 再点铃铛能收",
            "msg_click_outside_close", False, 10)
        self.toast_switch = self._switch(
            sec, "显示消息提示（右下角弹出的小卡片）", "", "toast_on", True, 6)

        links = self._add_section(lay, "links", "🔗", tr("快捷链接"))
        link_row = QtWidgets.QWidget()
        ll = QtWidgets.QHBoxLayout(link_row)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        self.link_buttons = {}
        for key, label, url, _colors in _LINKS:
            btn = QtWidgets.QPushButton(tr(label))
            btn.setFixedHeight(28)
            btn.setMinimumWidth(150)
            btn.setToolTip(url)
            btn.clicked.connect(lambda _=False, u=url: self.open_link(u))
            ll.addWidget(btn)
            self.link_buttons[key] = btn
        ll.addStretch(1)
        links.add(link_row)
        links.add(MutedLabel(LINK_GITHUB + tr(
            "\nMC百科：中文模组资料库　·　PCL：整合包启动器　·　欢迎反馈问题或提交建议。"),
            self._theme))
        self._tabs.addTab(page, tr("🎨 外观与启动"))

    # ================= 页 2：迁移与分类 =================
    def _build_migrate_tab(self):
        page, lay = self._page()
        sec = self._add_section(lay, "migrate", "🏷️", tr("迁移行为"))

        self.rename_switch = self._switch(
            sec, "复制过去的模组加标记前缀（方便在目标 mods 里一眼认出）", "",
            "rename_migrated_mods", False, after=self._refresh_marker_preview)
        self.marker_seg = SegmentedControl(
            [(m, m) for m in ("★", "☆", "▶", "◆", "●", "✦", "✚", "【新】", "NEW_")],
            str(self._cfg.get("rename_marker", "★") or "★"), self._theme, height=28, min_seg_w=44)
        self.marker_seg.valueChanged.connect(self._on_marker_changed)
        row, _ = make_row(tr("标记："), self.marker_seg, self._theme)
        sec.add(row, spacing=6)
        self.marker_preview = MutedLabel("", self._theme)
        sec.add(self.marker_preview)
        self._refresh_marker_preview()

        sec.add(MutedLabel(tr("回滚时默认还原："), self._theme), spacing=8)
        rollback_items = (("mods", tr("模组（mods/）")), ("config", tr("配置（config/）")),
                          ("saves", tr("存档（saves/）")), ("files", tr("根目录文件（options.txt 等）")),
                          ("extras", tr("「其它文件」清单里的条目")))
        saved = self._cfg.get("rollback_parts")
        saved = set(saved) if isinstance(saved, (list, tuple)) and saved else {k for k, _ in rollback_items}
        if not saved:
            saved = {"mods"}
        self._rollback_switches = {}
        for key, text in rollback_items:
            sw = SwitchRow(self._theme, text, "", compact=True, accent="switch_on")
            sw.setChecked(key in saved)
            sw.toggle = self._wrap_rollback_toggle(sw, key)
            sec.add(sw)
            self._rollback_switches[key] = sw
        sec.add(MutedLabel(tr("注：回滚对话框里勾「记住我的选择」会用那一次的选择覆盖这里。"),
                           self._theme), spacing=2)

        self.confirm_switch = self._switch(
            sec, "正式迁移前再确认一次", "", "confirm_migrate", True, 8)
        sec.add(MutedLabel(tr("迁移历史最多保存　份（1–15，超出的删最旧）"), self._theme), spacing=8)
        try:
            hl_val = int(history_store.get_limit())
        except Exception:
            hl_val = 10
        self.history_slider = SliderRow(history_store.MIN_LIMIT, history_store.MAX_LIMIT,
                                        hl_val, self._theme)
        self.history_slider.valueChanged.connect(lambda v: self._live({"history_limit": v}))
        sec.add(self.history_slider)

        step_head = QtWidgets.QWidget()
        shl = QtWidgets.QHBoxLayout(step_head)
        shl.setContentsMargins(0, 0, 0, 0)
        shl.addWidget(MutedLabel(tr("平滑滚动速度　档（1 最慢 – 5 最快，3 = 原速）"), self._theme), 1)
        sec.add(step_head, spacing=8)
        self.scroll_stepper = StepperRow(1, 5, self._scroll_speed_value(), self._theme)
        self.scroll_stepper.valueChanged.connect(lambda v: self._live({"scroll_speed": v}))
        sec.add(self.scroll_stepper)

        # ---- 模组分类标签 ----
        tags = self._add_section(lay, "tags", "🌐", tr("模组分类标签"))
        self.tags_switch = self._switch(
            tags, "联网获取真实分类（Modrinth；默认关闭）", "", "online_tags", False)
        tags.add(MutedLabel(tr(
            "关闭时按关键词推测（标注“推测”的就是它）。开启后扫描会在后台联网查询，结果缓存到本地；"
            "断网或匹配不到时自动沿用推测结果。"), self._theme), spacing=2)
        self.tag_cache_label = MutedLabel("", self._theme)
        tags.add(self.tag_cache_label)
        self._update_tag_cache_label()

        tags.add(MutedLabel(tr("CurseForge API Key（可选）"), self._theme), spacing=8)
        key_row = QtWidgets.QWidget()
        kl = QtWidgets.QHBoxLayout(key_row)
        kl.setContentsMargins(0, 0, 0, 0)
        self.cf_key_edit = QtWidgets.QLineEdit()
        self.cf_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        kl.addWidget(self.cf_key_edit, 1)
        self.cf_save_btn = QtWidgets.QPushButton(tr("保存"))
        self.cf_test_btn = QtWidgets.QPushButton(tr("测试"))
        self.cf_clear_btn = QtWidgets.QPushButton(tr("清除"))
        set_button_kind(self.cf_save_btn, "add")
        set_button_kind(self.cf_test_btn, "neutral")
        set_button_kind(self.cf_clear_btn, "danger")
        for b in (self.cf_save_btn, self.cf_test_btn, self.cf_clear_btn):
            b.setAutoDefault(False)
            kl.addWidget(b)
        self.cf_save_btn.clicked.connect(self._save_cf_key)
        self.cf_test_btn.clicked.connect(self._test_cf_key)
        self.cf_clear_btn.clicked.connect(self._clear_cf_key)
        tags.add(key_row)
        self.cf_status_label = MutedLabel("", self._theme)
        tags.add(self.cf_status_label)
        self._cf_testing = False
        self._cf_test_done.connect(self._on_cf_test_done)
        self._update_cf_status()
        tags.add(MutedLabel(tr(
            "不填也能用：程序内置了一把共享 Key 时直接用它；填了就用你自己的配额（更稳，共享那把可能被限流）。"
            "\nKey 只存在 %s，不进主配置、不进日志。"
            "\n状态行只显示末 4 位和指纹，贴图报障不会泄露整把 Key。" % secrets.where_text()),
            self._theme), spacing=4)

        # ---- 默认携带的目录 ----
        extra = self._add_section(lay, "extra", "📦", tr("默认携带的目录"))
        extra.add(MutedLabel(tr(
            "迁移只带「其它文件」清单里列出的东西（还有模组 / config / 存档这三个清单）。"
            "清单是唯一依据：不在这里的一律不动。常用目录在「其它文件」页用「＋ 常用目录」加。"),
            self._theme))
        self.extra_status_label = MutedLabel(self._extra_status_from_parent(), self._theme)
        extra.add(self.extra_status_label)
        self._tabs.addTab(page, tr("🚚 迁移与分类"))

    def _scroll_speed_value(self):
        try:
            v = int(self._cfg.get("scroll_speed", 3))
        except Exception:
            v = 3
        return max(1, min(5, v))

    def _on_marker_changed(self, v):
        """换标记符号：立刻生效，并刷新下面的效果预览（同 Tk 的 _set_rename_marker）。"""
        self._live({"rename_marker": v})
        self._refresh_marker_preview()

    def _refresh_marker_preview(self):
        on = self.rename_switch.isChecked() if hasattr(self, "rename_switch") else False
        marker = self.marker_seg.value() if hasattr(self, "marker_seg") else "★"
        state = tr("已开启") if on else tr("未开启")
        # 关着的时候也把符号画出来 —— 同 Tk：「方便先挑」
        self.marker_preview.setText(
            trp("效果（{0}）：{1} create-1.20.1-6.0.9.jar", state, marker))

    def _wrap_rollback_toggle(self, sw, key):
        orig = sw.toggle

        def toggle():
            if not sw.isEnabled():
                return
            if sw.isChecked() and sum(1 for s in self._rollback_switches.values() if s.isChecked()) <= 1:
                return                      # 至少留一项
            orig()
            picked = [k for k, s in self._rollback_switches.items() if s.isChecked()]
            self._live({"rollback_parts": picked or ["mods"]})
        return toggle

    # ================= 页 3：放大查看 =================
    def _build_view_tab(self):
        page, lay = self._page()
        sec = self._add_section(lay, "view", "🗂", tr("放大查看窗口"))

        self.pyside_switch = SwitchRow(
            self._theme, tr("启用 PySide6 窗口"),
            tr("关掉 = 纯 Tk 模式：主进程不再加载 Qt（重启后生效）"),
            compact=True, accent="switch_on")
        self.pyside_switch.setChecked(bool(self._cfg.get("qt_enabled", True)))
        self.pyside_switch.toggle = self._wrap_toggle(self.pyside_switch, "qt_enabled")
        sec.add(self.pyside_switch)
        sec.add(MutedLabel(self._pyside_state_text(), self._theme))

        sec.add(MutedLabel(tr("主界面用哪个实现（重启后生效）："), self._theme), spacing=8)
        self.backend_seg = SegmentedControl(
            [(tr("PySide6 主界面（当前）"), "qt"), (tr("经典 Tk 主界面"), "tk")],
            str(self._cfg.get("main_window_backend", "qt") or "qt"), self._theme, min_seg_w=140)
        self.backend_seg.valueChanged.connect(lambda v: self._live({"main_window_backend": v}))
        sec.add(self.backend_seg)
        sec.add(MutedLabel(tr("※ 切换主界面实现在下次启动后生效"), self._theme))

        # 两个列表窗口固定用 Qt 卡片视图（Tk 版同文案）
        sec.add(MutedLabel(tr("放大查看：固定使用 Qt 卡片视图"), self._theme), spacing=8)
        sec.add(MutedLabel(tr("差异扫描：固定使用 Qt 卡片视图"), self._theme))
        sec.add(MutedLabel(tr(
            "两个列表窗口优先使用 Qt 卡片视图，不提供表格 / 卡片切换；PySide6 不可用时自动回退到经典窗口。"),
            self._theme))
        self._tabs.addTab(page, tr("🗂 放大查看"))

    def _pyside_state_text(self):
        """PySide6 可用性（不能 import ui.mw_qt：那个模块顶层 import tkinter，Qt 进程里不能碰）。"""
        import importlib.util
        ok = importlib.util.find_spec("PySide6") is not None
        state = tr("可用") if ok else tr("不可用")
        why = "" if ok else tr("装好 PySide6 后可切到 Qt 版窗口：pip install PySide6")
        return trp("PySide6 当前{state}。{why}", state, why)

    # ================= 页 4：界面按钮 =================
    def _build_buttons_tab(self):
        page, lay = self._page()
        sec = self._add_section(lay, "buttons", "🧩", tr("界面按钮（勾选显示 / 上下调整顺序）"))
        try:
            from ui.qt_main.buttons import DEFAULT_ORDER, GROUPS, LABELS, LOCKED
        except Exception:
            DEFAULT_ORDER, GROUPS, LABELS, LOCKED = {}, (), {}, {"start"}
        self._btn_groups = GROUPS
        self._btn_labels = LABELS
        self._btn_locked = set(LOCKED)
        hidden = set(self._cfg.get("buttons_hidden") or [])
        order_cfg = self._cfg.get("button_order") or {}
        self._btn_order = {}
        for gkey, glabel in GROUPS:
            saved = [k for k in (order_cfg.get(gkey) or []) if k in (DEFAULT_ORDER.get(gkey) or [])]
            rest = [k for k in (DEFAULT_ORDER.get(gkey) or []) if k not in saved]
            self._btn_order[gkey] = saved + rest

        self.btn_tree = QtWidgets.QTreeWidget()
        self.btn_tree.setColumnCount(2)
        self.btn_tree.setHeaderLabels([tr("按钮"), tr("状态")])
        self.btn_tree.setRootIsDecorated(True)
        self.btn_tree.setUniformRowHeights(True)
        self.btn_tree.itemDoubleClicked.connect(lambda *_: self._toggle_btn_visible())
        sec.add(self.btn_tree)
        self._fill_btn_tree(hidden)

        op_row = QtWidgets.QWidget()
        ol = QtWidgets.QHBoxLayout(op_row)
        ol.setContentsMargins(0, 0, 0, 0)
        for text, fn, kind in ((tr("显示 / 隐藏"), self._toggle_btn_visible, "add"),
                               (tr("↑ 上移"), lambda: self._move_btn(-1), "neutral"),
                               (tr("↓ 下移"), lambda: self._move_btn(1), "neutral"),
                               (tr("恢复默认"), self._reset_btns, "danger")):
            b = QtWidgets.QPushButton(text)
            set_button_kind(b, kind)
            b.clicked.connect(fn)
            ol.addWidget(b)
        ol.addStretch(1)
        sec.add(op_row, spacing=6)
        sec.add(MutedLabel(tr("双击一行也能切换显示/隐藏；顺序只在同一排内调整。"), self._theme), spacing=2)
        sec.add(MutedLabel(tr(
            "主界面的按钮改完立刻生效；放大查看 / 日志放大查看这些窗口里的按钮，是下次打开那个窗口时生效。"),
            self._theme))
        self._tabs.addTab(page, tr("🔘 界面按钮"))

    def _fill_btn_tree(self, hidden):
        self.btn_tree.clear()
        for gkey, glabel in self._btn_groups:
            top = QtWidgets.QTreeWidgetItem([glabel, ""])
            top.setFlags(QtCore.Qt.ItemIsEnabled)
            for key in self._btn_order.get(gkey, []):
                label = self._btn_labels.get(key, key)
                if key in self._btn_locked:
                    status = tr("显示（不可隐藏）")
                else:
                    status = tr("隐藏") if key in hidden else tr("显示")
                it = QtWidgets.QTreeWidgetItem([label, status])
                it.setData(0, QtCore.Qt.UserRole, (gkey, key))
                top.addChild(it)
            self.btn_tree.addTopLevelItem(top)
            top.setExpanded(True)

    def _selected_btn(self):
        it = self.btn_tree.currentItem()
        if it is None:
            return None, None
        data = it.data(0, QtCore.Qt.UserRole)
        return data if data else (None, None)

    def _toggle_btn_visible(self):
        gkey, key = self._selected_btn()
        if not key or key in self._btn_locked:
            return
        hidden = list(self._cfg.get("buttons_hidden") or [])
        if key in hidden:
            hidden.remove(key)
        else:
            hidden.append(key)
        self._cfg["buttons_hidden"] = hidden
        self._fill_btn_tree(set(hidden))
        self._live({"buttons_hidden": hidden})

    def _move_btn(self, delta):
        gkey, key = self._selected_btn()
        if not gkey or not key:
            return
        order = list(self._btn_order.get(gkey) or [])
        if key not in order:
            return
        i = order.index(key)
        j = i + delta
        if not (0 <= j < len(order)):
            return
        order[i], order[j] = order[j], order[i]
        self._btn_order[gkey] = order
        self._cfg["button_order"] = dict(self._btn_order)
        hidden = set(self._cfg.get("buttons_hidden") or [])
        self._fill_btn_tree(hidden)
        self._live({"button_order": dict(self._btn_order)})

    def _reset_btns(self):
        self._btn_order = {g: list(DEFAULT_ORDER.get(g) or []) for g, _ in self._btn_groups} \
            if isinstance(DEFAULT_ORDER, dict) else {}
        self._cfg["buttons_hidden"] = []
        self._cfg["button_order"] = dict(self._btn_order)
        self._fill_btn_tree(set())
        self._live({"buttons_hidden": [], "button_order": dict(self._btn_order)})

    # ================= 页 5：后台与退出 =================
    def _build_close_tab(self):
        page, lay = self._page()
        sec = self._add_section(lay, "close", "🚪", tr("关闭与后台"))
        sec.add(MutedLabel(tr("关闭窗口时："), self._theme))
        self.close_cards = OptionCards(
            [("tray", tr("收进系统托盘"), tr("程序继续在后台跑")),
             ("exit", tr("直接退出程序"), tr("结束整个程序")),
             ("ask", tr("每次问我"), tr("关闭时弹一次询问"))],
            str(self._cfg.get("close_action", "ask") or "ask"), self._theme)
        self.close_cards.valueChanged.connect(lambda v: self._live({"close_action": v}))
        sec.add(self.close_cards)
        self.silent_switch = self._switch(
            sec, "后台静默执行任务（不弹进度/结果窗口，完成后系统通知）", "",
            "silent_background", False, 8)
        self._tabs.addTab(page, tr("🚪 后台与退出"))

    # ================= 取值（保持旧接口，测试与 _apply_settings 都靠它） =================
    def values(self):
        level = self.fx_seg.value() if hasattr(self, "fx_seg") else fx.normalize(self._cfg.get("fx_level"))
        d = fx.derived(level)
        hidden = list(self._cfg.get("buttons_hidden") or [])
        order = dict(self._cfg.get("button_order") or {})
        return {
            "theme": self.theme_seg.value() if hasattr(self, "theme_seg") else self._cfg.get("theme", "light"),
            "fx_level": level,
            "splash": d["splash"],
            "float_edge_fx": d["float_edge_fx"],
            "start_btn_feel": d["start_btn_feel"],
            "language": self.lang_seg.value() if hasattr(self, "lang_seg") else i18n.language(),
            "dry_run": bool(getattr(self._parent_win, "dry_run", self._cfg.get("dry_run", True))),
            "overwrite": bool(getattr(self._parent_win, "overwrite_mods", self._cfg.get("overwrite", False))),
            "extra_conflict": str(getattr(self._parent_win, "extra_conflict",
                                          self._cfg.get("extra_conflict", "overwrite"))),
            "confirm_migrate": self.confirm_switch.isChecked() if hasattr(self, "confirm_switch") else True,
            "start_countdown": self.countdown_slider.value() if hasattr(self, "countdown_slider") else 5,
            "rename_migrated_mods": self.rename_switch.isChecked() if hasattr(self, "rename_switch") else False,
            "rename_marker": self.marker_seg.value() if hasattr(self, "marker_seg") else "★",
            "silent_background": self.silent_switch.isChecked() if hasattr(self, "silent_switch") else False,
            "net_float_on": bool(getattr(self._parent_win, "config", {}).get("net_float_on", False)),
            "net_float_size": int(self.float_size_seg.value()) if hasattr(self, "float_size_seg") else 56,
            "net_float_dock": self.float_dock_switch.isChecked() if hasattr(self, "float_dock_switch") else True,
            "net_float_shadow": self.float_shadow_switch.isChecked() if hasattr(self, "float_shadow_switch") else True,
            "msg_click_outside_close": self.msg_outside_switch.isChecked()
            if hasattr(self, "msg_outside_switch") else False,
            "blank_exit_edit": self.blank_exit_switch.isChecked() if hasattr(self, "blank_exit_switch") else True,
            "scroll_speed": self.scroll_stepper.value() if hasattr(self, "scroll_stepper") else 3,
            "online_tags": self.tags_switch.isChecked() if hasattr(self, "tags_switch") else False,
            "rollback_parts": [k for k in self._ROLLBACK_ORDER
                               if hasattr(self, "_rollback_switches") and
                               self._rollback_switches[k].isChecked()] or ["mods"],
            "sfx_on": self.sfx_switch.isChecked() if hasattr(self, "sfx_switch") else False,
            "sfx_hover": self._sfx_hover,
            "sfx_press": self._sfx_press,
            "toast_on": self.toast_switch.isChecked() if hasattr(self, "toast_switch") else True,
            "close_action": self.close_cards.value() if hasattr(self, "close_cards") else "ask",
            "main_window_backend": self.backend_seg.value() if hasattr(self, "backend_seg") else "qt",
            "qt_enabled": self.pyside_switch.isChecked() if hasattr(self, "pyside_switch") else True,
            "buttons_hidden": hidden,
            "button_order": order,
            "history_limit": self.history_slider.value() if hasattr(self, "history_slider")
            else history_store.get_limit(),
        }

    def _values_full(self):
        return self.values()

    # ================= 语言 / 配置 / 链接（业务逻辑沿用原实现） =================
    def _change_language(self, lang):
        if lang == i18n.language():
            return
        name = tr("中文") if lang == "zh" else "English"
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle(tr("切换界面语言"))
        box.setText(trp("改成「{0}」后需要重启程序才会生效。现在重启吗？", name))
        restart = box.addButton(tr("现在重启"), QtWidgets.QMessageBox.AcceptRole)
        later = box.addButton(tr("稍后再说"), QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(later)
        box.exec()
        if box.clickedButton() is not restart:
            # 还原分段条,什么都不写
            if hasattr(self, "lang_seg"):
                self.lang_seg.setValue(i18n.language())
            return
        try:
            raw = dict(self._cfg)
            raw["language"] = lang
            from utils.config import save_raw_config
            save_raw_config(raw)
        except Exception:
            pass
        # 同 Tk：记下目标语言、写进配置，**界面保持原样**（当场翻会看到"哗"地变色），
        # 提示一句再重启。没有重启入口时别谎称"正在重启"。
        if self._restart_callable() is None:
            self._restart_app()
            return
        toast = getattr(self._parent_win, "toast", None)
        if callable(toast):
            toast(trp("🌐 界面语言已改成「{0}」，正在重启…", name), "ok")
        QtCore.QTimer.singleShot(650, self._restart_app)

    def _restart_callable(self):
        win = self._parent_win
        for name in ("_restart_for_language", "restart_app", "_restart"):
            fn = getattr(win, name, None)
            if callable(fn):
                return fn
        return None

    def _restart_app(self):
        fn = self._restart_callable()
        if fn is not None:
            try:
                fn()
                return
            except Exception:
                pass
        QtWidgets.QMessageBox.information(
            self, tr("设置"), tr("请关闭程序后重新打开，新设置才会生效。"))

    def open_link(self, url):
        """打开外部链接（同 Tk 的 PagesMixin.open_link）：成功给提示，失败要说出来。"""
        try:
            webbrowser.open(url)
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, tr("打开失败"),
                                          trp("无法打开链接：{0}", error))
            return
        toast = getattr(self._parent_win, "toast", None)
        if callable(toast):
            toast(trp("🔗 已打开链接：{0}", url), "info")

    def open_config_file(self):
        """打开配置文件所在位置(同 Tk 的 open_config_file)。"""
        import os
        import subprocess
        try:
            from utils.config import CONFIG_FILE
            path = Path(CONFIG_FILE)
            if not path.exists():
                win = self._parent_win
                fn = getattr(win, "save_config", None)
                if callable(fn):
                    fn()
            if sys.platform == "win32" and path.exists():
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "win32":
                os.startfile(str(path.parent))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path.parent)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
            win = self._parent_win
            if hasattr(win, "toast"):
                win.toast(trp("📝 已打开配置文件：{0}", path), "ok")
        except Exception as error:
            win = self._parent_win
            if hasattr(win, "log"):
                win.log(trp("❌ 打开配置文件失败：{0}", error), "ERROR")
            if hasattr(win, "toast"):
                win.toast(tr("❌ 打开配置文件失败，详见日志"), "error")

    # ---- CurseForge Key / 缓存条数 / 试听（沿用原实现） ----
    def _update_tag_cache_label(self):
        try:
            from core import mod_search
            n = mod_search.tag_cache_size()
            where = mod_search.TAG_CACHE_FILE
        except Exception:
            n, where = 0, ""
        self.tag_cache_label.setText(trp("本地分类缓存：{0} 条（{1}）", n, where))

    def _update_cf_status(self):
        try:
            text = secrets.status_text()
        except Exception as error:
            text = str(error)
        self.cf_status_label.setText(text)

    def _cf_toast(self, text, level="info"):
        win = self._parent_win
        if hasattr(win, "toast"):
            win.toast(text, level)

    def _cf_log(self, text, level="INFO"):
        win = self._parent_win
        if hasattr(win, "log"):
            win.log(text, level)

    def _save_cf_key(self):
        """把输入框里的 key 存进密钥文件（不进主配置、不进日志）。"""
        raw = self.cf_key_edit.text()
        if not secrets.clean(raw):
            self._cf_toast(tr("ℹ️ 输入框是空的：没有保存任何东西（要删掉已保存的 Key 请点「清除」）。"),
                           "warn")
            return
        if secrets.set_key(raw):
            self.cf_key_edit.clear()
            self._cf_log(trp("🔑 CurseForge API Key 已保存：{0}（指纹 {1}）",
                             secrets.mask(), secrets.fingerprint()), "INFO")
            self._cf_toast(tr("🔑 CurseForge API Key 已保存"), "ok")
        else:
            self._cf_log(trp("❌ CurseForge API Key 保存失败：写不了 {0}",
                             secrets.where_text()), "ERROR")
            self._cf_toast(tr("❌ CurseForge API Key 保存失败，详见日志"), "warn")
        self._update_cf_status()

    def _clear_cf_key(self):
        self.cf_key_edit.clear()
        if secrets.clear_key():
            msg = tr("🗑 已删除本机保存的 CurseForge API Key（联网搜索仍走 Modrinth）。")
            self._cf_log(msg, "INFO")
            self._cf_toast(msg, "ok")
        self._update_cf_status()

    def _test_cf_key(self):
        """后台线程测一次最小请求，别卡住设置窗。"""
        if self._cf_testing:
            return
        pending = secrets.clean(self.cf_key_edit.text())
        if not pending and not secrets.has_key():
            self._cf_toast(tr("ℹ️ 先在输入框里粘一个 Key，或者先「保存」一个再测。"), "warn")
            return
        self._cf_toast(tr("🌐 正在测试 CurseForge API Key…（首次联网可能要几秒）"), "info")
        self._cf_testing = True
        self.cf_test_btn.setEnabled(False)
        self.cf_status_label.setText(tr("正在测试…"))

        def run():
            try:
                from core.mod_search import test_curseforge_key
                ok, note = test_curseforge_key(pending or None)
            except Exception as error:
                ok, note = False, secrets.redact_exc(error)
            try:
                self._cf_test_done.emit(bool(ok), str(note))
            except RuntimeError:              # 设置窗已经关了
                pass

        threading.Thread(target=run, daemon=True).start()

    @QtCore.Slot(bool, str)
    def _on_cf_test_done(self, ok, note):
        self._cf_testing = False
        self.cf_test_btn.setEnabled(True)
        self._cf_log(trp("✅ CurseForge Key 可用：{0}", note) if ok
                     else trp("❌ CurseForge Key 不可用：{0}", note),
                     "INFO" if ok else "ERROR")
        self._cf_toast(tr("✅ CurseForge Key 可用") if ok
                       else tr("❌ CurseForge Key 不可用，详见日志"),
                       "ok" if ok else "warn")
        self._update_cf_status()

    def _test_sfx(self):
        try:
            from utils import sfx
            sfx.设置(True, self._sfx_hover, self._sfx_press)
            sfx.预热()
            if not sfx.可用():
                QtWidgets.QMessageBox.warning(self, tr("音效"), tr("⚠ 音效文件不存在"))
                return
            sfx.play("hover")
            QtCore.QTimer.singleShot(320, lambda: sfx.play("press"))
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, tr("音效"), str(error))

    # ---- 其它 ----
    def _extra_status_from_parent(self):
        parent = self._parent_win
        lines, source = [], ""
        try:
            box = getattr(parent, "extra_text", None)
            if box is not None:
                lines = box.toPlainText().splitlines()
            source = str(getattr(parent, "source_path", "") or "")
        except Exception:
            pass
        return extra_status_text(lines, source)

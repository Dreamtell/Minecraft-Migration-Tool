# ui/qt_main/look.py
"""Qt 主界面的外观层:数值全部取自 Tk 版自绘控件的实测,不另起炉灶。

对照来源(都在 Tk 侧,这里只读不改):
  · 渐变按钮   utils/helpers.create_gradient_button:圆角 8、高 30、9 号粗体、
               上下渐变 c0→c1,悬停两个色标各 +40,禁用是单色面 tone(底,0.14)/字 tone(底,0.68)
  · 圆角输入框 utils/helpers.RoundedEntry:圆角 12、高 30、内边距 9、
               描边 深 #4a4a4a / 浅 #c8c8c8,聚焦 深 #64b5f6 / 浅 #4a90d9
  · 文本区     RoundedTextArea:圆角 8
  · 区块框     tk.LabelFrame / tk.Frame(relief=RIDGE):Tk 自己算的 2px 立体边
               (亮 = max(1.4c, (255+c)/2),暗 = 0.6c,按 16 位通道算;已和截图逐像素对过)

⚠ 这里不能 import tkinter,也不能 import utils.helpers / ui.mw_common(它们顶层 import tkinter)。
纯函数(颜色、QSS)不依赖 Qt,方便单测;控件类(SectionFrame / WarningBanner)才用 PySide6。
"""
import math

from PySide6 import QtCore, QtGui, QtWidgets

# ---- 按钮配色(ui/mw_common.py 的 _BTN_*,以及顶栏两颗图标按钮的色) ----
BTN_NEUTRAL = ("#1e88e5", "#42a5f5")      # 查看 / 检查 / 打开 / 浏览
BTN_ADD = ("#43a047", "#66bb6a")          # 添加 / 导入 / 扫描
BTN_DANGER = ("#e53935", "#ff5252")       # 清空 / 定位错误
BTN_TOOL = ("#546e7a", "#78909c")         # 设置(齿轮)
BTN_THEME = ("#8e24aa", "#ab47bc")        # 主题切换
BTN_PIN = ("#90a4ae", "#607d8b")          # 底栏 📌(Tk: 30×20 胶囊,8 号字)
BTN_LOG = ("#607d8b", "#78909c")          # 「打开完整日志」(Tk create_log_button 的灰蓝)
KIND_COLORS = {
    "neutral": BTN_NEUTRAL,
    "add": BTN_ADD,
    "danger": BTN_DANGER,
    "tool": BTN_TOOL,
    "theme": BTN_THEME,
    "log": BTN_LOG,
    "pin": BTN_PIN,
}
# 旧的 Qt 语义名 → 新的三色 + 工具色(调用方不用一起改)
KIND_ALIASES = {
    "import": "add", "scan": "add",
    "view": "neutral", "open": "neutral", "history": "neutral",
    "clear": "danger", "warn": "danger",
}

BTN_RADIUS = 8
BTN_HEIGHT = 30          # Tk 渐变按钮统一高 30(create_gradient_button height=30)
ENTRY_HEIGHT = 30        # RoundedEntry height=30
ENTRY_RADIUS = 12
AREA_RADIUS = 8


# ======================================================================
# 颜色工具(和 utils/helpers.py 同名同算法;那边 import tkinter,这里重写一份)
# ======================================================================
def _rgb(h):
    h = str(h).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(r, g, b):
    return "#%02x%02x%02x" % (max(0, min(255, int(r))), max(0, min(255, int(g))),
                              max(0, min(255, int(b))))


def lighten(h, amount=40):
    """悬停时每个色标整体提亮(同 helpers.lighten_color)。"""
    r, g, b = _rgb(h)
    return _hex(r + amount, g + amount, b + amount)


def shade(h, amount):
    """整体加减一个值,负数变暗(同 helpers._shade)。"""
    r, g, b = _rgb(h)
    return _hex(r + amount, g + amount, b + amount)


def tone(bg, t):
    """往 bg 的"反色方向"混 t:深底往白、浅底往黑(同 helpers._tone)。禁用控件的面/字色用它。"""
    try:
        r, g, b = _rgb(bg)
    except Exception:
        return "#c8c8c8" if t < 0.4 else "#707070"
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    tgt = 255 if lum < 0.5 else 0
    return _hex(*(round(c + (tgt - c) * t) for c in (r, g, b)))


def is_dark(theme):
    try:
        r, g, b = _rgb((theme or {}).get("bg", "#f0f0f0"))
    except Exception:
        return False
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0 < 0.5


def three_d_shades(bg):
    """Tk 画 3D 边用的(亮, 暗)两色,算法照 tk3d.c(16 位通道,最后右移 8 位)。

    亮 = max(1.4c, (65535+c)/2);暗 = 0.6c。深底 #2e2e2e → (#979797, #1b1b1b),
    和 Tk 实际画出来的像素一致;浅底 #f0f0f0 → (#ffffff, #909090)。
    """
    light, dark = [], []
    for c8 in _rgb(bg):
        c = c8 * 257
        hi = max(min(65535, 14 * c // 10), (65535 + c) // 2)
        light.append(hi >> 8)
        dark.append((60 * c // 100) >> 8)
    return _hex(*light), _hex(*dark)


def entry_border(theme, focus=False):
    """圆角输入框 / 文本区的描边色(同 RoundedEntry._render)。"""
    dark = is_dark(theme)
    if focus:
        return "#64b5f6" if dark else "#4a90d9"
    return "#4a4a4a" if dark else (theme or {}).get("border", "#c8c8c8")


# ======================================================================
# QSS
# ======================================================================
def _grad(c0, c1):
    return ("qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %s,stop:1 %s)" % (c0, c1))


def button_rules(theme):
    """各语义渐变按钮的 QSS(用 [kind="…"] 属性选择器,切主题只要重设一次全局 QSS)。"""
    bg = (theme or {}).get("bg", "#f0f0f0")
    dis_face, dis_fg = tone(bg, 0.14), tone(bg, 0.68)
    out = []
    for kind, (c0, c1) in KIND_COLORS.items():
        out.append(
            'QPushButton[tone="%(k)s"]{background:%(n)s;color:#ffffff;font-weight:bold;'
            'border:none;border-radius:%(r)dpx;padding:0 18px;min-height:%(ht)dpx;max-height:%(ht)dpx;}'
            'QPushButton[tone="%(k)s"]:hover{background:%(h)s;}'
            'QPushButton[tone="%(k)s"]:pressed{background:%(p)s;}'
            # 禁用:单色面 + 暗字(同 Tk _disabled_face/_disabled_fg),必须排在 hover 之后
            'QPushButton[tone="%(k)s"]:disabled{background:%(df)s;color:%(dg)s;}'
            % {"k": kind, "r": BTN_RADIUS, "ht": BTN_HEIGHT, "n": _grad(c0, c1),
               "h": _grad(lighten(c0), lighten(c1)),
               "p": _grad(shade(c0, -28), shade(c1, -28)),
               "df": dis_face, "dg": dis_fg})
    # 只有普通 QPushButton 走这里;自绘按钮见 grad_button.py
    out.append('QPushButton[kind="log"]{min-height:28px;max-height:28px;}')
    out.append('QPushButton[kind="pin"]{min-height:20px;max-height:20px;min-width:30px;max-width:30px;'
               'padding:0;font-size:8pt;}')
    out.append('QPushButton[iconOnly="true"]{padding:0;min-height:34px;max-height:34px;'
               'min-width:34px;max-width:34px;font-size:15pt;}')
    # 主操作行的高按钮（同 Tk 的 btn_h = 44）:必须排在 kind 规则之后,
    # 否则 kind 规则里的 min/max-height:30 会把 setFixedHeight 压回去。
    out.append('QPushButton[tall="true"]{min-height:44px;max-height:44px;}')
    return "".join(out)


def build_qss(theme):
    """主窗口的全局样式表。键名全部来自 utils/theme.py,缺键用浅色主题的值兜底。"""
    t = theme or {}
    g = t.get
    bd = entry_border(t)
    bd_focus = entry_border(t, focus=True)
    sel_bg = g("text_sel_bg", g("ttk_select_bg", "#d0d0d0"))
    sel_fg = g("text_sel_fg", g("ttk_select_fg", "#000000"))
    return f"""
        QMainWindow {{
            background: {g("bg", "#f0f0f0")};
            color: {g("fg", "#000")};
        }}
        QLabel {{
            color: {g("fg", "#000")};
        }}
        QLineEdit {{
            background: {g("entry_bg", "#fff")};
            color: {g("entry_fg", "#000")};
            border: 1px solid {bd};
            border-radius: {ENTRY_RADIUS}px;
            padding: 0 9px;
            font-size: 10pt;              /* 同 Tk RoundedEntry 默认 10 号 */
            min-height: {ENTRY_HEIGHT - 2}px;
            max-height: {ENTRY_HEIGHT - 2}px;
            selection-background-color: {sel_bg};
            selection-color: {sel_fg};
        }}
        QLineEdit:focus {{
            border: 1px solid {bd_focus};
        }}
        QLineEdit#pathEdit {{
            color: {g("data_fg", g("entry_fg", "#000"))};
        }}
        QLineEdit#worldEdit {{
            color: {g("data_id_fg", g("entry_fg", "#000"))};
        }}
        QTextEdit {{
            background: {g("text_bg", "#fff")};
            color: {g("text_fg", "#000")};
            border: 1px solid {bd};
            border-radius: {AREA_RADIUS}px;
            padding: {AREA_RADIUS}px;
            font-size: 10pt;
            selection-background-color: {sel_bg};
            selection-color: {sel_fg};
        }}
        QTextEdit:focus {{
            border: 1px solid {bd_focus};
        }}
        QTextEdit#modListEdit, QTextEdit#configListEdit, QTextEdit#extraListEdit {{
            font-family: "Microsoft YaHei Light";   /* Tk: ("微软雅黑 Light", 10) */
        }}
        QTextEdit#logEdit {{
            background: {g("log_bg", g("text_bg", "#fff"))};
            color: {g("log_fg", g("text_fg", "#000"))};
            font-family: "Microsoft YaHei UI";      /* Tk log_font(): YaHei UI 10 */
        }}
        QTextEdit#modListEdit QScrollBar:vertical,
        QTextEdit#configListEdit QScrollBar:vertical,
        QTextEdit#extraListEdit QScrollBar:vertical,
        QTextEdit#logEdit QScrollBar:vertical {{
            background: {g("bg", "#f0f0f0")};
            width: 12px;
            margin: 0;
            border: none;
        }}
        QTextEdit#modListEdit QScrollBar:horizontal,
        QTextEdit#configListEdit QScrollBar:horizontal,
        QTextEdit#extraListEdit QScrollBar:horizontal,
        QTextEdit#logEdit QScrollBar:horizontal {{
            background: {g("bg", "#f0f0f0")};
            height: 12px;
            margin: 0;
            border: none;
        }}
        QTextEdit#modListEdit QScrollBar::handle:vertical,
        QTextEdit#configListEdit QScrollBar::handle:vertical,
        QTextEdit#extraListEdit QScrollBar::handle:vertical,
        QTextEdit#logEdit QScrollBar::handle:vertical,
        QTextEdit#modListEdit QScrollBar::handle:horizontal,
        QTextEdit#configListEdit QScrollBar::handle:horizontal,
        QTextEdit#extraListEdit QScrollBar::handle:horizontal,
        QTextEdit#logEdit QScrollBar::handle:horizontal {{
            background: {g("button_bg", "#e0e0e0")};
            border-radius: 5px;
            min-height: 22px;
            min-width: 22px;
        }}
        QTextEdit#modListEdit QScrollBar::handle:hover,
        QTextEdit#configListEdit QScrollBar::handle:hover,
        QTextEdit#extraListEdit QScrollBar::handle:hover,
        QTextEdit#logEdit QScrollBar::handle:hover {{
            background: {g("hover_bg", "#e9eef5")};
        }}
        QTextEdit#modListEdit QScrollBar::add-line,
        QTextEdit#modListEdit QScrollBar::sub-line,
        QTextEdit#configListEdit QScrollBar::add-line,
        QTextEdit#configListEdit QScrollBar::sub-line,
        QTextEdit#extraListEdit QScrollBar::add-line,
        QTextEdit#extraListEdit QScrollBar::sub-line,
        QTextEdit#logEdit QScrollBar::add-line,
        QTextEdit#logEdit QScrollBar::sub-line {{
            width: 0;
            height: 0;
            border: none;
            background: transparent;
        }}
        QTextEdit#modListEdit QScrollBar::add-page,
        QTextEdit#modListEdit QScrollBar::sub-page,
        QTextEdit#configListEdit QScrollBar::add-page,
        QTextEdit#configListEdit QScrollBar::sub-page,
        QTextEdit#extraListEdit QScrollBar::add-page,
        QTextEdit#extraListEdit QScrollBar::sub-page,
        QTextEdit#logEdit QScrollBar::add-page,
        QTextEdit#logEdit QScrollBar::sub-page {{
            background: transparent;
        }}
        QPushButton {{
            background: {g("button_bg", "#e0e0e0")};
            color: {g("button_fg", "#000")};
            border: none;
            border-radius: {BTN_RADIUS}px;
            padding: 0 14px;
            min-height: {BTN_HEIGHT}px;
        }}
        QPushButton:hover {{
            background: {g("hover_bg", "#e9eef5")};
        }}
        {button_rules(t)}
        QGroupBox {{
            border: 1px solid {g("muted_fg", "#888")};
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 8px;
            font-weight: bold;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            color: {g("accent_fg", g("fg", "#000"))};
        }}
        QTabWidget::pane {{
            border: 1px solid {g("muted_fg", "#888")};
            border-radius: 4px;
            top: -1px;
        }}
        QTabBar::tab {{
            background: {g("button_bg", "#e0e0e0")};
            color: {g("fg", "#000")};
            padding: 6px 14px;
            border: 1px solid {g("muted_fg", "#888")};
            border-bottom: none;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }}
        QTabBar::tab:selected {{
            background: {g("text_bg", "#fff")};
            color: {g("accent_fg", g("fg", "#000"))};
        }}
        QProgressBar {{
            border: 1px solid {g("muted_fg", "#888")};
            border-radius: 4px;
            text-align: center;
            background: {g("text_bg", "#fff")};
            color: {g("fg", "#000")};
        }}
        QProgressBar::chunk {{
            background: {g("card_sel_bar", "#2f7fd1")};
            border-radius: 3px;
        }}
        QCheckBox {{
            color: {g("fg", "#000")};
            spacing: 6px;
        }}
        /* ---- 现代材质：只改 Qt 自有控件的观感，不动版式与尺寸 ---- */
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border: 2px solid {bd_focus};
        }}
        QToolTip {{
            background: {g("entry_bg", "#fff")};
            color: {g("entry_fg", "#000")};
            border: 1px solid {lighten(bd, 10)};
            border-radius: 8px;
            padding: 5px 9px;
        }}
        QListWidget, QTreeWidget {{
            background: {g("entry_bg", "#fff")};
            color: {g("entry_fg", "#000")};
            border: 1px solid {tone(g("entry_bg", "#fff"), 0.10)};
            border-radius: 10px;
            outline: 0;
            padding: 4px;
        }}
        QListWidget::item, QTreeWidget::item {{
            border-radius: 6px;
            padding: 4px 6px;
        }}
        QListWidget::item:hover, QTreeWidget::item:hover {{
            background: {g("hover_bg", "#e9eef5")};
        }}
        QListWidget::item:selected, QTreeWidget::item:selected {{
            background: {g("card_sel_bar", "#2f7fd1")};
            color: #ffffff;
        }}
        QListWidget QScrollBar:vertical, QTreeWidget QScrollBar:vertical,
        QScrollArea QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 2px;
        }}
        QListWidget QScrollBar::handle:vertical, QTreeWidget QScrollBar::handle:vertical,
        QScrollArea QScrollBar::handle:vertical {{
            background: {tone(g("bg", "#f0f0f0"), 0.18)};
            border-radius: 5px;
            min-height: 28px;
        }}
        QListWidget QScrollBar::handle:vertical:hover, QTreeWidget QScrollBar::handle:vertical:hover,
        QScrollArea QScrollBar::handle:vertical:hover {{
            background: {tone(g("bg", "#f0f0f0"), 0.30)};
        }}
        QListWidget QScrollBar::add-line, QListWidget QScrollBar::sub-line,
        QTreeWidget QScrollBar::add-line, QTreeWidget QScrollBar::sub-line,
        QScrollArea QScrollBar::add-line, QScrollArea QScrollBar::sub-line {{
            height: 0;
            border: none;
            background: transparent;
        }}
        QHeaderView::section {{
            background: {g("ttk_bg", "#f0f0f0")};
            color: {g("ttk_fg", g("fg", "#000"))};
            border: none;
            border-right: 1px solid {tone(g("entry_bg", "#fff"), 0.10)};
            padding: 4px 6px;
            font-weight: 600;
        }}
        QProgressBar {{
            border-radius: 8px;
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {lighten(g("card_sel_bar", "#2f7fd1"), 18)},
                stop:1 {shade(g("card_sel_bar", "#2f7fd1"), -12)});
            border-radius: 7px;
        }}
        QTabBar::tab {{
            border: none;
            border-radius: 10px;
            margin-right: 4px;
            padding: 6px 14px;
        }}
        QTabBar::tab:hover {{
            background: {lighten(g("button_bg", "#e0e0e0"), 40)};
        }}
        QTabBar::tab:selected {{
            background: {g("card_sel_bar", "#2f7fd1")};
            color: #ffffff;
        }}
        QComboBox, QSpinBox, QDoubleSpinBox {{
            background: {g("entry_bg", "#fff")};
            color: {g("entry_fg", "#000")};
            border: 1px solid {bd};
            border-radius: 8px;
            padding: 2px 8px;
            min-height: 24px;
        }}
        QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
            border-color: {bd_focus};
        }}
        QComboBox QAbstractItemView {{
            background: {g("entry_bg", "#fff")};
            color: {g("entry_fg", "#000")};
            border: 1px solid {bd};
            border-radius: 8px;
            selection-background-color: {g("card_sel_bar", "#2f7fd1")};
            selection-color: #ffffff;
        }}
    """


def kind_for(name):
    """把任意语义名收成 KIND_COLORS 里的键;认不出来返回 None(= 用通用按钮样式)。"""
    name = KIND_ALIASES.get(name, name)
    return name if name in KIND_COLORS else None


def style_button(btn, kind, icon_only=False):
    """给按钮挂语义色。样式本身在全局 QSS 里,这里只设属性并重新套样式。"""
    kind = kind_for(kind)
    btn.setProperty("kind", kind or "")
    # QSS 用 [tone=…] 选:自绘按钮只设 kind、不设 tone,于是不会被这套几何覆盖
    btn.setProperty("tone", kind or "")
    btn.setProperty("iconOnly", bool(icon_only))
    if kind:
        btn.setCursor(QtCore.Qt.PointingHandCursor)
    st = btn.style()
    st.unpolish(btn)
    st.polish(btn)
    btn.update()


# ======================================================================
# Tk 风格的 2px 立体边(groove / ridge)
# ======================================================================
def draw_3d_border(painter, rect, bg, relief="groove", width=2):
    """沿 rect 画 Tk 的 groove / ridge 边(width 为偶数,外半圈 + 内半圈各 width/2)。

    groove:外半圈 左上暗 / 右下亮;内半圈反过来。ridge 整个反过来。
    """
    light, dark = three_d_shades(bg)
    half = max(1, int(width) // 2)
    if relief == "ridge":
        outer_tl, outer_br = light, dark
    else:
        outer_tl, outer_br = dark, light
    inner_tl, inner_br = outer_br, outer_tl
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
    for ring in range(half * 2):
        tl, br = (outer_tl, outer_br) if ring < half else (inner_tl, inner_br)
        x0, y0 = rect.left() + ring, rect.top() + ring
        x1, y1 = rect.right() - ring, rect.bottom() - ring
        if x1 <= x0 or y1 <= y0:
            break
        painter.setPen(QtGui.QPen(QtGui.QColor(tl), 1))
        painter.drawLine(x0, y0, x1, y0)           # 上
        painter.drawLine(x0, y0, x0, y1)           # 左
        painter.setPen(QtGui.QPen(QtGui.QColor(br), 1))
        painter.drawLine(x0 + 1, y1, x1, y1)       # 下
        painter.drawLine(x1, y0 + 1, x1, y1)       # 右
    painter.restore()


def draw_3d_border_physical(painter, widget, top_logical, bg, relief="groove", width=2,
                            fill=None):
    """在控件上画 3D 边,每一圈都是**整数个物理像素**。

    Tk 的 2px 边在 125% / 150% 缩放下仍然是 2 个物理像素(一暗一亮)。Qt 6 在 125% 下
    控件原点的设备坐标是 12.5 这种小数,按逻辑像素画 1px 线时,两圈会被取整并到同一个
    像素,凹槽就只剩一条亮线。做法:仍在逻辑坐标里画,但
      · 起点用 deviceTransform 求出设备坐标后取整,再除回逻辑坐标;
      · 每一圈的线宽取 1/缩放 个逻辑像素,恰好是 1 个物理像素。
    (试过 resetTransform 改成设备坐标:Qt 并不会把之后的坐标当设备像素,边会画到屏外。)
    top_logical 是边框顶线的逻辑 y(标题压在线上,所以不是 0)。
    起点向内取整(ceil / floor):控件的裁剪区从 12.5 这种小数开始,向外取整的那一像素会被
    裁掉(实测:最外圈暗线消失,只剩亮线)。fill 给了就先把边框内侧整块填上这个颜色。
    """
    xf = painter.deviceTransform()
    sx = xf.m11() or 1.0
    sy = xf.m22() or 1.0
    p0 = xf.map(QtCore.QPointF(0, top_logical))
    p1 = xf.map(QtCore.QPointF(widget.width(), widget.height()))
    eps = 1e-6
    x0, y0 = math.ceil(p0.x() - eps), math.ceil(p0.y() - eps)       # 设备像素,整数,在控件内
    x1, y1 = math.floor(p1.x() + eps), math.floor(p1.y() + eps)
    # 设备 → 当前 painter 的逻辑坐标(去掉平移后再除缩放)
    ox, oy = xf.dx(), xf.dy()

    def lx(dev):
        return (dev - ox) / sx

    def ly(dev):
        return (dev - oy) / sy

    light, dark = three_d_shades(bg)
    half = max(1, int(width) // 2)
    if relief == "ridge":
        outer_tl, outer_br = light, dark
    else:
        outer_tl, outer_br = dark, light
    inner_tl, inner_br = outer_br, outer_tl

    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
    painter.setPen(QtCore.Qt.NoPen)
    if fill is not None:
        painter.fillRect(QtCore.QRectF(lx(x0), ly(y0), lx(x1) - lx(x0), ly(y1) - ly(y0)),
                         QtGui.QColor(fill))
    fill_rect = painter.fillRect
    for ring in range(half * 2):
        tl, br = (outer_tl, outer_br) if ring < half else (inner_tl, inner_br)
        dl, dt_ = x0 + ring, y0 + ring               # 这一圈左 / 上线所在的设备像素
        dr, db = x1 - ring - 1, y1 - ring - 1        # 右 / 下线所在的设备像素(含)
        if dr <= dl or db <= dt_:
            break
        ctl, cbr = QtGui.QColor(tl), QtGui.QColor(br)
        wpx, hpx = 1.0 / sx, 1.0 / sy
        fill_rect(QtCore.QRectF(lx(dl), ly(dt_), lx(dr + 1) - lx(dl), hpx), ctl)       # 上
        fill_rect(QtCore.QRectF(lx(dl), ly(dt_), wpx, ly(db + 1) - ly(dt_)), ctl)      # 左
        fill_rect(QtCore.QRectF(lx(dl + 1), ly(db), lx(dr + 1) - lx(dl + 1), hpx), cbr)  # 下
        fill_rect(QtCore.QRectF(lx(dr), ly(dt_ + 1), wpx, ly(db + 1) - ly(dt_ + 1)), cbr)  # 右
    painter.restore()


class SectionFrame(QtWidgets.QFrame):
    """Tk 的 tk.LabelFrame:方角凹槽边,标题压在上边线上。

    用法同 QGroupBox:往里放布局即可。标题字体沿用控件字体(Tk 是 9 号,不加粗、不上色)。
    """

    TITLE_X = 8          # 标题离左边的距离(Tk 实测 ≈ 9)

    def __init__(self, title="", theme=None, parent=None):
        super().__init__(parent)
        self._title = str(title or "")
        self._theme = dict(theme or {})
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._fit_margins()

    # -- 对外 --
    def title(self):
        return self._title

    def setTitle(self, title):
        self._title = str(title or "")
        self._fit_margins()
        self.update()

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    # -- 内部 --
    def _title_h(self):
        return self.fontMetrics().height() if self._title else 0

    def _fit_margins(self):
        th = self._title_h()
        # 左右/下 = 2px 边 + Tk 的 padx/pady 5 再加控件自己的 5;上 = 标题高度 + 同样的间距
        self.setContentsMargins(11, th + 6, 11, 8)

    def changeEvent(self, e):
        if e.type() == QtCore.QEvent.FontChange:
            self._fit_margins()
        super().changeEvent(e)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        bg = self._theme.get("labelframe_bg", self._theme.get("bg", "#f0f0f0"))
        fg = self._theme.get("labelframe_fg", self._theme.get("fg", "#000000"))
        th = self._title_h()
        top = th // 2
        draw_3d_border_physical(p, self, top, bg, "groove", 2)
        if self._title:
            tw = p.fontMetrics().horizontalAdvance(self._title)
            # 标题底下的那段边线擦掉(Tk 的 LabelFrame 标题是压在线上的)
            p.fillRect(QtCore.QRect(self.TITLE_X - 3, top - 1, tw + 6, 4), QtGui.QColor(bg))
            p.setPen(QtGui.QColor(fg))
            p.drawText(QtCore.QRect(self.TITLE_X, 0, tw + 2, th),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._title)
        p.end()


class WarningBanner(QtWidgets.QFrame):
    """窗口顶部的红色免费声明条:Tk 是 tk.Frame(relief=RIDGE, bd=2) + 居中粗体 10 号字。"""

    def __init__(self, text="", theme=None, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self.label = QtWidgets.QLabel(str(text or ""))
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        f = self.label.font()
        f.setPointSizeF(10.0)
        f.setBold(True)
        self.label.setFont(f)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(2, 8, 2, 8)
        lay.addWidget(self.label)
        self._restyle()

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self._restyle()
        self.update()

    def _restyle(self):
        fg = self._theme.get("warning_fg", "#a31515")
        self.label.setStyleSheet("color:%s;background:transparent;" % fg)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        bg = self._theme.get("warning_bg", "#ffcccc")
        draw_3d_border_physical(p, self, 0, bg, "ridge", 2, fill=bg)
        p.end()

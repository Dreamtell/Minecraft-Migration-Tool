# -*- coding: utf-8 -*-
"""迁移前自检的弹窗：圆角卡片 + 逐条清单 + 药丸按钮。

以前这里是 `messagebox.askyesno` / `showerror` —— 系统原生窗，字体、颜色、
按钮全是 Windows 的老样子，跟主界面（圆角、渐变按钮、暗色主题）不是一套的。

这里做成跟消息中心/设置页一致的样子：
  · Win11 上交给 DWM 画抗锯齿圆角（和 message_center 抽屉同一套做法）；
  · 顶部图标 + 标题 + 关闭 ✕，下面是副标题；
  · 中间是可滚动的条目清单，每条前面一个按级别着色的圆点；
  · 底部两个药丸按钮，主操作靠右。

对外只有两个函数：
    ask_errors(父窗, 主题, 条目)   → None（只有一个「知道了」）
    ask_warnings(父窗, 主题, 条目) → bool（「开始迁移」/「先不开始」）
"""
from __future__ import annotations

import tkinter as tk

from utils.dpi import px
from utils.textsafe import 可见
from utils.helpers import create_gradient_button, set_window_icon
from utils.i18n import trp

# 卡片配色：主题里没有 card_bg / border，就自己从 bg 往 fg 混一点出来
def _配色(主题):
    th = 主题 or {}
    bg = th.get("bg") or "#2e2e2e"
    fg = th.get("fg") or "#ffffff"

    def 混(甲, 乙, 比):
        try:
            a = 甲.lstrip("#")
            b = 乙.lstrip("#")
            ra, ga, ba = (int(a[i:i + 2], 16) for i in (0, 2, 4))
            rb, gb, bb = (int(b[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return 甲
        return "#%02x%02x%02x" % (int(ra + (rb - ra) * 比),
                                  int(ga + (gb - ga) * 比),
                                  int(ba + (bb - ba) * 比))

    深 = _亮度(bg) < 140
    return {
        "卡": th.get("card_bg") or 混(bg, fg, 0.06 if 深 else 0.02),
        "边": th.get("border") or 混(bg, fg, 0.18 if 深 else 0.14),
        "字": fg,
        "弱": th.get("muted_fg") or 混(fg, bg, 0.45),
        "条": th.get("card_bg") or 混(bg, fg, 0.04 if 深 else 0.03),
        "悬": 混(bg, fg, 0.10 if 深 else 0.07),
        "次": 混(bg, fg, 0.34 if 深 else 0.30),
        "错": "#ff6b6b" if 深 else "#c62828",
        "警": "#ffb648" if 深 else "#c77700",
        "信": th.get("muted_fg") or 混(fg, bg, 0.45),
        "主": th.get("card_sel_bar") or "#2f7fd1",
    }


def _亮度(色):
    try:
        c = 色.lstrip("#")
        if len(c) != 6:
            return 255
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
        return 0.299 * r + 0.587 * g + 0.114 * b
    except Exception:
        return 255


def _级别色(配色, 级别):
    if 级别 == "fail":
        return 配色["错"], "❌"
    if 级别 == "warn":
        return 配色["警"], "⚠️"
    return 配色["信"], "ℹ️"


def _设圆角(窗):
    """Win11 上让 DWM 画抗锯齿圆角；其它系统静默跳过。"""
    try:
        from ui.message_center import _WIN11, _设DWM圆角
        if _WIN11:
            _设DWM圆角(窗)
    except Exception:
        pass


def _外壳(win):
    """拿到窗口的顶层句柄（Tk 的 winfo_id 是内层子窗）。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        return int(user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id())
    except Exception:
        return 0


def _贴在父窗上(窗, 父窗):
    """把弹窗插到父窗正上方（Z 序），但不设 topmost —— 别的程序切到前台时自然被盖住。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = _外壳(窗)
        after_hwnd = _外壳(父窗)
        if not hwnd or not after_hwnd:
            return False
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        GW_HWNDPREV = 3
        # SetWindowPos 的 hWndInsertAfter 是"排在它**后面**"那个窗口，
        # 想贴在父窗上面就得传"父窗上面那个窗口"；父窗已经在最上面时就传 HWND_TOP。
        user32.GetWindow.restype = ctypes.c_void_p
        user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        prev = user32.GetWindow(ctypes.c_void_p(after_hwnd), GW_HWNDPREV)
        插入位置 = ctypes.c_void_p(int(prev) if prev else None)
        return bool(user32.SetWindowPos(ctypes.c_void_p(hwnd), 插入位置,
                                        0, 0, 0, 0,
                                        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE))
    except Exception:
        return False


def _是置顶窗(窗):
    """当前是否 WS_EX_TOPMOST（用于自检，探针也用它）。"""
    try:
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_TOPMOST = 0x00000008
        风 = ctypes.windll.user32.GetWindowLongW(ctypes.c_void_p(_外壳(窗)), GWL_EXSTYLE)
        return bool(风 & WS_EX_TOPMOST)
    except Exception:
        return False


def _前台窗口():
    """当前前台窗口句柄；拿不到返回 0。"""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _居中(父窗, 窗, 宽, 高):
    try:
        父窗.update_idletasks()
        rx, ry = 父窗.winfo_rootx(), 父窗.winfo_rooty()
        rw, rh = 父窗.winfo_width(), 父窗.winfo_height()
        x = rx + max(0, (rw - 宽) // 2)
        # 竖直位置：取剩余空间的一半略偏上（原来取 1/3，用户嫌太靠上）
        y = ry + max(0, int((rh - 高) * 0.45))
    except Exception:
        x = y = None
    if x is None:
        return
    窗.geometry("+%d+%d" % (x, y))


def _动画进(窗, x, y, 宽, 高, 时长=150):
    """弹出：透明度 0→1 同时从下方 10px 滑到位（ease-out-cubic）。"""
    步数 = 10

    def 一步(i):
        k = min(1.0, i / 步数)
        e = 1.0 - (1.0 - k) ** 3
        try:
            窗.attributes("-alpha", max(0.02, e))
            窗.geometry("%dx%d+%d+%d" % (宽, 高, x, y + int(round((1.0 - e) * px(10)))))
        except Exception:
            return
        if i < 步数:
            窗.after(max(1, 时长 // 步数), lambda: 一步(i + 1))
        else:
            try:
                窗.attributes("-alpha", 0.999)   # 保持分层，否则 DWM 圆角会掉
            except Exception:
                pass

    一步(0)


def _动画出(窗, x, y, 宽, 高, 结束后, 时长=110):
    """收回：透明度 1→0 同时向下沉 6px，然后销毁。"""
    步数 = 8

    def 一步(i):
        k = min(1.0, i / 步数)
        e = k ** 2
        try:
            窗.attributes("-alpha", max(0.02, 1.0 - e))
            窗.geometry("%dx%d+%d+%d" % (宽, 高, x, y + int(round(e * px(6)))))
        except Exception:
            pass
        if i < 步数:
            窗.after(max(1, 时长 // 步数), lambda: 一步(i + 1))
        else:
            try:
                结束后()
            except Exception:
                pass

    一步(0)


def _弹(父窗, 主题, 标题, 副标题, 条目, 按钮, 宽=px(600), 图标级别="warn"):
    """返回被点的按钮名（按钮 = [(文字, 返回值, 是否主按钮), …]）。"""
    配色 = _配色(主题)
    窗 = tk.Toplevel(父窗)
    窗.withdraw()
    窗.overrideredirect(True)
    # 0.999 而不是 1.0：分层窗才吃 DWM 圆角（实测普通不透明窗设了也不圆）
    try:
        窗.attributes("-alpha", 0.999)
    except Exception:
        pass
    窗.configure(bg=配色["卡"])
    set_window_icon(窗)
    try:
        窗.title("__precheck__")   # 无边框窗看不见标题，但探针/自动化能靠它认出这个窗
    except Exception:
        pass
    try:
        窗.transient(父窗)
    except Exception:
        pass

    结果 = {"值": None}

    位置 = {"x": 0, "y": 0, "宽": 宽, "高": 0}
    已关 = {"值": False}
    按住 = {"id": None}

    def 关(值=None):
        if 已关["值"]:
            return
        已关["值"] = True
        结果["值"] = 值
        try:
            窗.grab_release()
        except Exception:
            pass

        def 销毁():
            try:
                if 按住["id"]:
                    父窗.unbind("<FocusIn>", 按住["id"])
            except Exception:
                pass
            try:
                窗.destroy()
            except Exception:
                pass

        # 收回动画；万一窗口已经没了就直接销毁
        try:
            _动画出(窗, 位置["x"], 位置["y"], 位置["宽"], 位置["高"] or 窗.winfo_height(), 销毁)
        except Exception:
            销毁()

    窗.bind("<Escape>", lambda e: 关(按钮[-1][1] if 按钮 else None))

    # 无边框窗（overrideredirect）不参与"从属窗口"那套层叠，transient 说了不算：
    # 主界面被点一下就能盖到它上面。两手一起用 —— 置顶 + 主界面一拿焦点就把它抬起来。
    # 不用 topmost（用户报"过分置顶"）：改成把自己插到父窗正上方，
    # 这样它压得住主界面，但别的程序一切到前台就把它盖住。

    def _父窗聚焦(_e=None):
        if 已关["值"]:
            return
        for 做 in (lambda: 窗.lift(), lambda: _贴在父窗上(窗, 父窗)):
            try:
                做()
            except Exception:
                pass

    try:
        按住["id"] = 父窗.bind("<FocusIn>", _父窗聚焦, add="+")
    except Exception:
        按住["id"] = None

    # 置顶只能"有限"：主界面收进托盘 / 最小化时跟着藏起来，别的程序在前台时也让开。
    # （用户 m07008：主界面隐藏了这窗户还在最上面。）
    def _跟父窗():
        if 已关["值"] or not 窗.winfo_exists():
            return
        try:
            父可见 = bool(父窗.winfo_viewable()) and 父窗.state() not in ("iconic", "withdrawn")
        except Exception:
            父可见 = False
        try:
            if not 父可见:
                if 窗.winfo_viewable():
                    窗.withdraw()
            else:
                if not 窗.winfo_viewable():
                    窗.deiconify()
                    _设圆角(窗)
                _贴在父窗上(窗, 父窗)
        except Exception:
            pass
        try:
            窗.after(220, _跟父窗)
        except Exception:
            pass

    try:
        窗.after(60, _跟父窗)
    except Exception:
        pass

    # 不画矩形描边：窗口本身是 DWM 圆角，方形描边会在圆角处露出来
    框 = tk.Frame(窗, bg=配色["卡"])
    框.pack(fill="both", expand=True)

    # ---- 头部：图标 + 标题 + ✕ ----
    头 = tk.Frame(框, bg=配色["卡"])
    头.pack(fill="x", padx=px(20), pady=(px(16), px(4)))
    图色, 图标 = _级别色(配色, 图标级别)
    tk.Label(头, text=图标, bg=配色["卡"], fg=图色,
             font=("微软雅黑", 15)).pack(side="left", padx=(0, px(8)))
    tk.Label(头, text=标题, bg=配色["卡"], fg=配色["字"],
             font=("微软雅黑", 13, "bold")).pack(side="left")
    叉 = tk.Label(头, text="✕", bg=配色["卡"], fg=配色["弱"],
                  font=("微软雅黑", 12), cursor="hand2")
    叉.pack(side="right")
    叉.bind("<Button-1>", lambda e: 关(按钮[-1][1] if 按钮 else None))
    叉.bind("<Enter>", lambda e: 叉.configure(fg=配色["字"]))
    叉.bind("<Leave>", lambda e: 叉.configure(fg=配色["弱"]))

    if 副标题:
        tk.Label(框, text=可见(副标题), bg=配色["卡"], fg=配色["弱"], justify="left",
                 anchor="w", wraplength=宽 - px(48),
                 font=("微软雅黑", 9)).pack(fill="x", padx=px(20), pady=(0, px(10)))

    # ---- 条目清单（多了就滚动）----
    区 = tk.Frame(框, bg=配色["卡"])
    区.pack(fill="both", expand=True, padx=px(16), pady=(0, px(8)))
    画布 = tk.Canvas(区, bg=配色["卡"], highlightthickness=0, bd=0, height=px(80))
    画布.pack(side="left", fill="both", expand=True)
    内 = tk.Frame(画布, bg=配色["卡"])
    内号 = 画布.create_window((0, 0), window=内, anchor="nw")

    def _排(e=None):
        画布.itemconfigure(内号, width=画布.winfo_width())
        画布.configure(scrollregion=画布.bbox("all"))
    内.bind("<Configure>", _排)
    画布.bind("<Configure>", _排)

    行们 = []
    for 级别, 文本 in 条目:
        色, 点 = _级别色(配色, 级别)
        行 = tk.Frame(内, bg=配色["条"])
        行.pack(fill="x", pady=(0, px(6)))
        tk.Label(行, text=点, bg=配色["条"], fg=色,
                 font=("微软雅黑", 9)).pack(side="left", padx=(px(10), px(6)),
                                            pady=px(7), anchor="n")
        字 = tk.Label(行, text=可见(文本), bg=配色["条"], fg=配色["字"], justify="left",
                      anchor="w", wraplength=宽 - px(84), font=("微软雅黑", 9))
        字.pack(side="left", fill="x", expand=True, padx=(0, px(10)), pady=px(7))
        行们.append((行, 字, 配色))

    窗.update_idletasks()
    try:
        _内容高 = max(px(48), int(内.winfo_reqheight()))
    except Exception:
        _内容高 = px(120)
    画布.configure(height=min(px(320), _内容高))

    if len(条目) > 6:
        条 = tk.Scrollbar(区, orient="vertical", command=画布.yview)
        条.pack(side="right", fill="y")
        画布.configure(yscrollcommand=条.set)

    def _滚(e):
        try:
            画布.yview_scroll(int(-1 * (e.delta / 120)), "units")
        except Exception:
            pass
    画布.bind("<MouseWheel>", _滚)
    内.bind("<MouseWheel>", _滚)

    # ---- 底部按钮 ----
    底 = tk.Frame(框, bg=配色["卡"])
    底.pack(fill="x", padx=px(20), pady=(px(4), px(16)))
    # 按声明顺序 pack(side="right")：第一个声明的最靠右（主按钮在右，符合 Windows 习惯）
    for 文字, 值, 主 in 按钮:
        if 主:
            钮 = create_gradient_button(底, 文字, lambda v=值: 关(v),
                                        colors=(配色["主"], 配色["主"]),
                                        font=("微软雅黑", 10, "bold"),
                                        width=px(96), height=px(32))
        else:
            钮 = create_gradient_button(底, 文字, lambda v=值: 关(v),
                                        colors=(配色["次"], 配色["次"]),
                                        font=("微软雅黑", 10),
                                        width=px(96), height=px(32))
        钮.pack(side="right", padx=(px(8), 0))

    # 窗体能拖（overrideredirect 没有标题栏）
    拖动 = {"x": 0, "y": 0}

    def _按下(e):
        拖动["x"], 拖动["y"] = e.x_root - 窗.winfo_rootx(), e.y_root - 窗.winfo_rooty()

    def _拖(e):
        try:
            窗.geometry("+%d+%d" % (e.x_root - 拖动["x"], e.y_root - 拖动["y"]))
        except Exception:
            pass

    for w in (头,):
        w.bind("<Button-1>", _按下)
        w.bind("<B1-Motion>", _拖)

    窗.update_idletasks()
    高 = min(px(660), max(px(200), 框.winfo_reqheight()))
    _居中(父窗, 窗, 宽, 高)
    窗.geometry("%dx%d" % (宽, 高))
    try:
        窗.attributes("-alpha", 0.02)     # 先压到几乎透明，免得第一帧整块闪出来
    except Exception:
        pass
    窗.deiconify()
    # deiconify 会重建 HWND，圆角必须在显示之后设一次（顺序反了就白设）
    _设圆角(窗)
    窗.lift()
    位置.update({"x": 窗.winfo_rootx(), "y": 窗.winfo_rooty(),
                 "宽": 宽, "高": 高})
    _动画进(窗, 位置["x"], 位置["y"], 宽, 高)
    try:
        窗.grab_set()
    except Exception:
        pass
    # 回车/Esc 直接选，不用先点一下窗口（焦点落在哪里都认）
    _绑键(窗, 关, (按钮[0][1] if 按钮 else None), (按钮[-1][1] if 按钮 else None))
    窗.focus_force()
    窗.after(80, lambda: (窗.lift(), 窗.focus_force()))
    窗.wait_window()
    return 结果["值"]


def _绑键(窗, 关, 主值, 次值):
    """回车 = 主按钮，Esc = 次按钮；绑到整棵子树，焦点落在哪个控件上都管用。"""
    def 全绑(w):
        try:
            w.bind("<Return>", lambda e: 关(主值))
            w.bind("<KP_Enter>", lambda e: 关(主值))
            w.bind("<Escape>", lambda e: 关(次值))
        except Exception:
            pass
        for c in w.winfo_children():
            全绑(c)
    全绑(窗)



def ask_errors(父窗, 主题, 条目, 标题=None):
    """有错误：只有一个「知道了」。"""
    return _弹(父窗, 主题, 标题 or trp("迁移前自检没通过"),
               trp("下面这些不解决，迁移跑不起来："),
               [("fail", x) for x in 条目],
               [(trp("知道了"), True, True)], 图标级别="fail")


def ask_warnings(父窗, 主题, 条目, 标题=None):
    """只有提醒：问一句，返回 True 表示照样开始。"""
    值 = _弹(父窗, 主题, 标题 or trp("迁移前自检有提醒"),
             trp("这些不影响运行，但结果可能不是你要的："),
             [("warn", x) for x in 条目],
             [(trp("开始迁移"), True, True), (trp("先不开始"), False, False)])
    return bool(值)

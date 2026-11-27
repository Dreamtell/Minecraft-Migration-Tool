# -*- coding: utf-8 -*-
# ui/preset_menu.py
"""「＋ 常用目录」弹出卡片：分组、图标、悬停高亮、「已在清单」徽章。

为什么不用 tk.Menu：原生菜单跟主界面完全不是一个视觉体系 —— 没有圆角、没有悬停
高亮，条目和说明挤在一行靠空格对齐，深色主题下也只看得到系统色。这里自绘一张卡片：

  · Win11 走 DWM 原生抗锯齿圆角（和消息中心同一套路），Win10 退回方角；
  · 每行 [图标] 名称 [说明] [✓ 已在清单]，悬停整行高亮；
  · 按用途分组，14 个条目不再是一根长条；
  · 点一下加进清单，行上的徽章**立刻更新、弹窗不关**（可以连着加好几样）；
  · 图标复用 utils/file_icons：文件夹是文件夹，文件带扩展名角标。

只依赖 tkinter + Pillow（PIL 缺失时退回文字符号，不挡功能）。
"""
import ctypes
import os
import tkinter as tk

from core.extra_presets import preset_groups
from utils.dpi import px
from utils.helpers import is_dark_theme
from utils.i18n import trp

_W = 620          # 卡片宽（设计像素）
_PAD = 14         # 卡片内边距
_ROW_H = 32       # 每行高
_ICON = 20        # 图标边长

_FONT_TITLE = ("微软雅黑", 11, "bold")
_FONT_NAME = ("微软雅黑", 10, "bold")
_FONT_DESC = ("微软雅黑", 9)
_FONT_SECTION = ("微软雅黑", 8, "bold")


def _mix(c1, c2, t):
    """两个 #rrggbb 按 t 混色（t=0 取 c1，t=1 取 c2）。"""
    try:
        a = [int(str(c1).lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        b = [int(str(c2).lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))
    except Exception:
        return c1


def _round_corners(win):
    """让 DWM 给无边框窗画抗锯齿圆角（Win11 才有；失败就保持方角）。"""
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        hwnd = user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id()
        值 = ctypes.c_int(2)                       # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), 33, ctypes.byref(值), ctypes.sizeof(值))
        return True
    except Exception:
        return False


def _elide(text, font, max_px):
    """按像素宽截断并加省略号（Tk 的 Label 不会自己省略，长说明会硬切）。"""
    try:
        if font.measure(text) <= max_px:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid] + "…") <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return (text[:lo] + "…") if lo else "…"
    except Exception:
        return text


class PresetMenu:
    """一张弹出卡片。`on_pick(条目)` 负责真正把条目加进清单。"""

    def __init__(self, root, theme, existing_lines, anchor, on_pick,
                 get_existing=None):
        self.root = root
        self.theme = dict(theme or {})
        self.on_pick = on_pick
        self.get_existing = get_existing
        self.anchor = anchor
        self._photos = []
        self._rows = []            # [(条目, 行容器, 徽章 label)]
        self._closed = False
        self._sel = -1

        card = self.theme.get("entry_bg", "#ffffff")
        self._card = card
        self._line = _mix(card, self.theme.get("fg", "#000000"), 0.14)
        self._hover = self.theme.get("hover_bg", "#e9eef5")
        self._fg = self.theme.get("fg", "#000000")
        self._muted = self.theme.get("muted_fg", "#6b6b6b")
        self._ok = self.theme.get("ok_fg", "#2e7d32")
        self._dark = is_dark_theme(self.theme)

        win = tk.Toplevel(root)
        self.win = win
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=self._line)               # 1px 描边：外层露出来的那一圈

        outer = tk.Frame(win, bg=card, bd=0, highlightthickness=0)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(outer, bg=card)
        head.pack(fill="x", padx=px(_PAD), pady=(px(10), 0))
        tk.Label(head, text="＋ 常用目录", bg=card, fg=self._fg,
                 font=_FONT_TITLE, anchor="w").pack(side="left")
        tk.Label(head, text=trp("点一下即加入「其它文件」清单"), bg=card,
                 fg=self._muted, font=_FONT_DESC, anchor="e").pack(side="right")

        self.body = tk.Frame(outer, bg=card)
        self.body.pack(fill="both", expand=True, padx=px(_PAD), pady=(px(6), px(10)))

        self._build(existing_lines)
        self._place()
        win.deiconify()
        win.update_idletasks()
        _round_corners(win)
        try:
            win.grab_set()                          # 点外面要能关掉（见 _on_press）
        except Exception:
            pass
        try:
            win.focus_force()
        except Exception:
            pass
        try:
            win.lift()                              # 压在按钮/主窗口之上
        except Exception:
            pass
        win.bind("<Button-1>", self._on_press, add="+")
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<FocusOut>", self._on_focus_out, add="+")
        win.bind("<Up>", lambda e: self._move(-1))
        win.bind("<Down>", lambda e: self._move(1))
        win.bind("<Return>", lambda e: self._activate_sel())
        self._root_bid = []
        for ev in ("<Destroy>", "<Unmap>"):
            try:
                fid = root.bind(ev, self._on_root_gone, add="+")
                if fid:
                    self._root_bid.append((ev, fid))
            except Exception:
                pass

    # ---------------- 内容 ----------------
    def _icon(self, entry):
        try:
            from PIL import ImageTk
            from utils.file_icons import config_entry_icon
            im = config_entry_icon(entry, "", size=int(px(_ICON)), dark=self._dark)
            if im is None:
                return None
            ph = ImageTk.PhotoImage(im)
            self._photos.append(ph)
            return ph
        except Exception:
            return None

    def _build(self, existing_lines):
        import tkinter.font as tkfont

        for ch in self.body.winfo_children():
            ch.destroy()
        self._rows = []
        self._photos = []
        card, hover = self._card, self._hover
        groups = preset_groups(existing_lines)
        # 列宽：名称列按最长条目；徽章列**固定**（加完出现 ✓ 时不把说明挤短、不跳）；
        # 说明列吃剩下的，放不下就省略号截断。
        f_name = tkfont.Font(font=_FONT_NAME)
        f_desc = tkfont.Font(font=_FONT_DESC)
        names = [r[0] for _t, rows in groups for r in rows] or [""]
        name_w = max(f_name.measure(n) for n in names) + px(10)
        chip_w = px(96)
        icon_w = px(_ICON) + px(12)
        avail = max(px(120), px(_W) - 2 * px(_PAD) - 2 - icon_w - name_w - chip_w)

        for 标题, 行们 in groups:
            tk.Label(self.body, text=标题, bg=card, fg=self._muted,
                     font=_FONT_SECTION, anchor="w").pack(fill="x", pady=(px(6), px(2)))
            for 条目, 说明, 在了 in 行们:
                row = tk.Frame(self.body, bg=card, height=px(_ROW_H))
                row.pack(fill="x")
                row.pack_propagate(False)

                chip_box = tk.Frame(row, bg=card, width=chip_w, height=px(_ROW_H))
                chip_box.pack(side="right")
                chip_box.pack_propagate(False)
                chip = tk.Label(chip_box, text="", bg=card, fg=self._ok, font=_FONT_DESC,
                                anchor="e")
                chip.pack(side="right", padx=(0, px(8)))

                ph = self._icon(条目)
                if ph is not None:
                    ic = tk.Label(row, image=ph, bg=card, bd=0)
                else:
                    ic = tk.Label(row, text="📁" if 条目.endswith("/") else "📄",
                                  bg=card, fg=self._muted, font=_FONT_DESC)
                ic.pack(side="left", padx=(px(4), px(0)), pady=px(4))

                nf = tk.Frame(row, bg=card, width=name_w, height=px(_ROW_H))
                nf.pack(side="left")
                nf.pack_propagate(False)
                name = tk.Label(nf, text=条目, bg=card, fg=self._fg, font=_FONT_NAME,
                                anchor="w")
                name.pack(side="left", fill="y")

                desc = tk.Label(row, text=_elide(说明, f_desc, avail), bg=card,
                                fg=self._muted, font=_FONT_DESC, anchor="w")
                desc.pack(side="left", fill="x", expand=True)

                if 在了:
                    chip.configure(text="✓ " + trp("已在清单"))
                kids = [row, ic, nf, name, desc, chip, chip_box]
                for w in kids:
                    w.bind("<Button-1>", lambda e, x=条目: self._pick(x))
                    w.bind("<Enter>", lambda e, ws=kids: self._hover_on(ws))
                    w.bind("<Leave>", lambda e, ws=kids: self._hover_off(ws))
                self._rows.append((条目, kids, chip))
        if self._rows:
            self._sel = -1

    def _paint(self, kids, color):
        for w in kids:
            try:
                w.configure(bg=color)
            except Exception:
                pass

    def _hover_on(self, kids):
        self._paint(kids, self._hover)

    def _hover_off(self, kids):
        self._paint(kids, self._card)

    # ---------------- 交互 ----------------
    def _pick(self, entry):
        try:
            if self.on_pick:
                self.on_pick(entry)
        finally:
            self.update(self.get_existing() if self.get_existing else None)

    def _activate_sel(self):
        if 0 <= self._sel < len(self._rows):
            self._pick(self._rows[self._sel][0])

    def _move(self, d):
        if not self._rows:
            return
        if self._sel >= 0:
            self._paint(self._rows[self._sel][1], self._card)
        self._sel = (self._sel + d) % len(self._rows)
        self._paint(self._rows[self._sel][1], self._hover)

    def _on_press(self, e):
        """点卡片外面就关（grab 之后外面的点击也会送到这里，坐标是越界的）。"""
        try:
            if not (0 <= e.x < self.win.winfo_width() and 0 <= e.y < self.win.winfo_height()):
                self.close()
        except Exception:
            pass

    def _on_focus_out(self, e=None):
        """切到别的程序时自动收起（弹窗不是置顶窗，留着会"卡"在屏幕上一段时间）。"""
        try:
            self.win.after(90, self._maybe_close_on_focus)
        except Exception:
            pass

    def _maybe_close_on_focus(self):
        if self._closed:
            return
        try:
            import ctypes
            from ctypes import wintypes
            fg = ctypes.windll.user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            if int(pid.value) != os.getpid():
                self.close()
        except Exception:
            pass

    def _on_root_gone(self, e=None):
        try:
            if e is not None and getattr(e, "widget", None) is not self.root:
                return
        except Exception:
            pass
        self.close()

    def _place(self):
        """贴着按钮下方展开；下面放不下就翻到按钮上方，再夹回屏幕内。"""
        win = self.win
        win.update_idletasks()
        w = max(win.winfo_reqwidth(), px(_W))
        h = win.winfo_reqheight()
        try:
            bx = self.anchor.winfo_rootx()
            by = self.anchor.winfo_rooty()
            bh = self.anchor.winfo_height()
        except Exception:
            bx, by, bh = 200, 200, 30
        x = bx
        y = by + bh + px(6)
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            if y + h > sh - px(8):
                y = by - h - px(6)
            y = max(px(8), min(y, sh - h - px(8)))
            x = max(px(8), min(x, sw - w - px(8)))
        except Exception:
            pass
        win.geometry("%dx%d+%d+%d" % (w, h, int(x), int(y)))

    # ---------------- 状态 ----------------
    def update(self, existing_lines):
        """清单变了：重建行上的「已在清单」徽章（弹窗保持打开、位置不变）。"""
        if existing_lines is None or self._closed:
            return
        try:
            self._build(existing_lines)
        except Exception:
            pass

    def alive(self):
        try:
            return (not self._closed) and bool(self.win.winfo_exists())
        except Exception:
            return False

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.win.grab_release()
        except Exception:
            pass
        for ev in ("<Destroy>", "<Unmap>"):
            try:
                self.root.unbind(ev, self._root_bid.get(ev)) \
                    if getattr(self, "_root_bid", None) else None
            except Exception:
                pass
        try:
            self.win.destroy()
        except Exception:
            pass
        # 焦点还给主窗口：弹窗里 focus_force 过，不还的话输入框那边看起来"点不了"。
        # 但只在**前台还是本程序**时才抢：用户点了别的程序，不能把焦点抢回来。
        try:
            self.root.focus_force()
        except Exception:
            pass

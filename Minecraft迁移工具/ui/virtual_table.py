# -*- coding: utf-8 -*-
"""自绘的虚拟化表格控件，用来替代 ttk.Treeview。

为什么要自己画：ttk.Treeview 只要修改任意一行的 tag，就会重绘整个可见区域
（本机实测 26~39ms），鼠标悬停高亮因此明显滞后；而且它无法只给某一列的文字上色。

本控件只把可见行画在 Canvas 上，悬停时仅重绘受影响的两行（约 1ms），
并且状态列可以用彩色圆点单独表达。

数据通过 model 回调按需读取，不复制、不缓存整表：

    row_count()        -> int
    cell(row, key)     -> str                单元格文本
    dot(row)           -> (颜色, 文本) | None  仅状态列使用
    tags(row)          -> 行标签元组          决定整行底色
"""

import tkinter as tk
import tkinter.font as tkfont

from utils.helpers import lighten_color

# 7x7 实心圆（透明背景）
_DOT_PATTERN = (
    "  ###  ",
    " ##### ",
    "#######",
    "#######",
    "#######",
    " ##### ",
    "  ###  ",
)


class VirtualTable(tk.Frame):
    """虚拟化表格。columns 为 [(key, title, width, anchor)]，anchor 取 "w"/"e"/"center"。"""

    def __init__(self, master, columns, theme, model=None, tag_styles=None,
                 hover_style=None, row_height=23, header_height=28,
                 status_key="status", font=("微软雅黑", 11),
                 on_row_click=None, on_header_click=None, on_row_hover=None,
                 on_leave=None, on_scroll=None):
        super().__init__(master, bg=theme.get("bg", "#ffffff"))

        self.theme = theme
        self.columns = [tuple(c) for c in columns]
        self.model = model
        self.row_height = row_height
        self.header_height = header_height
        self.status_key = status_key
        self.on_row_click = on_row_click
        self.on_header_click = on_header_click
        self.on_row_hover = on_row_hover
        self.on_leave = on_leave
        self.on_scroll = on_scroll

        self._font = tkfont.Font(family=font[0], size=font[1])
        self._font_bold = tkfont.Font(family=font[0], size=font[1], weight="bold")
        self._dot_cache = {}
        self._fit_cache = {}

        self._row_count = 0
        self._rendered = {}      # 行号 -> 槽位（只保留当前可见的行）
        self._free_slots = []    # 空闲槽位，滚动时复用，避免反复创建/销毁
        self._first = 0
        self._hover_row = -1
        self._hover_col = None       # 鼠标经过的列头（列头点了能排序，得有高亮反馈）
        self.sort_col = None
        self.sort_rev = False

        self._widths = [c[2] for c in self.columns]
        self._col_x = []
        _x = 0
        for _w in self._widths:
            self._col_x.append(_x)
            _x += _w
        self._total_width = _x
        self._layout_after = None
        self._hsb_shown = True

        self._reload_theme(theme, tag_styles, hover_style)
        self._build()
        self._bind_events()
        # 平滑滚动：这张表按行增量滚（整屏 moveto 要 66ms，按行只要 12ms），交给
        # for_rows 攒零头做动画。**必须放在 _bind_events() 之后**：滚轮绑定是谁后绑谁生效，
        # 早绑会被它自己的处理顶掉（那一个 return "break" 会把后面的链子全掐断）。
        self._scroller = None
        try:
            from utils.helpers import SmoothScroller
            self._scroller = SmoothScroller.for_rows(
                self.body, self.row_height, on_render=self._after_scroll,
                bind_widgets=[self.body, self.header])
        except Exception:
            self._scroller = None

    # ------------------------------------------------------------------ 主题
    def _reload_theme(self, theme, tag_styles=None, hover_style=None):
        self.theme = theme
        self._base_bg = theme.get("ttk_bg", "#ffffff")
        self._base_fg = theme.get("ttk_fg", "#000000")
        self._border = theme.get("border", "#c8c8c8")
        # 列头悬停高亮：和按钮同一套规则——把常态底色整体调亮一档
        self._header_hover_bg = lighten_color(self._base_bg)
        if tag_styles is not None:
            self.tag_styles = dict(tag_styles)
        else:
            self.tag_styles = {
                "checked": (theme.get("card_sel_bg", "#d4e6f8"), theme.get("card_sel_fg", "#0d3d63")),
                "missing": (theme.get("danger_bg", "#ffb3b3"), theme.get("danger_fg", "#8b0000")),
                "new": (theme.get("warn_bg", "#ffeaa7"), theme.get("warn_fg", "#000000")),
            }
        if hover_style is not None:
            self.hover_style = tuple(hover_style)
        else:
            self.hover_style = (theme.get("hover_bg", "#e9eef5"),
                                theme.get("hover_fg", "#000000"))

    def apply_theme(self, theme):
        """主题切换后重新配色并重绘（不改动数据/顺序）。"""
        self._reload_theme(theme)
        self._fit_cache.clear()
        try:
            self.configure(bg=theme.get("bg", "#ffffff"))
            self.header.configure(bg=self._base_bg)
            self.body.configure(bg=self._base_bg)
            self.corner.configure(bg=self._base_bg)
        except Exception:
            pass
        self._draw_header()
        self._invalidate()
        self._render()

    # -------------------------------------------------------------- 构建/绑定
    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header = tk.Canvas(self, height=self.header_height, bd=0,
                                highlightthickness=0, bg=self._base_bg)
        self.header.grid(row=0, column=0, sticky="ew")
        self.corner = tk.Frame(self, bg=self._base_bg, width=16, height=self.header_height)
        self.corner.grid(row=0, column=1, sticky="nsew")
        self.corner.grid_propagate(False)

        self.body = tk.Canvas(self, bd=0, highlightthickness=0, bg=self._base_bg)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.configure(yscrollincrement=self.row_height)

        self.vsb = tk.Scrollbar(self, orient="vertical", command=self._yview)
        self.vsb.grid(row=1, column=1, sticky="ns")
        self.hsb = tk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.hsb.grid(row=2, column=0, sticky="ew")

        self.body.configure(yscrollcommand=self._on_yscroll,
                            xscrollcommand=self._on_xscroll)
        self._draw_header()

    def _after_scroll(self):
        """滚动后重绘可见行，并通知外部（比如收起悬停提示）。"""
        self._render()
        if self.on_scroll:
            try:
                self.on_scroll()
            except Exception:
                pass

    def _bind_events(self):
        self.header.bind("<Button-1>", self._ev_header_click)
        self.header.bind("<Motion>", self._ev_header_motion)
        self.header.bind("<Leave>", self._ev_header_leave)
        self.body.bind("<Button-1>", self._ev_click)
        self.body.bind("<Motion>", self._ev_motion)
        self.body.bind("<Leave>", self._ev_leave)
        # 纵向滚轮交给 SmoothScroller（在 __init__ 里装），这里只管横向
        self.body.bind("<Shift-MouseWheel>", self._ev_wheel_x)
        self.body.bind("<Configure>", self._ev_configure)

    # ------------------------------------------------------------------- 表头
    def _draw_header(self):
        c = self.header
        c.delete("all")
        h = self.header_height
        c.create_rectangle(0, 0, self._total_width, h, fill=self._base_bg, width=0)
        # 鼠标经过的那一列先铺一块高亮底，再画分隔线和文字（文字在最上层）
        j0 = self._hover_col
        if j0 is not None and 0 <= j0 < len(self.columns) and self.columns[j0][1]:
            c.create_rectangle(self._col_x[j0], 0,
                               self._col_x[j0] + self._widths[j0], h,
                               fill=self._header_hover_bg, width=0)
        for j, (key, title, _w, anchor) in enumerate(self.columns):
            width = self._widths[j]
            x = self._col_x[j]
            c.create_line(x, 0, x, h, fill=self._border)
            label = title
            if self.sort_col == key and title:
                label = f"{title} {'▼' if self.sort_rev else '▲'}"
            if anchor == "w":
                tx, ax = x + 6, "w"
            elif anchor == "e":
                tx, ax = x + width - 6, "e"
            else:
                tx, ax = x + width // 2, "center"
            # 列头也可能过长（尤其加了图标后），同样按列宽截断，避免压到相邻列
            c.create_text(tx, h // 2, text=self._fit(label, width - 14), anchor=ax,
                          fill=self._base_fg, font=self._font_bold)
        c.create_line(0, h - 1, self._total_width, h - 1, fill=self._border)
        c.configure(scrollregion=(0, 0, self._total_width, h))

    def set_sort(self, col, rev):
        self.sort_col = col
        self.sort_rev = rev
        self._draw_header()

    # ------------------------------------------------------------- 列宽自适应
    def _relayout(self, avail=None):
        """按可视宽度重算列宽：把富余空间按各列基础宽度比例分配，填满窗口，
        避免右侧留白。返回列宽是否真的变了。"""
        if avail is None:
            avail = self.body.winfo_width()
        if avail <= 1:
            return False
        base = [c[2] for c in self.columns]
        total_base = sum(base)
        if avail <= total_base:
            # 比基础列宽还窄：按比例压缩，让表格始终贴合窗口而不是冒出横向滚动条
            scale = avail / total_base
            widths = [max(1, int(w * scale)) for w in base]
            widths[-1] += avail - sum(widths)
        else:
            extra = avail - total_base
            widths = [w + int(extra * w / total_base) for w in base]
            widths[-1] += avail - sum(widths)      # 余数补到最后一列
        if widths == self._widths:
            return False
        self._widths = widths
        self._col_x = []
        x = 0
        for w in widths:
            self._col_x.append(x)
            x += w
        self._total_width = x
        self._update_scrollregion()
        self._sync_hscroll()
        return True

    def _sync_hscroll(self):
        """只有在内容真的超过可视宽度时才显示横向滚动条。
        列宽现在是自适应填满的，平时它都是多余的，常驻着只会让人以为可以横向拖动。"""
        need = self._total_width > self.body.winfo_width() + 1
        try:
            if need and not self._hsb_shown:
                self.hsb.grid()
                self._hsb_shown = True
            elif not need and self._hsb_shown:
                self.hsb.grid_remove()
                self._hsb_shown = False
        except Exception:
            pass

    def _ev_configure(self, event):
        """窗口缩放：防抖后再重排列宽，拖动过程中不反复重画。"""
        if self._layout_after is not None:
            try:
                self.after_cancel(self._layout_after)
            except Exception:
                pass
        self._layout_after = self.after(80, self._apply_layout)

    def _apply_layout(self):
        self._layout_after = None
        if self._relayout():
            self._draw_header()
            self._invalidate()
            self._render()

    def fit_now(self):
        """立即按当前可视宽度排一次列宽。
        窗口显示前调用，避免显示出来之后才重排造成二次闪烁。"""
        try:
            self._apply_layout()
        except Exception:
            pass

    # ------------------------------------------------------------------- 滚动
    def _yview(self, *args):
        if args and args[0] == "moveto":
            self._scroll_to_fraction(float(args[1]))
        else:
            self.body.yview(*args)
        self._render()

    def _scroll_to_fraction(self, frac):
        """把滚动条的「跳转」换算成按行增量滚动。
        Tk 的 canvas 只有按增量滚动时才会用位块传输复用画面，直接 moveto 会让
        整个可见区域重绘（2000 行时实测约 66ms，而按行滚动约 12ms）。"""
        total = self._row_count * self.row_height
        view = max(self.body.winfo_height(), 1)
        max_top = max(total - view, 0)
        target = int((frac * max_top) // self.row_height)
        cur = int(self.body.canvasy(0) // self.row_height)
        delta = target - cur
        if delta:
            self.body.yview_scroll(delta, "units")
        else:
            self.body.yview_moveto(frac)

    def _xview(self, *args):
        self.body.xview(*args)

    def _on_yscroll(self, first, last):
        self.vsb.set(first, last)

    def _on_xscroll(self, first, last):
        self.hsb.set(first, last)
        try:
            self.header.xview_moveto(first)
        except Exception:
            pass

    def _ev_wheel(self, event):
        self.body.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self._render()
        if self.on_scroll:
            self.on_scroll()
        return "break"

    def _ev_wheel_x(self, event):
        self.body.xview_scroll(-1 if event.delta > 0 else 1, "units")
        if self.on_scroll:
            self.on_scroll()
        return "break"

    # ------------------------------------------------------------------- 数据
    def refresh(self):
        """数据或排序变化后重新渲染（行数会重新读取）。"""
        self._row_count = self.model.row_count() if self.model is not None else 0
        self._hover_row = -1
        self._relayout()          # 首次布局时可视宽度才有效，顺带对齐列宽
        self._update_scrollregion()
        # 就地重画已渲染的行：行号没变时 _paint_slot 会跳过位置设置，
        # 比「先全部隐藏、再整片重建」少一轮无谓的 Tcl 往返。
        for row in list(self._rendered):
            slot = self._rendered[row]
            if row < self._row_count:
                self._paint_slot(slot, row)
            else:
                self._hide_slot(slot)
                self._free_slots.append(slot)
                del self._rendered[row]
        self._render()

    def _invalidate(self):
        """丢弃所有已渲染的行：内容变了必须整片重画。"""
        for slot in self._rendered.values():
            self._hide_slot(slot)
        self._free_slots.extend(self._rendered.values())
        self._rendered.clear()

    def set_model(self, model):
        self.model = model
        self.refresh()

    def _update_scrollregion(self):
        self.body.configure(
            scrollregion=(0, 0, self._total_width, self._row_count * self.row_height))

    @property
    def row_count(self):
        return self._row_count

    def repaint_row(self, row):
        """只重绘一行（扫描进度、勾选等场景，避免整表重画）。"""
        slot = self._rendered.get(row)
        if slot is not None:
            self._paint_slot(slot, row)

    # ------------------------------------------------------------------- 渲染
    def _visible_rows(self):
        height = max(self.body.winfo_height(), self.row_height)
        top = self.body.canvasy(0)
        first = max(0, int(top // self.row_height))
        need = int(height // self.row_height) + 3
        if first + need > self._row_count:
            need = self._row_count - first
        return first, max(0, need)

    def _render(self):
        """增量渲染：只为「新进入视野」的行绘制，移出视野的槽位直接回收复用。
        因此滚动一行只重画一行，而不是整片可见区域。"""
        if self.model is None:
            return
        first, need = self._visible_rows()
        last = first + need
        for row in [r for r in self._rendered if r < first or r >= last]:
            slot = self._rendered.pop(row)
            self._hide_slot(slot)
            self._free_slots.append(slot)
        for row in range(first, last):
            if row in self._rendered:
                continue
            slot = self._free_slots.pop() if self._free_slots else self._create_slot()
            self._paint_slot(slot, row)
            self._rendered[row] = slot
        self._first = first

    def _create_slot(self):
        b = self.body
        return {
            "bg": b.create_rectangle(0, 0, 0, 0, width=0),
            "dot": b.create_image(0, 0, anchor="w"),
            "texts": [b.create_text(0, 0, anchor="w") for _ in self.columns],
        }

    def _hide_slot(self, slot):
        self.body.itemconfigure(slot["bg"], state="hidden")
        self.body.itemconfigure(slot["dot"], state="hidden")
        for t in slot["texts"]:
            self.body.itemconfigure(t, state="hidden")

    def _row_style(self, row, hovered):
        """行配色。语义色（选中/缺失/新添加）优先级高于悬停高亮：
        已经带语义色的行，悬停不再改变其底色，否则「触碰」会把「选中」盖掉。"""
        tag = None
        try:
            for t in (self.model.tags(row) or ()):
                if t in self.tag_styles:
                    tag = t
                    break
        except Exception:
            tag = None
        if tag is not None:
            return self.tag_styles[tag]
        if hovered and self.hover_style:
            return self.hover_style
        return self._base_bg, self._base_fg

    def _paint_slot(self, slot, row):
        b = self.body
        y = row * self.row_height
        mid = y + self.row_height // 2
        # 行号没变时（例如只是刷新数据）位置无需重设，省掉一批 Tcl 调用
        moved = slot.get("row") != row
        slot["row"] = row
        bg, fg = self._row_style(row, row == self._hover_row)
        if moved:
            b.coords(slot["bg"], 0, y, self._total_width, y + self.row_height)
        b.itemconfigure(slot["bg"], fill=bg, state="normal")
        dot_shown = False
        for j, (key, title, _w, anchor) in enumerate(self.columns):
            width = self._widths[j]
            item = slot["texts"][j]
            x = self._col_x[j]
            dot = None
            if key == self.status_key:
                try:
                    dot = self.model.dot(row)
                except Exception:
                    dot = None
                if dot:
                    text = dot[1]
                    tx, ax, avail = x + 22, "w", width - 26
                else:
                    text, tx, ax, avail = "", x + 6, "w", width - 12
            else:
                try:
                    text = self.model.cell(row, key)
                except Exception:
                    text = ""
                if anchor == "e":
                    tx, ax = x + width - 6, "e"
                elif anchor == "center":
                    tx, ax = x + width // 2, "center"
                else:
                    tx, ax = x + 6, "w"
                avail = width - 12
            if moved:
                b.coords(item, tx, mid)
                b.itemconfigure(item, anchor=ax)
            if key == self.status_key and dot:
                b.coords(slot["dot"], x + 7, mid)
                b.itemconfigure(slot["dot"], image=self._dot(dot[0]), state="normal")
                dot_shown = True
            b.itemconfigure(item, text=self._fit(text, avail), fill=fg, state="normal")
        if not dot_shown:
            b.itemconfigure(slot["dot"], state="hidden")

    # ------------------------------------------------------------- 文本与圆点
    def _fit(self, text, avail):
        """按可用宽度截断文本（Canvas 没有裁剪，只能自己算）。"""
        if not text:
            return ""
        text = str(text)
        key = (text, avail)
        hit = self._fit_cache.get(key)
        if hit is not None:
            return hit
        if avail <= 0:
            out = ""
        elif self._font.measure(text) <= avail:
            out = text
        else:
            ell = "…"
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._font.measure(text[:mid] + ell) <= avail:
                    lo = mid
                else:
                    hi = mid - 1
            out = text[:lo] + ell
        if len(self._fit_cache) > 6000:
            self._fit_cache.clear()
        self._fit_cache[key] = out
        return out

    def _dot(self, color):
        img = self._dot_cache.get(color)
        if img is None:
            img = tk.PhotoImage(width=7, height=7)
            for y, line in enumerate(_DOT_PATTERN):
                for x, ch in enumerate(line):
                    if ch == "#":
                        img.put(color, to=(x, y, x + 1, y + 1))
            self._dot_cache[color] = img
        return img

    # ------------------------------------------------------------------- 事件
    def row_at(self, y):
        """屏幕 y -> 行号，不在任何行上时返回 -1。"""
        row = int(self.body.canvasy(y) // self.row_height)
        return row if 0 <= row < self._row_count else -1

    def col_at(self, x):
        """屏幕 x -> 列 key，不在任何列上时返回 None。"""
        cx = self.body.canvasx(x)
        for j, (key, title, _w, anchor) in enumerate(self.columns):
            start = self._col_x[j]
            if start <= cx < start + self._widths[j]:
                return key
        return None

    def _ev_motion(self, event):
        row = self.row_at(event.y)
        if row != self._hover_row:
            old = self._hover_row
            self._hover_row = row
            self.repaint_row(old)
            self.repaint_row(row)
        if self.on_row_hover:
            self.on_row_hover(row, event)

    def _ev_leave(self, event):
        old = self._hover_row
        self._hover_row = -1
        self.repaint_row(old)
        if self.on_leave:
            self.on_leave(event)

    def _ev_click(self, event):
        row = self.row_at(event.y)
        if row >= 0 and self.on_row_click:
            self.on_row_click(row, event)

    def _ev_header_motion(self, event):
        """列头鼠标经过：高亮那一格（列头点一下能排序，得给反馈）。"""
        x = self.header.canvasx(event.x)
        col = None
        for j, (key, title, _w, anchor) in enumerate(self.columns):
            if title and self._col_x[j] <= x < self._col_x[j] + self._widths[j]:
                col = j
                break
        if col != self._hover_col:
            self._hover_col = col
            self._draw_header()

    def _ev_header_leave(self, event):
        if self._hover_col is not None:
            self._hover_col = None
            self._draw_header()

    def _ev_header_click(self, event):
        x = self.header.canvasx(event.x)
        for j, (key, title, _w, anchor) in enumerate(self.columns):
            cx = self._col_x[j]
            if cx <= x < cx + self._widths[j]:
                if self.on_header_click:
                    self.on_header_click(key, event)
                return

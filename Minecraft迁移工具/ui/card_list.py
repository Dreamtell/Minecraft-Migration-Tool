# ui/card_list.py
"""PCL2 风格的模组卡片列表（只读预览）。

为什么单独写一个控件：主界面的迁移清单是 Text 控件（要支持粘贴、拖拽、撤销编辑），
做不了卡片；而"放大查看"窗口是只读的，正适合这种一行一卡片的富信息列表。

实现要点：
- 只画可见行（算好首行/末行，其余不建 item），滚动时整块重画——
  可见行只有十几行，一次重画 ≈ 3ms，比槽位复用简单且够快。
- 滚动交给 SmoothScroller（像素级），跟其它列表手感一致。
- 图标懒加载：只有滚到的行才去解 jar 取图标（9ms/个，缓存后 0ms）。
- 单击行体 = 勾选（和表格视图一致），双击 = 打开详情；双击由 Tk 原生
  <Double-Button-1> 负责，间隔就是系统设置里的鼠标双击速度，程序不自己判。
"""
import tkinter as tk
import tkinter.font as tkfont

from utils.helpers import SmoothScroller, is_dark_theme

# 分类标签的配色（中间调 + 白字，深浅主题下都清楚）
TAG_COLORS = {
    "优化":     ("#2e7d32", "#ffffff"),
    "画面":     ("#6a1b9a", "#ffffff"),
    "信息显示": ("#0277bd", "#ffffff"),
    "科技":     ("#00838f", "#ffffff"),
    "魔法":     ("#4527a0", "#ffffff"),
    "冒险":     ("#ef6c00", "#ffffff"),
    "装备":     ("#c62828", "#ffffff"),
    "存储":     ("#5d4037", "#ffffff"),
    "建筑":     ("#558b2f", "#ffffff"),
    "生物":     ("#ad1457", "#ffffff"),
    "农业":     ("#33691e", "#ffffff"),
    "任务":     ("#00695c", "#ffffff"),
    "音效":     ("#283593", "#ffffff"),
    "前置库":   ("#546e7a", "#ffffff"),
    "多人":     ("#455a64", "#ffffff"),
    # 加载器品牌色：Fabric 用的就是它 logo 上那块布料的米黄（#dbb69b），配深棕字才看得清
    "Fabric":   ("#dbb69b", "#3b2c22"),
    "Quilt":    ("#8b5cf6", "#ffffff"),
    "NeoForge": ("#f16436", "#ffffff"),
    "Forge":    ("#5b6e7f", "#ffffff"),
}


# 圆角图缓存：卡片行/标签 chip 都用它（键=尺寸+圆角+颜色）
_ROUND_CACHE = {}


def rounded_image(w, h, radius, color, supersample=4):
    """生成一张圆角矩形图（四角透明，交给 Tk 跟画布底色混）。

    和渐变按钮同一套路：Tk 的 canvas 画不出圆角矩形，只能先渲成图再贴。
    按 尺寸+圆角+颜色 缓存，所以每个宽度只需要渲一次。
    """
    key = (w, h, radius, color)
    hit = _ROUND_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from PIL import Image, ImageDraw
        s = max(1, int(supersample))
        im = Image.new("RGBA", (max(1, w * s), max(1, h * s)), (0, 0, 0, 0))
        ImageDraw.Draw(im).rounded_rectangle(
            [0, 0, w * s - 1, h * s - 1], radius=max(0, int(radius * s)), fill=color)
        im = im.resize((max(1, w), max(1, h)), Image.LANCZOS)
    except Exception:
        return None
    if len(_ROUND_CACHE) > 300:
        # 展开动画每帧一个新宽度，缓存会持续增长（一张最大约 200KB），给个上限兜住
        _ROUND_CACHE.clear()
    _ROUND_CACHE[key] = im
    return im


def default_fallback_icon(size=44, bg="#7a7a7a", fg="#ffffff"):
    """给没有图标的模组画一个兜底图标（代码生成，不依赖外部素材）。

    为什么不用仓库里的 cube PNG：打包成单文件 exe 后素材路径依赖 --add-data，
    现画一个最省事也最稳。
    """
    try:
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        s = size * 2
        d.rounded_rectangle([2, 2, s - 2, s - 2], radius=int(s * 0.22), fill=bg)
        # 一个简化的"箱子"轮廓
        m = int(s * 0.26)
        d.rectangle([m, m + int(s * 0.08), s - m, s - m], outline=fg, width=max(2, s // 20))
        d.line([m, m + int(s * 0.08), s // 2, m - int(s * 0.04),
                s - m, m + int(s * 0.08)], fill=fg, width=max(2, s // 20))
        d.line([s // 2, m - int(s * 0.04), s // 2, s - m], fill=fg, width=max(2, s // 24))
        return im.resize((size, size), Image.LANCZOS)
    except Exception:
        return None


class ModCardList(tk.Frame):
    ROW_H = 64            # 卡片行高
    ICON_PX = 44          # 图标显示边长
    PAD = 8
    MARGIN_X = 6          # 卡片左右外边距（圆角要看得见）
    GAP = 6               # 卡片之间的竖直间隙
    RADIUS = 8            # 卡片圆角半径
    ACTION_W = 22         # 悬停操作图标（详情/定位/移除）的边长

    # 双击由 Tk 原生 <Double-Button-1> 负责（间隔 = 系统设置里的鼠标双击速度），
    # 程序里不再自己算时间间隔，也不再自适应。

    def __init__(self, master, theme, icon_provider=None, on_click=None,
                 on_double_click=None, on_action=None, on_check=None,
                 on_context=None, fallback_icon=None):
        super().__init__(master, bg=theme["bg"])
        self.theme = theme
        self.rows = []
        self.icon_provider = icon_provider          # 函数(row) -> PIL.Image 或 None
        self.on_click = on_click                    # 函数(index, event)
        self.on_double_click = on_double_click
        self.on_action = on_action                  # 函数(index, "info"|"remove")
        self.on_check = on_check                    # 函数(index)：切换该行勾选
        self.on_context = on_context                # 函数(index, event)：右键菜单
        self.fallback_icon = fallback_icon          # PIL.Image
        self._photos = {}                           # 图标路径 -> PhotoImage（留引用）
        self._misc_photos = {}                      # 圆角底/chip 的 PhotoImage
        self._elide_cache = {}                      # (宽度, 原文) -> 截断结果
        self._top = 0.0                             # 滚动位置（像素，唯一真源）
        self._drawn_top = None                      # 上次整块重画时的 _top（None=还没画）
        self._drawn_first = None
        self._hover = -1
        self._sel = -1
        self._action_rects = {}                     # 行号 -> [(x0,y0,x1,y1,action)]
        self._hover_action = None
        self._sel_anim = {}                         # 行号 -> {"t": 选中进度 0~1, "job": id}

        self.canvas = tk.Canvas(self, bg=theme["bg"], highlightthickness=0, bd=0,
                                takefocus=1)
        self.vsb = tk.Scrollbar(self, orient="vertical", command=self._on_scrollbar)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self._font_title = tkfont.Font(family="微软雅黑", size=11, weight="bold")
        self._font_sub = tkfont.Font(family="微软雅黑", size=9)
        self._font_desc = tkfont.Font(family="微软雅黑", size=9)

        self.canvas.bind("<Configure>", lambda e: self._render())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click_evt)
        # 双击用 Tk 原生事件（间隔就是系统设置里那个，程序不再自己判、也不再自适应）
        if on_double_click is not None:
            self.canvas.bind("<Double-Button-1>", self._on_double_evt)
        self.canvas.bind("<Button-3>", self._on_context_evt)
        # 键盘也要能用：↑↓ 一行、PgUp/PgDn 一屏、Home/End 两端、空格切勾选
        for seq in ("<Up>", "<Down>", "<Prior>", "<Next>", "<Home>", "<End>", "<space>"):
            self.canvas.bind(seq, self._on_key)

        # 平滑滚动：位置用**像素**记（self._top），每帧只挪十几像素，看起来才是连续的。
        # 绝不能调 canvas.yview_scroll：画布真滚起来，画出的行会整体位移，而命中测试
        # 按客户区坐标算 —— 表现就是"鼠标位置和实际反馈的行对不上"。
        acc = {"v": 0.0}

        def mover(px):
            acc["v"] += px
            if abs(acc["v"]) < 0.5:
                return 0, False
            step, acc["v"] = acc["v"], 0.0
            before = self._top
            self._set_top(self._top + step)
            moved = self._top - before
            if abs(moved) < 0.01:
                return 0, True                 # 到顶/到低了
            # 必须返回**带符号**的位移：引擎做的是 self._left -= consumed，
            # 返回绝对值的话向上滚会把剩余量越减越大（越滚越快，一帧比一帧猛）。
            return moved, False

        self._scroller = SmoothScroller(
            self.canvas, mover, px_per_notch=self.ROW_H,   # 一格滚一张卡，原来的 ROW_H*3 太快
            bind_widgets=[self.canvas])

    # ------------------------------------------------------ 滚动位置（像素）
    @property
    def _first(self):
        """视口顶部所在行号 —— 由像素位置换算，只读。"""
        return int(self._top // self.ROW_H)

    @property
    def _offset(self):
        """视口顶部相对 _first 行顶部的像素偏移（0 ~ ROW_H-1）。"""
        return self._top - self._first * self.ROW_H

    def _set_top(self, top):
        """改滚动位置并刷新画面。

        重建全部 item 实测 6.9ms，`canvas.move` 只要 1.2ms —— 所以像素滚动期间
        （视野里的行没变）只平移，只有**新的一行进入视野**时才整块重画。
        平移是纯视觉的：命中测试始终按 _top 算逻辑行，所以不会跟着漂。
        """
        top = max(0.0, min(self._max_top(), top))
        if abs(top - self._top) < 0.01:
            return
        self._top = top
        if self._drawn_top is None or self._first != self._drawn_first:
            self._render()                      # 行集合变了，必须重建
            return
        dy = self._top - self._drawn_top
        if abs(dy) >= 0.4:
            self.canvas.move("all", 0, -dy)
            self._shift_rects(-dy)
            self._drawn_top = self._top
        self._update_scrollbar()

    def _shift_rects(self, dy):
        """平移画面后，命中矩形也要跟着挪，否则悬停图标会点不中。"""
        for i, r in list(self._action_rects.items()):
            if not r:
                continue
            self._action_rects[i] = [(x0, y0 + dy, x1, y1 + dy, a)
                                     for x0, y0, x1, y1, a in r]

    # ------------------------------------------------------------------ 数据
    def set_rows(self, rows):
        """rows: [{title, subtitle, version, desc, icon_key, ...}]"""
        self.rows = list(rows or [])
        self._top = 0.0
        # _elide_cache 不清：同一个字符串+同一个宽度，截断结果恒定，清掉只会在
        # 后台元数据刷新（set_rows 被反复调用）时白算一遍。上限 800 条自动兜住。
        self._hover = -1
        self._sel = -1
        self._update_scrollbar()
        self._render()

    def refresh_row(self, index):
        """只重画某一行（悬停反馈/数据已更新时用）。"""
        if 0 <= index < len(self.rows) and self._first <= index <= self._first + self._rows_per_page() + 1:
            self._draw_row(index)

    def update_row(self, index, row_dict):
        """替换某一行的数据并重画（后台元数据扫描到这一行时用）。

        注意别只调 refresh_row：那只是重画，数据还是扫描前抓的旧值。
        """
        if 0 <= index < len(self.rows):
            self.rows[index] = row_dict
            self.refresh_row(index)

    def apply_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme["bg"])
        self.canvas.configure(bg=theme["bg"])
        self._render()

    # ------------------------------------------------------------------ 滚动
    def _view_h(self):
        h = self.canvas.winfo_height()
        return h if h > 1 else self.ROW_H * 10      # 还没布局完时先给个合理值

    def _rows_per_page(self):
        return max(1, self._view_h() // self.ROW_H)

    def _total_px(self):
        return len(self.rows) * self.ROW_H

    def _max_top(self):
        return max(0.0, self._total_px() - self._view_h())

    def _max_first(self):
        return self._max_top() / self.ROW_H

    def _update_scrollbar(self):
        total = max(self._total_px(), 1)
        first = self._top / total
        last = min(1.0, (self._top + self._view_h()) / total)
        try:
            self.vsb.set(first, last)
        except Exception:
            pass

    def _on_scrollbar(self, *args):
        """拖滚动条：位置仍是像素，所以拖起来是连续的，不是整行跳。"""
        if not self.rows:
            return
        if args and args[0] == "moveto":
            try:
                frac = float(args[1])
            except Exception:
                return
            self._set_top(frac * self._total_px())
        elif args and args[0] == "scroll":
            try:
                step = int(args[1])
            except Exception:
                return
            pages = args[2] == "pages"
            delta = step * (self._view_h() if pages else self.ROW_H)
            self._set_top(self._top + delta)
        self._update_scrollbar()

    def _on_wheel(self, event):
        return "break"          # 交给 SmoothScroller（它已经绑了 MouseWheel）

    def scroll_by_rows(self, rows):
        self._set_top(self._top + rows * self.ROW_H)
        self._update_scrollbar()

    # ---------------------------------------------------------------- 鼠标
    def _row_at(self, y):
        """命中测试：把客户区 y 换算回"画布坐标"，再除行高。

        必须加上 _offset（像素滚动的零头），否则滚到半行时鼠标会选中相邻行 ——
        这正是之前"鼠标位置和列表反馈对不上"的另一半原因。
        """
        idx = self._first + int((y + self._offset) // self.ROW_H)
        return idx if 0 <= idx < len(self.rows) else -1

    def _on_motion(self, event):
        idx = self._row_at(event.y)
        act = self._action_at(idx, event.x, event.y)
        if idx != self._hover or act != self._hover_action:
            old = self._hover
            self._hover, self._hover_action = idx, act
            self._render_rows({old, idx})

    def _action_at(self, idx, x, y):
        """命中检测：鼠标是不是落在某一行的操作图标上。"""
        for x0, y0, x1, y1, act in self._action_rects.get(idx, []):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return act
        return None

    def _on_leave(self, event=None):
        if self._hover != -1:
            old, self._hover = self._hover, -1
            self._hover_action = None
            self._render_rows({old})

    def _on_key(self, event):
        """键盘操作：滚动走同一套平滑动画（不然键盘是一跳一跳的，跟滚轮两手感）。"""
        k = event.keysym
        page = max(1, self._rows_per_page() - 1) * self.ROW_H
        if k == "Up":
            self._scroller.scroll_px(-self.ROW_H)
        elif k == "Down":
            self._scroller.scroll_px(self.ROW_H)
        elif k == "Prior":
            self._scroller.scroll_px(-page)
        elif k == "Next":
            self._scroller.scroll_px(page)
        elif k == "Home":
            self._set_top(0.0)
        elif k == "End":
            self._set_top(self._max_top())
        elif k == "space":
            idx = self._sel if self._sel >= 0 else self._hover
            if idx >= 0 and self.on_check:
                self._toggle_with_anim(idx)
            else:
                return None
        else:
            return None
        return "break"

    def _on_context_evt(self, event):
        """右键：先选中该行（菜单里的操作默认作用于它），再把菜单交给调用方弹。"""
        idx = self._row_at(event.y)
        if idx >= 0 and idx != self._sel:
            old, self._sel = self._sel, idx
            self._render_rows({old, idx})
        if self.on_context is not None:
            try:
                self.on_context(idx, event)
            except Exception:
                pass
        return "break"

    def set_checked(self, index, value):
        """由外部改完勾选状态后同步这一行（不重排、不整屏重画）。"""
        if 0 <= index < len(self.rows):
            self.rows[index]["checked"] = bool(value)
            self.refresh_row(index)

    def _toggle_instant(self, idx):
        """不做动画地把选中态翻回去（双击撤销第一下时用）。

        双击的第二下本来就会把状态翻回原样，但如果让它走动画，用户会看到
        "高亮唰地亮起来、又唰地收回去" —— 双击应该完全不动高亮（原来是亮的就还亮着，
        原来没亮就还没亮）。这里直接停掉本行的动画、清掉动画进度，让重画读数据里的最终值。
        """
        if not (0 <= idx < len(self.rows)) or self.on_check is None:
            return
        anim = self._sel_anim.pop(idx, None)
        if anim and anim.get("job") is not None:
            try:
                self.after_cancel(anim["job"])
            except Exception:
                pass
        self.on_check(idx)                     # 外部翻数据并触发本行重画
        self._set_sel_progress(idx, self.rows[idx].get("checked"))

    def _set_sel_progress(self, idx, checked):
        """把某行的选中进度直接打到终值（不带动画），并只重画这一行。"""
        try:
            self._sel_anim.pop(idx, None)
            self._sel_anim[idx] = {"t": 1.0 if checked else 0.0, "to": 1.0 if checked else 0.0,
                                   "job": None, "final": True}
            self._render_rows([idx])
            self._sel_anim.pop(idx, None)
        except Exception:
            pass

    def _toggle_with_anim(self, idx):
        """切换某行的选中状态，并让高亮"渐变过去"（勾上=蓝底淡入，取消=淡出）。

        注意顺序：先登记动画（进度=切换前的状态），再调 on_check 让外部改数据。
        外部改完数据会触发本行的重画，而重画时读的是"动画当前进度"，
        所以不会先闪一下最终状态再倒回去。
        """
        if not (0 <= idx < len(self.rows)) or self.on_check is None:
            return
        was = 1.0 if self.rows[idx].get("checked") else 0.0
        self._animate_sel(idx, was, 0.0 if was > 0.5 else 1.0)
        self.on_check(idx)

    def _animate_sel(self, idx, from_t, to_t, frames=12, frame_ms=16):
        """某一行的展开进度从 from_t 走到 to_t（只有这一行重画，很便宜）。

        用**缓出**曲线（前快后慢）：高亮"唰"地窜出去、末尾轻轻收住，PCL 那种灵动感
        主要就来自这条曲线；匀速看着像机械拉伸。12 帧 × 16ms ≈ 190ms。
        """
        old = self._sel_anim.get(idx)
        if old and old.get("job") is not None:
            try:
                self.after_cancel(old["job"])
            except Exception:
                pass
        anim = {"t": float(from_t), "to": float(to_t), "job": None, "n": 0}
        self._sel_anim[idx] = anim

        def step():
            anim["n"] += 1
            if anim["n"] >= frames:
                self._sel_anim.pop(idx, None)      # 结束：交回数据里的最终状态
                self.refresh_row(idx)
                return
            p = anim["n"] / frames
            eased = 1 - (1 - p) ** 3               # ease-out-cubic
            anim["t"] = from_t + (to_t - from_t) * eased
            self.refresh_row(idx)
            try:
                anim["job"] = self.after(frame_ms, step)
            except Exception:
                anim["job"] = None

        self.refresh_row(idx)          # 先画成"切换前"的样子，动画从它开始
        try:
            anim["job"] = self.after(1, step)
        except Exception:
            anim["job"] = None

    def _on_click_evt(self, event):
        try:
            self.canvas.focus_set()      # 点过之后键盘才能直接翻页
        except Exception:
            pass
        idx = self._row_at(event.y)
        if idx < 0:
            return
        # 悬停图标优先：点图标只触发图标动作，不改选中
        act = self._action_at(idx, event.x, event.y)
        if act and self.on_action:
            self.on_action(idx, act)
            return
        # 行体：单击 = 选中/取消（和表格视图一致，点一下就能选中，带渐变）。
        # 双击走 Tk 自己的 <Double-Button-1>（见 _on_double_evt），这里只处理单击。
        self._sel = idx
        if self.on_click:
            self.on_click(idx, event)
        self._toggle_with_anim(idx)

    def _on_double_evt(self, event):
        """双击（Tk 原生 <Double-Button-1>）= 打开详情，选中状态保持原样。

        第二次按下 Tk 只发 <Double-Button-1>（不再发 <Button-1>），所以第一下单击
        造成的选中变化要在这里撤回来 —— 不走动画，双击不该看到高亮闪一下。
        """
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        idx = self._row_at(event.y)
        if idx < 0:
            return
        act = self._action_at(idx, event.x, event.y)
        if act:                      # 连点悬停图标不算双击
            return
        if self.on_check:
            self._toggle_instant(idx)
        if self.on_double_click:
            self.on_double_click(idx, event)

    # ---------------------------------------------------------------- 绘制
    def _icon_photo(self, row):
        """取（并缓存）这行的图标 PhotoImage；拿不到就用兜底图标。"""
        key = row.get("icon_key")
        if key and key in self._photos:
            return self._photos[key]
        img = None
        if self.icon_provider is not None and key:
            try:
                img = self.icon_provider(row)
            except Exception:
                img = None
        if img is None:
            img = self.fallback_icon
        if img is None:
            return None
        try:
            from PIL import ImageTk
            im = img.copy()
            im.thumbnail((self.ICON_PX, self.ICON_PX), img.LANCZOS if hasattr(img, "LANCZOS") else None)
            photo = ImageTk.PhotoImage(im)
        except Exception:
            return None
        if key:
            self._photos[key] = photo
        return photo

    def _photo_for(self, pil_img):
        """PIL 图 → PhotoImage（按对象身份缓存；rounded_image 自己也有缓存）。"""
        if pil_img is None:
            return None
        key = id(pil_img)
        hit = self._misc_photos.get(key)
        if hit is not None:
            return hit
        try:
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(pil_img)
        except Exception:
            return None
        self._misc_photos[key] = photo
        return photo

    def _elide(self, text, font, max_w):
        """截断到能放下的宽度。

        坑：以前是"每字符回退一次，每轮回拼一次整串再 measure"，那是 O(n²)。
        2560 字的描述实测**单次 994ms** —— 一屏 12 行就是 1 秒的卡顿，
        滚过一个超长描述的模组就会明显顿。改成二分查找（十几次 measure）
        + 结果缓存（同一行每次重建不用重算）。
        """
        text = str(text or "")
        if max_w <= 0 or not text:
            return ""
        key = (id(font), int(max_w), text)          # 带字体：标题/描述可能同串不同字号
        hit = self._elide_cache.get(key)
        if hit is not None:
            return hit
        try:
            if font.measure(text) <= max_w:
                out = text
            else:
                ell = "…"
                lo, hi = 0, len(text)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if font.measure(text[:mid] + ell) <= max_w:
                        lo = mid
                    else:
                        hi = mid - 1
                out = (text[:lo] + ell) if lo > 0 else ""
        except Exception:
            out = text
        if len(self._elide_cache) > 800:
            self._elide_cache.clear()
        self._elide_cache[key] = out
        return out

    def _render_rows(self, indices):
        """只重画指定行（悬停反馈用；行没变就不用整块重画）。"""
        for i in indices:
            if i is None or i < 0 or i >= len(self.rows):
                continue
            self._draw_row(i)

    def _sel_t(self, i):
        """这一行的"选中进度" 0~1。

        1 = 高亮铺满整行、竖杠完全立起，0 = 都没有。中间值就是那几帧：
        整块高亮从左往右扫、蓝竖杠同时从中间往上下延伸。
        """
        anim = self._sel_anim.get(i)
        if anim is not None:
            return anim["t"]
        return 1.0 if self.rows[i].get("checked") else 0.0

    def _blend(self, c0, c1, t):
        """两个 #rrggbb 按 t 混合（t=0 取 c0，t=1 取 c1）。"""
        try:
            a = [int(c0[i:i + 2], 16) for i in (1, 3, 5)]
            b = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
            return "#%02x%02x%02x" % tuple(
                max(0, min(255, int(a[k] + (b[k] - a[k]) * t))) for k in range(3))
        except Exception:
            return c0 if t < 0.5 else c1

    def _corner_photo(self, fill, r):
        """圆角用的"角圆"图（2r×2r 的实心圆）。尺寸和颜色固定，只会渲一次。"""
        return self._photo_for(rounded_image(r * 2, r * 2, r, fill))

    def _draw_rounded_rect(self, c, ctag, x, y, w, h, fill, radius=None):
        """用"两块矩形 + 四个角圆"拼一个圆角矩形（几何上等价于 rounded_image）。

        为什么不用 rounded_image：PIL 渲一张 880×58 的圆角图要 **约 10ms**（4 倍超采样 +
        LANCZOS 缩放）。选中动画每帧换一个宽度，等于每帧重渲一张图 —— 实测动画卡就是它
        拖的（帧耗时 9.8ms 中 9ms 是这里）。角圆尺寸恒定、只渲一次，之后每帧只是挪位置，
        实测 0.03ms，快 300 倍。
        """
        r = self.RADIUS if radius is None else radius
        r = max(0, min(int(r), int(w) // 2, int(h) // 2))
        if r <= 0:
            c.create_rectangle(x, y, x + w, y + h, fill=fill, outline="", tags=ctag)
            return
        c.create_rectangle(x, y + r, x + w, y + h - r, fill=fill, outline="", tags=ctag)
        c.create_rectangle(x + r, y, x + w - r, y + h, fill=fill, outline="", tags=ctag)
        corner = self._corner_photo(fill, r)
        if corner is not None:
            for cx, cy in ((x, y), (x + w - 2 * r, y),
                           (x, y + h - 2 * r), (x + w - 2 * r, y + h - 2 * r)):
                c.create_image(cx, cy, anchor="nw", image=corner, tags=ctag)

    def _draw_row(self, i):
        row = self.rows[i]
        y0 = (i - self._first) * self.ROW_H - self._offset
        c = self.canvas
        ctag = f"row{i}"
        c.delete(ctag)
        width = c.winfo_width()
        # 卡片本体：圆角矩形（留一点外边距，圆角才看得出来）
        mx = self.MARGIN_X
        card_w = max(40, width - mx * 2)
        card_h = self.ROW_H - self.GAP
        anim = self._sel_anim.get(i)
        t_sel = anim["t"] if anim is not None else (
            1.0 if row.get("checked") else 0.0)
        checked = anim is not None or bool(row.get("checked"))
        bg = self.theme["bg"]
        # 选中动画两部分：
        #   1) 整块高亮**从左往右扫出来**（左边缘固定不动）；
        #   2) 左侧那条蓝色竖杠**从上下（中间）延伸**出来，略快一点先立住。
        if checked:
            w_hl = max(2, int(card_w * min(1.0, t_sel)))
            x_hl = mx
            bg = self.theme.get("card_sel_bg", "#d4e6f8")
        elif i == self._sel:
            w_hl, x_hl = card_w, mx
            bg = self.theme.get("ttk_select_bg", bg)
        elif i == self._hover:
            w_hl, x_hl = card_w, mx
            bg = self.theme.get("hover_bg", "#e9eef5")
        else:
            w_hl, x_hl = card_w, mx
        # 和画布底色同色就别画了：正常行就是这样，只有选中/悬停才有底色
        # （项数才是画布重绘的成本大头）。
        if bg != self.theme["bg"]:
            self._draw_rounded_rect(c, ctag, x_hl, y0 + self.GAP // 2, w_hl, card_h, bg)
        if checked:
            # 竖杠跟着高亮左边缘，从中间往上下延伸（bar_t 略快，先立住再扫底色）
            bar_full = max(8, card_h - 16)
            bar_h = max(2, int(bar_full * min(1.0, t_sel * 1.25)))
            bar_c = self.theme.get("card_sel_bar", "#2f7fd1")
            bar = self._photo_for(rounded_image(4, bar_h, 2, bar_c))
            bar_y = y0 + self.GAP // 2 + (card_h - bar_h) / 2.0
            if bar is not None:
                c.create_image(x_hl + 2, bar_y, anchor="nw", image=bar, tags=ctag)
            else:
                c.create_rectangle(x_hl + 2, bar_y, x_hl + 6, bar_y + bar_h,
                                   fill=bar_c, outline="", tags=ctag)
        # 图标 / 文本从卡片左内边距开始（勾选框去掉后位置不变，选中与否不会跳）
        x = mx + self.PAD
        photo = self._icon_photo(row)
        if photo is not None:
            c.create_image(x, y0 + (self.ROW_H - photo.height()) // 2,
                           anchor="nw", image=photo, tags=ctag)
            x += photo.width() + self.PAD + 2
        # 文本
        right = c.winfo_width() - mx - self.PAD
        title = row.get("title") or ""
        sub = row.get("subtitle") or ""
        ver = row.get("version") or ""
        ver_w = self._font_sub.measure(ver) + 10 if ver else 0
        title_max = max(60, right - x - ver_w - 8)
        t_show = self._elide(title, self._font_title, title_max)
        # 选中的卡片标题跟着一起变蓝（颜色随选中进度插值）
        title_fg = (self._blend(self.theme["fg"], self.theme.get("card_sel_fg", "#0d3d63"),
                                t_sel) if checked else self.theme["fg"])
        c.create_text(x, y0 + 16, anchor="w", text=t_show,
                      fill=title_fg, font=self._font_title, tags=ctag)
        tx = x + self._font_title.measure(t_show) + 8
        if sub and tx < right - 60:
            c.create_text(tx, y0 + 17, anchor="w",
                          text=self._elide(sub, self._font_sub, right - tx - ver_w - 8),
                          fill=self.theme.get("muted_fg", self.theme["fg"]),
                          font=self._font_sub, tags=ctag)
        if ver:
            c.create_text(right - 6, y0 + 16, anchor="e", text=ver,
                          fill=self.theme.get("muted_fg", self.theme["fg"]),
                          font=self._font_sub, tags=ctag)
        desc = row.get("desc") or ""
        # 第二行：分类标签 chip（左）+ 描述（跟在后边）
        cx = x
        self._action_rects[i] = []
        # 悬停时右侧会出现三个操作图标，描述别铺到它们底下
        reserve = self.ACTION_W * 3 + 8 if i == self._hover else 8
        # 状态 chip 永远排第一：这个窗口本来就是用来查存在性的
        status = str(row.get("status") or "…")
        chips = [(status.replace("✅", "").replace("❌", "").strip() or "检测中",
                  {"✅ 存在": ("#2e7d32", "#ffffff"),
                   "❌ 缺失": ("#c62828", "#ffffff")}.get(status, ("#546e7a", "#ffffff")))]
        for tag in (row.get("tags") or []):
            chips.append((tag, TAG_COLORS.get(tag, ("#546e7a", "#ffffff"))))
        for text_c, (bg_c, fg_c) in chips[:4]:
            w_chip = self._font_sub.measure(text_c) + 14
            if cx + w_chip > right - 120:
                break
            chip = self._photo_for(rounded_image(w_chip, 17, 8, bg_c))
            if chip is not None:
                c.create_image(cx, y0 + 32, anchor="nw", image=chip, tags=ctag)
            else:
                c.create_rectangle(cx, y0 + 32, cx + w_chip, y0 + 49,
                                   fill=bg_c, outline="", tags=ctag)
            c.create_text(cx + w_chip / 2, y0 + 40, text=text_c, fill=fg_c,
                          font=self._font_sub, tags=ctag)
            cx += w_chip + 5
        if desc:
            gap = 6 if cx > x else 0
            c.create_text(cx + gap, y0 + 40, anchor="w",
                          text=self._elide(desc, self._font_desc,
                                           right - cx - gap - reserve),
                          fill=self.theme.get("muted_fg", self.theme["fg"]),
                          font=self._font_desc, tags=ctag)
        # 悬停时右侧出现操作图标：详情 / 打开所在位置 / 移除（从右往左画，视觉顺序是 ℹ 📂 🗑）
        if i == self._hover:
            blue = "#64b5f6" if is_dark_theme(self.theme) else "#1565c0"
            ax = right - 2
            for act, glyph, color in (
                    ("remove", "🗑", self.theme.get("fail_fg", "#c62828")),
                    ("reveal", "📂", blue),
                    ("info", "ℹ", self.theme.get("info_fg", "#0288d1"))):
                w_btn = self.ACTION_W
                x0, y0b = ax - w_btn, y0 + (self.ROW_H - self.ACTION_W) // 2
                hot = (self._hover_action == act)
                btn_bg = self.theme.get("button_bg", "#e0e0e0") if hot else self.theme["bg"]
                btn_img = self._photo_for(rounded_image(w_btn, self.ACTION_W, 6, btn_bg))
                if btn_img is not None:
                    c.create_image(x0, y0b, anchor="nw", image=btn_img, tags=ctag)
                else:
                    c.create_rectangle(x0, y0b, x0 + w_btn, y0b + self.ACTION_W,
                                       fill=btn_bg, outline="", tags=ctag)
                c.create_text(x0 + w_btn / 2, y0b + self.ACTION_W / 2 + 1, text=glyph,
                              fill=color, font=self._font_sub, tags=ctag)
                self._action_rects[i].append((x0, y0b, x0 + w_btn,
                                              y0b + self.ACTION_W, act))
                ax -= w_btn + 4

    def _render(self):
        c = self.canvas
        c.delete("all")
        self._action_rects.clear()
        # 整屏重画时把没跑完的选中动画丢掉：重画是按数据里的最终状态画的，
        # 留着旧动画的下一帧反而会把这一行画回中间态。
        for anim in list(self._sel_anim.values()):
            if anim.get("job") is not None:
                try:
                    self.after_cancel(anim["job"])
                except Exception:
                    pass
        self._sel_anim.clear()
        if not self.rows:
            c.create_text(c.winfo_width() // 2, 40, text="（没有内容）",
                          fill=self.theme.get("muted_fg", self.theme["fg"]),
                          font=self._font_sub)
            return
        per = self._rows_per_page()
        end = min(len(self.rows), self._first + per + 2)     # 多画两行：像素滚动时上下不留缝
        for i in range(self._first, end):
            self._draw_row(i)
        # 记下"这一屏是按哪个 _top 画的"，像素滚动时据此只做平移
        self._drawn_top = self._top
        self._drawn_first = self._first
        self._update_scrollbar()

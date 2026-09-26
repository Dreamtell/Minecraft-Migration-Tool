# ui/rounded_tabs.py
"""圆角药丸标签页（设置窗用）。

为什么不用 `ttk.Notebook`：它的标签是直角矩形，也没法给"选中块滑动 / 页面滑入"做动画。
这里用 Canvas 自己画：

- 每个标签一张**圆角**图（普通 / 悬停 / 选中三种配色），图和 `create_gradient_button`
  共用同一套 3 倍超采样渲染，所以边缘是抗锯齿的，不会糊；
- 选中那张单独一个 canvas item，切页时按缓动**滑**过去（170ms，OutCubic）；
- 页面用 `place` 叠在同一个容器里，切页时从下方 10px **滑到位**（150ms）；
- 主题色全从 theme 里取，深/浅色都跟着走。

对外就三个方法：`page(label)` 建一页、`select(i)` 切页、`labels()` 看标签文字。
"""
import time
import tkinter as tk
import tkinter.font as tkfont

from utils.helpers import _rounded_gradient, lighten_color

PAD_X = 22          # 标签左右内边距
GAP = 4             # 标签之间的间隙
BAR_H = 44          # 标签条高度
PILL_H = 34         # 药丸高度
PILL_Y = 5          # 药丸上边距
RADIUS = 12         # 圆角半径
SLIDE_MS = 170      # 选中块滑动时长
PAGE_MS = 150       # 页面滑入时长
FRAME_MS = 12       # 动画帧间隔（和主界面泵同量级）


class RoundedTabs(tk.Frame):
    def __init__(self, parent, theme, font=("微软雅黑", 9, "bold"), **kw):
        super().__init__(parent, bg=theme["bg"], **kw)
        self.theme = dict(theme)
        self._font = tkfont.Font(family=font[0], size=font[1], weight=font[2])
        self.bar = tk.Canvas(self, height=BAR_H, bg=self.theme["bg"],
                             highlightthickness=0, bd=0, takefocus=1)
        self.bar.pack(fill="x")
        self.body = tk.Frame(self, bg=self.theme["bg"])
        self.body.pack(fill="both", expand=True)

        self._tabs = []            # [{"label","page","x","w","pill","text","hover"}]
        self._photos = {}          # (宽, 种类) -> PhotoImage（留住引用）
        self._cur = -1
        self._anim = None
        self._page_anim = None
        self._sel_item = None
        self._use_images = self._make_photo(20, "normal") is not None

        self.bar.bind("<Button-1>", self._on_click)
        self.bar.bind("<Motion>", self._on_motion)
        self.bar.bind("<Leave>", self._on_leave)

    # ------------------------------------------------------------------ 对外
    @property
    def current(self):
        return self._cur

    def labels(self):
        return [t["label"] for t in self._tabs]

    def page(self, label):
        """建一页并返回它的容器（往里塞内容即可）。"""
        page = tk.Frame(self.body, bg=self.theme["bg"])
        self._tabs.append({"label": label, "page": page, "x": 0, "w": 0,
                           "pill": None, "text": None, "hover": False})
        self._layout_bar()
        if self._cur < 0:
            self.select(0, animate=False)
        return page

    def select(self, index, animate=True):
        if not (0 <= index < len(self._tabs)) or index == self._cur:
            return
        old = self._cur
        self._cur = index
        for i, t in enumerate(self._tabs):
            if i == index:
                t["page"].place(x=0, y=10 if animate else 0,
                                relwidth=1, relheight=1)
                if animate:
                    self._slide_page(t["page"])
            else:
                t["page"].place_forget()
        self._refresh_pills()

        tgt = self._tabs[index]
        self.bar.itemconfigure(self._sel_item, state="normal",
                               image=self._photo(tgt["w"], "sel"))
        if animate and old >= 0:
            x0 = self.bar.coords(self._sel_item)[0]
            self._slide_sel(x0, tgt["x"])
        else:
            self.bar.coords(self._sel_item, tgt["x"], PILL_Y)

    def set_theme(self, theme):
        """换主题：重出图、重画（设置窗一般整窗重建，这个留给以后复用）。"""
        self.theme = dict(theme)
        self.configure(bg=self.theme["bg"])
        self.bar.configure(bg=self.theme["bg"])
        self.body.configure(bg=self.theme["bg"])
        for t in self._tabs:
            t["page"].configure(bg=self.theme["bg"])
        self._photos.clear()
        cur = self._cur
        self._cur = -1
        self._layout_bar()
        self._cur = cur
        if cur >= 0:
            t = self._tabs[cur]
            self.bar.itemconfigure(self._sel_item, state="normal",
                                   image=self._photo(t["w"], "sel"))
            self.bar.coords(self._sel_item, t["x"], PILL_Y)
        self._refresh_pills()

    # ------------------------------------------------------------------ 绘制
    def _layout_bar(self):
        c = self.bar
        c.delete("all")
        x = 6
        for t in self._tabs:
            tw = self._font.measure(t["label"]) + PAD_X * 2
            t["x"], t["w"] = x, tw
            if self._use_images:
                t["pill"] = c.create_image(x, PILL_Y, anchor="nw")
            else:
                t["pill"] = c.create_rectangle(x, PILL_Y, x + tw, PILL_Y + PILL_H,
                                               width=0)
            t["text"] = c.create_text(x + tw // 2, PILL_Y + PILL_H // 2,
                                      text=t["label"], font=self._font,
                                      fill=self.theme["fg"])
            x += tw + GAP
        c.configure(width=x + 6)
        # 选中块：压在普通药丸上面、所有文字下面
        self._sel_item = c.create_image(0, PILL_Y, anchor="nw")
        c.tag_raise(self._sel_item)
        for t in self._tabs:
            c.tag_raise(t["text"])
        self._refresh_pills()
        # 重排（新加了一页 / 换了主题）之后，选中块要重新贴回当前页 ——
        # 否则它会停在 canvas 左上角（新 item 默认在 0,0）
        if 0 <= self._cur < len(self._tabs):
            t = self._tabs[self._cur]
            c.itemconfigure(self._sel_item, state="normal",
                            image=self._photo(t["w"], "sel"))
            c.coords(self._sel_item, t["x"], PILL_Y)

    def _refresh_pills(self):
        c = self.bar
        for i, t in enumerate(self._tabs):
            if i == self._cur:
                c.itemconfigure(t["pill"], state="hidden")   # 让位给滑动的那张
                c.itemconfigure(t["text"], fill=self._sel_fg())
            else:
                kind = "hover" if t["hover"] else "normal"
                if self._use_images:
                    c.itemconfigure(t["pill"], state="normal",
                                    image=self._photo(t["w"], kind))
                else:
                    c.itemconfigure(t["pill"], state="normal",
                                    fill=self._color(kind))
                c.itemconfigure(t["text"], fill=self.theme["fg"])

    def _color(self, kind):
        th = self.theme
        if kind == "sel":
            return th.get("card_sel_bar") or th.get("accent_bg", "#2f7fd1")
        base = th.get("button_bg", "#e0e0e0")
        return lighten_color(base) if kind == "hover" else base

    def _sel_fg(self):
        """选中标签的文字色：底色亮就用深色字，暗就用白字。"""
        bar = self._color("sel").lstrip("#")
        try:
            r, g, b = (int(bar[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return "#ffffff"
        return "#202020" if (0.299 * r + 0.587 * g + 0.114 * b) > 170 else "#ffffff"

    def _make_photo(self, w, kind):
        from PIL import ImageTk
        w = max(8, int(w))
        c = self._color(kind)
        imgs = _rounded_gradient(w, PILL_H, (c, c), (c, c), RADIUS, self)
        return ImageTk.PhotoImage(imgs["normal"])

    def _photo(self, w, kind):
        key = (max(8, int(w)), kind)
        hit = self._photos.get(key)
        if hit is None:
            hit = self._make_photo(w, kind)
            self._photos[key] = hit
        return hit

    # ------------------------------------------------------------------ 动画
    def _cancel(self):
        if self._anim is not None:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None

    def _slide_sel(self, x0, x1):
        self._cancel()
        t0 = time.perf_counter()
        step_ms = SLIDE_MS

        def step():
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / step_ms)
            e = 1 - (1 - k) ** 3                      # OutCubic：快起慢收
            self.bar.coords(self._sel_item, int(round(x0 + (x1 - x0) * e)), PILL_Y)
            self._anim = self.after(FRAME_MS, step) if k < 1.0 else None

        step()

    def _slide_page(self, page):
        self._cancel_page()
        t0 = time.perf_counter()

        def step():
            # 中途又切页了：旧页的动画必须停，否则 place_configure 会把已经
            # 收起来的旧页重新摆出来（两个页叠在一起）
            if self._cur < 0 or self._tabs[self._cur]["page"] is not page:
                self._page_anim = None
                return
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / PAGE_MS)
            e = 1 - (1 - k) ** 3
            try:
                page.place_configure(y=int(round((1 - e) * 10)))
            except Exception:
                return                                # 窗口关了就停
            self._page_anim = self.after(FRAME_MS, step) if k < 1.0 else None

        step()

    def _cancel_page(self):
        if self._page_anim is not None:
            try:
                self.after_cancel(self._page_anim)
            except Exception:
                pass
            self._page_anim = None

    # ------------------------------------------------------------------ 事件
    def _hit(self, x):
        for i, t in enumerate(self._tabs):
            if t["x"] <= x < t["x"] + t["w"]:
                return i
        return -1

    def _on_click(self, event):
        i = self._hit(event.x)
        if i >= 0:
            self.select(i)

    def _on_motion(self, event):
        i = self._hit(event.x)
        self.bar.configure(cursor="hand2" if i >= 0 else "")
        changed = False
        for j, t in enumerate(self._tabs):
            want = (j == i)
            if t["hover"] != want:
                t["hover"] = want
                changed = True
        if changed:
            self._refresh_pills()

    def _on_leave(self, _event):
        self.bar.configure(cursor="")
        changed = False
        for t in self._tabs:
            if t["hover"]:
                t["hover"] = False
                changed = True
        if changed:
            self._refresh_pills()

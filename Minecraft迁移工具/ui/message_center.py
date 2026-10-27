# ui/message_center.py
"""消息中心：吐司消失之后还能回看。

## 为什么要它

吐司是"纯告知、过一会儿自己消失"的通道 —— 这本来是对的，但它有个副作用：
**看漏了就没了**。执行日志只留下与迁移相关的内容，操作反馈（保存设置、复制路径、
目录不存在……）在日志里根本没有。所以需要第三个地方：把吐司同时记一份，
随时能翻。

## 设计取舍（用户定过的）

· **只记吐司**，不混入迁移过程日志 —— 保持"日志=过程、消息中心=操作反馈"的分工；
· **只有重要消息落盘**（warn / error）。info / ok 这类"我刚做成了什么"属于即时反馈，
  重启后没有回看价值，写盘只是噪音；
· 内存里始终保留最近 200 条（含 info），面板里看得到本次运行的完整提示序列；
· **形态是右侧抽屉**：从主界面右边框滑出、贴右下角，而不是一个居中的独立窗口。

## 落盘位置

与配置同目录的 .minecraft_migrate_messages.json（原子写，写坏不会丢掉历史）。
"""

import json
import os
import time
from pathlib import Path

import tkinter as tk

from utils import anim
from utils.config import CONFIG_FILE
from utils.dpi import px
from utils.i18n import trp

容量 = 200
_重要级别 = ("warn", "error")


def _存盘路径():
    try:
        return Path(CONFIG_FILE).parent / ".minecraft_migrate_messages.json"
    except Exception:
        return Path.home() / ".minecraft_migrate_messages.json"


class MessageStore:
    """消息存档：内存里全留，磁盘上只留重要的。"""

    def __init__(self, 上限=容量, 路径=None):
        self.上限 = int(上限)
        self.路径 = Path(路径) if 路径 else _存盘路径()
        self.items = []          # [{t: 时间戳, level: 级别, text: 文本, 旧: 是否上次运行}]
        self.未读 = 0
        self._读盘()

    def _读盘(self):
        try:
            with open(self.路径, "r", encoding="utf-8") as fp:
                数据 = json.load(fp)
            项 = 数据.get("items") if isinstance(数据, dict) else None
            if isinstance(项, list):
                for x in 项[-self.上限:]:
                    if not isinstance(x, dict):
                        continue
                    文 = str(x.get("text") or "")
                    if not 文:
                        continue
                    self.items.append({
                        "t": float(x.get("t") or 0) or time.time(),
                        "level": str(x.get("level") or "info"),
                        "text": 文,
                        "旧": True,
                    })
        except Exception:
            pass

    def _写盘(self):
        """只写重要的；原子写（写一半崩掉不会留下坏 JSON）。"""
        try:
            项 = [x for x in self.items if x.get("level") in _重要级别][-self.上限:]
            self.路径.parent.mkdir(parents=True, exist_ok=True)
            临时 = self.路径.with_name(self.路径.name + ".mctmp")
            with open(临时, "w", encoding="utf-8") as fp:
                json.dump({"v": 1, "items": [
                    {"t": x["t"], "level": x["level"], "text": x["text"]} for x in 项
                ]}, fp, ensure_ascii=False, indent=1)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(临时, self.路径)
        except Exception:
            pass

    def add(self, text, level="info"):
        文 = str(text or "").strip()
        if not 文:
            return
        self.items.append({"t": time.time(), "level": level or "info",
                           "text": 文, "旧": False})
        if len(self.items) > self.上限:
            del self.items[:-self.上限]
        self.未读 += 1
        if (level or "info") in _重要级别:
            self._写盘()

    def 全部(self):
        return list(self.items)

    def clear(self):
        self.items = []
        self.未读 = 0
        try:
            if self.路径.exists():
                self.路径.unlink()
        except Exception:
            pass

    def 标记已读(self):
        self.未读 = 0


# 级别 → 中文名（列表里写 ok/warn/error 是开发者语言，用户看的是中文）
_级别名 = {"ok": "成功", "info": "提示", "warn": "警告", "error": "错误"}

# 级别 → (图标, 颜色)
_外观 = {
    "ok":    ("✓", "#4caf50"),
    "info":  ("i", "#4c9aff"),
    "warn":  ("!", "#ffa000"),
    "error": ("×", "#e53935"),
}

_宽 = 372          # 抽屉宽度（设计像素）
_高上限 = 560      # 抽屉最高
_滑入ms = 260      # 170ms 太快，位移 372px 时一眼就是"啪"地出现
_圆角 = 16


_键色 = "#010203"        # 透明键色（和吐司同一套路）


def _圆角化窗口(win, 宽, 高, 半径):
    """用 SetWindowRgn 把无边框窗口裁成圆角。

    ⚠ 为什么不用 DWM 的 DWMWA_WINDOW_CORNER_PREFERENCE：那个只对**有标题栏**的
    标准窗口生效，overrideredirect 的无边框窗口会被忽略（实测就是四角依旧发方）。
    也不用 -transparentcolor：那要求窗口是 layered，而 layered 窗口会被 DWM 跳过
    弹出/关闭动画，还和子控件的绘制抢合层。
    SetWindowRgn 是**物理裁剪**：窗口本身就是圆角的，桌面从缺角露出来 ——
    ui/splash.py 一直是这么干的。代价是边缘没有抗锯齿，半径十几像素时看不出来。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        try:
            win.update_idletasks()
        except Exception:
            pass
        hwnd = user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id()
        r = max(2, int(半径) * 2)
        rgn = gdi32.CreateRoundRectRgn(0, 0, int(宽) + 1, int(高) + 1, r, r)
        if rgn:
            user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
            return True
    except Exception:
        pass
    return False


def _面板图(宽, 高, 半径, 填充, 描边):
    """圆角面板图（PIL，抗锯齿）：底色圆角矩形 + 1px 描边环。"""
    from PIL import Image
    from utils.helpers import _圆角矩形
    底 = _圆角矩形((宽, 高), 半径, 描边)
    内 = _圆角矩形((max(1, 宽 - 2), max(1, 高 - 2)), max(1, 半径 - 1), 填充)
    return Image.alpha_composite(底, 内)


class _抽屉:
    """右侧抽屉：无边框、贴主界面右边框、从右滑入。"""

    def __init__(self, 父窗, store, theme):
        self.父窗 = 父窗
        self.store = store
        self.theme = dict(theme or {})
        self.深 = str(self.theme.get("bg", "#ffffff")).lower() in (
            "#2e2e2e", "#1e1e1e", "#252525", "#202020", "#2b2b2b")
        self.底 = self.theme.get("bg", "#f0f0f0")
        self.字 = self.theme.get("fg", "#222222")
        self.弱 = self.theme.get("muted_fg", "#888888")
        self.文本底 = self.theme.get("text_bg", "#ffffff")
        self._job = None
        self._configure_bind = None
        self.win = None

    # ---------------------------------------------------------------- 尺寸/位置
    def _目标几何(self):
        """(宽, 高, 目标x, 目标y)：贴主界面右边框、靠下。"""
        try:
            mw = self.父窗.winfo_width()
            mh = self.父窗.winfo_height()
            rx = self.父窗.winfo_rootx()
            ry = self.父窗.winfo_rooty()
        except Exception:
            return int(px(_宽)), int(px(_高上限)), 0, 0
        宽 = int(px(_宽))
        高 = int(min(px(_高上限), max(px(240), mh - px(90))))
        # 右边框内侧；底部留一点边距，看起来是"从右边缘滑出"的抽屉
        x = rx + mw - 宽
        y = ry + mh - 高 - px(6)
        return 宽, 高, int(x), int(y)

    def _屏幕外x(self):
        try:
            return self.父窗.winfo_rootx() + self.父窗.winfo_width() + px(4)
        except Exception:
            return 4000

    # ---------------------------------------------------------------- 构建
    def build(self):
        宽, 高, 目标x, 目标y = self._目标几何()
        装 = self.theme.get("card_bg") or ("#262626" if self.深 else "#ffffff")
        边 = self.theme.get("border") or ("#3c3c3c" if self.深 else "#d8d8d8")
        半径 = int(px(_圆角))
        内缩 = 半径 + 2           # 内容必须整体缩进圆角里，否则方角会盖住圆弧

        win = tk.Toplevel(self.父窗)
        win.overrideredirect(True)
        # 圆角靠"键色 = 透明"实现：窗口四角是键色 → 真透明，露出下面的界面。
        # （Tk 控件没有真透明，圆角只能这么做；和吐司是同一套思路。）
        try:
            win.attributes("-transparentcolor", _键色)
            win.configure(bg=_键色)
        except Exception:
            win.configure(bg=装)
        win.withdraw()
        self.win = win
        # ⚠ 窗口**固定在最终位置**，动画只挪里面的内容容器：
        # 之前是让整个窗口从屏幕外滑进来，于是动画期间它浮在桌面之上（用户：
        # "根本不是从边框出来"）。窗口不动、内容在窗口内右→左滑入，超出部分由 Tk
        # 自动裁掉，看起来才是"从右边框里被拉出来"。
        win.geometry("%dx%d+%d+%d" % (宽, 高, 目标x, 目标y))
        self._目标 = (宽, 高, 目标x, 目标y)

        # 内容容器：整体右→左平移，实现"从边框滑出"
        容器 = tk.Frame(win, bg=_键色, bd=0, highlightthickness=0)
        # ⚠ 初始 x 必须是**窗口宽度**（整块在窗口外），不能是 0：
        # deiconify() 会先把窗口渲一帧，容器若在 0 就先完整露一下，
        # 下一帧才被挪到窗口外开始滑入 —— 这就是用户看到的"闪一下"。
        容器.place(x=宽, y=0, width=宽, height=高)
        self._容器 = 容器

        画 = tk.Canvas(容器, bg=_键色, highlightthickness=0, bd=0)
        画.place(x=0, y=0, width=宽, height=高)
        try:
            from PIL import ImageTk
            self._photo = ImageTk.PhotoImage(_面板图(宽, 高, 半径, 装, 边))
            画.create_image(0, 0, anchor="nw", image=self._photo)
        except Exception:
            self._photo = None
            画.configure(bg=装)

        # 内容层：place 内缩，四角的圆弧由下面的面板图显示
        内 = tk.Frame(容器, bg=装)
        内.place(x=内缩, y=内缩, width=宽 - 2 * 内缩, height=高 - 2 * 内缩)

        # 顶栏：标题 + 条数徽标 + 关闭（原来还挂一行灰色副标题，把标题区压成三层，
        # 视觉上很挤；提示文字挪到底部做脚注）
        顶 = tk.Frame(内, bg=装)
        顶.pack(fill="x", padx=px(14), pady=(px(12), px(8)))
        关 = tk.Label(顶, text="✕", bg=装, fg=self.弱, cursor="hand2",
                      font=("微软雅黑", 11))
        关.pack(side="right")
        关.bind("<Button-1>", lambda e: self.close())
        关.bind("<Enter>", lambda e: 关.config(fg=self.字))
        关.bind("<Leave>", lambda e: 关.config(fg=self.弱))
        徽标 = tk.Label(顶, text="", bg=装, fg=self.弱, font=("微软雅黑", 8))
        徽标.pack(side="right", padx=(0, px(10)))
        tk.Label(顶, text=trp("消息中心"), bg=装, fg=self.字,
                 font=("微软雅黑", 11, "bold")).pack(side="left")

        底栏 = tk.Frame(内, bg=装)
        底栏.pack(side="bottom", fill="x", padx=px(12), pady=(px(6), px(10)))

        # 列表去掉描边（现代面板靠留白分层，不靠框线），内边距加大
        文 = tk.Text(内, bg=装, fg=self.字, bd=0, highlightthickness=0,
                     wrap="word", font=("微软雅黑", 9),
                     padx=px(12), pady=px(10), spacing1=px(2), spacing3=px(4))
        # 滚动条**自己画**：tk.Scrollbar 用的是系统原生渲染，bg/troughcolor 被忽略，
        # 在这块深色面板上就是一条 (240,240,240) 的浅灰柱子（用户截图里那条白杠）。
        滚 = tk.Canvas(内, width=px(6), bg=装, highlightthickness=0, bd=0)
        滚.pack(side="right", fill="y", padx=(px(2), px(2)), pady=(px(6), px(6)))

        def _同步(第一, 最后):
            try:
                a, b = float(第一), float(最后)
            except Exception:
                return
            高 = 滚.winfo_height()
            if 高 <= 1:
                return
            上 = int(a * 高)
            下 = int(b * 高)
            if 下 - 上 < px(18):
                下 = 上 + int(px(18))
            try:
                滚.delete("all")
                if b - a < 0.999:          # 全部可见时不画滑块
                    滚.create_rectangle(0, 上, px(6), min(下, 高),
                                        fill=self.theme.get("border") or "#555555",
                                        outline="")
            except Exception:
                pass

        文.configure(yscrollcommand=_同步)

        def _拖(e):
            try:
                文.yview_moveto(max(0.0, min(1.0, e.y / max(1, 滚.winfo_height()))))
            except Exception:
                pass

        滚.bind("<Button-1>", _拖)
        滚.bind("<B1-Motion>", _拖)
        滚.bind("<Configure>", lambda e: 文.yview())
        文.pack(side="left", fill="both", expand=True, padx=(px(12), 0),
                  pady=(px(4), px(4)))
        self.文 = 文

        for 级, (图, 色) in _外观.items():
            文.tag_configure("级_" + 级, foreground=色)
        文.tag_configure("时", foreground=self.弱)
        文.tag_configure("元", foreground=self.弱, font=("微软雅黑", 8))
        # 每条消息做成一条**行带**：自带底色 + 左右内缩 + 上下留白。
        # 原来所有行同底色连成一片，9 条挤在一起像一整块（用户截图）。
        # 间隙露出面板底色，就自然分成了"条"。
        行底 = self.theme.get("row_bg") or ("#2f2f2f" if self.深 else "#f5f5f5")
        文.tag_configure("条", background=行底, lmargin1=px(10), lmargin2=px(10),
                         rmargin=px(10), spacing1=px(6), spacing3=px(0))
        文.tag_configure("条元", background=行底, foreground=self.弱,
                         font=("微软雅黑", 8), lmargin1=px(10), lmargin2=px(14),
                         rmargin=px(10), spacing1=px(0), spacing3=px(2))
        # ⚠ 分隔不能靠 spacing1/3：Tk 会把那段留白也涂上标签底色，整块还是连在一起。
        # 只能插入一条**面板底色的小行**当真正的空隙。
        文.tag_configure("隔", background=装, font=("微软雅黑", 4),
                         spacing1=px(1), spacing3=px(1))
        文.tag_configure("空图", foreground=self.弱, font=("微软雅黑", 20),
                         justify="center", spacing1=px(8))
        文.tag_configure("空字", foreground=self.字, font=("微软雅黑", 10),
                         justify="center", spacing1=px(6))
        文.tag_configure("空提", foreground=self.弱, font=("微软雅黑", 8),
                         justify="center", spacing1=px(4))

        def _重画():
            try:
                文.configure(state="normal")
                文.delete("1.0", "end")
            except Exception:
                return
            项 = self.store.全部()
            if not 项:
                # 空状态居中（原来是一句灰字贴在左上角，像出错了）
                文.insert("end", "\n\n\n")
                文.insert("end", "🔔\n", "空图")
                文.insert("end", trp("还没有消息") + "\n", "空字")
                文.insert("end", trp("操作提示会记在这里，吐司消失后也能回看"), "空提")
            # 连续重复的折叠成一条（末尾显示 ×N）：反复开关同一个设置时，
            # 面板会被十条一模一样的消息糊满，真正的内容反而看不见。
            组 = []
            for x in 项:
                if (组 and 组[-1][0].get("text") == x.get("text")
                        and 组[-1][0].get("level") == x.get("level")):
                    组[-1].append(x)
                else:
                    组.append([x])
            for 序号, 一组 in enumerate(组):
                if 序号:
                    文.insert("end", "\n", "隔")   # 行与行之间的空隙
                x = 一组[-1]                     # 用最后一条的时间
                级 = x.get("level") if x.get("level") in _外观 else "info"
                图, _色 = _外观[级]
                try:
                    时 = time.strftime("%H:%M", time.localtime(float(x.get("t") or 0)))
                except Exception:
                    时 = ""
                # 两行一条：正文一行，下面一行灰色小字（级别 · 时间 · 次数）。
                # 挤成一行的"时间 图标 正文"读起来像日志文件，不像通知。
                文.insert("end", " " + 图 + " ", ("条", "级_" + 级))
                文.insert("end", str(x.get("text") or ""), "条")
                文.insert("end", "\n", "条")
                元 = 时 + "  " + _级别名.get(级, "")
                if len(一组) > 1:
                    元 += "  ×%d" % len(一组)
                文.insert("end", 元, "条元")
                文.insert("end", "\n", ("条元", "gap"))
            文.configure(state="disabled")
            try:
                文.see("end")
            except Exception:
                pass

        self._重画 = _重画

        def _清空():
            self.store.clear()
            self._重画()

        # 底栏：左边一句脚注（原来是标题下面那行灰字），右边两个按钮 ——
        # 「清空」是次要操作，做成幽灵按钮（无底色），「关闭」保留实心。
        tk.Label(底栏, text=trp("灰字为上次运行留下"), bg=装, fg=self.弱,
                 font=("微软雅黑", 8)).pack(side="left")

        def _幽灵(文字, 命令):
            钮 = tk.Label(底栏, text=文字, bg=装, fg=self.弱, cursor="hand2",
                          font=("微软雅黑", 9), padx=px(10), pady=px(3))
            钮.pack(side="right", padx=(px(8), 0))
            钮.bind("<Button-1>", lambda e: 命令())
            钮.bind("<Enter>", lambda e: 钮.config(fg=self.字))
            钮.bind("<Leave>", lambda e: 钮.config(fg=self.弱))
            return 钮

        try:
            from utils.helpers import create_gradient_button
            create_gradient_button(底栏, trp("关闭"), self.close,
                                   colors=("#42a5f5", "#1565c0"), width=px(58),
                                   height=px(24), font=("微软雅黑", 9)).pack(side="right")
            _幽灵(trp("清空"), _清空)
        except Exception:
            钮 = tk.Label(底栏, text=trp("关闭"), bg=self.theme.get("button_bg", "#e0e0e0"),
                          fg=self.theme.get("button_fg", "#222222"), cursor="hand2",
                          font=("微软雅黑", 9), padx=px(12), pady=px(3))
            钮.pack(side="right")
            钮.bind("<Button-1>", lambda e: self.close())
            _幽灵(trp("清空"), _清空)

        def _刷新徽标():
            try:
                项 = self.store.全部()
                徽标.config(text=(trp("{0} 条").format(len(项)) if 项 else ""))
            except Exception:
                pass

        旧重画 = _重画

        def _重画2():
            旧重画()
            _刷新徽标()

        self._重画 = _重画2
        _重画2()
        self.store.标记已读()

        # 真正的圆角：把窗口区域裁成圆角矩形（无边框窗口只能这么做）。
        # 隐藏状态下先裁一次，让**首次显示**就是圆的。
        try:
            win.update_idletasks()
            _圆角化窗口(win, 宽, 高, 半径)
        except Exception:
            pass

        win.bind("<Escape>", lambda e: self.close())
        try:
            self._configure_bind = self.父窗.bind(
                "<Configure>", self._on_parent_configure, add="+")
        except Exception:
            pass
        return win

    def _on_parent_configure(self, _e=None):
        """主窗口移动/缩放时重新贴住右边框（尺寸变了还要重裁圆角）。"""
        if self.win is None:
            return
        try:
            if not self.win.winfo_exists():
                return
            宽, 高, x, y = self._目标几何()
            self._目标 = (宽, 高, x, y)
            self.win.geometry("%dx%d+%d+%d" % (宽, 高, x, y))
            _圆角化窗口(self.win, 宽, 高, int(px(_圆角)))
        except Exception:
            pass

    def slide_in(self):
        宽, 高 = self._目标[0], self._目标[1]
        try:
            self.win.deiconify()
            self.win.lift()
        except Exception:
            pass
        # ⚠ 显示之后**立刻**再裁一次（不是延迟 80ms）：withdraw 状态的 Toplevel 被
        # deiconify 时 HWND 可能被重建，区域会跟着丢；延迟补裁的话，动画前几帧
        # 就是方的（用户："动画时没有圆角化"）。这里同步补，第一帧就是圆的。
        _圆角化窗口(self.win, 宽, 高, int(px(_圆角)))
        # 从"整块在窗口右侧外面"的位置滑到 0：窗口本身不动
        self._animate(宽, 0)

    def slide_out_and_close(self):
        宽 = self._目标[0]
        self._animate(0, 宽, 结束后=self._destroy)

    def _animate(self, 起, 终, 结束后=None):
        宽 = self._目标[0]
        t0 = time.perf_counter()
        try:
            anim.hold()
        except Exception:
            pass
        向外 = 终 > 起

        def 帧():
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / _滑入ms)
            # 开：ease-out-quint（一上来就动，尾部缓）；关：ease-in（越走越快被抽走）
            缓 = (k * k) if 向外 else (1 - (1 - k) ** 5)
            x = int(起 + (终 - 起) * 缓)
            try:
                self._容器.place_configure(x=x)
            except Exception:
                pass
            if k < 1.0:
                try:
                    self._job = self.win.after(max(4, anim.frame_ms(self.win)), 帧)
                except Exception:
                    self._job = None
            else:
                self._job = None
                try:
                    anim.release()
                except Exception:
                    pass
                if 结束后 is not None:
                    结束后()

        帧()


    def _destroy(self):
        try:
            if self._configure_bind is not None:
                self.父窗.unbind("<Configure>", self._configure_bind)
        except Exception:
            pass
        self._configure_bind = None
        try:
            self.win.destroy()
        except Exception:
            pass
        self.win = None
        if getattr(open_message_center, "_抽屉", None) is self:
            open_message_center._抽屉 = None

    def close(self):
        try:
            if self._job is not None:
                self.win.after_cancel(self._job)
        except Exception:
            pass
        self._job = None
        self.slide_out_and_close()


def open_message_center(父窗, store, theme):
    """打开/收起消息中心（右侧抽屉）。返回抽屉对象或 None。"""
    旧 = getattr(open_message_center, "_抽屉", None)
    if 旧 is not None and 旧.win is not None:
        try:
            if 旧.win.winfo_exists():
                旧.close()
                return None
        except Exception:
            pass
    抽 = _抽屉(父窗, store, theme)
    try:
        抽.build()
        抽.slide_in()
    except Exception:
        return None
    open_message_center._抽屉 = 抽
    return 抽

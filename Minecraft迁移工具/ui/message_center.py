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
import sys
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
                        "未读": False,      # 上次运行的记录，打开面板不该算新消息
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

    def add(self, text, level="info", key=None):
        文 = str(text or "").strip()
        if not 文:
            return
        # 同一个 key = 同一条消息的"状态变化"（开/关这类开关反复拨）。
        # 直接并进上一条：面板只留一行最新状态 + ×N，而不是被十条糊满。
        上 = self.items[-1] if self.items else None
        if key and 上 is not None and 上.get("key") == key:
            # 合并的这一行如果本来就是未读，就不该再 +1 —— 未读数是"面板里有几行
            # 是新消息"，不是"add 被调用过几次"（合并三次仍是一行）。
            self.items[-1] = {"t": time.time(), "level": level or "info",
                              "text": 文, "旧": False, "key": key,
                              "n": int(上.get("n") or 1) + 1, "未读": True}
            if not 上.get("未读"):
                self.未读 += 1
            if (level or "info") in _重要级别:
                self._写盘()
            return
        self.items.append({"t": time.time(), "level": level or "info",
                           "text": 文, "旧": False, "key": key, "n": 1,
                           "未读": True})
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
        for x in self.items:
            if x.get("未读"):
                x["未读"] = False
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

_宽 = 372          # 抽屉卡片宽度（设计像素）
_边距 = 10         # 卡片离主窗右/上/下的留白（设计像素），留白透明
_高上限 = 560      # 抽屉最高
_滑入ms = 260      # 170ms 太快，位移 372px 时一眼就是"啪"地出现
_圆角 = 16


_键色 = "#010203"        # 透明键色（和吐司同一套路；Win10 那条路线才用）

# ---------------------------------------------------------------- 圆角
# Win11 的 DWM 自己会给窗口画**抗锯齿**圆角（DWMWA_WINDOW_CORNER_PREFERENCE）。
# 以前这里是纯 SetWindowRgn 手工裁圆角：区域是 1-bit 的掩码，圆角只能一格一格
# 切出来 —— 用户看到的就是"消息栏圆角化有锯齿"。能用 DWM 就把形状交给它，
# 圆角是真的平滑；Win10 及更早没有这个效果，退回原来的裁区域路线。
_DWM角档 = 2            # 2 = DWMWCP_ROUND；3 = ROUNDSMALL（更小的圆角）
_圆角px_DWM = 8         # DWM 圆角大约 8px：面板图按它画，区域裁也按它，才不会一大一小


def _探测DWM圆角():
    """Win11（build ≥ 22000）才有窗口圆角；顺便试一下接口在不在。"""
    try:
        v = sys.getwindowsversion()
        return int(getattr(v, "build", 0) or 0) >= 22000
    except Exception:
        return False


_WIN11 = _探测DWM圆角()


def _设DWM圆角(win, 档=None):
    """让 DWM 给这个窗口画抗锯齿圆角；成功返回 True。

    无边框窗也能吃这条（实测 overrideredirect + -alpha 的分层窗照样出圆角）。
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        hwnd = user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id()
        值 = ctypes.c_int(int(_DWM角档 if 档 is None else 档))
        r = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), 33, ctypes.byref(值), ctypes.sizeof(值))
        return r == 0
    except Exception:
        return False


def _撤区域(win):
    """撤掉 SetWindowRgn，把形状还给 DWM。

    区域留着的话，DWM 画的圆角会被它按 1-bit 形状切掉 —— 又回到楼梯了。
    展开动画期间仍然用区域（每帧一个完整圆角矩形），展开完就撤。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        hwnd = user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id()
        user32.SetWindowRgn(ctypes.c_void_p(hwnd), None, True)
        return True
    except Exception:
        return False


def _裁区域(win, 左, 卡宽, 高, 半径):
    """把窗口可见区域裁成 [左, 卡宽] x [0, 高] 的圆角矩形。

    抽屉的展开动画**只动这条区域**：容器和内容始终钉在窗口左上角 0 处，区域从左
    往右扫过去。这样每一帧可见的都是完整圆角矩形 —— 包括展开时那条前缘边。
    （之前"内容在窗口里平移"的做法，前缘那条边在窗口内部，物理上只能是直角，
    用户看到的就是"动画过程没有圆角化"。）

    故意不调用 update_idletasks：每帧都会调用，必须够便宜。
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
        hwnd = user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id()
        左 = int(左)
        卡宽 = int(卡宽)
        高 = int(高)
        if 左 >= 卡宽:                 # 收拢：空区域 → 窗口整块不显示
            rgn = gdi32.CreateRoundRectRgn(0, 0, 0, 0, 0, 0)
        else:
            r = max(2, int(半径) * 2)  # CreateRoundRectRgn 的角宽是直径
            rgn = gdi32.CreateRoundRectRgn(左, 0, 卡宽, 高 + 1, r, r)
        if rgn:
            user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
            return True
    except Exception:
        pass
    return False


def _面板图(宽, 高, 半径, 填充, 描边):
    """圆角面板图（PIL，抗锯齿）：底色圆角矩形 + 1px 描边环。

    ⚠ 以前是「先铺一层描边色的圆角矩形，再把填充色的内矩形 alpha_composite 上去」，
    但内矩形贴在 (0, 0) 而不是 (1, 1)，于是描边只剩右边和下边各 2px，上边和左边
    一点都没有 —— 四条边不对称。描边统一交给 helpers._圆角描边。
    """
    from PIL import Image
    from utils.helpers import _圆角矩形, _圆角描边
    底 = _圆角矩形((宽, 高), 半径, 填充)
    环 = _圆角描边((宽, 高), 半径, 描边, 1)
    return Image.alpha_composite(底, 环)


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
        self._关闭中 = False      # 关闭动画进行中（close 幂等 + 防止滑入动画回抢）
        self._unmap_bind = None
        self._configure_bind = None
        self._外点 = None          # 「点外部收回」的全局点击绑定
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
            return int(px(_宽)) + int(px(_边距)), int(px(_高上限)), 0, 0
        边距 = int(px(_边距))
        卡宽 = int(min(px(_宽), max(px(180), mw - 2 * 边距)))
        # Win11：形状交给 DWM，窗口就**等于卡片**，不再用键色留白；
        # 「悬浮」的观感改由窗口外的真实空隙来做（x 里那 10px）。
        宽 = 卡宽 + (0 if _WIN11 else 边距)
        高 = int(min(px(_高上限), max(px(160), mh - 2 * 边距)))
        # 悬浮卡片：右侧和底部各留一个边距，不贴边。底部对齐（不是顶部）：
        # 铃铛在底部状态栏，卡片从它上面长出来才连得上；窗口很高时上方留白。
        x = rx + mw - 卡宽 - 边距
        y = ry + mh - 高 - 边距
        return 宽, 高, int(x), int(y)

    def _可滑距离(self, 目标x, 卡宽):
        """窗口还能往右挪多少而不跑出主界面（抽屉离右边框本来就有 _边距 的间隙）。"""
        try:
            父右 = int(self.父窗.winfo_rootx()) + int(self.父窗.winfo_width())
            return max(0, min(int(卡宽), 父右 - (int(目标x) + int(卡宽))))
        except Exception:
            return 0

    def _卡宽(self):
        """卡片宽度 = 窗口宽度 - 右侧留白。"""
        try:
            return max(2, int(self._目标[0]) - (0 if _WIN11 else int(px(_边距))))
        except Exception:
            return int(px(_宽))

    def _半径(self):
        """窗口圆角半径：Win11 跟 DWM 对齐（约 8px），否则用设计值 16 交给区域裁。"""
        return int(px(_圆角px_DWM)) if _WIN11 else int(px(_圆角))

    def build(self):
        宽, 高, 目标x, 目标y = self._目标几何()
        装 = self.theme.get("card_bg") or ("#262626" if self.深 else "#ffffff")
        边 = self.theme.get("border") or ("#3c3c3c" if self.深 else "#d8d8d8")
        半径 = self._半径()
        内缩 = 半径 + 2           # 内容必须整体缩进圆角里，否则方角会盖住圆弧

        win = tk.Toplevel(self.父窗)
        # ⚠ withdraw 必须是**第一个**动作：下面 overrideredirect / attributes 都会
        # 触发 Tk 的 idle 更新，窗口会被 map 出来一瞬间 —— 那一刻它就是默认尺寸、
        # 默认底色的一个方块，正是用户看到的"打开时闪一下"。
        win.withdraw()
        # 整体 alpha=0：从"窗口被 map"到"面板画好、圆角贴好"之间，任何一帧漏出来
        # 都只会是键色（近黑）的一块 —— 用户看到的就是"闪了个黑方块"。alpha=0 期间
        # 窗口物理上不可见，所以这一整段时序竞争都不再可见；起滑前再设回 1。
        try:
            win.attributes("-alpha", 0.0)
        except Exception:
            pass
        win.overrideredirect(True)
        if _WIN11:
            # Win11：窗口就是卡片，形状交给 DWM —— 它画的圆角是真抗锯齿的。
            # （以前四角靠键色透明 + SetWindowRgn 裁，那条边是 1-bit 的，必然是楼梯。）
            win.configure(bg=装)
            self._dwm = _设DWM圆角(win)
        else:
            # Win10：没有 DWM 圆角，退回"键色 = 透明"那一套（圆角仍由区域裁）。
            try:
                win.attributes("-transparentcolor", _键色)
                win.configure(bg=_键色)
            except Exception:
                win.configure(bg=装)
        win.withdraw()
        self.win = win
        # 窗口**固定在最终位置**，动画既不挪窗口也不挪内容：只改窗口区域
        # （_裁区域），区域从左往右扫过去。之前是让内容在窗口里右→左平移，
        # 于是展开途中前缘那条边落在窗口内部，只能是直角 —— 用户："动画过程
        # 没有圆角化"。区域驱动之后，每一帧可见形状都是完整的圆角矩形。
        win.geometry("%dx%d+%d+%d" % (宽, 高, 目标x, 目标y))
        win.withdraw()          # 属性设完再收一次，保险
        self._目标 = (宽, 高, 目标x, 目标y)
        卡宽 = self._卡宽()
        self._左 = 宽            # 收拢初值：区域整体在卡片右侧之外 → 不可见

        # 内容容器钉死在 0：内容静止，动画期间不重排也不重画
        窗底 = 装 if _WIN11 else _键色
        容器 = tk.Frame(win, bg=窗底, bd=0, highlightthickness=0)
        容器.place(x=0, y=0, width=卡宽, height=高)
        self._容器 = 容器

        画 = tk.Canvas(容器, bg=窗底, highlightthickness=0, bd=0)
        画.place(x=0, y=0, width=卡宽, height=高)
        try:
            from PIL import ImageTk
            self._photo = ImageTk.PhotoImage(_面板图(卡宽, 高, 半径, 装, 边))
            画.create_image(0, 0, anchor="nw", image=self._photo)
        except Exception:
            self._photo = None
            画.configure(bg=装)

        # 内容层：place 内缩，四角的圆弧由下面的面板图显示
        内 = tk.Frame(容器, bg=装)
        内.place(x=内缩, y=内缩, width=卡宽 - 2 * 内缩, height=高 - 2 * 内缩)
        self._内 = 内
        self._内缩 = 内缩
        # 展开/收回时的位移分两半：
        #   ① 整个窗口往右挪 —— 这才是真"滑进来"，但只许在**主界面内部**滑，
        #      不许跑到窗口外面（用户报过"收回动画都跑出窗口边框了"）。抽屉本身
        #      离右边框有 _边距 的间隙，所以窗口能滑的距离就是那点间隙；
        #   ② 挪不动的那部分，让内容横着飘补上，保证位移量足够看得出来。
        self._漂移 = max(0, min(int(px(26)), 内缩 + 20))
        self._滑 = self._可滑距离(目标x, 卡宽)
        self._漂 = max(0, self._漂移 - self._滑)

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

        # ⚠ 底栏必须**先** pack：列表画布是 fill="both", expand=True，先 pack 它就会
        # 把剩余空间全吃掉，后 pack 的底栏高度变成 0（按钮直接看不见）。
        # 左右留白跟卡片对齐，否则"关闭"按钮会贴到圆角上，看起来像被裁掉。
        底栏 = tk.Frame(内, bg=装)
        底栏.pack(side="bottom", fill="x", padx=(内缩 + px(12), px(24)),
                  pady=(px(6), px(10)))

        # 列表改用 Canvas 自己排行：Text 控件只能给整行铺一块**方形**底色，
        # 想要"每条消息是一张圆角卡片"就只能自己画（PIL 圆角底图 + 原生文字）。
        行底 = self.theme.get("row_bg") or ("#2f2f2f" if self.深 else "#f5f5f5")
        卡半径 = int(px(10))
        内边 = int(px(11))
        图标列 = int(px(17))
        行宽 = 卡宽 - 2 * 内缩 - px(12) - px(14) - px(6)   # 左右留白 + 滚动条 + 间隙
        文本宽 = max(px(80), 行宽 - 2 * 内边 - 图标列)
        字号 = ("微软雅黑", 9)
        元号 = ("微软雅黑", 8)
        芯片号 = ("微软雅黑", 8, "bold")

        滚 = tk.Canvas(内, width=px(6), bg=装, highlightthickness=0, bd=0)
        滚.pack(side="right", fill="y", padx=(px(2), px(4)), pady=(px(6), px(8)))
        列表 = tk.Canvas(内, bg=装, highlightthickness=0, bd=0, takefocus=0)
        列表.pack(side="left", fill="both", expand=True, padx=(px(12), 0),
                  pady=(px(2), px(8)))
        self._列表 = 列表

        图缓存 = {}
        引住 = []          # ImageTk 对象必须有人引用，否则被 GC 掉就变白块

        def 圆角图(w, h, r, 色):
            键 = (int(w), int(h), int(r), 色)
            图 = 图缓存.get(键)
            if 图 is None:
                try:
                    from utils.helpers import _圆角矩形
                    图 = _圆角矩形((max(1, 键[0]), max(1, 键[1])), max(1, 键[2]), 色)
                except Exception:
                    图 = None
                图缓存[键] = 图
            return 图

        def 贴底(x, y, w, h, r, 色, 置底=False):
            try:
                from PIL import ImageTk
                图 = 圆角图(w, h, r, 色)
                if 图 is None:
                    return None
                照 = ImageTk.PhotoImage(图)
                引住.append(照)
                号 = 列表.create_image(int(x), int(y), anchor="nw", image=照)
                if 置底:
                    列表.tag_lower(号)
                return 号
            except Exception:
                return None

        def 圆角滑块(上, 下):
            """滚动条也做成圆头（系统滚动条是方的，在这一版里很扎眼）。"""
            色 = self.theme.get("border") or "#555555"
            高 = max(int(px(18)), int(下) - int(上))
            贴底(0, int(上), px(6), 高, px(3), 色)

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
                    圆角滑块(上, min(下, 高))
            except Exception:
                pass

        列表.configure(yscrollcommand=_同步)

        # 连续重复的折叠成一条（末尾显示 ×N）：反复开关同一个设置时，
        # 面板会被十条一模一样的消息糊满，真正的内容反而看不见。
        # 带 key 的消息文案会随开/关变，但仍是"同一条"，也折进来。
        def _分组(项):
            组 = []
            for x in 项:
                上 = 组[-1][-1] if 组 else None
                同 = False
                if 上 is not None:
                    if x.get("key"):
                        同 = 上.get("key") == x.get("key")
                    else:
                        同 = (not 上.get("key")
                              and 上.get("text") == x.get("text")
                              and 上.get("level") == x.get("level"))
                if 同:
                    组[-1].append(x)
                else:
                    组.append([x])
            return 组

        self._可见条数 = 0

        def _重画():
            列表.delete("all")
            del 引住[:]
            组 = _分组(self.store.全部())
            self._可见条数 = len(组)
            if not 组:
                cw = 列表.winfo_width()
                cw = cw if cw > 1 else (行宽 + px(24))
                ch = 列表.winfo_height()
                ch = ch if ch > 1 else px(240)
                列表.create_text(cw // 2, ch // 2 - px(28), text="🔔",
                                 font=("微软雅黑", 20), fill=self.弱)
                列表.create_text(cw // 2, ch // 2 + px(8), text=trp("还没有消息"),
                                 font=("微软雅黑", 10), fill=self.字)
                列表.create_text(cw // 2, ch // 2 + px(32),
                                 text=trp("操作提示会记在这里，吐司消失后也能回看"),
                                 font=("微软雅黑", 8), fill=self.弱)
                列表.configure(scrollregion=(0, 0, cw, ch))
                return
            y = 0
            for 一组 in 组:
                x = 一组[-1]                    # 用最后一条的时间与文案
                级 = x.get("level") if x.get("level") in _外观 else "info"
                图符, 色 = _外观[级]
                try:
                    时 = time.strftime("%H:%M", time.localtime(float(x.get("t") or 0)))
                except Exception:
                    时 = ""
                次 = sum(int(v.get("n") or 1) for v in 一组)
                元 = 时 + "  " + _级别名.get(级, "")
                # 先让 Tk 按宽度折行，再拿 bbox 量出正文高度，卡片高度才准
                正 = 列表.create_text(内边 + 图标列, y + 内边, anchor="nw",
                                      width=文本宽, text=str(x.get("text") or ""),
                                      font=字号, fill=self.字)
                bb = 列表.bbox(正)
                文高 = (bb[3] - bb[1]) if bb else px(16)
                卡高 = 内边 + 文高 + px(5) + int(px(13)) + 内边
                贴底(0, y, 行宽, 卡高, 卡半径, 行底, 置底=True)
                列表.coords(正, 内边 + 图标列, y + 内边)
                列表.create_text(内边 + int(px(4)), y + 内边 - px(2), anchor="nw",
                                 text=图符, font=("微软雅黑", 10, "bold"), fill=色)
                列表.create_text(内边 + 图标列, y + 内边 + 文高 + px(5), anchor="nw",
                                 text=元, font=元号, fill=self.弱)
                if 次 > 1:
                    # ×N 做成实心色块（只当灰字太小了，用户："×5 不够明显"）
                    芯高 = int(px(16))
                    芯宽 = int(px(10) + px(7) * len(str(次)))
                    芯y = y + 内边 + 文高 + px(3)
                    贴底(行宽 - 内边 - 芯宽, 芯y, 芯宽, 芯高, 芯高 // 2, 色)
                    列表.create_text(行宽 - 内边 - 芯宽 // 2, 芯y + 芯高 // 2,
                                     text="×%d" % 次, font=芯片号,
                                     fill="#1b1b1b" if 色 in ("#ffa000", "#8bc34a") else "#ffffff")
                y += 卡高 + px(6)
            列表.configure(scrollregion=(0, 0, 行宽 + px(2), y))
            try:
                列表.yview_moveto(1.0)      # 最新的在下面：打开就停在最新一条
            except Exception:
                pass

        def _轮(e):
            try:
                列表.yview_scroll(-1 if int(getattr(e, "delta", 0)) > 0 else 1, "units")
            except Exception:
                pass
            return "break"

        列表.bind("<MouseWheel>", _轮)
        列表.bind("<Configure>", lambda e: _重画())

        def _拖(e):
            try:
                列表.yview_moveto(max(0.0, min(1.0, e.y / max(1, 滚.winfo_height()))))
            except Exception:
                pass

        滚.bind("<Button-1>", _拖)
        滚.bind("<B1-Motion>", _拖)
        滚.bind("<Configure>", lambda e: 列表.yview())

        def _清空():
            self.store.clear()
            self._重画()

        # 底栏：左边一句脚注，右边两个按钮 ——「清空」是次要操作做成幽灵按钮，
        # 「关闭」保留实心（和主界面同一套自绘渐变按钮）
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
        except Exception:
            for 标, 命令, side in ((trp("清空"), _清空, "left"),
                                   (trp("关闭"), self.close, "right")):
                钮 = tk.Label(底栏, text=标, bg=self.theme.get("button_bg", "#e0e0e0"),
                              fg=self.theme.get("button_fg", "#222222"), cursor="hand2",
                              font=("微软雅黑", 9), padx=px(12), pady=px(3))
                钮.pack(side=side)
                钮.bind("<Button-1>", lambda e, f=命令: f())
        _幽灵(trp("清空"), _清空)

        def _刷新徽标():
            # 徽标显示的是**列表里真正有几行**（合并后的行数），不是原始条数：
            # 原来显示 len(store.items)，合并过的消息就会"5 条却只看到 3 行"。
            try:
                n = int(getattr(self, "_可见条数", 0) or 0)
                徽标.config(text=(trp("{0} 条").format(n) if n else ""))
            except Exception:
                pass

        旧重画 = _重画

        def _重画2():
            旧重画()
            _刷新徽标()

        self._重画 = _重画2
        _重画2()
        self.store.标记已读()

        # 隐藏状态下先把区域收成"零宽"（左 = 卡宽）：这样首次显示不会漏出方块，
        # 淡入的第一帧就是从右边缘长出来的一条圆角。
        try:
            win.update_idletasks()
            _裁区域(win, 卡宽, 卡宽, 高, 半径)
        except Exception:
            pass

        win.bind("<Escape>", lambda e: self.close())
        try:
            self._configure_bind = self.父窗.bind(
                "<Configure>", self._on_parent_configure, add="+")
        except Exception:
            pass
        try:
            # 主窗口被最小化时，抽屉是个独立窗口，会孤零零留在桌面上 ——
            # 这时候也要走同一条滑出动画（"被动收回"）
            self._unmap_bind = self.父窗.bind("<Unmap>", self._on_parent_unmap, add="+")
        except Exception:
            pass
        return win

    def _on_parent_unmap(self, _e=None):
        try:
            if self.win is not None and str(self.父窗.state()) == "iconic":
                self.close()
        except Exception:
            pass

    def _on_parent_configure(self, _e=None):
        """主窗口移动/缩放时重新贴住右边框（尺寸变了还要重裁圆角）。"""
        if self.win is None:
            return
        try:
            if not self.win.winfo_exists():
                return
            宽, 高, x, y = self._目标几何()
            self._目标 = (宽, 高, x, y)
            # 跟着主窗动的时候也要保持在"动画当前进度"对应的位置上，
            # 否则拖窗口会看到卡片先跳回终点、动画一结束又跳回去。
            self._滑 = self._可滑距离(x, self._卡宽())
            self._漂 = max(0, self._漂移 - self._滑)
            比 = self._当前左(0) / float(max(1, self._目标[0]))
            偏移 = int(round(self._滑 * 比)) if _WIN11 else 0
            self.win.geometry("%dx%d+%d+%d" % (宽, 高, x + 偏移, y))
            卡宽 = self._卡宽()
            self._容器.place_configure(width=卡宽, height=高)
            if not _WIN11:
                _裁区域(self.win, self._当前左(0), 卡宽, 高, self._半径())
        except Exception:
            pass

    def _当前左(self, 默认=0):
        return int(getattr(self, "_左", 默认))

    def slide_in(self):
        宽, 高 = self._目标[0], self._目标[1]
        卡宽 = self._卡宽()
        self._关闭中 = False
        # 显示之前把区域收成"零宽"：deiconify 渲的第一帧只能是空的，
        # 不会先整块露一下再从右边长出来（那就是"闪一下"）
        self._左 = 宽
        # Win11：不裁区域（那条边没有抗锯齿，就是"边框立在那"），改成挪窗口。
        # 起点只能摆到"屏幕右缘还容得下整张卡"的地方 —— 再往右就被屏幕切掉，
        # 那正是上一版"打开动画没了、收回跑出边框"的原因。滑不动的距离由
        # 内容轻飘补（见 _animate）。
        if _WIN11:
            try:
                self.win.geometry("%dx%d+%d+%d"
                                  % (宽, 高, self._目标[2] + int(self._滑),
                                     self._目标[3]))
            except Exception:
                pass
        if not _WIN11:
            try:
                _裁区域(self.win, 宽, 卡宽, 高, self._半径())
            except Exception:
                pass
        try:
            self.win.deiconify()
            self.win.lift()
        except Exception:
            pass
        if _WIN11:
            # deiconify 可能重建 HWND，窗口一换角偏好就没了 —— 这里补一次。
            _设DWM圆角(self.win)
        # ⚠ 显示之后**立刻**再裁一次（不是延迟 80ms）：withdraw 状态的 Toplevel 被
        # deiconify 时 HWND 可能被重建，区域会跟着丢；延迟补裁的话，动画前几帧
        # 就是方的（用户："动画时没有圆角化"）。这里同步补，第一帧就是圆的。
        if not _WIN11:
            try:
                _裁区域(self.win, self._左, 卡宽, 高, self._半径())
            except Exception:
                pass
        # 关键顺序：deiconify → 先让 Tk 把客户区**真 paint 一遍**（填键色）→ 再放开 alpha。
        # 少了这一步，窗口可能"已经 alpha=1，但客户区还没被 paint"，系统对未初始化的
        # 表面不做键色匹配，于是把一块近黑矩形原样端上去 —— 就是用户看到的黑方块。
        try:
            self.win.update()
        except Exception:
            pass
        # 面板图和圆角都已经就位，这才把窗口放出来（alpha 由动画逐帧抬起来）。
        # 同步起滑（不再 after(0)）：放开 alpha 和第一帧动画落在同一帧里，
        # 中间不会夹一个"已经可见但区域还是收拢的"空帧。
        def 起滑():
            # 这一帧之前就被关掉了（铃铛连点）：别再起滑入动画，让关闭动画接管
            if not self._关闭中:
                self._animate(self._当前左(宽), 0)

        try:
            起滑()
        except Exception:
            self._animate(宽, 0)
        self._装外点()

    def slide_out_and_close(self):
        宽 = self._目标[0]
        # 起点取**当前**位置：滑入还没走完就被关掉时，从当前位置往回抽，
        # 而不是先跳回 0 再抽走（那样会看到一下弹跳）
        self._animate(self._当前左(0), 宽, 结束后=self._destroy)

    def _移内容(self, 漂):
        """Win11 下动画只动内容：卡片本身（四条边 + 圆角）一动不动。"""
        try:
            self._内.place_configure(x=self._内缩 + int(漂))
        except Exception:
            pass

    def _animate(self, 起, 终, 结束后=None):
        """只改两样东西：窗口区域（展开/收拢）+ alpha（淡入淡出）。

        内容容器钉在 0，每帧都不用重排重画；可见形状任何一帧都是半径 _圆角 的
        圆角矩形，连展开时的前缘那条边也是。
        """
        卡宽, 高 = self._卡宽(), self._目标[1]
        半径 = self._半径()
        目标x = self._目标[2]
        t0 = time.perf_counter()
        try:
            anim.hold()
        except Exception:
            pass
        收 = 终 > 起                 # 收回
        上次 = [None, None]

        def 帧():
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / _滑入ms)
            # 开：ease-out-quint（一上来就动，尾部缓）；关：ease-in（越走越快被抽走）
            缓 = (k * k) if 收 else (1 - (1 - k) ** 5)
            左 = int(起 + (终 - 起) * 缓)
            if 左 != 上次[0]:
                上次[0] = 左
                self._左 = 左
                if _WIN11:
                    # 几何一律不动。挪窗口在父窗最大化时会跑到屏幕外（实测：
                    # 「打开动画没了，收回动画都跑出窗口边框」）；裁区域又会留下
                    # 一条没有抗锯齿的硬边（「边框还立在那」）。所以只让内容横着
                    # 飘一点点，卡片四条边从头到尾一动不动，靠 alpha 撑起"从右侧
                    # 进来"的观感。
                    总 = max(1, max(abs(起), abs(终)))
                    比 = float(左) / float(总)
                    滑 = int(round(self._滑 * 比))
                    if 滑:
                        try:
                            self.win.geometry("%dx%d+%d+%d"
                                              % (self._目标[0], 高, 目标x + 滑,
                                                 self._目标[3]))
                        except Exception:
                            pass
                    self._移内容(int(round(getattr(self, "_漂", 0) * 比)))
                else:
                    try:
                        _裁区域(self.win, 左, 卡宽, 高, 半径)
                    except Exception:
                        pass
            # alpha：开 前 45% 淡到 1；收 后 40% 淡到 0。
            # 区域边是硬的（SetWindowRgn 没有抗锯齿），淡一下才不显得是在"跳"。
            if _WIN11:
                # Win11 没有位移可看，淡入就摊到整段时长（不然 117ms 就到 1，
                # 剩下 140ms 什么都没发生，看着像"卡了一下才出现"）。
                a = max(0.0, 1.0 - k) if 收 else min(1.0, k)
            else:
                a = max(0.0, 1.0 - (k - 0.6) / 0.4) if 收 else min(1.0, k / 0.45)
            if 上次[1] is None or abs(a - 上次[1]) > 0.04 or k >= 1.0:
                上次[1] = a
                try:
                    self.win.attributes("-alpha", round(a, 3))
                except Exception:
                    pass
            if k < 1.0:
                try:
                    self._job = self.win.after(max(4, anim.frame_ms(self.win)), 帧)
                except Exception:
                    self._job = None
            else:
                self._job = None
                if _WIN11:
                    self._移内容(0)
                    try:
                        self.win.geometry("%dx%d+%d+%d"
                                          % (self._目标[0], self._目标[1],
                                             目标x, self._目标[3]))
                    except Exception:
                        pass
                if _WIN11 and not 收:
                    # 展开了：撤掉区域，圆角重新交给 DWM（留着会把 DWM 的圆角
                    # 按 1-bit 形状切掉，又变回楼梯）。
                    # ⚠ 必须在撤完**再下一次**属性：deiconify 会重建 HWND、
                    # SetWindowRgn 之后 DWM 也可能丢掉角偏好，不下就会变成直角
                    # （第一次就踩了：build 里设过，展开完还是方的）。
                    _撤区域(self.win)
                    _设DWM圆角(self.win)
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
            if self._unmap_bind is not None:
                self.父窗.unbind("<Unmap>", self._unmap_bind)
        except Exception:
            pass
        self._unmap_bind = None
        if getattr(self, "_外点", None):
            # bind_all 是解释器级的：这里一起摘掉（全项目只有这一处用 <Button-1> 的
            # 全局绑定，整条摘掉不会误伤别的窗口）
            try:
                self.父窗.unbind_all("<Button-1>")
            except Exception:
                pass
        self._外点 = None
        try:
            self.win.destroy()
        except Exception:
            pass
        self.win = None
        if getattr(open_message_center, "_抽屉", None) is self:
            open_message_center._抽屉 = None

    # ------------------------------------------------------------ 点外部收回
    def _装外点(self):
        """「点消息中心以外就收回」：默认关，开了才挂这个全局点击监听。"""
        开关 = getattr(self.父窗, "msg_click_outside_close", None)
        开 = False
        try:
            开 = bool(开关.get()) if 开关 is not None else False
        except Exception:
            开 = False
        if not 开 or self.win is None:
            return
        try:
            self._外点 = self.win.bind_all("<Button-1>", self._外点点击, add="+")
        except Exception:
            self._外点 = None

    def _外点点击(self, e):
        """点到了抽屉以外的控件（bind_all 能看到整个程序里的点击）。"""
        if getattr(self, "_关闭中", False) or self.win is None:
            return
        try:
            w = e.widget
            if w is not None and w.winfo_toplevel() is self.win:
                return          # 点在自己身上（卡片、滚动条、✕ 都算）
        except Exception:
            return
        self.close()

    def close(self):
        """任何关闭路径都走这里：统一成滑出动画，不再有瞬间消失。"""
        if getattr(self, "_关闭中", False) or self.win is None:
            return
        self._关闭中 = True
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
                return None      # 正在收起时再点铃铛：先让它收完，不开第二个窗口
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

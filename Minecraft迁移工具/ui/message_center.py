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


# 级别 → (图标, 颜色)
_外观 = {
    "ok":    ("✓", "#4caf50"),
    "info":  ("i", "#4c9aff"),
    "warn":  ("!", "#ffa000"),
    "error": ("×", "#e53935"),
}

_宽 = 372          # 抽屉宽度（设计像素）
_高上限 = 560      # 抽屉最高
_滑入ms = 170


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

        win = tk.Toplevel(self.父窗)
        win.overrideredirect(True)
        win.configure(bg=边)          # 外层当 1px 描边
        win.withdraw()
        self.win = win
        win.geometry("%dx%d+%d+%d" % (宽, 高, self._屏幕外x(), 目标y))
        self._目标 = (宽, 高, 目标x, 目标y)

        内 = tk.Frame(win, bg=装)
        内.pack(fill="both", expand=True, padx=1, pady=1)

        # 顶栏：标题 + 关闭
        顶 = tk.Frame(内, bg=装)
        顶.pack(fill="x", padx=px(12), pady=(px(10), px(2)))
        tk.Label(顶, text=trp("消息中心"), bg=装, fg=self.字,
                 font=("微软雅黑", 10, "bold")).pack(side="left")
        关 = tk.Label(顶, text="✕", bg=装, fg=self.弱, cursor="hand2",
                      font=("微软雅黑", 10))
        关.pack(side="right")
        关.bind("<Button-1>", lambda e: self.close())

        tk.Label(内, text=trp("灰字是上一次运行留下的"), bg=装, fg=self.弱,
                 font=("微软雅黑", 8), anchor="w").pack(fill="x", padx=px(12))

        # 底栏先 pack，否则列表会把它挤没（Tk 的 pack 按顺序分配）
        底栏 = tk.Frame(内, bg=装)
        底栏.pack(side="bottom", fill="x", padx=px(12), pady=px(10))

        文 = tk.Text(内, bg=self.文本底, fg=self.字, bd=0, highlightthickness=1,
                     highlightbackground=边, wrap="word", font=("微软雅黑", 9),
                     padx=px(8), pady=px(6))
        # 滚动条也要跟主题：默认那条是系统浅灰色，在深色抽屉里像贴了张纸条
        条 = tk.Scrollbar(内, command=文.yview, orient="vertical",
                          bg=self.theme.get("button_bg", "#e0e0e0"),
                          troughcolor=装, activebackground=self.弱,
                          bd=0, highlightthickness=0, width=px(10))
        文.configure(yscrollcommand=条.set)
        条.pack(side="right", fill="y", pady=(px(4), px(4)))
        文.pack(side="left", fill="both", expand=True, padx=(px(12), 0),
                  pady=(px(4), px(4)))
        self.文 = 文

        for 级, (图, 色) in _外观.items():
            文.tag_configure("级_" + 级, foreground=色)
        文.tag_configure("时", foreground=self.弱)

        def _重画():
            try:
                文.configure(state="normal")
                文.delete("1.0", "end")
            except Exception:
                return
            项 = self.store.全部()
            if not 项:
                文.insert("end", trp("还没有消息。\n"), "时")
            for x in 项:
                级 = x.get("level") if x.get("level") in _外观 else "info"
                图, _色 = _外观[级]
                try:
                    时 = time.strftime("%H:%M:%S", time.localtime(float(x.get("t") or 0)))
                except Exception:
                    时 = ""
                文.insert("end", 时 + "  ", "时")
                文.insert("end", 图 + " ", "级_" + 级)
                文.insert("end", str(x.get("text") or "") + "\n")
            文.configure(state="disabled")
            try:
                文.see("end")
            except Exception:
                pass

        self._重画 = _重画

        def _清空():
            self.store.clear()
            _重画()

        for 标, 命令, side in ((trp("清空"), _清空, "left"),
                               (trp("关闭"), self.close, "right")):
            钮 = tk.Label(底栏, text=标, bg=self.theme.get("button_bg", "#e0e0e0"),
                          fg=self.theme.get("button_fg", "#222222"), cursor="hand2",
                          font=("微软雅黑", 9), padx=px(12), pady=px(3))
            钮.pack(side=side)
            钮.bind("<Button-1>", lambda e, f=命令: f())

        _重画()
        self.store.标记已读()

        win.bind("<Escape>", lambda e: self.close())
        # 主窗口移动/缩放时跟着走（抽屉必须始终贴住右边框）
        try:
            self._configure_bind = self.父窗.bind(
                "<Configure>", self._on_parent_configure, add="+")
        except Exception:
            pass
        return win

    def _on_parent_configure(self, _e=None):
        if self.win is None:
            return
        try:
            if not self.win.winfo_exists():
                return
            宽, 高, x, y = self._目标几何()
            self._目标 = (宽, 高, x, y)
            self.win.geometry("%dx%d+%d+%d" % (宽, 高, x, y))
        except Exception:
            pass

    # ---------------------------------------------------------------- 动画
    def slide_in(self):
        宽, 高, 目标x, 目标y = self._目标
        起x = self._屏幕外x()
        try:
            self.win.deiconify()
            self.win.lift()
        except Exception:
            pass
        self._animate(起x, 目标x, 目标y)

    def slide_out_and_close(self):
        宽, 高, 目标x, 目标y = self._目标
        终x = self._屏幕外x()
        self._animate(目标x, 终x, 目标y, 结束后=self._destroy)

    def _animate(self, 起x, 终x, y, 结束后=None):
        宽, 高 = self._目标[0], self._目标[1]
        t0 = time.perf_counter()
        try:
            anim.hold()
        except Exception:
            pass

        def 帧():
            k = min(1.0, (time.perf_counter() - t0) * 1000.0 / _滑入ms)
            缓 = 1 - (1 - k) ** 3 if 终x > 起x else k * k
            x = int(起x + (终x - 起x) * 缓)
            try:
                self.win.geometry("%dx%d+%d+%d" % (宽, 高, x, int(y)))
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

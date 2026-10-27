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
· 内存里始终保留最近 200 条（含 info），面板里看得到本次运行的完整提示序列。

## 落盘位置

与配置同目录的 .minecraft_migrate_messages.json（原子写，写坏不会丢掉历史）。
"""

import json
import os
import time
from pathlib import Path

import tkinter as tk

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

    # ---------------------------------------------------------------- 读写
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

    # ---------------------------------------------------------------- 对外
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


def open_message_center(父窗, store, theme):
    """打开消息中心（已开着就提到前面）。返回窗口对象。"""
    旧 = getattr(open_message_center, "_win", None)
    try:
        if 旧 is not None and 旧.winfo_exists():
            旧.deiconify()
            旧.lift()
            旧.focus_set()
            return 旧
    except Exception:
        pass

    深 = str((theme or {}).get("bg", "#ffffff")).lower() in (
        "#2e2e2e", "#1e1e1e", "#252525", "#202020", "#2b2b2b")
    底 = theme.get("bg", "#f0f0f0")
    字 = theme.get("fg", "#222222")
    弱 = theme.get("muted_fg", "#888888")
    文本底 = theme.get("text_bg", "#ffffff")

    win = tk.Toplevel(父窗)
    win.title(trp("消息中心"))
    win.configure(bg=底)
    win.geometry("%dx%d" % (px(660), px(430)))
    win.transient(父窗)
    open_message_center._win = win

    tk.Label(win, text=trp("最近的提示消息（灰字是上一次运行留下的）"),
             bg=底, fg=弱, font=("微软雅黑", 9), anchor="w").pack(
        fill="x", padx=px(12), pady=(px(10), px(4)))

    # ⚠ 先 pack 底部按钮栏（side=bottom），再 pack 可扩展的列表区。
    # 反过来的话，Text 的默认请求高度（24 行）+ expand 会吃光空间，
    # 后面 pack 的按钮栏只分到 1px、mapped=0 —— 按钮"消失"（实测踩过）。
    底栏 = tk.Frame(win, bg=底)
    底栏.pack(side="bottom", fill="x", padx=px(12), pady=px(10))

    框 = tk.Frame(win, bg=底)
    框.pack(fill="both", expand=True, padx=px(12))
    文 = tk.Text(框, bg=文本底, fg=字, bd=0, highlightthickness=1,
                 highlightbackground=theme.get("border", "#cccccc"),
                 wrap="word", font=("微软雅黑", 9), padx=px(8), pady=px(6))
    条 = tk.Scrollbar(框, command=文.yview, orient="vertical")
    文.configure(yscrollcommand=条.set)
    条.pack(side="right", fill="y")
    文.pack(side="left", fill="both", expand=True)

    for 级, (图, 色) in _外观.items():
        文.tag_configure("级_" + 级, foreground=色)
    文.tag_configure("时", foreground=弱)
    文.tag_configure("旧", foreground=弱)

    项 = store.全部()
    if not 项:
        文.insert("end", trp("还没有消息。\n"), "时")
    for x in 项:
        级 = x.get("level") if x.get("level") in _外观 else "info"
        图, _色 = _外观[级]
        try:
            时 = time.strftime("%m-%d %H:%M:%S", time.localtime(float(x.get("t") or 0)))
        except Exception:
            时 = ""
        文.insert("end", 时 + "  ", "时")
        文.insert("end", 图 + " ", "级_" + 级)
        文.insert("end", str(x.get("text") or "") + "\n",
                  ("旧",) if x.get("旧") else ())
    文.configure(state="disabled")
    try:
        文.see("end")
    except Exception:
        pass

    store.标记已读()

    def _清空():
        store.clear()
        try:
            文.configure(state="normal")
            文.delete("1.0", "end")
            文.insert("end", trp("还没有消息。\n"), "时")
            文.configure(state="disabled")
        except Exception:
            pass

    tk.Button(底栏, text=trp("清空"), command=_清空, bg=theme.get("button_bg", "#e0e0e0"),
              fg=theme.get("button_fg", "#222222"), bd=0, padx=px(12), pady=px(3),
              font=("微软雅黑", 9)).pack(side="left")
    tk.Button(底栏, text=trp("关闭"), command=win.destroy,
              bg=theme.get("button_bg", "#e0e0e0"),
              fg=theme.get("button_fg", "#222222"), bd=0, padx=px(12), pady=px(3),
              font=("微软雅黑", 9)).pack(side="right")

    win.bind("<Escape>", lambda e: win.destroy())
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    return win

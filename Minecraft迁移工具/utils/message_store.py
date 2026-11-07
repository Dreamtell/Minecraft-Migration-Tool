# utils/message_store.py
"""消息中心的存档(Tk 主界面和 Qt 主界面共用)。

原来写在 ui/message_center.py 里,但那个模块顶层就 import tkinter + 一整套 Tk 绘制代码,
Qt 进程不能碰。存储本身是纯 Python(读写 JSON),抽到这里;ui/message_center.py 原地
re-export,老的 `from ui.message_center import MessageStore` 照常可用。代码逐字未改。

与配置同目录的 .minecraft_migrate_messages.json(原子写,写坏不会丢掉历史)。
内存里全留,磁盘上只留 warn / error。
"""
import json
import os
import time
from pathlib import Path

from utils.config import CONFIG_FILE

容量 = 200
_重要级别 = ("warn", "error")

# 级别 → 中文名(列表里写 ok/warn/error 是开发者语言,用户看的是中文)
级别名 = {"ok": "成功", "info": "提示", "warn": "警告", "error": "错误"}
# 级别 → (图标, 颜色)
外观 = {
    "ok":    ("✓", "#4caf50"),
    "info":  ("i", "#4c9aff"),
    "warn":  ("!", "#ffa000"),
    "error": ("×", "#e53935"),
}


def _存盘路径():
    try:
        return Path(CONFIG_FILE).parent / ".minecraft_migrate_messages.json"
    except Exception:
        return Path.home() / ".minecraft_migrate_messages.json"


class MessageStore:
    """消息存档:内存里全留,磁盘上只留重要的。"""

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
                        "未读": False,      # 上次运行的记录,打开面板不该算新消息
                    })
        except Exception:
            pass

    def _写盘(self):
        """只写重要的;原子写(写一半崩掉不会留下坏 JSON)。"""
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
        # 同一个 key = 同一条消息的"状态变化"(开/关这类开关反复拨)。
        # 直接并进上一条:面板只留一行最新状态 + ×N,而不是被十条糊满。
        上 = self.items[-1] if self.items else None
        if key and 上 is not None and 上.get("key") == key:
            # 合并的这一行如果本来就是未读,就不该再 +1 —— 未读数是"面板里有几行
            # 是新消息",不是"add 被调用过几次"(合并三次仍是一行)。
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

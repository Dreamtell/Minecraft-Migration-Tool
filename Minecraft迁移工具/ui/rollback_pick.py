# -*- coding: utf-8 -*-
"""选「这次回滚还原哪些东西」的小对话框（自绘，跟自检弹窗一个风格）。"""
from __future__ import annotations

import tkinter as tk

from utils.i18n import trp

# 键 → (显示名, 说明)
_PART_LABELS = {
    "mods": ("🧩 模组（mods/）", "把 mods 目录整个还原成迁移前的样子"),
    "config": ("⚙️ 配置（config/）", "把 config 目录整个还原成迁移前的样子"),
    "saves": ("💾 存档（saves/）", "把 saves 目录整个还原成迁移前的样子"),
    "files": ("📄 根目录文件", "options.txt 这类直接放在实例根目录的文件"),
    "extras": ("📦 「其它文件」清单", "你在「其它文件」页里列的那些条目"),
}


def _配色(theme):
    dark = str(theme.get("bg", "#ffffff")).lower() in ("#2e2e2e", "#1e1e1e", "#252526")
    if dark:
        return {"卡": "#2e2e2e", "底": "#252526", "字": "#e6e6e6", "弱": "#9e9e9e",
                "边": "#3a3a3a", "要": "#4da3f0", "悬": "#353535"}
    return {"卡": "#f7f7f7", "底": "#ffffff", "字": "#1f1f1f", "弱": "#666666",
            "边": "#dcdcdc", "要": "#2f7fd1", "悬": "#eeeeee"}


def _设圆角(窗):
    try:
        from ui.message_center import _WIN11, _设DWM圆角
        if _WIN11:
            _设DWM圆角(窗)
    except Exception:
        pass


def pick_parts(parent, theme, 有无, 默认=None, 记住=False):
    """问用户「这次还原哪些」。

    参数
      parent: 父窗口（居中用）
      theme : 主题字典
      有无  : {键: 数量}（`core.migrator.backup_parts_available` 的返回）
      默认  : 默认勾选的键集合（一般是上次记住的选择）
      记住  : 「记住我的选择」的初始状态

    返回 `(选中的键集合 或 None, 记住与否)`；用户取消时返回 `(None, 记住)`。
    """
    from utils.helpers import create_gradient_button

    色 = _配色(theme)
    可选 = [k for k in ("mods", "config", "saves", "files", "extras") if 有无.get(k)]
    if not 可选:
        可选 = ["mods", "config", "saves", "files", "extras"]
    预选 = [k for k in 可选 if (默认 is None or k in 默认)]

    结果 = {"值": None, "记住": bool(记住)}

    窗 = tk.Toplevel(parent)
    窗.withdraw()
    窗.overrideredirect(True)
    try:
        窗.attributes("-alpha", 0.999)
    except Exception:
        pass
    窗.configure(bg=色["卡"])

    壳 = tk.Frame(窗, bg=色["卡"])
    壳.pack(fill="both", expand=True)

    头 = tk.Frame(壳, bg=色["卡"])
    头.pack(fill="x", padx=14, pady=(12, 2))
    tk.Label(头, text="↩", bg=色["卡"], fg=色["要"],
             font=("微软雅黑", 13, "bold")).pack(side="left")
    tk.Label(头, text=trp("回滚：这次还原哪些"), bg=色["卡"], fg=色["字"],
             font=("微软雅黑", 11, "bold")).pack(side="left", padx=(6, 0))

    小 = tk.Frame(壳, bg=色["卡"])
    小.pack(fill="x", padx=16, pady=(0, 6))
    tk.Label(小, text=trp("勾掉的类别这次不动（迁移后的内容会原样留着）。"),
             bg=色["卡"], fg=色["弱"], font=("微软雅黑", 8),
             justify="left", anchor="w").pack(fill="x")

    def 做勾选行(父, 文, 说明=None, 初值=True, 小字=False):
        """自绘的圆角勾选行：左边一个小圆角方块（勾上时填主色 + 白勾），
        右边是名称（可选带一行灰说明）。整行可点，悬停会亮一点。"""
        高 = 30 if 说明 else 26
        宽 = int(父.winfo_width() or 400)
        画 = tk.Canvas(父, height=高, bg=色["卡"], highlightthickness=0, bd=0,
                       cursor="hand2")
        画.pack(fill="x", pady=1)
        态 = {"值": bool(初值), "悬": False}

        def 圆角图(宽, 高, 半径, 填充, 描边=None):
            """烤一张圆角矩形（超采样再缩回来，边缘不发毛）。"""
            from PIL import Image as _I, ImageDraw as _D, ImageTk as _Tk
            S = 4
            大 = _I.new("RGBA", (宽 * S, 高 * S), (0, 0, 0, 0))
            _D.Draw(大).rounded_rectangle(
                [0, 0, 宽 * S - 1, 高 * S - 1], radius=max(1, 半径) * S,
                fill=填充, outline=描边, width=(S if 描边 else 0))
            小 = 大.resize((宽, 高), _I.LANCZOS)
            return _Tk.PhotoImage(小)

        _图缓存 = {}

        def 取图(k, 宽, 高, 半径, 填充, 描边=None):
            t = (k, 宽, 高, 半径, 填充, 描边)
            if t not in _图缓存:
                _图缓存[t] = 圆角图(宽, 高, 半径, 填充, 描边)
            return _图缓存[t]

        def 画全部(_=None):
            画.delete("all")
            宽 = int(画.winfo_width() or 400)
            底 = 色["悬"] if 态["悬"] else 色["卡"]
            画.create_image(0, 0, anchor="nw",
                            image=取图("行", 宽, 高, 8, 底))
            盒 = 16
            x0, y0 = 8, (高 - 盒) // 2
            if 态["值"]:
                画.create_image(x0, y0, anchor="nw",
                                image=取图("盒开", 盒, 盒, 5, 色["要"]))
                画.create_text(x0 + 盒 / 2, y0 + 盒 / 2 - 1, text="✓",
                                fill="#ffffff", font=("微软雅黑", 9, "bold"))
            else:
                画.create_image(x0, y0, anchor="nw",
                                image=取图("盒关", 盒, 盒, 5, 色["底"], 色["边"]))
            字 = 文 if not 小字 else 文
            画.create_text(x0 + 盒 + 10, 高 / 2, text=字, anchor="w",
                           fill=色["字"], font=("微软雅黑", 9))
            if 说明:
                画.create_text(宽 - 10, 高 / 2, text=说明, anchor="e",
                               fill=色["弱"], font=("微软雅黑", 8))

        def 点(_e=None):
            态["值"] = not 态["值"]
            画全部()

        画.bind("<Button-1>", 点)
        画.bind("<Configure>", 画全部)
        画.bind("<Enter>", lambda _e: (态.__setitem__("悬", True), 画全部()))
        画.bind("<Leave>", lambda _e: (态.__setitem__("悬", False), 画全部()))
        画.get = lambda: 态["值"]
        画全部()
        return 画

    变量们 = {}
    for 键 in 可选:
        名, 说明 = _PART_LABELS[键]
        数 = int(有无.get(键) or 0)
        文 = trp(名) + (trp("（{0} 项）", 数) if 数 else "")
        变量们[键] = 做勾选行(壳, 文, trp(说明), 键 in 预选)

    记住变量 = 做勾选行(壳, trp("记住我的选择（以后回滚都按这个，可在设置里改）"),
                     None, bool(记住), 小字=True)

    底 = tk.Frame(壳, bg=色["卡"])
    底.pack(fill="x", padx=14, pady=(8, 12))

    def 收(值):
        结果["值"] = 值
        结果["记住"] = bool(记住变量.get())
        try:
            窗.grab_release()
        except Exception:
            pass
        窗.destroy()

    def 开始():
        选了 = {k for k, v in 变量们.items() if v.get()}
        if not 选了:
            收(None)
            return
        收(选了)

    主 = create_gradient_button(底, trp("↩ 开始回滚"), 开始,
                                colors=("#d32f2f", "#e53935"), height=32)
    主.pack(side="right")
    次 = create_gradient_button(底, trp("取消"), lambda: 收(None),
                                colors=(色["边"], 色["边"]), height=32)
    次.pack(side="right", padx=(0, 8))

    窗.bind("<Escape>", lambda _e: 收(None))
    窗.bind("<Return>", lambda _e: 开始())

    窗.update_idletasks()
    宽 = max(460, 窗.winfo_reqwidth())
    高 = 窗.winfo_reqheight()
    try:
        parent.update_idletasks()
        rx, ry = parent.winfo_rootx(), parent.winfo_rooty()
        rw, rh = parent.winfo_width(), parent.winfo_height()
        x = rx + max(0, (rw - 宽) // 2)
        y = ry + max(0, (rh - 高) // 2)
        窗.geometry("%dx%d+%d+%d" % (宽, 高, x, y))
    except Exception:
        窗.geometry("%dx%d+200+160" % (宽, 高))

    窗.deiconify()
    _设圆角(窗)
    窗.transient(parent)
    try:
        窗.grab_set()
        窗.focus_force()
    except Exception:
        pass
    窗.wait_window()
    return 结果["值"], 结果["记住"]

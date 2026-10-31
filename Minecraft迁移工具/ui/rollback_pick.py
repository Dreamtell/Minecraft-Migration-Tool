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
                "边": "#3a3a3a", "要": "#4da3f0"}
    return {"卡": "#f7f7f7", "底": "#ffffff", "字": "#1f1f1f", "弱": "#666666",
            "边": "#dcdcdc", "要": "#2f7fd1"}


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

    变量们 = {}
    for 键 in 可选:
        名, 说明 = _PART_LABELS[键]
        数 = int(有无.get(键) or 0)
        文 = trp(名) + (trp("（{0} 项）", 数) if 数 else "")
        变量 = tk.BooleanVar(value=键 in 预选)
        变量们[键] = 变量
        行 = tk.Frame(壳, bg=色["卡"])
        行.pack(fill="x", padx=14, pady=1)
        cb = tk.Checkbutton(行, text=文, variable=变量, bg=色["卡"], fg=色["字"],
                            activebackground=色["卡"], activeforeground=色["要"],
                            selectcolor=色["底"], font=("微软雅黑", 9),
                            anchor="w", bd=0, highlightthickness=0)
        cb.pack(side="left")
        tk.Label(行, text="　" + trp(说明), bg=色["卡"], fg=色["弱"],
                 font=("微软雅黑", 8)).pack(side="left")

    记住变量 = tk.BooleanVar(value=bool(记住))
    记住行 = tk.Frame(壳, bg=色["卡"])
    记住行.pack(fill="x", padx=14, pady=(6, 2))
    tk.Checkbutton(记住行, text=trp("记住我的选择（以后回滚都按这个，可在设置里改）"),
                   variable=记住变量, bg=色["卡"], fg=色["字"],
                   activebackground=色["卡"], activeforeground=色["要"],
                   selectcolor=色["底"], font=("微软雅黑", 9),
                   bd=0, highlightthickness=0).pack(side="left")

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

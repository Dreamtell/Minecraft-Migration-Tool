# ui/lang_picker.py
"""首次启动时的语言选择窗口。

为什么要有它：界面语言必须在**建任何窗口之前**定下来（见 utils/i18n.py 顶部），
而第一次使用的人还没有配置文件 —— 那就先问一句再往下走。

几个刻意的选择：
· **标题和说明都是中英双语**写死的 —— 这时候还不知道用户要哪种语言，用任何一种
  都是赌；两种并排显示最省事。
· 两个按钮各自用**自己的语言**（"中文" / "English"），不用翻译。
· 配色也是写死的深色：主题配置这时候还没读到（同一个配置里），先给个好看的中性色。
· 关窗口（X / Esc）= 选中文（项目的默认语言），不阻断启动。
"""

import tkinter as tk

from utils.helpers import create_gradient_button, set_window_icon

# 启动早期还没有主题配置，用一套固定配色（和项目的深色主题观感一致）
_BG = "#2e2e2e"
_FG = "#f0f0f0"
_MUTED = "#a0a0a0"

ZH = "zh"
EN = "en"


def ask_language(parent=None, icon_path=None):
    """弹一个模态小窗问语言，返回 "zh" 或 "en"。关掉窗口 = "zh"。"""
    根 = parent
    自己建的 = False
    if 根 is None:
        根 = tk.Tk()
        根.withdraw()
        自己建的 = True

    窗 = tk.Toplevel(根)
    窗.withdraw()
    窗.title("选择语言 / Choose language")
    窗.configure(bg=_BG)
    窗.resizable(False, False)
    if icon_path:
        try:
            set_window_icon(窗, icon_path)
        except Exception:
            pass
    选 = {"v": ZH}

    def 定了(值):
        选["v"] = 值
        try:
            窗.grab_release()
        except Exception:
            pass
        窗.destroy()

    tk.Label(窗, text="🌐  选择界面语言 / Choose your language",
             bg=_BG, fg=_FG, font=("微软雅黑", 13, "bold")).pack(padx=28, pady=(22, 2))
    tk.Label(窗, text="以后可以随时在「设置 → 🎨 外观与启动」里改",
             bg=_BG, fg=_MUTED, font=("微软雅黑", 9)).pack()
    tk.Label(窗, text="You can change this later in Settings → Appearance.",
             bg=_BG, fg=_MUTED, font=("微软雅黑", 9)).pack(pady=(0, 16))

    条 = tk.Frame(窗, bg=_BG)
    条.pack(padx=28, pady=(0, 22))
    中文按钮 = create_gradient_button(
        条, "中文", lambda: 定了(ZH), colors=("#00bcd4", "#3f51b5"),
        width=150, height=44, font=("微软雅黑", 12, "bold"))
    中文按钮.pack(side="left", padx=(0, 12))
    英文按钮 = create_gradient_button(
        条, "English", lambda: 定了(EN), colors=("#00c853", "#00e676"),
        width=150, height=44, font=("微软雅黑", 12, "bold"))
    英文按钮.pack(side="left")

    窗.protocol("WM_DELETE_WINDOW", lambda: 定了(ZH))
    窗.bind("<Escape>", lambda e: 定了(ZH))
    窗.bind("<Return>", lambda e: 定了(选["v"]))

    窗.update_idletasks()
    宽, 高 = 窗.winfo_reqwidth(), 窗.winfo_reqheight()
    屏宽, 屏高 = 窗.winfo_screenwidth(), 窗.winfo_screenheight()
    窗.geometry("%dx%d+%d+%d" % (宽, 高, (屏宽 - 宽) // 2, max(0, (屏高 - 高) // 2 - 60)))
    窗.deiconify()
    try:
        窗.transient(根)
    except Exception:
        pass
    窗.grab_set()
    窗.lift()
    try:
        窗.focus_force()
    except Exception:
        pass
    try:
        根.wait_window(窗)
    except Exception:
        pass
    if 自己建的:
        try:
            根.destroy()
        except Exception:
            pass
    return 选["v"]

# ui/pack_warning.py
"""整合包更新提醒：三个清单页共用一条红色警示条，只能收起、不能关。

为什么必须常驻：「扫描差异」只比对两件事 —— 清单里写的文件名、目标目录里实际存在的
文件。作者更新整合包时如果悄悄删掉或替换某个模组，我们的清单里本来就没有它，扫出来
是「没有差异」，实际却少了东西。所以这条提醒不提供「不再提示」，最多收成一个 ⚠ 图标。

为什么自己管配色：通用主题迁移（utils/theme.py 的 apply_theme_to_widget_tree）会把
Frame / Label 刷成主题底色，红色警示条会被刷没。这里挂 _skip_theme_walk 挡住整棵子树，
改由 set_theme() 整套重建。
"""
import tkinter as tk

from utils.dpi import px
from utils.helpers import create_gradient_button, is_dark_theme, set_window_icon

标题 = "整合包更新提醒"

横幅文案 = (
    "整合包更新时，作者可能会删除或替换模组 —— 这种情况下「扫描差异」只能比出文件名和"
    "版本的不同，被作者删掉的模组是发现不了的。\n"
    "请务必核对更新日志里的模组变更；也可以把更新日志交给 AI 帮你逐条分析。")

完整说明 = (
    "「扫描差异」只比对两件事：清单里写的文件名，和目标目录里实际存在的文件。\n\n"
    "作者更新整合包时如果删掉或替换了某个模组，我们的清单里本来就没有它 —— "
    "扫出来是「没有差异」，实际却少了东西。\n\n"
    "建议的做法：\n"
    "① 打开整合包的更新日志，逐条核对模组变更；\n"
    "② 或者点下面的「复制 AI 提示词」，把提示词连同更新日志一起交给 AI 逐条分析。")

提示词 = (
    "我在把一个 Minecraft 整合包从旧版本迁移到新版本，需要你帮我从下面的"
    "更新日志里找出【与模组相关的变更】。\n\n"
    "整合包：%s\n旧实例目录：%s\n新实例目录：%s\n\n"
    "请按这个格式输出：\n"
    "1. 新增的模组（尽量给出 modid 或中文名）\n"
    "2. 移除的模组\n"
    "3. 被替换/合并的模组（A 换成了 B）\n"
    "4. 依赖或前置发生变化的模组\n"
    "5. 需要我手动处理的注意点（配置重置、存档兼容性、存档版本）\n\n"
    "更新日志如下：\n（把更新日志粘贴在这里）\n")


class PackWarning(tk.Frame):
    """红色警示条 / 收起后的 ⚠ 图标按钮。两个形态都在这个 Frame 里重画。"""

    def __init__(self, parent, root, theme, *, 取路径, 已收起=False,
                 收起回调=None, 形态回调=None):
        super().__init__(parent, bg=theme.get("bg", "#ffffff"))
        self.应用根 = root
        self.theme = theme
        self._取路径 = 取路径
        self._收起回调 = 收起回调
        self._形态回调 = 形态回调
        self.已收起 = bool(已收起)
        self._弹窗 = None
        # 红色是"警示语义色"，不是主题色：不让通用主题迁移碰这棵子树
        self._skip_theme_walk = True
        self._重画()

    # ---------- 配色 ----------
    def _配色(self):
        try:
            深 = is_dark_theme(self.theme)
        except Exception:
            深 = str(self.theme.get("bg", "")).lower() in (
                "#2e2e2e", "#1e1e1e", "#252525", "#202020", "#2b2b2b")
        if 深:
            return {"底": "#4d1517", "边": "#f0545a", "字": "#ffd9d9",
                    "图": "#ff6b70", "主": "#ff9c9c", "弱": "#c89595", "悬": "#5f1b1e"}
        # 浅色主题：原来底 #fde5e1 太淡，深色主题那条是「暗红底」，浅色这边要
        # 有同样的分量感 —— 底色压深一档，悬停跟着压深，文字对比度反而更好。
        return {"底": "#f7cfc7", "边": "#b71c1c", "字": "#5d0f0b",
                "图": "#b71c1c", "主": "#a51e18", "弱": "#8a5a55", "悬": "#f1bdb2"}

    def set_theme(self, theme):
        """主题切换：整套颜色重出图（本控件挡住了通用主题迁移）。"""
        self.theme = theme
        self._重画()

    # ---------- 两个形态 ----------
    def _重画(self):
        for 子 in self.winfo_children():
            try:
                子.destroy()
            except Exception:
                pass
        try:
            self.configure(bg=self.theme.get("bg", "#ffffff"))
        except Exception:
            pass
        if not self.已收起:
            self._建横幅()
        # 收起时自己不画东西：交给形态回调把整个横幅摘掉，改成标签行右端的 ⚠ 药丸
        if self._形态回调:
            try:
                self._形态回调(self.已收起)
            except Exception:
                pass

    def _建横幅(self):
        坏 = self._配色()
        框 = tk.Frame(self, bg=坏["底"], highlightthickness=1,
                      highlightbackground=坏["边"], highlightcolor=坏["边"])
        框.pack(fill="x")
        tk.Frame(框, bg=坏["边"], width=px(4)).pack(side="left", fill="y")
        tk.Label(框, text="⚠", font=("微软雅黑", 14), bg=坏["底"], fg=坏["图"]).pack(
            side="left", padx=(px(12), px(10)), anchor="n", pady=px(9))
        tk.Label(框, text=横幅文案, justify="left", anchor="w", bg=坏["底"], fg=坏["字"],
                 font=("微软雅黑", 9), wraplength=px(560)).pack(
            side="left", fill="x", expand=True, pady=px(8))
        右 = tk.Frame(框, bg=坏["底"])
        右.pack(side="right", padx=(px(8), px(10)))
        self._建链接(右, 坏, "复制 AI 提示词", self._复制)
        self._建链接(右, 坏, "收起 ▴", lambda: self._设收起(True), 弱=True)

    def _建链接(self, 父, 坏, 文字, 命令, 弱=False):
        标 = tk.Label(父, text=文字, bg=坏["底"], fg=坏["弱"] if 弱 else 坏["主"],
                     cursor="hand2", font=("微软雅黑", 9, "underline"),
                     padx=px(4), pady=px(2), bd=0, highlightthickness=0)
        标.pack(side="left", padx=(0, px(4)))
        标.bind("<Button-1>", lambda e: 命令())
        标.bind("<Enter>", lambda e: 标.configure(bg=坏["悬"]))
        标.bind("<Leave>", lambda e: 标.configure(bg=坏["底"]))
        return 标

    def _设收起(self, 收起):
        self.已收起 = bool(收起)
        self._重画()
        if self._收起回调:
            try:
                self._收起回调(self.已收起)
            except Exception:
                pass

    # ---------- 复制提示词 ----------
    def _复制(self):
        try:
            源, 标路径 = self._取路径()
        except Exception:
            源 = 标路径 = ""
        名 = (源 or "").replace("\\", "/").rstrip("/").split("/")[-1] or "整合包"
        文 = 提示词 % (名, 源 or "（未选择）", 标路径 or "（未选择）")
        try:
            self.应用根.clipboard_clear()
            self.应用根.clipboard_append(文)
            self.应用根.update_idletasks()
        except Exception:
            return
        self._复制反馈()

    def _复制反馈(self):
        坏 = self._配色()

        def _写():
            for 子 in self.winfo_children():
                for 孙 in getattr(子, "winfo_children", list)():
                    if isinstance(孙, tk.Label) and str(孙.cget("text")).startswith("复制 AI 提示词"):
                        try:
                            孙.config(text="已复制 ✓", fg=坏["边"])
                            self.after(1600, lambda: 孙.config(text="复制 AI 提示词", fg=坏["主"]))
                        except Exception:
                            pass
        try:
            _写()
        except Exception:
            pass

    # ---------- 完整说明弹窗 ----------
    def _开弹窗(self):
        旧 = self._弹窗
        if 旧 is not None:
            try:
                旧.lift()
                旧.focus_force()
                return
            except Exception:
                self._弹窗 = None
        坏 = self._配色()
        th = self.theme
        窗 = tk.Toplevel(self.应用根)
        窗.title(标题)
        窗.configure(bg=th.get("bg", "#ffffff"))
        # 不设图标的话这个窗口显示的是 Tk 默认的羽毛图标（用户报「ico 异常」）；
        # 主界面和别的弹窗都设了，漏的是这一处。
        set_window_icon(窗)
        try:
            窗.resizable(False, False)
            窗.transient(self.应用根)
        except Exception:
            pass
        self._弹窗 = 窗

        def _关():
            self._弹窗 = None
            try:
                窗.grab_release()
            except Exception:
                pass
            try:
                窗.destroy()
            except Exception:
                pass

        窗.protocol("WM_DELETE_WINDOW", _关)
        窗.bind("<Escape>", lambda e: _关())

        头 = tk.Frame(窗, bg=坏["底"], highlightthickness=1, highlightbackground=坏["边"])
        头.pack(fill="x")
        行 = tk.Frame(头, bg=坏["底"])
        行.pack(fill="x", padx=px(14), pady=px(10))
        tk.Label(行, text="⚠", font=("微软雅黑", 18), bg=坏["底"], fg=坏["图"]).pack(
            side="left", padx=(0, px(10)))
        字 = tk.Frame(行, bg=坏["底"])
        字.pack(side="left", anchor="w")
        tk.Label(字, text=标题, bg=坏["底"], fg=坏["字"],
                 font=("微软雅黑", 11, "bold")).pack(anchor="w")
        tk.Label(字, text="这条提醒不能关闭，只能收起成一个 ⚠ 图标",
                 bg=坏["底"], fg=坏["弱"], font=("微软雅黑", 8)).pack(anchor="w")

        tk.Label(窗, text=完整说明, justify="left", anchor="w", bg=th.get("bg"),
                 fg=th.get("fg", "#222222"), font=("微软雅黑", 9),
                 wraplength=px(520)).pack(fill="x", padx=px(18), pady=(px(14), px(10)))

        底 = tk.Frame(窗, bg=th.get("bg"))
        底.pack(fill="x", padx=px(18), pady=(0, px(14)))
        create_gradient_button(底, "复制 AI 提示词", self._复制,
                               width=px(150), height=px(30)).pack(side="left")
        if self.已收起:
            create_gradient_button(底, "展开成横幅", self._展开,
                                   colors=("#e5484d", "#b3261e"),
                                   width=px(120), height=px(30)).pack(
                side="left", padx=(px(8), 0))
        create_gradient_button(底, "关闭", _关, width=px(90),
                               height=px(30)).pack(side="right")

        try:
            窗.update_idletasks()
            rx, ry = self.应用根.winfo_rootx(), self.应用根.winfo_rooty()
            rw, rh = self.应用根.winfo_width(), self.应用根.winfo_height()
            w, h = 窗.winfo_reqwidth(), 窗.winfo_reqheight()
            窗.geometry("+%d+%d" % (rx + max(0, (rw - w) // 2), ry + max(0, (rh - h) // 3)))
        except Exception:
            pass
        try:
            窗.grab_set()
            窗.focus_force()
        except Exception:
            pass

    def _展开(self):
        旧 = self._弹窗
        if 旧 is not None:
            self._弹窗 = None
            try:
                旧.grab_release()
                旧.destroy()
            except Exception:
                pass
        self._设收起(False)

# ui/mw_edit.py
"""编辑模式：编辑锁、空白处点击、失焦、文本框状态。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import tkinter as tk
from utils.helpers import trace_exc
# 文案模板：trp 按位置填值
from utils.i18n import trp


class EditMixin:
    """编辑模式：编辑锁、空白处点击、失焦、文本框状态。"""

    # ---------- 清单窗口开着时，锁住"主界面编辑" ----------
    # 哪些窗口算"照着清单显示"的：放大查看 / 模组差异（Qt 子进程版和 Tk 回退版都算）。
    # 执行日志的放大查看不在内 —— 它看的是日志，不碰清单。
    _MIRROR_EDIT_NAMES = {"bigview": "放大查看", "diff": "模组差异"}

    def _edit_locked(self):
        return bool(getattr(self, "_edit_locks", None))

    def _edit_enabled_for_config(self):
        """写进配置的「主界面编辑」= **用户的意图**，不是被锁强制关掉的那个值。

        放大查看 / 模组差异开着时，`edit_mode` 是被我们强制设成 False 的（窗口里冻结的
        是它们那份清单快照）。如果这时候正好保存了配置（改任何设置、拖文件、开迁移都会
        存），或者程序被强杀，写下去的就是这个 False —— 下次启动编辑莫名其妙是关的，
        用户看到的就是"原本是打开着的，怎么没恢复"。所以锁着时按"锁之前的意愿"写。
        """
        if self._edit_locked():
            return bool(getattr(self, "_edit_before_lock", False))
        return bool(self.edit_mode.get())

    def _lock_edit(self, kind):
        """放大查看 / 模组差异打开：把「主界面编辑」关掉并锁住，窗口全关了再还回去。

        为什么必须锁：这两个窗口手里是**打开那一刻的清单快照**，而且会往回写（增删同步）。
        主界面同时还在编辑，就是两边同时改同一份清单 —— 之前那个"主界面编辑的内容被
        写回盖掉"的 bug 正是从这儿来的。锁住期间：三个清单文本框只读、开关灰掉点不动、
        说明文字写清是哪个窗口拦着。
        """
        if kind not in self._MIRROR_EDIT_NAMES:
            return
        锁 = getattr(self, "_edit_locks", None)
        if 锁 is None:
            self._edit_locks = 锁 = {}
        if kind in 锁:
            return
        if not 锁:                                  # 第一次锁：记住用户原来的选择
            self._edit_before_lock = bool(self.edit_mode.get())
        锁[kind] = True
        if self.edit_mode.get():
            self.edit_mode.set(False)
            self.log("ℹ️ %s 打开期间，主界面编辑已关闭（清单两边同时改会互相盖）。"
                     % self._MIRROR_EDIT_NAMES[kind], level="INFO", save=False)
        self._sync_edit_lock()
        self._update_text_states()

    def _unlock_edit(self, kind):
        """窗口关掉了：解锁；最后一个也关了，就把编辑状态还回用户原来的选择。"""
        锁 = getattr(self, "_edit_locks", None)
        if not 锁 or kind not in 锁:
            return
        del 锁[kind]
        if 锁:
            self._sync_edit_lock()
            self._update_text_states()
            return
        旧 = bool(getattr(self, "_edit_before_lock", False))
        self._edit_before_lock = None
        if 旧 and not self.edit_mode.get():
            self.edit_mode.set(True)
            self.log("ℹ️ 清单窗口都关了，主界面编辑恢复成「开」。", level="INFO", save=False)
        self._sync_edit_lock()
        self._update_text_states()
        self.save_config()

    def _sync_edit_lock(self):
        """把"锁着"画到编辑开关上：灰掉 + 说明写清原因；解锁还原。"""
        sw = getattr(self, "edit_switch", None)
        if sw is None:
            return
        try:
            锁 = getattr(self, "_edit_locks", None) or {}
            if sw.get() != bool(self.edit_mode.get()):
                sw.set(self.edit_mode.get(), animate=True)     # 被自动关掉时开关也得跟上
            if 锁:
                名 = "、".join(self._MIRROR_EDIT_NAMES.get(k, k) for k in 锁)
                sw.set_text(desc=trp("{0} 开着时不能编辑 · 关掉那个窗口即可恢复", 名))
                sw.set_enabled(False)
            else:
                # 这里显式过一遍语言层：虽然语言层也包了 SwitchRow.set_text（双保险），
                # 但自绘控件的文字不走 tk 的 text=，漏包一次就是中文残留（踩过）
                sw.set_text(desc=trp("直接改动清单文字 · 谨慎使用"))
                sw.set_enabled(True)
        except Exception:
            trace_exc("main_window", "同步编辑锁")

    def _lock_tk_mirror(self, kind, win):
        """Tk 回退版（进程内的放大查看 / 模组差异）同样要锁；窗口销毁时解锁。"""
        if win is None:
            return
        self._lock_edit(kind)

        def 关掉了(ev):
            try:
                if ev.widget is win and not win.winfo_exists():
                    self._unlock_edit(kind)
            except Exception:
                pass

        try:
            win.bind("<Destroy>", 关掉了, add="+")
        except Exception:
            pass

    def _update_text_states(self):
        # 放大查看 / 模组差异开着时**强制只读**：它们手里是清单快照（还会往回写），
        # 两边同时改就是互相盖（见 _lock_edit）
        state = (tk.NORMAL if self.edit_mode.get() and not self._edit_locked()
                 else tk.DISABLED)
        self.mod_text.configure(state=state)
        self.config_text.configure(state=state)
        self.extra_text.configure(state=state)
        self._check_overflow()

    def on_edit_switch(self):
        """开关被点：它只翻了自己的状态，这里把业务变量同步过去再应用。

        （原来那个 Checkbutton 是"先翻 variable 再调 command"，换成自绘开关后
        这一步得自己做，否则 toggle_edit_mode 读到的还是旧值。）
        """
        self.edit_mode.set(self.edit_switch.get())
        self.toggle_edit_mode()

    # "有意图的交互"控件：点它们不算点空白（按钮/输入框/滚动条/勾选框/下拉框…）
    _INTERACTIVE_CLASSES = ("Button", "TButton", "Entry", "TEntry", "TCombobox",
                            "Spinbox", "TSpinbox", "Scale", "Checkbutton",
                            "TCheckbutton", "Radiobutton", "TRadiobutton",
                            "Scrollbar", "TScrollbar", "Menubutton", "TMenubutton",
                            "Listbox", "Treeview", "TNotebook")

    def _on_blank_click(self, event):
        """点击窗口空白处 = 退出主界面编辑模式（等于让清单文本框失焦）。

        编辑模式下改清单不用先去找那个开关，随手点一下背景就回到只读
        （设置 →「🎨 外观与启动」可以关掉这个行为）。

        只有下面这些"点下去有明确用途"的地方不算空白，点了不退出：
        - **三个清单文本框本身** —— 那正是要继续编辑的地方；
        - **清单页签上的药丸**（模组清单 / config 清单 / 其它文件）—— 点它是切页；
          同一栏里药丸之外的那截空条**算空白**，点了会退出编辑；
        - **有意图的交互控件**：按钮、输入框、滚动条、勾选框、下拉框（渐变按钮和
          自绘开关是 Canvas，按标记属性认）。
        清单区里其余的留白（文本框外面那一圈圆角边、页签下方的空白）、背景
        Frame/Label、日志文本框和它周围的底图 —— 统统算空白。
        """
        try:
            if not self.edit_mode.get():
                return
            w = event.widget
            for 框 in (getattr(self, "mod_text", None),
                      getattr(self, "config_text", None),
                      getattr(self, "extra_text", None)):
                if 框 is not None and w is 框:
                    return
            bar = getattr(getattr(self, "list_tabs", None), "bar", None)
            if bar is not None and w is bar:
                # 这一栏：点在**药丸**上是切页（不是点空白）；点在药丸之外的
                # 那截空条上才算空白 —— 该退出编辑、该失焦
                try:
                    if self.list_tabs._hit(event.x) >= 0:
                        return
                except Exception:
                    pass
            if w.winfo_class() in self._INTERACTIVE_CLASSES:
                return
            # 自绘控件：渐变按钮（Canvas + set_command）、开关卡片、圆角输入框
            if getattr(w, "set_command", None) is not None:
                return
            if getattr(w, "_is_switch_row", False) or getattr(w, "_is_rounded_entry", False):
                return
        except Exception:
            return
        # 点空白处一律让清单文本框失焦（"点了别处"该有的反应），
        # 至于要不要连编辑模式一起关，由设置里的开关决定
        if not self.blank_exit_edit.get():
            self._blur_list_texts()
            return
        self.edit_mode.set(False)
        self.toggle_edit_mode()                     # 里面也会失焦 + 清选区

    def _blur_list_texts(self):
        """让三个清单文本框失焦：清掉蓝色选区、焦点收回主窗口（描边跟着灭）。

        焦点收回主窗口，三个文本框各收到一次 FocusOut；再显式把圆角框的聚焦态
        复一遍，免得个别情况下 FocusOut 没到、那圈亮蓝描边还亮着。
        """
        for 框 in (getattr(self, "mod_text", None), getattr(self, "config_text", None),
                  getattr(self, "extra_text", None)):
            if 框 is None:
                continue
            try:
                框.tag_remove("sel", "1.0", "end")   # tag 操作不受 disabled 限制
            except Exception:
                pass
        try:
            self.root.focus_set()               # 键盘焦点收回主窗口
        except Exception:
            pass
        for box in (getattr(self, "mod_text_box", None),
                    getattr(self, "config_text_box", None),
                    getattr(self, "extra_text_box", None)):
            try:
                box._set_focus(False)           # 兜底复一遍描边状态
            except Exception:
                pass

    def toggle_edit_mode(self):
        self._update_text_states()
        # 开关卡片跟着状态走（从别处改 edit_mode 时也同步；值一致就别重启动画）
        try:
            if self.edit_switch.get() != self.edit_mode.get():
                self.edit_switch.set(self.edit_mode.get(), animate=True)
        except Exception:
            pass
        if self.edit_mode.get():
            self.log("⚠️ 警告：已启用主界面编辑模式，直接修改清单可能导致数据错误，请谨慎操作！", level="WARNING")
        else:
            self._blur_list_texts()
            self.log("ℹ️ 主界面编辑模式已关闭，清单恢复只读。", level="INFO")
        self.save_config()

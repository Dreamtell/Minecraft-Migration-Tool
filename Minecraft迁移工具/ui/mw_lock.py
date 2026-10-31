# ui/mw_lock.py
"""迁移期间的界面保护：按钮禁用 + 输入只读。

**锁屏遮罩已经整体删掉了**（2026-10-31，用户：「锁屏这个选择不要了，这样好得很」）。
原来这里有一套流动边框遮罩 + 内嵌执行日志 + 完成态，用户在 2c6cf65 之后确认
"按钮全禁用 + 进度窗口 + 执行日志"就够了，遮罩反而在真复制时把主线程压出
卡顿。现在没有开关、没有遮罩代码，也就不会再有人把它打开。

对外仍然保留三个入口（ui/mw_migration.py 里还有调用点），行为一律是空：
_lock_main_window / _finish_migration_lock / _unlock_main_window。
真正干活的只有两个安全的：
  - _refresh_busy_state：把所有操作类按钮置灰/恢复（含标签栏锁、开始迁移的 inert）
  - _set_edit_lock：路径框 / 存档名 / 开关 / 清单文本框只读/恢复
"""
import tkinter as tk

from utils.helpers import trace_exc
from utils.i18n import trp


class LockMixin:
    """迁移期间的界面保护（锁屏遮罩已移除，只剩按钮禁用与输入只读）。"""

    # ---------- 锁屏的三个旧入口：留空，只为外面还有调用点 ----------
    def _lock_main_window(self, text="正在执行迁移任务"):
        """（已移除）原来在这里盖流动边框遮罩 + 内嵌日志。"""
        self._刷新忙态后记一笔()

    def _finish_migration_lock(self):
        """（已移除）原来在这里切完成态、等用户按键。"""
        self._mig_finish_pending = False
        try:
            self._refresh_busy_state()
        except Exception as e:
            trace_exc("main_window/收尾刷新", e)

    def _unlock_main_window(self):
        """（已移除）原来在这里撤遮罩。解锁不需要做什么，按钮状态由忙态决定。"""
        try:
            self._refresh_busy_state()
        except Exception as e:
            trace_exc("main_window/解锁刷新", e)

    def _刷新忙态后记一笔(self):
        """第一次进入迁移时在日志里留一句，免得看代码的人以为锁屏是坏了。"""
        if getattr(self, "_锁屏说明已记", False):
            return
        self._锁屏说明已记 = True
        try:
            self.log(trp("🔒 迁移期间主界面按钮全部禁用（锁屏遮罩已移除）"), "INFO")
        except Exception as e:
            trace_exc("main_window/锁屏说明", e)
        try:
            self._refresh_busy_state()
        except Exception as e:
            trace_exc("main_window/锁屏说明刷新", e)

    def _set_edit_lock(self, locked):
        """把"现在不能改东西"落到输入控件上：路径框 / 存档名 / 两个开关 / 清单文本框。

        只管"能不能改"，不管"能不能点"——按钮的禁用由 _refresh_busy_state 负责。
        解锁时交给 _sync_edit_lock 决定编辑开关该不该恢复（镜像窗口还开着就不能恢复）。
        """
        locked = bool(locked)
        if getattr(self, "_ui_locked", None) == locked:
            return
        self._ui_locked = locked
        state = "disabled" if locked else "normal"
        for 名 in ("source_entry", "target_entry", "world_entry"):
            框 = getattr(self, 名, None)
            try:
                if 框 is not None:
                    # RoundedEntry 是 Frame + 内层 tk.Entry：state 只有内层认
                    # （直接对 Frame configure(state=...) 会抛 unknown option -state，
                    #  被 except 吞掉 → 看着"锁了"，其实没锁）。
                    内 = getattr(框, "entry", 框)
                    if locked:
                        # tk.Entry 一旦 disabled，Windows 会拿系统的"灰底灰字"来画，
                        # 落在深色圆角框里就是一块亮灰色砖头（用户截图："难看"）。
                        # readonly 也一样走系统色，没用。正解是把 disabled 的两套颜色
                        # 指回它自己的底色/字色 —— 看不出变化，但改不动了。
                        内.configure(disabledbackground=内.cget("bg"),
                                    disabledforeground=内.cget("fg"))
                    内.configure(state=state)
            except Exception as e:
                trace_exc("main_window/锁输入框 %s" % 名, e)
        for 名 in ("dry_run_sw", "overwrite_sw"):
            开 = getattr(self, 名, None)
            try:
                if 开 is not None:
                    开.set_enabled(not locked)
            except Exception as e:
                trace_exc("main_window/锁开关 %s" % 名, e)
        编 = getattr(self, "edit_switch", None)
        try:
            if locked and 编 is not None:
                编.set_enabled(False)
        except Exception as e:
            trace_exc("main_window/锁编辑开关", e)
        try:
            self._update_text_states()
        except Exception as e:
            trace_exc("main_window/刷新文本状态", e)
        if not locked:
            try:
                self._sync_edit_lock()
            except Exception as e:
                trace_exc("main_window/恢复编辑开关", e)

    def _refresh_busy_state(self):
        """迁移/扫描进行中时禁用主界面所有操作/内容变更类按钮，防止连点或误操作。

        注意 `_starting` 也要算"忙"：准备阶段（统计文件那几秒）按钮必须一直是灰的，
        否则收尾那几步会把它当成"没在跑"而重新点亮。
        """
        # 开始前倒计时也算"忙"：那几秒里别的按钮不该能点，
        # 唯一还能点的是「开始迁移」—— 它就是取消键（见下面那段恢复 normal）。
        busy = (bool(self._busy_task_name())
                or bool(getattr(self, "_starting", False))
                or bool(getattr(self, "_countdown_running", False))
                # 迁移跑完、但执行日志还在往下滚：这段也算忙（用户要的"结束标志"
                # 就是日志滚完那一刻），不然按钮先亮、按钮却还在背面。
                or bool(getattr(self, "_ending_wait_log", False)))
        state = "disabled" if busy else "normal"
        # 标签栏**必须一起锁**：切到一个还没建过的页会当场构建整页（377 个模组 =
        # 377 个卡片控件），主线程卡好几秒 —— 用户"点几下标签就弹未响应"就是这么来的。
        # 这里不能只靠遮罩：准备阶段（_starting）那几秒遮罩还没盖上，点击照样能落到标签栏。
        try:
            self.list_tabs.set_locked(busy)
        except Exception:
            pass
        # 两个复选开关（模拟运行 / 覆盖已存在的模组）由下面的 switch 树兜底处理
        btns = (
            # 「开始迁移」不在这里：迁移进行中它要保持活的样子（红背面 + 悬停特效
            # + 「⏳ 迁移中...」），由下面单独处理成 normal + inert。
            # 「回滚」按钮已并入迁移历史窗口，主界面没有它了（现在回滚只能在历史窗里点，
            # 那时窗口本身是模态的，用户点不到主界面）。
            getattr(self, "rollback_btn", None),
            # 「查看历史」原来漏在名单外：迁移/倒计时期间它一直亮着（用户要求全部禁用）
            getattr(self, "history_btn", None), self.scan_btn,
            self.btn_changelog, self.mod_magnify_btn, self.add_mods_btn,
            self.clear_mods_btn, self.check_mods_btn, self.config_magnify_btn,
            self.add_config_dir_btn, self.add_config_file_btn, self.clear_config_btn,
            self.config_check_btn,
            # 路径区那三个：迁移期间改来源/目标路径同样是误操作，一起禁掉
            self._btn_widgets.get("browse_source"), self._btn_widgets.get("copy_target"),
            self._btn_widgets.get("browse_target"),
            # 右上角的设置/主题
            getattr(self, "settings_btn", None), getattr(self, "theme_btn", None),
            # 日志区那三个（遮罩不盖日志，这里靠禁用挡住）
            getattr(self, "log_magnify_btn", None),
            self._btn_widgets.get("log_open"), self._btn_widgets.get("log_clear"),
        )
        for btn in btns:
            if btn is None:
                continue
            try:
                btn.state(state)
            except Exception:
                pass
        # 按钮之外，输入类也要跟着锁：路径框、存档名、两个开关、清单文本框。
        # 倒计时那几秒用户完全可能正在改路径或往清单里粘贴，不锁就会带着改动开跑。
        try:
            self._set_edit_lock(busy)
        except Exception as e:
            trace_exc("main_window/锁定输入", e)
        # 「开始迁移」单独处理：
        #   - 倒计时期间点亮：再点一下就是取消（start_migration 里判断）；
        #   - 迁移进行中（含准备阶段）也保持 normal：用户要求按钮保持特效（红背面 +
        #     悬停 + 「⏳ 迁移中...」），但用 set_inert 把点击吞掉，所以点不动；
        #   - 其它忙（扫描/文件任务）照旧禁用。
        迁移中 = bool(getattr(self, "_migration_running", False)) or bool(
            getattr(self, "_starting", False))
        try:
            if 迁移中 or getattr(self, "_countdown_running", False):
                self.start_btn.state("normal")
                self.start_btn.set_inert(迁移中)
            else:
                self.start_btn.set_inert(False)
                self.start_btn.state(state)
        except Exception as e:
            trace_exc("main_window/开始迁移按钮状态", e)

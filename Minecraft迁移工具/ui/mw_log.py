# ui/mw_log.py
"""执行日志：写日志、日志配色、双击定位、失败标记、日志滚动。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from utils import anim
from pathlib import Path
from tkinter import messagebox
from utils import i18n, secrets
from utils.textsafe import 可见 as _可见文本
from utils.helpers import SmoothScroller, tree_row_px, trace_exc
from utils.session_log import SessionLog
# 日志/文案模板：trp 按位置填值（中文模式下与原 f-string 逐字一致）
from utils.i18n import trp



class LogMixin:
    """执行日志：写日志、日志配色、双击定位、失败标记、日志滚动。"""

    # 日志分类文字色 -> 主题键
    # PLAIN = 红字但不铺底：给"检查结果汇总"这种一行统计用（❌ 缺失的模组：3），
    # 它本身不是故障，铺一坨红底只会让一屏里到处都是高亮
    _LOG_COLOR_KEYS = {
        "INFO": "log_info_fg",
        "WARNING": "log_warning_fg",
        "ERROR": "log_error_fg",
        "SUCCESS": "log_success_fg",
        "SIMULATE": "log_simulate_fg",
        "PLAIN": "log_error_fg",
    }
    _LOG_TAGS = tuple(_LOG_COLOR_KEYS)

    def _mark_failed_from_log(self, 文本):
        """日志里报错时，去三个清单里找出被提到的条目：标红 + 记进失败列表。

        和双击定位同一个思路 —— 不解析日志格式，拿清单条目名反查，谁被提到谁就是出错的。
        """
        if not 文本:
            return
        for 索引, 框, 页名 in ((0, self.mod_text, trp("模组清单")),
                             (1, self.config_text, trp("config 清单")),
                             (2, self.extra_text, trp("其它文件"))):
            try:
                条目 = 框.get("1.0", "end-1c").splitlines()
            except Exception:
                continue
            for i, 条 in enumerate(条目, start=1):
                名 = Path(条.strip()).name
                if not 名 or 名 not in 文本:
                    continue
                try:
                    框.tag_add("migrate_fail", "%d.0" % i, "%d.end" % i)
                except Exception:
                    pass
                if all(名 != x[1] for x in self._failed_items):
                    self._failed_items.append((索引, 名, 页名))
                    self._refresh_fail_button()
                return

    def _clear_fail_marks(self):
        """清掉上一次留下的"出错标红"（每次开始新迁移时调）。"""
        self._failed_items = []
        self._fail_cursor = 0
        for 框 in (self.mod_text, self.config_text, self.extra_text):
            try:
                框.tag_remove("migrate_fail", "1.0", "end")
            except Exception:
                pass
        self._refresh_fail_button()

    def _refresh_fail_button(self):
        """刷新「📍 定位错误」：文字带上错误条数，**没有错误就置灰点不动**。

        以前没错误时按钮还是彩色的，点一下只弹一句"这次没有出错的条目"——
        不如直接禁用，一眼就知道现在无可定位的东西。
        """
        n = len(self._failed_items)
        try:
            self.btn_fail_locate.set_text("📍 定位错误" + (" (%d)" % n if n else ""))
        except Exception:
            pass
        try:
            # 顺序要紧：set_state 禁用时会按当前文字重出一遍灰面图，
            # 所以先把文字改好再切状态
            self.btn_fail_locate.state("normal" if n else "disabled")
        except Exception:
            pass

    def _goto_next_fail(self):
        """在清单里循环跳到下一个出错的条目（比翻日志、双击都快）。"""
        if not self._failed_items:
            # 不弹窗（用户嫌烦），往日志里说一句就够了
            self.log("ℹ️ 这次没有出错的条目", level="INFO", save=False)
            return
        索引, 名, 页名 = self._failed_items[self._fail_cursor % len(self._failed_items)]
        self._fail_cursor = (self._fail_cursor + 1) % len(self._failed_items)
        框 = (self.mod_text, self.config_text, self.extra_text)[索引]
        try:
            条目 = 框.get("1.0", "end-1c").splitlines()
        except Exception:
            条目 = []
        for i, 条 in enumerate(条目, start=1):
            if Path(条.strip()).name == 名:
                self._goto_list_line(索引, 框, i, 名, 页名)
                return
        self.log(trp("ℹ️ 「{0}」已经不在清单里了", 名), level="INFO", save=False)

    def _bind_log_locate(self, widget):
        """给日志控件挂上"双击哪一行，就跳到那一条"（主日志和放大日志都用它）。"""
        try:
            widget.bind("<Double-Button-1>",
                        lambda e, w=widget: self._locate_from_log(w, e), add="+")
        except Exception:
            pass

    def _locate_from_log(self, widget, event=None):
        """把日志里这一行提到的清单条目定位出来：切到对应页签、选中并滚到可见。

        不去解析日志格式（"❌ 复制失败: xxx.jar（原因）"这种写法太容易变），而是拿清单里
        的条目名去这一行里找 —— 谁被提到，谁就是"出错的位置"。
        """
        try:
            if event is not None:
                行 = widget.index("@%d,%d" % (event.x, event.y))
            else:
                行 = widget.index("insert")
            行号 = int(行.split(".")[0])
            文本 = widget.get("%d.0" % 行号, "%d.end" % 行号).strip()
            # 只有错误/警告行才响应双击：普通信息/成功行点了也没意义，
            # 以前会给它们弹一句"没提到清单条目"，纯属骚扰
            标签 = list(widget.tag_names("%d.0" % 行号) or [])
        except Exception:
            return
        if "ERROR" not in 标签 and "WARNING" not in 标签:
            return
        if not 文本:
            return
        for 索引, 框, 页名 in ((0, self.mod_text, trp("模组清单")),
                             (1, self.config_text, trp("config 清单")),
                             (2, self.extra_text, trp("其它文件"))):
            try:
                条目 = 框.get("1.0", "end-1c").splitlines()
            except Exception:
                continue
            for i, 条 in enumerate(条目, start=1):
                名 = Path(条.strip()).name
                if 名 and 名 in 文本:
                    self._goto_list_line(索引, 框, i, 名, 页名)
                    return
        # 错误行里没提到清单条目：只在日志里记一句，不弹窗打扰
        self.log("ℹ️ 这一行没提到清单中的条目，无法定位", level="INFO", save=False)

    def _blink_locate(self, 框, 行号, 次数=3):
        """让定位到的那一行闪几下再收干净（比一直亮着更抓眼，也不会留个高亮在那儿）。"""
        def 一下(i):
            try:
                if i % 2 == 0:
                    框.tag_add("locate", "%d.0" % 行号, "%d.end" % 行号)
                else:
                    框.tag_remove("locate", "1.0", "end")
            except Exception:
                return
            if i < 次数 * 2 - 1:
                self.root.after(240, lambda: 一下(i + 1))
            else:
                try:
                    框.tag_remove("locate", "1.0", "end")
                except Exception:
                    pass
        一下(0)

    def _goto_list_line(self, 页索引, 框, 行号, 名, 页名):
        """切到那一页、把这一行选中 + 滚到可见，并闪一下高亮。"""
        try:
            self.list_tabs.select(页索引, animate=False)
        except Exception:
            pass
        只读 = str(框.cget("state")) == "disabled"
        try:
            if 只读:                      # 只读的 Text 没法选中，临时放开一下
                框.configure(state=tk.NORMAL)
            框.tag_remove("locate", "1.0", "end")
            框.tag_configure("locate",
                             background=self.theme.get("card_sel_bg", "#d4e6f8"),
                             foreground=self.theme.get("card_sel_fg", "#0d3d63"))
            框.tag_add("locate", "%d.0" % 行号, "%d.end" % 行号)
            框.mark_set("insert", "%d.0" % 行号)
            框.see("%d.0" % 行号)
            框.focus_set()
            self._blink_locate(框, 行号)     # 闪几下比一直亮着更抓眼，也不会留痕迹
        except Exception:
            pass
        finally:
            if 只读:
                try:
                    框.configure(state=tk.DISABLED)
                except Exception:
                    pass
        self.log(trp("📍 已定位到「{0}」（{1} 第 {2} 行）", 名, 页名, 行号), level="INFO", save=False)

    def init_log_colors(self):
        """按当前主题设置日志分类颜色（INFO/警告/错误/成功/模拟）。"""
        self._configure_log_colors(self.log_text)

    def _configure_log_colors(self, widget):
        """把日志分类色应用到指定控件的 tag 上（跟随主题，主日志与放大日志共用）。"""
        default = {"INFO": "gray", "WARNING": "orange", "ERROR": "red",
                   "SUCCESS": "green", "SIMULATE": "blue"}
        for tag, key in self._LOG_COLOR_KEYS.items():
            color = self.theme.get(key, default.get(tag, "gray"))
            try:
                widget.tag_config(tag, foreground=color)
            except Exception:
                pass
        # 只有错误行给底色：以前连警告也给（免责声明、跳过提示那种），
        # 一屏里黄一块红一块，真正要看的反而看不出来。
        # 汇总行走 PLAIN（红字无底），免得"❌ 缺失的模组：3"这种统计也铺一坨红
        try:
            widget.tag_config("ERROR", background=self.theme.get("danger_bg", ""))
        except Exception:
            pass

    # ---------- 日志 ----------
    # 明显无用的提示（纯 UI 反馈，不算操作记录），保存到日志文件时过滤掉；屏幕仍会显示。
    _LOG_TRIVIAL = (
        "已将目标路径复制到源路径",
        "目标路径为空，无法复制",
        "主题已切换为",
        "路径验证通过",
        "ℹ️ 原生对话框不可用",
        "📋 日志已清空",
    )

    def _fade_text(self, widget, ranges, from_color, to_color, frames=12, frame_ms=20,
                   indent_from=None, indent_to=None, on_done=None):
        """给若干段文字做动画（ranges = [(起, 止), …] 的 Text 索引对）。

        两个可插值的量：
        - foreground：底色 ↔ 目标色，看着就是淡入/淡出；
        - lmargin1/lmargin2：左边距，看着就是"滑进来/滑出去"。
        实测**位移比变色显眼得多**，所以两样一起做；只改颜色 6 帧 72ms 那版基本看不出来。
        动画跑完把临时 tag 撤掉，让原本的着色（如日志级别色）重新生效。
        """
        ranges = [(a, b) for a, b in (ranges or []) if a and b]
        if not ranges or (str(from_color) == str(to_color) and indent_from is None):
            if on_done:
                on_done()
            return
        self._fade_seq = getattr(self, "_fade_seq", 0) + 1
        tag = "fadeanim%d" % self._fade_seq
        try:
            widget.tag_configure(tag, foreground=from_color)
            if indent_from is not None:
                widget.tag_configure(tag, lmargin1=int(indent_from),
                                     lmargin2=int(indent_from))
            for a, b in ranges:
                widget.tag_add(tag, a, b)
        except Exception:
            if on_done:
                on_done()
            return
        state = {"i": 0}

        def step():
            i = state["i"]
            if i >= frames:
                try:
                    widget.tag_remove(tag, "1.0", tk.END)
                except Exception:
                    pass
                if on_done:
                    on_done()
                return
            state["i"] = i + 1
            t = state["i"] / frames
            try:
                opts = {"foreground": self._lerp_color(from_color, to_color, t)}
                if indent_from is not None:
                    off = int(indent_from + (indent_to - indent_from) * t)
                    opts["lmargin1"] = off
                    opts["lmargin2"] = off
                widget.tag_configure(tag, **opts)
            except Exception:
                pass
            try:
                self.root.after(frame_ms, step)
            except Exception:
                pass
        try:
            # 延后一帧再开始，确保"透明态"先真正显示出来，否则会先正常显示再跳回透明
            self.root.after(1, step)
        except Exception:
            pass

    def _fade_text_lines(self, widget, lines, from_color, to_color, **kw):
        """按整行淡入/淡出（lines 是 1-based 行号）。"""
        ranges = []
        for n in sorted({int(x) for x in (lines or [])}):
            ranges.append(("%d.0" % n, "%d.end" % n))
        self._fade_text(widget, ranges, from_color, to_color, **kw)

    def _roll_counter(self, label, text):
        """让"共 N 项"这类计数滚到新值（格式没变才滚，否则直接换文本）。"""
        try:
            old = label.cget("text") or ""
        except Exception:
            return
        # 上一轮还没滚完又来了新值（扫描期间每 120ms 回来一批结果就刷一次）：
        # 先把上一轮的目标落定，再决定要不要开新一轮。
        # 必须落定——只 after_cancel 的话，标签会停在中间那个数字上：实测检测存在性
        # 期间"存在 1"会被打断成"存在 0"并且再也不动（数据其实早就对了）。
        job = getattr(label, "_roll_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            label._roll_job = None
            prev = getattr(label, "_roll_target", None)
            if prev is not None:
                old = prev
                try:
                    label.config(text=prev)
                except Exception:
                    pass
        label._roll_target = text
        if old == text:
            return
        pat = re.compile(r"\d+")
        old_t = pat.sub("{}", old)
        new_t = pat.sub("{}", text)
        old_n = pat.findall(old)
        new_n = pat.findall(text)
        if old_t != new_t or not old_n or len(old_n) != len(new_n):
            try:
                label.config(text=text)
            except Exception:
                pass
            return
        parts = re.split(r"(\d+)", text)
        slots = [i for i, p in enumerate(parts) if p.isdigit()]
        steps = 12          # 12 步 × 30ms ≈ 360ms：8 步 208ms 太快，像直接跳过去
        tick = 30

        def render(t):
            out = list(parts)
            for slot, a, b in zip(slots, old_n, new_n):
                out[slot] = str(int(int(a) + (int(b) - int(a)) * t))
            return "".join(out)

        def step(i):
            try:
                if i >= steps:
                    label.config(text=text)
                    label._roll_job = None
                    return
                label.config(text=render(i / steps))
                label._roll_job = self.root.after(tick, lambda: step(i + 1))
            except Exception:
                pass
        label._roll_job = self.root.after(tick, lambda: step(1))

    def _fade_log_line(self, start, end, level):
        """日志新行淡入。

        只在"慢速零散输出"时做：迁移时日志会成批涌入（一次十几行），
        每行都跑动画既积压又晃眼 —— 最近 200ms 超过 3 行就直接显示。
        """
        now = time.time()
        recent = getattr(self, "_log_recent", None)
        if recent is None:
            recent = self._log_recent = []
        recent.append(now)
        del recent[:-10]
        if sum(1 for t in recent if now - t <= 0.2) > 3:
            return
        if getattr(self, "_log_fading", False):
            return
        self._log_fading = True
        color = self.theme.get(self._LOG_COLOR_KEYS.get(level, "log_info_fg"),
                              self.theme.get("log_fg", "#000000"))
        # 9 帧 × 14ms ≈ 126ms：原来 14×18≈250ms 太慢，日志一行一行冒出来时明显拖沓。
        # 位移同步从 44px 收到 26px —— 时长减半、速度（px/ms）基本不变，才不会"闪一下"。
        self._fade_text(self.log_text, [(start, end)],
                        self.theme.get("log_bg", "#ffffff"), color,
                        frames=9, frame_ms=14,
                        indent_from=26, indent_to=0,
                        on_done=lambda: setattr(self, "_log_fading", False))

    def _init_execution_log(self):
        # Created before widgets: startup logs and workers use the same store.
        self._session_log = SessionLog(limit=5000)
        self._log_lock = threading.RLock()
        self._last_log_key = None
        self._log_update_job = None
        self._log_views_closed = False

    def _start_log_updates(self):
        self.log_text.bind("<Destroy>", self._close_execution_log, add="+")
        self._tick_log_views()

    def _close_execution_log(self, event):
        if event.widget is not self.log_text:
            return
        self._log_views_closed = True
        if self._log_update_job is not None:
            self.root.after_cancel(self._log_update_job)
            self._log_update_job = None
        self._session_log.close()

    def _tick_log_views(self):
        self._log_update_job = None
        if self._log_views_closed:
            return
        try:
            self._冲写日志()
        finally:
            if not self._log_views_closed:
                self._log_update_job = self.root.after(33, self._tick_log_views)

    def _冲写日志(self):
        # Bounded refresh; workers only append to the store, never call Tk.
        for view in (getattr(self, "_log_view", None),
                     getattr(self, "_log_big_live_view", None)):
            if view is not None and not view._closed:
                view.refresh()
        error = self._session_log.error
        # 正常时留空：「界面最多显示 N 行」是实现细节，用户用不着；这一行的位置只留给
        # 真出错的情况（完整日志写盘失败）。右边的「打开完整日志」按钮仍在。
        status = trp("完整日志保存失败：{0}", error) if error else ""
        label = getattr(self, "_log_status_label", None)
        if label is not None and label.cget("text") != status:
            label.configure(text=status)

    def _日志批次开始(self):
        self._批处理中 = True

    def _日志批次结束(self):
        self._批处理中 = False

    def log(self, message, level="INFO", save=True):
        # Redaction applies before either the live tail or the full file.
        message = i18n.tr(_可见文本(secrets.redact(message)))
        key = (level, message)
        persist = save and self._persistable(level, message)
        with self._log_lock:
            if not persist and key == self._last_log_key:
                return
            self._last_log_key = key
            self._session_log.append(message, level, save=persist)
        if level == "ERROR":
            if threading.current_thread() is threading.main_thread():
                self._mark_log_failure(message)
            elif hasattr(self, "_ui_calls"):
                self._ui_post(self._mark_log_failure, message)

    def _mark_log_failure(self, message):
        try:
            self._mark_failed_from_log(message)
        except (AttributeError, tk.TclError):
            # Startup or window teardown can precede the list widgets.
            pass

    def _flush_logs_to_file(self, include_header=False):
        # Kept for callers; each line is already flushed to the session file.
        self._session_log.flush()

    def clear_log(self):
        with self._log_lock:
            self._session_log.clear()
            self._last_log_key = None
        self._冲写日志()
        self.log(trp("📋 已清空显示，完整日志仍保留在文件中"), save=False)

    def open_full_log(self):
        try:
            self._session_log.flush()
            path = self._session_log.path
            if path is None or not path.is_file():
                messagebox.showinfo(trp("提示"), trp("暂无已保存的日志。"), parent=self.root)
                return
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as error:
            messagebox.showerror(trp("错误"), trp("无法打开日志：{0}", error), parent=self.root)

    def open_log_folder(self):
        try:
            self._session_log.flush()
            folder = self._session_log.directory
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as error:
            messagebox.showerror(trp("错误"), trp("无法打开文件夹：{0}", error), parent=self.root)

    # ---------- 日志区平滑滚动联动 ----------
    def _smooth(self, widget, rows=False, bind_widgets=None, on_render=None, **kw):
        """给一个可滚动控件装上平滑滚动。

        rows=False：Text 类，像素级真平滑。
        rows=True ：Treeview / 自绘表格，只能整行走，由引擎攒零头做出动画。
        """
        try:
            if rows:
                sc = SmoothScroller.for_rows(
                    widget, kw.pop("row_px", None) or tree_row_px(widget),
                    on_render=on_render, bind_widgets=bind_widgets, **kw)
            else:
                sc = SmoothScroller.for_text(widget, bind_widgets=bind_widgets, **kw)
            self._scrollers.append(sc)
            return sc
        except Exception:
            return None

    def _at_log_bottom(self):
        return self._log_view.at_bottom()

    def when_log_bottom(self, cb, timeout=3.0):
        # A reader looking at older output must not delay migration completion.
        # timeout 只为兼容旧调用方保留：新日志视图直接贴底渲染，不需要等滚动。
        self._冲写日志()

        def _run():
            # after_idle 里抛的异常 Tk 只会打到 stderr，收尾回调出错要进轨迹日志
            try:
                cb()
            except Exception as e:
                trace_exc("mw_log/when_log_bottom回调", e)
        self.root.after_idle(_run)

    def _log_remain_px(self):
        # Live following renders the latest batch directly at the bottom.
        return 0.0

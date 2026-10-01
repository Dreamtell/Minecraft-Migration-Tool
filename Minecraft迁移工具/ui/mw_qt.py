# ui/mw_qt.py
"""Qt 子进程宿主：建窗、传命令、收结果、跨进程写回清单。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import json
import os
import sys
import tkinter as tk
from pathlib import Path
from ui.diff_window import show_diff_window
from utils import secrets


class QtMixin:
    """Qt 子进程宿主：建窗、传命令、收结果、跨进程写回清单。"""

    # ------------------------------------------------------------------ #
    # 放大查看：PySide6 试点窗口（Tk 主窗口 + root.after 驱动 Qt 事件循环）
    # ------------------------------------------------------------------ #
    def _qt_available(self):
        """PySide6 是否可用。返回 (bool, 给用户看的原因)。

        **只探测、不 import**：主进程一旦 import 了 PySide6 就等于把 Qt 载了进来，
        而现在的架构是"Qt 窗口跑在 `ui/qt_host.py` 子进程里"，主进程要保持干净
        （否则又回到"Tk 与 Qt 共享主线程"那条老路）。所以这里用 find_spec 探一下。
        """
        if not getattr(self, "qt_enabled", True):
            return (False, "已在设置里关掉 PySide6（纯 Tk 模式）：主进程不再加载 Qt。")
        cached = getattr(self, "_qt_ok_cache", None)
        if cached is not None:
            return cached
        try:
            import importlib.util
            for cand in (Path(__file__).resolve().parent.parent / "_qt",
                         Path(__file__).resolve().parent.parent.parent / "_qt"):
                try:
                    if cand.is_dir() and str(cand) not in sys.path:
                        sys.path.insert(0, str(cand))
                except Exception:
                    pass
            if importlib.util.find_spec("PySide6") is not None:
                cached = (True, "Qt 窗口由独立子进程渲染；它崩了也不会带走主程序。")
                self._qt_ok_cache = cached
                return cached
            return (False, "装好 PySide6 后可切到 Qt 版窗口：pip install PySide6")
        except Exception as e:
            return (False, "PySide6 探测失败（%s: %s）" % (type(e).__name__, e))

    def _write_text_keep_scroll(self, text_widget, 行):
        """整份重写清单文本，但保持滚动位置（放大查看那条线共用）。

        不记住滚动位置的话，关掉放大查看会发现主界面清单自己跳回顶部。
        """
        content = "\n".join(str(x) for x in 行)
        try:
            first = text_widget.yview()[0]
        except Exception:
            first = 0.0
        text_widget.configure(state=tk.NORMAL)
        text_widget.edit_separator()
        text_widget.delete("1.0", tk.END)
        text_widget.insert("1.0", content + ("\n" if content else ""))
        text_widget.edit_separator()
        try:
            text_widget.yview_moveto(first)
        except Exception:
            pass
        self._update_text_states()
        self.save_config()

    def _apply_entries_delta(self, text_widget, added, removed):
        """把清单的"增删"应用到**当前**文本上，返回结果行列表。

        放大查看写回清单时只发变化，由这里落到文本上 —— 用户在编辑模式里另外敲的
        内容原样保留。以前是整份覆盖，而放大查看手里是"打开那一刻的快照"，
        于是它一关窗/一增删就把主界面的编辑盖回去了（用户报过）。
        """
        try:
            结果 = [ln.strip() for ln in text_widget.get("1.0", tk.END).splitlines()
                    if ln.strip()]
        except Exception:
            结果 = []
        if not added and not removed:
            # 一样都没变：**一个字符都别动**。这正是"只是开关了一下放大查看"那种情况，
            # 以前会拿快照整份覆盖 —— 顺手也就把用户在编辑模式里敲的内容、还没提交的
            # 编辑状态、滚动位置全搅了一遍。
            return 结果
        for 项 in (removed or []):
            项 = str(项)
            if 项 in 结果:
                结果.remove(项)          # 多重集口径：同名条目一次删一条
        for 项 in (added or []):
            项 = str(项)
            if 项 and 项 not in 结果:
                结果.append(项)
        self._write_text_keep_scroll(text_widget, 结果)
        return 结果

    def _qt_apply_entries(self, text_widget, entries, added=None, removed=None):
        """把 Qt 放大查看窗口的清单改动写回主界面。

        给了 added/removed 就走**增量**（只动变化的那几条，见 _apply_entries_delta）；
        没给（老调用方/老子进程）才退回整份覆盖 —— 保持向后兼容。
        """
        if added is None and removed is None:
            self._write_text_keep_scroll(text_widget, [str(x) for x in entries])
        else:
            self._apply_entries_delta(text_widget, added, removed)

    def _pump_qt(self):
        """Tk 的 after 循环里驱动 Qt 事件，并回收已经关掉的 Qt 窗口。

        两个事件循环同线程共存：Qt 的所有回调（点击/动画/绘制）都在这个
        after 回调里被调用，所以它们跑在主线程，从里面改 Tk 控件是安全的。

        **关掉的窗口必须在这里 deleteLater()**：Qt 的 C++ 对象不能在别的线程里析构
        —— 托盘图标的那个后台消息循环会触发 Python 的 GC，GC 一旦在托盘线程里回收
        PySide6 的 wrapper，shiboken 就在非主线程碰 Qt 的 C++ 层，表现就是致命的
        `PyEval_RestoreThread ... the GIL is released`（用户实测崩过：栈里正是
        tray.py 的 PumpMessages + 主线程 mainloop）。
        """
        views = [v for v in getattr(self, "_qt_views", []) if v.is_alive()]
        for v in views:
            try:
                v.pump()
            except Exception as e:
                self.log(f"⚠ PySide6 窗口事件循环异常：{e}", level="ERROR", save=False)
        # 关掉的窗口（pump 之前就关了、或者就在这次 pump 里关的）都交给主线程销毁。
        # 注意要拿"旧列表"和"现在还活着的"对比 —— 只看 views 的话，早就关掉的那些
        # 第一步就被过滤掉了，永远轮不到 deleteLater（C++ 对象就一直挂着）
        活的 = [v for v in views if v.is_alive()]
        retired = getattr(self, "_qt_retired", None)
        if retired is None:
            retired = self._qt_retired = []
        关掉的 = [v for v in getattr(self, "_qt_views", []) if v not in 活的]
        for v in 关掉的:
            retired.append(v)
            try:
                v.deleteLater()      # 主线程里安排销毁
            except Exception:
                pass
            try:
                from utils.helpers import trace_line
                trace_line("qt 窗口关闭 %s" % type(v).__name__)
            except Exception:
                pass
        self._qt_views = 活的
        if 关掉的:
            self._flush_qt_deletes()
        if len(retired) > 4:
            del retired[:-4]             # 只留最近几个引用，别攒着不放
        if 活的:
            self._qt_pump_after = self.root.after(12, self._pump_qt)
        else:
            self._qt_pump_after = None

    def _flush_qt_deletes(self):
        """把刚刚 `deleteLater()` 排下的销毁真正执行掉。

        `processEvents()` **不会**处理 DeferredDelete（实测：只 processEvents 的话
        C++ 对象一直不销毁），得显式 `sendPostedEvents` 一次。这一步必须在主线程做 ——
        它正是"别让托盘线程里的 GC 去析构 Qt 对象"的落点。
        """
        try:
            from PySide6 import QtCore as _QtCore, QtWidgets as _QtWidgets
            app = _QtWidgets.QApplication.instance()
            if app is not None:
                app.sendPostedEvents(None, _QtCore.QEvent.DeferredDelete)
                app.processEvents(_QtCore.QEventLoop.AllEvents, 8)
        except Exception:
            pass

    def _open_big_view_qt(self, source_text, title):
        """放大查看的 Qt 版 —— **跑在独立子进程里**（`ui/qt_host.py`）。

        返回 True 表示已接管。
        """
        ok, why = self._qt_available()
        if not ok:
            self.log("ℹ " + why, level="INFO", save=False)
            return False
        is_mod = "模组" in title or source_text is getattr(self, "mod_text", None)
        entries = [ln.strip() for ln in source_text.get("1.0", tk.END).splitlines()
                   if ln.strip()]
        sp = self.source_path.get().strip() if hasattr(self, "source_path") else ""
        if self._qt_host_alive("bigview"):
            self._send_qt_host_command("raise", kind="bigview")
            return True
        载荷 = {"entries": entries, "is_mod": is_mod, "source_path": sp,
                "title": title,
                "online_tags": bool(getattr(self, "online_tags", False)),
                "cards": (getattr(self, "big_view_view", "table") == "cards"),
                "failed": [list(x) for x in getattr(self, "_failed_items", [])]}
        if not self._start_qt_host("bigview", 载荷, source_text=source_text):
            return False
        try:
            from utils.helpers import trace_line
            trace_line("打开 Qt 放大查看（子进程）%s rows=%d" % (title, len(entries)))
        except Exception:
            pass
        self.log(f"🗂 已打开 Qt 放大查看：{title}（{len(entries)} 项，独立进程）",
                 level="INFO", save=False)
        return True

    def _open_diff_window(self, data, apply_callback):
        """开差异窗口。

        Qt 版**跑在独立子进程里**（`ui/qt_host.py`）：主进程一行 Qt 都不碰。
        之前是在主进程里用"Tk 的 after 驱动 processEvents"，用户机器上反复触发致命的
        `PyEval_RestoreThread ... the GIL is released`（崩点全在 Qt 窗口的操作路径上：
        打开窗口、切卡片、开详情），几轮加固都没根治 —— 根子是"两个 GUI 库共享主线程"。
        子进程建不起来时才回退到进程内 Tk 版（`show_diff_window`），功能不能少。
        """
        self.diff_qt = None
        self.diff_window = None
        _qt_ok = self._qt_available()[0]
        _想卡片 = (getattr(self, "diff_view", "table") == "cards")
        if getattr(self, "diff_backend", "qt") != "qt" or not _qt_ok:
            # 设置里选了经典 Tk 版，或总开关关了 PySide6，或 PySide6 不可用
            self.diff_window = show_diff_window(self.root, data, self.theme,
                                                self.current_theme, apply_callback,
                                                cards=_想卡片, env=self._instance_env())
            self._lock_tk_mirror("diff", self.diff_window)
            return self.diff_window
        if self._start_qt_host("diff",
                               {"data": data, "cards": _想卡片,
                                "source_path": self.source_path.get().strip()},
                               apply_callback):
            try:
                from utils.helpers import trace_line
                trace_line("打开 Qt 差异窗口（子进程）rows=%d" % len(data))
            except Exception:
                pass
            self.log(f"🗂 已打开 Qt 差异窗口（{len(data)} 项，独立进程）",
                     level="INFO", save=False)
            return None
        self.log("⚠ Qt 子进程起不来，改用进程内 Tk 差异窗口", level="WARNING", save=False)
        self.diff_window = show_diff_window(self.root, data, self.theme,
                                            self.current_theme, apply_callback,
                                            cards=_想卡片, env=self._instance_env())
        self._lock_tk_mirror("diff", self.diff_window)
        return self.diff_window

    # ---------- Qt 窗口的独立子进程宿主 ----------
    def _qt_host_alive(self, kind):
        """某个 Qt 窗口的子进程还活着吗。"""
        信息 = (getattr(self, "_qt_hosts", None) or {}).get(kind)
        if not 信息:
            return False
        proc = 信息.get("proc")
        return proc is not None and proc.poll() is None

    def _start_qt_host(self, kind, payload, apply_callback=None, source_text=None):
        """把 Qt 窗口丢到独立子进程里跑；主进程只写请求文件、轮询结果文件。

        已经有一个同类窗口在跑就只写一条 "raise" 命令把它叫回来（不重复开窗）。
        """
        try:
            import subprocess
            import tempfile
            if self._qt_host_alive(kind):
                self._send_qt_host_command("raise", kind=kind)
                return True
            根 = Path(__file__).resolve().parent.parent
            临时 = Path(tempfile.gettempdir()) / ("mctool_qt_%d" % os.getpid())
            临时.mkdir(parents=True, exist_ok=True)
            序号 = getattr(self, "_qt_host_seq", 0) + 1
            self._qt_host_seq = 序号
            req = 临时 / ("%s%d.req.json" % (kind, 序号))
            res = 临时 / ("%s%d.res.json" % (kind, 序号))
            cmd = 临时 / ("%s%d.cmd.json" % (kind, 序号))
            err = 临时 / ("%s%d.log" % (kind, 序号))
            for p in (res, cmd, err):
                try:
                    p.unlink()
                except Exception:
                    pass
            请求 = dict(payload)
            请求.update({"kind": kind, "theme": self.theme,
                         "result": str(res), "command": str(cmd)})
            # 请求是**落在临时目录里的明文 JSON**：API Key 绝不能从这里过去。
            # 子进程要用 key 就自己 import utils.secrets 读那个密钥文件（同一台机器、
            # 同一个用户，不需要"传递"）。scrub_obj 是兜底，防的是以后有人顺手往载荷里塞。
            req.write_text(json.dumps(secrets.scrub_obj(请求), ensure_ascii=False),
                           encoding="utf-8")
            if getattr(sys, "frozen", False):        # 打包成 exe 后没有 app.py 可传
                命令 = [sys.executable, "--qt-host", str(req)]
            else:
                命令 = [sys.executable, str(根 / "app.py"), "--qt-host", str(req)]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            errf = open(err, "wb")                   # 子进程的输出留档，崩了能看
            proc = subprocess.Popen(命令, cwd=str(根), creationflags=flags,
                                    stdin=subprocess.DEVNULL,
                                    stdout=errf, stderr=subprocess.STDOUT)
            if not hasattr(self, "_qt_hosts"):
                self._qt_hosts = {}
            self._qt_hosts[kind] = {"proc": proc, "req": req, "res": res, "cmd": cmd,
                                    "err": err, "apply_cb": apply_callback,
                                    "source_text": source_text}
            self._lock_edit(kind)          # 放大查看 / 模组差异开着 → 主界面不能编辑
            if not getattr(self, "_qt_host_poll", None):
                self._poll_qt_host()
            return True
        except Exception as e:
            self.log(f"⚠ 无法启动 Qt 子进程：{e}", level="ERROR", save=False)
            return False

    def _send_qt_host_command(self, cmd, kind=None, 附加=None):
        """给 Qt 子进程写一条命令。

        - `raise`：把窗口叫到前面
        - `theme`：换主题（`附加={"theme": {...}}`）—— Qt 窗口在独立子进程里，
          主界面切主题只能这样告诉它
        - `entries`：主界面清单变了（`附加={"entries": [...]}`）—— 子进程重建列表，
          勾选态保留（实时重载，见 _push_big_view_entries）
        """
        try:
            主机们 = getattr(self, "_qt_hosts", None) or {}
            目标 = [kind] if kind else list(主机们)
            行 = {"cmd": cmd}
            if 附加:
                行.update(附加)
            # 命令文件同样是落盘的明文（临时目录），一样过一遍脱敏
            数据 = json.dumps(secrets.scrub_obj(行), ensure_ascii=False) + "\n"
            for k in 目标:
                路径 = (主机们.get(k) or {}).get("cmd")
                if 路径 is None:
                    continue
                with open(路径, "a", encoding="utf-8") as f:
                    f.write(数据)
        except Exception:
            pass

    def _poll_qt_host(self):
        """Tk 主线程里轮询各个子进程的结果文件（150ms 一次）。"""
        self._qt_host_poll = None
        主机们 = getattr(self, "_qt_hosts", None) or {}
        if not 主机们:
            return
        for kind, 信息 in list(主机们.items()):
            proc = 信息.get("proc")
            res = 信息.get("res")
            if res is not None and res.exists():
                行们 = []
                try:
                    行们 = res.read_text(encoding="utf-8").splitlines()
                    res.unlink()
                except Exception:
                    行们 = []
                for line in 行们:
                    try:
                        消息 = json.loads(line)
                    except Exception:
                        continue
                    self._handle_qt_host(kind, 信息, 消息)
            if proc is not None and proc.poll() is not None:
                code = proc.returncode
                主机们.pop(kind, None)
                self._unlock_edit(kind)        # 窗口没了 → 主界面编辑解锁
                if code not in (0, None):
                    err = 信息.get("err")
                    尾巴 = ""
                    try:
                        if err is not None and err.exists():
                            尾巴 = err.read_text(encoding="utf-8",
                                                 errors="ignore")[-400:]
                    except Exception:
                        pass
                    self.log("⚠ Qt 窗口进程异常退出（%s）%s" % (code, 尾巴),
                             level="ERROR", save=False)
        if 主机们:
            self._qt_host_poll = self.root.after(150, self._poll_qt_host)

    def _handle_qt_host(self, kind, 信息, 消息):
        """子进程报上来的动作 —— 这里已经在 Tk 的 after 上下文里，改 Tk 是安全的。"""
        动作 = 消息.get("action")
        if 动作 == "ready":
            # 子进程窗口建好了（缓动泵也起来了）
            if not hasattr(self, "_qt_host_ready"):
                self._qt_host_ready = {}
            self._qt_host_ready[kind] = 消息
        elif 动作 == "apply":
            cb = 信息.get("apply_cb")
            if cb is not None:
                try:
                    cb(list(消息.get("files") or []))
                except Exception:
                    pass
        elif 动作 == "write_back":
            st = 信息.get("source_text")
            if st is not None:
                try:
                    self._qt_apply_entries(st, list(消息.get("entries") or []),
                                           消息.get("added"), 消息.get("removed"))
                except Exception:
                    pass
        elif 动作 == "close":
            # 子进程自己说"窗口关了"（app.exec() 返回后补的这条）：立刻解锁，不等轮询
            # 那边发现进程没了。两条路都走 _unlock_edit，重复调用是幂等的。
            self._unlock_edit(kind)
        elif 动作 == "entries_ok":
            # 子进程确认换好了清单（实时重载）；只留最新的，用于诊断/测试
            self._qt_entries_ok = {"kind": 消息.get("kind"), "rows": 消息.get("rows")}
        elif 动作 == "entries_fail":
            self.log("⚠ Qt 窗口更新清单失败：%s" % 消息.get("error"),
                     level="ERROR", save=False)
        elif 动作 == "theme_ok":
            # 子进程确认换好了：记个内存标记（诊断/测试用），日志里不刷（切一次一行太吵）
            if not hasattr(self, "_qt_theme_ok"):
                self._qt_theme_ok = {}
            self._qt_theme_ok[kind] = 消息.get("bg")
            try:
                from utils.helpers import trace_line
                trace_line("Qt 窗口主题已切换 kind=%s bg=%s"
                           % (kind, 消息.get("bg")))
            except Exception:
                pass
        elif 动作 == "theme_fail":
            self.log("⚠ Qt 窗口换主题失败：%s" % 消息.get("error"),
                     level="ERROR", save=False)

    def stop_qt_host(self):
        """退出前把所有 Qt 子进程收掉。"""
        poll, self._qt_host_poll = getattr(self, "_qt_host_poll", None), None
        if poll is not None:
            try:
                self.root.after_cancel(poll)
            except Exception:
                pass
        主机们, self._qt_hosts = getattr(self, "_qt_hosts", None) or {}, {}
        for 信息 in 主机们.values():
            proc = 信息.get("proc")
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

    def _push_big_view_entries(self, text_widget):
        """把清单的当前内容推给已经开着的 Qt 放大查看（实时重载）。

        Qt 版放大查看跑在独立子进程里，手里是"打开那一刻的快照"，主界面改了它不知道
        —— 于是它显示的清单会越看越旧。Tk 版是靠 <<ModlistChanged>>/<<ConfigChanged>>
        自己重载的，这里给 Qt 版补上同一条路子（子进程收到后重建，勾选态保留）。

        同一份内容只推一次：放大查看自己写回清单时也会触发 <<Modified>>，
        不挡一下就会把刚同步过去的内容再推回去（白跑一趟重排）。
        """
        if getattr(self, "_suppress_entries_push", False):
            return
        信息 = (getattr(self, "_qt_hosts", None) or {}).get("bigview") or {}
        if 信息.get("proc") is None or 信息["proc"].poll() is not None:
            return
        if 信息.get("source_text") is not text_widget:
            return                          # 那个窗口看的是另一张清单
        try:
            entries = [ln.strip() for ln in text_widget.get("1.0", tk.END).splitlines()
                       if ln.strip()]
        except Exception:
            return
        已推 = getattr(self, "_entries_pushed", None)
        if 已推 is None:
            self._entries_pushed = 已推 = {}
        if 已推.get(id(text_widget)) == entries:
            return
        已推[id(text_widget)] = entries
        self._send_qt_host_command("entries", kind="bigview", 附加={"entries": entries})

# ui/qt_host.py
"""Qt 窗口的独立进程宿主（放大查看 / 差异扫描都走这里）。

## 为什么单开一个进程

Tk 和 Qt 都要求"GUI 跑在主线程"，两者共享主线程就只能交替处理事件 —— 主进程那边的
做法是在 Tk 的 `after` 里调 `QApplication.processEvents()`。这套组合在用户机器上反复
触发致命的

    Fatal Python error: PyEval_RestoreThread: the function must be called with the
    GIL held ... but the GIL is released (the current Python thread state is NULL)

崩点全部落在 Qt 窗口的操作路径上（打开窗口、切卡片视图、打开模组详情），
前后试过：托盘线程单线程化、Qt 对象主线程 deleteLater、对话框延后释放、
建窗也 defer 回 Tk —— 都没能根治；这说明问题是"两个 GUI 库共享主线程"这个架构本身。

把 Qt 窗口整个挪到子进程之后：主进程里一行 Qt 都不跑（连 PySide6 都不加载），
子进程里只有 Qt（没有 Tk），各自拥有自己的主线程和事件循环 —— 那个组合从根上消失。

## 协议

主进程写一个请求 JSON，子进程读它建窗：

    {
      "kind": "diff" | "bigview",
      "title": "...",                 # bigview 用
      "is_mod": true,                 # bigview 用
      "source_path": "...",           # bigview 用
      "entries": ["a.jar", ...],      # bigview 用
      "data": [[...], ...],           # diff 用（差异表的 9 元组）
      "theme": {...},
      "result": "结果文件路径",         # 子进程把用户操作写这里
      "command": "命令文件路径"         # 主进程写这里（"raise" 叫到前面 / "theme" 换主题）
    }

结果文件里是一条一条的 JSON 行（子进程写、主进程读走就删）：

    {"action": "apply", "files": ["a.jar", ...]}     差异/放大窗口"应用所选"
    {"action": "write_back", "entries": ["a.jar"]}   放大窗口改完清单写回
    {"action": "theme_ok", "kind": "diff", "bg": "#2e2e2e"}   主题换好了
    {"action": "theme_fail", "kind": "diff", "error": "..."}  换主题炸了（主进程记一条 ERROR）
    {"action": "close"}                              窗口关了
"""
import json
import os
import sys
from pathlib import Path


def _make_hooks(QtCore):
    """把 Qt 的定时器包装成和主进程一样的 hooks 接口。

    主进程侧 hooks 是 Tk 的 `root.after`；子进程里没有 Tk，就用 QTimer ——
    接口一样（`after(ms, fn) -> job` / `after_cancel(job)`），窗口代码不用改。
    子进程里没有别的 GUI 库跟它抢事件循环，`app.exec()` 就是正常的主循环。
    """
    jobs = {}

    def _after(ms, fn):
        job = QtCore.QTimer()
        job.setSingleShot(True)

        def _run():
            jobs.pop(id(job), None)
            try:
                fn()
            except Exception:
                pass
        job.timeout.connect(_run)
        job.start(max(0, int(ms)))
        jobs[id(job)] = job
        return id(job)

    def _cancel(job_id):
        job = jobs.pop(job_id, None)
        if job is not None:
            try:
                job.stop()
            except Exception:
                pass

    return {"defer": lambda fn: fn(), "after": _after, "after_cancel": _cancel,
            "_jobs": jobs}


def _ensure_qt_on_path():
    """把项目里的 `_qt`（PySide6 的便携目录）加进 sys.path。

    主进程那边是 `qt_big_view` 在 import 时顺手加的；子进程不一定按那个顺序 import，
    这里自己加一遍，免得依赖环境变量 PYTHONPATH。
    """
    for cand in (Path(__file__).resolve().parent.parent / "_qt",
                 Path(__file__).resolve().parent.parent.parent / "_qt"):
        try:
            if cand.is_dir() and str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
        except Exception:
            pass


def _脱敏(值):
    """把要写进结果文件的内容过一遍脱敏（拿不到 utils.secrets 就原样返回）。

    这个子进程是"另一个进程"，但**不是外面的世界**：它和主进程同机同用户，
    要用 key 自己读那个密钥文件就行，不需要经过这里。
    """
    try:
        from utils import secrets
        return secrets.scrub_obj(值)
    except Exception:
        return 值


def run_host(argv):
    """子进程入口（app.py 里用 `--qt-host <请求文件>` 调进来）。"""
    _ensure_qt_on_path()
    try:
        req_path = Path(argv[argv.index("--qt-host") + 1])
    except Exception:
        return 2
    try:
        请求 = json.loads(req_path.read_text(encoding="utf-8"))
    except Exception:
        return 3

    结果路径 = Path(请求.get("result") or (str(req_path) + ".res"))
    命令路径 = Path(请求.get("command") or (str(req_path) + ".cmd"))
    主题 = 请求.get("theme") or {}

    def 写结果(消息):
        try:
            with open(结果路径, "a", encoding="utf-8") as f:
                # 结果文件也是落盘的明文；子进程只是"另一个进程"，不是"外面的世界"，
                # 一样按不可信处理：任何要写出去的东西先脱敏（见 utils/secrets.py）。
                f.write(json.dumps(_脱敏(消息), ensure_ascii=False) + "\n")
                f.flush()
        except Exception:
            pass

    from PySide6 import QtCore          # 只在子进程里加载 Qt

    hooks = _make_hooks(QtCore)

    try:
        from ui import qt_big_view as Q
        app = Q.ensure_app(主题 or None)
    except Exception as e:
        try:
            Path(str(结果路径) + ".err").write_text(
                _脱敏("加载 PySide6 失败：%r" % (e,)), encoding="utf-8")
        except Exception:
            pass
        return 4

    kind = 请求.get("kind") or "diff"
    view = None
    try:
        if kind == "bigview":
            from ui.qt_big_view import QtBigView

            def 写回(entries, added=None, removed=None):
                # added/removed 是相对上次写回的增删：主进程只把变化应用到当前文本上，
                # 免得把用户在主界面敲的内容盖回去（整份覆盖那个坑）
                写结果({"action": "write_back", "entries": list(entries),
                        "added": [str(x) for x in (added or [])],
                        "removed": [str(x) for x in (removed or [])]})

            主机 = dict(hooks)
            主机["write_back"] = 写回
            失败名单 = list(请求.get("failed") or [])
            主机["failed"] = lambda: list(失败名单)   # "定位错误"按钮要的名单
            view = QtBigView(请求.get("entries") or [],
                             bool(请求.get("is_mod", True)),
                             str(请求.get("source_path") or ""),
                             str(请求.get("title") or "清单"),
                             主题,
                             online_tags=bool(请求.get("online_tags", False)),
                             hooks=主机,
                             cards=bool(请求.get("cards", False)))
        else:
            from ui.qt_diff_view import QtDiffView

            def 应用(files):
                写结果({"action": "apply", "files": list(files)})

            view = QtDiffView(请求.get("data") or [], 主题,
                              hooks=hooks, apply_callback=应用,
                              cards=bool(请求.get("cards", False)),
                              source_path=str(请求.get("source_path") or ""))
        view.show_centered()
    except Exception as e:
        import traceback
        try:
            Path(str(结果路径) + ".err").write_text(
                _脱敏("建窗失败：\n" + traceback.format_exc()), encoding="utf-8")
        except Exception:
            pass
        return 5

    # 窗口一关，这个子进程的活儿就干完了 —— **必须退出**。
    # 主进程判断"窗口还开着没有"靠的就是"这个进程还在不在"（编辑锁、重复点放大查看
    # 时的 raise 都指着它）。而 `ensure_app` 里把 quitOnLastWindowClosed 关掉了，
    # 那是"Qt 跑在 Tk 主进程里"那会儿留的（关个 Qt 窗口不能把主进程带走）；
    # 子进程里只跑这一个窗口，就得打开它，否则关窗后进程一直挂在后台，
    # 表现就是：窗口明明关了，主界面编辑还是锁着；再点"放大查看"也只会给一个
    # 已经没有窗口的进程发 raise，窗口再也出不来。
    try:
        app.setQuitOnLastWindowClosed(True)
        # 双保险：窗口对象真被销毁（WA_DeleteOnClose）也明确退一次
        view.destroyed.connect(lambda *_: QtCore.QTimer.singleShot(0, app.quit))
    except Exception:
        pass

    # 父进程（主界面，或者跑验证脚本的那个 python）没了就自己退。
    # 不然它崩掉 / 被强杀之后，桌面上会留一个没人管的窗口 —— 而且这种孤儿窗口
    # 还会让"按标题找窗口"的测试认错对象（踩过一次：WM_CLOSE 发给了上一个残留窗口，
    # 被测进程当然没反应，看起来像修复失效）。1.5 秒问一次，开销可以忽略。
    #
    # 判活的两个坑：
    #   1) `OpenProcess` 对"已经退出、进程对象还没销毁"的 pid **照样返回句柄**（实测非 0），
    #      必须再看 `GetExitCodeProcess == STILL_ACTIVE(259)`；
    #   2) pid 会被**复用** —— 长回归里几百个进程起落，父进程的号很快会被别人用上，
    #      光看"这个号还活着"就会一直误判"父还在"，于是留下孤儿窗口（实测就这么漏了一个）。
    #      所以启动时把父进程的**创建时间**记下来，每次再对一遍。
    try:
        import ctypes
        from ctypes import wintypes as _wt

        class _FILETIME(ctypes.Structure):
            _fields_ = [("低", _wt.DWORD), ("高", _wt.DWORD)]

        _k32 = ctypes.windll.kernel32
        _父pid = os.getppid()

        def _创建时间(pid):
            """取进程创建时间（100ns 计数）。取不到返回 None。"""
            h = _k32.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                建, 退, 内, 用 = (_FILETIME(), _FILETIME(), _FILETIME(), _FILETIME())
                if not _k32.GetProcessTimes(h, ctypes.byref(建), ctypes.byref(退),
                                            ctypes.byref(内), ctypes.byref(用)):
                    return None
                return (建.高 << 32) | 建.低
            except Exception:
                return None
            finally:
                _k32.CloseHandle(h)

        _父创建 = _创建时间(_父pid)

        def _父还在():
            h = _k32.OpenProcess(0x1000, False, _父pid)
            if not h:
                return _k32.GetLastError() == 5              # 拒绝访问 = 还在，只是没权限
            try:
                code = ctypes.c_ulong()
                if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return True
                if code.value != 259:                        # STILL_ACTIVE
                    return False
                if _父创建 is not None:
                    现在 = _创建时间(_父pid)
                    if 现在 is not None and 现在 != _父创建:
                        return False                         # 号被复用了，原父进程已经没了
                return True
            finally:
                _k32.CloseHandle(h)

        _看门 = QtCore.QTimer()
        _看门.setInterval(1500)
        _看门.timeout.connect(lambda: app.quit() if not _父还在() else None)
        _看门.start()
    except Exception:
        pass

    # 滚轮缓动现在由控件自己驱动（`_SmoothWheel._sw_ensure_timer`，收到滚轮就起一个
    # 12ms 的 QTimer）。这里**不再额外 tick** —— 两处一起推会让动画速度翻倍。
    # 只上报"窗口好了、缓动自驱可用"给主进程（诊断用）。
    写结果({"action": "ready", "kind": kind, "pump": True,
            "cards": bool(请求.get("cards", False)),
            "rows": (len(请求.get("data") or []) if kind == "diff"
                     else len(请求.get("entries") or []))})

    # 主进程想"叫回"窗口 / 换主题时往命令文件里写一行；这里轮询执行
    def 巡命令():
        try:
            if 命令路径.exists():
                for line in 命令路径.read_text(encoding="utf-8").splitlines():
                    try:
                        命令 = json.loads(line)
                    except Exception:
                        continue
                    if view is None:
                        continue
                    动作 = 命令.get("cmd")
                    if 动作 == "raise":
                        # 重复点「放大查看 / 模组差异」时主进程就写这条命令。
                        # 光 showNormal+raise_+activateWindow 在 Windows 前台锁下
                        # 常常只是任务栏闪一下，窗口还压在后面 —— 再走一次 Win32 级置顶。
                        try:
                            view.showNormal()
                            view.raise_()
                            view.activateWindow()
                            try:
                                from utils.helpers import force_foreground
                                force_foreground(view)
                            except Exception:
                                pass
                        except Exception:
                            pass
                    elif 动作 == "entries":
                        # 主界面清单改了：换成新内容（勾选态保留，见 QtBigView.set_entries）
                        try:
                            n, _变了 = view.set_entries(命令.get("entries") or [])
                            写结果({"action": "entries_ok", "kind": kind, "rows": n})
                        except Exception as e:
                            写结果({"action": "entries_fail", "kind": kind,
                                    "error": repr(e)})
                    elif 动作 == "theme":
                        # 主界面切了浅色/深色：这里把整套配色重新铺一遍，并回报结果
                        try:
                            view.set_theme(dict(命令.get("theme") or {}))
                            写结果({"action": "theme_ok", "kind": kind,
                                    "bg": (命令.get("theme") or {}).get("bg")})
                        except Exception as e:
                            写结果({"action": "theme_fail", "kind": kind,
                                    "error": repr(e)})
                try:
                    命令路径.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        QtCore.QTimer.singleShot(300, 巡命令)

    QtCore.QTimer.singleShot(300, 巡命令)
    try:
        app.exec()
    except Exception:
        pass
    写结果({"action": "close"})
    return 0

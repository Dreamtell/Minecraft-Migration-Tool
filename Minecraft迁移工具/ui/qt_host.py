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
      "command": "命令文件路径"         # 主进程写这里（目前只有 "raise"）
    }

结果文件里是一条一条的 JSON 行（子进程写、主进程读走就删）：

    {"action": "apply", "files": ["a.jar", ...]}     差异/放大窗口"应用所选"
    {"action": "write_back", "entries": ["a.jar"]}   放大窗口改完清单写回
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
                f.write(json.dumps(消息, ensure_ascii=False) + "\n")
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
                "加载 PySide6 失败：%r" % (e,), encoding="utf-8")
        except Exception:
            pass
        return 4

    kind = 请求.get("kind") or "diff"
    view = None
    try:
        if kind == "bigview":
            from ui.qt_big_view import QtBigView

            def 写回(entries):
                写结果({"action": "write_back", "entries": list(entries)})

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
                              hooks=hooks, apply_callback=应用)
        view.show_centered()
    except Exception as e:
        import traceback
        try:
            Path(str(结果路径) + ".err").write_text(
                "建窗失败：\n" + traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        return 5

    # 滚轮缓动：窗口里那套"泵"原来是主进程用 Tk 的 after 推的（`view.pump()`），
    # 现在窗口在子进程里，改用一个 QTimer 推 —— 子进程有自己的事件循环，不跟谁抢。
    # 只推**滚动缓动**，不调 `pump()`：那里面还有 processEvents，在 app.exec() 里
    # 重入事件循环是另一类麻烦。
    def _tick():
        now = time.perf_counter()
        for w in (getattr(view, "table", None), getattr(view, "cards", None)):
            if w is None:
                continue
            try:
                if getattr(w, "_sw_active", False):
                    w.tick_scroll(now)
            except Exception:
                pass

    泵 = QtCore.QTimer()
    泵.setInterval(16)
    泵.timeout.connect(_tick)
    泵.start()
    view._pump_timer = 泵                 # 留引用，别被回收

    # 告诉主进程"窗口好了、缓动泵也起来了"（主进程日志/诊断用得到）
    写结果({"action": "ready", "kind": kind, "pump": True,
            "rows": (len(请求.get("data") or []) if kind == "diff"
                     else len(请求.get("entries") or []))})

    # 主进程想"叫回"这个窗口时往命令文件里写一行；这里轮询执行
    def 巡命令():
        try:
            if 命令路径.exists():
                for line in 命令路径.read_text(encoding="utf-8").splitlines():
                    try:
                        命令 = json.loads(line)
                    except Exception:
                        continue
                    if 命令.get("cmd") == "raise" and view is not None:
                        try:
                            view.showNormal()
                            view.raise_()
                            view.activateWindow()
                        except Exception:
                            pass
                        break
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

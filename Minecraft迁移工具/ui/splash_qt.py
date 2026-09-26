"""独立进程的 Qt 闪屏（PySide6）。

**为什么不是一个库的事**：实测瓶颈不在"画得多慢"，而在"主线程什么时候肯让出一帧"。
构建主界面期间 `on_stage` 只被调用 30 次、最大间隔 325ms —— 闪屏在这段时间里是完全静止的
（整体约 25fps）。Tk 版每帧 5ms、Qt 版每帧 3.8ms，谁画都救不了这一点。

让它跑在**独立进程**里就解决了：子进程有自己的事件循环，主线程构建界面时它照样刷帧。
实测（父进程连续构建 1.95 秒、期间完全不泵）：子进程 **59.5 / 62.5 fps**。

协议（父↔子都用文本行，简单到不会出错）：
    子 → 父：READY / FPS xx.x / CLOSED
    父 → 子：close
拿不到 PySide6、或者子进程 1.2 秒内没 READY，就由调用方回落到进程内的 Tk 闪屏。

冻结成 exe 之后 `sys.executable` 就是 exe 自己，所以子进程用 `--splash-child` 参数
重新拉起同一个程序（app.py 里最开头就拦这个参数）。
"""
from __future__ import annotations

import math
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

# 视觉参数与 ui/splash.py 完全一致，两个闪屏长得一样
from ui.splash import (BG, BORDER, CARD_ALPHA, CARD_H, CARD_RADIUS, CARD_W,
                       SUB_FG, TITLE_FG, _CUBE_CX, _CUBE_CY, _CUBE_E, _CUBE_PX,
                       _CUBE_RGB, _CUBE_V, _DIST, _HUE_SPEED, _OUT_SEC, _PARTICLES,
                       _POP_FROM, _POP_SEC, _SPIN_X, _SPIN_Y, _SUB_Y, _TITLE_Y,
                       _WIN_H, _WIN_W, _out_scale, _pop_scale, _shade)

SPLASH_ARG = "--splash-child"
TARGET_FPS = 60
READY_TIMEOUT_SEC = 1.2


def _load_qt():
    """导入 PySide6（项目目录/仓库根目录下的 _qt 也认）。拿不到就返回 None。"""
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        return QtCore, QtGui, QtWidgets
    except Exception:
        pass
    for cand in (Path(__file__).resolve().parent.parent / "_qt",
                 Path(__file__).resolve().parent.parent.parent / "_qt"):
        if (cand / "PySide6").is_dir() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
            break
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        return QtCore, QtGui, QtWidgets
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 子进程：真正画闪屏的那个窗口
# --------------------------------------------------------------------------- #
def run_child():                      # pragma: no cover - 在子进程里跑
    """子进程入口：显示闪屏并自己跑 60fps 动画，直到 stdin 收到 close。"""
    mods = _load_qt()
    if mods is None:
        print("NOQT", flush=True)
        return 1
    QtCore, QtGui, QtWidgets = mods
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    try:
        # 任务栏那一瞬间的图标也用应用的 1.ico（不设就是 Windows 通用占位图标）。
        # 这里故意**不 import utils.helpers**：那个 import 要 0.1s，会推迟闪屏第一帧，
        # 所以就地按同样的规则找一遍 1.ico（打包后 / 项目根目录 / 当前目录）。
        _base = getattr(sys, "_MEIPASS", None)
        _cands = ([Path(_base) / "1.ico"] if _base else []) + [
            Path(__file__).resolve().parent.parent / "1.ico",
            Path.cwd() / "1.ico"]
        for _p in _cands:
            if _p.exists():
                app.setWindowIcon(QtGui.QIcon(str(_p)))
                break
    except Exception:
        pass

    class SplashWindow(QtWidgets.QWidget):
        def __init__(self):
            super().__init__(None)
            self._ax, self._ay, self._hue = -0.50, 0.62, 0.0
            self._scale = _POP_FROM
            self._pop_t = 0.0
            self._out_t = 0.0
            self._closing = False
            self._last_t = time.perf_counter()
            self._particles = [self._spawn() for _ in range(_PARTICLES)]
            self.setWindowFlags(QtCore.Qt.FramelessWindowHint
                                | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
                                | QtCore.Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
            self.setFixedSize(_WIN_W, _WIN_H)
            self.setWindowOpacity(CARD_ALPHA)
            scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
            self.move(scr.center().x() - _WIN_W // 2, scr.center().y() - _WIN_H // 2)
            self._f_title = QtGui.QFont("Microsoft YaHei UI", 16)
            self._f_title.setBold(True)
            self._f_sub = QtGui.QFont("Microsoft YaHei UI", 10)
            self._title = "Minecraft 整合包迁移工具"
            self._subtitle = "增强版 v4"

        @staticmethod
        def _spawn():
            while True:
                x, y, z = (random.uniform(-1, 1) for _ in range(3))
                r = math.sqrt(x * x + y * y + z * z)
                if 0.35 < r <= 1.0:
                    break
            k = random.uniform(1.05, 1.35) / r
            x, y, z = x * k, y * k, z * k
            sp = random.uniform(0.012, 0.028)
            return [x, y, z, x * sp, y * sp, z * sp, random.uniform(0.7, 1.3)]

        @staticmethod
        def _q(rgb, a=255):
            return QtGui.QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(a))

        def paintEvent(self, _ev):
            p = QtGui.QPainter(self)
            p.setRenderHints(QtGui.QPainter.Antialiasing
                             | QtGui.QPainter.TextAntialiasing)
            s = self._scale
            cw, ch = CARD_W * s, CARD_H * s
            ox, oy = (_WIN_W - cw) / 2.0, (_WIN_H - ch) / 2.0
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtCore.QRectF(ox, oy, cw, ch),
                                CARD_RADIUS * s, CARD_RADIUS * s)
            p.setPen(QtGui.QPen(QtGui.QColor(BORDER), max(1.0, 2.0 * s)))
            p.setBrush(QtGui.QColor(BG))
            p.drawPath(path)
            if s > 0.02:
                self._paint_cube(p, ox + _CUBE_CX * s, oy + _CUBE_CY * s, _CUBE_PX * s)
            p.setPen(QtGui.QColor(TITLE_FG))
            f = QtGui.QFont(self._f_title)
            f.setPointSizeF(max(1.0, 16.0 * s))
            p.setFont(f)
            p.drawText(QtCore.QRectF(ox, oy + _TITLE_Y * s - 20 * s, cw, 40 * s),
                       int(QtCore.Qt.AlignCenter), self._title)
            p.setPen(QtGui.QColor(SUB_FG))
            f2 = QtGui.QFont(self._f_sub)
            f2.setPointSizeF(max(1.0, 10.0 * s))
            p.setFont(f2)
            p.drawText(QtCore.QRectF(ox, oy + _SUB_Y * s - 14 * s, cw, 28 * s),
                       int(QtCore.Qt.AlignCenter), self._subtitle)
            p.end()

        def _paint_cube(self, p, cx, cy, size):
            ca, sa = math.cos(self._ax), math.sin(self._ax)
            cb, sb = math.cos(self._ay), math.sin(self._ay)
            rot, proj = [], []
            for (x, y, z) in _CUBE_V:
                y1 = y * ca - z * sa
                z1 = y * sa + z * ca
                x1 = x * cb + z1 * sb
                z2 = -x * sb + z1 * cb
                rot.append((x1, y1, z2))
                f = _DIST / (_DIST + z2)
                proj.append((x1 * f, -y1 * f))
            half = size / 2.0
            k = size / _CUBE_PX
            for a, b in _CUBE_E:
                ax_, ay_ = proj[a]
                bx_, by_ = proj[b]
                near = 1.0 - ((rot[a][2] + rot[b][2]) * 0.5 + 1.6) / 3.2
                near = max(0.0, min(1.0, near))
                lw = 3 if near > 0.62 else (2 if near > 0.32 else 1)
                x1, y1 = cx + ax_ * half / 2.0, cy + ay_ * half / 2.0
                x2, y2 = cx + bx_ * half / 2.0, cy + by_ * half / 2.0
                grad = QtGui.QLinearGradient(x1, y1, x2, y2)
                for i in range(9):
                    t = i / 8.0
                    wave = 0.5 + 0.5 * math.sin(((t * 0.75 + self._hue) % 1.0)
                                                * 2 * math.pi)
                    lit = 0.46 + 0.54 * wave * (0.40 + 0.60 * near)
                    grad.setColorAt(t, self._q(_shade(_CUBE_RGB, lit)))
                p.setPen(QtGui.QPen(QtGui.QBrush(grad), max(1.0, lw * k),
                                    QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
                p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))
            p.setPen(QtCore.Qt.NoPen)
            for i, pt in enumerate(self._particles):
                pt[0] += pt[3]
                pt[1] += pt[4]
                pt[2] += pt[5]
                pt[6] -= 0.035
                if pt[6] <= 0:
                    self._particles[i] = self._spawn()
                    pt = self._particles[i]
                f = _DIST / (_DIST + pt[2])
                sx, sy = cx + pt[0] * f * half / 2.0, cy - pt[1] * f * half / 2.0
                fade = min(1.0, pt[6] / 0.7)
                rad = max(0.9, (0.7 + 0.9 * f) * 1.1 * fade * k)
                p.setBrush(self._q(_shade(_CUBE_RGB, 0.40 + 0.60 * fade),
                                   int(235 * fade)))
                p.drawEllipse(QtCore.QPointF(sx, sy), rad, rad)

        def tick(self):
            now = time.perf_counter()
            dt = max(0.0, min(0.1, now - self._last_t))
            self._last_t = now
            if self._closing:
                self._out_t = min(1.0, self._out_t + dt / _OUT_SEC)
                self._scale = _out_scale(self._out_t)
            elif self._pop_t < 1.0:
                self._pop_t = min(1.0, self._pop_t + dt / _POP_SEC)
                self._scale = _pop_scale(self._pop_t)
            self._ax += _SPIN_X * dt
            self._ay += _SPIN_Y * dt
            self._hue = (self._hue + _HUE_SPEED * dt) % 1.0
            self.update()

    win = SplashWindow()
    win.show()
    st = {"n": 0, "t0": time.perf_counter(), "closing": False, "orphan": False}

    def tick():
        if st["orphan"]:                  # 父进程没了：立刻退，不播收起动画
            app.quit()
            return
        if st["closing"]:
            win._closing = True
            win.tick()
            if win._out_t >= 1.0:
                print("CLOSED", flush=True)
                app.quit()
            return
        win.tick()
        st["n"] += 1
        now = time.perf_counter()
        if now - st["t0"] >= 1.0:
            print("FPS %.1f" % (st["n"] / (now - st["t0"])), flush=True)
            st["n"], st["t0"] = 0, now

    timer = QtCore.QTimer()
    timer.setInterval(max(1, int(1000 / TARGET_FPS)))
    timer.timeout.connect(tick)
    timer.start()

    def reader():
        try:
            for line in sys.stdin:
                if line.strip() == "close":
                    st["closing"] = True
        except Exception:
            pass
        # stdin 到了 EOF：父进程没了（正常退出会先发 close；这里是异常退出/被杀）。
        # 必须自己退出，否则会留一个孤儿闪屏进程一直在那儿转。
        st["orphan"] = True

    threading.Thread(target=reader, daemon=True).start()
    print("READY", flush=True)
    app.exec()
    return 0


# --------------------------------------------------------------------------- #
# 父进程侧：句柄
# --------------------------------------------------------------------------- #
class QtSplashHandle:
    """子进程闪屏的父进程句柄：ok / fps / close() / kill()。"""

    def __init__(self, proc):
        self.proc = proc
        self.fps = []
        self.ready = False
        self.closed_line = False
        self._closed = False
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if line == "READY":
                    self.ready = True
                elif line == "CLOSED":
                    self.closed_line = True
                elif line.startswith("FPS"):
                    try:
                        self.fps.append(float(line.split()[1]))
                    except Exception:
                        pass
        except Exception:
            pass

    def close(self):
        """让子进程播"收起"动画后自己退出（不阻塞、不等待）。"""
        if self._closed:
            return
        self._closed = True
        try:
            self.proc.stdin.write("close\n")
            self.proc.stdin.flush()
        except Exception:
            self.kill()

    def wait(self, timeout=3.0):
        try:
            self.proc.wait(timeout=timeout)
        except Exception:
            self.kill()

    def kill(self):
        self._closed = True
        try:
            self.proc.kill()
        except Exception:
            pass


def _has_qt() -> bool:
    """有没有 PySide6 —— 只用 find_spec 探一下，**不 import**。

    父进程为了"查一下库在不在"去 import PySide6 要花 ~150ms，还会把整包加载进内存，
    对只想赶紧建主界面的父进程来说纯属浪费；真正的导入放在子进程里做。
    """
    try:
        import importlib.util
        if importlib.util.find_spec("PySide6") is not None:
            return True
    except Exception:
        pass
    for cand in (Path(__file__).resolve().parent.parent / "_qt",
                 Path(__file__).resolve().parent.parent.parent / "_qt"):
        if (cand / "PySide6").is_dir():
            return True
    return False


def spawn():
    """尝试拉起独立进程闪屏。成功返回 QtSplashHandle，失败返回 None（调用方回落 Tk 闪屏）。

    只有拿到子进程的 READY 才算成功：PySide6 没装、导入失败、或者启动太慢，
    都会走回落，不会让程序卡在"没有闪屏也没主界面"的中间态。
    """
    if not _has_qt():
        return None
    try:
        if getattr(sys, "frozen", False):       # PyInstaller：重新拉起自己
            cmd = [sys.executable, SPLASH_ARG]
        else:
            cmd = [sys.executable, str(Path(__file__).resolve().parents[1] / "app.py"),
                   SPLASH_ARG]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", cwd=str(Path(__file__).resolve().parents[1]))
    except Exception:
        return None
    handle = QtSplashHandle(proc)
    deadline = time.time() + READY_TIMEOUT_SEC
    while time.time() < deadline:
        if handle.ready:
            return handle
        if proc.poll() is not None:            # 子进程自己退了（比如没装 Qt）
            break
        time.sleep(0.02)
    handle.kill()
    return None

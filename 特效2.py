"""特效实验场 2 —— CS2 式「翻转」按钮。

用法：
    python 特效2.py                     # 开窗口玩：鼠标悬停 = 扫光，点击 = 翻转，Esc 退出
    python 特效2.py --auto 1.6          # 每 1.6 秒自动翻一次（演示用）
    python 特效2.py --axis x            # 绕横轴翻（上下翻）而不是绕竖轴（左右翻）
    python 特效2.py --bench 240         # 不开窗口跑 240 帧，打印帧时统计
    python 特效2.py --shot out.png --steps 20   # 离屏渲染一帧存图

翻转是怎么做的（不是假的缩放）：
    按钮当成一块真的硬纸板绕中轴旋转。四个角各自按 3D 位置 (X, Y, Z) 算出来，
    再用透视 k = cam / (cam + Z) 投到屏幕 —— 近的一边变高变宽、远的一边收窄，
    这才是"翻转"，不是 cos(theta) 横向压扁。
    投影得到的梯形用 QTransform.quadToQuad 贴回矩形面，面本身（渐变/文字/斜纹）
    照旧按 (0,0,w,h) 画，所以透视形变全交给 Qt，性能基本免费。

为什么翻到一半就换成背面：
    前半程画正面、角度 0 -> 90 度；后半程画背面、角度 -90 -> 0 度。
    这样背面收尾时角度正好是 0，文字天然是正的，不用镜像。
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

BG = "#0b0d12"
WIN_W, WIN_H = 1280, 760


# ---------------------------------------------------------------- 参数

@dataclass
class Params:
    fps: int = 240
    bw: float = 420.0       # 按钮宽
    bh: float = 96.0        # 按钮高
    rad: float = 12.0       # 圆角半径（主程序按钮是 8px，这里放大一点看得清）
    flip_ms: float = 240.0  # 翻一次多久
    axis: str = "x"         # x = 绕横轴（上下翻，CS2 那个手感）  y = 绕竖轴（左右翻）
    cam: float = 900.0      # 透视距离：越小透视越猛，越大越像平行投影
    tip: float = 1.0        # 1 = 上边朝观众扑过来, -1 = 反方向（F 键切换）
    sink: float = 0.05      # 侧过来时整体收一点，像被推进去，落地才有重量
    over: float = 1.3       # 过冲量（OutBack 的 s）：0 = 平滑，越大越"啪"一下
    auto: float = 0.0       # > 0 每隔这么多秒自动翻一次
    flip_lock: float = -1.0  # >= 0 时把翻转角钉死在这个值（截图/比对用）
    sweep_ms: float = 780.0 # 悬停扫光扫一趟多久

    # 配色（CS2 那种近黑面板 + 冷灰描边 + 冷白高光）
    face_top: str = "#232a31"
    face_bot: str = "#12171b"
    face_top_hover: str = "#2f3a44"
    face_bot_hover: str = "#1a2026"
    edge: str = "#4a5560"
    edge_hot: str = "#8fd8ff"
    accent: str = "#6fd3ff"


# 三档手感：P 键现场切，边翻边比
PRESETS = [
    ("干脆", {"flip_ms": 240.0, "over": 1.30, "sink": 0.05}),
    ("标准", {"flip_ms": 320.0, "over": 0.90, "sink": 0.05}),
    ("沉",   {"flip_ms": 460.0, "over": 0.55, "sink": 0.08}),
]


def apply_preset(p: "Params", idx: int) -> None:
    for k, v in PRESETS[idx % len(PRESETS)][1].items():
        setattr(p, k, v)


# 两个面各一套配色，语义跟 CS2 对齐：正面"开始匹配"是绿的，背面"搜索中"是红的
FACE_IDLE = {"face_top": "#1b2a20", "face_bot": "#0a120d",
             "face_top_hover": "#25382b", "face_bot_hover": "#0f1a13",
             "edge": "#3d5647", "edge_hot": "#7ee0a8", "accent": "#46cf8a",
             "title": "#eefaf2", "sub": "#8fc9a8"}

FACE_SEARCH = {"face_top": "#2c1a1a", "face_bot": "#120a0a",
               "face_top_hover": "#3d2424", "face_bot_hover": "#1a0f0f",
               "edge": "#5c3c3c", "edge_hot": "#ff9b9b", "accent": "#ff5a5a",
               "title": "#fdeeee", "sub": "#e0a0a0"}


# ---------------------------------------------------------------- 缓动

def _ease_out_back(t: float, s: float) -> float:
    t -= 1.0
    return t * t * ((s + 1.0) * t + s) + 1.0


def _q(color: str) -> QtGui.QColor:
    return QtGui.QColor(color)


# ---------------------------------------------------------------- 特效

class Effect:
    def __init__(self, p: Params):
        self.p = p
        self.w = 1
        self.h = 1
        self.mouse = QtCore.QPointF(-1e4, -1e4)
        self.t = 0.0
        self.hover = False
        self.hover_amt = 0.0     # 0..1 平滑过的悬停量（不插值的话进出会硬切）
        self.flip = 0.0          # 0 = 正面完全朝向观众, 1 = 背面
        self.flipping = False
        self.flip_from = 0.0
        self.flip_to = 0.0
        self.flip_t0 = 0.0
        self.back = False
        self._auto_t0 = 0.0
        self._paths: dict = {}
        self._img = None         # 面先画进这张图，再整张贴上去
        self.dpr = 1.0           # 由窗口每帧喂进来：贴图必须按物理像素排，1.25 缩放屏上不然就是放大糊
        self._recolor()

    def _recolor(self) -> None:
        # 两套颜色常驻，paint 每帧按当前是哪个面挑一套（self._sq 是下游唯一读口）
        self._sq_idle = {k: _q(v) for k, v in FACE_IDLE.items()}
        self._sq_search = {k: _q(v) for k, v in FACE_SEARCH.items()}
        self._sq = self._sq_idle

    def _face_path(self, face: QtCore.QRectF, inset: float = 0.0) -> QtGui.QPainterPath:
        # 圆角路径每帧重建要 1ms 左右，按 (宽,高,圆角,内缩) 缓存下来只建一次
        key = (face.width(), face.height(), self.p.rad, inset)
        path = self._paths.get(key)
        if path is None:
            r = face.adjusted(inset, inset, -inset, -inset)
            rad = max(0.0, min(self.p.rad - inset, r.width() * 0.5, r.height() * 0.5))
            path = QtGui.QPainterPath()
            path.addRoundedRect(r, rad, rad)
            self._paths[key] = path
        return path

    # ---- 生命周期
    def reset(self, w: int, h: int) -> None:
        self.w, self.h = max(1, w), max(1, h)
        self.t = 0.0

    def rect(self) -> QtCore.QRectF:
        bw, bh = self.p.bw, self.p.bh
        return QtCore.QRectF((self.w - bw) / 2.0, (self.h - bh) / 2.0, bw, bh)

    # ---- 输入
    def click(self, x: float, y: float) -> None:
        if self.rect().adjusted(-40, -40, 40, 40).contains(x, y):
            self.toggle()

    def toggle(self) -> None:
        self.back = not self.back
        self.flip_from = self.flip
        self.flip_to = 1.0 if self.back else 0.0
        self.flip_t0 = self.t
        self.flipping = True
        self._auto_t0 = self.t

    # ---- 每帧
    def step(self, dt: float) -> None:
        self.t += dt
        self.hover = self.rect().contains(self.mouse)
        target = 1.0 if self.hover else 0.0
        # 悬停淡入 90ms / 淡出 150ms
        k = min(1.0, dt / (0.09 if target > self.hover_amt else 0.15))
        self.hover_amt += (target - self.hover_amt) * k

        if self.p.flip_lock >= 0.0:
            self.flip = self.p.flip_lock
        elif self.flipping:
            u = (self.t - self.flip_t0) * 1000.0 / max(1.0, self.p.flip_ms)
            if u >= 1.0:
                self.flip = self.flip_to
                self.flipping = False
            else:
                e = _ease_out_back(u, self.p.over)
                self.flip = self.flip_from + (self.flip_to - self.flip_from) * e

        if (self.p.auto > 0.0 and self.p.flip_lock < 0.0 and not self.flipping
                and self.t - self._auto_t0 >= self.p.auto):
            self.toggle()

    # ---- 3D 投影
    def _project(self, ang: float) -> list:
        """把 (0,0,w,h) 这块面绕中轴转 ang 弧度后投到屏幕，返回四个屏幕角点。"""
        r = self.rect()
        cx, cy = r.center().x(), r.center().y()
        w, h = r.width(), r.height()
        c, s = math.cos(ang), math.sin(ang)
        vertical = self.p.axis.lower() == "x"
        pts = []
        for fx, fy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
            X, Y = fx * w, fy * h
            if vertical:                     # 绕横轴：上下翻
                Xr, Yr, Z = X, Y * c, Y * s
            else:                            # 绕竖轴：左右翻
                Xr, Yr, Z = X * c, Y, X * s
            Z *= self.p.tip                     # 决定哪条边走远：翻的方向感全在这里
            k = self.p.cam / (self.p.cam + Z)   # Z > 0 = 离观众更远
            k *= 1.0 - self.p.sink * abs(math.sin(ang))
            pts.append(QtCore.QPointF(cx + Xr * k, cy + Yr * k))
        return pts

    def _transform(self, quad: list):
        r = self.rect()
        src = QtGui.QPolygonF([
            QtCore.QPointF(0, 0), QtCore.QPointF(r.width(), 0),
            QtCore.QPointF(r.width(), r.height()), QtCore.QPointF(0, r.height())])
        dst = QtGui.QPolygonF(quad)
        t = QtGui.QTransform()
        if not QtGui.QTransform.quadToQuad(src, dst, t):
            return None
        return t

    def _knife(self, p: QtGui.QPainter, quad: list) -> None:
        xs = [q.x() for q in quad]
        ys = [q.y() for q in quad]
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtGui.QPen(self._sq["accent"], 2.0))
        if max(xs) - min(xs) < max(ys) - min(ys):
            x = (min(xs) + max(xs)) * 0.5
            p.drawLine(QtCore.QPointF(x, min(ys)), QtCore.QPointF(x, max(ys)))
        else:
            y = (min(ys) + max(ys)) * 0.5
            p.drawLine(QtCore.QPointF(min(xs), y), QtCore.QPointF(max(xs), y))
        p.restore()

    def _lerp(self, a: QtGui.QColor, b: QtGui.QColor, k: float) -> QtGui.QColor:
        k = max(0.0, min(1.0, k))
        return QtGui.QColor(
            int(a.red() + (b.red() - a.red()) * k),
            int(a.green() + (b.green() - a.green()) * k),
            int(a.blue() + (b.blue() - a.blue()) * k))

    # ---- 画
    def paint(self, p: QtGui.QPainter) -> None:
        f = self.flip
        if f <= 0.0:
            front, ang = True, 0.0
        elif f < 0.5:
            front, ang = True, f * math.pi
        else:
            front, ang = False, (f - 1.0) * math.pi    # -90 度 -> 0 度
        quad = self._project(ang)

        xs = [q.x() for q in quad]
        ys = [q.y() for q in quad]
        if min(max(xs) - min(xs), max(ys) - min(ys)) < 4.0:
            # 正好侧立的那一两帧：面已经退化没了，只剩"刀锋"，给它一条亮线
            self._knife(p, quad)
            return

        t = self._transform(quad)
        if t is None:
            return

        r = self.rect()
        face = QtCore.QRectF(0.0, 0.0, r.width(), r.height())
        self._sq = self._sq_idle if front else self._sq_search
        self._cosv = abs(math.cos(ang))   # 面朝向观众的"正对度"：接近 0 就是侧立

        if abs(math.sin(ang)) < 1e-3:
            # 平放这一档（静止、翻完）走矢量：字是原生渲染的，一旦排进图再贴出去就要重采样，字会发虚
            p.save()
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            d = max(1.0, float(self.dpr))
            # 落点对齐物理像素：1280 宽里按钮边正好落在 0.5px 上，描边会糊成两像素
            p.translate(round(quad[0].x() * d) / d, round(quad[0].y() * d) / d)
            self._draw_face(p, face, front, ang)
            p.restore()
            return

        img = self._face_image(face, front, ang)
        p.save()
        # 翻动中的帧排成图再整张贴：带透视的画家会把圆角路径摊成多边形，裁剪出来就是锯齿
        p.setTransform(t, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.drawImage(QtCore.QRectF(0.0, 0.0, face.width(), face.height()), img)
        p.restore()

    def _face_image(self, face: QtCore.QRectF, front: bool, ang: float) -> QtGui.QImage:
        d = max(1.0, float(self.dpr))
        w = max(1, int(round(face.width() * d)))
        h = max(1, int(round(face.height() * d)))
        img = self._img
        if img is None or img.width() != w or img.height() != h:
            img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
            self._img = img
        img.fill(QtCore.Qt.transparent)

        q = QtGui.QPainter(img)
        q.setRenderHint(QtGui.QPainter.Antialiasing, True)
        q.scale(d, d)           # 图按物理像素排，之后仍然用逻辑坐标画
        self._draw_face(q, face, front, ang)
        q.end()
        return img

    def _draw_face(self, q: QtGui.QPainter, face: QtCore.QRectF,
                   front: bool, ang: float) -> None:
        path = self._face_path(face)
        # 面内容必须裁在面上：斜纹/扫光漏出去会被透视甩到按钮外
        q.setClipPath(path)

        # 面：竖直渐变，悬停时更亮
        hv = self.hover_amt
        g = QtGui.QLinearGradient(0, 0, 0, face.height())
        g.setColorAt(0.0, self._lerp(self._sq["face_top"], self._sq["face_top_hover"], hv))
        g.setColorAt(1.0, self._lerp(self._sq["face_bot"], self._sq["face_bot_hover"], hv))
        q.fillPath(path, g)

        if front:
            self._front(q, face, hv)
        else:
            self._back(q, face)

        # 侧过去时压暗：|sin| 越大越暗，翻转才有"转过去"的体积感
        shade = int(min(150.0, abs(math.sin(ang)) * 150.0))
        if shade > 0:
            q.fillPath(path, QtGui.QColor(0, 0, 0, shade))

        edge = self._lerp(self._sq["edge"], self._sq["edge_hot"], hv)
        pen = QtGui.QPen(edge)
        pen.setWidthF(1.2)
        q.setPen(pen)
        q.setBrush(QtCore.Qt.NoBrush)
        q.drawPath(self._face_path(face, 0.8))

    def _text_alpha(self) -> float:
        # 快侧过来时文字会被压成一条糊线，干脆淡掉，比糊着好看
        return max(0.0, min(1.0, (getattr(self, "_cosv", 1.0) - 0.15) / 0.30))

    def _front(self, p: QtGui.QPainter, face: QtCore.QRectF, hv: float) -> None:
        # 左侧竖条：悬停时点亮（CS2 那根勾边）
        p.fillRect(QtCore.QRectF(0.0, 0.0, 4.0, face.height()),
                   self._lerp(self._sq["edge"], self._sq["accent"], hv))

        # 扫光：悬停时斜着扫过，一趟 sweep_ms
        if hv > 0.02:
            prog = (self.t * 1000.0 / max(1.0, self.p.sweep_ms)) % 1.0
            x = -0.35 * face.width() + prog * 1.7 * face.width()
            g = QtGui.QLinearGradient(x - 110.0, 0.0, x + 110.0, face.height())
            a = int(80 * hv)
            g.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
            g.setColorAt(0.5, QtGui.QColor(255, 255, 255, a))
            g.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            p.fillRect(face, g)

        ta = self._text_alpha()
        f = QtGui.QFont("Microsoft YaHei")
        f.setPixelSize(30)
        f.setBold(True)
        p.setFont(f)
        tc = QtGui.QColor(self._sq["title"])
        tc.setAlpha(int(255 * ta))
        p.setPen(tc)
        p.drawText(face.adjusted(0, -6, 0, -6), QtCore.Qt.AlignCenter, "开始匹配")

        f2 = QtGui.QFont("Consolas")
        f2.setPixelSize(11)
        f2.setBold(True)
        f2.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 2.0)
        p.setFont(f2)
        sc = QtGui.QColor(self._sq["sub"])
        sc.setAlpha(int(210 * ta))
        p.setPen(sc)
        p.drawText(face.adjusted(0, 0, 0, -12),
                   QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom, "SOLO  QUEUE")

    def _back(self, p: QtGui.QPainter, face: QtCore.QRectF) -> None:
        # 斜纹在流：搜索态的骨架
        stripes = QtGui.QColor(self._sq["accent"])
        stripes.setAlpha(26)
        pen = QtGui.QPen(stripes)
        pen.setWidthF(6.0)
        p.setPen(pen)
        step = 28.0
        off = (self.t * 55.0) % step
        x = -face.height() + off
        while x < face.width() + step:
            p.drawLine(QtCore.QPointF(x, face.height()),
                       QtCore.QPointF(x + face.height(), 0.0))
            x += step

        # 底部进度条：来回扫，暗示"还在找"
        u = 0.5 - 0.5 * math.cos(self.t * 2.6)
        p.fillRect(QtCore.QRectF(0.0, face.height() - 3.0,
                                 face.width() * max(0.06, u), 3.0), self._sq["accent"])

        f = QtGui.QFont("Microsoft YaHei")
        f.setPixelSize(28)
        f.setBold(True)
        p.setFont(f)
        ta = self._text_alpha()
        bt = QtGui.QColor(self._sq["title"])
        bt.setAlpha(int(255 * ta))
        p.setPen(bt)
        # 三个点单独画在右侧固定槽位：拼进同一个字符串会让整行随点数左右跳
        box = face.adjusted(0, -6, 0, -6)
        fm = QtGui.QFontMetrics(f)
        bw = fm.horizontalAdvance("搜索中")
        base_x = box.center().x() - bw * 0.5 - 9.0
        p.drawText(QtCore.QRectF(base_x, box.top(), bw, box.height()),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, "搜索中")
        p.drawText(QtCore.QRectF(base_x + bw + 2.0, box.top(), 40.0, box.height()),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                   "." * (1 + int(self.t * 3.0) % 3))

        f2 = QtGui.QFont("Consolas")
        f2.setPixelSize(11)
        f2.setBold(True)
        f2.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 2.0)
        p.setFont(f2)
        sc = QtGui.QColor(self._sq["sub"])
        sc.setAlpha(int(210 * ta))
        p.setPen(sc)
        p.drawText(face.adjusted(0, 0, 0, -12),
                   QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom, "SEARCHING")


# ---------------------------------------------------------------- 窗口

class Stage(QtWidgets.QWidget):
    def __init__(self, p: Params):
        super().__init__()
        self.p = p
        self.fx = Effect(p)
        self.setWindowTitle("特效实验场 2 - 翻转按钮")
        self.resize(WIN_W, WIN_H)
        self.setStyleSheet("background:" + BG + ";")
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self._last = time.perf_counter()
        self._paint_ms = 0.0
        self._step_ms = 0.0
        self._fps = 0.0
        self._fps_at = time.perf_counter()
        self._frames = 0
        self.pre = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.setInterval(max(1, round(1000.0 / p.fps)))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def resizeEvent(self, e):
        self.fx.reset(self.width(), self.height())
        super().resizeEvent(e)

    def mouseMoveEvent(self, e):
        self.fx.mouse = QtCore.QPointF(e.position())

    def mousePressEvent(self, e):
        pos = e.position()
        self.fx.mouse = QtCore.QPointF(pos)
        self.fx.click(pos.x(), pos.y())

    def keyPressEvent(self, e):
        # 手感靠现场拧：只改参数不重建任何东西，下一帧就生效
        k = e.key()
        p = self.p
        if k == QtCore.Qt.Key_Escape:
            self.close()
        elif k in (QtCore.Qt.Key_Space, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.fx.toggle()
        elif k in (QtCore.Qt.Key_A, QtCore.Qt.Key_X):
            p.axis = "x" if p.axis == "y" else "y"
        elif k == QtCore.Qt.Key_Left:
            p.flip_ms = max(120.0, p.flip_ms - 20.0)
        elif k == QtCore.Qt.Key_Right:
            p.flip_ms = min(900.0, p.flip_ms + 20.0)
        elif k == QtCore.Qt.Key_Up:
            p.over = min(2.5, round(p.over + 0.1, 2))
        elif k == QtCore.Qt.Key_Down:
            p.over = max(0.0, round(p.over - 0.1, 2))
        elif k == QtCore.Qt.Key_Comma:
            p.cam = max(300.0, p.cam - 100.0)
        elif k == QtCore.Qt.Key_Period:
            p.cam = min(4000.0, p.cam + 100.0)
        elif k == QtCore.Qt.Key_Minus:
            p.rad = max(0.0, p.rad - 2.0)
        elif k == QtCore.Qt.Key_Equal:
            p.rad = min(48.0, p.rad + 2.0)
        elif k == QtCore.Qt.Key_P:
            self.pre = (self.pre + 1) % len(PRESETS)
            apply_preset(p, self.pre)
        elif k == QtCore.Qt.Key_F:
            p.tip = -p.tip
        elif k == QtCore.Qt.Key_R:
            fresh = Params()
            for fld in fresh.__dataclass_fields__:
                setattr(p, fld, getattr(fresh, fld))
            self.fx._recolor()
            self.fx.reset(self.width(), self.height())

    def _tick(self):
        now = time.perf_counter()
        dt = now - self._last
        self._last = now
        t0 = time.perf_counter()
        self.fx.step(dt)
        self._step_ms = self._step_ms * 0.9 + (time.perf_counter() - t0) * 1000.0 * 0.1
        self.update()

    def paintEvent(self, e):
        t0 = time.perf_counter()
        p = QtGui.QPainter(self)
        # WA_OpaquePaintEvent 说了"我自己涂满"，就必须真涂：不涂则上一帧留着，翻起来全是残影
        p.fillRect(self.rect(), QtGui.QColor(BG))
        self.fx.dpr = self.devicePixelRatioF()
        self.fx.paint(p)
        p.end()

        n = time.perf_counter()
        self._paint_ms = self._paint_ms * 0.9 + (n - t0) * 1000.0 * 0.1
        self._frames += 1
        if n - self._fps_at >= 0.5:
            self._fps = self._frames / (n - self._fps_at)
            self._fps_at = n
            self._frames = 0

        p = self.p
        hud = QtGui.QPainter(self)
        hud.setFont(QtGui.QFont("Consolas", 10))
        hud.setPen(QtGui.QColor(120, 130, 150))
        hud.drawText(12, self.height() - 52,
                     "fps %.0f   paint %.2fms   step %.2fms   axis %s   hover %s"
                     % (self._fps, self._paint_ms, self._step_ms, p.axis,
                        "on" if self.fx.hover else "off"))
        hud.setPen(QtGui.QColor(150, 200, 235))
        hud.drawText(12, self.height() - 34,
                     "flip %dms   over %.2f   sink %.2f   cam %.0f   rad %.0f   tip %+d   [%s]"
                     % (p.flip_ms, p.over, p.sink, p.cam, p.rad, int(p.tip), PRESETS[self.pre][0]))
        hud.setPen(QtGui.QColor(95, 105, 125))
        hud.drawText(12, self.height() - 14,
                     "空格 翻转   P 手感   X 翻轴   F 翻向   <- -> 时长   ^ v 过冲   , . 透视   - = 圆角   R 复位")
        hud.end()


# ---------------------------------------------------------------- 离屏

def _render(fx: Effect, w: int, h: int, dt: float) -> QtGui.QImage:
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtGui.QColor(BG))
    fx.dpr = 1.0
    fx.step(dt)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    fx.paint(p)
    ok = p.end()
    assert ok, "paint 里忘了 p.end() 或抛过异常"
    return img


def _prepare(p: Params, w: int, h: int) -> Effect:
    fx = Effect(p)
    fx.reset(w, h)
    fx.mouse = QtCore.QPointF(w / 2.0, h / 2.0)     # 停在按钮上，悬停态常开
    fx.hover_amt = 1.0
    return fx


def bench(frames: int, p: Params) -> int:
    fx = _prepare(p, WIN_W, WIN_H)
    if p.auto <= 0.0:
        p.auto = 1.2
    dt = 1.0 / 60.0
    for _ in range(30):
        _render(fx, WIN_W, WIN_H, dt)

    ts = []
    for _ in range(frames):
        t0 = time.perf_counter()
        _render(fx, WIN_W, WIN_H, dt)
        ts.append((time.perf_counter() - t0) * 1000.0)

    ts.sort()
    avg = sum(ts) / len(ts)
    print("frames %d   min %.2fms   avg %.2fms   p95 %.2fms   max %.2fms"
          % (len(ts), ts[0], avg, ts[int(len(ts) * 0.95)], ts[-1]))
    print("verdict: %s (240Hz budget 4.10ms)"
          % ("OK" if avg <= 4.1 else ("borderline" if avg <= 6.9 else "TOO SLOW")))
    return 0


def shot(path: str, p: Params, steps: int, w: int, h: int) -> int:
    fx = _prepare(p, w, h)
    if p.auto <= 0.0:
        p.auto = 1.6
    img = None
    for _ in range(max(1, steps)):
        img = _render(fx, w, h, 1.0 / 60.0)
    if not img.save(path):
        print("save failed: " + path)
        return 1
    print("saved " + path + "  %dx%d  steps %d  flip %.2f"
          % (w, h, steps, fx.flip))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CS2 式翻转按钮")
    ap.add_argument("--bench", type=int, default=0, metavar="N")
    ap.add_argument("--shot", default="", metavar="PNG")
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--size", default="%dx%d" % (WIN_W, WIN_H))
    ap.add_argument("--axis", default="", choices=["", "x", "y"])
    ap.add_argument("--auto", type=float, default=0.0, metavar="SEC")
    ap.add_argument("--cam", type=float, default=0.0)
    ap.add_argument("--flip-ms", type=float, default=0.0)
    ap.add_argument("--flip", type=float, default=-1.0, metavar="0..1",
                    help="把翻转角钉死（0=正面, 0.5=侧立, 1=背面），截图用")
    args = ap.parse_args(argv)

    w, h = WIN_W, WIN_H
    if "x" in args.size:
        a, b = args.size.lower().split("x", 1)
        w, h = int(a), int(b)

    p = Params()
    if args.axis:
        p.axis = args.axis
    if args.auto > 0:
        p.auto = args.auto
    if args.cam > 0:
        p.cam = args.cam
    if args.flip_ms > 0:
        p.flip_ms = args.flip_ms
    if args.flip >= 0.0:
        p.flip_lock = args.flip

    # 离屏跑也必须有 QGuiApplication：画文字要字体表，没有它 QFontDatabase 直接罢工
    if args.bench > 0 or args.shot:
        # 不设 QT_QPA_PLATFORM=offscreen：那个平台插件在 Windows 上没有字体表，
        # 截出来的图里文字是空的。默认 windows 平台不建窗口也能画 QImage。
        QtGui.QGuiApplication.instance() or QtGui.QGuiApplication(sys.argv[:1])
        if args.bench > 0:
            return bench(args.bench, p)
        return shot(args.shot, p, args.steps, w, h)

    app = QtWidgets.QApplication(sys.argv[:1])
    stage = Stage(p)
    stage.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

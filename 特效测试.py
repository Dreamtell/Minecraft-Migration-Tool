"""特效测试 —— 从 0 造一个可调参的粒子 / 发光 / 拖尾试验场。

用法：
    python 特效测试.py                  # 开窗口玩
    python 特效测试.py --preset 极光
    python 特效测试.py --bench 240      # 不开窗口跑 240 帧，打印帧时统计
    python 特效测试.py --shot out.png   # 渲染一帧存成图片（用来贴图/比对）

窗口里：
    鼠标移动 = 发射源，按下 = 爆一发，空格暂停，C 清屏，1..5 切预设。
    三种模式（面板“模式”下拉、命令行 --mode）：
        喷泉 = 自由粒子从发射点喷出（星尘 / 火焰 / 极光 / 电涌）
        流场 = 一片有“家”的网格点，被鼠标推开又靠弹簧弹回，越快越亮（流体）
        飘落 = 整页飘落的樱花 / 雪 / 星火（CSDN 博客背景特效那一类，博主开启才看得到）
    右侧滑块实时改参数；不写任何配置文件 —— 这是试验场，不是产品。

渲染思路（为什么这么画）：
    所有粒子先画进一张与窗口同尺寸的 QImage「画布」：每帧先给整张画布盖一层
    半透明黑让历史衰减，再用 CompositionMode_Plus 把发光贴图加色叠上去。
    好处是拖尾就是画布自己的记忆，不需要每个粒子存一串轨迹点；加色叠加会让
    重叠处自然变亮，发光不用额外做模糊。贴图（_sprite）按色相预渲染成 64×64
    的径向渐变并缓存，粒子只是缩放这张贴图，所以上千粒子也不会每帧现算渐变。
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from dataclasses import dataclass, replace

from PySide6 import QtCore, QtGui, QtWidgets

GS = 64                 # 发光贴图边长（像素）
MAX_PARTICLES = 6000    # 上限，防止拖尾参数调太狠把内存/帧率拖死
MAX_SPAWN_PER_FRAME = 300
MAX_GRID = 4200         # 流场点数上限：实测 6420 点 1080p 要 21~23ms（约 45fps），
                        # 压到 4200 才回到 60fps；1180x720（3744 点）本身是 11.1ms
MAX_FALL = 4000         # 飘落数量上限
FADE_IN = 0.12          # 出生后多久到达满亮度（秒）


# ---------------------------------------------------------------- 参数与预设

@dataclass
class Params:
    name: str = "星尘"
    emission: float = 900.0     # 每秒发射个数
    speed: float = 90.0         # 初速 px/s
    lifetime: float = 2.4       # 寿命 秒
    size: float = 14.0          # 绘制半径基准 px
    spread: float = 40.0        # 发射点抖动半径 px（全挤在一个点会立刻过曝成白团）
    gravity: float = -30.0      # 正数向下，负数上浮
    drag: float = 0.6           # 指数阻尼系数
    twist: float = 0.8          # 湍流场强度
    hue: float = 200.0          # 基准色相
    hue_spread: float = 60.0    # 色相随机宽度
    sat: float = 0.85
    glow: float = 0.85          # 叠加强度
    trail: float = 0.90         # 每帧保留比例，越大拖尾越长
    mode: str = "喷泉"          # 喷泉 = 自由粒子；流场 = 会被鼠标推开又弹回的网格
    repel: float = 5200.0       # 流场：鼠标斥力
    radius: float = 220.0       # 流场：作用半径 px
    spring: float = 18.0        # 流场：回弹劲度（越小回得越慢、形变越大）
    swirl: float = 2400.0       # 流场：切向涡旋（正负决定转向）
    spacing: float = 15.0       # 流场：网格间距 px
    shape: str = "花瓣"         # 飘落：花瓣 / 雪 / 星
    count: float = 260.0        # 飘落：同屏最大数量
    fall: float = 70.0          # 飘落：下落速度 px/s
    wind: float = 25.0          # 飘落：横向风速 px/s
    sway: float = 28.0          # 飘落：左右摇摆幅度
    spin: float = 35.0          # 飘落：自转速度 deg/s


PRESETS = {
    "星尘": Params(),
    "火焰": Params(name="火焰", emission=1100, speed=110, lifetime=1.4, size=16, spread=34,
                  gravity=-140, drag=0.9, twist=1.1, hue=28, hue_spread=30,
                  sat=0.95, glow=0.95, trail=0.86),
    "极光": Params(name="极光", emission=700, speed=170, lifetime=3.2, size=22, spread=64,
                  gravity=-10, drag=0.35, twist=2.6, hue=170, hue_spread=110,
                  sat=0.8, glow=0.7, trail=0.94),
    "电涌": Params(name="电涌", emission=1800, speed=260, lifetime=0.9, size=9, spread=24,
                  gravity=120, drag=1.6, twist=0.3, hue=280, hue_spread=90,
                  sat=0.9, glow=1.0, trail=0.75),
    "流体": Params(name="流体", mode="流场", hue=190, hue_spread=150, sat=0.75,
                  glow=0.95, trail=0.90, repel=5200, radius=220, spring=18,
                  swirl=2400, spacing=15),
    "樱花": Params(name="樱花", mode="飘落", shape="花瓣", hue=340, hue_spread=26,
                  sat=0.55, glow=1.0, trail=0.0, count=260, fall=70, wind=26,
                  sway=30, spin=40, size=15),
    "雪": Params(name="雪", mode="飘落", shape="雪", hue=205, hue_spread=20,
                sat=0.15, glow=1.0, trail=0.0, count=430, fall=95, wind=12,
                sway=18, spin=22, size=9),
    "星火": Params(name="星火", mode="飘落", shape="星", hue=45, hue_spread=120,
                  sat=0.9, glow=1.0, trail=0.82, count=150, fall=45, wind=14,
                  sway=34, spin=0, size=10),
}


def preset_params(name: str | None) -> Params:
    if not name:
        return replace(PRESETS["星尘"])
    return replace(PRESETS.get(name, PRESETS["星尘"]))


def resolve_params(preset: str | None, mode: str | None) -> Params:
    """命令行给 --mode 时，用“流体”的网格参数打底，但保留所选预设的调色。"""
    p = preset_params(preset)
    if mode:
        p.mode = mode
        if mode == "流场" and p.name != "流体":
            for attr in ("repel", "radius", "spring", "swirl", "spacing"):
                setattr(p, attr, getattr(PRESETS["流体"], attr))
        elif mode == "飘落" and p.name not in ("樱花", "雪", "星火"):
            for attr in ("shape", "count", "fall", "wind", "sway", "spin", "size", "trail"):
                setattr(p, attr, getattr(PRESETS["樱花"], attr))
    return p


# ---------------------------------------------------------------- 发光贴图缓存

_SPRITE_CACHE: dict[tuple[int, int], QtGui.QPixmap] = {}


def _sprite(hue: float, sat: float) -> QtGui.QPixmap:
    key = (int(hue) % 360 // 6, int(max(0.0, min(1.0, sat)) * 10))
    hit = _SPRITE_CACHE.get(key)
    if hit is not None:
        return hit
    base = QtGui.QColor.fromHsvF((key[0] * 6 % 360) / 360.0, key[1] / 10.0, 1.0)
    pm = QtGui.QPixmap(GS, GS)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    grad = QtGui.QRadialGradient(GS / 2, GS / 2, GS / 2)
    core = QtGui.QColor(base)
    core.setAlpha(150)
    mid = QtGui.QColor(base)
    mid.setAlpha(38)
    edge = QtGui.QColor(base)
    edge.setAlpha(0)
    grad.setColorAt(0.0, core)
    grad.setColorAt(0.34, mid)
    grad.setColorAt(1.0, edge)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QBrush(grad))
    p.drawEllipse(QtCore.QRectF(0, 0, GS, GS))
    p.end()
    _SPRITE_CACHE[key] = pm
    return pm


# ---------------------------------------------------------------- 飘落形态贴图

_SHAPE_CACHE: dict[tuple, QtGui.QPixmap] = {}


def _shape_sprite(shape: str, hue: float, sat: float) -> QtGui.QPixmap:
    """花瓣/雪/星各预渲染一张 64x64 贴图，之后只做旋转缩放，不重画曲线。"""
    key = (shape, int(hue) % 360 // 10, int(max(0.0, min(1.0, sat)) * 10))
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    base = QtGui.QColor.fromHsvF((key[1] * 10 % 360) / 360.0, key[2] / 10.0, 1.0)
    pm = QtGui.QPixmap(GS, GS)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setPen(QtCore.Qt.NoPen)
    if shape == "花瓣":
        path = QtGui.QPainterPath()
        path.moveTo(GS / 2, GS * 0.08)
        path.cubicTo(GS * 0.92, GS * 0.26, GS * 0.88, GS * 0.80, GS / 2, GS * 0.92)
        path.cubicTo(GS * 0.12, GS * 0.80, GS * 0.08, GS * 0.26, GS / 2, GS * 0.08)
        grad = QtGui.QLinearGradient(GS / 2, GS * 0.08, GS / 2, GS * 0.92)
        # 注意：QColor.lighter() 会顺手压低饱和度，粉色会被洗成灰紫；直接在 HSV 上捏
        hh, ss, vv, _ = base.getHsvF()
        light = QtGui.QColor.fromHsvF(hh, ss * 0.45, 1.0)
        light.setAlpha(250)
        deep = QtGui.QColor.fromHsvF(hh, min(1.0, ss * 1.25), vv * 0.88)
        deep.setAlpha(230)
        grad.setColorAt(0.0, light)
        grad.setColorAt(1.0, deep)
        p.setBrush(QtGui.QBrush(grad))
        p.drawPath(path)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 70), 1.4))
        p.drawLine(QtCore.QPointF(GS / 2, GS * 0.16), QtCore.QPointF(GS / 2, GS * 0.86))
    elif shape == "雪":
        grad = QtGui.QRadialGradient(GS / 2, GS / 2, GS / 2)
        grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 240))
        grad.setColorAt(0.42, QtGui.QColor(214, 236, 255, 95))
        grad.setColorAt(1.0, QtGui.QColor(214, 236, 255, 0))
        p.setBrush(QtGui.QBrush(grad))
        p.drawEllipse(QtCore.QRectF(0, 0, GS, GS))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 80), 1.6))
        for i in range(3):
            ang = math.pi * i / 3
            dx, dy = math.cos(ang) * GS * 0.40, math.sin(ang) * GS * 0.40
            p.drawLine(QtCore.QPointF(GS / 2 - dx, GS / 2 - dy),
                       QtCore.QPointF(GS / 2 + dx, GS / 2 + dy))
    else:   # 星：加色叠加，中间一个亮核 + 一道十字芒
        grad = QtGui.QRadialGradient(GS / 2, GS / 2, GS / 2)
        core = QtGui.QColor(base)
        core.setAlpha(240)
        mid = QtGui.QColor(base)
        mid.setAlpha(48)
        edge = QtGui.QColor(base)
        edge.setAlpha(0)
        grad.setColorAt(0.0, core)
        grad.setColorAt(0.30, mid)
        grad.setColorAt(1.0, edge)
        p.setBrush(QtGui.QBrush(grad))
        p.drawEllipse(QtCore.QRectF(0, 0, GS, GS))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 150), 1.2))
        p.drawLine(QtCore.QPointF(GS / 2, GS * 0.16), QtCore.QPointF(GS / 2, GS * 0.84))
        p.drawLine(QtCore.QPointF(GS * 0.16, GS / 2), QtCore.QPointF(GS * 0.84, GS / 2))
    p.end()
    _SHAPE_CACHE[key] = pm
    return pm


# ---------------------------------------------------------------- 粒子

class Particle:
    __slots__ = ("x", "y", "vx", "vy", "age", "life", "size", "hue")

    def __init__(self, x, y, vx, vy, life, size, hue):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.age = 0.0
        self.life = life
        self.size = size
        self.hue = hue


class GridPoint:
    """流场里的一颗：自己有家（hx,hy），被推开后靠弹簧回家。"""
    __slots__ = ("x", "y", "vx", "vy", "hx", "hy")

    def __init__(self, hx, hy):
        self.hx = hx
        self.hy = hy
        self.x = hx
        self.y = hy
        self.vx = 0.0
        self.vy = 0.0


class Fall:
    """飘落的一片：depth 决定大小/速度/透明度，让画面有前后景深。"""
    __slots__ = ("x", "y", "vy", "rot", "spin", "size", "hue", "phase", "depth")

    def __init__(self, x, y, vy, size, hue, phase, depth):
        self.x = x
        self.y = y
        self.vy = vy
        self.size = size
        self.hue = hue
        self.phase = phase
        self.depth = depth
        self.rot = phase * 40.0
        self.spin = 0.0


# ---------------------------------------------------------------- 画布控件

class FxField(QtWidgets.QWidget):
    statsChanged = QtCore.Signal(float, int)

    def __init__(self, params: Params, autotick: bool = True, parent=None):
        super().__init__(parent)
        self._p = params
        self._parts: list[Particle] = []
        self._grid: list[GridPoint] = []
        self._fall: list[Fall] = []
        self._grid_key: tuple | None = None
        self._buffer = QtGui.QImage()
        self._t = 0.0
        self._emit_acc = 0.0
        self._mouse: QtCore.QPointF | None = None
        self._paused = False
        self._rng = random.Random(20240607)
        self._hud_font = QtGui.QFont("Consolas", 9)
        self._fps = 0.0
        self._last = time.perf_counter()
        self._stat_t = 0.0
        self.setMouseTracking(True)
        self.setMinimumSize(420, 300)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        if autotick:
            self._timer = QtCore.QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(16)

    # -- 对外
    @property
    def params(self) -> Params:
        return self._p

    def set_params(self, params: Params) -> None:
        self._p = params

    def particle_count(self) -> int:
        if self._p.mode == "流场":
            return len(self._grid)
        if self._p.mode == "飘落":
            return len(self._fall)
        return len(self._parts)

    def clear(self) -> None:
        self._parts.clear()
        self._grid.clear()
        self._grid_key = None
        self._fall.clear()
        self._fill_buffer(QtCore.Qt.black)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def burst(self, pos: QtCore.QPointF, count: int = 150) -> None:
        p = self._p
        for i in range(count):
            a = math.tau * i / count + self._rng.uniform(-0.05, 0.05)
            sp = p.speed * self._rng.uniform(0.8, 2.2)
            self._parts.append(Particle(
                pos.x() + self._rng.uniform(-p.spread, p.spread),
                pos.y() + self._rng.uniform(-p.spread, p.spread),
                math.cos(a) * sp, math.sin(a) * sp,
                p.lifetime * self._rng.uniform(0.6, 1.2),
                p.size * self._rng.uniform(0.7, 1.4),
                p.hue + self._rng.uniform(-p.hue_spread / 2, p.hue_spread / 2),
            ))

    # -- 每帧
    def step(self, dt: float) -> None:
        if dt <= 0:
            return
        self._ensure_buffer()
        if not self._paused:
            self._t += dt
            if self._p.mode == "流场":
                self._rebuild_grid()
                self._step_grid(dt)
            elif self._p.mode == "飘落":
                self._step_fall(dt)
            else:
                self._emit(dt)
                self._advance(dt)
        self._draw()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self.step(dt)
        self._fps = self._fps * 0.9 + (1.0 / dt if dt > 0 else 0.0) * 0.1
        self._stat_t += dt
        if self._stat_t > 0.25:
            self._stat_t = 0.0
            self.statsChanged.emit(self._fps, self.particle_count())
        self.update()

    def _emit(self, dt: float) -> None:
        if len(self._parts) >= MAX_PARTICLES:
            return
        self._emit_acc += max(0.0, self._p.emission) * dt
        n = min(MAX_SPAWN_PER_FRAME, int(self._emit_acc), MAX_PARTICLES - len(self._parts))
        self._emit_acc -= n
        if n <= 0:
            return
        origin = self._emitter_pos()
        p = self._p
        rng = self._rng
        for _ in range(n):
            a = rng.uniform(0, math.tau)
            sp = p.speed * (0.25 + rng.random() ** 0.6)
            self._parts.append(Particle(
                origin.x() + rng.uniform(-p.spread, p.spread),
                origin.y() + rng.uniform(-p.spread, p.spread),
                math.cos(a) * sp, math.sin(a) * sp,
                p.lifetime * rng.uniform(0.7, 1.2),
                p.size * rng.uniform(0.6, 1.3),
                p.hue + rng.uniform(-p.hue_spread / 2, p.hue_spread / 2),
            ))

    def _emitter_pos(self) -> QtCore.QPointF:
        if self._mouse is not None:
            return self._mouse
        w, h = self.width(), self.height()
        t = self._t
        return QtCore.QPointF(w * 0.5 + math.cos(t * 0.9) * w * 0.28,
                              h * 0.5 + math.sin(t * 1.3) * h * 0.28)

    def _advance(self, dt: float) -> None:
        p = self._p
        t = self._t
        damp = math.exp(-max(0.0, p.drag) * dt)
        alive: list[Particle] = []
        for q in self._parts:
            q.age += dt
            if q.age >= q.life:
                continue
            # 廉价湍流：用两个正弦拼出一个随时间转动的方向场，够飘，不建噪声表
            ang = math.sin(q.x * 0.012 + t * 0.9) + math.cos(q.y * 0.017 - t * 0.7)
            ax = math.cos(ang * 2.0) * p.twist * 220.0
            ay = math.sin(ang * 2.0) * p.twist * 220.0 + p.gravity
            q.vx = (q.vx + ax * dt) * damp
            q.vy = (q.vy + ay * dt) * damp
            q.x += q.vx * dt
            q.y += q.vy * dt
            if -200 < q.x < self.width() + 200 and -200 < q.y < self.height() + 200:
                alive.append(q)
        self._parts = alive

    def _draw(self) -> None:
        if self._buffer.isNull():
            return
        p = self._p
        painter = QtGui.QPainter(self._buffer)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        fade = int((1.0 - max(0.0, min(0.985, p.trail))) * 255)
        painter.fillRect(self._buffer.rect(), QtGui.QColor(0, 0, 0, max(2, fade)))
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        src = QtCore.QRectF(0, 0, GS, GS)
        glow = max(0.0, min(1.5, p.glow))
        if p.mode == "流场":
            self._paint_grid(painter, src, glow)
        elif p.mode == "飘落":
            self._paint_fall(painter, src, glow)
        else:
            self._paint_particles(painter, src, glow)
        painter.end()

    def _paint_particles(self, painter, src, glow) -> None:
        p = self._p
        for q in self._parts:
            life_frac = 1.0 - q.age / q.life
            alpha = life_frac * min(1.0, q.age / FADE_IN)
            if alpha <= 0.01:
                continue
            r = q.size * (0.55 + 0.45 * life_frac)
            painter.setOpacity(min(1.0, alpha * glow * 0.45))
            painter.drawPixmap(QtCore.QRectF(q.x - r, q.y - r, r * 2, r * 2),
                               _sprite(q.hue, p.sat), src)

    # -- 流场：一片“有家”的点，被鼠标推开再弹回来
    def _rebuild_grid(self) -> None:
        w, h = self.width(), self.height()
        spacing = max(6.0, self._p.spacing)
        cols = max(1, int(w / spacing))
        rows = max(1, int(h / spacing))
        if cols * rows > MAX_GRID:      # 间距调太小就别硬画，等比放大间距
            spacing *= math.sqrt(cols * rows / MAX_GRID)
            cols = max(1, int(w / spacing))
            rows = max(1, int(h / spacing))
        key = (w, h, round(spacing, 2))
        if key == self._grid_key and self._grid:
            return
        self._grid_key = key
        ox = (w - (cols - 1) * spacing) / 2
        oy = (h - (rows - 1) * spacing) / 2
        self._grid = [GridPoint(ox + i * spacing, oy + j * spacing)
                      for j in range(rows) for i in range(cols)]

    def _step_grid(self, dt: float) -> None:
        p = self._p
        t = self._t
        damp = math.exp(-2.4 * dt)
        if self._mouse is not None:
            mx, my = self._mouse.x(), self._mouse.y()
        else:
            mx = my = 1e9
        R = max(1.0, p.radius)
        R2 = R * R
        for q in self._grid:
            ax = (q.hx - q.x) * p.spring
            ay = (q.hy - q.y) * p.spring
            # 鼠标不在时也要活着：一点缓慢的呼吸波，否则整片静止像张静态网格
            ax += math.sin(t * 1.1 + q.hy * 0.02) * 90.0
            ay += math.cos(t * 0.9 + q.hx * 0.02) * 90.0
            dx = q.x - mx
            dy = q.y - my
            d2 = dx * dx + dy * dy
            if d2 < R2:
                d = math.sqrt(d2) + 1e-6
                fall = 1.0 - d / R
                fall *= fall
                push = p.repel * fall
                ax += dx / d * push - dy / d * p.swirl * fall
                ay += dy / d * push + dx / d * p.swirl * fall
            q.vx = (q.vx + ax * dt) * damp
            q.vy = (q.vy + ay * dt) * damp
            q.x += q.vx * dt
            q.y += q.vy * dt

    # -- 飘落：樱花 / 雪 / 星火，横风 + 左右摇摆 + 自转
    def _step_fall(self, dt: float) -> None:
        p = self._p
        t = self._t
        w, h = self.width(), self.height()
        rng = self._rng
        want = int(max(0.0, min(MAX_FALL, p.count)))
        while len(self._fall) < want:      # 开局整屏铺满，别让前 5 秒空荡荡
            depth = rng.random()
            self._fall.append(Fall(
                rng.uniform(-40.0, w + 40.0),
                rng.uniform(-h * 0.2, h),
                p.fall * (0.55 + 0.95 * depth),
                p.size * (0.45 + 0.85 * depth),
                p.hue + rng.uniform(-p.hue_spread / 2, p.hue_spread / 2),
                rng.uniform(0, math.tau),
                depth,
            ))
        for q in self._fall:
            q.y += q.vy * dt
            q.x += (p.wind * (0.4 + q.depth) + math.sin(t * 1.6 + q.phase) * p.sway) * dt
            q.rot += p.spin * dt * (0.4 + q.depth)
            if q.y > h + 60:
                q.y = -60.0
                q.x = rng.uniform(-40.0, w + 40.0)
            if q.x < -90:
                q.x = w + 70
            elif q.x > w + 90:
                q.x = -70

    def _paint_fall(self, painter, src, glow) -> None:
        p = self._p
        additive = p.shape == "星"
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus if additive
                                   else QtGui.QPainter.CompositionMode_SourceOver)
        sprite = None
        for q in self._fall:
            sprite = _shape_sprite(p.shape, q.hue, p.sat)
            s = q.size * 2.0
            painter.save()
            painter.translate(q.x, q.y)
            if p.spin:
                painter.rotate(q.rot)
            painter.setOpacity(min(1.0, (0.45 + 0.55 * q.depth) * glow))
            painter.drawPixmap(QtCore.QRectF(-s / 2, -s / 2, s, s), sprite, src)
            painter.restore()
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)

    def _paint_grid(self, painter, src, glow) -> None:
        p = self._p
        for q in self._grid:
            heat = min(1.0, math.hypot(q.vx, q.vy) / 300.0) ** 0.65   # 越快越亮越偏色
            r = 2.2 + 5.2 * heat
            painter.setOpacity(min(1.0, (0.10 + 0.90 * heat) * glow))
            painter.drawPixmap(QtCore.QRectF(q.x - r, q.y - r, r * 2, r * 2),
                               _sprite(p.hue + p.hue_spread * heat, p.sat), src)

    # -- 画布尺寸 / 事件
    def _fill_buffer(self, color) -> None:
        if not self._buffer.isNull():
            self._buffer.fill(color)

    def _ensure_buffer(self) -> None:
        dpr = self.devicePixelRatioF() or 1.0
        w = max(1, int(self.width() * dpr))
        h = max(1, int(self.height() * dpr))
        if self._buffer.width() != w or self._buffer.height() != h:
            img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
            img.fill(QtCore.Qt.black)
            img.setDevicePixelRatio(dpr)
            self._buffer = img

    def paint_frame(self, painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
        painter.fillRect(rect, QtCore.Qt.black)
        if not self._buffer.isNull():
            painter.drawImage(rect, self._buffer)

    def snapshot(self) -> QtGui.QImage:
        self._ensure_buffer()
        return self._buffer.copy()

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        self.paint_frame(painter, self.rect())
        painter.setPen(QtGui.QColor(120, 190, 255, 190))
        painter.setFont(self._hud_font)
        painter.drawText(12, 20, f"{self._fps:5.1f} fps   {self.particle_count():4d} 粒子   {self._p.name} · {self._p.mode}")
        painter.setPen(QtGui.QColor(255, 255, 255, 70))
        painter.drawText(12, self.height() - 12, "移动发射 · 点击爆发 · 空格暂停 · C 清屏 · 1-4 预设")
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        self._mouse = event.position()

    def leaveEvent(self, event) -> None:
        self._mouse = None

    def mousePressEvent(self, event) -> None:
        self._mouse = event.position()
        self.burst(event.position())

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == QtCore.Qt.Key_Space:
            self._paused = not self._paused
        elif key == QtCore.Qt.Key_C:
            self.clear()
        elif QtCore.Qt.Key_1 <= key <= QtCore.Qt.Key_8:
            name = list(PRESETS)[key - QtCore.Qt.Key_1]
            win = self.window()
            if isinstance(win, MainWindow):
                win.apply_preset(name)
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------- 右侧控制面板

# (属性名, 标签, 最小值, 最大值, 滑块放大倍数)
SLIDERS = [
    ("emission", "发射率", 0.0, 3000.0, 1.0),
    ("speed", "初速", 0.0, 400.0, 1.0),
    ("lifetime", "寿命", 0.2, 6.0, 100.0),
    ("size", "尺寸", 2.0, 60.0, 1.0),
    ("spread", "发射半径", 0.0, 160.0, 1.0),
    ("gravity", "重力", -400.0, 400.0, 1.0),
    ("drag", "阻尼", 0.0, 4.0, 100.0),
    ("twist", "湍流", 0.0, 4.0, 100.0),
    ("hue", "色相", 0.0, 359.0, 1.0),
    ("hue_spread", "色相宽度", 0.0, 180.0, 1.0),
    ("glow", "辉光", 5.0, 100.0, 100.0),
    ("trail", "拖尾", 0.0, 98.0, 100.0),
    ("repel", "斥力", 0.0, 8000.0, 1.0),
    ("radius", "作用半径", 20.0, 500.0, 1.0),
    ("spring", "回弹", 0.0, 120.0, 1.0),
    ("swirl", "涡旋", -3000.0, 3000.0, 1.0),
    ("spacing", "网格间距", 8.0, 44.0, 1.0),
    ("count", "飘落数量", 0.0, 1200.0, 1.0),
    ("fall", "落速", 5.0, 400.0, 1.0),
    ("wind", "风", -200.0, 200.0, 1.0),
    ("sway", "摇摆", 0.0, 120.0, 1.0),
    ("spin", "自转", 0.0, 360.0, 1.0),
]


class ControlPanel(QtWidgets.QWidget):
    presetRequested = QtCore.Signal(str)
    modeRequested = QtCore.Signal(str)
    pauseToggled = QtCore.Signal(bool)
    clearRequested = QtCore.Signal()

    def __init__(self, params: Params, parent=None):
        super().__init__(parent)
        self._p = params
        self._sliders: dict[str, QtWidgets.QSlider] = {}
        self.setFixedWidth(260)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._mode = QtWidgets.QComboBox()
        self._mode.addItems(["喷泉", "流场", "飘落"])
        self._mode.currentTextChanged.connect(self.modeRequested.emit)
        root.addWidget(QtWidgets.QLabel("模式"))
        root.addWidget(self._mode)

        self._combo = QtWidgets.QComboBox()
        self._combo.addItems(list(PRESETS))
        self._combo.currentTextChanged.connect(self.presetRequested.emit)
        root.addWidget(QtWidgets.QLabel("预设（键盘 1-5 同效）"))
        root.addWidget(self._combo)

        body = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(body)
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)
        for attr, label, lo, hi, gain in SLIDERS:
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(int(lo * gain), int(hi * gain))
            slider.valueChanged.connect(self._on_slider)
            self._sliders[attr] = slider
            form.addRow(label, slider)
        scroll = QtWidgets.QScrollArea()      # 滑块越来越多，装进滚动区
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        row = QtWidgets.QHBoxLayout()
        self._btn_pause = QtWidgets.QPushButton("暂停")
        self._btn_pause.setCheckable(True)
        self._btn_pause.toggled.connect(self._on_pause)
        btn_clear = QtWidgets.QPushButton("清屏")
        btn_clear.clicked.connect(self.clearRequested.emit)
        row.addWidget(self._btn_pause)
        row.addWidget(btn_clear)
        root.addLayout(row)

        self._lbl_stats = QtWidgets.QLabel("—")
        self._lbl_stats.setStyleSheet("color:#7fb6ff;")
        root.addWidget(self._lbl_stats)

        self.sync_from_params()

    def sync_from_params(self) -> None:
        mi = self._mode.findText(self._p.mode)
        if mi >= 0 and mi != self._mode.currentIndex():
            self._mode.blockSignals(True)
            self._mode.setCurrentIndex(mi)
            self._mode.blockSignals(False)
        for attr, _label, lo, hi, gain in SLIDERS:
            s = self._sliders[attr]
            s.blockSignals(True)
            s.setValue(int(getattr(self._p, attr) * gain))
            s.blockSignals(False)
        idx = self._combo.findText(self._p.name)
        if idx >= 0 and idx != self._combo.currentIndex():
            self._combo.blockSignals(True)
            self._combo.setCurrentIndex(idx)
            self._combo.blockSignals(False)

    def set_stats(self, fps: float, count: int) -> None:
        self._lbl_stats.setText(f"{fps:5.1f} fps   {count} 粒子")

    def _on_slider(self, _value: int) -> None:
        for attr, _label, lo, hi, gain in SLIDERS:
            if self._sliders[attr] is self.sender():
                setattr(self._p, attr, self._sliders[attr].value() / gain)
                return

    def _on_pause(self, checked: bool) -> None:
        self._btn_pause.setText("继续" if checked else "暂停")
        self.pauseToggled.emit(checked)


# ---------------------------------------------------------------- 主窗口

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, preset: str | None = None, mode: str | None = None):
        super().__init__()
        self.setWindowTitle("特效测试 · 粒子 / 发光 / 拖尾 / 流场")
        self.resize(1180, 720)
        params = resolve_params(preset, mode)
        self.field = FxField(params)
        self.panel = ControlPanel(params)
        self.panel.presetRequested.connect(self.apply_preset)
        self.panel.modeRequested.connect(self.apply_mode)
        self.panel.pauseToggled.connect(self.field.set_paused)
        self.panel.clearRequested.connect(self.field.clear)
        self.field.statsChanged.connect(self.panel.set_stats)

        central = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.field, 1)
        lay.addWidget(self.panel)
        self.setCentralWidget(central)
        self.field.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.field.setFocus()

    def apply_preset(self, name: str) -> None:
        fresh = preset_params(name)
        self.field.set_params(fresh)
        self.field.clear()
        self.panel._p = fresh
        self.panel.sync_from_params()

    def apply_mode(self, mode: str) -> None:
        """切模式时连带换一套调好的参数，否则从“喷泉”切过来会是一堆没劲的道具值。"""
        if mode == self.field.params.mode:
            return
        self.apply_preset({"流场": "流体", "飘落": "樱花"}.get(mode, "星尘"))
        self.panel.sync_from_params()


# ---------------------------------------------------------------- 无窗口模式

def _make_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def run_bench(frames: int, width: int, height: int, preset: str | None,
              mode: str | None = None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _make_app()
    field = FxField(resolve_params(preset, mode), autotick=False)
    field.resize(width, height)
    target = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
    for _ in range(30):                      # 预热：让粒子数爬到位
        field.step(1.0 / 60.0)
    times: list[float] = []
    peak = 0
    for _ in range(frames):
        t0 = time.perf_counter()
        field.step(1.0 / 60.0)
        pt = QtGui.QPainter(target)
        field.paint_frame(pt, QtCore.QRect(0, 0, width, height))
        pt.end()
        times.append((time.perf_counter() - t0) * 1000.0)
        peak = max(peak, field.particle_count())
    times.sort()
    avg = sum(times) / len(times)
    print(f"帧数 {frames}  尺寸 {width}x{height}  预设 {field.params.name}  峰值粒子 {peak}")
    print(f"平均 {avg:6.2f} ms ({1000.0 / avg:6.1f} fps)")
    print(f"中位 {times[len(times) // 2]:6.2f} ms")
    print(f"P95  {times[int(len(times) * 0.95)]:6.2f} ms")
    print(f"最差 {times[-1]:6.2f} ms")
    return 0


def run_shot(path: str, width: int, height: int, preset: str | None, warm: int,
             mode: str | None = None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _make_app()
    field = FxField(resolve_params(preset, mode), autotick=False)
    field.resize(width, height)
    if warm > 0:
        field.burst(QtCore.QPointF(width * 0.5, height * 0.5), 200)
    # 让“鼠标”沿一条李萨如曲线走：截出来的就是运动中的样子，而不是一个静止的洞
    total = max(1, warm)
    for i in range(total):
        ph = i / total * math.tau * 1.5
        field._mouse = QtCore.QPointF(width * (0.5 + 0.28 * math.sin(ph)),
                                      height * (0.5 + 0.24 * math.sin(ph * 1.7 + 1.1)))
        field.step(1.0 / 60.0)
    img = field.snapshot()
    if not img.save(path):
        print(f"保存失败：{path}")
        return 1
    print(f"已保存 {path}  ({img.width()}x{img.height()}, 粒子 {field.particle_count()})")
    return 0


# ---------------------------------------------------------------- 入口

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="粒子 / 发光 / 拖尾 特效试验场")
    ap.add_argument("--preset", default=None, help="初始预设：星尘 / 火焰 / 极光 / 电涌 / 流体")
    ap.add_argument("--mode", default=None, choices=["喷泉", "流场", "飘落"], help="渲染模式")
    ap.add_argument("--bench", type=int, default=0, metavar="N", help="无窗口跑 N 帧并打印帧时")
    ap.add_argument("--shot", default=None, metavar="PNG", help="渲染一帧存成图片")
    ap.add_argument("--warm", type=int, default=150, help="--shot 前先跑多少帧")
    ap.add_argument("--size", default="1180x720", help="无窗口模式的画布尺寸，如 1280x720")
    args = ap.parse_args(argv)
    try:
        width, height = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        print(f"--size 写法不对：{args.size}")
        return 2

    if args.bench:
        return run_bench(args.bench, width, height, args.preset, args.mode)
    if args.shot:
        return run_shot(args.shot, width, height, args.preset, args.warm, args.mode)

    app = _make_app()
    win = MainWindow(args.preset, args.mode)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

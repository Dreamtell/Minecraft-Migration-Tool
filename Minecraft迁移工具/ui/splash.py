# -*- coding: utf-8 -*-
"""启动闪屏：圆角卡片 + 旋转的绿色线框立方体。

出现方式不是淡入，而是**缩放弹出**：卡片从小长到原尺寸、路上过冲一点点再回落，
收尾时反过来（先微微一涨蓄力，再快速缩到消失）。所以整张卡片——包括标题文字——
都得能连续缩放，而 Tk 的 canvas 文字做不到，于是卡片（背景 + 圆角描边 + 文字）
交给 Pillow 画成一张图，弹出时缩这张图；立方体照旧每帧重画后贴上去。

立方体是空心线框：8 个顶点绕轴旋转 → 透视投影 → 12 条棱、每条再切若干段，
段间颜色递变并让相位随时间推进，于是颜色沿着框架流动。画面在 2 倍画布上绘制、
再缩回显示尺寸（超采样），线条边缘才是抗锯齿的。

圆角靠 Windows 的 SetWindowRgn（每帧跟着缩放走）；整套东西失败也只是没有闪屏，
不影响程序启动。
"""

import math
import random
import time
import tkinter as tk

CARD_W = 480
CARD_H = 360
CARD_RADIUS = 24

BG = "#1e1e1e"
BORDER = "#3d3d3d"
TITLE_FG = "#ffffff"
SUB_FG = "#9e9e9e"
ACCENT = "#4dd0e1"
CARD_ALPHA = 0.82       # 整窗半透明：透出后面的桌面，又不影响文字可读
                        # （Tk 的 -alpha 是整窗属性，做不到"只有背景透明、文字不透明"）

# 窗口比卡片大一圈，给弹出的过冲留余量（峰值约卡片 ×1.08）
_WIN_W = int(CARD_W * 1.12)
_WIN_H = int(CARD_H * 1.12)

# ---- 弹出 / 收起 ----
_POP_FROM = 0.66        # 弹出起始缩放
_POP_SEC = 0.30         # 弹出时长（秒）
_POP_BACK = 3.0         # easeOutBack 的回弹强度：越大冲得越猛
_OUT_SEC = 0.19         # 收起时长（秒）
_SHRUNK_AT = 0.90       # 收到这个进度就通知调用方（此时卡片只剩一小点）

# ---- 立方体几何（空心：只画 12 条棱） ----
_CUBE_V = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
           (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
_CUBE_E = [(0, 1), (1, 2), (2, 3), (3, 0),
           (4, 5), (5, 6), (6, 7), (7, 4),
           (0, 4), (1, 5), (2, 6), (3, 7)]
_CUBE_RGB = (86, 224, 128)      # 框架基色（绿）

_CUBE_PX = 210      # 立方体在屏幕上的边长（像素）
_CUBE_CX = 240      # 立方体中心在卡片坐标系里的位置
_CUBE_CY = 124
_SS = 2             # 超采样倍率：画 2 倍大再缩回来换抗锯齿（3x 要 14.7ms/帧，不划算）
_SEG = 8            # 每条棱切几段（渐变细度）
_FRAME_MS = 10      # 目标帧间隔 ≈ 95fps；_spin 按实际耗时补偿，掉帧也不会拖慢转速
# 排下一帧时的**最小延时**：这个值绝不能小。
#
# 为什么：启动时主界面还在建，app.py 靠 `root.update()` 撑着闪屏动画。Tk 的 update()
# 是"把当前所有到期事件跑完再返回"，而 Tcl 的定时器精度只有毫秒级 —— 一旦延时小到
# 1~3ms，刚排上的定时器在 update() 眼里**立刻又到期**，于是 update() 永远返回不了，
# 启动就卡在闪屏（主界面一直不显形）。实测：延时 1ms/2ms 必卡；≥4ms 才开始能返回；
# ≥10ms 时每次 update() 只处理 1 帧，节奏稳定。
# 一帧实际成本约 20ms（比 _FRAME_MS 还大），所以 delay 本来就会落到下限上，
# 真正节奏 ≈ 30ms/帧（33fps）—— 这是"能启动"和"跑满 95fps"之间的必要取舍。
_FRAME_MIN_MS = 10
_SPIN_X = 1.364     # 绕 X 轴角速度（弧度/秒）
_SPIN_Y = 2.273     # 绕 Y 轴角速度
_HUE_SPEED = 1.364  # 颜色流动速度（周期/秒）
_DIST = 4.4         # 透视距离，越小透视越强
_PARTICLES = 22     # 环绕立方体飘散的粒子数（同色系）

_TITLE_Y = 264      # 标题 / 副标题在卡片坐标系里的位置
_SUB_Y = 294

# 中文字体：优先微软雅黑，实在没有再退到黑体/宋体
_PIL_FONT_FILES = (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
                   r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc")
_FONT_CACHE = {}


def _shade(rgb, factor):
    """按亮度系数调整颜色，返回 RGB 三元组。"""
    return tuple(max(0, min(255, int(v * factor))) for v in rgb)


def _pil_font(bold, px):
    """按像素高度取一个中文字体（Tk 的 size 是磅，96dpi 下 1pt ≈ 4/3 px）。"""
    key = (bool(bold), max(6, int(px)))
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from PIL import ImageFont
    except Exception:
        return None
    files = _PIL_FONT_FILES if key[0] else _PIL_FONT_FILES[1:]
    font = None
    for path in files:
        try:
            font = ImageFont.truetype(path, key[1])
            break
        except Exception:
            font = None
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            return None
    _FONT_CACHE[key] = font
    return font


def _ease_out_back(t, back=_POP_BACK):
    """0 → 1，中途冲过 1 再回落（这就是"弹"的那一下）。"""
    t -= 1.0
    return 1.0 + (back + 1.0) * t * t * t + back * t * t


def _pop_scale(t):
    """弹出时的缩放：_POP_FROM → 1.0，峰值约 1.08。"""
    if t >= 1.0:
        return 1.0
    if t <= 0.0:
        return _POP_FROM
    return _POP_FROM + (1.0 - _POP_FROM) * _ease_out_back(t)


def _out_scale(t):
    """收起时的缩放：先微微一涨（蓄力），再快速缩到 0。"""
    if t <= 0.0:
        return 1.0
    if t >= 1.0:
        return 0.0
    if t < 0.22:
        u = t / 0.22
        return 1.0 + 0.05 * (1.0 - (1.0 - u) ** 2)
    u = (t - 0.22) / 0.78
    return 1.05 * (1.0 - u ** 2.2)


class SplashScreen:
    """无边框圆角卡片闪屏：缩放弹出，close() 时缩放收起。"""

    def __init__(self, parent, icon_path=None, title="Minecraft 整合包迁移工具",
                 subtitle="增强版 v4"):
        self._alive = True
        self._closing = False
        self._out_t = 0.0
        self._pop_t = 0.0
        self._scale = _POP_FROM
        self._on_done = None
        self._on_shrunk = None
        self._ax, self._ay = -0.50, 0.62          # 方块初始姿态
        self._hue = 0.0                            # 颜色流动相位
        self._last_img = None                      # 立方体那一张（_CUBE_PX 见方）
        self._card_photo = None                    # 卡片那张 Tk 图（缩放变化时才重传）
        self._cube_photo = None                    # 立方体那张 Tk 图（每帧更新）
        self._card_item = None
        self._cube_item = None
        self._card_shown = (0, 0)
        self._cube_shown = 0
        self._rgn_scale = None                     # 上次裁圆角用的缩放值
        self._hwnd = None
        self._card_cache = (0, 0, None)
        self._card_master = None                   # 1 倍母版（缩放目标 ≤ 卡片原尺寸时用）
        self._card_master2 = None                  # 2 倍母版（放大时用，字更锐）
        self._title = title
        self._subtitle = subtitle
        self._particles = [self._spawn_particle() for _ in range(_PARTICLES)]

        win = tk.Toplevel(parent)
        self.win = win
        self._parent = parent
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=BG)

        canvas = tk.Canvas(win, width=_WIN_W, height=_WIN_H,
                           highlightthickness=0, bd=0, bg=BG)
        canvas.pack()
        self.canvas = canvas

        # 卡片（含文字）先渲成 2 倍母版，弹出时缩它
        try:
            from PIL import Image, ImageTk
            self._card_master = self._render_card_master(1)
            self._card_master2 = self._render_card_master(2)
            # 卡片和立方体各占一个画布图元：以前是把两者合成成一张整窗 RGBA 再整体
            # 上传给 Tk，实测 14.5ms/帧（其中 Tk 上传 6.8ms，半透明像素走 Tk 的逐像素
            # 慢路径）。分开之后卡片只在缩放变化时重传，立方体那张只有 _CUBE_PX 见方。
            self._card_photo = ImageTk.PhotoImage(
                Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
            self._card_item = canvas.create_image(0, 0, anchor="nw",
                                                  image=self._card_photo)
            self._cube_photo = ImageTk.PhotoImage(
                Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
            self._cube_item = canvas.create_image(0, 0, anchor="nw",
                                                  image=self._cube_photo)
        except Exception:
            self._card_master = None
            self._card_master2 = None

        self._draw_cube()
        self._compose(self._scale)
        self._apply_round_region(self._scale)
        self._center()
        win.deiconify()
        try:
            win.attributes("-topmost", True)
            win.attributes("-alpha", CARD_ALPHA)      # 半透明背景
        except Exception:
            pass
        win.lift()
        self._last_t = time.perf_counter()
        self._spin()

    # ------------------------------------------------------------ 卡片渲染
    def _render_card_master(self, factor=2):
        """把整张卡片（背景 + 圆角描边 + 标题 + 副标题）渲成一张图。

        factor 决定母版倍率：2 倍用于放大时保持字锐，1 倍用于缩小（源小 4 倍，快得多）。
        文字必须跟着卡片一起缩放，而 Tk 的 canvas 文字没法连续缩放，所以整体走 Pillow。
        """
        from PIL import Image, ImageDraw
        w, h = CARD_W * factor, CARD_H * factor
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pad = factor
        d.rounded_rectangle([pad, pad, w - 1 - pad, h - 1 - pad],
                            radius=CARD_RADIUS * factor, fill=BG,
                            outline=BORDER, width=max(1, 2 * factor))
        ft = _pil_font(True, 16 * factor * 4 / 3)
        fs = _pil_font(False, 10 * factor * 4 / 3)
        if ft is not None:
            d.text((w // 2, int(_TITLE_Y * factor)), self._title, font=ft,
                   fill=TITLE_FG, anchor="mm")
        if fs is not None:
            d.text((w // 2, int(_SUB_Y * factor)), self._subtitle, font=fs,
                   fill=SUB_FG, anchor="mm")
        return img

    def _scaled_card(self, scale):
        """缩放后的卡片图（同一尺寸复用缓存，稳态时就不用反复缩了）。

        两张母版择近用：目标尺寸 ≤ 1 倍卡片时用 1 倍母版（480x360），否则用 2 倍母版。
        以前无论缩到多小都从 960x720 的 2 倍母版缩，弹出/收起阶段每帧要 9.2ms（读 69 万
        像素只为写十几万像素）；改用 1 倍母版后同样精度下这笔钱降到 ~2.5ms。
        """
        w = max(1, int(round(CARD_W * scale)))
        h = max(1, int(round(CARD_H * scale)))
        cw, ch, img = self._card_cache
        if cw == w and ch == h and img is not None:
            return img
        # 1 倍母版直接够用（弹出/收起的缩放范围是 0.66~1.082，过冲只有 8%），
        # 超过 1.15 倍才动 2 倍母版。实测：过冲那几帧原来落在 2 倍母版上，
        # 从 960x720 缩到 500 宽要 9~18ms/帧，换成 1 倍母版后同一帧只要 ~0.3ms。
        master = self._card_master if w <= CARD_W * 1.15 else self._card_master2
        img = master if master.size == (w, h) else master.resize((w, h), self._pil_resample())
        self._card_cache = (w, h, img)
        return img

    @staticmethod
    def _pil_resample():
        from PIL import Image
        # 缩小时用 HAMMING 而不是 LANCZOS：LANCZOS 会在边缘铺出大量半透明像素，
        # 而 Tk 把 RGBA 交给照片时对半透明像素走逐像素慢路径，半透明越多贴图越慢。
        return Image.HAMMING

    def _compose(self, scale):
        """把「缩放后的卡片」和「缩放后的立方体」分别贴到画布上。

        性能（实测每帧，25 帧平均）：旧写法 new 一张整窗 RGBA → 贴卡片 → 贴立方体 →
        整张上传给 Tk = 14.5ms（其中 Tk 上传 6.8ms、卡片缩放 4.6ms、Image.new 1.0ms）。
        现在两项分开：
        - 卡片只在"显示尺寸变了"时重新缩放 + 重传（稳态时 0 开销）
        - 立方体那张只有 _CUBE_PX 见方，复用同一个 Tk 图（同尺寸用 paste 更新，不重建）
        - 位置用 canvas.coords 挪，不重画
        """
        if self._card_photo is None or self._card_master is None:
            return
        from PIL import ImageTk
        w = max(1, int(round(CARD_W * scale)))
        h = max(1, int(round(CARD_H * scale)))
        ox, oy = (_WIN_W - w) // 2, (_WIN_H - h) // 2
        if (w, h) != self._card_shown:
            card = self._scaled_card(scale)
            self._card_photo = ImageTk.PhotoImage(card)
            self.canvas.itemconfigure(self._card_item, image=self._card_photo)
            self.canvas.coords(self._card_item, ox, oy)
            self._card_shown = (w, h)

        cube = self._last_img
        if cube is None or scale <= 0.02:
            if self._cube_shown:
                self.canvas.itemconfigure(self._cube_item, state="hidden")
                self._cube_shown = 0
            return
        px = max(1, int(round(_CUBE_PX * scale)))
        if px != cube.size[0]:
            cube = cube.resize((px, px), self._pil_resample())
        cx = ox + int(round(_CUBE_CX * scale)) - px // 2
        cy = oy + int(round(_CUBE_CY * scale)) - px // 2
        # 补丁 = 卡片上立方体那一块 + 立方体，得到一张**不透明**的图。
        # Tk 上传 RGBA 时对"半透明像素"走逐像素慢路径：实测同样 210x210，
        # 半透明立方体要 5.66ms，不透明补丁只要 0.29ms（19 倍）。
        # 补丁盖住的正是立方体那块，所以上一帧的立方体像素一并被覆盖，不用另外擦除。
        card = self._scaled_card(scale)
        patch = card.crop((cx - ox, cy - oy, cx - ox + px, cy - oy + px)).copy()
        patch.alpha_composite(cube)
        if px != self._cube_shown:
            self._cube_photo = ImageTk.PhotoImage(patch)
            self.canvas.itemconfigure(self._cube_item, image=self._cube_photo,
                                      state="normal")
            self._cube_shown = px
        else:
            self._cube_photo.paste(patch)
        # 立方体中心在卡片坐标系里是 (_CUBE_CX, _CUBE_CY)，跟着卡片一起缩放
        self.canvas.coords(self._cube_item, cx, cy)

    # -------------------------------------------------------------- 立体方块
    def _spawn_particle(self):
        """在立方体外壳附近随便取一点，让它沿径向向外飘。

        返回 [x, y, z, vx, vy, vz, 剩余寿命]（坐标是归一化空间，和顶点同一套）。
        """
        while True:
            x = random.uniform(-1.0, 1.0)
            y = random.uniform(-1.0, 1.0)
            z = random.uniform(-1.0, 1.0)
            r = math.sqrt(x * x + y * y + z * z)
            if 0.35 < r <= 1.0:
                break
        k = random.uniform(1.05, 1.35) / r        # 落在立方体外壳附近
        x, y, z = x * k, y * k, z * k
        # 速度 × 寿命 决定总飞行距离。画布的有效半径约 2.0，
        # 所以把总距离压在 1.0 以内，粒子飘出去就该淡出重生了。
        speed = random.uniform(0.012, 0.028)
        return [x, y, z, x * speed, y * speed, z * speed,
                random.uniform(0.7, 1.3)]

    def _draw_cube(self):
        """旋转 → 透视 → 超采样渲染成抗锯齿的绿色线框（结果放在 self._last_img）。"""
        try:
            from PIL import Image, ImageDraw
        except Exception:
            return

        ca, sa = math.cos(self._ax), math.sin(self._ax)
        cb, sb = math.cos(self._ay), math.sin(self._ay)

        rot, proj = [], []
        for (x, y, z) in _CUBE_V:
            y1 = y * ca - z * sa          # 绕 X 轴
            z1 = y * sa + z * ca
            x1 = x * cb + z1 * sb         # 绕 Y 轴
            z2 = -x * sb + z1 * cb
            rot.append((x1, y1, z2))
            f = _DIST / (_DIST + z2)      # 透视：越远越小
            proj.append((x1 * f, -y1 * f))

        big = _CUBE_PX * _SS
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        c = big / 2.0
        # 实测：旋转 + 透视后，顶点到中心的归一化半径最大约 1.9，
        # 所以按它反推缩放，否则立方体四个角会被画布裁掉（看起来像"被挡住"）。
        scale = c / 2.0

        for a, b in _CUBE_E:
            ax_, ay_ = proj[a]
            bx_, by_ = proj[b]
            # 深度决定这条棱的亮度与粗细（近亮粗、远暗细）
            near = 1.0 - ((rot[a][2] + rot[b][2]) * 0.5 + 1.6) / 3.2
            near = max(0.0, min(1.0, near))
            # 线宽跟着画布尺寸走：210px 时就是原来的 1/2/3 px（闪屏完全不变），
            # 这样把同一套画法拿去渲别的尺寸时比例才一致——不缩放的话小图标里
            # 线会粗成一坨、大图上又细得看不见。
            lw = 3 if near > 0.62 else (2 if near > 0.32 else 1)
            lw = max(1, int(round(lw * _CUBE_PX / 210.0)))
            w = lw * _SS
            for s in range(_SEG):
                t0 = s / float(_SEG)
                t1 = (s + 1) / float(_SEG)
                # 颜色相位：叠加 _hue 之后会沿棱往前跑
                wave = 0.5 + 0.5 * math.sin(((t0 * 0.75 + self._hue) % 1.0)
                                            * 2 * math.pi)
                lit = 0.46 + 0.54 * wave * (0.40 + 0.60 * near)
                draw.line(
                    [c + (ax_ + (bx_ - ax_) * t0) * scale,
                     c + (ay_ + (by_ - ay_) * t0) * scale,
                     c + (ax_ + (bx_ - ax_) * t1) * scale,
                     c + (ay_ + (by_ - ay_) * t1) * scale],
                    fill=_shade(_CUBE_RGB, lit) + (255,), width=max(1, int(w)))

        # 缩回显示尺寸换抗锯齿。这里**不能用 LANCZOS**：它会在棱边铺出大量
        # 半透明像素（实测 2x 下来约 5000 个），而 Tk 把 RGBA 塞进照片时对
        # "既不全透明也不全不透明"的像素走逐像素慢路径——半透明像素越多
        # paste 越慢。换成 HAMMING 之后半透明像素降到约 3200、画质肉眼看不出
        # 差别，整帧从 27 ms 掉到 8 ms（LANCZOS 缩放本身 12 ms + paste 13.6 ms）。
        img = img.resize((_CUBE_PX, _CUBE_PX), Image.HAMMING)

        # 粒子直接画在缩放后的图上：小圆点不需要抗锯齿，放在超采样画布上画
        # 会让每帧多花十几毫秒（实测 29ms → 52ms），得不偿失。
        d2 = ImageDraw.Draw(img)
        c1 = _CUBE_PX / 2.0
        sc1 = c1 / 2.0
        for i, pt in enumerate(self._particles):
            pt[0] += pt[3]
            pt[1] += pt[4]
            pt[2] += pt[5]
            pt[6] -= 0.035                        # 寿命递减
            if pt[6] <= 0:
                pt = self._spawn_particle()
                self._particles[i] = pt
            f = _DIST / (_DIST + pt[2])
            sx = c1 + pt[0] * f * sc1
            sy = c1 - pt[1] * f * sc1
            fade = min(1.0, pt[6] / 0.7)          # 快消失时淡出
            # 粒子刻意做小：最大一颗半径也就 2px 出头，是"尘点"而不是光球
            rad = max(0.9, (0.7 + 0.9 * f) * 1.1 * fade)
            d2.ellipse([sx - rad, sy - rad, sx + rad, sy + rad],
                       fill=_shade(_CUBE_RGB, 0.40 + 0.60 * fade)
                       + (int(235 * fade),))

        self._last_img = img

    # ---------------------------------------------------------------- 主循环
    def _spin(self):
        """一条定时器链干三件事：推进弹跳、推进旋转、合成贴图。

        转角按"真实经过的时间"推进，而不是每帧固定加一点：启动时主线程会去建
        主界面，中间难免有几十毫秒让不出帧，按帧加就会越转越慢；按时间加，
        掉帧只是画面顿一下，整体转速和终止姿态始终一致。
        """
        if not self._alive:
            return
        t0 = time.perf_counter()
        dt = t0 - self._last_t
        self._last_t = t0
        dt = max(0.0, min(0.1, dt))       # 夹住：卡太久也不要一下跳过去

        if self._closing:
            self._out_t = min(1.0, self._out_t + dt / _OUT_SEC)
            self._scale = _out_scale(self._out_t)
            if self._on_shrunk is not None and self._out_t >= _SHRUNK_AT:
                cb, self._on_shrunk = self._on_shrunk, None
                try:
                    cb()
                except Exception:
                    pass
        elif self._pop_t < 1.0:
            self._pop_t = min(1.0, self._pop_t + dt / _POP_SEC)
            self._scale = _pop_scale(self._pop_t)

        self._ax += _SPIN_X * dt
        self._ay += _SPIN_Y * dt
        self._hue = (self._hue + _HUE_SPEED * dt) % 1.0
        try:
            self._draw_cube()
            self._compose(self._scale)
            self._apply_round_region(self._scale)
        except Exception:
            pass

        if self._closing and self._out_t >= 1.0:
            self._finish()
            return

        used_ms = (time.perf_counter() - t0) * 1000.0
        # 下限见 _FRAME_MIN_MS 的注释：延时太小会让调用方的 root.update() 永不返回
        delay = max(_FRAME_MIN_MS, int(_FRAME_MS - used_ms))
        try:
            self.win.after(delay, self._spin)
        except Exception:
            pass

    # ----------------------------------------------------------------对外接口
    def set_status(self, text):
        """保留这个接口：app.py 的构建阶段回调还在用它报进度，
        但闪屏上已经不放状态文字了（一直显示"正在加载"反而滑稽），所以这里不做事。"""
        return

    # ------------------------------------------------------------------ 收尾
    def close(self, on_done=None, on_shrunk=None):
        """收起卡片：先微微一涨蓄力，再快速缩到 0，然后销毁。

        on_done 在窗口真的消失之后调用；on_shrunk 在卡片已经缩到很小
        （约 90% 进度）时调用一次——调用方可以借这个时机让主界面显形：
        那一下会给主窗口做第一次整绘（实测 150ms 上下），趁卡片只剩一小点
        的时候做，卡顿就看不出来。

        这里不再用 -alpha 淡出——淡出看着"糊"，缩放收起来更干脆。
        """
        self._on_done = on_done
        self._on_shrunk = on_shrunk
        if self._closing:
            return
        self._closing = True
        self._out_t = 0.0
        if not self._alive:
            self._finish()

    def _finish(self):
        """收起结束（或中途出错）：销毁窗口，然后通知调用方。

        收尾要挪到父窗口（root）的 after 上做，不能就地销毁：这个回调本身是
        登记在闪屏窗口上的，就地 destroy 之后 tkinter 会在回调返回时去删一个
        已经不存在的 Tcl 命令，抛 AttributeError；实测那一抛还会把同一时刻
        刚登记到 root 上的回调一起带走。
        """
        try:
            self._parent.after(1, self._destroy_and_notify)
        except Exception:
            self._destroy_and_notify()

    def _destroy_and_notify(self):
        self.destroy()
        cb = getattr(self, "_on_done", None)
        self._on_done = None
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def destroy(self):
        self._alive = False
        try:
            self.win.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ 布局
    def _center(self):
        try:
            self.win.update_idletasks()
            x = (self.win.winfo_screenwidth() - _WIN_W) // 2
            y = (self.win.winfo_screenheight() - _WIN_H) // 2
            self.win.geometry(f"{_WIN_W}x{_WIN_H}+{x}+{y}")
        except Exception:
            pass

    def _apply_round_region(self, scale=1.0):
        """把窗口裁成圆角矩形——Tk 做不到，只能靠 Windows API。缩放到多大就裁多大。

        稳态（缩放没变）时直接跳过：SetWindowRgn 实测 1.8ms/次，而旋转阶段缩放一直是 1.0，
        每帧白花这笔钱。
        """
        if self._rgn_scale is not None and abs(scale - self._rgn_scale) < 0.002:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            user32.GetParent.restype = ctypes.c_void_p
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_bool]
            gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
            gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6

            if self._hwnd is None:
                self.win.update_idletasks()
                self._hwnd = (user32.GetParent(ctypes.c_void_p(self.win.winfo_id()))
                              or self.win.winfo_id())
            w = max(1, int(round(CARD_W * scale)))
            h = max(1, int(round(CARD_H * scale)))
            x0, y0 = (_WIN_W - w) // 2, (_WIN_H - h) // 2
            r = max(2, int(round(CARD_RADIUS * 2 * scale)))
            rgn = gdi32.CreateRoundRectRgn(x0, y0, x0 + w + 1, y0 + h + 1, r, r)
            if rgn:
                user32.SetWindowRgn(ctypes.c_void_p(self._hwnd),
                                    ctypes.c_void_p(rgn), True)
                self._rgn_scale = scale
        except Exception:
            pass


def _find_icon():
    try:
        from utils.helpers import get_icon_path
        return get_icon_path()
    except Exception:
        return None

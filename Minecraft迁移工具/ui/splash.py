# -*- coding: utf-8 -*-
"""启动闪屏：圆角卡片 + 旋转的绿色线框立方体 + 加载提示。

立方体是空心线框：8 个顶点绕轴旋转 → 透视投影 → 12 条棱、每条再切若干段，
段间颜色递变并让相位随时间推进，于是颜色沿着框架流动。

画面用 Pillow 在 3 倍尺寸的画布上绘制、再缩回显示尺寸（超采样），
这样线条边缘是抗锯齿的 —— Tk 的 Canvas 画线没有抗锯齿，再怎么加段数都会发毛。

圆角靠 Windows 的 SetWindowRgn；卡片背景与描边也用 Pillow。
任何一步失败都只是没有闪屏，不影响程序启动。
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

# ---- 立方体几何（空心：只画 12 条棱） ----
_CUBE_V = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
           (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
_CUBE_E = [(0, 1), (1, 2), (2, 3), (3, 0),
           (4, 5), (5, 6), (6, 7), (7, 4),
           (0, 4), (1, 5), (2, 6), (3, 7)]
_CUBE_RGB = (86, 224, 128)      # 框架基色（绿）

_CUBE_PX = 210      # 立方体在屏幕上的边长（像素）
_SS = 2             # 超采样倍率：画 2 倍大再缩回来换抗锯齿（3x 效果更好但吃不消）
_SEG = 8            # 每条棱切几段（渐变细度）
_FRAME_MS = 22      # 目标帧间隔 ≈ 45fps；_spin 按实际耗时补偿，掉帧也不会拖慢转速
_SPIN_X = 1.364     # 绕 X 轴角速度（弧度/秒，等价于原来 33ms 一帧的 0.045）
_SPIN_Y = 2.273     # 绕 Y 轴角速度
_HUE_SPEED = 1.364  # 颜色流动速度（周期/秒）
_DIST = 4.4         # 透视距离，越小透视越强
_PARTICLES = 22     # 环绕立方体飘散的粒子数（同色系）


def _shade(rgb, factor):
    """按亮度系数调整颜色，返回 RGB 三元组。"""
    return tuple(max(0, min(255, int(v * factor))) for v in rgb)


class SplashScreen:
    """无边框圆角卡片闪屏。用 close() 淡出。"""

    def __init__(self, parent, icon_path=None, title="Minecraft 整合包迁移工具",
                 subtitle="增强版 v4"):
        self._alive = True
        self._ax, self._ay = -0.50, 0.62          # 方块初始姿态
        self._hue = 0.0                            # 颜色流动相位
        self._cube_photo = None
        self._particles = [self._spawn_particle() for _ in range(_PARTICLES)]

        win = tk.Toplevel(parent)
        self.win = win
        self._parent = parent
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=BG)

        canvas = tk.Canvas(win, width=CARD_W, height=CARD_H,
                           highlightthickness=0, bd=0, bg=BG)
        canvas.pack()
        self.canvas = canvas

        # 卡片背景 + 圆角描边（角落的直角会被窗口 region 裁掉）
        try:
            from PIL import Image, ImageDraw, ImageTk
            card = Image.new("RGB", (CARD_W, CARD_H), BG)
            draw = ImageDraw.Draw(card)
            draw.rounded_rectangle([1, 1, CARD_W - 2, CARD_H - 2],
                                   radius=CARD_RADIUS, outline=BORDER, width=2)
            self._card = ImageTk.PhotoImage(card)
            canvas.create_image(0, 0, anchor="nw", image=self._card)
        except Exception:
            canvas.create_rectangle(0, 0, CARD_W, CARD_H, fill=BG,
                                    outline=BORDER, width=2)

        # 立方体的图片槽位。PhotoImage 只建一次，之后每帧 paste 更新内容——
        # 每帧新建 PhotoImage 会把整块图像数据拷给 Tk，是这个动画最大的开销。
        try:
            from PIL import Image as _Image, ImageTk as _ImageTk
            self._cube_photo = _ImageTk.PhotoImage(
                _Image.new("RGBA", (_CUBE_PX, _CUBE_PX), (0, 0, 0, 0)))
        except Exception:
            self._cube_photo = None
        self._cube_item = canvas.create_image(CARD_W // 2, 124, image=self._cube_photo)
        self._title_id = canvas.create_text(CARD_W // 2, 264, text=title,
                                            fill=TITLE_FG, font=("微软雅黑", 16, "bold"))
        canvas.create_text(CARD_W // 2, 294, text=subtitle, fill=SUB_FG,
                           font=("微软雅黑", 10))

        self._draw_cube()
        self.canvas.tag_lower(self._cube_item, self._title_id)

        self._apply_round_region()
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
        """旋转 → 透视 → 超采样渲染成抗锯齿的绿色线框。"""
        try:
            from PIL import Image, ImageDraw, ImageTk
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
            w = (3 if near > 0.62 else (2 if near > 0.32 else 1)) * _SS
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

        img = img.resize((_CUBE_PX, _CUBE_PX), Image.LANCZOS)

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

        self._last_img = img              # 留一份原图，便于调试与自检
        if self._cube_photo is None:
            self._cube_photo = ImageTk.PhotoImage(img)
            self.canvas.itemconfigure(self._cube_item, image=self._cube_photo)
        else:
            self._cube_photo.paste(img)      # 复用同一个 PhotoImage，省掉整块拷贝

    def _spin(self):
        """持续旋转 + 颜色流动。

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
        self._ax += _SPIN_X * dt
        self._ay += _SPIN_Y * dt
        self._hue = (self._hue + _HUE_SPEED * dt) % 1.0
        try:
            self._draw_cube()
        except Exception:
            pass
        used_ms = (time.perf_counter() - t0) * 1000.0
        delay = max(1, int(_FRAME_MS - used_ms))
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
    def close(self, steps=9, interval=14, on_done=None):
        """淡出并销毁。on_done 在窗口真的消失之后调用。

        app.py 靠它把主界面显出来：淡出期间主界面一直以 0 透明度藏在下面
        （已经画好了），等卡片没了再由它淡入——顺序是"卡片消失 → 主界面出现"。
        """
        self._alive = False
        self._on_done = on_done
        self._fade(steps, steps, interval)

    def _finish(self):
        """淡出结束（或中途出错）：销毁窗口，然后通知调用方。"""
        self.destroy()
        cb = getattr(self, "_on_done", None)
        self._on_done = None
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def _fade(self, step, total, interval):
        if step <= 0:
            # 收尾要挪到父窗口（root）的 after 上做，不能就地销毁：这个回调本身是
            # 登记在闪屏窗口上的，就地 destroy 之后 tkinter 会在回调返回时去删一个
            # 已经不存在的 Tcl 命令，抛 AttributeError；实测那一抛还会把同一时刻
            # 刚登记到 root 上的回调一起带走（主界面的淡入就是这么被干掉的）。
            try:
                self._parent.after(1, self._finish)
            except Exception:
                self._finish()
            return
        try:
            # 从半透明基准往下降，而不是从 1.0 开始
            self.win.attributes("-alpha", CARD_ALPHA * step / float(total))
        except Exception:
            self._finish()
            return
        try:
            self.win.after(interval, lambda: self._fade(step - 1, total, interval))
        except Exception:
            self._finish()

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
            x = (self.win.winfo_screenwidth() - CARD_W) // 2
            y = (self.win.winfo_screenheight() - CARD_H) // 2
            self.win.geometry(f"{CARD_W}x{CARD_H}+{x}+{y}")
        except Exception:
            pass

    def _apply_round_region(self):
        """把窗口裁成圆角矩形——Tk 做不到，只能靠 Windows API。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            user32.GetParent.restype = ctypes.c_void_p
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
            gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6

            self.win.update_idletasks()
            hwnd = (user32.GetParent(ctypes.c_void_p(self.win.winfo_id()))
                    or self.win.winfo_id())
            rgn = gdi32.CreateRoundRectRgn(0, 0, CARD_W + 1, CARD_H + 1,
                                           CARD_RADIUS * 2, CARD_RADIUS * 2)
            if rgn:
                user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
        except Exception:
            pass


def _find_icon():
    try:
        from utils.helpers import get_icon_path
        return get_icon_path()
    except Exception:
        return None

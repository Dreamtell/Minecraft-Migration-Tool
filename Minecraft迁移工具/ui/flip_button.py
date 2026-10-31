# ui/flip_button.py
"""CS2 式翻转按钮：近黑暗面 + 4px 竖条 + 1.2px 细描边 + 悬停扫光 + 红背斜纹（纯 Tk + Pillow）。

外观**逐条照抄**原型 特效2.py 的 Effect._draw_face / _front / _back（303-422 行），数值
就是准绳；只是把画笔从 Qt 换成 Pillow —— 主进程**不 import Qt**（架构：Tk 主进程 +
PySide6 子进程）。翻转本体的算法（透视投影 / 三档手感 / 半程换面 / 侧立亮线）沿用本项目
原有实现，与原型一致。

  面     竖直渐变 face_top→face_bot；悬停按 hv（0→1）lerp 到 face_top_hover/face_bot_hover。
  竖条   左侧 4px，lerp(edge, accent, hv)，悬停点亮（只画正面）。
  描边   线宽 1.2px，lerp(edge, edge_hot, hv)，路径内缩 0.8。
  文字   微软雅黑粗体居中，颜色 title，保留 emoji，**无副标题**（38px 高塞不下中间那行）；
         字号按高度自适应（38px 高 → 16px，约等于原型的 30px/96px 比例）。
  扫光   悬停时白色斜向渐变扫过：alpha 峰值 80×hv，范围 -0.35w → +1.35w，一趟 780ms。
  背面   斜纹 accent alpha 26、线宽 6、间距 28、沿 55px/s 流；底部 3px 进度条
         宽 = w*max(0.06, 0.5-0.5cos(t*2.6))；标题的三个点画在固定槽位（免得整行左右跳）。
  压暗   侧立时 |sin(ang)|*150 的黑色叠加，只动 RGB 不动 alpha（否则边缘出一圈黑边）。
  淡出   文字按 |cos(ang)| 在 0.15~0.45 之间淡入淡出。

圆角取 8 而不是原型的 12：原型按钮 96px 高（12/96=0.125），这颗只有 38px 高，按比例该取
5；8 是本项目按钮族的既定值（helpers._BTN_RADIUS），比原型略"方"，而 12/38 会圆得像个
胶囊、离原型那块"方块面板"更远 —— 取 8，并在 _face_rgba 里按 8 建遮罩。

为什么持续动画做成"相位表"：Tk 的 canvas image item 不会把 PhotoImage 的 alpha 和下层图
合成（透明处露出的是画布底色），所以扫光/斜纹只能烤进面图。整面 110x38 现画一次约 1.3ms，
30fps 下光这里就吃掉 mainloop；改成把整面按相位烤成 PhotoImage（扫光 24 张、背面 72 张），
tick 里只 itemconfigure 换图（~0.05ms）。相位表在 prewarm 里分片烤，首次悬停/首次翻到
背面就能命中。

对外接口与 create_gradient_button 返回的 Canvas 保持一致：state()、set_text()/get_text()、
set_command()、set_gradient()、set_icon()、set_corner_bg()，外加本组件自己的 flip_to_back()/
flip_to_front()/set_feel()/get_feel()/set_dry_run()。
"""
import math
import time
from collections import deque as _deque
import tkinter as tk
from tkinter import font as tkfont

from utils import helpers as _h
from utils import anim as _anim
from utils import i18n

try:
    from PIL import Image as _Image
    from PIL import ImageChops as _ImageChops
    from PIL import ImageDraw as _ImageDraw
    from PIL import ImageFont as _ImageFont
    from PIL import ImageTk as _ImageTk
except Exception:                                   # 没装 Pillow：整个组件退回旧按钮
    _Image = _ImageChops = _ImageDraw = _ImageFont = _ImageTk = None

_PIL_OK = bool(getattr(_h, "_PIL_OK", False) and _Image is not None
               and _ImageTk is not None and _ImageDraw is not None)

# ---------------------------------------------------------------- 视觉常量（原型口径）
_RADIUS = 8             # 圆角：见模块说明（原型 12/96 高，这里 38 高，取按钮族既定的 8）
_ACCENT_W = 3.0         # 左侧竖条宽度（原型 fillRect(0,0,4,h)，贴边满高；见下）
# 竖条**不贴边**：原型按钮高 96，4px 只占 4%，贴边满高也看不出来；这里高 38，4px 占
# 10.5%，而静止态 hv=0 时竖条颜色 = lerp(edge, accent, 0) = edge = 描边色，于是左边读作
# 「4px 粗描边」、右边只有 1.2px —— 用户原话「左边比右边厚」。改成内缩胶囊：外圈描边
# 四边一致，竖条缩进来当独立小元素（同 HANDOFF 里吐司左侧色条的结论）。
_ACCENT_INSET_X = 4.0   # 竖条离左边框的距离
_ACCENT_INSET_Y = 6.0   # 竖条上下缩进（38 高 -> 26 高的胶囊）
_BORDER_W = 1.2         # 描边线宽（原型 pen.setWidthF(1.2)）
_BORDER_INSET = 0.8     # 描边内缩（原型 _face_path(face, 0.8)）
_SS = 4                 # 超采样倍数（同 helpers._BTN_SS）：渐变/描边/斜纹都先画大再缩
_CAM = 900.0            # 透视相机距离（原型 cam=900）
_TEXT_LO, _TEXT_HI = 0.15, 0.45      # 文字淡入淡出的 |cos| 区间（原型 _text_alpha 同）
_SHADE_MAX = 150 / 255.0             # 侧立压暗上限（原型 shade = |sin|*150）

_SWEEP_MS = 780.0       # 扫光一趟（原型 sweep_ms）
_SWEEP_PEAK = 80        # 扫光 alpha 峰值（原型 int(80*hv)）
# 扫光带半宽：原型 x-110 → x+110，而原型按钮宽 420 → 半宽/宽 = 0.2619。
# 不能照抄 110 这个**绝对像素**：这颗按钮只有 110 宽，110 的半宽等于整面被灌满，
# 悬停就变成"整块刷白"，而原型里它始终只是一条带子。所以按比例取，比例本身照抄。
_SWEEP_HALF_RATIO = 110.0 / 420.0
_SWEEP_PHASES = 96      # 扫光相位表长度（780/96 = 8.1ms ≈ 124fps；原来 24 是 32.5ms，
                        # 144Hz 屏上光带一格一格跳，看着像掉帧）

_STRIPE_ALPHA = 26      # 背面斜纹 alpha（原型 stripes.setAlpha(26)）
_STRIPE_W = 6.0         # 斜纹线宽
_STRIPE_STEP = 28.0     # 斜纹间距
_STRIPE_SPEED = 55.0    # 斜纹流速 px/s
_PROGRESS_H = 3         # 底部进度条高度
_BAR_SUBPX = 4          # 条宽按 1/4 像素量化（头子能半格半格挪）
_PROGRESS_MIN = 0.06    # 进度条最短（原型 max(0.06, ...)）

_UI_MS = 33             # 持续动画的 tick 间隔（30fps 上限）
_HV_IN, _HV_OUT = 0.09, 0.15    # 悬停淡入 90ms / 淡出 150ms（原型 step() 里的两个常数）
# hv 量化档：面按档缓存，扫光只在 1.0 那档出现。**档数不能再少** —— 原来只有 4 档，
# 而淡入是指数逼近（_HV_IN=90ms，实际要 ~200ms 才到 0.99），于是鼠标一碰，亮度只跳
# 4 下，看着就是「高亮帧数低」。改 25 档（步长 0.04）≈ 5ms 一档，跟刷新率对上。
_HV_LEVELS = tuple(i / 24.0 for i in range(25))  # 0, 0.04, ... 1.0
# 背面斜纹相位表长度。相位周期固定 2.417s，表越长每步的角度越小、看着越连续：
# 72 = 每 33.6ms 换一格（≈30fps 的观感，用户反馈"背面帧数低"就是这个）；
# 180 = 每 13.4ms 一格（≈74fps），缓存只多一百来张。
_BACK_PHASES = 180
_BACK_DT = (2.0 * math.pi / 2.6) / _BACK_PHASES   # 让进度条的 cos 周期正好一圈（2.417s）

# ---------------------------------------------------------------- 配色（原型 FACE_IDLE / FACE_SEARCH 原值）
FACE_IDLE = {"face_top": "#1b2a20", "face_bot": "#0a120d",
             "face_top_hover": "#25382b", "face_bot_hover": "#0f1a13",
             "edge": "#3d5647", "edge_hot": "#7ee0a8", "accent": "#46cf8a",
             "title": "#eefaf2"}
FACE_SEARCH = {"face_top": "#2c1a1a", "face_bot": "#120a0a",
               "face_top_hover": "#3d2424", "face_bot_hover": "#1a0f0f",
               "edge": "#5c3c3c", "edge_hot": "#ff9b9b", "accent": "#ff5a5a",
               "title": "#fdeeee"}
# 模拟运行（原型没有这一面）：同一套结构，把 IDLE 的绿换成琥珀，明度关系照抄
# （face_top 比 face_bot 亮约 1.9 倍、hover 再抬 ~35%，edge_hot 与 accent 同族）。
FACE_DRY = {"face_top": "#2a2517", "face_bot": "#12100a",
            "face_top_hover": "#3a3320", "face_bot_hover": "#191510",
            "edge": "#5a5136", "edge_hot": "#ffd76e", "accent": "#f5c451",
            "title": "#fffaf0"}
# 禁用：暗面 + 灰竖条 + 灰细描边（不再是一颗亮灰药丸）；hover 档与常态同值 = 移上去不变亮
FACE_DISABLED = {"face_top": "#262626", "face_bot": "#111111",
                 "face_top_hover": "#262626", "face_bot_hover": "#111111",
                 "edge": "#4a4a4a", "edge_hot": "#4a4a4a", "accent": "#6e6e6e",
                 "title": "#9a9a9a"}

# 浅色主题：上面那四套是照原型（深色界面）调的，face_top #1b2a20 这种近乎黑，
# 放到浅色窗口里就是一块黑方块（用户报「浅色主题下这个好黑」）。
# 这里另出一套：色相不变，面换成同族的浅色（top 比 bot 亮的关系照抄），
# 字和竖条换成深色，edge_hot/accent 加深以保证在浅底上看得清。
FACE_IDLE_L = {"face_top": "#e9f6ee", "face_bot": "#cde7d8",
               "face_top_hover": "#f3fbf6", "face_bot_hover": "#d8eee1",
               "edge": "#9dbfab", "edge_hot": "#1f9d55", "accent": "#1f9d55",
               "title": "#0d2a19"}
FACE_SEARCH_L = {"face_top": "#fbebeb", "face_bot": "#f5d3d3",
                 "face_top_hover": "#fdf5f5", "face_bot_hover": "#f8dede",
                 "edge": "#d9a3a3", "edge_hot": "#cc2b2b", "accent": "#c62828",
                 "title": "#4a0f0e"}
FACE_DRY_L = {"face_top": "#fbf4e2", "face_bot": "#f2e4c3",
              "face_top_hover": "#fdfaf0", "face_bot_hover": "#f7ecd2",
              "edge": "#d3c39a", "edge_hot": "#b8791a", "accent": "#b8791a",
              "title": "#3b2b09"}
FACE_DISABLED_L = {"face_top": "#f2f2f2", "face_bot": "#e4e4e4",
                   "face_top_hover": "#f2f2f2", "face_bot_hover": "#e4e4e4",
                   "edge": "#cfcfcf", "edge_hot": "#cfcfcf", "accent": "#bdbdbd",
                   "title": "#8a8a8a"}

_SCHEMES = {
    "dark": {"idle": FACE_IDLE, "search": FACE_SEARCH, "dry": FACE_DRY,
             "off": FACE_DISABLED},
    "light": {"idle": FACE_IDLE_L, "search": FACE_SEARCH_L, "dry": FACE_DRY_L,
              "off": FACE_DISABLED_L},
}


def _is_light_bg(color):
    """按底色亮度判断该用浅色面还是深色面（阈值 0.62：白/浅灰/浅黄都算浅）。"""
    try:
        c = str(color).strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        return False
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0 > 0.62

_FRONT_GREEN = ("#00c853", "#00e676")     # 只作 _btn_colors 标记 / 兼容旧调用（面走 FACE_IDLE）
_FRONT_YELLOW = ("#f9a825", "#ffd54f")    # 同上（面走 FACE_DRY）
_BACK_RED = ("#e53935", "#ff7043")        # 同上（面走 FACE_SEARCH）

# 三档手感。sink 是侧立时整体收缩量（原型：干脆/标准 0.05，沉 0.08）。
PRESETS = {
    "crisp":    {"ms": 240, "over": 1.30, "sink": 0.05},
    "standard": {"ms": 320, "over": 0.90, "sink": 0.05},
    "heavy":    {"ms": 460, "over": 0.55, "sink": 0.08},
}
FEELS = ("crisp", "standard", "heavy")
DEFAULT_FEEL = "standard"
FEEL_LABELS = {"crisp": "干脆", "standard": "标准", "heavy": "沉"}
FEEL_KEY = "start_btn_feel"        # 配置文件里的键（main_window.py 默认值 + 存档）

_TEXT_PAD = 18          # 左右内边距，和 helpers._BTN_TEXT_PAD 一致
_FRAME_MS = 16          # 目标帧间隔（60fps）—— 现在只当兜底值用，见 _TABLE_MS

# 帧表密度：**按显示器刷新周期烤**（144Hz → 7ms，320ms 的手感 = 47 帧）。原来按固定
# 16ms 烤只有 21 帧，就算 tick 跑到 240fps，屏幕上 16ms 才换一张图，看着还是「一卡一卡」；
# 固定 8ms 也不行 —— 144Hz 下每帧要显示 1.75 个刷新周期，运动分辨率对不上。
# 帧图缓存在 _frames 里（key 带状态）：47 帧 × 2 方向 × 3 档手感 ≈ 280 张 ≈ 5.6MB。
_TABLE_MS_MIN = 4       # 帧表密度的下限（240Hz 屏）
_TABLE_MS_MAX = 16      # 上限（60Hz 屏），再密就是白烤帧
# 非翻转帧的预算。以前是"一超上限就把非翻转帧全清"，相位表一满就整表重烤，
# 滚动动画每轮都要暖一遍；改成按插入顺序挤掉最老的（dict 保序），稳态就不会抖。
_MAX_FRAMES = 420       # 图缓存上限（翻转帧 + 静止帧 + 相位表）
_PREWARM_MS_PER_TICK = 0.006   # 预热每轮最多占用主线程 6ms，然后让出

# 字体：PIL 里 CJK 用微软雅黑粗体，emoji 只能单独用 Segoe UI Emoji 画
_CJK_FONTS = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
              "C:/Windows/Fonts/simhei.ttf")
_EMOJI_FONTS = ("C:/Windows/Fonts/seguiemj.ttf", "C:/Windows/Fonts/seguisym.ttf")

_face_cache = {}        # (w, h, pid, hv) -> RGBA 面（不含文字/扫光/斜纹）
_clip_cache = {}        # (w, h, r) -> L 圆角遮罩（1x，且已降采样抗锯齿）
_layer_cache = {}       # (w, h, kind, key) -> RGBA 覆盖层（扫光带 / 斜纹 / 进度条）
_mask_cache = {}        # (W, H, r) -> L 圆角遮罩（超采样尺寸）
_text_cache = {}        # (w, h, text, px, color) -> RGBA 文字层
_font_cache = {}        # (paths, px) -> ImageFont


# ---------------------------------------------------------------- 小工具
def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else (hi if x > hi else x)


def _rgb(color):
    """'#rrggbb' -> (r, g, b)；颜色不合法时给个中性灰。"""
    try:
        return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
    except Exception:
        return (128, 128, 128)


def _mix(c1, c2, k):
    """两个 #rrggbb 按 k（0=c1，1=c2）混一个出来 —— 即原型的 _lerp。"""
    try:
        a, b = _rgb(c1), _rgb(c2)
        return "#%02x%02x%02x" % tuple(
            max(0, min(255, int(round(a[i] + (b[i] - a[i]) * k)))) for i in range(3))
    except Exception:
        return c1


def _ease_out_back(t, s):
    """ease-out-back：末端冲过头再回弹，s 越大过冲越多（原型同公式）。"""
    t = _clamp(t)
    u = t - 1.0
    return 1.0 + (s + 1.0) * u * u * u + s * u * u


def _solve8(A, b):
    """8 元一次方程组（高斯消元 + 部分主元）。矩阵很小，纯 Python 够快。"""
    n = 8
    m = [list(A[i]) + [b[i]] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            return None                      # 退化（面正好侧立成一条线）——交给亮线分支
        if p != c:
            m[c], m[p] = m[p], m[c]
        pv = m[c][c]
        for r in range(n):
            if r == c:
                continue
            f = m[r][c] / pv
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _perspective_coeffs(dst, src):
    """输出四点 dst -> 原图四点 src 的单应系数。

    PIL 的 Image.transform(PERSPECTIVE) 要的正是"输出像素 -> 输入像素"这个方向，
    所以这里不能直接解"原图 -> 梯形"（那样得到的是反的，图会乱飞）。
    """
    A, b = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        b.append(u)
        A.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        b.append(v)
    return _solve8(A, b)


def _project(w, h, ang, sink):
    """把矩形四角按 ang 投影成梯形（返回 4 个点，顺序：左上、右上、右下、左下）。

    绕水平轴翻：绕 y 轴（屏幕上下方向）转，所以 y 值决定纵深 Z、x 只受透视缩放；
    c=cos(ang) 压的是"高度"，|sin| 越大侧立越厉害；sink 让侧立时整体再收一点，
    避免 90° 附近正好铺满一整个按钮宽、看着像"没动"。
    """
    c = math.cos(ang)
    s = math.sin(ang)
    k0 = 1.0 - sink * abs(s)
    out = []
    for x, y in ((0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))):
        dy = y - h / 2.0
        k = _CAM / (_CAM + dy * s) * k0
        out.append(((x - w / 2.0) * k + w / 2.0, dy * c * k + h / 2.0))
    return out


def _is_emoji(ch):
    """够用的 emoji 判定：PIL 里这些字符只有 Segoe UI Emoji 有字形。"""
    cp = ord(ch)
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF
            or 0x2B00 <= cp <= 0x2BFF or 0x2300 <= cp <= 0x23FF or cp == 0x20E3)


def _load_font(paths, px):
    """按 (候选字体, 像素高) 取 ImageFont（FreeType 打开一次就缓存）。"""
    key = (paths, px)
    hit = _font_cache.get(key)
    if hit is not None:
        return hit
    f = None
    for p in paths:
        try:
            f = _ImageFont.truetype(p, px)
            break
        except Exception:
            f = None
    if f is None:
        try:
            f = _ImageFont.load_default()
        except Exception:
            f = None
    _font_cache[key] = f
    return f


def _round_mask(W, H, r):
    """圆角遮罩（L 图）：超采样尺寸下建一次就好。"""
    key = (W, H, r)
    hit = _mask_cache.get(key)
    if hit is not None:
        return hit
    m = _Image.new("L", (W, H), 0)
    if r > 0:
        _ImageDraw.Draw(m).rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=255)
    else:
        _ImageDraw.Draw(m).rectangle([0, 0, W - 1, H - 1], fill=255)
    _mask_cache[key] = m
    return m


def _clip_mask(w, h):
    """1x 版圆角遮罩（从超采样遮罩降采样来，圆角才有抗锯齿）。

    扫光带 / 斜纹 / 进度条都要裁在面内（原型 setClipPath(path)）：漏出去就会在圆角
    外面画出一圈白边或红线。
    """
    key = (w, h, _RADIUS)
    hit = _clip_cache.get(key)
    if hit is not None:
        return hit
    big = _round_mask(w * _SS, h * _SS, min(_RADIUS * _SS, min(w * _SS, h * _SS) // 2))
    out = big.resize((w, h), _Image.LANCZOS) if w > 1 and h > 1 else big
    _clip_cache[key] = out
    return out


class FlipButton(tk.Canvas):
    """会翻面的 CS2 式深色按钮（tk.Canvas 子类：pack / place / winfo_req* 全都白拿）。

    参数与 create_gradient_button 基本对齐，多出来的几个：
      feel        手感（"crisp"/"standard"/"heavy"）
      dry_run     当前是不是"模拟运行"（决定正面配色和正/背面文案）
      back_colors 背面配色标记（背面固定走 FACE_SEARCH 红）
      back_text   背面文案（真跑）
    """

    def __init__(self, parent, text="🚀 开始迁移", command=None,
                 colors=_FRONT_GREEN, height=38, font=("微软雅黑", 12, "bold"),
                 feel=DEFAULT_FEEL, dry_run=False, back_text="⏳ 迁移中...",
                 back_colors=_BACK_RED, width=None, click_guard_ms=300,
                 extra_w=0):
        self._font = font
        # 原文留一份：切语言时按它重翻（_texts 里存的是当前语言的成品）
        self._texts_原 = {"front": text, "front_dry": "🚀 模拟运行",
                          "back": back_text, "back_dry": "⏳ 模拟中..."}
        self._texts = {k: i18n.tr(v) for k, v in self._texts_原.items()}

        def _重按语言():
            self._texts = {k: i18n.tr(v) for k, v in self._texts_原.items()}
            self._front_txt_custom = None
            self._back_txt_custom = None
            try:
                self._recalc_width() if hasattr(self, "_recalc_width") else None
                self._draw_rest()
            except Exception:
                pass
        i18n.注册自绘(self, _重按语言)
        self._front_colors = tuple(colors)
        self._back_colors = tuple(back_colors)
        self._feel = feel if feel in PRESETS else DEFAULT_FEEL
        self._dry = bool(dry_run)
        self._disabled = False
        self._btn_disabled = False            # 只读标记：和旧按钮同名，方便排查
        # inert：看着是活的（常色面 + 悬停特效），但点击什么都不做。
        # 迁移进行中就用它 —— 用户要求「按钮保持特效」，而 disabled 会把面切成
        # 灰的（见 _face_spec），特效全没。
        self._inert = False
        self._cmd = command
        self._click_guard_ms = click_guard_ms
        self._click_at = 0.0
        self._at_back = False
        self._hover = False                  # 指针是否在按钮上（只影响正面）
        self._hv = 0.0                       # 平滑过的悬停量 0..1（原型 hover_amt）
        self._animating = False
        self._anim_target = 0
        self._job = None
        self._hr = False                     # 是否已 anim.hold()
        self._prewarm_job = None
        self._ui_job = None                  # 持续动画（扫光 / 背面斜纹）的 tick
        self._ui_last = 0.0
        self._dots_n = 0                     # 背面文案当前点数（只在变点数时才改 Tk item）
        self._dots_id = None
        self._icon = None
        self._text_px_cache = 0
        self._tk_font = None
        self._frames = {}                    # 帧图/静止图缓存（key 里带状态，见各处）
        self._t0 = time.perf_counter()
        # 开始前倒计时要用：背面临时文案（「⏳ 3 秒后开始」）和真实进度（0..1）。
        # 都留 None = 保持原来的样子（省略号动画 + 来回扫的装饰条）。
        self._back_txt_custom = None
        # 正面临时文案（弹确认框时写「⏳ 等你确认…」），None = 用默认
        self._front_txt_custom = None
        # 文案淡入：换字那一下从暗到亮，别「啪」地换（倒计时 → 迁移中就是这种）
        self._shown_text = None
        self._text_fade_job = None
        self._prog_override = None
        # 条实际画到哪儿（缓动追 _prog_override）：日志一条一条地来，直接照抄目标值
        # 就是一跳一跳；这里让它每帧追一点，观感是连续推过去的。
        self._prog_show = None

        self._ch = int(height)
        # 宽度：和旧按钮同一套量法（helpers._btn_text_width + _BTN_TEXT_PAD），
        # 取四张面里最宽的那个 —— 否则切到"模拟运行"时按钮会跟着变窄，整排按钮乱跳。
        # 倒计时文案（「⏳ 10 秒后开始」）是运行时才换上去的第五种文案，比现有四张面都长：
        # 不预先算进宽度，10 秒那一档就会被裁掉半个字（中文实测 139px > 可用 127px）。
        self._countdown_ref = i18n.trp("⏳ {0} 秒后开始", 10)
        need = 44
        try:
            # extra_w：在「文字宽 + 两侧内边距」之外再补一点（调用方要按钮比文字长一些时给）。
            最宽 = max([_h._btn_text_width(t, font) for t in self._texts.values()]
                       + [_h._btn_text_width(self._countdown_ref, font)])
            need = max(44, 最宽 + _TEXT_PAD + max(0, int(extra_w or 0)))
        except Exception:
            need = 44
        self._cw = int(max(int(width or 0), need))
        try:
            bg = parent.cget("bg")
        except Exception:
            bg = "#f0f0f0"
        # 深浅两套面：按**父控件底色**判（深色主题 #1e1e1e 一类，浅色主题 #f5f5f5 一类）。
        # 切主题由 set_theme() 显式喂进来 —— 自绘控件吃不到 apply_theme_to_widget_tree。
        self._bg_now = bg
        self._scheme = "light" if _is_light_bg(bg) else "dark"
        self._pal = _SCHEMES[self._scheme]
        tk.Canvas.__init__(self, parent, width=self._cw, height=self._ch,
                           highlightthickness=0, bd=0, bg=bg)
        self.pack_propagate(False)
        self._btn_colors = tuple(self._front_colors)     # 只读标记（旧按钮同名）
        self._base_colors = tuple(self._front_colors)

        self._img_id = self.create_image(0, 0, anchor="nw", tags="face")
        # 底部那条进度单独做一个画布项：倒计时时它每秒要变上百次，跟整面一起合成的话
        # 每变一次都得重烤一整张面（实测 ~4.5ms），看着就是一顿一顿的。拆出来之后
        # 面只在斜纹相位变化时换图（还都命中相位表），条只换一张 w×3 的裁剪图。
        self._bar_id = self.create_image(0, 0, anchor="nw", tags="bar",
                                         state="hidden")
        self._bar_cache = {}
        self._txt_id = self.create_text(self._cw // 2, self._ch // 2, text="",
                                        fill=self._pal["idle"]["title"],
                                        font=self._txt_font_pick(),
                                        anchor="center", tags="text")
        self._icon_img_id = None
        self.bind("<Button-1>", self._on_click)
        # 悬停用 bind（本控件级）而不是 bind_all：项目里 bind_all 已被 bind_text_scroll
        # 之类的全局绑定占用，往那上面挂会让所有窗口共用一份 hover 状态。
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Destroy>", self._on_destroy)
        self._draw_rest()
        self.prewarm()

    # ------------------------------------------------------------ 面：配色与文案
    def _dry_on(self):
        return bool(self._dry)

    def _face_spec(self, front):
        """这一面用哪套配色、写什么字 —— 返回 (palette_id, palette, text)。

        ⚠ 两条配色规则是实测踩出来的：
        1) 背面永远是红（FACE_SEARCH）。背面只在迁移真跑起来时出现，而那时按钮已经被
           state("disabled") 了（mw_migration.py:1189）—— 要是背面也跟着变灰，
           整段"⏳ 迁移中"就只剩一块灰。禁用感交给发灰的文字 + 点不动来表达。
        2) 翻面过程中不切灰。点下去那一瞬间业务代码就禁用按钮，若按 _disabled 渲染，
           前半程会从绿突然变灰，看着像闪一下。
        """
        if not front:
            pid = "search"
        elif self._disabled and not self._animating:
            pid = "off"
        elif self._dry_on():
            pid = "dry"
        else:
            pid = "idle"
        pal = self._pal[pid]        # 深浅主题各一套，见 _SCHEMES
        if front:
            text = (self._front_txt_custom
                    or (self._texts["front_dry"] if self._dry_on()
                        else self._texts["front"]))
        else:
            # 倒计时会临时换掉背面文案（set_back_text），没换就用默认的「⏳ 迁移中...」
            text = (self._back_txt_custom
                    or (self._texts["back_dry"] if self._dry_on()
                        else self._texts["back"]))
        return pid, pal, text

    def _state_key(self):
        return (self._disabled, self._dry_on(), self._front_colors, self._back_colors)

    def _hv_q(self):
        """hv 量化到 4 档（面按档烤好缓存，中间值取最近的一档）。"""
        return min(_HV_LEVELS, key=lambda v: abs(v - self._hv))

    # ------------------------------------------------------------ 图像素材：底色/文字度量
    def _bg_rgb(self):
        try:
            r, g, b = self.winfo_rgb(self.cget("bg"))
            return (r // 256, g // 256, b // 256)
        except Exception:
            return (240, 240, 240)

    def _text_px(self):
        """文字像素字号：按按钮高度自适应（38px 高 → 16px，约等于原型 30px/96px）。"""
        if self._text_px_cache:
            return self._text_px_cache
        px = int(round(self._ch * 0.42))
        self._text_px_cache = max(11, min(24, px))
        return self._text_px_cache

    def _txt_font_pick(self):
        """Tk 侧字体：家族沿用调用方（微软雅黑），字号按高度换算回磅。"""
        family = "微软雅黑"
        try:
            if isinstance(self._font, (tuple, list)) and self._font:
                family = self._font[0]
        except Exception:
            pass
        try:
            dpi = float(self.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0
        pt = max(9, int(round(self._text_px() * 72.0 / max(48.0, dpi))))
        return (family, pt, "bold")

    def _text_w(self, s):
        """Tk 侧量一段文字的宽度（背面标题 + 三个点要按固定槽位排版）。"""
        try:
            if self._tk_font is None:
                self._tk_font = tkfont.Font(font=self._txt_font_pick())
            return float(self._tk_font.measure(s))
        except Exception:
            return float(len(s) * self._text_px() * 0.6)

    def _bg_image(self):
        return _Image.new("RGB", (self._cw, self._ch), self._bg_rgb())

    # ------------------------------------------------------------ 面：竖直渐变 + 竖条 + 描边
    def _face_rgba(self, pid, pal, hv, front):
        """按钮面（不含文字/扫光/斜纹）：原型 _draw_face 的前三步。

        · 面：竖直渐变 face_top→face_bot，悬停按 hv 插值到 *_hover；
        · 正面左侧 4px 竖条 lerp(edge, accent, hv)；
        · 描边 1.2px lerp(edge, edge_hot, hv)，内缩 0.8。
        全部在 _SS 倍尺寸下画完再 LANCZOS 缩小 —— 1x 直画的话 1.2px 描边和左侧竖条
        的圆角都是硬边，看着像贴纸。
        """
        # 键里必须带方案：pid 在深浅两套里同名（都是 "dry"），只按 pid 缓存会把
        # 深色面直接复用给浅色主题（第一次就撞了这个，浅色下还是一块黑）。
        key = ("face", self._cw, self._ch, pid, round(hv, 3), bool(front),
               self._scheme)
        hit = _face_cache.get(key)
        if hit is not None:
            return hit
        w, h = self._cw, self._ch
        W, H = w * _SS, h * _SS
        r = min(_RADIUS * _SS, min(W, H) // 2)
        c0 = _rgb(_mix(pal["face_top"], pal["face_top_hover"], hv))
        c1 = _rgb(_mix(pal["face_bot"], pal["face_bot_hover"], hv))
        col = _Image.new("RGB", (1, H))
        cpx = col.load()
        for y in range(H):
            t = y / (H - 1) if H > 1 else 0.0
            cpx[0, y] = (int(c0[0] + (c1[0] - c0[0]) * t),
                         int(c0[1] + (c1[1] - c0[1]) * t),
                         int(c0[2] + (c1[2] - c0[2]) * t))
        img = col.resize((W, H)).convert("RGBA")
        mask = _round_mask(W, H, r)
        if front:
            # 内缩胶囊（原来贴边满高，静止态会看成 4px 粗左描边，左右不对称）
            bar = _Image.new("L", (W, H), 0)
            bx0 = _ACCENT_INSET_X * _SS
            by0 = _ACCENT_INSET_Y * _SS
            bw_bar = max(2.0, _ACCENT_W * _SS)
            bx1 = bx0 + bw_bar
            by1 = H - by0
            _ImageDraw.Draw(bar).rounded_rectangle(
                [bx0, by0, bx1, by1], radius=bw_bar / 2.0, fill=255)
            img.paste(_rgb(_mix(pal["edge"], pal["accent"], hv)), (0, 0),
                      _ImageChops.multiply(bar, mask))
        bw = max(1, int(round(_BORDER_W * _SS)))
        inset = _BORDER_INSET * _SS
        _ImageDraw.Draw(img).rounded_rectangle(
            [inset, inset, W - 1 - inset, H - 1 - inset],
            radius=max(1, int(round(r - inset))),
            outline=_rgb(_mix(pal["edge"], pal["edge_hot"], hv)), width=bw)
        img.putalpha(mask)
        out = img.resize((w, h), _Image.LANCZOS)
        _face_cache[key] = out
        return out

    # ------------------------------------------------------------ 覆盖层：扫光 / 斜纹 / 进度条
    def _sweep_layer(self, phase):
        """悬停扫光：白色斜向渐变带，alpha 峰值 80，一趟 780ms 从 -0.35w 扫到 +1.35w。

        原型是一条从 (x-110, 0) 到 (x+110, h) 的线性渐变（所以带子微微倾斜）。这里按
        列算出 profile，再按行做亚像素横移 —— 逐像素算 2D 投影要 4ms，这样是 0.2ms。
        """
        key = ("sweep", self._cw, self._ch, phase, self._scheme)
        hit = _layer_cache.get(key)
        if hit is not None:
            return hit
        w, h = self._cw, self._ch
        prog = phase / float(_SWEEP_PHASES)
        x = -0.35 * w + prog * 1.7 * w
        half = max(24.0, w * _SWEEP_HALF_RATIO)
        D = 2.0 * half + h * h / (2.0 * half)          # 渐变轴在 x 方向的投影长度
        n = w + int(math.ceil(h * h / (2.0 * half))) + 2
        prof = bytearray(n)
        for i in range(n):
            u = (i - x + half) / D
            a = _SWEEP_PEAK * (1.0 - abs(2.0 * u - 1.0))
            prof[i] = 0 if a <= 0.0 else (255 if a > 255.0 else int(a))
        rows = []
        for py in range(h):
            sh = int(round(py * h / (2.0 * half)))
            rows.append(bytes(prof[sh:sh + w]))
        alpha = _Image.frombytes("L", (w, h), b"".join(rows))
        ov = _Image.new("RGBA", (w, h), (255, 255, 255, 0))
        ov.putalpha(_ImageChops.multiply(alpha, _clip_mask(w, h)))
        if len(_layer_cache) > 220:
            _layer_cache.clear()
        _layer_cache[key] = ov
        return ov

    def _stripe_layer(self, t):
        """背面斜纹：accent、alpha 26、线宽 6、间距 28，沿 55px/s 往下流。"""
        off = (t * _STRIPE_SPEED) % _STRIPE_STEP
        key = ("stripe", self._cw, self._ch, round(off, 2), self._scheme)
        hit = _layer_cache.get(key)
        if hit is not None:
            return hit
        w, h = self._cw, self._ch
        W, H = w * _SS, h * _SS
        acc = _rgb(self._pal["search"]["accent"])
        ov = _Image.new("RGBA", (W, H), acc + (0,))
        d = _ImageDraw.Draw(ov)
        lw = max(1, int(round(_STRIPE_W * _SS)))
        x = -h + off
        while x < w + _STRIPE_STEP:
            d.line([(int(round(x * _SS)), H), (int(round((x + h) * _SS)), 0)],
                   fill=acc + (_STRIPE_ALPHA,), width=lw)
            x += _STRIPE_STEP
        ov = ov.resize((w, h), _Image.LANCZOS)
        ov.putalpha(_ImageChops.multiply(ov.getchannel("A"), _clip_mask(w, h)))
        if len(_layer_cache) > 220:
            _layer_cache.clear()
        _layer_cache[key] = ov
        return ov

    def _decor_u(self, t):
        """装饰性的来回扫宽度（只表示在忙，与时间无关）：6% → 100% → 6%。"""
        return max(_PROGRESS_MIN, 0.5 - 0.5 * math.cos(t * 2.6))

    # 进度历史（时间, 值），只留最近的；条的显示位置从这里回放
    _PROG_LAG = 0.18        # 回放延迟（秒）
    _PROG_HIST_MAX = 48     # 最多记多少个采样点

    def _push_prog_history(self, 值):
        """记一笔真实进度，供底部条按"晚 _PROG_LAG 秒"回放。"""
        try:
            历史 = getattr(self, "_prog_hist", None)
            if 历史 is None:
                历史 = self._prog_hist = _deque(maxlen=self._PROG_HIST_MAX)
            历史.append((time.perf_counter(), 值))
        except Exception:
            pass

    def _prog_show_from_history(self, now, tgt):
        """按历史回放算"现在该画到哪"：取 now-lag 时刻的插值。"""
        历史 = getattr(self, "_prog_hist", None)
        if not 历史:
            return tgt
        目标时刻 = now - self._PROG_LAG
        if 目标时刻 <= 历史[0][0]:
            return 历史[0][1]
        上 = None
        for t, v in 历史:
            if t >= 目标时刻:
                if 上 is None:
                    return v
                跨 = t - 上[0]
                比 = 0.0 if 跨 <= 0 else (目标时刻 - 上[0]) / 跨
                return 上[1] + (v - 上[1]) * max(0.0, min(1.0, 比))
            上 = (t, v)
        # 历史还没追上"现在"（迁移刚停）：就停在最后一个值上，别倒着走
        return 上[1] if 上 else tgt

    def _bar_u(self):
        """这一帧该画的条宽：真进度优先（缓动值），没有就回到装饰性的来回扫。"""
        if self._prog_override is not None:
            if self._prog_show is None:
                self._prog_show = self._prog_override
            return self._prog_show
        self._prog_show = None
        return self._decor_u(self._back_phase() * _BACK_DT)

    def _bar_strip(self):
        """满宽进度条（w x _PROGRESS_H，已按圆角裁好）。只烤一次。"""
        key = ("barstrip", self._cw, self._ch, self._scheme)
        hit = _layer_cache.get(key)
        if hit is not None:
            return hit
        w, h = self._cw, self._ch
        strip = _Image.new("RGBA", (w, _PROGRESS_H),
                           _rgb(self._pal["search"]["accent"]) + (255,))
        strip.putalpha(_ImageChops.multiply(
            strip.getchannel("A"), _clip_mask(w, h).crop((0, h - _PROGRESS_H, w, h))))
        if len(_layer_cache) > 220:
            _layer_cache.clear()
        _layer_cache[key] = strip
        return strip

    def _bar_photo(self, u):
        """宽度 u（0..1）那一段的条贴图。

        原来按**整像素**取整：150px 宽、7 秒走完，头子每秒才挪 21 个像素，
        每挪一次就是一整格 —— 看着像"一秒动两下"。现在按 1/4 像素量化，
        最后一列按余数压透明度，头子就能半格半格地挪（84 格/秒）。
        裁剪 + 改一列 alpha 都是 C 层操作，比重新合成整面便宜得多。
        """
        w = self._cw
        try:
            q = int(round(max(0.0, min(1.0, float(u))) * w * _BAR_SUBPX))
        except Exception:
            q = 0
        q = max(0, min(w * _BAR_SUBPX, q))
        if q in self._bar_cache:
            return self._bar_cache[q]
        photo = None
        if q > 0:
            try:
                整, 余 = divmod(q, _BAR_SUBPX)
                条 = self._bar_strip()
                if 余:
                    段 = 条.crop((0, 0, 整 + 1, _PROGRESS_H))
                    阿尔法 = 段.getchannel("A")
                    # 条的填充是实色（alpha 255），所以最后那一列直接把 alpha 换成
                    # 余数比例就行 —— 看上去就是"半格宽"的头子
                    阿尔法.paste(int(round(255.0 * 余 / _BAR_SUBPX)), (整, 0))
                    段.putalpha(阿尔法)
                    photo = _ImageTk.PhotoImage(段)
                else:
                    photo = _ImageTk.PhotoImage(条.crop((0, 0, 整, _PROGRESS_H)))
            except Exception:
                photo = None
        if len(self._bar_cache) > 1600:
            self._bar_cache.clear()
        self._bar_cache[q] = photo
        return photo

    def _apply_bar(self, u=None):
        """刷底部那条：u=None 就藏起来（正面 / 翻转中）。"""
        if not _PIL_OK:
            return
        try:
            photo = None if u is None else self._bar_photo(u)
            if photo is None:
                self.itemconfigure(self._bar_id, state="hidden")
            else:
                self.coords(self._bar_id, 0, self._ch - _PROGRESS_H)
                self.itemconfigure(self._bar_id, image=photo, state="normal")
        except Exception:
            pass

    def _back_layer(self, t, hv=0.0):
        """背面整层（不含底色、不含底部进度条）：红面 + 斜纹。翻转帧和静止帧共用。

        底部进度条不在这里合成 —— 它是独立画布项（见 _apply_bar），否则进度一变就要
        重烤一整张面。
        """
        face = self._face_rgba("search", self._pal["search"], hv, False)
        return _Image.alpha_composite(face, self._stripe_layer(t))

    # ------------------------------------------------------------ 贴图：底 + 面 + 图标
    def _photo(self, key, layer):
        """RGBA 层贴到不透明底色上做成 PhotoImage（整面重绘，不会有残影）。"""
        hit = self._frames.get(key)
        if hit is not None:
            return hit
        img = self._bg_image()
        img.paste(layer, (0, 0), layer)
        if self._icon is not None:
            ic = self._icon
            img.paste(ic, ((self._cw - ic.width) // 2, (self._ch - ic.height) // 2),
                      ic if ic.mode == "RGBA" else None)
        photo = _ImageTk.PhotoImage(img)
        if len(self._frames) > _MAX_FRAMES:
            self._drop_photos()
        self._frames[key] = photo
        return photo

    def _drop_photos(self):
        """挤出最老的非翻转帧，保留翻转帧（那些最贵，扔了下次点击要现烤 40 帧）。

        原来是"一超上限就全清"：背面相位表 180 张、悬停扫光 96 张，来回一混就顶到
        上限，于是每 400 张左右整表重烤一遍（一次 4.5ms）—— 看着像周期性掉帧。
        改成按插入顺序只挤掉最老的几张，稳态下相位表能一直命中缓存。
        """
        超 = len(self._frames) - _MAX_FRAMES
        if 超 <= 0:
            return
        for k in list(self._frames):
            if 超 <= 0:
                break
            if isinstance(k, tuple) and k[0] == "flip":
                continue
            del self._frames[k]
            超 -= 1

    def _stem(self):
        return (self._cw, self._ch, id(self._icon) if self._icon else 0, self._bg_rgb())

    def _rest_photo(self, pid, pal, hv):
        """正面静止帧（无扫光）：扫光只在 hv==1 那档由 _hover_photo 出图。"""
        key = ("rest", pid, round(hv, 3)) + self._stem()
        return self._photo(key, self._face_rgba(pid, pal, hv, True))

    def _hover_photo(self, pid, pal, phase):
        """悬停静止帧：hv=1 的面 + 第 phase 相位的扫光。"""
        key = ("hover", pid, phase) + self._stem()
        layer = _Image.alpha_composite(self._face_rgba(pid, pal, 1.0, True),
                                       self._sweep_layer(phase))
        return self._photo(key, layer)

    def _back_photo(self, phase, hv=0.0):
        """背面静止帧：第 phase 相位的斜纹 + 面（hv = 悬停亮度，0 是常色）。"""
        key = ("back", phase, round(hv, 3)) + self._stem()
        return self._photo(key, self._back_layer(phase * _BACK_DT, hv))

    def _back_hover_photo(self, phase, sweep):
        """背面悬停到位后的帧：亮面 + 斜纹（第 phase 相）+ 扫光（第 sweep 相）。

        斜纹必须继续走 —— 停住的话看着像"高亮之后画面卡死了"（用户报的）。
        这里不靠 `_photo` 的键缓存（72×96 = 6912 种组合，等于每帧都是新图），
        而是靠下层三张图各自命中缓存：`_face_rgba` 按 hv 缓存、`_stripe_layer`
        按相位缓存、`_sweep_layer` 按相位缓存，每帧只做两次 alpha 合成。
        """
        layer = _Image.alpha_composite(self._back_layer(phase * _BACK_DT, 1.0),
                                       self._sweep_layer(sweep))
        return self._photo(("backhover", phase, sweep) + self._stem(), layer)


    # ------------------------------------------------------------ 文字层（翻转动效里烤进图）
    def _text_layer(self, text, color):
        """文字层（RGBA、满不透明）：title 色粗体，CJK 用 msyh、emoji 用 seguiemj 拼。"""
        if not text:
            return None
        px = self._text_px()
        key = (self._cw, self._ch, text, px, color)
        hit = _text_cache.get(key)
        if hit is not None:
            return hit
        img = _Image.new("RGBA", (self._cw, self._ch), (0, 0, 0, 0))
        d = _ImageDraw.Draw(img)
        cjk = _load_font(_CJK_FONTS, px)
        emo = _load_font(_EMOJI_FONTS, px)
        runs = []
        for ch in text:
            if ch == "️":               # 变体选择符：没有宽度，别占位
                continue
            runs.append((ch, emo if (_is_emoji(ch) and emo is not None) else cjk))
        if not runs:
            for ch in text:
                runs.append((ch, cjk))
        widths = []
        for ch, f in runs:
            try:
                widths.append(float(d.textlength(ch, font=f)))
            except Exception:
                widths.append(float(px))
        total = sum(widths)
        try:
            asc, desc = cjk.getmetrics()
        except Exception:
            asc, desc = px, int(px * 0.25)
        # 统一一条基线：两套字体各自用 anchor="ls" 落笔，视觉上才对得齐
        base = self._ch / 2.0 + (asc - desc) / 2.0
        x = (self._cw - total) / 2.0
        fill = _rgb(color) + (255,)
        for (ch, f), wch in zip(runs, widths):
            try:
                d.text((x, base), ch, font=f, fill=fill, anchor="ls")
            except Exception:
                pass
            x += wch
        if len(_text_cache) > 96:
            _text_cache.clear()
        _text_cache[key] = img
        return img

    # ------------------------------------------------------------ 帧表 / 单帧
    def _table_ms(self):
        """帧表密度 = 这块屏的刷新周期（毫秒），夹在 _TABLE_MS_MIN.._TABLE_MS_MAX。"""
        try:
            return max(_TABLE_MS_MIN, min(_TABLE_MS_MAX, int(_anim.frame_ms(self))))
        except Exception:
            return _FRAME_MS

    def _frame_table(self, feel, direction):
        """某一档手感、某个方向的 f 表（缓动+过冲先算好，帧才可缓存）。"""
        p = PRESETS.get(feel) or PRESETS[DEFAULT_FEEL]
        n = max(9, int(round(p["ms"] / float(self._table_ms()))) + 1)
        tab = []
        for i in range(n):
            e = _ease_out_back(i / float(n - 1), p["over"])
            tab.append(e if direction > 0 else 1.0 - e)
        # 半程必须有一帧正好 90 deg（画成 2px 亮线），否则从正面 62 deg 一步跨到背面 71 deg
        k = min(range(n), key=lambda i: abs(tab[i] - 0.5))
        tab[k] = 0.5
        return tab

    def _knife_index(self, tab, direction):
        """哪一帧正好是 90°（半程换面那一帧）—— 那一帧画一条 2px 亮线。"""
        for i, f in enumerate(tab):
            if (direction > 0 and f >= 0.5) or (direction < 0 and f <= 0.5):
                return i
        return len(tab) // 2

    def _knife_image(self, quad, front):
        """侧立成一条线：整面重绘成底色 + 一条 2px accent 亮线（原型 _knife）。"""
        _pid, pal, _text = self._face_spec(front)
        img = self._bg_image()
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        y = int(round(sum(ys) / 4.0))
        x0 = int(round(min(xs)))
        x1 = int(round(max(xs)))
        if x1 - x0 < 2:
            x0, x1 = self._cw // 2 - 1, self._cw // 2 + 1
        color = _rgb(pal["accent"])
        y0 = max(0, min(self._ch - 2, y - 1))
        _ImageDraw.Draw(img).rounded_rectangle([x0, y0, x1, y0 + 1], radius=1, fill=color)
        return img

    def _render_frame(self, feel, direction, idx):
        """画一帧翻转中的按钮（整面重绘，绝不叠影）。

        hover 量在这里固定取 0：帧缓存 key 不含 hover，动画途中改 hover 才不会出现
        "同一帧两套图"或把缓存打乱（上一轮的结论保留）。
        """
        tab = self._frame_table(feel, direction)
        f = tab[idx]
        front = f < 0.5
        ang = (f * math.pi) if front else ((f - 1.0) * math.pi)
        # 过冲：正面时 f 可能略小于 0、背面时 f 可能略大于 1，角度按同样公式自然回弹
        ang = max(-math.pi, min(math.pi, ang))
        sink = (PRESETS.get(feel) or PRESETS[DEFAULT_FEEL])["sink"]
        quad = _project(self._cw, self._ch, ang, sink)

        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0 = max(0, int(math.floor(min(xs))) - 1)
        y0 = max(0, int(math.floor(min(ys))) - 1)
        x1 = min(self._cw, int(math.ceil(max(xs))) + 1)
        y1 = min(self._ch, int(math.ceil(max(ys))) + 1)
        bw, bh = x1 - x0, y1 - y0
        if bw <= 2 or bh <= 2 or abs(math.cos(ang)) < 0.02:
            return self._knife_image(quad, front)

        pid, pal, text = self._face_spec(front)
        if front:
            face = self._face_rgba(pid, pal, 0.0, True)
        else:
            # 背面：斜纹/进度条按帧号给个相位，翻转途中看着也是"在流"（纯缓存，零额外成本）
            face = self._back_layer(idx * 0.034)
        comp = face
        layer = self._text_layer(text, pal["title"])
        alpha = _clamp((abs(math.cos(ang)) - _TEXT_LO) / (_TEXT_HI - _TEXT_LO))
        if layer is not None and alpha > 0.02:
            comp = face.copy()
            tint = layer.copy()
            if alpha < 0.99:
                tint.putalpha(layer.getchannel("A").point(
                    [int(v * alpha) for v in range(256)]))
            comp.paste(tint, (0, 0), tint)

        src = [(0.0, 0.0), (float(self._cw), 0.0),
               (float(self._cw), float(self._ch)), (0.0, float(self._ch))]
        dst = [(p[0] - x0, p[1] - y0) for p in quad]
        coeffs = _perspective_coeffs(dst, src)
        if coeffs is None:
            return self._knife_image(quad, front)
        try:
            warped = comp.transform((bw, bh), _Image.PERSPECTIVE, coeffs, _Image.BICUBIC)
        except Exception:
            return self._knife_image(quad, front)

        # 侧立时压暗一点：只动 RGB、不动 alpha，边缘才不会多出一圈黑边
        k = int(round(255 * (1.0 - _SHADE_MAX * abs(math.sin(ang)))))
        if k < 254:
            r, g, b, a = warped.split()
            lut = [min(255, (v * k) // 255) for v in range(256)]
            warped = _Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))

        out = self._bg_image()
        out.paste(warped, (x0, y0), warped)          # 整面重绘：不会有残影
        return out

    def _frame_photo(self, feel, direction, idx):
        key = ("flip", feel, direction, idx, self._state_key())
        hit = self._frames.get(key)
        if hit is not None:
            return hit
        photo = _ImageTk.PhotoImage(self._render_frame(feel, direction, idx))
        if len(self._frames) > _MAX_FRAMES:
            self._drop_photos()
        self._frames[key] = photo
        return photo

    # ------------------------------------------------------------ 动画驱动（翻转）
    def _cancel_anim(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._rel_hr()

    # 定时器精度（anim.hold/release 是引用计数，必须配平，否则整机一直 1ms 空转）
    def _hold_hr(self):
        if getattr(self, "_hr", False):
            return
        try:
            _anim.hold()
            self._hr = True
        except Exception:
            self._hr = False

    def _rel_hr(self):
        if not getattr(self, "_hr", False):
            return
        self._hr = False
        try:
            _anim.release()
        except Exception:
            pass

    def _animate(self, direction, 结束后=None):
        """direction=+1 翻到背面、-1 翻回正面；按时间推进，帧号量化后取缓存。"""
        self._cancel_anim()
        self._stop_tick()            # 翻转期间由动画独占画面（扫光/斜纹不插队）
        feel = self._feel
        tab = self._frame_table(feel, direction)
        n = len(tab)
        ms = (PRESETS.get(feel) or PRESETS[DEFAULT_FEEL])["ms"]
        dur = ms / 1000.0
        # 帧间隔跟显示器刷新率走（anim.delay 会顺手把失焦/最小化降到 30fps），
        # 但不要慢于帧表自身的密度，否则会跳帧。
        spacing = max(1, int(round(ms / float(max(1, n - 1)))))
        try:
            interval = max(1, min(int(_anim.delay(self)), spacing))
        except Exception:
            interval = max(1, min(_FRAME_MS, spacing))
        self._animating = True
        # 翻面用的是常色帧，所以这里把悬停亮度归零：否则「点一下开始 → 取消 → 翻回正面」
        # 时 _hv 还是点之前的 1.0，第一帧就是满亮的高亮面，看着像"啪一下亮了"。
        # 归零之后翻回正面会从 0 开始一格格涨上去（25 档，约 170ms）。
        self._hv = 0.0
        self._hold_hr()          # 不 raise 定时器精度，after(16) 实测只有 42fps
        self._anim_target = direction
        self.itemconfigure(self._txt_id, state="hidden")
        self._apply_bar(None)
        if self._dots_id is not None:
            self.itemconfigure(self._dots_id, state="hidden")
        if self._icon_img_id is not None:
            self.itemconfigure(self._icon_img_id, state="hidden")
        st = {"idx": -1, "t0": time.perf_counter(), "done": False}

        def tick():
            self._job = None
            if not self.winfo_exists():
                return
            el = time.perf_counter() - st["t0"]
            over = el >= dur
            idx = n - 1 if over else min(n - 1, int(el / dur * (n - 1) + 0.5))
            if idx != st["idx"]:
                st["idx"] = idx
                try:
                    self.itemconfigure(self._img_id,
                                       image=self._frame_photo(feel, direction, idx))
                except Exception:
                    pass
            if over:
                self._animating = False
                self._rel_hr()
                self._at_back = direction > 0
                self._draw_rest()
                if 结束后 is not None:
                    # 放完再复位：翻之前就把条抹掉会"啪"一下消失，正好卡在翻面的那一帧
                    # 才收，看着是随翻面一起收进去的。
                    try:
                        结束后()
                    except Exception:
                        pass
                return
            self._job = self.after(interval, tick)

        self._job = self.after(0, tick)

    def _flip(self, want_back, 结束后=None):
        if self._animating:
            if self._anim_target == (1 if want_back else -1):
                return
        elif self._at_back == want_back:
            self._draw_rest()
            if 结束后 is not None:
                try:
                    结束后()
                except Exception:
                    pass
            return
        self._animate(1 if want_back else -1, 结束后=结束后)

    def flip_to_back(self, force=False):
        """翻到背面（点击时调用）。已经在背面就什么都不做（force 可以重播）。"""
        if force:
            self._animate(1)
            return
        self._flip(True)

    def flip_to_front(self, 结束后=None):
        """翻回正面（迁移结束——成功或失败——调用）。"""
        self._flip(False, 结束后=结束后)

    # ------------------------------------------------------------ 持续动画（扫光 / 背面）
    def _sweep_phase(self):
        return int((time.perf_counter() - self._t0) * 1000.0 / _SWEEP_MS
                   * _SWEEP_PHASES) % _SWEEP_PHASES

    def _back_phase(self):
        return int((time.perf_counter() - self._t0) / _BACK_DT) % _BACK_PHASES

    def _tick_ms(self):
        """持续动画的帧间隔。

        正面扫光/悬停淡入是「一小段动完就停」，跟刷新率走；背面斜纹是迁移期间一直跑的
        （可能几分钟），给 30fps 就够了 —— 别让整机一直挂在 1ms 定时器上。
        """
        if self._at_back:
            # 背面**一直**跟刷新率走：那条斜纹在流、底部条在长、文案点点在闪，
            # 定格 33ms 一格的话（30fps）斜纹看着就是一格一格跳 —— 用户直接说
            # "这个状态帧数低"。每帧只是一次 itemconfigure + 一次裁图（缓存命中，
            # 实测 ~0.2ms），整机开销可以忽略；真正贵的是现烤整面，那个在缓存里。
            try:
                return max(_TABLE_MS_MIN, min(_UI_MS, int(_anim.frame_ms(self))))
            except Exception:
                return _UI_MS
        try:
            return max(_TABLE_MS_MIN, min(_UI_MS, int(_anim.frame_ms(self))))
        except Exception:
            return _UI_MS

    def _tick_wanted(self):
        """现在还需不需要 tick：翻转中不要（动画独占）、背面要（斜纹在流）、
        正面只在悬停（含淡出过程）且未禁用时要。"""
        if self._animating:
            return False
        if self._at_back:
            return True
        return (not self._disabled) and (self._hover or self._hv > 0.02)

    def _ensure_tick(self):
        if self._ui_job is not None or not _PIL_OK:
            return
        if not self._tick_wanted():
            return
        self._ui_last = time.perf_counter()
        if not self._at_back:
            self._hold_hr()
        try:
            self._ui_job = self.after(self._tick_ms(), self._ui_tick)
        except Exception:
            self._ui_job = None

    def _stop_tick(self):
        """停表。离开悬停、翻回正面、禁用、控件销毁都必须走到这里，不能留空转定时器。"""
        if self._ui_job is not None:
            try:
                self.after_cancel(self._ui_job)
            except Exception:
                pass
            self._ui_job = None
        if not self._at_back:
            self._rel_hr()

    def _ui_tick(self):
        """30fps 的持续动画：只换相位表里的图（itemconfigure），不现画整面。"""
        self._ui_job = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._animating:
            return
        now = time.perf_counter()
        dt = max(0.0, min(0.2, now - self._ui_last))
        self._ui_last = now
        if not self._tick_wanted():
            self._stop_tick()
            if not self._at_back and self._hv > 0.0:
                self._hv = 0.0
                try:
                    self._apply_face()
                except Exception:
                    pass
            return
        try:
            if self._at_back:
                # 背面也要悬停高亮：和正面同一套指数逼近，只是亮面换成红色的
                target = 1.0 if self._hover else 0.0
                dur = _HV_IN if target > self._hv else _HV_OUT
                if abs(target - self._hv) > 0.02:
                    self._hv += (target - self._hv) * min(1.0, dt / dur)
                else:
                    self._hv = target
                if self._hv >= 0.99:
                    photo = self._back_hover_photo(self._back_phase(), self._sweep_phase())
                else:
                    photo = self._back_photo(self._back_phase(), self._hv_q())
                self.itemconfigure(self._img_id, image=photo)
                # 条的位置 = 进度历史的 180ms 前那个值（线性插值）。
                # 为什么不用"追目标"：进度每 100~250ms 才来一跳，
                #   * 指数追 —— 追平之后要干等下一跳，看着一顿一顿；
                #   * 加最小步长 —— 步长一下就把整跳吃掉了，等于每跳"啪"地挪一段；
                # 都是"一秒动两下"的观感。改成回放历史：源动多少条就动多少，
                # 只是整体晚 180ms（肉眼看不出来），运动是连续、匀速的。
                tgt = self._prog_override
                if tgt is not None:
                    self._prog_show = self._prog_show_from_history(now, tgt)
                self._apply_bar(self._bar_u())
                self._tick_dots(now)
            else:
                target = 1.0 if self._hover else 0.0
                dur = _HV_IN if target > self._hv else _HV_OUT
                if abs(target - self._hv) > 0.02:
                    self._hv += (target - self._hv) * min(1.0, dt / dur)
                else:
                    self._hv = target
                pid, pal, _text = self._face_spec(True)
                if self._hv >= 0.99:
                    photo = self._hover_photo(pid, pal, self._sweep_phase())
                else:
                    photo = self._rest_photo(pid, pal, self._hv_q())
                self.itemconfigure(self._img_id, image=photo)
        except Exception:
            pass
        self._ui_job = self.after(self._tick_ms(), self._ui_tick)

    def _tick_dots(self, now):
        """背面标题的三个点：固定槽位里 1→2→3 循环（原型 int(t*3)%3）。"""
        if self._dots_id is None or self._icon is not None:
            return
        if self._back_txt_custom:
            return          # 倒计时文案是完整的，不加省略号
        n = 1 + int((now - self._t0) * 3.0) % 3
        if n != self._dots_n:
            self._dots_n = n
            try:
                self.itemconfigure(self._dots_id, text="." * n)
            except Exception:
                pass

    # ------------------------------------------------------------ 静止帧
    def _ensure_dots_item(self):
        if self._dots_id is not None:
            return
        try:
            self._dots_id = self.create_text(0, 0, text="", anchor="w",
                                             fill=self._pal["search"]["title"],
                                             font=self._txt_font_pick(), tags="text")
        except Exception:
            self._dots_id = None

    def _apply_face(self):
        """把当前状态该有的那张（或那一相位的）静止图刷到画布上，并排版文字。"""
        front = not self._at_back
        pid, pal, text = self._face_spec(front)
        if front:
            if self._disabled:
                photo = self._rest_photo("off", self._pal["off"], 0.0)
            elif self._hover and self._hv >= 0.99:
                photo = self._hover_photo(pid, pal, self._sweep_phase())
            else:
                photo = self._rest_photo(pid, pal, self._hv_q())
        else:
            if self._hv >= 0.99:
                photo = self._back_hover_photo(self._back_phase(), self._sweep_phase())
            else:
                photo = self._back_photo(self._back_phase(), self._hv_q())
        self.itemconfigure(self._img_id, image=photo)
        self._apply_bar(None if front else self._bar_u())
        if self._icon is not None:
            if self._icon_img_id is None:
                self._icon_img_id = self.create_image(
                    self._cw // 2, self._ch // 2, anchor="center", tags="icon")
            self.itemconfigure(self._icon_img_id,
                               image=_ImageTk.PhotoImage(self._icon), state="normal")
            self.itemconfigure(self._txt_id, text="", state="hidden")
            if self._dots_id is not None:
                self.itemconfigure(self._dots_id, state="hidden")
        else:
            if self._icon_img_id is not None:
                self.itemconfigure(self._icon_img_id, state="hidden")
            self._set_text_items(text, front, pal)
        self.tag_raise("text")
        self.tag_raise("icon")

    def _文案淡入(self, pal):
        """文案换了：从接近面底色的暗色淡到正常色，约 90ms。"""
        目标 = pal["title"]
        起 = _mix(pal["face_bot"], 目标, 0.18)
        try:
            self.itemconfigure(self._txt_id, fill=起)
        except Exception:
            return
        if self._text_fade_job is not None:
            try:
                self.after_cancel(self._text_fade_job)
            except Exception:
                pass
            self._text_fade_job = None
        步 = [0.45, 0.66, 0.82, 0.93, 1.0]

        def 一步(i=0):
            self._text_fade_job = None
            if not self.winfo_exists():
                return
            try:
                self.itemconfigure(self._txt_id, fill=_mix(起, 目标, 步[i]))
            except Exception:
                return
            if i + 1 < len(步):
                self._text_fade_job = self.after(18, lambda: 一步(i + 1))

        一步()

    def _set_text_items(self, text, front, pal):
        """正面：一行居中；背面：标题 + 三个点的固定槽位（点变多时整行不左右跳）。"""
        title = pal["title"]
        换字 = getattr(self, "_shown_text", None) != (text, front)
        self._shown_text = (text, front)
        if front:
            self.itemconfigure(self._txt_id, text=text, anchor="center", fill=title,
                               state="normal")
            self.coords(self._txt_id, self._cw // 2, self._ch // 2)
            if self._dots_id is not None:
                self.itemconfigure(self._dots_id, state="hidden")
            if 换字:
                self._文案淡入(pal)
            return
        base = text.rstrip(".．… ")
        if not base:
            base = text
        if self._back_txt_custom:
            # 倒计时文案整行居中（"..." 槽位在 set_back_text 里已经藏起来了）
            if self._dots_id is not None:
                self.itemconfigure(self._dots_id, state="hidden")
            self.itemconfigure(self._txt_id, text=base, anchor="center", fill=title,
                               state="normal")
            self.coords(self._txt_id, self._cw // 2, self._ch // 2)
            if 换字:
                self._文案淡入(pal)
            return
        self._ensure_dots_item()
        tw = self._text_w(base)
        slot = self._text_w("...")
        gap = 2.0
        x0 = (self._cw - (tw + gap + slot)) / 2.0
        cy = self._ch // 2
        self.itemconfigure(self._txt_id, text=base, anchor="w", fill=title, state="normal")
        self.coords(self._txt_id, x0, cy)
        if self._dots_id is not None:
            self._dots_n = 1 + int((time.perf_counter() - self._t0) * 3.0) % 3
            self.itemconfigure(self._dots_id, text="." * self._dots_n, anchor="w",
                               fill=title, state="normal")
            self.coords(self._dots_id, x0 + tw + gap, cy)

    def _draw_rest(self):
        try:
            if not self.winfo_exists():
                return
            self._apply_face()
            if self._tick_wanted():
                self._ensure_tick()
            else:
                self._stop_tick()
        except Exception:
            pass

    # ------------------------------------------------------------ 对外接口
    def set_theme(self, theme=None):
        """主题切换：深浅两套面重算、重画、重烤缓存。

        自绘控件吃不到 apply_theme_to_widget_tree（它只刷普通控件的 bg/fg），
        主窗口切主题时必须显式喂进来，否则浅色主题下这颗按钮还是黑方块。
        传 theme 字典就用它的底色判深浅；不传就按父控件当前底色判。
        """
        bg = None
        if isinstance(theme, dict):
            bg = theme.get("btn_bg") or theme.get("card") or theme.get("bg")
        if not bg:
            try:
                bg = self.master.cget("bg")
            except Exception:
                bg = None
        scheme = "light" if _is_light_bg(bg) else "dark"
        if bg and bg != self._bg_now:
            self._bg_now = bg
            try:
                self.configure(bg=bg)
            except Exception:
                pass
        if scheme == self._scheme:
            return
        self._scheme = scheme
        self._pal = _SCHEMES[scheme]
        try:
            self._frames.clear()
        except Exception:
            pass
        self._drop_photos()
        if not self._animating:
            self._draw_rest()
        self.prewarm()

    def set_feel(self, feel):
        """换手感：立即生效（帧表按手感分表，缓存 key 里就带手感，不用清表）。"""
        feel = str(feel or "").lower()
        if feel not in PRESETS:
            feel = DEFAULT_FEEL
        if feel == self._feel:
            return
        was_back = self._at_back
        self._cancel_anim()
        self._animating = False
        self._feel = feel
        self._at_back = was_back
        self._draw_rest()
        self.prewarm()

    def get_feel(self):
        return self._feel

    def set_dry_run(self, flag):
        """模拟运行开关：正面配色 + 正/背面文案跟着变（立即生效）。"""
        flag = bool(flag)
        if flag == self._dry:
            return
        self._dry = flag
        if not self._animating:
            self._draw_rest()
        self.prewarm()               # 先把新配色的相位表烤上，首次悬停才不会现画

    def set_front_text(self, text=None):
        """临时换正面文案（None 恢复默认）。弹确认框时用。

        只影响"这一面现在写什么"，不动宽度：调用方给的文案别比原来的长太多。
        """
        新 = (i18n.tr(text) if text else None)
        if 新 == self._front_txt_custom:
            return
        self._front_txt_custom = 新
        if not self._at_back and not self._animating:
            try:
                self._draw_rest()
            except Exception:
                pass

    def set_back_text(self, text=None):
        """临时换掉背面文案（倒计时要用：「⏳ 3 秒后开始」）。

        传 None 恢复默认的「⏳ 迁移中...」/「⏳ 模拟中...」。自定义文案不加省略号动画、
        整行居中 —— 倒计时那几个字不该像加载动画一样一顿一顿地闪。
        """
        新 = None
        if text is not None:
            文案 = str(text).strip()
            新 = i18n.tr(文案) if 文案 else None
        if 新 == self._back_txt_custom:
            return
        self._back_txt_custom = 新
        if self._at_back and not self._animating:
            self._draw_rest()

    def set_back_progress(self, value=None):
        """背面底部那条 3px 条：0..1 = 真实进度，None = 回到默认的来回扫。

        倒计时用它同步"还剩几秒"，迁移真跑起来后由进度队列推给它。
        """
        新 = None
        if value is not None:
            try:
                新 = max(0.0, min(1.0, float(value)))
            except Exception:
                新 = None
        if 新 == self._prog_override:
            return
        self._prog_override = 新
        if 新 is None:
            self._prog_show = None
            ʰ = getattr(self, "_prog_hist", None)
            if ʰ is not None:
                ʰ.clear()
        else:
            self._push_prog_history(新)
        if self._at_back and not self._animating:
            self._apply_bar(self._bar_u())
            self._ensure_tick()     # 条在动，tick 得活着（也是提速的依据）

    def state(self, new_state=None):
        """像 tk.Button 一样切 normal/disabled（禁用时整颗按钮变暗灰、点击无效）。"""
        if new_state is None:
            return "disabled" if self._disabled else "normal"
        want = str(new_state).lower() == "disabled"
        if want == self._disabled:
            return
        self._disabled = want
        self._btn_disabled = want                 # 只读标记，和旧按钮同名
        # 注意：正在翻转时不打断 —— 点下去先翻面，业务代码紧接着就把按钮禁用了
        # （mw_migration.py:1189），这时候把动画掐掉就等于"点了没反应"。
        if not self._animating:
            self._draw_rest()

    def set_inert(self, flag):
        """看着活着但不响应点击（迁移进行中用）。和 state('disabled') 的区别：
        不禁用渲染，所以背面还是红的、悬停/扫光照旧。
        """
        flag = bool(flag)
        if flag == self._inert:
            return
        self._inert = flag
        if not self._animating:
            self._draw_rest()

    def set_corner_bg(self, new_bg):
        """主题切换时同步画布底色（帧图是**不透明**的，底色变了得重出）。"""
        try:
            self.configure(bg=new_bg)
        except Exception:
            return
        self._drop_photos()
        if not self._animating:
            self._draw_rest()

    def set_command(self, fn):
        self._cmd = fn

    def set_text(self, new_text):
        """改正面文案（等价旧按钮的 set_text）。"""
        self._texts["front"] = i18n.tr(new_text)
        self._texts.pop("front_dry", None)
        if not self._animating:
            self._draw_rest()
        self._texts.setdefault("front_dry", i18n.tr("🚀 模拟运行"))

    def get_text(self):
        _pid, _pal, text = self._face_spec(not self._at_back)
        return text

    def set_gradient(self, c0, c1, h0=None, h1=None):
        """记录正面色标（旧按钮语义）。

        新的外观是原型的固定语义配色（FACE_IDLE / FACE_DRY / FACE_SEARCH / 禁用灰），
        不再由任意渐变色决定 —— 所以这里只更新 _btn_colors 标记并重画，不改配色；
        保留该方法是为了不破坏调用方与 create_gradient_button 的接口一致性。
        """
        self._front_colors = (c0, c1)
        self._btn_colors = (c0, c1)
        if not self._animating:
            self._draw_rest()

    def set_icon(self, pil_img):
        """把面内容换成一张居中图标（传 None 回文字）。"""
        self._icon = pil_img
        self._drop_photos()
        if not self._animating:
            self._draw_rest()

    # ------------------------------------------------------------ 预热
    def prewarm(self):
        """分片预生成翻转帧 + 静止帧相位表（点下去/移上去那一刻就不用现算）。

        为什么分片：一次烤 40+ 帧要 300ms+，放在启动路径上就是一次卡顿；这里每个 idle
        只烤 6 帧翻转帧 + 1 张静止帧，用户真点下去之前基本都烤完了。
        """
        if not _PIL_OK:
            return
        if self._prewarm_job is not None:
            try:
                self.after_cancel(self._prewarm_job)
            except Exception:
                pass
            self._prewarm_job = None
        feel = self._feel
        jobs = []
        for direction in (1, -1):
            tab = self._frame_table(feel, direction)
            jobs.extend((direction, i) for i in range(len(tab)))
        pid, pal, _text = self._face_spec(True)
        rests = []
        if pid != "off":
            for hv in _HV_LEVELS:
                rests.append(("rest", pid, pal, hv))
            for phase in range(_SWEEP_PHASES):
                rests.append(("hover", pid, pal, phase))
        for phase in range(_BACK_PHASES):
            rests.append(("back", "search", self._pal["search"], phase))

        def step(pos=0, rpos=0):
            # 按**时间预算**搬，不按张数：单张约 4.5ms，原来固定搬 6 张 / 1 张静止帧，
            # 等于每 27ms 卡一下主线程 —— 帧表按刷新率加密后这个卡顿更明显。现在每轮
            # 最多干 6ms 就让出，启动后台预热不再抢界面。
            self._prewarm_job = None
            if not self.winfo_exists() or self._feel != feel:
                return
            t_end = time.perf_counter() + _PREWARM_MS_PER_TICK
            while pos < len(jobs) and time.perf_counter() < t_end:
                try:
                    self._frame_photo(feel, jobs[pos][0], jobs[pos][1])
                except Exception:
                    pass
                pos += 1
            while rpos < len(rests) and time.perf_counter() < t_end:
                kind, _p, _pal, arg = rests[rpos]
                try:
                    if kind == "rest":
                        self._rest_photo(_p, _pal, arg)
                    elif kind == "hover":
                        self._hover_photo(_p, _pal, arg)
                    else:
                        self._back_photo(arg)
                except Exception:
                    pass
                rpos += 1
            if pos < len(jobs) or rpos < len(rests):
                self._prewarm_job = self.after(1, lambda: step(pos, rpos))

        self._prewarm_job = self.after(30, step)

    # ------------------------------------------------------------ 事件
    def _on_click(self, event=None):
        if self._disabled or self._inert or self._cmd is None:
            return
        now = time.time()
        if self._click_guard_ms and (now - self._click_at) * 1000 < self._click_guard_ms:
            return
        self._click_at = now
        self.flip_to_back()
        try:
            self._cmd()
        except Exception:
            # 命令自己炸了：把面翻回去再抛，别让按钮永远停在背面
            self.flip_to_front()
            raise

    def _on_enter(self, event=None):
        """指针进按钮：正面开始淡入 + 扫光（由 30fps 的 tick 驱动）。

        Tk 的 <Enter>/<Leave> 是**控件级**事件：指针在画布自己的 text/image item
        上移动不会再触发，只有真进出这块画布才触发 —— 所以指针压在文字上也是亮的。
        """
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self._set_hover(True)

    def _on_leave(self, event=None):
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self._set_hover(False)

    def _set_hover(self, flag):
        """改悬停状态；只有静止态才起 tick / 重绘（动画中途交给动画收尾）。"""
        flag = bool(flag)
        if flag == self._hover:
            return
        self._hover = flag
        # 三种情况不接管画面：
        #   · 翻转途中 —— 那一帧该由动画 tick 决定，塞一张整面图进去就是一次闪；
        #   · 背面 / 禁用 —— 交给 _ui_tick（背面也有悬停高亮，由 tick 追 _hv 再换图）。
        # 状态本身照记（不是丢弃）：后续 _draw_rest 一律按它出图 —— 动画收尾、state 切回
        # normal 都会自己走一次 _draw_rest，所以"点下去翻面、翻回来时指针还在按钮上"
        # 会回到悬停配色，而不是停在常色。
        if self._animating or self._at_back or self._disabled:
            return
        self._draw_rest()

    def _on_destroy(self, event=None):
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self._cancel_anim()
        self._stop_tick()
        if self._prewarm_job is not None:
            try:
                self.after_cancel(self._prewarm_job)
            except Exception:
                pass
            self._prewarm_job = None


def create_flip_button(parent, text="🚀 开始迁移", command=None, colors=_FRONT_GREEN,
                       height=38, font=("微软雅黑", 12, "bold"), feel=DEFAULT_FEEL,
                       dry_run=False, back_text="⏳ 迁移中...", back_colors=_BACK_RED,
                       width=None, click_guard_ms=300, extra_w=0):
    """工厂函数（用法对齐 create_gradient_button）。

    没有 Pillow 或者建控件时出任何意外，都退回旧的 create_gradient_button ——
    主按钮不能因为特效把整个界面拖下水（返回值照样有 state/pack/set_corner_bg）。
    """
    if _PIL_OK:
        try:
            return FlipButton(parent, text=text, command=command, colors=colors,
                              height=height, font=font, feel=feel, dry_run=dry_run,
                              back_text=back_text, back_colors=back_colors,
                              width=width, click_guard_ms=click_guard_ms,
                              extra_w=extra_w)
        except Exception:
            pass
    return _h.create_gradient_button(parent=parent, text=text, command=command,
                                     colors=colors, width=width, height=height,
                                     font=font, click_guard_ms=click_guard_ms)

# utils/helpers.py
import tkinter as tk
import tkinter.font as tkfont
import math
import os
import sys
import time
from pathlib import Path


def warm_up_emoji_font():
    """提前把「第一次画 emoji」的两笔开销做掉，别让它卡在构建界面中途。

    1) 字体回退枚举：Tk 第一次遇到「微软雅黑里没有的字符」会去枚举系统字体找替代，
       实测单个字符约 267 ms。
    2) 带 emoji 的文字排版：量字体并不能预热这一条路——实测第一个带 ⚠️ 的 Label
       单独还要 30~50 ms，第二个才降到 3 ms。

    这两笔都放在启动闪屏刚画出来的时候做掉（那会儿用户刚看到卡片，卡一下看不出来）；
    留到构建中途就会看到立方体突然停住再接着转。
    """
    try:
        font = tkfont.Font(family="微软雅黑", size=9, weight="bold")
        # 界面里用到的 emoji 都列上：这里没覆盖到的字形，第一次画到时还要再查一次回退
        for ch in ("🌓", "📂", "⚠️", "←", "⚙", "☀️", "🌙"):
            font.measure(ch)
    except Exception:
        pass
    try:
        # 顺手把一个带 emoji 的 Label 建了再扔，把上面第 2 笔开销也带走
        lbl = tk.Label(text="⚠️ 预热", font=("微软雅黑", 10, "bold"))
        lbl.destroy()
    except Exception:
        pass


def lighten_color(hex_color, amount=40):
    """把颜色整体调亮，用于鼠标悬停的高亮反馈。"""
    r = min(255, int(hex_color[1:3], 16) + amount)
    g = min(255, int(hex_color[3:5], 16) + amount)
    b = min(255, int(hex_color[5:7], 16) + amount)
    return "#%02x%02x%02x" % (r, g, b)


def hover_pair(colors):
    """悬停时的配色 = 基色两个色标都调亮同样一档。

    以前每个调用点各自传 hover_colors，于是有的按钮悬停变亮、有的把渐变整个反过来、
    还有的变暗——手感乱七八糟。现在统一由这里算：全程序一个规则 = 变亮。
    """
    return (lighten_color(colors[0]), lighten_color(colors[1]))


try:
    from PIL import Image as _PILImage, ImageDraw as _PILDraw, ImageTk as _PILImageTk
    _PIL_OK = True
except Exception:      # 没有 Pillow 也能跑：退回原来的直角矩形画法
    _PIL_OK = False

_BTN_RADIUS = 6        # 渐变按钮圆角半径（像素）；按钮太矮时按高度收紧
_BTN_SS = 4            # 超采样倍数：4 倍画完再缩回来，圆角边缘才不会有锯齿
_BTN_CACHE_MAX = 512
_btn_cache = {}


def _tk_rgb(widget, color):
    """Tk 颜色（#rrggbb 或系统色名）→ PIL 用的 0~255 三元组。"""
    if isinstance(color, (tuple, list)) and len(color) == 3:
        return tuple(max(0, min(255, int(c))) for c in color)
    try:
        r, g, b = widget.winfo_rgb(color)
        return (r // 256, g // 256, b // 256)
    except Exception:
        return (240, 240, 240)


def _rounded_gradient(width, height, colors, hover, radius, widget):
    """按尺寸+配色缓存地生成「圆角渐变图」（RGBA，四角透明）。

    四角为什么用透明而不是涂成父容器底色：涂底色的话，一换主题所有按钮都得重新
    出图；透明角交给 Tk 自己跟画布底色混合，换主题只要改画布底色就行。
    """
    c0 = _tk_rgb(widget, colors[0])
    c1 = _tk_rgb(widget, colors[1])
    h0 = _tk_rgb(widget, hover[0])
    h1 = _tk_rgb(widget, hover[1])
    key = (width, height, c0, c1, h0, h1, radius)
    hit = _btn_cache.get(key)
    if hit is not None:
        return hit

    W, H = width * _BTN_SS, height * _BTN_SS
    r = max(0, min(int(radius) * _BTN_SS, min(W, H) // 2))

    def band(a, b):
        # 竖直渐变：先画 1 像素宽的一列再横向拉满，比逐像素填整张图快得多
        col = _PILImage.new("RGB", (1, H))
        px = col.load()
        for y in range(H):
            t = y / (H - 1) if H > 1 else 0.0
            px[0, y] = (int(a[0] + (b[0] - a[0]) * t),
                        int(a[1] + (b[1] - a[1]) * t),
                        int(a[2] + (b[2] - a[2]) * t))
        return col.resize((W, H))

    def rounded(base):
        img = base.convert("RGBA")
        if r > 0:
            mask = _PILImage.new("L", (W, H), 0)
            _PILDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1],
                                                  radius=r, fill=255)
            img.putalpha(mask)
        return img.resize((width, height), _PILImage.LANCZOS)

    imgs = {"normal": rounded(band(c0, c1)), "hover": rounded(band(h0, h1))}
    if len(_btn_cache) >= _BTN_CACHE_MAX:
        _btn_cache.clear()
    _btn_cache[key] = imgs
    return imgs


_icon_cache = {}


def make_theme_icon(kind, size=20, color="#ffffff"):
    """手绘的月亮/太阳图标（RGBA、纯白）。

    为什么不用 emoji：15pt 的 ☀️ 在 Tk 里就是一坨圆点加花边，用户根本不认。
    自己画的话圆心半径、光芒长度粗细全由自己定，配 4 倍超采样也够锐。
    """
    key = (kind, size, color)
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    if not _PIL_OK:
        return None

    S = size * _BTN_SS
    img = _PILImage.new("RGBA", (S, S), (0, 0, 0, 0))
    d = _PILDraw.Draw(img)
    c = S / 2.0

    if kind == "sun":
        # 参数是比出来的：核心太大+光芒太细像蒲公英，光芒太粗又像隔壁那个齿轮，
        # 这组（核心 r=0.22S，光芒 0.32S→0.48S，粗 0.10S）在 22px 下最像太阳。
        r_core = 0.22 * S
        d.ellipse([c - r_core, c - r_core, c + r_core, c + r_core], fill=color)
        w = max(2, int(round(0.10 * S)))
        r0, r1 = 0.32 * S, 0.48 * S
        for i in range(8):
            a = math.radians(i * 45)
            x0, y0 = c + r0 * math.cos(a), c + r0 * math.sin(a)
            x1, y1 = c + r1 * math.cos(a), c + r1 * math.sin(a)
            d.line([x0, y0, x1, y1], fill=color, width=w)
            # line 是方头，两端各补一个小圆，光芒才不是一截一截的方块
            for px_, py_ in ((x0, y0), (x1, y1)):
                d.ellipse([px_ - w / 2.0, py_ - w / 2.0,
                           px_ + w / 2.0, py_ + w / 2.0], fill=color)
    else:
        # 月牙 = 大圆减掉一个偏移的圆
        pad = 0.09 * S
        d.ellipse([pad, pad, S - pad, S - pad], fill=color)
        cut = _PILImage.new("L", (S, S), 0)
        off = 0.33 * S
        _PILDraw.Draw(cut).ellipse([pad + off, pad - 0.14 * S,
                                    S - pad + off, S - pad - 0.14 * S], fill=255)
        img.putalpha(_PILImage.composite(_PILImage.new("L", (S, S), 0),
                                         img.getchannel("A"), cut))

    img = img.resize((size, size), _PILImage.LANCZOS)
    if len(_icon_cache) > 64:
        _icon_cache.clear()
    _icon_cache[key] = img
    return img


def create_gradient_button(parent, text, command, colors=("#00bcd4", "#3f51b5"),
                           width=180, height=32, font=("微软雅黑", 10, "bold"),
                           click_guard_ms=300):
    state = {"colors": colors, "hover": hover_pair(colors), "text": text, "icon": None}
    radius = max(0, min(_BTN_RADIUS, height // 3))
    use_img = bool(_PIL_OK and radius > 0)
    canvas = tk.Canvas(parent, width=width, height=height, highlightthickness=0,
                       bg=parent.cget("bg"))
    canvas.pack_propagate(False)
    canvas._icon_photo = None
    bg_id = {"id": None}

    def _photos():
        got = _rounded_gradient(width, height, state["colors"], state["hover"],
                               radius, parent)
        return {k: _PILImageTk.PhotoImage(v) for k, v in got.items()}

    # PhotoImage 必须留引用，否则会被回收成空白
    canvas._btn_photos = _photos() if use_img else {}

    def draw_bg(hover=False):
        if use_img:
            key = "hover" if hover else "normal"
            if bg_id["id"] is None:
                bg_id["id"] = canvas.create_image(0, 0, anchor="nw",
                                                  image=canvas._btn_photos[key],
                                                  tags="bg")
            else:
                # 换图比重画几十行矩形便宜，悬停手感更稳
                canvas.itemconfigure(bg_id["id"], image=canvas._btn_photos[key])
            return
        canvas.delete("bg")
        bg_id["id"] = None
        c0, c1 = state["hover"] if hover else state["colors"]
        for i in range(height):
            ratio = i / height
            r = int(int(c0[1:3], 16) + (int(c1[1:3], 16) - int(c0[1:3], 16)) * ratio)
            g = int(int(c0[3:5], 16) + (int(c1[3:5], 16) - int(c0[3:5], 16)) * ratio)
            b = int(int(c0[5:7], 16) + (int(c1[5:7], 16) - int(c0[5:7], 16)) * ratio)
            color = f"#{r:02x}{g:02x}{b:02x}"
            canvas.create_rectangle(0, i, width, i+1, fill=color, outline="", tags="bg")

    def draw_content():
        """画按钮内容：有图标就贴图标，否则写文字。悬停/换色后都要重画一次。"""
        canvas.delete("text")
        canvas.delete("icon")
        canvas._icon_photo = None
        icon = state.get("icon")
        if icon is not None and _PIL_OK:
            canvas._icon_photo = _PILImageTk.PhotoImage(icon)   # 必须留引用
            canvas.icon_id = canvas.create_image(width // 2, height // 2,
                                                 image=canvas._icon_photo, tags="icon")
            return
        canvas.text_id = canvas.create_text(width//2, height//2, text=state["text"],
                                            fill="white", font=font, tags="text")

    def set_gradient(c0, c1, h0=None, h1=None):
        """热切换渐变配色（用于溢出提示等）。没给悬停色就按新基色重算高亮。"""
        state["colors"] = (c0, c1)
        state["hover"] = (h0, h1) if (h0 and h1) else hover_pair((c0, c1))
        if use_img:
            canvas._btn_photos = _photos()
        draw_bg(False)
        draw_content()

    canvas.set_gradient = set_gradient

    def set_corner_bg(new_bg):
        """主题切换时同步画布底色：圆角是透明的，由 Tk 拿画布底色去混。

        必须用「父容器的真实底色」而不是主题里的 bg —— 按钮可能坐在 LabelFrame
        这类底色不同的容器上，用错了四角会露出一点别的颜色。
        """
        try:
            canvas.configure(bg=new_bg)
        except Exception:
            pass
    canvas.set_corner_bg = set_corner_bg

    # 支持像普通 Button 一样动态改 command / state
    cmd = {"fn": command}
    def set_command(fn):
        cmd["fn"] = fn
    canvas.set_command = set_command

    def set_text(new_text):
        """动态改按钮文字（替代 tk.Button 的 config(text=...)）。"""
        state["text"] = new_text
        state["icon"] = None      # 回到文字模式
        draw_content()
    canvas.set_text = set_text

    def set_icon(pil_img):
        """把按钮内容换成一张居中的图标图（PIL Image）；传 None 回到文字。"""
        state["icon"] = pil_img
        draw_content()
    canvas.set_icon = set_icon

    flags = {"disabled": False}
    def state_get(key):
        return flags.get(key, False)
    canvas.state = lambda new_state=None: (None if new_state is None else flags.update(
        {"disabled": str(new_state).lower() == "disabled"}))
    canvas._base_colors = tuple(colors)
    canvas._base_hover = tuple(state["hover"])

    def on_enter(e):
        if not state_get("disabled"):
            draw_bg(True)
            draw_content()
    def on_leave(e):
        if not state_get("disabled"):
            draw_bg(False)
            draw_content()
    click_at = {"t": 0.0}

    def on_click(e):
        if state_get("disabled") or cmd["fn"] is None:
            return
        # 防连点/误双击：冷却期内忽略重复点击，避免一个按钮被连点触发多次重活
        now = time.time()
        if click_guard_ms and (now - click_at["t"]) * 1000 < click_guard_ms:
            return
        click_at["t"] = now
        cmd["fn"]()

    draw_bg(False)
    draw_content()
    canvas.bind("<Enter>", on_enter)
    canvas.bind("<Leave>", on_leave)
    canvas.bind("<Button-1>", on_click)
    return canvas

def center_window(win, w=None, h=None):
    """把窗口居中到屏幕。

    关键：必须在窗口仍然 withdraw 的时候调用，然后再 deiconify —— 否则会看到
    「先闪现在某个角落、再跳到屏幕中间」的瞬移。
    w/h 省略或无效时按窗口自身尺寸计算。
    """
    try:
        win.update_idletasks()
        if not w or w <= 1:
            w = win.winfo_width()
        if not h or h <= 1:
            h = win.winfo_height()
        if w <= 1:
            w = win.winfo_reqwidth()
        if h <= 1:
            h = win.winfo_reqheight()
        x = max(0, (win.winfo_screenwidth() - w) // 2)
        y = max(0, (win.winfo_screenheight() - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        pass


def circular_reveal(win, cx, cy, on_switch=None, steps=30, interval=13, on_done=None):
    """真·圆形揭示过渡：圆内是「新界面」，圆外是「旧界面」。

    Tk 既不能给窗口做圆形裁剪，画布也没有 alpha 合成，所以分成四步：
      1. 用 Pillow 把窗口当前画面截下来（这就是「旧界面」）
      2. 用一个无边框覆盖窗原样盖住 —— 此时用户察觉不到任何变化
      3. 在覆盖层底下真正切换主题
      4. 用 Windows 的 SetWindowRgn 在截图上挖一个不断扩大的「圆洞」，
         把下面的新界面一点点露出来
    cx/cy 是屏幕坐标。任何一步失败都会立刻执行切换，功能不受影响。
    """
    state = {"switched": False, "done": False, "ov": None}

    def do_switch():
        if not state["switched"]:
            state["switched"] = True
            if on_switch:
                try:
                    on_switch()
                except Exception:
                    pass

    def finish():
        if not state["done"]:
            state["done"] = True
            do_switch()          # 兜底：无论走哪条路径，主题都必须切过去
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass

    try:
        import ctypes
        from PIL import ImageGrab, ImageTk

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        # 64 位下句柄必须声明成指针，否则会被截断成 32 位
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        gdi32.CreateRectRgn.restype = ctypes.c_void_p
        gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        gdi32.CreateEllipticRgn.restype = ctypes.c_void_p
        gdi32.CreateEllipticRgn.argtypes = [ctypes.c_int] * 4
        gdi32.CombineRgn.restype = ctypes.c_int
        gdi32.CombineRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_int]
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]

        # 覆盖范围必须把已打开的子窗口也算进去：否则它们不会被截图遮住，
        # 主窗口还在做圆形揭示时它们就已经变成新主题了，看起来会不同步。
        wins = [win]
        try:
            for ch in win.winfo_children():
                if isinstance(ch, tk.Toplevel) and ch.winfo_ismapped():
                    wins.append(ch)
        except Exception:
            pass
        x0 = min(x.winfo_rootx() for x in wins)
        y0 = min(x.winfo_rooty() for x in wins)
        x1 = max(x.winfo_rootx() + x.winfo_width() for x in wins)
        y1 = max(x.winfo_rooty() + x.winfo_height() for x in wins)
        w, h = x1 - x0, y1 - y0
        if w <= 1 or h <= 1:
            finish()
            return None

        # 1) 截下旧界面
        shot = ImageGrab.grab(bbox=(x0, y0, x0 + w, y0 + h))
        photo = ImageTk.PhotoImage(shot)

        # 2) 覆盖层原样显示旧界面，所以下面的切换是「偷偷」发生的
        ov = tk.Toplevel(win)
        ov._is_theme_overlay = True      # 主题刷新时要跳过它（内容是一张截图）
        state["ov"] = ov
        ov.overrideredirect(True)
        ov.geometry(f"{w}x{h}+{x0}+{y0}")
        cv = tk.Canvas(ov, width=w, height=h, highlightthickness=0, bd=0)
        cv.pack()
        cv.create_image(0, 0, anchor="nw", image=photo)
        cv.image = photo              # 保住引用，否则图片被回收会变成空白
        try:
            ov.attributes("-topmost", True)
        except Exception:
            pass
        ov.update_idletasks()
        ov.deiconify()
        ov.update()

        # 3) 在覆盖层底下真正切换主题
        do_switch()
        ov.update()

        target = user32.GetParent(ctypes.c_void_p(ov.winfo_id())) or ov.winfo_id()
        px = min(max(0, cx - x0), w)
        py = min(max(0, cy - y0), h)
        # 半径要够大到让圆能盖住离圆心最远的那个角
        full_r = int(((max(px, w - px)) ** 2 + (max(py, h - py)) ** 2) ** 0.5) + 2

        def set_hole(r):
            """把覆盖层裁成「矩形 - 圆」，圆的位置就是露出来的新界面。"""
            box = gdi32.CreateRectRgn(0, 0, w, h)
            circ = gdi32.CreateEllipticRgn(int(px - r), int(py - r),
                                           int(px + r), int(py + r))
            if circ:
                gdi32.CombineRgn(ctypes.c_void_p(box), ctypes.c_void_p(box),
                                 ctypes.c_void_p(circ), 4)      # RGN_DIFF
                gdi32.DeleteObject(ctypes.c_void_p(circ))       # 未被系统接管，自己删
            if box:
                # box 交给系统后不能再删
                user32.SetWindowRgn(ctypes.c_void_p(target), ctypes.c_void_p(box), True)

        set_hole(0)
        step = {"i": 0}

        def tick():
            step["i"] += 1
            t = step["i"] / float(steps)
            eased = 1 - (1 - t) ** 3        # ease-out：先快后慢，收尾更自然
            set_hole(int(full_r * eased))
            if step["i"] < steps:
                ov.after(interval, tick)
            else:
                try:
                    ov.destroy()
                except Exception:
                    pass
                finish()

        ov.after(interval, tick)
        return ov
    except Exception:
        ov = state.get("ov")
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass
        finish()
        return None


def clear_layered_style(win):
    """摘掉窗口的 WS_EX_LAYERED 样式。

    Tk 只要用过一次 -alpha，这个样式就一直留在窗口上；而 **Windows 对 layered
    窗口会跳过系统的隐藏/显示过渡动画**——实测 withdraw() 之后窗口直接消失，
    连同为普通窗口时那段约 190ms 的淡出都没有。
    等淡入结束、窗口已经完全不透明时把它摘掉，窗口就恢复成普通窗口的行为，
    关闭时系统那段动画才会回来。摘掉时窗口是 alpha=1.0，所以视觉上没有变化。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_long]
        hwnd = user32.GetParent(ctypes.c_void_p(win.winfo_id())) or win.winfo_id()
        GWL_EXSTYLE, WS_EX_LAYERED = -20, 0x00080000
        ex = user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
        if ex & WS_EX_LAYERED:
            user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE,
                                  ex & ~WS_EX_LAYERED)
        return True
    except Exception:
        return False


def focus_window(win):
    """把焦点交给刚弹出的窗口，并把它提到最前。

    必须在 deiconify() 之后调用 —— 窗口还隐藏时抢焦点是无效的。
    """
    try:
        win.lift()
    except Exception:
        pass
    try:
        win.focus_force()
    except Exception:
        pass


def get_icon_path():
    """获取图标文件路径（支持开发环境和打包环境）"""
    import sys
    import os
    from pathlib import Path

    # 1. 打包后路径（sys._MEIPASS）
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
        # 在打包后的临时目录根目录查找
        icon_path = os.path.join(base_path, "1.ico")
        if os.path.exists(icon_path):
            return icon_path

    # 2. 开发环境：从当前文件（utils/helpers.py）向上找两级
    current_dir = Path(__file__).parent  # utils/
    project_root = current_dir.parent     # 项目根目录
    icon_path = project_root / "1.ico"
    if icon_path.exists():
        return str(icon_path)

    # 3. 尝试当前工作目录
    icon_path = Path.cwd() / "1.ico"
    if icon_path.exists():
        return str(icon_path)

    # 4. 都没找到，返回 None
    return None

def set_window_icon(window):
    icon_path = get_icon_path()
    if icon_path:
        try:
            window.iconbitmap(icon_path)
        except:
            pass
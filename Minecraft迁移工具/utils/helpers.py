# utils/helpers.py
import tkinter as tk
import tkinter.font as tkfont
import os
import sys
import time
from pathlib import Path


def warm_up_emoji_font():
    """把 Tk 的 emoji 字体回退提前查一次，免掉界面上第一个 emoji 白卡的那 260 ms。

    Tk 在 Windows 上第一次遇到「微软雅黑里没有的字符」时会去枚举系统字体找替代，
    实测单个字符约 267 ms；界面里第一个带 emoji 的按钮就会卡这么久。这里做的是
    完全一样的调用，只是提前到启动闪屏刚画出来的时候——那会儿用户刚看到卡片，
    卡一下看不出来；留到构建界面中途，就会看到立方体突然停住再接着转。
    一个字符预热完，后面所有 emoji 都便宜了（实测 10~18 ms，就是按钮本身的成本）。
    """
    try:
        font = tkfont.Font(family="微软雅黑", size=9, weight="bold")
        for ch in ("🌓", "📂", "⚠️", "←"):
            font.measure(ch)
    except Exception:
        pass


def create_gradient_button(parent, text, command, colors=("#00bcd4", "#3f51b5"),
                           hover_colors=None, width=180, height=32, font=("微软雅黑", 10, "bold"),
                           click_guard_ms=300):
    if hover_colors is None:
        def lighten(hex_color, amount=40):
            r = min(255, int(hex_color[1:3], 16) + amount)
            g = min(255, int(hex_color[3:5], 16) + amount)
            b = min(255, int(hex_color[5:7], 16) + amount)
            return f"#{r:02x}{g:02x}{b:02x}"
        hover_colors = (lighten(colors[0]), lighten(colors[1]))

    state = {"colors": colors, "hover": hover_colors, "text": text}
    canvas = tk.Canvas(parent, width=width, height=height, highlightthickness=0,
                       bg=parent.cget("bg"))
    canvas.pack_propagate(False)

    def draw_bg(hover=False):
        canvas.delete("bg")
        c0, c1 = state["hover"] if hover else state["colors"]
        for i in range(height):
            ratio = i / height
            r = int(int(c0[1:3], 16) + (int(c1[1:3], 16) - int(c0[1:3], 16)) * ratio)
            g = int(int(c0[3:5], 16) + (int(c1[3:5], 16) - int(c0[3:5], 16)) * ratio)
            b = int(int(c0[5:7], 16) + (int(c1[5:7], 16) - int(c0[5:7], 16)) * ratio)
            color = f"#{r:02x}{g:02x}{b:02x}"
            canvas.create_rectangle(0, i, width, i+1, fill=color, outline="", tags="bg")

    def draw_text():
        canvas.delete("text")
        canvas.text_id = canvas.create_text(width//2, height//2, text=state["text"],
                                            fill="white", font=font, tags="text")

    def set_gradient(c0, c1, h0=None, h1=None):
        """热切换渐变配色（用于溢出提示等）。"""
        state["colors"] = (c0, c1)
        if h0 and h1:
            state["hover"] = (h0, h1)
        draw_bg(False)
        draw_text()

    canvas.set_gradient = set_gradient

    # 支持像普通 Button 一样动态改 command / state
    cmd = {"fn": command}
    def set_command(fn):
        cmd["fn"] = fn
    canvas.set_command = set_command

    def set_text(new_text):
        """动态改按钮文字（替代 tk.Button 的 config(text=...)）。"""
        state["text"] = new_text
        draw_text()
    canvas.set_text = set_text

    flags = {"disabled": False}
    def state_get(key):
        return flags.get(key, False)
    canvas.state = lambda new_state=None: (None if new_state is None else flags.update(
        {"disabled": str(new_state).lower() == "disabled"}))
    canvas._base_colors = tuple(colors)
    canvas._base_hover = tuple(hover_colors)

    def on_enter(e):
        if not state_get("disabled"):
            draw_bg(True)
            draw_text()
    def on_leave(e):
        if not state_get("disabled"):
            draw_bg(False)
            draw_text()
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
    draw_text()
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
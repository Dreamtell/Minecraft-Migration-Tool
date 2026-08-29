# utils/helpers.py
import tkinter as tk
import os
import sys
from pathlib import Path

def create_gradient_button(parent, text, command, colors=("#00bcd4", "#3f51b5"),
                           hover_colors=None, width=180, height=32, font=("微软雅黑", 10, "bold")):
    if hover_colors is None:
        def lighten(hex_color, amount=40):
            r = min(255, int(hex_color[1:3], 16) + amount)
            g = min(255, int(hex_color[3:5], 16) + amount)
            b = min(255, int(hex_color[5:7], 16) + amount)
            return f"#{r:02x}{g:02x}{b:02x}"
        hover_colors = (lighten(colors[0]), lighten(colors[1]))

    state = {"colors": colors, "hover": hover_colors}
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
        canvas.text_id = canvas.create_text(width//2, height//2, text=text,
                                            fill="white", font=font, tags="text")

    def set_gradient(c0, c1, h0=None, h1=None):
        """热切换渐变配色（用于溢出提示等）。"""
        state["colors"] = (c0, c1)
        if h0 and h1:
            state["hover"] = (h0, h1)
        draw_bg(False)
        draw_text()

    canvas.set_gradient = set_gradient

    def on_enter(e):
        draw_bg(True)
        draw_text()
    def on_leave(e):
        draw_bg(False)
        draw_text()
    def on_click(e):
        command()

    canvas._base_colors = tuple(colors)
    canvas._base_hover = tuple(hover_colors)
    draw_bg(False)
    draw_text()
    canvas.bind("<Enter>", on_enter)
    canvas.bind("<Leave>", on_leave)
    canvas.bind("<Button-1>", on_click)
    return canvas

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
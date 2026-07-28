#!/usr/bin/env python3
# 本工具由 Dreamtell 与 AI DeepSeek 协作完成，源码完全开源，欢迎 fork 和改进。
# 开源协议：MIT License
# 作者：Dreamtell
# 说明：本工具完全免费，仅供学习交流使用。严禁用于商业用途或转卖。
"""
Minecraft 整合包迁移工具 - 增强版 v2
- 实时检测路径合法性
- 一键目标路径复制到源路径
- 支持迁移 config（可选择文件夹）
- 所有用户输入自动保存
- 更健壮的错误处理
- 兼容 PyInstaller 打包
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import threading
import shutil
import re
import json
import time
import os
import sys
from pathlib import Path
import traceback
import ctypes
import zipfile
import queue
from tendo import singleton
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

CONFIG_FILE = Path.home() / ".minecraft_migrate_config.json"

# 主题颜色定义
LIGHT_THEME = {
    "bg": "#f0f0f0",
    "fg": "#000000",
    "entry_bg": "#ffffff",
    "entry_fg": "#000000",
    "button_bg": "#e0e0e0",
    "button_fg": "#000000",
    "label_bg": "#f0f0f0",
    "label_fg": "#000000",
    "labelframe_bg": "#f0f0f0",
    "labelframe_fg": "#000000",
    "text_bg": "#ffffff",
    "text_fg": "#000000",
    "log_bg": "#ffffff",
    "log_fg": "#000000",
    "warning_bg": "#ffcccc",
    "warning_fg": "#ff0000",
    "bottom_bg": "#ffffff",
    "bottom_fg": "gray",
    "tooltip_bg": "#ffffe0"
}

DARK_THEME = {
    "bg": "#2e2e2e",
    "fg": "#ffffff",
    "entry_bg": "#3e3e3e",
    "entry_fg": "#ffffff",
    "button_bg": "#4e4e4e",
    "button_fg": "#ffffff",
    "label_bg": "#2e2e2e",
    "label_fg": "#ffffff",
    "labelframe_bg": "#2e2e2e",
    "labelframe_fg": "#ffffff",
    "text_bg": "#3e3e3e",
    "text_fg": "#ffffff",
    "log_bg": "#1e1e1e",
    "log_fg": "#ffffff",
    "warning_bg": "#553333",
    "warning_fg": "#ff8888",
    "bottom_bg": "#2e2e2e",
    "bottom_fg": "#aaaaaa",
    "tooltip_bg": "#3e3e3e"
}
def create_gradient_button(parent, text, command, colors=("#00bcd4", "#3f51b5"), hover_colors=None, width=180, height=32, font=("微软雅黑", 10, "bold")):
    if hover_colors is None:
        def lighten(hex_color, amount=40):
            r = min(255, int(hex_color[1:3], 16) + amount)
            g = min(255, int(hex_color[3:5], 16) + amount)
            b = min(255, int(hex_color[5:7], 16) + amount)
            return f"#{r:02x}{g:02x}{b:02x}"
        hover_colors = (lighten(colors[0]), lighten(colors[1]))

    canvas = tk.Canvas(parent, width=width, height=height, highlightthickness=0, bg=parent.cget("bg"))
    canvas.pack_propagate(False)

    def draw_bg(hover=False):
        canvas.delete("bg")
        c0, c1 = hover_colors if hover else colors
        for i in range(height):
            ratio = i / height
            r = int(int(c0[1:3], 16) + (int(c1[1:3], 16) - int(c0[1:3], 16)) * ratio)
            g = int(int(c0[3:5], 16) + (int(c1[3:5], 16) - int(c0[3:5], 16)) * ratio)
            b = int(int(c0[5:7], 16) + (int(c1[5:7], 16) - int(c0[5:7], 16)) * ratio)
            color = f"#{r:02x}{g:02x}{b:02x}"
            canvas.create_rectangle(0, i, width, i+1, fill=color, outline="", tags="bg")

    def draw_text():
        canvas.delete("text")
        # 保存文本 ID 到 canvas 属性
        canvas.text_id = canvas.create_text(width//2, height//2, text=text, fill="white", font=font, tags="text")

    def on_enter(e):
        draw_bg(True)
        draw_text()
    def on_leave(e):
        draw_bg(False)
        draw_text()
    def on_click(e):
        command()

    draw_bg(False)
    draw_text()
    canvas.bind("<Enter>", on_enter)
    canvas.bind("<Leave>", on_leave)
    canvas.bind("<Button-1>", on_click)
    return canvas
class SplashScreen:
    """极致炫酷 Pygame 启动动画（启动速度优化版）"""
    def __init__(self, on_finish):
        # 延迟导入，减少主程序启动时间
        import pygame
        import pygame.gfxdraw
        import math
        import random
        self.pygame = pygame
        self.gfxdraw = pygame.gfxdraw
        self.math = math
        self.random = random

        self.on_finish = on_finish
        self.WIDTH, self.HEIGHT = 600, 400
        self.SCALE = 2
        self.RW = self.WIDTH * self.SCALE
        self.RH = self.HEIGHT * self.SCALE

        pygame.init()
        self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT), pygame.NOFRAME)
        pygame.display.set_caption("迁移工坊 · 启动中")
        self.render_surf = pygame.Surface((self.RW, self.RH), pygame.SRCALPHA)

        # ---- 窗口居中 + 圆角 ----
        try:
            hwnd = pygame.display.get_wm_info()['window']
            ctypes.windll.user32.SetWindowPos(hwnd, -1,
                (ctypes.windll.user32.GetSystemMetrics(0) - self.WIDTH) // 2,
                (ctypes.windll.user32.GetSystemMetrics(1) - self.HEIGHT) // 2,
                0, 0, 0x0001)
            from ctypes import windll
            RGN = windll.gdi32.CreateRoundRectRgn(0, 0, self.WIDTH, self.HEIGHT, 20, 20)
            windll.user32.SetWindowRgn(hwnd, RGN, True)
        except:
            pass

        self.clock = pygame.time.Clock()
        self.running = True
        self.progress = 0
        self.angle = 0

        # ---- 粒子减少到 100 个（原 240） ----
        self.particles = []
        for _ in range(100):
            self.particles.append({
                'x': random.randint(0, self.RW),
                'y': random.randint(0, self.RH),
                'size': random.uniform(2, 6) * self.SCALE,
                'dx': random.uniform(-0.6, 0.6) * self.SCALE,
                'dy': random.uniform(-0.6, 0.6) * self.SCALE,
                'phase': random.uniform(0, 6.28)
            })

        # ---- 状态文字 ----
        self.status_texts = [
            "🔮 解析时空坐标...", "⚡ 唤醒核心引擎...",
            "🧬 同步武器数据...", "🌌 校准维度裂隙...",
            "🛠️ 加载模组矩阵...", "💾 重建区块记忆...",
            "🔥 注入赛博灵能...", "✨ 准备跃迁..."
        ]
        self.text_index = 0
        self.text_timer = 0

        # ---- 字体 ----
        self.font_big = pygame.font.SysFont("microsoftyahei", int(28 * self.SCALE), bold=True)
        self.font_small = pygame.font.SysFont("microsoftyahei", int(14 * self.SCALE))
        self.font_status = pygame.font.SysFont("microsoftyahei", int(13 * self.SCALE))
        self.font_pct = pygame.font.SysFont("consolas", int(16 * self.SCALE), bold=True)
        self.font_symbol = pygame.font.SysFont("segoeuisymbol", int(28 * self.SCALE))
        self.font_symbol_small = pygame.font.SysFont("segoeuisymbol", int(13 * self.SCALE))

        self.loop()

    def loop(self):
        pygame = self.pygame
        random = self.random
        math = self.math

        while self.running and self.progress < 100:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    pygame.quit()
                    return
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.running = False
                    pygame.quit()
                    return

            # 更新进度
            if self.progress < 100:
                self.progress += random.uniform(4, 2.0)
                if self.progress > 100:
                    self.progress = 100

            self.angle = (self.angle + 1.2) % 360
            self.text_timer += 1
            if self.text_timer > 18:
                self.text_timer = 0
                self.text_index = (self.text_index + 1) % len(self.status_texts)

            # 更新粒子
            for p in self.particles:
                p['x'] += p['dx'] + math.sin(self.angle * 0.008 + p['phase']) * 0.2 * self.SCALE
                p['y'] += p['dy'] + math.cos(self.angle * 0.008 + p['phase']) * 0.2 * self.SCALE
                if p['x'] < 0: p['x'] = self.RW
                if p['x'] > self.RW: p['x'] = 0
                if p['y'] < 0: p['y'] = self.RH
                if p['y'] > self.RH: p['y'] = 0

            # ---- 绘制到离屏表面 ----
            self.render_surf.fill((0, 0, 0, 0))

            # 1. 背景渐变
            for i in range(self.RH):
                ratio = i / self.RH
                r = int(8 + 12 * ratio)
                g = int(6 + 18 * ratio)
                b = int(20 + 35 * ratio)
                pygame.draw.line(self.render_surf, (r, g, b), (0, i), (self.RW, i))

            # 2. 粒子（抗锯齿）
            for p in self.particles:
                brightness = 0.6 + 0.4 * math.sin(pygame.time.get_ticks() * 0.0015 + p['phase'])
                col = (int(80*brightness+30), int(120*brightness+40), int(220*brightness+20))
                x, y = int(p['x']), int(p['y'])
                r = int(p['size'])
                pygame.gfxdraw.filled_circle(self.render_surf, x, y, r, col)
                pygame.gfxdraw.aacircle(self.render_surf, x, y, r, col)

            # 3. 中心双光环
            cx, cy = self.RW // 2, int(140 * self.SCALE)
            radius = int(55 * self.SCALE)
            for offset in range(0, 360, 5):
                rad = math.radians(self.angle + offset)
                x = cx + (radius + 8*self.SCALE) * math.cos(rad)
                y = cy + (radius + 8*self.SCALE) * math.sin(rad)
                alpha = 0.3 + 0.7 * (abs(offset - 180) / 180)
                col = (min(255, int(80*alpha)), min(255, int(200*alpha)), min(255, int(255*alpha)))
                px, py = int(x), int(y)
                pygame.gfxdraw.filled_circle(self.render_surf, px, py, 4, col)
                pygame.gfxdraw.aacircle(self.render_surf, px, py, 4, col)
            for offset in range(0, 360, 4):
                rad = math.radians(-self.angle * 1.3 + offset)
                x = cx + (radius - 12*self.SCALE) * math.cos(rad)
                y = cy + (radius - 12*self.SCALE) * math.sin(rad)
                alpha = 0.4 + 0.6 * (abs(offset - 90) / 180)
                col = (min(255, int(180*alpha)), min(255, int(80*alpha)), min(255, int(255*alpha)))
                px, py = int(x), int(y)
                pygame.gfxdraw.filled_circle(self.render_surf, px, py, 5, col)
                pygame.gfxdraw.aacircle(self.render_surf, px, py, 5, col)

            # 4. 核心光球
            pulse = 1 + 0.08 * math.sin(pygame.time.get_ticks() * 0.003)
            core_radius = int(18 * self.SCALE * pulse)
            for i in range(6, 0, -1):
                r = core_radius + i * 8 * self.SCALE
                alpha = 40 - i * 5
                surf = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
                pygame.draw.circle(surf, (int(60*(1-i/7)), int(120*(1-i/7)), int(255*(1-i/7)), alpha), (r, r), r)
                self.render_surf.blit(surf, (cx-r, cy-r))
            pygame.draw.circle(self.render_surf, (220, 240, 255), (cx, cy), core_radius - 4)

            # 5. 标题
            title_y = int(210 * self.SCALE)
            symbol = self.font_symbol.render("⚙️", True, (150, 230, 255))
            text_cn = self.font_big.render("迁移工坊", True, (150, 230, 255))
            total_w = symbol.get_width() + text_cn.get_width() + int(8*self.SCALE)
            start_x = cx - total_w // 2
            self.render_surf.blit(symbol, (start_x, title_y))
            self.render_surf.blit(text_cn, (start_x + symbol.get_width() + int(8*self.SCALE), title_y))

            # 副标题
            sub_y = int(248 * self.SCALE)
            sub = self.font_small.render("Minecraft 整合包 · 时空跃迁引擎", True, (150, 180, 220))
            self.render_surf.blit(sub, (cx - sub.get_width() // 2, sub_y))

            # 6. 进度条
            bar_x, bar_y = int(80 * self.SCALE), int(305 * self.SCALE)
            bar_w, bar_h = int(440 * self.SCALE), int(18 * self.SCALE)
            pygame.draw.rect(self.render_surf, (30, 35, 50), (bar_x, bar_y, bar_w, bar_h), border_radius=int(9*self.SCALE))
            fill_w = int((self.progress / 100) * bar_w)
            if fill_w > 0:
                color_fill = (80, 200, 255)
                pygame.draw.rect(self.render_surf, color_fill, (bar_x, bar_y, fill_w, bar_h), border_radius=int(9*self.SCALE))
            pygame.draw.rect(self.render_surf, (100, 150, 200), (bar_x, bar_y, bar_w, bar_h), 1, border_radius=int(9*self.SCALE))

            # 百分比
            pct_text = self.font_pct.render(f"{int(self.progress)}%", True, (180, 220, 255))
            self.render_surf.blit(pct_text, (bar_x + bar_w + int(15*self.SCALE), bar_y - int(2*self.SCALE)))

            # 状态文字（分段渲染 emoji + 中文）
            import re
            status = self.status_texts[self.text_index]
            emoji_pattern = re.compile("[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002700-\U000027BF\U00002600-\U000026FF]",re.UNICODE)
            parts = emoji_pattern.split(status)
            emojis = emoji_pattern.findall(status)
            x_offset = bar_x
            y_pos = bar_y + bar_h + int(15*self.SCALE)
            for i, part in enumerate(parts):
                if part:
                    surf_cn = self.font_status.render(part, True, (180, 200, 230))
                    self.render_surf.blit(surf_cn, (x_offset, y_pos))
                    x_offset += surf_cn.get_width()
                if i < len(emojis):
                    surf_emoji = self.font_symbol_small.render(emojis[i], True, (180, 200, 230))
                    self.render_surf.blit(surf_emoji, (x_offset, y_pos))
                    x_offset += surf_emoji.get_width()

            # 缩放回窗口并显示
            scaled = pygame.transform.smoothscale(self.render_surf, (self.WIDTH, self.HEIGHT))
            self.screen.blit(scaled, (0, 0))
            pygame.display.flip()
            self.clock.tick(60)

        # 结束
        pygame.quit()
        if self.on_finish:
            self.on_finish()
class ProgressWindow:
    """迁移进度模态窗口"""
    def __init__(self, parent, total_files, total_size):
        self.parent = parent
        self.total_files = total_files
        self.total_size = total_size  # 字节
        self.cancelled = False

        self.win = tk.Toplevel(parent)
        self.win.title("迁移进度")
        self.win.geometry("500x200")
        self.win.transient(parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self.on_cancel)

        self.file_label = tk.Label(self.win, text="准备中...", anchor="w")
        self.file_label.pack(fill="x", padx=10, pady=5)

        self.progress = ttk.Progressbar(self.win, length=460, mode='determinate')
        self.progress.pack(padx=10, pady=5)

        self.stats_label = tk.Label(self.win, text="0 / 0 个文件  |  0.0 MB / 0.0 MB", anchor="w")
        self.stats_label.pack(fill="x", padx=10, pady=5)

        self.cancel_btn = tk.Button(self.win, text="取消迁移", command=self.on_cancel, bg="lightcoral")
        self.cancel_btn.pack(pady=10)
        self.hint_label = tk.Label(
            self.win,
            text="⚠️ 迁移进行中，请勿关闭主窗口！",
            fg="red",
            font=("微软雅黑", 9, "bold")
        )
        self.hint_label.pack(pady=2)
        self.win.update()

    def _poll_progress(self):
        """定期检查队列并更新进度窗口"""
        if self.progress_queue is not None and self.progress_window is not None:
            try:
                while True:
                    msg = self.progress_queue.get_nowait()
                    if msg is None:  # 结束信号
                        self.progress_window.close()
                        self.progress_window = None
                        self.progress_queue = None
                        self.after_id = None
                        self._migration_running = False
                        return
                    # msg: (file_index, file_name, copied_bytes)
                    self.progress_window.update_progress(*msg)
            except queue.Empty:
                pass
            # 继续轮询
            self.after_id = self.root.after(100, self._poll_progress)
        else:
            self.after_id = None

    def on_cancel(self):
        self.cancelled = True
        self.cancel_btn.config(state=tk.DISABLED, text="正在取消...")

    def update_progress(self, file_index, file_name, copied_bytes):
        """更新进度（在主线程调用）"""
        self.file_label.config(text=f"正在复制: {file_name}")
        if self.total_size > 0:
            percent = min(100, (copied_bytes / self.total_size) * 100)
            self.progress['value'] = percent
        copied_mb = copied_bytes / (1024 * 1024)
        total_mb = self.total_size / (1024 * 1024)
        self.stats_label.config(
            text=f"{file_index} / {self.total_files} 个文件  |  {copied_mb:.1f} MB / {total_mb:.1f} MB"
        )
        self.win.update_idletasks()

    def close(self):
        self.win.destroy()

class ScanProgressWindow:
    """扫描模组差异进度窗口（自动居中，跟随主题）"""
    def __init__(self, parent, total_files, theme):
        self.parent = parent
        self.total_files = total_files
        self.closed = False
        self.theme = theme

        self.win = tk.Toplevel(parent)
        self.win.title("扫描模组差异进度")
        self.win.transient(parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self.on_cancel)

        # 应用主题背景
        self.win.configure(bg=self.theme["bg"])

        self.file_label = tk.Label(self.win, text="准备扫描...", anchor="w",
                                   bg=self.theme["bg"], fg=self.theme["fg"])
        self.file_label.pack(fill="x", padx=10, pady=5)
        # ---- 固定蓝色进度条样式（不随主题改变） ----
        style = ttk.Style()
        style.configure(
            "FixedBlue.Horizontal.TProgressbar",
            background="#4fc3f7",     # 亮蓝色
            troughcolor="#3a3a3a",    # 轨道深色
            borderwidth=0,
            relief='flat'
        )
        self.progress = ttk.Progressbar(
            self.win,
            length=460,
            mode='determinate',
            style="FixedBlue.Horizontal.TProgressbar"
        )
        self.progress.pack(padx=10, pady=5)

        self.stats_label = tk.Label(self.win, text="0 / 0 个文件", anchor="w",
                                    bg=self.theme["bg"], fg=self.theme["fg"])
        self.stats_label.pack(fill="x", padx=10, pady=5)

        self.win.update()
        # 居中
        win_width = self.win.winfo_width()
        win_height = self.win.winfo_height()
        screen_width = self.win.winfo_screenwidth()
        screen_height = self.win.winfo_screenheight()
        x = (screen_width - win_width) // 2
        y = (screen_height - win_height) // 2
        self.win.geometry(f"+{x}+{y}")

    def update_progress(self, current, filename):
        """更新进度（在主线程调用）"""
        self.file_label.config(text=f"正在解析: {filename}")
        if self.total_files > 0:
            percent = (current / self.total_files) * 100
            self.progress['value'] = percent
        self.stats_label.config(text=f"{current} / {self.total_files} 个文件")
        self.win.update_idletasks()

    def close(self):
        if not self.closed:
            self.closed = True
            self.win.destroy()

    def on_cancel(self):
        """禁止用户关闭进度窗口（扫描中）"""
        messagebox.showwarning("提示", "扫描正在进行，请等待完成。")
class MigrationGUI:
    def clear_log(self):
        """清空日志区域，只保存用户主动触发的有效操作记录"""
        # 检查是否有有效操作记录
        if not hasattr(self, '_saved_logs') or not self._saved_logs:
            # 无有效记录：只清空界面，不保存任何文件（不覆盖已有文件）
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")
            self.log("📋 日志已清空（无有效操作记录，不保存文件）", level="INFO", save=False)
            return

        # 有有效记录：保存到文件
        log_file = Path.home() / ".minecraft_migrate_last_log.txt"
        try:
            # 检查文件是否已存在，如果存在则追加（不覆盖）
            mode = 'a' if log_file.exists() else 'w'
            with open(log_file, mode, encoding='utf-8') as f:
                # 如果文件已存在，先写入一条分隔线
                if mode == 'a':
                    f.write("\n" + "=" * 50 + "\n")
                    f.write(f"--- 新日志记录 ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
                f.write("".join(self._saved_logs))

            # 清空界面
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")

            # 提示用户（不保存此提示）
            self.log(f"📋 日志已清空，有效操作记录已追加至 {log_file}", level="INFO", save=False)
            self._saved_logs = []
        except Exception as e:
            self.log(f"❌ 日志保存失败：{e}", level="ERROR", save=False)
    def open_log_folder(self):
        """打开日志文件所在的文件夹（并在资源管理器中选中日志文件）"""
        log_file = Path.home() / ".minecraft_migrate_last_log.txt"
        folder = log_file.parent
        if not folder.exists():
            messagebox.showwarning("提示", "日志文件夹不存在，请先执行操作产生日志。")
            return

        try:
            if sys.platform == 'win32':
                # Windows：打开文件夹并选中文件（如果文件存在）
                if sys.platform == 'win32':
                    if log_file.exists():
                        subprocess.Popen(['explorer', '/select,', str(log_file)])
                    else:
                        os.startfile(str(folder))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder)])
            else:
                subprocess.Popen(['xdg-open', str(folder)])
            self.log(f"📂 已打开日志文件夹：{folder}", level="INFO")
        except Exception as e:
            self.log(f"❌ 打开文件夹失败：{e}", level="ERROR")
            messagebox.showerror("错误", f"无法打开文件夹：{e}")

    def log(self, message, level="INFO", save=True):
        """在日志区域显示消息，并可选择是否保存到文件（仅保存有效操作）"""

        def _log():
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, message + "\n", level)
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
            self.root.update_idletasks()

        # 只有在 save=True 时才将消息加入待保存队列
        if save:
            # 将消息存入一个全局变量或列表，用于清空时保存
            if not hasattr(self, '_saved_logs'):
                self._saved_logs = []
            self._saved_logs.append(message + "\n")

        self.root.after(0, _log)

        # 保存有效操作记录
        log_file = Path.home() / ".minecraft_migrate_last_log.txt"
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write("".join(self._saved_logs))
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")
            self.log(f"📋 日志已清空，有效操作记录已保存至 {log_file}", level="INFO", save=False)
            self._saved_logs = []
        except Exception as e:
            self.log(f"❌ 日志保存失败：{e}", level="ERROR", save=False)
    def _get_history_path(self, target_path):
        """获取历史记录文件路径"""
        return target_path / ".migration_history.json"

    def _load_history(self, target_path):
        """加载历史记录列表"""
        hist_path = self._get_history_path(target_path)
        if hist_path.exists():
            try:
                with open(hist_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return []
        return []

    def _save_history(self, target_path, history):
        """保存历史记录列表"""
        hist_path = self._get_history_path(target_path)
        try:
            with open(hist_path, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except:
            pass

    def _add_history_entry(self, target_path, src_path, modlist, configlist):
        """添加一条迁移历史记录"""
        history = self._load_history(target_path)
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": str(src_path),
            "target": str(target_path),
            "mod_count": len(modlist),
            "config_count": len(configlist),
            "mods": modlist[:20],  # 只记录前20个，避免文件过大
            "configs": configlist,
            "rolled_back": False,
            "rollback_time": None
        }
        history.append(entry)
        self._save_history(target_path, history)
        return entry

    def _mark_rollback(self, target_path):
        """标记最近一次迁移为已回滚"""
        history = self._load_history(target_path)
        if history:
            # 从后往前找第一个未回滚的记录
            for entry in reversed(history):
                if not entry.get("rolled_back", False):
                    entry["rolled_back"] = True
                    entry["rollback_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._save_history(target_path, history)
                    return True
        return False

    def action_show_history(self):
        """显示历史记录窗口"""
        tgt = self.target_path.get().strip()
        if not tgt:
            messagebox.showwarning("提示", "请先选择目标实例根目录")
            return
        target_path = Path(tgt)
        history = self._load_history(target_path)
        if not history:
            messagebox.showinfo("提示", "当前目标实例没有迁移记录。")
            return

        # ---- 创建历史记录窗口 ----
        hist_win = tk.Toplevel(self.root)
        hist_win.title("迁移历史记录")
        hist_win.geometry("900x500")
        hist_win.transient(self.root)

        tk.Label(hist_win, text=f"目标实例：{target_path}", font=("微软雅黑", 9, "bold")).pack(pady=5)

        # ---- Treeview ----
        columns = ("时间", "来源", "模组数", "Config数", "状态")
        tree = ttk.Treeview(hist_win, columns=columns, show="headings", height=18)
        tree.heading("时间", text="迁移时间")
        tree.heading("来源", text="来源路径")
        tree.heading("模组数", text="模组数")
        tree.heading("Config数", text="Config数")
        tree.heading("状态", text="状态")

        tree.column("时间", width=160)
        tree.column("来源", width=400)
        tree.column("模组数", width=70, anchor="center")
        tree.column("Config数", width=70, anchor="center")
        tree.column("状态", width=100, anchor="center")

        scrollbar = ttk.Scrollbar(hist_win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side="left", fill="both", expand=True, padx=10, pady=5)
        scrollbar.pack(side="right", fill="y")

        # ---- 插入数据 ----
        for entry in reversed(history):  # 最新的在上面
            status_text = "✅ 已回滚" if entry.get("rolled_back", False) else "🟢 正常"
            tags = ("rolled_back",) if entry.get("rolled_back", False) else ("normal",)
            tree.insert("", "end", values=(
                entry.get("timestamp", "?"),
                entry.get("source", "?"),
                entry.get("mod_count", 0),
                entry.get("config_count", 0),
                status_text
            ), tags=tags)

        tree.tag_configure("rolled_back", background="#ffdddd")
        tree.tag_configure("normal", background="#ffffff")

        # ---- 关闭按钮 ----
        tk.Button(hist_win, text="关闭", command=hist_win.destroy, width=10).pack(pady=10)
    def action_rollback(self):
        """执行回滚操作（带二次确认 + 完整日志）"""
        self.log("=" * 50, level="INFO")
        self.log("🔄 用户请求执行回滚操作", level="INFO")

        tgt = self.target_path.get().strip()
        if not tgt:
            self.log("❌ 回滚失败：未选择目标实例根目录", level="ERROR")
            messagebox.showerror("错误", "请先选择目标实例根目录")
            return
        tgt_path = Path(tgt)
        if not tgt_path.exists():
            self.log(f"❌ 回滚失败：目标路径不存在 {tgt}", level="ERROR")
            messagebox.showerror("错误", f"目标路径不存在：{tgt}")
            return

        backup_root = tgt_path / ".migrate_backup"
        if not backup_root.exists():
            self.log(f"❌ 回滚失败：未找到备份目录 {backup_root}", level="ERROR")
            messagebox.showerror("回滚失败", "没有找到可用的备份，无法回滚。")
            return

        self.log(f"📁 找到备份目录：{backup_root}", level="INFO")

        # ---- 二次确认 ----
        if not messagebox.askyesno(
                "⚠️ 确认回滚",
                f"即将把目标实例恢复到迁移前的状态，此操作将覆盖当前所有内容！\n\n"
                f"目标路径：{tgt}\n"
                f"备份路径：{backup_root}\n\n"
                "此操作不可撤销！\n确定要继续吗？"
        ):
            self.log("❌ 用户取消了回滚操作", level="WARNING")
            return

        self.log("✅ 用户确认回滚，开始执行...", level="SUCCESS")
        success = self._do_restore(tgt_path)
        if success:
            self.log("✅ 回滚操作完成", level="SUCCESS")
        else:
            self.log("❌ 回滚操作失败，请检查日志", level="ERROR")
        self.log("=" * 50, level="INFO")

    def _do_backup(self, target_path):
        """迁移前备份目标实例的关键文件夹（带日志）"""
        self.log("📦 开始备份目标实例...", level="INFO")
        backup_root = target_path / ".migrate_backup"

        if backup_root.exists():
            self.log(f"🗑️ 删除旧备份：{backup_root}", level="INFO")
            shutil.rmtree(backup_root)
        backup_root.mkdir(parents=True)

        backed = []
        for folder in ["mods", "config", "saves"]:
            src = target_path / folder
            if src.exists():
                dst = backup_root / folder
                self.log(f"📂 备份 {folder} → {dst}", level="INFO")
                shutil.copytree(src, dst)
                backed.append(folder)
            else:
                self.log(f"ℹ️ {folder} 不存在，跳过备份", level="INFO")

        self.log(f"✅ 备份完成，已备份：{', '.join(backed) if backed else '无'}", level="SUCCESS")

    def _do_restore(self, target_path):
        """从备份恢复目标实例，返回是否成功"""
        backup_root = target_path / ".migrate_backup"
        if not backup_root.exists():
            self.log("❌ 恢复失败：备份目录不存在", level="ERROR")
            return False

        self.log("🔄 开始从备份恢复...", level="INFO")
        restored = []

        for folder in ["mods", "config", "saves"]:
            target_folder = target_path / folder
            backup_folder = backup_root / folder

            if backup_folder.exists():
                # 删除目标当前文件夹
                if target_folder.exists():
                    self.log(f"🗑️ 删除现有目录：{target_folder}", level="INFO")
                    shutil.rmtree(target_folder)
                # 恢复备份
                self.log(f"📂 恢复备份：{backup_folder} → {target_folder}", level="INFO")
                shutil.copytree(backup_folder, target_folder)
                restored.append(folder)
            else:
                self.log(f"ℹ️ 备份中不存在 {folder}，跳过", level="INFO")

        self.log(f"✅ 恢复完成，已恢复：{', '.join(restored) if restored else '无'}", level="SUCCESS")
        messagebox.showinfo("回滚完成", f"目标实例已恢复到迁移前的状态。\n已恢复：{', '.join(restored)}")
        # ---- 标记回滚 ----
        self._mark_rollback(target_path)
        self.log("📝 已标记本次回滚到历史记录", level="INFO")
        return True
    def _poll_progress(self):
        """定期检查队列并更新进度窗口"""
        if self.progress_queue is not None and self.progress_window is not None:
            try:
                while True:
                    msg = self.progress_queue.get_nowait()
                    if msg is None:  # 结束信号
                        self.progress_window.close()
                        self.progress_window = None
                        self.progress_queue = None
                        self.after_id = None
                        self._migration_running = False
                        return
                    # msg: (file_index, file_name, copied_bytes)
                    self.progress_window.update_progress(*msg)
            except queue.Empty:
                pass
            # 继续轮询
            self.after_id = self.root.after(100, self._poll_progress)
        else:
            self.after_id = None
    def browse_add_config_file(self):
        """从源 config 浏览并添加文件的相对路径到清单"""
        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
            return

        selected = filedialog.askopenfilename(
            title="请选择源 config 下的文件",
            initialdir=str(src_config),
            filetypes=[("所有文件", "*.*")]
        )
        if not selected:
            return
        selected_path = Path(selected)
        try:
            rel_path = selected_path.relative_to(src_config)
        except ValueError:
            self.log(f"❌ 选择的文件不在源 config 目录下: {selected}", level="ERROR")
            return
        current = self.config_text.get("1.0", tk.END).strip()
        if current and not current.endswith("\n"):
            current += "\n"
        self.config_text.insert(tk.END, str(rel_path) + "\n")
        self.log(f"✅ 已添加 config 条目: {rel_path}", level="SUCCESS")
        self.save_config()

    # ---------- 智能模组差分（元数据级） ----------
    def normalize_mod_name(self, name):
        """去掉文件名开头的 [Forge] [1.20.1] 等前缀，便于模糊匹配"""
        if name.startswith("[") and "]" in name:
            return name.split("]", 1)[1].strip()
        return name

    def get_mod_metadata(self, jar_path):
        """
        从 Forge 或 Fabric 模组 jar 中提取 modId 和 version。
        返回 (modId, version, mod_type)，失败返回 (None, None, None)
        """
        import json
        import re

        def is_placeholder(v):
            """检测版本字符串是否为占位符"""
            if not v:
                return True
            return any(x in v for x in ('${', '$', '{', '}'))

        try:
            with zipfile.ZipFile(jar_path, 'r') as zf:
                # ---- 检查是否存在 fabric.mod.json ----
                has_fabric = 'fabric.mod.json' in zf.namelist()

                if has_fabric:
                    try:
                        # 尝试读取 fabric.mod.json
                        with zf.open('fabric.mod.json') as f:
                            content = f.read().decode('utf-8', errors='ignore')
                            try:
                                data = json.loads(content)
                                modid = data.get('id')
                                version = data.get('version')
                                if modid and not is_placeholder(version):
                                    return modid, version or "?", "Fabric"
                                elif modid:
                                    # 版本是占位符，尝试从文件名推断
                                    filename = jar_path.name
                                    ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                                    if ver_match:
                                        return modid, ver_match.group(1), "Fabric(文件名推断)"
                                    return modid, "?", "Fabric(占位符)"
                            except json.JSONDecodeError:
                                # JSON 解析失败，尝试用正则提取
                                modid_match = re.search(r'"id"\s*:\s*"([^"]+)"', content)
                                version_match = re.search(r'"version"\s*:\s*"([^"]+)"', content)
                                if modid_match:
                                    modid = modid_match.group(1)
                                    version = version_match.group(1) if version_match else None
                                    if is_placeholder(version):
                                        filename = jar_path.name
                                        ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                                        if ver_match:
                                            return modid, ver_match.group(1), "Fabric(正则解析)"
                                        return modid, "?", "Fabric(正则解析)"
                                    return modid, version or "?", "Fabric(正则解析)"
                    except Exception:
                        pass

                    # 如果 fabric.mod.json 存在但无法提取任何信息，标记为 Fabric 但元数据未知
                    # 不再继续尝试 Forge
                    filename = jar_path.name
                    ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                    if ver_match:
                        return "未知", ver_match.group(1), "Fabric(仅文件名)"
                    return "未知", "?", "Fabric(未知)"

                # ---- Forge (仅当没有 fabric.mod.json 时) ----
                try:
                    with zf.open('META-INF/mods.toml') as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        modid_match = re.search(r'modId\s*=\s*"([^"]+)"', content)
                        version_match = re.search(r'version\s*=\s*"([^"]+)"', content)
                        if not modid_match:
                            modid_match = re.search(r"modId\s*=\s*'([^']+)'", content)
                        if not version_match:
                            version_match = re.search(r"version\s*=\s*'([^']+)'", content)
                        if modid_match:
                            modid = modid_match.group(1)
                            version = version_match.group(1) if version_match else None
                            if is_placeholder(version):
                                filename = jar_path.name
                                ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                                if ver_match:
                                    return modid, ver_match.group(1), "Forge(文件名推断)"
                                return modid, "?", "Forge(占位符)"
                            return modid, version or "?", "Forge"
                except (KeyError, zipfile.BadZipFile):
                    pass

                # ---- 从文件名推断 ----
                filename = jar_path.name
                version_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                if version_match:
                    return "未知", version_match.group(1), "文件名推断"
                return None, None, None
        except Exception:
            return None, None, None

    def scan_mod_differences(self, progress_queue=None, total=0):
        """扫描源和目标 mods 目录，返回差异列表（含元数据），带缓存加速，支持并行解析和进度反馈"""
        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        if src == tgt:
            return []  # 相同路径，无差异
        if not src or not tgt:
            messagebox.showerror("错误", "请先选择源和目标路径")
            return None

        src_mods = Path(src) / "mods"
        tgt_mods = Path(tgt) / "mods"

        if not src_mods.exists():
            messagebox.showerror("错误", f"源 mods 目录不存在: {src_mods}")
            return None
        if not tgt_mods.exists():
            messagebox.showerror("错误", f"目标 mods 目录不存在: {tgt_mods}")
            return None

        # ---- 加载缓存 ----
        def load_cache(cache_path):
            cache = {}
            if cache_path.exists():
                try:
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        cache = json.load(f)
                except:
                    pass
            return cache

        def save_cache(cache_path, cache):
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache, f, indent=2)
            except:
                pass

        src_cache_file = Path(src) / "mods_meta_cache.json"
        tgt_cache_file = Path(tgt) / "mods_meta_cache.json"
        src_cache = load_cache(src_cache_file)
        tgt_cache = load_cache(tgt_cache_file)

        src_files = {}
        tgt_files = {}

        # 准备文件列表
        src_paths = list(src_mods.glob("*.jar"))
        tgt_paths = list(tgt_mods.glob("*.jar"))
        total_files = len(src_paths) + len(tgt_paths)
        # 如果外部传入 total 则使用，否则用计算值
        if total == 0:
            total = total_files

        # 定义解析单个文件的函数（线程安全）
        def parse_jar(file_path, is_source):
            modid, version, mod_type = self.get_mod_metadata(file_path)
            stat = file_path.stat()
            return {
                "name": file_path.name,
                "path": file_path,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "norm": self.normalize_mod_name(file_path.name),
                "modid": modid,
                "version": version,
                "mod_type": mod_type
            }

        # ---- 并行解析源 ----
        current = 0
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_path = {executor.submit(parse_jar, p, True): p for p in src_paths}
            for future in as_completed(future_to_path):
                with lock:
                    current += 1
                    result = future.result()
                    src_files[result["name"]] = result
                    # 更新缓存
                    key = result["name"]
                    fingerprint = f"{result['mtime']}_{result['size']}"
                    src_cache[key] = {
                        "fingerprint": fingerprint,
                        "modid": result["modid"],
                        "version": result["version"],
                        "mod_type": result["mod_type"]
                    }
                    if progress_queue:
                        progress_queue.put((current, result["name"]))
        save_cache(src_cache_file, src_cache)

        # ---- 并行解析目标 ----
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_path = {executor.submit(parse_jar, p, False): p for p in tgt_paths}
            for future in as_completed(future_to_path):
                with lock:
                    current += 1
                    result = future.result()
                    tgt_files[result["name"]] = result
                    key = result["name"]
                    fingerprint = f"{result['mtime']}_{result['size']}"
                    tgt_cache[key] = {
                        "fingerprint": fingerprint,
                        "modid": result["modid"],
                        "version": result["version"],
                        "mod_type": result["mod_type"]
                    }
                    if progress_queue:
                        progress_queue.put((current, result["name"]))
        save_cache(tgt_cache_file, tgt_cache)

        # ---- 后续比对逻辑（与之前相同） ----
        src_by_modid = {info["modid"]: name for name, info in src_files.items() if info["modid"]}
        tgt_by_modid = {info["modid"]: name for name, info in tgt_files.items() if info["modid"]}
        src_by_norm = {info["norm"]: name for name, info in src_files.items()}
        tgt_by_norm = {info["norm"]: name for name, info in tgt_files.items()}

        results = []
        processed_tgt_names = set()

        for src_name, src_info in src_files.items():
            matched = False
            tgt_name = None
            # 1. 精确文件名匹配
            if src_name in tgt_files:
                tgt_name = src_name
                matched = True
            # 2. 按 modId 匹配（必须 mod_type 相同）
            elif src_info["modid"] and src_info["modid"] in tgt_by_modid:
                potential_tgt_name = tgt_by_modid[src_info["modid"]]
                potential_tgt_info = tgt_files[potential_tgt_name]
                if potential_tgt_info.get("mod_type") == src_info.get("mod_type"):
                    tgt_name = potential_tgt_name
                    matched = True
                # 如果 mod_type 不同，则不匹配，继续尝试标准化名匹配
            # 3. 按标准化名匹配
            if not matched and src_info["norm"] in tgt_by_norm:
                tgt_name = tgt_by_norm[src_info["norm"]]
                matched = True

            if matched:
                tgt_info = tgt_files[tgt_name]
                processed_tgt_names.add(tgt_name)
                update_reason = []
                if src_info["modid"] and tgt_info["modid"] and src_info["modid"] != tgt_info["modid"]:
                    update_reason.append("modId不同")
                if (src_info["version"] and src_info["version"] != "?" and
                        tgt_info["version"] and tgt_info["version"] != "?" and
                        src_info["version"] != tgt_info["version"]):
                    update_reason.append(f"版本 {tgt_info['version']} → {src_info['version']}")
                if src_info["size"] != tgt_info["size"]:
                    update_reason.append("大小变化")
                if src_info["mtime"] > tgt_info["mtime"]:
                    update_reason.append("源更新")

                if update_reason:
                    results.append((
                        src_name,
                        "更新",
                        src_name,
                        round(src_info["size"] / 1024, 1),
                        ", ".join(update_reason),
                        src_info["modid"] or "?",
                        src_info["version"] or "?",
                        src_info["mod_type"] or "未知"
                    ))
            else:
                results.append((
                    src_name,
                    "新增",
                    src_name,
                    round(src_info["size"] / 1024, 1),
                    "仅存在于源目录",
                    src_info["modid"] or "?",
                    src_info["version"] or "?",
                    src_info["mod_type"] or "未知"
                ))

        for tgt_name, tgt_info in tgt_files.items():
            if tgt_name not in processed_tgt_names:
                if tgt_info["modid"] and tgt_info["modid"] in src_by_modid:
                    continue
                if tgt_info["norm"] in src_by_norm:
                    continue
                results.append((
                    tgt_name,
                    "目标独有",
                    tgt_name,
                    round(tgt_info["size"] / 1024, 1),
                    "仅存在于目标（建议保留）",
                    tgt_info["modid"] or "?",
                    tgt_info["version"] or "?",
                    tgt_info["mod_type"] or "未知"
                ))

        if progress_queue:
            progress_queue.put(None)  # 结束信号
        return results

    def _poll_scan_progress(self):
        """定期检查扫描进度队列并更新进度窗口和按钮"""
        try:
            while True:
                msg = self._scan_progress_queue.get_nowait()
                if msg is None:
                    # 扫描结束，关闭进度窗口
                    if hasattr(self, 'scan_progress_window'):
                        self.scan_progress_window.close()
                        delattr(self, 'scan_progress_window')
                    return
                current, filename = msg
                total = getattr(self, '_scan_total', 0)
                # 更新按钮简短状态
                if total > 0:
                    self.scan_btn.itemconfig(self.scan_btn.text_id, text=f"⏳ 解析中 ({current}/{total})")
                else:
                    self.scan_btn.itemconfig(self.scan_btn.text_id, text=f"⏳ 解析中...")
                # 更新进度窗口
                if hasattr(self, 'scan_progress_window'):
                    self.scan_progress_window.update_progress(current, filename)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_scan_progress)

    def action_scan_mod_diff(self):
        # 如果差异窗口已存在，激活它并返回
        if hasattr(self, 'diff_window') and self.diff_window is not None and self.diff_window.winfo_exists():
            self.diff_window.lift()
            self.diff_window.focus_force()
            return

        if hasattr(self, '_scanning') and self._scanning:
            return

        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        if not src or not tgt:
            messagebox.showerror("错误", "请先选择源和目标路径")
            return

        # 防同实例
        if Path(src).resolve() == Path(tgt).resolve():
            messagebox.showinfo("提示", "源目录和目标目录相同，无需比较。")
            return

        self._scanning = True
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="⏳ 扫描中…")
        self.log("🔍 开始扫描模组差异，请稍候...", level="INFO")
        self.root.update_idletasks()

        # 统计总文件数
        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        total = 0
        if src:
            src_mods = Path(src) / "mods"
            if src_mods.exists():
                total += sum(1 for _ in src_mods.glob("*.jar"))
        if tgt:
            tgt_mods = Path(tgt) / "mods"
            if tgt_mods.exists():
                total += sum(1 for _ in tgt_mods.glob("*.jar"))
        self._scan_total = total

        # 创建进度窗口
        self.scan_progress_window = ScanProgressWindow(self.root, total, self.theme)

        # 创建进度队列
        progress_queue = queue.Queue()
        self._scan_progress_queue = progress_queue

        def scan_task():
            try:
                data = self.scan_mod_differences(progress_queue, self._scan_total)
            except Exception as e:
                data = None
                error_msg = str(e)
            else:
                error_msg = None
            self.root.after(0, lambda: self._finish_scan(data, error_msg))

        self._poll_scan_progress()
        threading.Thread(target=scan_task, daemon=True).start()

    def _finish_scan(self, data, error_msg):
        """扫描完成回调：恢复按钮文字，重置标志，关闭进度窗口"""
        self.scan_btn.itemconfig(self.scan_btn.text_id, text="🔍 扫描模组差异")
        self._scanning = False

        # 确保进度窗口关闭
        if hasattr(self, 'scan_progress_window'):
            self.scan_progress_window.close()
            delattr(self, 'scan_progress_window')

        if error_msg:
            self.log(f"❌ 扫描出错: {error_msg}", level="ERROR")
            messagebox.showerror("扫描错误", f"扫描过程中发生异常：{error_msg}")
            return

        if data is None:
            return

        if not data:
            messagebox.showinfo("提示", "两个 mods 目录完全一致，没有任何差异。")
            self.log("📊 扫描完成：无差异", level="INFO")
            return

        self.log(f"📊 扫描完成，发现 {len(data)} 项差异", level="SUCCESS")
        self._show_diff_window(data)

    def _show_diff_window(self, data):
        """显示差异选择窗口（含水平和垂直滚动条，纯 grid 布局，主题适配）"""
        diff_win = tk.Toplevel(self.root)
        diff_win.title("智能模组差异扫描（元数据级）")
        width, height = 1200, 600
        diff_win.geometry(f"{width}x{height}")
        diff_win.transient(self.root)
        diff_win.configure(bg=self.theme["bg"])

        # ---- 保存窗口引用 ----
        self.diff_window = diff_win

        # ---- 窗口关闭时清空引用 ----
        def on_diff_destroy(event):
            if event.widget == diff_win:
                self.diff_window = None

        diff_win.bind("<Destroy>", on_diff_destroy)

        # 居中
        diff_win.update()
        x = (diff_win.winfo_screenwidth() - width) // 2
        y = (diff_win.winfo_screenheight() - height) // 2
        diff_win.geometry(f"+{x}+{y}")

        # ---- 顶部说明（应用主题） ----
        tk.Label(diff_win, text="以下为扫描结果，勾选你希望复制到目标的模组：",
                 font=("微软雅黑", 10), bg=self.theme["bg"], fg=self.theme["fg"]).grid(
            row=0, column=0, columnspan=2, pady=5, sticky="w", padx=10)

        # ---- Treeview 和滚动条 ----
        columns = ("选择", "文件名", "状态", "类型", "Mod ID", "版本", "大小(KB)", "备注")
        tree = ttk.Treeview(diff_win, columns=columns, show="headings", height=18)
        tree.heading("选择", text="选择")
        tree.heading("文件名", text="文件名")
        tree.heading("状态", text="状态")
        tree.heading("类型", text="类型")
        tree.heading("Mod ID", text="Mod ID")
        tree.heading("版本", text="版本")
        tree.heading("大小(KB)", text="大小(KB)")
        tree.heading("备注", text="备注")

        # 列宽设置
        tree.column("选择", width=60, anchor="center", minwidth=60)
        tree.column("文件名", width=250, minwidth=150)
        tree.column("状态", width=100, minwidth=80)
        tree.column("类型", width=80, anchor="center", minwidth=60)
        tree.column("Mod ID", width=150, minwidth=100)
        tree.column("版本", width=120, minwidth=80)
        tree.column("大小(KB)", width=90, anchor="center", minwidth=80)
        tree.column("备注", width=300, minwidth=200)

        # 滚动条
        vsb = ttk.Scrollbar(diff_win, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(diff_win, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # 使用 grid 放置 tree 和滚动条
        tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        diff_win.grid_rowconfigure(1, weight=1)
        diff_win.grid_columnconfigure(0, weight=1)

        # ---- 适配 Treeview 主题 ----
        style = ttk.Style()
        if style.theme_use() != 'clam':
            try:
                style.theme_use('clam')
            except:
                pass
        style.configure("Treeview",
                        background=self.theme["bg"],
                        foreground=self.theme["fg"],
                        fieldbackground=self.theme["bg"])
        style.configure("Treeview.Heading",
                        background=self.theme["button_bg"],
                        foreground=self.theme["fg"])
        style.configure("Treeview", rowheight=24)
        tree.configure(style="Treeview")

        # ---- 选择状态存储 ----
        selection_state = {}

        for display_name, status, real_name, size_kb, note, modid, version, mod_type in data:
            default_checked = status == "新增"
            checked_char = "☑" if default_checked else "☐"
            tags = ()
            if status == "新增":
                tags = ("new",)
            elif status == "更新":
                tags = ("update",)
            else:
                tags = ("target_only",)

            item_id = tree.insert("", "end", values=(
                checked_char,
                display_name,
                status,
                mod_type,
                modid,
                version,
                size_kb,
                note
            ), tags=tags)
            selection_state[item_id] = default_checked

        tree.tag_configure("new", background="#d4edda" if self.current_theme == "light" else "#2d4a2d")
        tree.tag_configure("update", background="#fff3cd" if self.current_theme == "light" else "#4a3d2d")
        tree.tag_configure("target_only", background="#f8f9fa" if self.current_theme == "light" else "#3a3a3a")

        def toggle_selection(event):
            item_id = tree.focus()
            if not item_id:
                return
            current = selection_state.get(item_id, False)
            new_state = not current
            selection_state[item_id] = new_state
            tree.set(item_id, "选择", "☑" if new_state else "☐")

        tree.bind("<ButtonRelease-1>", toggle_selection)

        # ---- 底部按钮 ----
        btn_frame = tk.Frame(diff_win, bg=self.theme["bg"])
        btn_frame.grid(row=3, column=0, columnspan=2, pady=10)

        def select_all():
            for item_id in selection_state.keys():
                selection_state[item_id] = True
                tree.set(item_id, "选择", "☑")

        def deselect_all():
            for item_id in selection_state.keys():
                selection_state[item_id] = False
                tree.set(item_id, "选择", "☐")

        def apply_selection():
            selected_files = []
            for item_id, checked in selection_state.items():
                if checked:
                    values = tree.item(item_id, "values")
                    selected_files.append(values[1])
            if not selected_files:
                messagebox.showwarning("提示", "没有勾选任何模组")
                return
            self.mod_text.delete(1.0, tk.END)
            self.mod_text.insert(tk.END, "\n".join(selected_files))
            self.log(f"✅ 从差异扫描中导入了 {len(selected_files)} 个模组", level="SUCCESS")
            self.save_config()
            diff_win.destroy()

        tk.Button(btn_frame, text="☑ 全选", command=select_all, width=10,
                  bg=self.theme["button_bg"], fg=self.theme["button_fg"]).pack(side="left", padx=5)
        tk.Button(btn_frame, text="☐ 取消全选", command=deselect_all, width=10,
                  bg=self.theme["button_bg"], fg=self.theme["button_fg"]).pack(side="left", padx=5)
        tk.Button(btn_frame, text="✅ 应用所选到清单", command=apply_selection,
                  bg="lightgreen" if self.current_theme == "light" else "#2d6a2d", fg="white", width=20).pack(
            side="left", padx=20)
        tk.Button(btn_frame, text="关闭", command=diff_win.destroy, width=10,
                  bg=self.theme["button_bg"], fg=self.theme["button_fg"]).pack(side="right", padx=5)

        # ---- 底部统计 ----
        total = len(data)
        new_count = sum(1 for _, status, _, _, _, _, _, _ in data if status == "新增")
        update_count = sum(1 for _, status, _, _, _, _, _, _ in data if status == "更新")
        target_only_count = sum(1 for _, status, _, _, _, _, _, _ in data if status == "目标独有")
        tk.Label(diff_win,
                 text=f"总计 {total} 项差异 | 新增 {new_count} | 更新 {update_count} | 目标独有 {target_only_count}",
                 font=("微软雅黑", 9), bg=self.theme["bg"], fg=self.theme["fg"]).grid(row=4, column=0, columnspan=2,
                                                                                      pady=5)
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft 整合包迁移工具 - 增强版 v2")
        self.root.geometry("1000x1080")

        self.config = self.load_config()
        self.current_theme = self.config.get("theme", "light")
        self.theme = LIGHT_THEME if self.current_theme == "light" else DARK_THEME

        self.source_path = tk.StringVar(value=self.config.get("source", ""))
        self.target_path = tk.StringVar(value=self.config.get("target", ""))
        self.world_name = tk.StringVar(value=self.config.get("world", "老子的世界"))
        self.dry_run = tk.BooleanVar(value=self.config.get("dry_run", True))
        self.overwrite_mods = tk.BooleanVar(value=self.config.get("overwrite", False))

        self.added_mods = []
        self.updated_mods = []
        self.last_check_save_time = 0
        self.last_check_modlist_time = 0

        self.create_widgets()
        self.init_log_colors()
        self.apply_theme()

        # 恢复保存的清单
        self.mod_text.insert("1.0", self.config.get("mod_list", ""))
        self.config_text.insert("1.0", self.config.get("config_list", ""))

        self.log("=" * 60, level="INFO",save=False)
        self.log("【免费声明】本工具完全免费，严禁用于商业用途或转卖。", level="WARNING",save=False)
        self.log("如有任何收费行为，请立即举报。作者不会以任何形式向你收费。", level="WARNING",save=False)
        self.log("=" * 60, level="INFO",save=False)
        self.progress_queue = None
        self.progress_window = None
        self.after_id = None
        self._migration_running = False
        self.diff_window = None  # 用于跟踪差异窗口
        # 新增：在主题应用后刷新路径状态颜色
        self.on_path_change()

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {}

    def save_config(self):
        config = {
            "source": self.source_path.get(),
            "target": self.target_path.get(),
            "world": self.world_name.get(),
            "dry_run": self.dry_run.get(),
            "overwrite": self.overwrite_mods.get(),
            "theme": self.current_theme,
            "mod_list": self.mod_text.get("1.0", tk.END).strip(),
            "config_list": self.config_text.get("1.0", tk.END).strip(),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except:
            pass

    # ---------- 主题相关 ----------
    def init_log_colors(self):
        self.log_text.tag_config("INFO", foreground="gray")
        self.log_text.tag_config("WARNING", foreground="orange")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("SUCCESS", foreground="green")
        self.log_text.tag_config("SIMULATE", foreground="blue")

    def apply_theme(self):
        def apply_recursive(widget):
            try:
                if isinstance(widget, tk.Frame):
                    widget.configure(bg=self.theme["bg"])
                elif isinstance(widget, tk.LabelFrame):
                    widget.configure(bg=self.theme["labelframe_bg"], fg=self.theme["labelframe_fg"])
                elif isinstance(widget, tk.Label):
                    widget.configure(bg=self.theme["label_bg"], fg=self.theme["label_fg"])
                elif isinstance(widget, tk.Button):
                    widget.configure(bg=self.theme["button_bg"], fg=self.theme["button_fg"], activebackground=self.theme["button_bg"])
                elif isinstance(widget, tk.Entry):
                    widget.configure(bg=self.theme["entry_bg"], fg=self.theme["entry_fg"], insertbackground=self.theme["fg"])
                elif isinstance(widget, scrolledtext.ScrolledText):
                    widget.configure(bg=self.theme["text_bg"], fg=self.theme["text_fg"])
                    widget.vbar.configure(bg=self.theme["button_bg"], troughcolor=self.theme["bg"])
                elif isinstance(widget, tk.Text):
                    widget.configure(bg=self.theme["text_bg"], fg=self.theme["text_fg"])
                elif isinstance(widget, tk.Canvas):
                    widget.configure(bg=self.theme["bg"])
                elif isinstance(widget, tk.Listbox):
                    widget.configure(bg=self.theme["entry_bg"], fg=self.theme["entry_fg"])
            except:
                pass
            for child in widget.winfo_children():
                apply_recursive(child)

        self.root.configure(bg=self.theme["bg"])
        apply_recursive(self.root)

        if hasattr(self, 'bottom_frame'):
            self.bottom_frame.configure(bg=self.theme["bottom_bg"])
        if hasattr(self, 'warning_frame'):
            self.warning_frame.configure(bg=self.theme["warning_bg"])
            self.warning_label.configure(bg=self.theme["warning_bg"], fg=self.theme["warning_fg"])
        if hasattr(self, 'log_text'):
            self.log_text.configure(bg=self.theme["log_bg"], fg=self.theme["log_fg"])
        if hasattr(self, 'source_status'):
            self.source_status.configure(bg=self.theme["bg"])
        if hasattr(self, 'target_status'):
            self.target_status.configure(bg=self.theme["bg"])
        if hasattr(self, 'world_status'):
            self.world_status.configure(bg=self.theme["bg"])
        if hasattr(self, 'rollback_btn'):
            self.rollback_btn.config(bg="#d32f2f", fg="white", activebackground="#b71c1c", activeforeground="white")

    def toggle_theme(self):
        if self.current_theme == "light":
            self.current_theme = "dark"
            self.theme = DARK_THEME
        else:
            self.current_theme = "light"
            self.theme = LIGHT_THEME
        self.apply_theme()
        # 强制刷新路径状态颜色（因为 apply_theme 会覆盖前景色）
        self.on_path_change()
        self.save_config()
        self.log(f"主题已切换为{'深色' if self.current_theme == 'dark' else '浅色'}模式", level="SUCCESS",save=False)
    # ---------- 工具函数 ----------
    def create_tooltip(self, widget, text):
        def enter(event):
            self.tooltip = tk.Toplevel(widget)
            self.tooltip.wm_overrideredirect(True)
            self.tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
            label = tk.Label(self.tooltip, text=text, background=self.theme["tooltip_bg"], fg=self.theme["label_fg"], relief="solid", borderwidth=1, font=("微软雅黑", 9))
            label.pack()
        def leave(event):
            if hasattr(self, 'tooltip'):
                self.tooltip.destroy()
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    # ---------- 路径验证 ----------
    def validate_path(self, path_str, status_label, label_text):
        if not path_str:
            status_label.config(text="（未选择）", fg="gray")
            return
        p = Path(path_str)
        if not p.exists():
            status_label.config(text="❌ 路径不存在", fg="red")
            return
        if (p / "mods").exists() or (p / "saves").exists():
            status_label.config(text="✅ 有效实例目录", fg="green")
        else:
            status_label.config(text="⚠️ 未找到 mods 或 saves 子目录", fg="orange")

    def on_path_change(self, *args):
        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        self.validate_path(src, self.source_status, "源")
        self.validate_path(tgt, self.target_status, "目标")
        self.save_config()
    # ---------- 界面构建 ----------
    def create_widgets(self):
        top_bar = tk.Frame(self.root)
        top_bar.pack(fill="x", padx=10, pady=5)
        self.theme_btn = tk.Button(top_bar, text="🌓 切换主题", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=5)

        self.warning_frame = tk.Frame(self.root, relief=tk.RIDGE, bd=2)
        self.warning_frame.pack(fill="x", padx=10, pady=(5, 0))
        self.warning_label = tk.Label(self.warning_frame, text="⚠️ 本工具完全免费，请勿上当受骗！如遇收费行为，请立即举报。⚠️",font=("微软雅黑", 10, "bold"))
        self.warning_label.pack(pady=5)
        info_frame = tk.Frame(self.root)
        info_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(info_frame,text="【重要】请选择实例根目录（例如 D:\\.minecraft\\versions\\游戏名），该目录下应直接包含 mods、saves、options.txt 等",fg="blue", wraplength=950).pack()
        tk.Label(info_frame, text="👉 迁移方向：从“旧版”复制到“新版”（旧版模组 → 新版模组，保留你的自定义配置）",fg="green", wraplength=950).pack(pady=(0, 5))

        # ---- 源目录 ----
        frame_source = tk.LabelFrame(self.root, text="📤 旧版整合包（要迁移出去的源）", padx=5, pady=5)
        frame_source.pack(fill="x", padx=10, pady=5)
        tk.Entry(frame_source, textvariable=self.source_path, width=60).pack(side="left", padx=5)
        tk.Button(frame_source, text="浏览...", command=self.select_source).pack(side="left")
        btn_copy = tk.Button(frame_source, text="← 使用新版路径填充", command=self.copy_target_to_source,bg="lightyellow")
        btn_copy.pack(side="left", padx=5)
        self.create_tooltip(btn_copy, "将右侧“新版”的路径复制到左侧“旧版”栏，用于快速测试或反向操作")
        self.source_status = tk.Label(frame_source, text="", fg="gray")
        self.source_status.pack(side="left", padx=10)

        # ---- 目标目录 ----
        frame_target = tk.LabelFrame(self.root, text="📥 新版整合包（迁移目的地）", padx=5, pady=5)
        frame_target.pack(fill="x", padx=10, pady=5)
        tk.Entry(frame_target, textvariable=self.target_path, width=70).pack(side="left", padx=5)
        tk.Button(frame_target, text="浏览...", command=self.select_target).pack(side="left")
        self.target_status = tk.Label(frame_target, text="", fg="gray")
        self.target_status.pack(side="left", padx=10)

        # ---- 存档名称 ----
        frame_world = tk.LabelFrame(self.root, text="存档文件夹名称", padx=5, pady=5)
        frame_world.pack(fill="x", padx=10, pady=5)
        tk.Entry(frame_world, textvariable=self.world_name, width=40).pack(side="left", padx=5)
        tk.Label(frame_world, text="（例如：新的世界）").pack(side="left")
        self.world_status = tk.Label(frame_world, text="", fg="gray")
        self.world_status.pack(side="left", padx=10)
        tk.Button(frame_world, text="检查存档是否存在", command=self.check_save_exists).pack(side="right", padx=5)

        # ---- 模组清单 ----
        frame_modlist = tk.LabelFrame(self.root, text="需要复制的模组清单（每行一个 .jar 文件名）", padx=5, pady=5)
        frame_modlist.pack(fill="both", expand=True, padx=10, pady=5)
        self.mod_text = scrolledtext.ScrolledText(frame_modlist, height=8, wrap=tk.NONE)
        self.mod_text.pack(fill="both", expand=True, padx=5, pady=5)

        btn_frame = tk.Frame(frame_modlist)
        btn_frame.pack(fill="x", pady=5)
        btn_changelog = tk.Button(btn_frame, text="从变更日志导入（含Updated）", command=self.import_from_changelog,bg="lightcyan")
        btn_changelog.pack(side="left", padx=5)
        self.create_tooltip(btn_changelog,"你需要提供的是“崩溃助手 | Crash assistant”模组给予的mod变更列表（你可能需要主动制造一次崩溃，可使用PCL关闭游戏来触发，当然您也可以使用右侧的检测功能）")

        self.scan_btn = create_gradient_button(
            btn_frame,
            text="🔍 扫描模组差异",
            command=self.action_scan_mod_diff,
            colors=("#00bcd4", "#3f51b5")
        )
        self.scan_btn.pack(side="left", padx=5)
        tk.Button(btn_frame, text="清空清单",command=lambda: (self.mod_text.delete(1.0, tk.END), self.save_config())).pack(side="left", padx=5)
        tk.Button(btn_frame, text="检查清单模组是否存在（源目录）", command=self.check_modlist_existence,bg="lightyellow").pack(side="left", padx=5)

        # ---- Config 清单 ----
        frame_config = tk.LabelFrame(self.root, text="需要迁移的 config 内容（每行一个相对路径，相对于 config 目录）",padx=5, pady=5)
        frame_config.pack(fill="both", expand=True, padx=10, pady=5)
        warning_config = tk.Label(frame_config,text="⚠️ 注意：复制将直接覆盖目标 config 中的同名文件/文件夹，请谨慎操作！", fg="red",font=("微软雅黑", 9, "bold"))
        warning_config.pack(anchor="w", padx=5, pady=2)
        self.config_text = scrolledtext.ScrolledText(frame_config, height=6, wrap=tk.NONE)
        self.config_text.pack(fill="both", expand=True, padx=5, pady=5)

        # ---- Config 按钮区域（现在 frame_config 已经定义了） ----
        btn_config_frame = tk.Frame(frame_config)
        btn_config_frame.pack(fill="x", pady=5)

        tk.Button(btn_config_frame, text="从源 config 导入所有条目", command=self.import_config_entries,bg="lightgreen").pack(side="left", padx=5)
        tk.Button(btn_config_frame, text="从源 config 浏览添加（📂文件夹）", command=self.browse_add_config_entry,bg="lightblue").pack(side="left", padx=5)
        tk.Button(btn_config_frame, text="从源 config 浏览添加（📄文件）", command=self.browse_add_config_file,bg="lightblue").pack(side="left", padx=5)
        tk.Button(btn_config_frame, text="清空 config 清单",command=lambda: (self.config_text.delete(1.0, tk.END), self.save_config())).pack(side="left", padx=5)

        # ---- 选项和迁移按钮 ----
        opt_frame = tk.Frame(self.root)
        opt_frame.pack(fill="x", padx=10, pady=5)
        self.dry_run_cb = tk.Checkbutton(opt_frame, text="模拟运行（仅显示操作）", variable=self.dry_run,
                                         command=self.save_config)
        self.dry_run_cb.pack(side="left")
        self.overwrite_cb = tk.Checkbutton(opt_frame, text="覆盖已存在的模组", variable=self.overwrite_mods,
                                           command=self.save_config)
        self.overwrite_cb.pack(side="left", padx=20)
        tk.Button(opt_frame, text="开始迁移", command=self.start_migration, bg="lightgreen", width=15).pack(side="right", padx=5)
        tk.Button(opt_frame, text="📋 查看历史", command=self.action_show_history, bg="lightgray", width=10).pack(side="right", padx=5)
        # 在 opt_frame 区域添加回滚按钮（放在“开始迁移”按钮右侧）
        self.rollback_btn = tk.Button(
            opt_frame,
            text="⚠️ 回滚",
            command=self.action_rollback,
            font=("微软雅黑", 10, "bold"),
            relief=tk.RAISED,
            bd=3,
            padx=10,
            pady=3,
            cursor="hand2"
        )
        self.rollback_btn.pack(side="right", padx=5)
        # 强制设置颜色（覆盖主题）
        self.rollback_btn.config(bg="#d32f2f", fg="white", activebackground="#b71c1c", activeforeground="white")
        # ---- 日志区域 ----
        frame_log = tk.LabelFrame(self.root, text="执行日志", padx=5, pady=5)
        frame_log.pack(fill="both", expand=True, padx=10, pady=5)

        # 日志工具栏（清空按钮）
        # ---- 日志区域 ----
        #frame_log = tk.LabelFrame(self.root, text="执行日志", padx=5, pady=5)
        frame_log.pack(fill="both", expand=True, padx=10, pady=5)
        log_toolbar = tk.Frame(frame_log)
        log_toolbar.pack(fill="x", pady=(0, 5))
        tk.Button(log_toolbar, text="🗑️ 清空日志", command=self.clear_log, bg="lightgray", width=10).pack(side="right",padx=5)
        tk.Button(log_toolbar, text="📂 打开日志文件夹", command=self.open_log_folder, bg="lightgray", width=14).pack(side="right", padx=5)
        self.log_text = scrolledtext.ScrolledText(frame_log, height=15, wrap=tk.WORD, state="disabled")
        self.log_text.pack(fill="both", expand=True)

        # 绑定路径变化事件
        self.source_path.trace_add("write", self.on_path_change)
        self.target_path.trace_add("write", self.on_path_change)
        self.world_name.trace_add("write", self.save_config)

        self.on_path_change()

        # 底部免费声明
        self.bottom_frame = tk.Frame(self.root)
        self.bottom_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(self.bottom_frame, text="本工具完全免费，仅供个人学习交流使用。严禁倒卖或用于商业目的。",font=("微软雅黑", 8)).pack()

    # ---------- 路径选择 ----------
    def select_source(self):
        path = filedialog.askdirectory(title="选择源整合包的实例根目录")
        if path:
            self.source_path.set(path)

    def select_target(self):
        path = filedialog.askdirectory(title="选择目标整合包的实例根目录")
        if path:
            self.target_path.set(path)

    # 【修改】新的一键复制：目标→源
    def copy_target_to_source(self):
        """将目标路径复制到源路径"""
        tgt = self.target_path.get().strip()
        if tgt:
            self.source_path.set(tgt)
            self.log("已将目标路径复制到源路径", level="INFO")
        else:
            self.root.bell()
            self.log("⚠️ 目标路径为空，无法复制", level="WARNING")
    # ---------- 检查存档 ----------
    def check_save_exists(self):
        now = time.time()
        if now - self.last_check_save_time < 2:
            self.root.bell()
            self.log("⚠️ 请勿频繁操作！请稍后再试。", level="WARNING")
            return
        self.last_check_save_time = now

        target = self.target_path.get().strip()
        world = self.world_name.get().strip()
        if not target:
            self.root.bell()
            self.log("⚠️ 请先选择目标整合包实例根目录", level="WARNING")
            return
        if not world:
            self.root.bell()
            self.log("⚠️ 请输入存档名称", level="WARNING")
            return
        target_path = Path(target)
        if not target_path.exists():
            self.root.bell()
            self.log(f"❌ 目标路径不存在：{target}", level="ERROR")
            return
        save_dir = target_path / "saves" / world
        if save_dir.exists():
            self.world_status.config(text="✅ 存档已存在", fg="green")
            self.log(f"✅ 存档 '{world}' 存在于目标整合包中", level="SUCCESS")
        else:
            self.world_status.config(text="❌ 存档不存在", fg="red")
            self.log(f"❌ 存档 '{world}' 不存在于目标整合包中", level="WARNING")

    # ---------- 检查模组存在性 ----------
    def check_modlist_existence(self):
        now = time.time()
        if now - self.last_check_modlist_time < 2:
            self.root.bell()
            self.log("⚠️ 请勿频繁操作！请稍后再试。", level="WARNING")
            return
        self.last_check_modlist_time = now

        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_mods = Path(src) / "mods"
        if not src_mods.exists():
            self.root.bell()
            self.log(f"❌ 源 mods 目录不存在：{src_mods}", level="ERROR")
            return

        modlist_raw = self.mod_text.get(1.0, tk.END).splitlines()
        modlist = [line.strip() for line in modlist_raw if line.strip() and not line.strip().startswith("#")]
        if not modlist:
            self.root.bell()
            self.log("⚠️ 当前模组清单为空", level="WARNING")
            return

        source_files = {f.name: f for f in src_mods.glob("*.jar")}
        name_map = {}
        for orig in source_files:
            clean = orig
            if clean.startswith("[") and "]" in clean:
                clean = clean.split("]", 1)[1].strip()
            name_map[clean] = orig
            name_map[orig] = orig

        missing = []
        found = []
        for item in modlist:
            matched = self.match_mod(item, source_files, name_map)
            if matched:
                found.append(item)
            else:
                missing.append(item)

        self.log(f"📊 模组清单检查结果：总清单项数 {len(modlist)}", level="INFO")
        self.log(f"✅ 存在的模组：{len(found)}", level="SUCCESS")
        self.log(f"❌ 缺失的模组：{len(missing)}", level="ERROR" if missing else "INFO")
        if missing:
            self.root.bell()
            self.log("缺失列表：", level="WARNING")
            for m in missing[:50]:
                self.log(f"  - {m}", level="ERROR")
            if len(missing) > 50:
                self.log(f"  ... 还有 {len(missing)-50} 个未显示", level="WARNING")

    # ---------- 导入/导出模组清单 ----------
    def import_modlist(self):
        file_path = filedialog.askopenfilename(title="导入模组清单", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.mod_text.delete(1.0, tk.END)
                self.mod_text.insert(tk.END, content)
                self.log(f"✅ 已导入清单文件：{file_path}", level="SUCCESS")
                self.save_config()
            except Exception as e:
                messagebox.showerror("错误", f"导入失败：{e}")

    def export_modlist(self):
        file_path = filedialog.asksaveasfilename(title="保存模组清单", defaultextension=".txt", filetypes=[("文本文件", "*.txt")])
        if file_path:
            try:
                content = self.mod_text.get(1.0, tk.END)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.log(f"✅ 已保存清单至：{file_path}", level="SUCCESS")
            except Exception as e:
                messagebox.showerror("错误", f"保存失败：{e}")

    # ---------- 从变更日志导入 ----------
    def import_from_changelog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("从变更日志提取模组清单")
        dialog.geometry("800x600")
        tk.Label(dialog, text="请粘贴完整的变更日志文本（包含 'Added mods:' 和 'Updated mods:' 部分）：").pack(pady=5)
        text_widget = scrolledtext.ScrolledText(dialog, wrap=tk.WORD, height=20)
        text_widget.pack(fill="both", expand=True, padx=10, pady=5)

        def extract_and_close():
            raw_text = text_widget.get("1.0", tk.END)
            added, updated = self.extract_mods_from_changelog(raw_text)
            if not added and not updated:
                messagebox.showwarning("无结果", "未能提取到模组文件名")
                return
            if updated:
                ans = messagebox.askyesnocancel("发现 Updated mods",
                    f"已提取到 {len(added)} 个 Added 模组，{len(updated)} 个 Updated 模组。\n"
                    "是否将 Updated 模组也添加到复制清单中？\n\n"
                    "点击“是” → 全部添加\n"
                    "点击“否” → 只添加 Added 模组\n"
                    "点击“取消” → 不添加任何模组")
                if ans is None:
                    return
                elif ans:
                    all_mods = added + updated
                else:
                    all_mods = added
            else:
                all_mods = added

            if all_mods:
                self.mod_text.delete(1.0, tk.END)
                self.mod_text.insert(tk.END, "\n".join(all_mods))
                self.log(f"从变更日志中提取了 {len(all_mods)} 个模组（Added: {len(added)}, Updated: {len(updated)}）", level="SUCCESS")
                self.save_config()
                dialog.destroy()
            else:
                messagebox.showinfo("提示", "未添加任何模组")

        tk.Button(dialog, text="提取并应用", command=extract_and_close, bg="lightblue").pack(pady=10)

    def extract_mods_from_changelog(self, text):
        lines = text.splitlines()
        added = []
        updated = []
        current_section = None
        for line in lines:
            line_stripped = line.strip()
            if re.match(r"^Added\s+mods[:：]", line_stripped, re.IGNORECASE):
                current_section = "added"
                continue
            elif re.match(r"^Updated\s+mods[:：]", line_stripped, re.IGNORECASE):
                current_section = "updated"
                continue
            elif re.match(r"^(Removed|Updated)\s+mods[:：]", line_stripped, re.IGNORECASE):
                current_section = None
                continue
            if current_section and line_stripped and not line_stripped.startswith("#"):
                if line_stripped.endswith(".jar"):
                    if current_section == "added":
                        added.append(line_stripped)
                    elif current_section == "updated":
                        updated.append(line_stripped)
        return added, updated

    # ---------- Config 清单相关 ----------
    def import_config_entries(self):
        """扫描源 config 目录，将所有文件和一级子目录填入 config 清单"""
        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
            return

        entries = []
        for item in src_config.iterdir():
            entries.append(item.name)

        if entries:
            self.config_text.delete(1.0, tk.END)
            self.config_text.insert(tk.END, "\n".join(entries))
            self.log(f"✅ 已从源 config 导入 {len(entries)} 个条目（文件/文件夹）", level="SUCCESS")
            self.save_config()
        else:
            self.log("ℹ️ 源 config 目录为空，无条目可导入", level="INFO")

    # 【新增】浏览添加 config 条目（文件夹）
    def browse_add_config_entry(self):
        """从源 config 浏览并添加文件夹相对路径到清单"""
        src = self.source_path.get().strip()
        if not src:
            self.root.bell()
            self.log("⚠️ 请先选择源整合包实例根目录", level="WARNING")
            return
        src_config = Path(src) / "config"
        if not src_config.exists():
            self.root.bell()
            self.log(f"❌ 源 config 目录不存在：{src_config}", level="ERROR")
            return

        selected = filedialog.askdirectory(title="请选择源 config 下的文件夹", initialdir=str(src_config))
        if not selected:
            return
        selected_path = Path(selected)
        try:
            rel_path = selected_path.relative_to(src_config)
        except ValueError:
            self.log(f"❌ 选择的路径不在源 config 目录下: {selected}", level="ERROR")
            return
        current = self.config_text.get("1.0", tk.END).strip()
        if current and not current.endswith("\n"):
            current += "\n"
        self.config_text.insert(tk.END, str(rel_path) + "\n")
        self.log(f"✅ 已添加 config 条目: {rel_path}", level="SUCCESS")
        self.save_config()

    def export_config_list(self):
        file_path = filedialog.asksaveasfilename(title="保存 config 清单", defaultextension=".txt", filetypes=[("文本文件", "*.txt")])
        if file_path:
            try:
                content = self.config_text.get(1.0, tk.END)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.log(f"✅ 已保存 config 清单至：{file_path}", level="SUCCESS")
            except Exception as e:
                messagebox.showerror("错误", f"保存失败：{e}")

    def import_config_list(self):
        file_path = filedialog.askopenfilename(title="导入 config 清单", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.config_text.delete(1.0, tk.END)
                self.config_text.insert(tk.END, content)
                self.log(f"✅ 已导入 config 清单文件：{file_path}", level="SUCCESS")
                self.save_config()
            except Exception as e:
                messagebox.showerror("错误", f"导入失败：{e}")
    # ---------- 模组匹配辅助 ----------
    def match_mod(self, item, source_files, name_map):
        if item in source_files:
            return item
        if item in name_map:
            return name_map[item]
        clean_item = item
        if clean_item.startswith("[") and "]" in clean_item:
            clean_item = clean_item.split("]", 1)[1].strip()
        for orig in source_files:
            clean_orig = orig
            if clean_orig.startswith("[") and "]" in clean_orig:
                clean_orig = clean_orig.split("]", 1)[1].strip()
            if clean_orig == clean_item:
                return orig
        return None

    # ---------- 日志 ----------
    def log(self, message, level="INFO", save=True):
        """在日志区域显示消息，并可选择是否保存到文件（仅保存有效操作）"""

        def _log():
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, message + "\n", level)
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
            self.root.update_idletasks()

        # 只有在 save=True 时才将消息加入待保存队列
        if save:
            # 将消息存入一个全局变量或列表，用于清空时保存
            if not hasattr(self, '_saved_logs'):
                self._saved_logs = []
            self._saved_logs.append(message + "\n")

        self.root.after(0, _log)

    # ---------- 启动迁移 ----------
    def start_migration(self):

        if self._migration_running:
            messagebox.showwarning("提示", "迁移正在进行中，请勿重复启动")
            return

        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        world = self.world_name.get().strip()
        if not src or not tgt:
            messagebox.showerror("错误", "请选择源和目标实例根目录")
            return
        if not world:
            messagebox.showerror("错误", "请输入存档名称")
            return

        src_path = Path(src)
        tgt_path = Path(tgt)
        if not src_path.exists():
            messagebox.showerror("错误", f"源路径不存在：{src}")
            return
        if not tgt_path.exists():
            messagebox.showerror("错误", f"目标路径不存在：{tgt}")
            return

        modlist_raw = self.mod_text.get(1.0, tk.END).splitlines()
        modlist = [line.strip() for line in modlist_raw if line.strip() and not line.strip().startswith("#")]

        configlist_raw = self.config_text.get(1.0, tk.END).splitlines()
        configlist = [line.strip() for line in configlist_raw if line.strip() and not line.strip().startswith("#")]

        # 如果模组列表和 config 列表都为空，提示并返回
        if not modlist and not configlist:
            messagebox.showwarning("提示", "模组清单和 config 清单均为空，没有可迁移的内容。")
            return

        # ---- 判断是否为模拟运行 ----
        if self.dry_run.get():
            # 模拟模式：直接调用 run_migration，不创建进度窗口
            self.log("========== 开始迁移（模拟） ==========", level="INFO")
            self.log(f"旧版目录（源）: {src}", level="INFO")
            self.log(f"新版目录（目标）: {tgt}", level="INFO")
            self.log(f"存档名称: {world}", level="INFO")
            self.log("模拟模式: 是", level="INFO")
            self.log("不会实际修改任何文件", level="INFO")
            thread = threading.Thread(
                target=self.run_migration,
                args=(src_path, tgt_path, world, modlist, configlist,
                      True, self.overwrite_mods.get(), None)  # progress_queue = None
            )
            thread.daemon = True
            thread.start()
            return

        # ---- 非模拟模式：计算总文件数和总大小 ----
        total_files = 0
        total_size = 0

        # 模组
        src_mods = src_path / "mods"
        if src_mods.exists():
            source_files = {f.name: f for f in src_mods.glob("*.jar")}
            for mod in modlist:
                matched_name = self.match_mod(mod, source_files, {})
                if matched_name:
                    total_files += 1
                    total_size += (src_mods / matched_name).stat().st_size

        # options.txt
        src_opts = src_path / "options.txt"
        if src_opts.exists():
            total_files += 1
            total_size += src_opts.stat().st_size

        # config 条目
        src_config = src_path / "config"
        for entry in configlist:
            src_entry = src_config / entry
            if src_entry.is_file():
                total_files += 1
                total_size += src_entry.stat().st_size
            elif src_entry.is_dir():
                for f in src_entry.rglob("*"):
                    if f.is_file():
                        total_files += 1
                        total_size += f.stat().st_size

        # 如果没有任何文件（虽然上面已经检查过，但以防万一）
        if total_files == 0:
            messagebox.showinfo("提示", "没有找到需要复制的文件，请检查清单。")
            return
        # ---- 如果没有任何文件（虽然上面已经检查过，但以防万一） ----
        if total_files == 0:
            messagebox.showinfo("提示", "没有找到需要复制的文件，请检查清单。")
            return

        # ---- 新增：执行备份（非模拟模式） ----
        if not self.dry_run.get():
            self.log("📦 准备备份目标实例...", level="INFO")
            self._do_backup(tgt_path)
            self.log("📦 备份完成，开始迁移...", level="INFO")

        # ---- 创建进度窗口 ----
        self.progress_queue = queue.Queue()
        self.progress_window = ProgressWindow(self.root, total_files, total_size)
        self._migration_running = True
        # 开始轮询
        if self.after_id is None:
            self._poll_progress()

        # ---- 启动迁移线程 ----
        thread = threading.Thread(
            target=self.run_migration,
            args=(src_path, tgt_path, world, modlist, configlist,
                  False, self.overwrite_mods.get(), self.progress_queue)
        )
        thread.daemon = True
        thread.start()

    # ---------- 安全的复制函数 ----------
    def safe_copy(self, src, dst, dry_run, overwrite, is_file=True):
        if dry_run:
            return True, "模拟复制"
        if dst.exists() and not overwrite:
            return False, "目标已存在，跳过"
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if is_file:
                shutil.copy2(src, dst)
            else:
                # 使用 dirs_exist_ok 直接合并，不先删除
                shutil.copytree(src, dst, dirs_exist_ok=True)
            return True, "复制成功"
        except PermissionError:
            return False, f"权限不足：无法写入 {dst}"
        except OSError as e:
            if "No space left" in str(e):
                return False, "磁盘空间不足"
            return False, f"系统错误：{e}"
        except Exception as e:
            return False, f"未知错误：{e}"

    def safe_copytree(self, src, dst, dry_run):
        if dry_run:
            return True, "模拟复制目录"
        if dst.exists():
            try:
                backup = dst.with_suffix(".backup")
                shutil.move(str(dst), str(backup))
                self.log(f"已备份原有目录至：{backup.name}", level="SUCCESS")
            except Exception as e:
                return False, f"备份失败：{e}"
        try:
            shutil.copytree(src, dst)
            return True, "目录复制成功"
        except Exception as e:
            return False, f"复制目录失败：{e}"

    # ---------- 主迁移过程 ----------
    def run_migration(self, src_path, tgt_path, world_name, modlist, configlist, dry_run, overwrite, progress_queue):
        try:
            copied_bytes = 0
            file_index = 0

            # 1. 复制模组
            self.log("【步骤1】复制模组...", level="INFO")
            src_mods = src_path / "mods"
            tgt_mods = tgt_path / "mods"
            if not src_mods.exists():
                self.log(f"⚠️ 旧 mods 目录不存在: {src_mods}，跳过", level="WARNING")
            else:
                if not dry_run:
                    tgt_mods.mkdir(parents=True, exist_ok=True)

                source_files = {f.name: f for f in src_mods.glob("*.jar")}
                name_map = {}
                for orig in source_files:
                    clean = orig
                    if clean.startswith("[") and "]" in clean:
                        clean = clean.split("]", 1)[1].strip()
                    name_map[clean] = orig
                    name_map[orig] = orig

                success = 0
                skipped = 0
                failed = []
                for item in modlist:
                    # 检查取消（仅在非模拟且窗口存在时）
                    if self.progress_window and self.progress_window.cancelled:
                        self.log("⚠️ 用户取消了迁移", level="WARNING")
                        if progress_queue:
                            progress_queue.put(None)
                        return

                    matched = self.match_mod(item, source_files, name_map)
                    if matched:
                        src_file = source_files[matched]
                        dst_file = tgt_mods / matched
                        if dst_file.exists() and not overwrite and not dry_run:
                            self.log(f"⏭️ 跳过已存在的模组: {matched}", level="WARNING")
                            skipped += 1
                            continue
                        ok, msg = self.safe_copy(src_file, dst_file, dry_run, overwrite, is_file=True)
                        if ok:
                            success += 1
                            file_index += 1
                            copied_bytes += src_file.stat().st_size
                            if progress_queue:
                                progress_queue.put((file_index, matched, copied_bytes))
                            if dry_run:
                                self.log(f"[模拟] 将复制: {matched}", level="SIMULATE")
                            else:
                                self.log(f"✅ 已复制: {matched}", level="SUCCESS")
                        else:
                            failed.append((item, msg))
                            self.log(f"❌ 复制失败 {matched}: {msg}", level="ERROR")
                    else:
                        failed.append((item, "未找到匹配的文件"))
                        self.log(f"❌ 未找到匹配模组: {item}", level="ERROR")
                self.log(f"模组复制完成: 成功 {success} 个, 跳过 {skipped} 个, 失败 {len(failed)} 个", level="INFO")

            # 2. 复制 options.txt
            self.log("\n【步骤2】复制 options.txt...", level="INFO")
            if self.progress_window and self.progress_window.cancelled:
                self.log("⚠️ 用户取消了迁移", level="WARNING")
                if progress_queue:
                    progress_queue.put(None)
                return

            src_opts = src_path / "options.txt"
            dst_opts = tgt_path / "options.txt"
            if src_opts.exists():
                ok, msg = self.safe_copy(src_opts, dst_opts, dry_run, overwrite=True, is_file=True)
                if ok:
                    file_index += 1
                    copied_bytes += src_opts.stat().st_size
                    if progress_queue:
                        progress_queue.put((file_index, "options.txt", copied_bytes))
                    self.log(f"{'[模拟]' if dry_run else '✅'} 已复制 options.txt",
                             level="SUCCESS" if not dry_run else "SIMULATE")
                else:
                    self.log(f"❌ 复制 options.txt 失败: {msg}", level="ERROR")
            else:
                self.log("⚠️ 源 options.txt 不存在，跳过", level="WARNING")

            # 3. 复制存档
            self.log("\n【步骤3】复制存档...", level="INFO")
            if self.progress_window and self.progress_window.cancelled:
                self.log("⚠️ 用户取消了迁移", level="WARNING")
                if progress_queue:
                    progress_queue.put(None)
                return

            src_world = src_path / "saves" / world_name
            dst_world = tgt_path / "saves" / world_name
            if not src_world.exists():
                self.log(f"⚠️ 源存档不存在: {src_world}，跳过", level="WARNING")
            else:
                ok, msg = self.safe_copytree(src_world, dst_world, dry_run)
                if ok:
                    total_world_size = sum(f.stat().st_size for f in src_world.rglob("*") if f.is_file())
                    file_index += 1
                    copied_bytes += total_world_size
                    if progress_queue:
                        progress_queue.put((file_index, f"存档: {world_name}", copied_bytes))
                    self.log(f"{'[模拟]' if dry_run else '✅'} 存档 {world_name} 已{'准备复制' if dry_run else '复制'}",
                             level="SUCCESS" if not dry_run else "SIMULATE")
                else:
                    self.log(f"❌ 复制存档失败: {msg}", level="ERROR")

            # 4. 复制 config
            self.log("\n【步骤4】复制 config 内容...", level="INFO")
            if self.progress_window and self.progress_window.cancelled:
                self.log("⚠️ 用户取消了迁移", level="WARNING")
                if progress_queue:
                    progress_queue.put(None)
                return

            src_config = src_path / "config"
            tgt_config = tgt_path / "config"
            if not configlist:
                self.log("ℹ️ config 清单为空，跳过", level="INFO")
            elif not src_config.exists():
                self.log(f"⚠️ 源 config 目录不存在: {src_config}，跳过", level="WARNING")
            else:
                if not dry_run:
                    tgt_config.mkdir(parents=True, exist_ok=True)

                success_cfg = 0
                failed_cfg = []
                for entry in configlist:
                    if self.progress_window and self.progress_window.cancelled:
                        self.log("⚠️ 用户取消了迁移", level="WARNING")
                        if progress_queue:
                            progress_queue.put(None)
                        return

                    src_entry = src_config / entry
                    if not src_entry.exists():
                        self.log(f"❌ 源 config 条目不存在: {entry}，跳过", level="ERROR")
                        failed_cfg.append((entry, "源不存在"))
                        continue

                    if src_entry.is_file():
                        entry_size = src_entry.stat().st_size
                    else:
                        entry_size = sum(f.stat().st_size for f in src_entry.rglob("*") if f.is_file())

                    dst_entry = tgt_config / entry
                    is_file = src_entry.is_file()
                    ok, msg = self.safe_copy(src_entry, dst_entry, dry_run, overwrite=True, is_file=is_file)
                    if ok:
                        success_cfg += 1
                        file_index += 1
                        copied_bytes += entry_size
                        if progress_queue:
                            progress_queue.put((file_index, f"config: {entry}", copied_bytes))
                        if dry_run:
                            self.log(f"[模拟] 将复制 config: {entry}", level="SIMULATE")
                        else:
                            self.log(f"✅ 已复制 config: {entry}", level="SUCCESS")
                    else:
                        failed_cfg.append((entry, msg))
                        self.log(f"❌ 复制 config 失败 {entry}: {msg}", level="ERROR")

                self.log(f"config 复制完成: 成功 {success_cfg} 个, 失败 {len(failed_cfg)} 个", level="INFO")
            # ---- 记录迁移历史（非模拟模式） ----
            if not dry_run and progress_queue:
                self._add_history_entry(tgt_path, src_path, modlist, configlist)
                self.log(f"📝 已记录迁移历史到 {self._get_history_path(tgt_path)}", level="INFO")
            self.log("\n========== 迁移完成 ==========", level="INFO")
            if dry_run:
                self.log("这是模拟运行，未实际修改任何文件。如需实际执行，请取消勾选【模拟运行】。", level="INFO")
            else:
                self.log("实际复制完成，请检查日志中的错误信息。", level="INFO")

            # 发送结束信号（仅当 progress_queue 非 None）
            if progress_queue:
                progress_queue.put(None)

        except Exception as e:
            self.log(f"❌ 迁移过程中发生未预期错误: {e}", level="ERROR")
            self.log(traceback.format_exc(), level="ERROR")
            if progress_queue:
                progress_queue.put(None)
            # 确保重置运行标志
            self._migration_running = False

def get_icon_path():
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(base_path, "1.ico")
    if os.path.exists(icon_path):
        return icon_path
    return None


def main():
    try:
        # 如果已有实例在运行，会自动退出并提示
        me = singleton.SingleInstance()
    except singleton.SingleInstanceException:
        # 已有实例，尝试激活窗口后退出
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "Minecraft 整合包迁移工具 - 增强版 v2")
            if hwnd:
                if ctypes.windll.user32.IsIconic(hwnd):
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except:
            pass
        sys.exit(0)
    def create_main_app():
        root = tk.Tk()
        icon_path = get_icon_path()
        if icon_path:
            try:
                root.iconbitmap(icon_path)
            except:
                pass

        # 设置窗口大小并居中
        width, height = 1000, 1080
        root.geometry(f"{width}x{height}")
        root.update()
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 2
        root.geometry(f"+{x}+{y}")

        app = MigrationGUI(root)

        def on_closing():
            # 检查是否正在迁移或扫描
            is_running = False
            if hasattr(app, '_migration_running') and app._migration_running:
                is_running = True
                operation_name = "迁移"
            elif hasattr(app, '_scanning') and app._scanning:
                is_running = True
                operation_name = "扫描模组差异"

            if is_running:
                result = messagebox.askyesnocancel(
                    f"⚠️ {operation_name}进行中",
                    f"{operation_name}任务正在执行，关闭窗口将中断操作，可能导致数据损坏或程序状态异常！\n\n"
                    "点击“是” → 立即强制退出（不推荐）\n"
                    "点击“否” → 返回程序，等待操作完成\n"
                    "点击“取消” → 取消本次关闭操作"
                )
                if result is None:  # 取消
                    return
                elif result is False:  # 返回程序
                    return
                else:  # 强制退出
                    app.save_config()
                    root.destroy()
            else:
                app.save_config()
                root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_closing)
        root.mainloop()

    splash = SplashScreen(create_main_app)

if __name__ == "__main__":
    main()

# 本工具由 Dreamtell 与 AI DeepSeek 协作完成，源码完全开源，欢迎 fork 和改进。
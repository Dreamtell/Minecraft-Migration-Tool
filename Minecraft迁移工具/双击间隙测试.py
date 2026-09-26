"""双击间隙诊断 —— 量出你自己的双击间隔，看它落在哪个窗口里。

背景（走过的弯路就不重复了）：程序里的"同一行点两下 = 打开详情"现在**完全用框架自带的
双击事件**（Qt 的 doubleClicked、Tk 的 <Double-Button-1>），不再自己算时间间隔、
也不再自适应。这样一来"多快算双击"就由两个窗口决定：

    放大查看窗口（Qt 试点，默认用它）    0.175s     ← 代码里写死的 DOUBLE_CLICK_MS
    Tk 的列表（卡片视图 / 模组差异列表） 0.50s      ← Windows 的"双击速度"设置

（0.175s 是用户按自己手感定的：两次单击绝不会被并成双击，只有"哒哒"很快的两下
  才算双击；慢一点就走右键菜单「ℹ 详情」。）

这个工具就是量你自己的手速，然后告诉你落在哪一档：

    python 双击间隙测试.py        （双击这个文件也行）

在方框里按**平时习惯的速度**双击 10 次（每次双击之间停一下），
程序给出最快 / 中位 / 最慢和结论。它**不改任何设置**，纯看。
"""
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.config import load_raw_config                                    # noqa: E402
from utils.helpers import measured_double_click_sec, system_double_click_sec  # noqa: E402

QT_WINDOW_SEC = 0.175    # ui/qt_big_view.py 里的 DOUBLE_CLICK_MS
TARGET = 10              # 想采样的次数
MIN_SAMPLES = 3          # 少于这个数不给结论
PAIR_MAX_GAP = 3.0       # 两下之间超过这个秒数就不算同一次双击
PAIR_MIN_GAP = 0.03      # 小于它的间隔当抖动（连击误触）忽略
COOLDOWN = 0.25          # 记完一次后静默这么久，免得三连击被拆成两次样本
OUTLIER_K = 2.0          # 超过中位数这么多倍的样本视为"误配的一对"，统计时剔除


def analyze(samples):
    """返回 (最快, 中位, 最慢, 剔除掉的离群样本)。"""
    s = sorted(samples)
    if not s:
        return 0.0, 0.0, 0.0, []
    med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2.0
    outliers = [x for x in s if x > med * OUTLIER_K]
    clean = [x for x in s if x <= med * OUTLIER_K]
    return min(clean or s), med, max(clean or s), outliers


def verdict(med):
    """按量出来的手速给结论（窗口值见文件头）。"""
    sys_sec = system_double_click_sec()
    lo, hi = sorted((sys_sec, QT_WINDOW_SEC))
    if lo < 0.25:
        # 窗口被刻意调得很小：这时候"双击开详情"基本不可用，重点提醒走右键菜单
        small = ("放大查看窗口（%.2fs）" % QT_WINDOW_SEC if QT_WINDOW_SEC < sys_sec
                 else "Tk 列表（%.2fs）" % sys_sec)
        tail = ("你的手速 %.2fs，另一边（%.2fs）%s。" % (
            med, hi, "还认得出双击" if med <= hi * 0.9 else "也偏慢、偶尔认不出"))
        return ("⚠ %s 太小了：正常速度的双击基本认不出来，开详情请用右键菜单「ℹ 详情」"
                "（单击选中不受影响）。%s" % (small, tail))
    if med <= lo * 0.9:
        return ("✅ 你的手速 %.2fs：放大查看窗口（%.2fs）和 Tk 列表（%.2fs）都能认，随便点。"
                % (med, QT_WINDOW_SEC, sys_sec))
    if med <= hi * 0.9:
        wider = ("放大查看窗口（%.2fs）" % QT_WINDOW_SEC if QT_WINDOW_SEC >= sys_sec
                 else "Tk 列表（%.2fs）" % sys_sec)
        return ("🟡 你的手速 %.2fs：%s 能认出来；另一个窗口（%.2fs）偶尔认不出 —— "
                "认不出时用右键菜单的「ℹ 详情」。" % (med, wider, lo))
    return ("🔴 你的手速 %.2fs 超过了两边的窗口（放大查看 %.2fs / Tk 列表 %.2fs）：双击都可能"
            "认不出来。建议再用这个工具练一轮（尽量连贯地『哒哒』两下），或者直接走右键菜单"
            "「ℹ 详情」。" % (med, QT_WINDOW_SEC, sys_sec))


class DoubleClickTest:
    def __init__(self):
        self.samples = []
        self._last = 0.0
        self._cool_until = 0.0
        self._done = False
        self._loaded_old = []
        try:
            cfg = load_raw_config()
            self._loaded_old = [float(x) for x in (cfg.get("double_click_samples") or [])]
        except Exception:
            self._loaded_old = []
        theme_name = str(load_raw_config().get("theme", "light") or "light")
        try:
            from utils.theme import DARK_THEME, LIGHT_THEME
            self.theme = DARK_THEME if theme_name == "dark" else LIGHT_THEME
        except Exception:
            self.theme = {"bg": "#f5f5f5", "fg": "#202020", "muted_fg": "#666666",
                          "entry_bg": "#ffffff", "accent_bg": "#2f7fd1"}
        self._build()

    # ------------------------------------------------------------------ 界面
    def _build(self):
        th = self.theme
        root = tk.Tk()
        self.root = root
        root.title("双击间隙诊断")
        root.configure(bg=th["bg"])
        root.resizable(False, False)
        try:
            from utils.helpers import set_window_icon
            set_window_icon(root)
        except Exception:
            pass

        pad = {"padx": 16, "pady": 6}
        tk.Label(root, text="🖱 双击间隙诊断", bg=th["bg"], fg=th["fg"],
                 font=("微软雅黑", 14, "bold")).pack(anchor="w", **pad)
        tk.Label(root,
                 text=("在下面的方框里，按你平时习惯的速度双击 %d 次。\n"
                       "每次双击之间停一下（程序会自动分对），不用刻意快或慢。"
                       % TARGET),
                 bg=th["bg"], fg=th.get("muted_fg", th["fg"]), justify="left",
                 font=("微软雅黑", 9)).pack(anchor="w", padx=16)

        self.area = tk.Canvas(root, width=560, height=190, highlightthickness=2,
                              highlightbackground=th.get("muted_fg", "#999999"),
                              bg=th.get("entry_bg", "#ffffff"))
        self.area.pack(padx=16, pady=10)
        self.area.bind("<Button-1>", self._on_click)
        self._draw_area()

        self.progress = tk.Label(root, text="", bg=th["bg"], fg=th["fg"],
                                 font=("微软雅黑", 10, "bold"))
        self.progress.pack(anchor="w", padx=16)
        self.detail = tk.Label(root, text="", bg=th["bg"],
                               fg=th.get("muted_fg", th["fg"]), justify="left",
                               font=("微软雅黑", 9))
        self.detail.pack(anchor="w", padx=16)

        row = tk.Frame(root, bg=th["bg"])
        row.pack(fill="x", padx=16, pady=10)
        self._btn(row, "↺ 重新测", self.reset, "#757575", "#9e9e9e")
        self._btn(row, "关闭", root.destroy, "#c62828", "#e0574f")

        self.status = tk.Label(root, text="", bg=th["bg"], fg=th.get("muted_fg", th["fg"]),
                               justify="left", wraplength=560, font=("微软雅黑", 9))
        self.status.pack(anchor="w", padx=16, pady=(0, 12))
        self._refresh()

        try:
            from utils.helpers import center_window, is_dark_theme, style_window
            center_window(root, 600, 440)
            style_window(root, dark=is_dark_theme(th))
        except Exception:
            pass

    def _btn(self, parent, text, cmd, c1, c2):
        try:
            from utils.helpers import create_gradient_button
            b = create_gradient_button(parent, text, cmd, colors=(c1, c2), width=126,
                                       height=30, font=("微软雅黑", 9, "bold"))
        except Exception:
            b = tk.Button(parent, text=text, command=cmd)
        b.pack(side="left", padx=(0, 8))
        return b

    def _draw_area(self):
        th = self.theme
        c = self.area
        c.delete("all")
        w = int(c["width"])
        h = int(c["height"])
        if self._done:
            head = "✔ 测完了"
            sub = "看下面的结论；按「重新测」可以再来一轮"
        elif self.samples:
            head, sub = "继续双击…", "还差 %d 次" % (TARGET - len(self.samples))
        else:
            head, sub = "在这里双击", "点两下算一次，共 %d 次" % TARGET
        c.create_text(w // 2, h // 2 - 14, text=head, fill=th["fg"],
                      font=("微软雅黑", 18, "bold"))
        c.create_text(w // 2, h // 2 + 22, text=sub, fill=th.get("muted_fg", "#888888"),
                      font=("微软雅黑", 10))

    # ------------------------------------------------------------------ 逻辑
    def _on_click(self, _ev=None):
        now = time.perf_counter()
        if now < self._cool_until:
            return
        gap = now - self._last if self._last else 0.0
        self._last = now
        if self._done:
            return
        if gap and PAIR_MIN_GAP <= gap <= PAIR_MAX_GAP:
            self.samples.append(gap)
            self._cool_until = now + COOLDOWN
            self._last = 0.0            # 这一对记完了，下一击重新开始配对
            if len(self.samples) >= TARGET:
                self._done = True
            self._draw_area()
            self._refresh()
        elif gap > PAIR_MAX_GAP:
            self._last = now            # 隔太久：这一击当下一对的第一下

    def reset(self):
        self.samples, self._last, self._cool_until, self._done = [], 0.0, 0.0, False
        self._draw_area()
        self._refresh()
        self.status.config(text="")

    def _refresh(self):
        n = len(self.samples)
        self.progress.config(text="已记录 %d / %d 次" % (n, TARGET))
        if self.samples:
            fast, med, slow, outliers = analyze(self.samples)
            txt = ("每次间隔（秒）：%s\n最快 %.2f  中位 %.2f  最慢 %.2f"
                   % ("  ".join("%.2f" % x for x in self.samples[-8:]), fast, med, slow))
            if outliers:
                txt += ("\n已忽略 %d 个离群样本（%s，多半是中途停顿后误配的一对）"
                        % (len(outliers), "、".join("%.2fs" % x for x in outliers)))
            if n >= MIN_SAMPLES:
                self.status.config(text=verdict(med))
        else:
            old = measured_double_click_sec()
            txt = ("当前系统双击速度：%.2fs   放大查看窗口：%.2fs%s\n"
                   "（双击 = 在同一个地方点两下）"
                   % (system_double_click_sec(), QT_WINDOW_SEC,
                      "；配置里还留着旧的判定值 %.2fs，程序已经不读它了" % old
                      if old else ""))
            self.status.config(text="")
        self.detail.config(text=txt)


def main():
    app = DoubleClickTest()
    app.root.mainloop()


if __name__ == "__main__":
    main()

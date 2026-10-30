# 开始迁移按钮：3D 翻转（交接）

> 2026-10-30 · 记录人：DSH 派单会话 · 对应提交 `f0c8fdc`
> 这份文件是给「换台机器、换个会话」用的：目标怎么来的、东西落在哪、数字是多少、
> 哪些是拍板的、哪些是我替你定的、下一步能做什么。

## 一句话现状

「开始迁移」这一颗按钮已经从老工厂（亮色药丸渐变 + 白字）换成 `特效2.py` 原型那套
近黑 CS2 语言 + 真透视 3D 翻转；视觉、行为、性能都已验收通过并推到 `origin/main`。
**用户还没在自己的机器上看过实机效果**（他那会儿跑着的实例还是老代码）。

## 任务是怎么走到这儿的

1. 用户说 CS2「开始匹配」按钮的切换动画吸引他 → 我追问，他答「**它会翻转**」
   → 要的是 3D 翻转，不是扫光、不是手风琴展开
2. 先在根目录 `特效2.py` 里做原型（PySide6 + PIL，可直接跑、可键位调参），
   绿/红双面、上下翻、圆角无锯齿 → 用户「帅」
3. 用户要求接进主程序：只做「开始迁移」这一颗，文案换主程序真实的，设置里可选
   干脆/标准/沉；翻的时机 = 点下去翻红、跑完翻回，另外「模拟运行开关打开时正面变黄、
   文案改『模拟运行』」；手感设置放设置窗「🎨 外观与启动」
4. 第一版接完我按主程序的按钮语言做成「亮色药丸 + hover 提亮」，用户否掉：
   「**我想要原特效文件一模一样的按钮，不要模仿本程序**」→ 返工成原型的视觉语言

## 产物与落点

| 文件 | 说明 |
|---|---|
| `Minecraft迁移工具/ui/flip_button.py`（新增，1234 行） | `FlipButton(tk.Canvas)` + `create_flip_button` 工厂；**纯 Tk + PIL，不 import Qt** |
| `ui/mw_pages.py:687` | `create_gradient_button` → `create_flip_button`（只换这一颗，其余 86 颗不动） |
| `ui/mw_migration.py:1174` | `_flip_start_btn`：点下去翻红、跑完翻回；`getattr` 兜底，降级回老工厂不报错 |
| `ui/mw_settings.py` | 「🎨 外观与启动」页的三档单选 + `_set_start_btn_feel` |
| `ui/main_window.py` | 配置键 `start_btn_feel` 的默认值/非法值回落 + 进 `save_config()` |
| `utils/i18n.py` | 新文案（开始迁移 / 模拟运行 / 迁移中... / 三档标签） |
| `特效2.py`、`特效测试.py` | 原型脚本（已一并提交，台式上可继续对着调手感） |

## 关键参数（都在 `flip_button.py` 顶部，改一个常数就能微调）

- 配色：`FACE_IDLE` / `FACE_SEARCH` 是原型原值（`#1b2a20→#0a120d`、竖条 `#3d5647`、
  描边高亮 `#7ee0a8`、accent `#46cf8a`；背面 `#2c1a1a→#120a0a`、accent `#ff5a5a`）；
  `FACE_DRY`（模拟运行的琥珀面）是我按 IDLE 的明度关系推的，原型里没有这一面；
  `FACE_DISABLED` 是暗面 + 灰竖条 + 灰细描边
- 几何/线宽：`_RADIUS 8`、`_ACCENT_W 4`（左竖条）、`_BORDER_W 1.2`、`_BORDER_INSET 0.8`、
  `_SS 4`（4x 超采样）、`_CAM 900`
- 动效：`_SWEEP_MS 780`、`_SWEEP_PEAK 80`、`_SWEEP_HALF_RATIO 110/420`（按比例，别用绝对像素）、
  斜纹 alpha 26 / 线宽 6 / 间距 28 / 55px·s⁻¹、进度条高 3、`_UI_MS 33`（≤30fps）、
  `_HV_IN 0.09` / `_HV_OUT 0.15`（hover 淡入淡出）、`_HV_LEVELS (0,⅓,⅔,1)`（量化档）
- 三档：`crisp 240ms/over 1.30`、`standard 320/0.90`、`heavy 460/0.55`（`sink` 0.05/0.05/0.08）
- 文字：`text_px` 16（0.42 高），静止帧的文字交给 Tk text item（自带字体回退，emoji 正常），
  只有翻转中间帧才把文字烤进 PIL

## 验收数字（真主窗口，152×38，DPI 1.25）

- **与原型像素对齐**：把原型 hover 关掉（`fx.hover_amt=0`）后同一取样点
  面顶 `(25,39,30)` / 面底 `(12,21,15)` / 竖条 `(61,86,71)=#3d5647`；
  按钮 `(25,39,29)` / `(12,21,15)` / `(56,79,66)`
- 三档时长 246.5 / 328 / 473ms（目标 240/320/460）
- 单帧 avg 0.7ms、p95 1.0ms、冷帧最大 3.56ms；hover tick 0.31ms、背面 tick 0.49ms
- hover 整面 92.7% 像素变化、离开逐字节复原、禁用态零变化
- 定时器只在需要时起表：idle 停 → hover 起 → 离开停 → 背面起 → 翻回停 → 禁用停
- 透视证据：宽度全程 128~132px（±3%），高度 38→2→38，90° 只有一条 2px accent 亮线

## 拍板过的（用户明确说的，别改）

- 只手这一颗按钮，其余 86 颗不动
- 文案换主程序的真实文案；「模拟运行」时正面黄
- 翻的时机：点下去翻红、跑完翻回（不是 hover 就翻）
- 手感设置放设置窗「🎨 外观与启动」
- 没有副标题（38px 高放不下原型那行 SOLO QUEUE）
- 保留 emoji（🚀 / ⏳）

## 我替他定的取舍（他要是不满意，改起来都是常数）

1. 标题 16px —— 原型 30/96≈0.31 折算到 38px 只有 12px，太瘦
2. 圆角 8 —— 原型的 12 是 96px 高上的值；按钮族既定值就是 8
3. 琥珀面的具体色值（`FACE_DRY`）
4. hover 的 4 档量化（不是连续曲线）—— 为了压住 tick 开销

## 环境与踩坑

- Python 3.12.10；Pillow / PySide6 6.12 / tkinterdnd2 0.6.4 / PyInstaller 6.22.3
- 控制台是 GBK：跑脚本前 `export PYTHONIOENCODING=utf-8`，别打印 ✓ 之类符号
- 产品源码 CRLF；根目录原型脚本与 `docs/*.md` 用 LF（`core.autocrlf=true`，
  checkout 后可能变 CRLF，需要严格 LF 就加 `.gitattributes` —— 目前还没加）
- 主进程绝不 import Qt：所以要画像素只能用 PIL，不能用 QtGui
- 探针：`MCTOOL_CONFIG` 指向真实配置的副本 → `SetProcessDpiAwareness(2)` →
  直接 `MW.MigrationGUI(root)`（绕开单实例锁与闪屏）→ PrintWindow + GetDIBits 抓图。
  **ctypes 必须给 `SelectObject/GetDIBits/CreateCompatibleBitmap/PrintWindow/GetClientRect/
  GetDC/ScreenToClient` 设 argtypes**，否则 `OverflowError: int too long to convert`
- `taskkill //F //IM python.exe` 会连测试进程一起杀；Windows 的 `timeout.exe` 不认 GNU 参数
- 用 importlib 加载 `特效2.py` 必须先 `sys.modules['fx2'] = m` 再 `exec_module`，
  否则 dataclass 报 `'NoneType' object has no attribute '__dict__'`
- `特效2.py` 的 `_prepare()` 里 `hover_amt` 初值是 **1.0** —— 直接 `--shot` 截出来的
  原型图是**悬停态**，拿它当常态对照会误判

## 想自己看效果 / 自己调手感

```bash
# 原型：可键位实时调参（空格翻转 / P 手感 / X 翻轴 / ←→ 时长 / ↑↓ 过冲 / , . 透视 / -= 圆角）
python 特效2.py
python 特效2.py --shot out.png --flip 0.35      # 定格某帧截图
python 特效2.py --bench 400                     # 压测，报 avg/p95

# 主程序：重启才看到新按钮
python Minecraft迁移工具/app.py
```

## 下一步可以做的

1. 用户在实机上过一眼，可能要微调标题字号 / 圆角 / 琥珀色值
2. 想推广到别的按钮（回滚、查看历史）需要让 `create_flip_button` 支持自定义三面文案与
   配色 —— 现在文案硬编码在 `mw_pages.py` 的调用处
3. `docs/HANDOFF.md` 有两个「## 六」小节（历史遗留，未修）

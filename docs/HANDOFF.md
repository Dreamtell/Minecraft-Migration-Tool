# 会话交接（Hand-off）

> 基线提交：`cd38991` · 本地 = `origin/main`，工作区干净
> 最近更新：2026-10-25
> 这份文件是跨会话的记忆。新会话开工前先读它，能省掉一轮"你重新熟悉环境"。
> 文中的路径与提交号都经过实测核对；与旧版记录冲突时**以本文为准**。

---

## 一、项目与环境

| 项 | 值 |
|---|---|
| 仓库 | `%USERPROFILE%\PycharmProjects\Minecraft-Migration-Tool`（分支 `main`） |
| 源码根 | `Minecraft迁移工具\`，48 个 `.py` |
| Python | `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`（3.12.10） |
| 远端 | `https://github.com/Dreamtell/Minecraft-Migration-Tool.git`（推送正常，GCM 凭据 `Dreamtell`） |
| 配置 | `~/.minecraft_migrate_config.json`；测试可用环境变量 `MCTOOL_CONFIG` 指向临时文件 |
| 档案目录 | **`~/migration_history`**（= `Path(CONFIG_FILE).parent / "migration_history"`，见 `utils/history_store.py:_root()`） |
| 已装依赖 | PySide6 6.12.0 · Pillow 13.0.0 · requests 2.34.2 · tkinterdnd2 0.6.4 · PyInstaller 6.22.3 |

> ⚠ 旧版交接把档案目录写成 `~/.minecraft_migration_history/`，**是错的**。实测是
> `%USERPROFILE%\migration_history`（当前有 6 份档案）—— 它跟着配置文件所在目录走，
> 所以 `MCTOOL_CONFIG` 指向临时文件时档案也跟着搬走。

**架构**：Tk 主进程 + PySide6 子进程（`app.py --qt-host` 画大视图/图表，避免 Tk/Qt GIL 冲突）。
**主进程绝不 import Qt。** 两进程只交换 JSON 小文件，写盘前会剥掉密钥。

**约定**：

- 源码 CRLF；
- 控制台 GBK —— 跑脚本前 `export PYTHONIOENCODING=utf-8`；
- 打印别用 ✓ 之类符号（会 `UnicodeEncodeError`）；
- 中文是安装态零成本路径：中文下翻译层根本不装；新增文案只改 `utils/i18n.py`，
  拼接串走 `trf` / `trp` 模板；
- 新增配置键**必须同时出现在 `ui/main_window.py` 的 `save_config()` 里**，
  否则用户下次改任何设置都会把它抹掉（`save_config()` 是整体重建字典）。

---

## 二、已完成的工作（按主题）

### 浮标 `ui/net_float.py`（1540 行，改动最多）

- 独立置顶窗口 → **非置顶**，靠 `lift()` 压在主窗口之上；本程序有其它窗口
  （设置/历史/图表/对话框）时**不抬升**，让对话框盖住它
- 只在**本程序前台**时显示（`GetForegroundWindow` 比 pid），被别的程序覆盖即隐藏
- 显示延迟 **150ms**（对齐 Windows 显示动画）；关闭/托盘**立即**隐藏（16ms）；最小化保留 90ms 淡出
- 拖动：**定时器驱动**（8ms，`GetCursorPos` 现取光标），不依赖 `<B1-Motion>`；
  `SetCapture` + `GetAsyncKeyState` 兜底；几何缓存
- 收纳：贴边 8px 触发，缩成 22px 小标签，悬停展开；**溶解式**换形态（淡出→换尺寸→淡入）
- 提示特效：脉冲绿环（**预渲染 6 帧**循环）+ 窗口边缘**绿色光带 + 直线粒子**（可设置关闭）
- 绿色/红色主题随网络状态切换；`_park()` 挪到 -32000 代替 `withdraw`

### 延迟图表 `ui/mw_pages.py`（1595 行）

- Modrinth / CurseForge **两条线**，共用时间基准，PCHIP 插值，独立末端缓动
- 面积填充（`antialiased=False`，否则 28fps）、顶部横排图例、悬停同时显示两边
- x 轴**先填满再滚动**（`基准 = min(跨度, 窗口)`），窗口 120s
- 60fps blitting；窗口不在前台降到 30fps；拖动浮标时让路
- 图表窗口三态点击：关→开、开着在后面→提前、已在前→关（用 `WindowFromPoint` 判断，
  **不能用前台窗口**）

### 迁移历史

- `utils/history_store.py`（193 行）：时间戳档案（`20261022-233015-482.json`）、
  上限 1~15（默认 10，设置可调）、原子写、日志截断 200k
- `core/migrator.py` 收尾写档案：日志（含级别）、统计、清单、失败明细；**模拟运行也存**（标 `dry`）
- `ui/mw_migration.py`（1371 行）：卡片列表（圆角、悬停、缓动滚动、只挪坐标不重建）、
  右侧详情（数据分级配色）、日志**按级别上色**（旧档案按图标推断）、**窗口内放大**
  （列表宽度动画）、同档案点击不重渲染

### 其它

- 整合包更新警告条（复制 AI 提示词 / ⚠ 按钮关闭）
- 快捷链接加 MC百科、PCL
- 空清单时「扫描差异 / 放大查看」置灰（`btn.state("normal"/"disabled")`）
- 点空白只失焦，不再关「主界面编辑」开关
- 删除整机流量统计（psutil），打包脚本去掉 `--hidden-import psutil`
- CPU：迷你图 20fps→4fps，空闲 7.8%→4.3%

---

## 三、新增配置键

`history_limit` · `pack_warning` · `float_edge_fx` ·
`net_float_on` / `_x` / `_y` / `_size` / `_shadow` / `_dock`

---

## 四、踩坑记录（务必保留）

### 浮标的五条命

1. **创建时 `withdraw()` 会让 `UpdateLayeredWindow` 永久失败** → 窗口只剩方块底色。
   隐藏必须用 `_park()`（挪到 -32000）。
2. `transient()` 会把窗口变成子窗口（被裁剪、坐标变客户区相对）。
3. 拖动期间 **Enter/Leave 与提示变化绝不能重画图标**（6 倍超采样一次 20~30ms，就是"掉帧"）。
4. `wm_frame()` 在窗口隐藏时返回子窗口 → 用 `_real_hwnd()` 向上找。
5. `-topmost` 会压住别的程序 → 已去掉。

### 迁移锁屏 `ui/mw_lock.py`（506 行）

- 流动边框用**定长渐变瓦片首尾相接 + 每帧整体平移**（十几次 `canvas.move`），
  不要逐帧改每个格子的颜色（上百次 `itemconfigure`）。瓦片定长 240px，
  窗口缩放只增减瓦片、不重渲图。
- **入场动画试过 8 版全部翻车，结论是别做**：逐字浮现依赖 `after` 准时，而真实迁移时
  主线程忙于刷日志，`after` 被大幅延后（标题一直停在"正"）；盖层类（半透明描边/扫光）
  更糟 —— Tk 画布不支持真透明，直接盖住内容。现在锁屏**直接完整出现**，动感交给流动边
  （入场先快后慢，见 `_flow_boost`），它不依赖 `after` 准时，也从不遮内容。
- **完成态的按键绑定必须延迟注册**（`after(260, ...)`）：迁移期间用户在遮罩上敲的键
  没有绑定去处理，会积压在 tkinter 事件队列里；绑定一注册，旧按键立刻被投递，锁屏在完成的
  瞬间就被收掉（用户报过"还没完成时按键 → 完成时立即退出"）。

---

## 五、待办

| 优先级 | 事项 |
|---|---|
| **进行中** | 用户口述清单：已过「浮标」「迁移历史」，**下一条是「迁移锁屏」** |
| 中 | 打包验证（`--exclude-module numpy` 去掉后 matplotlib/TkAgg 未实跑） |
| 中 | MC百科接入（跳转链接零风险 → API 需申请） |
| 低 | 浮标改用 Qt 重写（消除手写 ULW/淡化/捕获） |

---

## 六、测试套路（可直接复用）

启动 app 并接管 mainloop：

```python
import ui.main_window as MW
_orig = MW.MigrationGUI
class Rec(_orig):
    def __init__(self, *a, **k):
        _orig.__init__(self, *a, **k)
        box["me"] = self
MW.MigrationGUI = Rec
tk.Misc.mainloop = lambda self, n=0: (self.after(18000, step) or _ml(self, n))
import app
app.main()
```

- 截图：`u.SetProcessDpiAwarenessContext(-4)` + `GetWindowRect` + `BitBlt` + `GetDIBits`
- 模拟点击：**窗口必须可见**（`withdraw` 的窗口收不到 `event_generate`）
- 模拟拖动：`GetAsyncKeyState` 打桩返回 `0x8000`，鼠标用 `SendInput`
- **注意**：`taskkill //F //IM python.exe` 会连带杀掉测试进程

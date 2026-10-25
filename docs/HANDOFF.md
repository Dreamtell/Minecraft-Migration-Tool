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

### 浮标的六条命

1. **创建时 `withdraw()` 会让 `UpdateLayeredWindow` 永久失败** → 窗口只剩方块底色。
   隐藏必须用 `_park()`（挪到 -32000）。
2. `transient()` 会把窗口变成子窗口（被裁剪、坐标变客户区相对）。
3. 拖动期间 **Enter/Leave 与提示变化绝不能重画图标**（6 倍超采样一次 20~30ms，就是"掉帧"）。
4. `wm_frame()` 在窗口隐藏时返回子窗口 → 用 `_real_hwnd()` 向上找。
5. `-topmost` 会压住别的程序 → 已去掉。
6. **`_blit` 里 ULW 的 `SourceConstantAlpha` 必须用 `self._alpha`**。曾经写死 255，
   于是 `_fade_to()` 只改 `_alpha` 却合成不出任何变化 —— 实测同一窗口
   alpha=255 / 0 / 128 的截屏像素**完全一致**，也就是说"溶解式换形态"一直是硬切，
   贴边收纳时看着就是闪一下。判断这类问题别读代码猜，**截屏比对**一次就定案。
   同文件的 `_EdgeGlow._blit` 一直是对的，可以当参照。
   另外淡入淡出必须按真实时间 + 跟随刷新率排帧（`after(16)` + 固定帧数在真 mainloop 里
   一半的帧会滑到 31ms，45ms 的淡出只剩 2~3 帧还不均匀）。

### 迁移锁屏 `ui/mw_lock.py`（506 行）

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

## 五、动画帧节奏（2026-10 重做，务必先读）

### 契约：`utils/anim.py` 是唯一入口

| 接口 | 语义 |
|---|---|
| `anim.frame_ms(win)` | 一帧毫秒。**跟随窗口所在那块屏的刷新率**，上限 240Hz（本机 240Hz → 4ms） |
| `anim.slow_ms()` | 降帧档 33ms（30fps） |
| `anim.is_slow(win)` | 窗口最小化 / 失去焦点 |
| `anim.delay(win, slow=False)` | 本帧该排多久；`slow` 或 `is_slow` 时给 33 |
| `anim.hold()` / `anim.release()` / `anim.holding()` | **引用计数**管 `timeBeginPeriod(1)`，只在动画期间占用 |

**铁律：帧率只改"多久排下一帧"，动画速度必须按真实 dt 算。** 所有插值系数都要写成
`a' = 1 - (1 - a) ** (dt / 基准帧)`，位移量按 `dt / 基准帧` 线性缩放。只缩放其中一边会快/慢数倍。

### 坑 1：Tk 的 `after()` 被 15.6ms 定时器粒度卡住（不 raise 就快不了）

真 mainloop（阻塞在 `GetMessage`）下自链定时器实测：

| | 无 `timeBeginPeriod` | 有 `timeBeginPeriod(1)` |
|---|---|---|
| `after(16)` | 50.0fps，p90 **30.45ms** | 61.4fps，p90 16.98ms |
| `after(8)` | 63.4fps / p50 15.87 | 120.3fps / p50 8.06 |
| `after(4)` | 64.3fps / p50 15.89 | 232.5fps / p50 4.05 |

也就是说**改之前线上那些 16ms 动画其实只有 50fps，一半帧滑到 30ms**。
⚠ **千万不要在"忙泵"里量定时器**（一边 `root.update()` 一边轮询）：泵一直烧消息队列，
定时器不用等，会量出 `after(4)=228fps` 的假象——第一版就是这么错的。

### 坑 2：`time.monotonic()` 在本机只有 15.6ms 粒度

`time.get_clock_info("monotonic").resolution == 0.015625`（GetTickCount64）。4ms 排帧时
**4 帧里有 3 帧 dt 算成 0**。算 dt 一律用 `time.perf_counter()`（分辨率 1e-7）。
仓库里还剩 `hangwatch.py`（100ms 看门狗）和 `mw_log.py:448`（25ms 淡入节流）在用 monotonic，
它们的量级远大于 15.6ms，**不需要改**。

### 坑 3：`self._w` 是 tkinter 的控件路径名，不能拿去存东西

`_ArchiveList._build` 曾写 `self._w, self._h = w, h`，覆盖了 tkinter 内部的控件路径名。
后果是连锁的：`winfo_exists()` 返回 0、`winfo_width()/winfo_toplevel()` 抛
`TclError: bad window path name 420`、`<Destroy>` 绑定不响、`anim.is_slow()` 永远 False。
已改名 `_卡宽/_卡高`。**任何 Tk 控件上都别再定义 `_w` / `_h` 这类名字。**

### 坑 4：PySide 的 `destroyed` 信号只投递给 lambda / 模块级函数

绑定方法（`self.method`）**0 次命中**（Python 3.12 + PySide6 6.12 实测）。
所以 Qt 那边"控件销毁就还定时器精度"不能挂在绑定方法上，要挂
`lambda *_, _st=对象: _st.release()`，而那个对象必须是纯 Python 小对象，不能碰 PySide 实例。

### 坑 5：Qt 的 QTimer **不**吃 15.6ms 粒度（和 Tk 不同）

裸 `QTimer(4)` 自链在真 `app.exec()` 下，**不 raise 定时器精度**也有 249.6fps / p50 4.00ms；
CoarseTimer 与 PreciseTimer 都能排到 4ms（p50 4.00 / 4.01）。选 Precise 只为避开 Coarse 的
5% 容差。所以"Qt 侧必须 hold"是错的，但 Qt 侧照样 hold —— 让所有滚动面共用一套策略。

### 坑 6：视图被 see() 硬拽时，平滑滚动必须让位

日志这类会"自动跟到底"的控件，`see(tk.END)` 和平滑滚动是**两个驱动**：动画链的目标按
像素算，视图被硬拽之后那条链还会按旧目标继续往底部顶（顶到"滚不动"才退出），表现就是
"被拽一下又自己往前拱"。所以拽之前先 `sc.stop()`。

而且 `see()` 必须节流（现为 80ms）：`ui/mw_log.py` 的注释里曾写着"80ms 一次"，
**代码里根本没有实现**，每行都拽一次 —— 实测日志涌入 170 行/秒时 `see` 也是 170 次/秒，
节流后约 10 次/秒。**注释承诺的行为一定要去代码里核对**，这个仓库已经出现过两次这种偏差。

### 实测成绩（本机 240Hz）

| 目标 | 改前 | 改后 |
|---|---|---|
| 文本类平滑滚动（`SmoothScroller`） | 60fps（实际 50fps，p90 30ms） | 205~235fps，p50 4.2ms，p90 4.9ms |
| 迁移历史档案卡（`_ArchiveList`） | 32fps / p50 31ms | 205fps / p50 4.57ms（约 73% 单核，只在滚动那几百毫秒） |
| 迁移历史日志框（`_SmoothText`） | 每帧 1 行 | 按真实时间推进，16ms 与 4ms 排帧速度差 0.02% |
| Qt 大视图（`_SmoothWheel`） | 73fps / p50 12.13ms | 242fps / p50 4.00ms |

**速度不变性**是单独验过的：同样 600px，4 / 16 / 33ms 三档排帧的收敛时间是
400.8 / 400.0 / 400.2ms。验收任何帧率改动时这两条要一起看：fps 上去了、收敛时间没变。

**滚动速度用 `SmoothScroller.GLIDE_SCALE` 显式标定**（默认 1.25）。帧率从 62 提到
229 之后，同样一格滚轮的总耗时从 97ms 掉到 76ms —— 速度不该由帧率隐式决定。实测（一格 39px、
4ms 排帧）：1.0 → 72ms、1.25 → 92ms、1.4 → 101ms、1.6 → 112ms。觉得快/慢就调这一个数，
别去改 ease/accel。注意 Qt 大视图（WHEEL_FRAME_S）和迁移历史的 _SmoothText（_REST）各有
自己的等价常量。

### 排帧的一个技巧

`after(anim.delay(...))` 是"从现在起算"，帧周期会变成"一帧的活 + 延迟"（实测只有 131fps）。
要真的排到目标帧率，必须扣掉本帧已花的时间：`after(max(1, anim.delay(self) - 用了))`。
慢档（33ms）语义不变。副作用：当一帧的活本身就超过延迟时，会以 `after(1)` 连跑 = 吃满一个核，
这是"跑满刷新率"的固有代价，不是 bug。

---

## 六、待办

| 优先级 | 事项 |
|---|---|
| **进行中** | 用户口述清单：已过「浮标」「迁移历史」，**下一条是「迁移锁屏」** |
| 中 | 打包验证（`--exclude-module numpy` 去掉后 matplotlib/TkAgg 未实跑） |
| 中 | MC百科接入（跳转链接零风险 → API 需申请） |
| 低 | 浮标改用 Qt 重写（消除手写 ULW/淡化/捕获） |
| 低 | 档案卡列表每帧 4.6ms 里 Tk 重绘占 3.6~4.4ms；要再降只能改画法（Tk 原生绘制/减图元） |
| 低 | 未改动的动画仍是老节奏：曲线图 4fps、进度条 30fps、流动边 30fps、标签页滑动 12ms（本轮只做滚动） |

---

## 七、测试套路（可直接复用）

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

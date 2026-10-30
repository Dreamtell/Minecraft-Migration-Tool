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

### 开始迁移按钮（3D 翻转）`ui/flip_button.py`（1234 行）

详细交接（任务来源、取舍、自测步骤）：`docs/HANDOFF-flip-button.md`

- 只换了「开始迁移」这一个按钮（`mw_pages.py:687` 把 `create_gradient_button` 换成
  `create_flip_button`）；其余 86 个按钮仍是 `helpers.py:1684` 的老工厂
- **视觉语言照抄 `特效2.py` 原型**（用户原话「我想要原特效文件一模一样的按钮，不要模仿
  本程序」）：近黑暗面竖直渐变 + 左侧 4px 竖条 + 1.2px 细描边（内缩 0.8）+ 悬停白色斜
  扫光 + 红背斜纹 / 底部进度条；没有副标题（38px 高放不下 SOLO QUEUE 那行，用户选的）
- 像素级对齐证据：关掉原型 hover（`fx.hover_amt=0`，注意 `_prepare` 默认给的是 1.0，
  直接 `--shot` 截出来的原型图**是悬停态**）后同一取样点 face_top (25,39,30) /
  face_bot (12,21,15) / 左竖条 (61,86,71)=`#3d5647`；我的按钮 (25,39,29) / (12,21,15) /
  (56,79,66)
- **真透视不是压扁**：四角 3D 旋转 → 相机透视 → `PIL Image.transform(PERSPECTIVE)` 贴面。
  实测宽度几乎不变（128~132px）、高度 38→2→38，90° 时收成一条 2px 的 accent 亮线
- 三面语义：正面绿（🚀 开始迁移）/ 正面黄（🚀 模拟运行，`dry_run` 打开时）/ 背面红
  （⏳ 迁移中...）。禁用态是暗面 + 灰竖条 + 灰细描边（不是亮灰药丸）；翻转过程不切灰
  （否则前半程闪一下）；背面永不灰（否则「迁移中」只剩一块灰）
- 点下去翻红、跑完翻回；入口 `mw_migration.py:1174 _flip_start_btn` 用 getattr 兜底，
  按钮降级回老工厂时不报错
- 手感三档（设置 → 🎨 外观与启动）：干脆 240ms / 标准 320ms / 沉 460ms，`over` 分别
  1.30 / 0.90 / 0.55（配置键 `start_btn_feel`）；实测 246.5 / 328 / 473ms
- hover 不再是 `hover_pair` 提亮，而是原型的 `lerp(edge, edge_hot, hv)` 换面 + 扫光；
  淡入 90ms / 淡出 150ms，按 4 档量化（0/⅓/⅔/1）
- 常驻动画由 `after(33ms)` 驱动（≤30fps），`_tick_wanted` 判定：只有 hover 未到位、
  扫光、背面斜纹/进度条在跑时才起表；离开 / 翻回 / 禁用都会停表（实测 idle false →
  hover true → leave false → 背面 true → 正面 false → 禁用 false，不空转）；
  hover tick 0.31ms、背面 tick 0.49ms
- 帧表在构造后 idle 预热并缓存；单帧重绘实测 avg 0.7ms / p95 1.0ms（240Hz 预算 4.1ms），
  冷帧最大 3.56ms
- 它踩过的坑：① tkinter 保留 `self._w`（控件路径名），存宽度会被 `Canvas.__init__`
  覆盖成字符串 → 一律用 `_cw/_ch`；② PIL 里 `msyhbd.ttc` 把 🚀 画成豆腐块、
  `seguiemj.ttf` 没有 CJK → 静止帧的文字交给 Tk text item（自带字体回退），
  只有翻转中间帧才把文字烤进 PIL；③ Tk 的 `PhotoImage` 不混合半透明像素，
  每帧都必须贴到不透明底色上；④ `Image.transform(PERSPECTIVE)` 要的是「输出→输入」
  系数；⑤ ease-out-back 取整会跳过 0.5，必须把最接近 0.5 的帧钉成 0.5 才有那条亮线；
  ⑥ 扫光半宽必须按比例取 `110/420=0.2619`（照抄绝对 110px 会把 110 宽的按钮整面刷白）；
  ⑦ 圆角取 8（原型的 12 是 96px 高上的值，按钮族既定值是 8）
- 有意偏离原型的取舍：标题 16px（原型 30/96≈0.31，折算到 38px 只有 12px 太瘦）；
  圆角 8；无副标题；保留 emoji
### 其它

- 整合包更新警告条（复制 AI 提示词 / ⚠ 按钮关闭）
- 快捷链接加 MC百科、PCL
- 空清单时「扫描差异 / 放大查看」置灰（`btn.state("normal"/"disabled")`）
- 点空白只失焦，不再关「主界面编辑」开关
- 删除整机流量统计（psutil），打包脚本去掉 `--hidden-import psutil`
- CPU：迷你图 20fps→4fps，空闲 7.8%→4.3%

---

## 三、新增配置键

`history_limit` · `pack_warning` · `float_edge_fx` · `start_btn_feel` ·
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
7. **贴边悬停的展开/收缩不能用 `<Enter>`/`<Leave>` 判断**。展开会让窗口变大（右贴边时向左长），
   Windows 在几何变化时会**再判一次 `<Leave>`** —— 假离开。据它收缩就进死循环：
   收缩 → 光标落回小标签 → `<Enter>` → 展开 → 假 `<Leave>` → 收缩……
   实测光标**完全静止**时 2.2 秒里换了 5 次形态、发起 2 次收缩（看着就是不停闪）。
   原来靠 180ms 延迟只是把循环拖慢，没根治。现在的判据是 `_cursor_inside()`
   （`GetCursorPos` + 窗口矩形）：`_on_leave` 里光标还在就忽略，`_collapse_now` 里再确认一次。
   修完：静止 2.2 秒 0 次换形态；真实进出每次只换 1 次（进/出/进 = 3 次）。
6. **`_blit` 里 ULW 的 `SourceConstantAlpha` 必须用 `self._alpha`**。曾经写死 255，
   于是 `_fade_to()` 只改 `_alpha` 却合成不出任何变化 —— 实测同一窗口
   alpha=255 / 0 / 128 的截屏像素**完全一致**，也就是说"溶解式换形态"一直是硬切，
   贴边收纳时看着就是闪一下。判断这类问题别读代码猜，**截屏比对**一次就定案。
   同文件的 `_EdgeGlow._blit` 一直是对的，可以当参照。
   另外淡入淡出必须按真实时间 + 跟随刷新率排帧（`after(16)` + 固定帧数在真 mainloop 里
   一半的帧会滑到 31ms，45ms 的淡出只剩 2~3 帧还不均匀）。
7. **贴边悬停的展开/收缩不能用 `<Enter>`/`<Leave>` 判断**，只能用 `GetCursorPos` 轮询。
   展开会改窗口矩形，Windows 在几何变化时会再判一次 `<Leave>`（假离开）。两头都会出事：
   信它 → 收到/展开来回抖（光标静止 2.2 秒换 5 次形态）；丢它 → 假事件被吃掉后，真的移开也不再
   有新事件，展开态永远收不回去。现在的做法：`_hover_update()` 只按真实光标位置判定，
   `_poll_hover()` 每 120ms 自驱巡一次（实测人造卡死态 600ms 内自愈）。
8. **换形态必须交叉溶解，不能"淡出→换尺寸→淡入"**。后者会让浮标先彻底透明 45ms 再淡回来，
   就是"闪一下"。现在 `_redraw_faded()` 把两边的图按贴边锚点对到同一画布、逐帧 `Image.blend`，
   过渡期临时用较大窗口，结束落到目标尺寸（实测上传帧的最小内容不透明度 79，无空帧）。
   ⚠ 入口时 `_peek` 已经是**新**值，渲染旧图要临时翻回去；落定那一步**别**再翻一次（翻了会停在原地）。

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
- **键鼠是两套语义**（用户明确要求）：鼠标/滚轮**任何时候都不响应**（迁移中吃掉；完成态也吃掉 ——
  不让它选中、滚动，更不会凭一次点击收掉锁屏）；键盘只在迁移期间吃掉，完成态放行给
  root 上的"按任意键关闭"。所以完成态 `root.bind` 只挂 `<Key>`，解绑时也只摘它。
  `_吞鼠标` 恒返回 break，`_吞键` 看 `_lock_done` 决定放行还是吃掉。
- **流动边的速度必须按 dt 算**（原来"每帧固定 3px"）。帧率从 33 提到 111 之后，如果还按帧走
  会快 3 倍多；现在是 `速度(px/ms) × dt` + 零头累积，实测稳态仍是 94px/s（= 原设计 3px/32ms）。
  排帧走 `anim.frame_ms`（8ms 地板，125fps 左右）、动画期间 `anim.hold()`，解锁时 `_flow_release_hr()`。
- **红→绿过渡只换图元 image，不重建几何**：原来每帧 `_build_flow_border`（delete + 重建 18 块 +
  新渲瓦片），10 帧 482ms 且会和"迁移总结写进日志"抢主线程；现在 `_flow_set_tiles()` 只发
  itemconfigure，实测 479ms / 整圈重建 0 次，流动动画也不会被打断（它握着 item id，重建就错位）。

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
| 低 | 翻转按钮只接了「开始迁移」一颗；其余按钮要不要跟进（以及 hover 浮起/扫光）未定 |

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
- **探针必须先预置配置**：app.py 用「配置里没有 language 键」判断首次运行，会弹语言选择框
  并**等用户点击**（ask_language 内部 wait_window）。用 MCTOOL_CONFIG 指向临时文件的探针，
  如果只 unlink() 不写入，就会卡在那个框上等人工点击 —— 表现是探针挂到超时，还以为是性能
  问题。正确写法：先 json.dump({language: zh}) 写进临时配置再启动。
- **性能探针要用真实数据形态**：吐司卡片宽度随文案变化，上限 340px。只用短文案（225px）
  测出来的「每帧 1ms、很顺」是假象；真实长文案是 4 倍像素量，9.6ms/帧的合成开销就是这样
  漏掉的。压测至少用满宽度 + 栈满 4 条。

## 六、浮层与真透明（吐司踩过的四个坑）

Tk 控件**没有真透明**：圆角只能靠"四角填父容器底色"假装。这套做法对坐在固定底色上的
控件够用，但**浮层**（吐司、浮标这类）会盖在任意内容上，四角必然露出底色方块。
### 结论：浮层必须用**逐像素 alpha**（UpdateLayeredWindow）

两条路都走不通，别再试：

| 做法 | 结果 |
|---|---|
| 四角填父容器底色 | 盖在别的内容上时四角露出一块底色方块 |
| attributes("-transparentcolor", 键色) | **只有 1 位透明**：抗锯齿的半透明边缘不变透明，而是和键色（近黑）混成脏灰边；把 alpha 二值化消掉脏边，圆角又变成像素台阶 |

最终做法（见 utils/layered.py + ui/toast.py、ui/net_float.py）：内容全部用 **PIL 画**
（PIL 有真抗锯齿），再用 UpdateLayeredWindow 贴到无边框窗口上 —— 8 位 alpha，
既真透明又平滑。代价是 Tk 不能在里面画东西，文字/图标/进度条都得自己在 PIL 里画
（图标用矢量描，别指望字体里有 ✓）。

⚠ **ULW 移窗口后 Tk 不知道新位置**：winfo_rootx/rooty 是旧值，凡是按屏幕坐标做的事
（截图、点击换算）都要用自己记的坐标。

踩过的四个坑（换了 ULW 之后前两个不再出现，但排查思路值得留着）：

1. **主题遍历会把底色刷掉**。apply_theme_to_widget_tree 会把所有 Toplevel/Canvas 的
   底色设成主题色 —— 用键色方案时这会把键色刷没，变成**一块主题色实心方块**（用户报
   "底部一直有一个背景方形"）。浮层窗口现在仍挂 _skip_theme_walk = True。
2. **抗锯齿边会糊出暗边**。半透明像素在透明键色下不变透明，而是和近黑的键色混合 →
   圆角边缘一圈脏灰（用户报"圆角化不彻底"）。这条路走不通，见上面结论。
   ⚠ 还有一条：withdraw→deiconify 之间 Tk 可能**重建窗口**，缓存的 HWND 会让 ULW
   静默失败（窗口在那儿，但一片空白）—— 每次上传前重取 HWND。
3. **动画会被自己的画布裁掉**。整叠共用一张 Canvas 时，画布高度=内容高度，往下滑就超出
   边界被切（用户报"下半截没了"）。画布底部要留出滑动余量（_PADB），定位时再扣掉。
4. **定位别读 winfo_width/height**。geometry("WxH") 异步生效，紧接着读拿到的是旧值
   （实测 325 vs 86），位置会算歪几百像素。自己记住目标尺寸，并且**尺寸+位置一次给全**。

### 小尺寸圆角的坑（不限于浮层）

- rounded_rectangle 的圆角半径会被**夹到宽度的一半**：4px 宽的色条最多只能 2px 圆角，
  看着就是方头。想要贴合别的轮廓就用掩膜求交（ImageChops.multiply），但那样端头会被削
  成月牙。小元素最好让它**自己是一个完整形状**（吐司左侧色条最终做成内缩胶囊）。
- **圆角看不见 ≠ 没画**。深色下卡片底 #262626 贴在窗口底 #1e1e1e 上只差 8/255，圆角的
  过渡肉眼读成直角。先查对比度，再怀疑几何。
- 行宽 800+ 而圆角半径写死 9px 时，弧相对长边太微弱，也读成方角。半径按行高取。


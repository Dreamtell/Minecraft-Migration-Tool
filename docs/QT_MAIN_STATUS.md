# Qt主界面重构(第一阶段骨架) - 运行说明

## 架构决策

- **不复用Tk的12个mixin**:那些mixin本身就是Tk代码(910处 `tk.`/`after()`/`messagebox`),无法搬进Qt
- **复用 core/ 业务层**:已确认 core/ 完全没有import tkinter,可以被任何UI层调用
- **独立顶层进程**(不是Tk主进程的子进程):掌管托盘/配置/单实例锁
- **在进程内直接用现有Qt控件**:qt_big_view/qt_diff_view不需要再开子进程

## 文件列表

```
ui/qt_main/
  __init__.py     # 包入口
  window.py       # Qt主窗口(路径区+单清单+日志+底栏)
  __main__.py     # 独立预览入口
```

## 预览运行(独立模式,不碰Tk版)

```bash
cd "Minecraft迁移工具"
python -m ui.qt_main
```

**前提**:
- PySide6 已装在 `_qt/`:
  ```
  pip install "PySide6==6.11.2" --target _qt
  ```
- 项目根在 `PYTHONPATH`(上面的命令已处理)

**预期结果**:
- 打开一个Qt窗口,显示路径区+模组清单+日志区+开始按钮
- 浏览按钮能选目录、源路径输入时会调 `core.scanner.detect_instance_env` 检测实例
- 添加模组能选jar、文本框可编辑
- 点「开始迁移」会弹提示框(业务逻辑在任务#6接入)
- 关窗口自动保存配置到 `~/.minecraft_migrate_config.json`(和Tk版共用同一个文件)

## 已实现

✅ 核对接口签名(Task #1):
  - `core.scanner.detect_instance_env(root) -> {"mc", "loader", "src"}`
  - `core.scanner.scan_mod_differences(src,tgt,prog_q,total) -> [9元组列表]`
  - `core.migrator.run_migration(..., log_callback, progress_callback)`
  - `utils.config.load_raw_config/save_raw_config`
  - PySide6 加载方式(`_qt/` 加进 sys.path)

✅ 搭建骨架(Task #2):
  - 配置继承Tk版键名(`source`/`target`/`world`/`dry_run`/`mod_list`等)
  - 主题应用(LIGHT_THEME/DARK_THEME的QSS)
  - 路径区+实例检测
  - 单个清单页(模组,可编辑+添加+清空)
  - 日志区(级别配色)
  - 底栏(模拟运行/覆盖开关+开始按钮)
  - 独立预览入口(`__main__.py`)

## 第一阶段已全部完成 ✅

- [x] Task #3: 路径区实时存档检测(saves/存档名 存在性)、环境检测自动触发
- [x] Task #4: 三个清单页(模组/config/其它)+ 页签切换 + 条数徽章
- [x] Task #5: 底栏(模拟/覆盖开关 + 开始迁移 + 查看历史)+ 执行日志级别配色
- [x] Task #6: 打通迁移主流程 —— 新建 `migration.py`:
  - `MigrationWorker`(QObject+moveToThread):后台调 do_backup→prune_legacy_backup→run_migration
  - 跨线程用 **Qt Signal**(log_signal/progress_signal/finished_signal),不是Tk的队列泵
  - `ProgressDialog`:Qt原生进度对话框,可取消(cancel_event)
  - 扫描差异:后台线程调 scan_mod_differences,完成后在进程内直接开 qt_diff_view(不开子进程)
- [x] Task #7: py_compile全通过 + AST级签名核对 + 线程安全审查

## 第6任务的关键技术点(和Tk版的本质区别)

| 方面 | Tk版 | Qt版 |
|---|---|---|
| 后台→主线程 | `root.after` 轮询 `_ui_calls` 队列泵 | `Signal`(AutoConnection自动队列到主线程) |
| 进度反馈 | ProgressWindow(Toplevel) + 队列 | ProgressDialog(QDialog) + 信号 |
| 差异窗口 | 开 `--qt-host` 子进程 + after 泵驱动 | 进程内直接 `show_diff_view`(本身就是Qt进程) |
| 取消 | cancel_event + 队列哨兵 | cancel_event(worker 的 check_cancel 直接读) |

**为什么Qt版不需要子进程**:HANDOFF.md 记录的GIL致命错误根源是"Tk和Qt共享主线程"。
Qt主窗口本身就是纯Qt进程(没有Tk),qt_diff_view/qt_big_view 直接在进程内用即可,
`hooks["defer"]`(回Tk的after)也不需要了。

## 核对过的签名(全部对上)

- `run_migration(src,tgt,world,modlist,configlist,dry_run,overwrite, *, progress_callback,log_callback,check_cancel,extralist,extra_overwrite,record_id,plan)` —— 全用关键字传
- `do_backup(target, *, log_func,extra_entries,record_id,check_cancel)`
- `prune_legacy_backup(target, *, log_func)`
- `scan_mod_differences(src,tgt,progress_queue=None,total=0)` —— 返回 None(缺mods目录)/[]/list
- `progress_callback(index,label,bytes,step文案)` 4参 + 收尾 `(None,None,None)` 3参 —— worker的 `_progress(step=None)` 两种都接住
- `checked_data()` 返回**显示名字符串列表** —— apply_cb 按字符串处理

## 待你在Windows上验证

1. `python -m ui.qt_main` 能开窗(前提:`_qt/` 有 PySide6)
2. 三个清单页切换 + 条数徽章
3. 存档名状态行(✅存在 / ⚠️源中无此存档)
4. 「🔍 扫描差异」能开 qt_diff_view 差异窗口,「应用选中」能把模组加进清单
5. 勾「🧪 模拟运行」点「开始迁移」—— 不动文件,日志里能看到复制计划
6. **谨慎**:取消勾模拟运行是真迁移(会备份+真复制)。先拿两个测试实例试

## 第二阶段(已写完,待用户统一检查)

| 项 | 文件 | 说明 |
|---|---|---|
| 主题切换 + 顶部工具条 | window.py | ⚙️ 设置 / 🌙☀️ 主题,写 `theme` 键 |
| 设置窗 | settings.py | 主题/语言/冲突策略/历史份数/默认开关/启动动画/关闭行为/主界面实现 |
| 历史与回滚 | history.py | list_records + 详情;回滚走 RollbackWorker(QThread)→ do_restore → mark_rollback |
| 托盘 + 关闭行为 | tray.py, window.py | QSystemTrayIcon(不依赖 pywin32);close_action ask/tray/exit;任务中拦关窗 |
| 第二实例唤起 | tray.py | QLocalServer 命名管道 `MinecraftMigrationTool-QtMain` |
| 正式入口 | app_qt.py + app.py | 单实例锁之后、建 Tk() 之前分流;Qt 失败自动回落 Tk |
| Tk 侧切换入口 | mw_settings.py / mw_common.py / main_window.py | 设置 →「🗂 放大查看」→「主界面用哪个实现」 |
| i18n | utils/i18n.py | 新增 `set_language_qt()`:只改内存值,不装 Tk 钩子 |

### 怎么切到 Qt 主界面

- Tk 版:设置 →「🗂 放大查看」→「主界面用哪个实现」选 PySide6 → 重启
- 或临时:`set MCTOOL_MAIN=qt` 再 `python app.py`(`MCTOOL_MAIN=tk` 强制回 Tk)
- 配置键 `main_window_backend`(默认 `tk`);`qt_enabled=false`(纯 Tk 模式)时一律走 Tk

### 检查时重点看

1. `python app.py`(Qt 模式)能起:语言选择(首次)→ 闪屏 → 主窗口 → 托盘图标
2. 关窗:ask 弹三选一;tray 收托盘;exit 直接退;迁移中点 ❌ 只能收托盘/取消
3. 已运行时再启动一次:应把已有窗口叫回来(含藏在托盘时),新进程自己退出
4. Tk 设置里切后端 → 重启 → 进 Qt;Qt 设置里切回 tk → 重启 → 进 Tk
5. 历史窗回滚:**拿测试实例试**,真回滚会改目标目录

### 第三批(已写完,待检查)

| 项 | 位置 | 说明 |
|---|---|---|
| 迁移前自检 | window._start_migration + migration.show_precheck | core.precheck.run_precheck;fail 拦下,warn 问继续 |
| 后台准备 + 磁盘检查 | migration.PrepareWorker | build_migration_plan + backup_size,算法同 Tk 版 _check_disk_space |
| 二次确认 | migration.ask_confirm | 配置键 confirm_migrate(默认开),模拟运行不问 |
| 进度条总数 | _launch_migration | 用计划里的 total_files/total_size(之前固定 1,进度条不准) |
| 放大查看 | window._open_big_view | 进程内 QtBigView;写回只应用增删(_apply_entries_delta),同清单不重复开窗 |
| 拖拽 / 粘贴文件 | ListEdit | 模组页:jar 或整个 mods 文件夹;config/其它文件页:转成相对路径,不在源目录下的跳过 |
| config/其它文件添加按钮 | _add_config_dir/_files、_add_extra_dir/_files、＋ options.txt | 源里没有的条目拒绝加入(同 Tk 版) |
| 扫描/迁移互斥 | _scanning 标志 | 扫描中不能开始迁移,也不能重复扫描 |

检查时补看:
6. 自检:故意填错存档名 → 应弹提醒;源=目标 → 应被拦下
7. 真迁移(非模拟)→ 弹确认框,内容和实际一致;进度条走到 100%
8. 拖一个 mods 文件夹进模组页;拖源 config 下的文件进 config 页
9. 放大查看里删两条 → 主界面清单同步删掉这两条,别的不动

### 第四批(已写完,待检查)

| 项 | 位置 | 说明 |
|---|---|---|
| 开始前倒计时 | window._begin_countdown / _countdown_tick / _cancel_countdown | 配置键 start_countdown(1~10,默认 5,与 Tk 同键);倒计时中再点开始按钮 = 取消 |
| 从变更日志导入 | window._import_changelog | 规则抽到 `core/changelog.py`,Tk 版 `extract_mods_from_changelog` 改为调用它(已用样本逐字比对新旧实现一致) |
| 网络状态 | 状态栏 net_label | 复用 `ui/mw_net.probe()`(纯 TCP 443,不耗 API 配额),5 秒一次,子线程探测 + invokeMethod 回主线程 |
| 设置窗 | settings.py | 新增「正式迁移前再确认一次」(confirm_migrate)和「开始前倒计时」 |
| 忙态 | busy_task_name | 倒计时、回滚也算在跑的任务,托盘退出/关窗会拦 |

检查时补看:
10. 点开始迁移 → 按钮倒数;倒数中再点一次 → 取消,日志说明没动文件
11. 变更日志:粘一段带 `+ xxx.jar` / `- yyy.jar` 的文本 → 问是否带上 Updated → 清单被替换
12. 拔网线 / 断 Wi-Fi → 右下角 5 秒内变红;恢复后变绿
13. **Tk 版**的「从变更日志导入」行为应和以前完全一样(解析函数换了位置)

### 顺手修的 Tk 侧老 bug(HEAD 里就有,不是这次改出来的)

全仓 pyflakes 扫出的「未定义名」,都落在 `except` 分支里 —— 平时不触发,一出异常就
`NameError`,把本该吞掉的异常变成崩溃/弹 Traceback:

| 文件 | 缺的名字 | 修法 |
|---|---|---|
| ui/mw_lists.py(3 处) | trace_exc | 补导入 |
| ui/mw_qt.py | trace_exc | 补导入 |
| ui/mw_pages.py | set_window_icon | 补导入(日志放大窗口建窗时用) |
| ui/mw_settings.py | trf | 补导入(开关「迁移标记」时的吐司) |
| ui/precheck_dialog.py | trace_exc | 补导入 |
| ui/qt_diff_view.py(2 处) | trace_exc | 补导入,并改成传真正的异常对象 |

另外 `utils/helpers.trace_exc` 原来是必填两参:仓库里有 3 处在 except 里**裸调
`trace_exc()`**(mw_pages ×2、rounded_tabs ×1)会再抛 TypeError;还有 8 处第二参传的是
说明文字而不是异常,结果什么都没记下来。现在签名改成 `trace_exc(where="", exc=None)`,
三种调法都能记到当前异常的 traceback(已单测 4 种情形)。

检查时补看:
14. Tk 版正常用一遍(切页签、开设置、开日志放大窗口)—— 这几处只是补导入,行为应不变

### 第五批(已写完,待检查)—— 原「已知未覆盖」清单全部补齐

| 项 | 位置 | 说明 |
|---|---|---|
| 消息中心 | `ui/qt_main/messages.py`(MessageDock)+ window.toast | 状态栏 🔔 + 未读数,Ctrl+M 开关右侧停靠面板;存档与 Tk 版共用 |
| 消息存档抽层 | `utils/message_store.py` | MessageStore 从 ui/message_center.py 抽出(那个模块顶层 import tkinter);Tk 侧原名 re-export,代码逐字未改 |
| 操作反馈 | window.toast(text, level, key) | 文字进状态栏 3 秒 + 记进消息中心;配置键 toast_on 关掉后只记不显示。已接:主题、模拟运行/覆盖开关、添加条目、设置保存、迁移/回滚结果 |
| 定位错误 | window._mark_failed_from_log / _goto_next_fail / _locate_from_log_line | 规则同 Tk 版:拿清单条目名去 ERROR 日志里反查。日志工具条「📍 定位错误 (N)」循环跳;双击错误/警告行直接定位;定位后闪三下 |
| 检查是否存在 | `core/list_check.py` + window._check_list | 三个清单各一颗「🔎 检查是否存在」;存在=绿、缺失=红底、重复=黄底;改动文本后颜色自动清掉。判定逻辑已用临时目录单测(含 `[前缀]` 模组、`..` 越界、大小写重复) |
| 撤销 | window._replace_text | 程序改清单(清空/添加/导入/放大查看写回/差异应用)不再用 setPlainText(那会清掉撤销栈),都能 Ctrl+Z 撤回 |
| 界面按钮 | `ui/qt_main/buttons.py` + 顶栏「🔘 界面按钮」 | 勾选显示、↑↓ 排序、恢复默认;「开始迁移」不许隐藏。和 Tk 版共用 buttons_hidden / button_order,只改 Qt 认识的 key,Tk 独有的按钮(回滚、日志放大查看…)原样保留(合并逻辑已单测) |
| 网络浮标 | `ui/qt_main/net_float.py` | 圆形浮标显示延迟(绿/橙/红),单击开延迟曲线,按住拖动换位置;位置相对主窗口、跟着主窗口走。设置里开关 + 大小(net_float_on / net_float_size / _x / _y,与 Tk 同键)。状态栏网络灯也能点开曲线 |

⚠ 两边共享 `buttons_hidden`:同一个按钮(比如「从变更日志导入」)在 Qt 里藏掉,切回 Tk 也是藏着的。这是有意的。

检查时补看:
15. 迁移出错后「📍 定位错误 (N)」亮起;连点循环跳;双击日志里的红色行能跳到对应条目
16. 三个清单的「🔎 检查是否存在」颜色对得上;改一个字颜色消失
17. 清空 / 导入变更日志之后 Ctrl+Z 能撤回
18. 🔘 界面按钮:藏一个、换个顺序 → 立即生效、重启后还在;切回 Tk 版看 Tk 自己的按钮没被打乱
19. 浮标:开启 → 出现在主窗口右下角;拖到左上 → 移动主窗口时跟着走;重启后位置保持;收进托盘时浮标一起消失
20. Ctrl+M 开消息中心,未读数清零;Tk 版产生的 warn/error 历史在这里也能看到

### 和 Tk 版仍有的差异(刻意简化,不算缺功能)

- 消息中心:Tk 是自绘滑出抽屉;Qt 是可停靠/可浮动的 QDockWidget
- 倒计时:开始按钮上的文字倒数,没有 Tk 版的倒计时动画

### 第六批(已写完,待检查)—— 审查修复 + 补齐原先刻意简化的四项

| 项 | 位置 | 说明 |
|---|---|---|
| 吐司 | `ui/qt_main/toast.py` | 右下角圆角卡片浮层,规则同 Tk 版(最多 4 条、按字数定停留、error 常驻、同 key 原地更新、悬停暂停);`toast_on` 关掉只进消息中心 |
| 3D 翻转开始按钮 | `ui/qt_main/flip_button.py` | 配色/三档手感(`start_btn_feel`)与 Tk 同值;真透视、90° 亮线、模拟运行正面变黄、背面红斜纹 + 真实进度 |
| 浮标贴边收纳 + 边缘特效 | `ui/qt_main/net_float.py` | 离边 8px 松手吸附成 22px 小标签,按真实光标轮询展开/收回;拖动时边缘绿光带 + 颗粒(`net_float_dock` / `float_edge_fx` 现在生效) |
| 逐步撤销 | `window.ListEdit` | 每次内容变化一个快照(上限 200),Ctrl+Z / Ctrl+Y / 右键菜单都走它 |
| Qt 失败回落 | `app.py` | PySide6 已进进程后才失败的,放锁后用 `MCTOOL_MAIN=tk` 另起干净进程,不在同一进程里建 Tk() |
| i18n | `utils/i18n.py` | Qt 主界面与本轮 Tk 侧新文案补齐英文(247 条) |

检查时补看:
21. 开始迁移 → 按钮翻到红色背面、底部进度条跟着走;结束翻回;设置里换手感立刻生效
22. 勾/取消模拟运行 → 按钮正面黄/绿切换,右下角同一张卡片原地换字,不叠新卡
23. 浮标拖到右边缘 → 边上出现绿色光带;松手后移开鼠标收成小标签,移上去展开
24. 清单里连删三行 → Ctrl+Z 一行一行还原
25. 设置里切到 English 重启 → Qt 主界面、设置窗、历史窗都是英文

### 第七批之前补的:和 Tk 版共用的设置 + 四个按钮 + 忙态锁定 + 更新提醒

| 项 | 位置 | 说明 |
|---|---|---|
| 迁移标记 / 后台静默 / 回滚范围记忆 | `window._start_migration`、`migration.py`、`history.pick_rollback_parts` | 与 Tk 同键(`rename_migrated_mods` `rename_marker` `silent_background` `rollback_parts` `rollback_parts_remember`);之前 Qt 版不读,Tk 里设的到 Qt 会被悄悄忽略 |
| 四个按钮 | `window.py` | 「← 填充路径」「📂 放大查看(日志)」「📂 打开日志文件夹」+ 日志写会话文件并脱敏;**主界面没有「回滚」按钮**——Tk 的 cafd9e8 把回滚并进了历史窗口,Qt 保持一致 |
| 忙态锁定 | `window._refresh_busy_state` | 迁移 / 准备 / 扫描 / 倒计时 / 历史窗里的回滚期间:操作按钮置灰、路径框 / 存档名 / 三个清单只读;倒计时里「开始迁移」仍可点(= 取消) |
| 整合包更新提醒 | `ui/qt_main/pack_warning.py` | 红条 + 复制 AI 提示词 + 收起成页签行右端的「⚠ 更新提醒」药丸;`pack_warning_collapsed` 与 Tk 共用,默认收起 |
| 浮标阴影 / 点消息中心外面收回 | `net_float.py`、`messages.py`、设置窗 | `net_float_shadow` / `msg_click_outside_close`,默认值与 Tk 一致 |

**不做(而不是漏了)**:迁移锁屏遮罩——Tk 的 c915ef8 已整体删除(用户:「锁屏这个选择不要了」),`lock_mode` / `lock_wait_key` 只是死配置键。

**本次补的**:主界面编辑开关(`edit_enabled` / `blank_exit_edit`)、按钮音效(`sfx_on` / `sfx_hover` / `sfx_press`)。

| 项 | 位置 | 说明 |
|---|---|---|
| 主界面编辑 | `window._on_edit_switch`、`_lock_edit`/`_unlock_edit`、`eventFilter` | 三个清单默认只读;开关打开才能直接改;放大查看 / 模组差异打开期间强制关闭并锁住(两个都关了才还回用户原来的选择);点窗口空白处只让清单失焦,不碰开关本身(`blank_exit_edit` 控制这条) |
| 按钮音效 | `window._init_sfx`、`_play_press_if_button`、`settings.py` 的试听 | 直接复用 `utils/sfx.py`(纯 ctypes + 线程,不碰 tkinter);应用级事件过滤器拦所有按钮点击播 `press`,勾选框 / 单选不响(同 Tk) |

**不做(范围之外)**:双击间隔(`double_click_*`)——Tk 侧自己也早已停用判定、只为兼容旧配置保留两个键,Qt 主界面原样保存即可,不用再实现。

**后来改主意做了(第八批,见下)**:滚动速度(`scroll_speed`)。原先因为要改 `ui/qt_big_view.py` 的 `_SmoothWheel` 而划在范围外;那个类本来就是 Qt 窗口(放大查看/差异)在用的滚轮缓动,接一个档位旋钮不碰 Tk 主进程。

检查时补看:
26. 迁移 / 倒计时 / 扫描期间,三个清单、路径框、各按钮都点不动;结束后恢复;倒计时里再点「开始迁移」能取消
27. 页签行右端有「⚠ 更新提醒」药丸(默认收起);点开看说明,「展开成横幅」后红条出现在页签上方;重启后形态保持
28. 「复制 AI 提示词」后粘贴,里面是当前的源 / 目标路径
29. 设置里开「点消息中心以外就收回」:点主窗口别处时消息中心收起,点铃铛 / 面板自己不收
30. 「主界面编辑」默认关:三个清单打不进字;开关打开后能改;打开放大查看或模组差异时开关自动变灰并说明原因,关掉那个窗口后自动还原
31. 开着编辑时点清单以外的空白处,只清掉选区和焦点,开关不会被带着关掉
32. 设置里开「按钮音效」,点各处按钮有声音;「试听」按钮能单独听一遍悬停/按下

### 第八批(已写完,待检查)—— Qt 设置窗补齐 Tk 设置窗的几块

| 项 | 位置 | 说明 |
|---|---|---|
| 平滑滚动速度 | `qt_big_view._SmoothWheel`、`settings.py`、`app_qt.py` | `scroll_speed` 1~5 档(3 = 原速),档位表直接用 Tk 版 `SmoothScroller._SPEED_SCALES`;设置里改了立刻生效,启动时读一次 |
| 联网分类 | `settings.py`、`window._apply_settings` | `online_tags`(默认关),显示本地分类缓存条数;改动后提示"重开放大查看生效" |
| CurseForge API Key | `settings.py` | 保存 / 测试 / 清除三个按钮,**点了就生效、不等「确定」**(同 Tk);只写 `~/.minecraft_migrate_secret.json`,不进主配置也不进日志;输入框是密码样式,存完即清空;「测试」在后台线程跑,不卡窗口 |
| 回滚默认还原范围 | `settings.py`、`window._apply_settings` | `rollback_parts` 五个勾选(至少留一个);回滚选择框按它预勾,与历史窗里「记住我的选择」共用同一个键 |

测试:`tools/test_qt_main_parity.py`(滚动速度)、`tools/test_qt_main_settings_parity.py`(其余三项,密钥文件和配置都指到临时目录)。

检查时补看:
33. 设置里调"平滑滚动速度"(1 和 5 各试一次),打开放大查看滚一格,手感有明显差别;确定后不用重启
34. 设置里开"联网获取真实分类",下方缓存条数正确;确定后弹提示;重开放大查看会联网取分类
35. CurseForge Key:粘一个 key 点「保存」→ 输入框清空、状态行只显示末 4 位和指纹;「测试」期间按钮置灰;「清除」后状态变回"未设置";这几步**不点「确定」**也已生效
36. 设置里取消回滚默认还原的前 4 项,最后一项取消不掉;确定后去历史窗点回滚,选择框只勾剩下的那些

### 第九批(已写完,待检查)—— 清单区补齐 Tk 版的两块

| 项 | 位置 | 说明 |
|---|---|---|
| 「＋ 常用目录 ▾」 | `core/extra_presets.py`、`window._open_extra_preset_menu` / `_quick_add_preset` | 「其它文件」页的预设菜单(14 项:光影包、资源包、KubeJS…)。预设表原来在 `ui/mw_common.py`(顶层 import tkinter,Qt 进程不能碰),现抽到 `core/`,Tk 侧 `_EXTRA_PRESETS` 原名 re-export;已在清单里的打 ✅;源里没有的拒绝并提示,重复的只提示;和别的清单编辑一样能 Ctrl+Z |
| 应用迁移记录 | `window._apply_record` / `_apply_last_record` / `_undo_apply_record`、`history.HistoryDialog._apply_current` | 页签行右端新增药丸「🔁 应用上次记录」,点过变「↩ 还原到应用前」(存快照:路径、存档名、三个清单);历史窗新增「🔁 应用到主界面」。**只回填、不开始迁移**;忙的时候(迁移/扫描/倒计时/回滚)拒绝 |

按钮 key `add_extra_preset` 与 Tk 版同名,「界面按钮」里能显示/隐藏、调顺序。
测试:`tools/test_qt_main_lists_parity.py`。

检查时补看:
37. 「其它文件」页点「＋ 常用目录 ▾」:菜单弹在按钮正下方;选一项会写进清单;再打开同一项带 ✅;源里没有的会提示且不加
38. 页签行右端有蓝色药丸「🔁 应用上次记录」(和红色「⚠ 更新提醒」并排);点后路径、存档名、三个清单被换成最近一次记录;药丸变成「↩ 还原到应用前」,再点退回
39. 迁移历史窗选一条点「🔁 应用到主界面」:主界面被回填,历史窗自动关闭;没选中或回滚在跑时按钮是灰的

### 第七批(提案,等主界面重构完成后再决策)—— Tk 设置窗改由 Qt 渲染

**背景**:Tk 设置窗(`ui/mw_settings.py`,约 1600 行)是手搓的一堆控件,和 Qt 设置窗
(`ui/qt_main/settings.py`)两套实现要做同样的键、同样的校验、同样的英文文案,加一个
设置项要改两处。想让它也走 Qt。

**为什么可行**(机制现成):
- `SettingsDialog.values()` 返回的 dict 键名**与 `main_window.save_config()` 完全一致**
  (theme / fx_level / splash / float_edge_fx / start_btn_feel / language /
  extra_conflict / history_limit / dry_run / overwrite / confirm_migrate /
  start_countdown / net_float_on / net_float_size / net_float_dock / toast_on /
  close_action / main_window_backend),主窗拿到就能直接落盘。
- `ui/qt_host.py` 已有子进程协议(请求 JSON + 结果 JSONL + command 通道,现支持
  raise / theme),加一个 `kind: "settings"` 分支即可。

**要做的四步**:
1. 补齐选项:Qt 设置窗目前只覆盖 Tk 设置窗的一部分,缺音效开关与音量、CurseForge Key、
   放大查看默认 backend/视图、差异视图默认、其它文件预设、重命名标记、清空规则等。
2. 一次性模态:主窗 `grab_set()` + `after(60)` 轮询结果文件,收到
   `{"action":"apply","values":{...}}` 再统一 `_apply_values()` + `save_config()`;
   `qt_host` 现在的 bigview/diff 都是常驻非模态,要新加"用完即销毁"的用法。
3. 立即生效的时序:主题 / 浮标开关 / 按钮手感现在点了立刻变,走子进程后是确定之后才变,
   要把 `_apply_values()` 从 `open_settings()` 里拆出来复用。
4. 启动类选项(语言 / 主界面后端 / 闪屏)照旧提示"下次启动生效"。

**两个坑**:
- 冷启动 300~800 ms,点"设置"会愣一下(可先弹占位卡;不值得为它常驻预热进程)。
- 设置窗开着时主窗可能改了配置(迁移中写 history),整包覆盖会冲掉 —— 返回前重读配置合并。

**决策点(先做这个再动手)**:
- 若重构完 `main_window_backend` 默认成 qt,Tk 设置窗迟早退场 → **不做这个重构**,
  把力气花在补全 Qt 设置窗上。
- 若 Tk 要长期留作兜底 → 按上面四步做,估 1.5~2 天,顺序是先补选项、再接 `qt_host`。

**验收**:Tk 主窗点"设置"→ Qt 窗口弹出并置顶;改档位/主题/浮标 → 确定后主窗立刻生效;
取消 → 一个键都不写;关掉设置窗 → 子进程退出(任务管理器里没有残留 python)。

## 和Tk版的配置兼容性

两版读写同一个配置文件(`~/.minecraft_migrate_config.json`),可无缝切换:
- 「特效强度」`fx_level`(off/light/standard/full)一口管启动动画、浮标粒子、按钮手感、
  消息卡片动画;派生值仍写回 `splash` / `float_edge_fx` / `start_btn_feel` 三个老键,
  老配置不受影响。读配置统一走 `utils/fx.py` 的 `from_config()`(没有 `fx_level` 时原样返回)
- Qt版启动时读 `mod_list`/`source`/`target`等,载入到控件
- Qt版关闭时把控件内容写回配置
- Tk版照常读写,互不干扰

## 下一步

Task #3 ~ #7 逐个完成后,Qt骨架就能跑通「选路径→扫描差异→迁移」主流程。
设置窗/按钮自定义/消息中心/网络浮标等在第二阶段补齐(均已补齐,见第六批)。

**第七批是提案,不是排期**:等主界面重构完成、确认 `main_window_backend` 的默认值之后,
再按那节开头的「决策点」决定做不做(默认倾向不做——Qt 若成默认,Tk 设置窗自然退场)。

# 本仓库的智能体说明

产品本体在 `Minecraft迁移工具/`（Tk 主进程 + PySide6 子进程画大视图/图表）；
**主进程绝不 import Qt**。根目录的 `特效2.py`、`特效测试.py`、`特效2*.py` 是原型脚本，不是产品代码。

## 目录速查

- `Minecraft迁移工具/app.py` —— 入口，`python app.py` 起主界面
- `Minecraft迁移工具/core/` —— 迁移、自检、联网搜索（不碰 UI）
- `Minecraft迁移工具/ui/` —— Tk 界面（`mw_*.py` 按功能分模块）；`ui/qt_main/` 是 Qt 主界面
  （含设置/托盘/浮标/历史/翻转按钮/星空滑块），`ui/qt_host.py` 是 Tk 主进程叫起 Qt 窗口的子进程宿主
- `Minecraft迁移工具/utils/` —— i18n、动画、DPI、音效、国旗等基础设施
- `docs/HANDOFF.md` —— 架构、约定、踩坑（最权威，先看它）
- `docs/HANDOFF-flip-button.md` —— 「开始迁移」3D 翻转按钮的交接
- `docs/RELEASE_v5.0.0_body.md` —— 发布正文模板
- `打包_命令行.bat` —— 打包入口（在仓库根，不在产品目录）
- `legacy/` —— v4 之前的**单文件版本**（`Minecraft存档迁移工具.py`，2941 行），只存档、不参与
  构建和测试，**不要改它**；看当时的写法或对外解释历史时可以读（说明见 `legacy/README.md`）

## 换一台电脑要准备什么

1. **克隆仓库**（默认分支 `main` 已含全部代码与测试）：

   ```bash
   git clone https://github.com/Dreamtell/Minecraft-Migration-Tool.git
   ```

2. **Python 3.12**（`python -V` 确认；打包产物也是 3.12 的）。
3. **装依赖**：`pip install -r requirements.txt`（Pillow / tkinterdnd2 / tendo / winotify /
   matplotlib / PyInstaller）。
4. **PySide6**（只在用 Qt 大视图、Qt 主界面时需要）—— 两种放法都支持：

   - **推荐：装进 site-packages**

     ```bash
     pip install "PySide6==6.11.2"      # 本机实测 6.12.0 也能用
     ```

   - **可选：仓库根放一份便携版 `_qt/`**（约 211 MB，**不进 git**）

     ```bash
     pip install "PySide6==6.11.2" --target _qt
     ```

   代码是这么找的：`ui/mw_qt.py` / `ui/qt_big_view.py` 先把 `Minecraft迁移工具/_qt`
   与仓库根 `_qt/` 加进 `sys.path`，再 `find_spec("PySide6")`；都没有就在设置页提示
   “装好 PySide6 后可切到 Qt 版窗口”。所以 **site-packages 里有一份就够**。

   ⚠️ `打包_命令行.bat` 里仍写着 `--paths _qt`：用 site-packages 方案时先跑一次打包确认
   （本机目前没有 `_qt/`，**无 `_qt/` 情况下的打包尚未验证**）。

5. **配置和密钥不随仓库走**，在新机器上要么手动拷、要么重新设置：

   - `%USERPROFILE%\.minecraft_migrate_config.json` —— 主题、语言、各种开关
   - `%USERPROFILE%\.minecraft_migrate_secret.json` —— CurseForge API Key，**永远不要提交**

   直接拷这两个文件最省事；不拷就回到默认设置（语言会重新问一次）。

6. **不在仓库里的东西**（没有也能开发，别去找）：

   | 路径 | 说明 |
   |---|---|
   | `_qt/` | 可选的 PySide6 便携目录（见上） |
   | `_dctest/` | 本地探针脚本与验收截图 |
   | `_probe_shots/` | 渲染/截图输出 |
   | `dist/`、`build/`、`*.spec` | 打包产物 |
   | `.idea/`、`__pycache__/` | IDE 与解释器缓存 |

7. **验证环境**（两条都过就说明这台机器能干活了）：

   ```bash
   cd Minecraft迁移工具 && python app.py      # 窗口能起来
   cd .. && python tools/run_all.py           # 全量测试：应为 23 个文件全过
   ```

## 常用命令

```bash
cd Minecraft迁移工具
python app.py                        # 跑源码
python -m compileall -q .            # 语法自检

cd ..                                # 测试与打包
python tools/run_all.py              # 全量测试：每个测试文件独立进程跑（CI 用这个，退出码可信）
python tools/run_all.py qt_main      # 只跑文件名带 qt_main 的

cmd //c 打包_命令行.bat nopause       # 打包（产物在 dist/app）
```

> `python -m unittest discover -s tools` 仍可用，但**不要拿它当 CI 信号**：
> 所有测试挤在一个进程里时，Qt 窗口/定时器/全局补丁会互相漏，偶发随机失败。
> 配置路径已改成跟随 `MCTOOL_CONFIG` 的代理（`utils/config.py`），跨文件的
> 配置污染没了；剩下的偶发项是 Qt 事件循环状态，靠分进程解决。

## 硬约束（违反过，会出事）

- 产品源码 CRLF；根目录原型脚本与 `docs/*.md` 用 LF；
  `打包_命令行.bat` 必须 UTF-8 无 BOM，否则 cmd 会把中文目录名弄坏
- 控制台是 GBK：跑脚本前设 `PYTHONIOENCODING=utf-8`，别打印 ✓ 之类符号
- 新增配置键必须同时写进 `ui/main_window.py` 的 `save_config()`，
  否则用户下次改任何设置都会把它抹掉；且要在 `load_config()` 之后**立刻**读，
  晚读会被别处的 `save_config()` 用默认值写回
- 中文是安装态零成本路径：新增文案只改 `utils/i18n.py`，拼接串走 `trf`/`trp`
- 探针必须先预置配置（`MCTOOL_CONFIG` 指向写好的临时配置），
  否则会弹语言选择框等人工点击而挂到超时
- `taskkill //F //IM python.exe` 会连测试进程一起杀掉
- 打包前必须先 `taskkill /F /IM app.exe` 再删 `dist/app`，
  否则删不掉 `base_library.zip`（WinError 32）
- `app.py` 在**模块导入期**就把临时目录钉死（`_准备干净临时目录()`）：
  `tempfile` 在 `%TEMP%` 不可用时会悄悄退回当前目录，锁和 `tmp*` 目录就会撒进程序目录。
  改这段务必保证 `Path` 等名字都已导入——上一版就是漏了 `from pathlib import Path`，
  `NameError` 被外层 `try/except` 吞掉，加固等于没做

## 多个 agent 协作改代码（分工 + 接力）

这个项目的常态是**几个 agent 各管一块**（一位管 Qt、一位管 Tk），按目录分工、前后接力；
真正"两个人同时改同一份文件"是例外。但只要改动有重叠，**文件系统没有锁**：`write` 是
整文件覆盖，`edit` 是按原文替换，谁后写谁生效，没有任何冲突检测。所以下面的规矩是纪律，
不是建议。

### 接力（最容易出事的地方）

- **交接前（上一棒）**：跑 `python tools/run_all.py` 通过 → 把改动**提交**（别留半成品在
  工作区）→ 在 `docs/HANDOFF.md` / `docs/QT_MAIN_STATUS.md` 写清"做到哪、下一步、已知坑"。
- **接手时（下一棒）**：`git status --short` 应当是**干净**的；`git log --oneline -5` 看
  上一棒改了什么；先读交接文档再动手。看到 `M` 文件但没人说在改 → **先问**，别猜着改。
- **写作用域按目录分**：
  - Qt 侧：`ui/qt_main/`、`ui/qt_big_view.py`、`ui/qt_win.py`、`ui/qt_host.py`
  - Tk 侧：`ui/mw_*.py`、`ui/main_window.py`、`ui/dialogs.py`
  - 公共层：`core/`、`utils/`、`tools/`、`docs/` —— 两边都要碰，改之前先打招呼
- **跨界改动**（例如 Qt 侧要读 Tk 侧新增的配置键）：只加不删，改完在 HANDOFF 记一句，
  让下一个人知道这个键两边都在用。

### 三种结局

1. **改了不同区域 → 两个人都"活下来"**（最常见）：看着像合并成功，但可能有语义冲突
   （A 把函数改成返回 `None`，B 的新代码还在用返回值）。两边各自测试都过，合起来是坏的。
2. **改了同一段 → 后写的静默覆盖前者**（最危险）：没有报错，测试没覆盖到就没人发现，
   过几天才发现"我明明改过"。
3. **锚点文字已经不存在 → `edit` 直接报错**：这反而是好事，至少你知道撞了；
   别用 `write` 整文件重写去"绕过冲突"，那会把结局 3 变成结局 2。

### git 能挡什么、挡不住什么

- 挡得住：`merge` / `rebase` / `cherry-pick` 时的行级冲突。
- 挡不住：两个 agent 在**同一个分支**各自 commit——不会冲突，只是历史里一前一后。
- **危险的是回滚类命令**：`git checkout -- .`、`git reset --hard`、`git stash` 会把
  **别人未提交的改动一起清掉**。要回退就逐文件还原，或 `git stash push -- <自己的文件>`。

### 规矩

1. **开工前先 `git status --short`**：看到别人正在改的文件（`M` 状态）就别动它，
   换文件、先做别的、或者去问。
2. **一个文件一个写者**。DSH 的 task `write_scopes` 只是声明，**不是锁**，别指望它拦冲突。
3. 非改同一个文件不可：一个人写，另一个人给 diff/patch 由写者应用。
4. 优先小范围 `edit` + **唯一锚点**，改完立刻 `git diff <file>` 复查；避免 `write` 全量覆盖。
5. 提交时 `git add -p` 只挑自己那几段，别把别人未完成的改动一起提交。
6. **小步、勤提交**，缩短"未提交窗口"；合并后跑 `python tools/run_all.py`（按文件分进程，
   退出码可信）。
7. 发现别人动了自己正在改的文件：**先停、先问**，不要硬写；把冲突点记进交接说明。

### 真实案例（本仓库）

- `ui/qt_main/window.py` 在我准备改它的时候被别人占了——我放弃那处改写、把功能做进别的文件，
  没有强写。
- 并行期（不是接力期）某次 `git status` 同时有 12 个 `M` 文件是另一个 agent 的产物；
  `test_qt_main_edit_sfx`
  单跑 26 全过、全量跑挂，最后靠 `tools/run_all.py` 分进程隔离才稳定。
- 用户要求"回退到某次修复之前"时，我是**逐文件手工还原**的，没有用 `git reset --hard`，
  否则另一个 agent 未提交的改动会被一起清掉。

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

1. **Python 3.12**（`python -V` 确认；低版本没有 `pathlib` 之外的一些新写法不用，但打包产物是 3.12 的）。
2. 装依赖：`pip install -r requirements.txt`（Pillow / PyInstaller / tkinterdnd2）。
3. **PySide6 不在 site-packages，而在仓库根的 `_qt/` 目录**（约 211 MB，**不进 git**）。
   新机器上要自己放进去，否则大视图/图表起不来：

   ```
   pip install "PySide6==6.11.2" --target _qt
   ```

   `打包_命令行.bat` 靠 `--paths _qt` 找它；直接 `python app.py` 跑源码时也从这里导入。
4. 配置和密钥**不在仓库里**，在用户目录（换机不会跟着走，需重新设置）：
   - `~/.minecraft_migrate_config.json` —— 主题、语言、各种开关
   - `~/.minecraft_migrate_secret.json` —— CurseForge API Key，**永远不要提交**
5. `_dctest/`（探针脚本）是本地临时目录，不进仓库；新机器上没有属正常，需要时重写。

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

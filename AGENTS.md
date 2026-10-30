# 本仓库的智能体说明

产品本体在 `Minecraft迁移工具/`（Tk 主进程 + PySide6 子进程画大视图/图表）；
**主进程绝不 import Qt**。根目录的 `特效2.py`、`特效测试.py` 是原型脚本，不是产品代码。

## 先读这两份

- `docs/HANDOFF.md` —— 架构、约定、踩坑、待办（最权威，先看它）
- `docs/HANDOFF-flip-button.md` —— 「开始迁移」3D 翻转按钮的交接（2026-10-30，最近的改动）

## 硬约束（违反过，会出事）

- 产品源码 CRLF；根目录原型脚本与 `docs/*.md` 用 LF
- 控制台是 GBK：跑脚本前 `export PYTHONIOENCODING=utf-8`，别打印 ✓ 之类符号
- 新增配置键必须同时写进 `ui/main_window.py` 的 `save_config()`，
  否则用户下次改任何设置都会把它抹掉
- 中文是安装态零成本路径：新增文案只改 `utils/i18n.py`，拼接串走 `trf`/`trp`
- 探针必须先预置配置（`MCTOOL_CONFIG` 指向写好的临时配置），
  否则会弹语言选择框等人工点击而挂到超时
- `taskkill //F //IM python.exe` 会连测试进程一起杀掉

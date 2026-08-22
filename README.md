# 🧳 Minecraft 存档迁移工具

[![GitHub license](https://img.shields.io/github/license/Dreamtell/Minecraft-Migration-Tool)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/Dreamtell/Minecraft-Migration-Tool)](https://github.com/Dreamtell/Minecraft-Migration-Tool/releases)
[![GitHub issues](https://img.shields.io/github/issues/Dreamtell/Minecraft-Migration-Tool)](https://github.com/Dreamtell/Minecraft-Migration-Tool/issues)
[![GitHub stars](https://img.shields.io/github/stars/Dreamtell/Minecraft-Migration-Tool)](https://github.com/Dreamtell/Minecraft-Migration-Tool/stargazers)
[![GitHub downloads](https://img.shields.io/github/downloads/Dreamtell/Minecraft-Migration-Tool/total)](https://github.com/Dreamtell/Minecraft-Migration-Tool/releases)

**版本：v3.4** · 开源 · 免费 · 纯中文界面

> 一款用于在不同 Minecraft 整合包实例之间迁移 **模组、配置文件、存档和设置** 的图形化工具。  
> 由 Dreamtell 与 AI DeepSeek 协作完成，源码完全开源。

---

## ✨ 功能特性

### 核心功能
- 🚀 **一键迁移**：从旧版整合包迁移到新版，保留你的自定义配置和存档
- 🧩 **智能模组差异扫描**：基于 `modId` 和版本号识别差异，支持 Fabric 与 Forge
- 📁 **灵活清单管理**：手动编辑或自动导入模组和 config 清单
- 🔄 **备份与回滚**：迁移前自动备份目标实例，随时恢复到之前状态
- 📜 **迁移历史记录**：记录每次迁移详情，支持查看和回滚标记

### 交互增强
- 🌓 **深色/浅色主题**：随心切换，保护眼睛
- 🧪 **模拟运行**：预览操作效果，避免误操作
- 🔒 **路径安全检查**：防止 config 清单中的 `..` 路径越界
- 📂 **放大查看窗口**：支持搜索、编辑和同步，方便管理长清单
- 🖱️ **双击查看详情**：在差异列表中双击任意模组，查看完整元数据（作者、描述、依赖等）
- 🎯 **按状态批量选择**：一键全选“新增”、“更新”或“目标独有”的模组
- 📊 **智能排序**：差异列表支持按文件名、状态、类型、Mod ID、版本、大小排序

### 系统集成
- 🔔 **Windows 系统通知**：程序启动时右下角弹出通知，提示正在加载
- ⚡ **快速启动**：移除 Pygame 依赖，启动速度提升 50%
- 📦 **轻量体积**：打包体积约 30MB（相比旧版减少 50%）

---

## 📦 下载与安装

### 方法一：使用预编译可执行文件（推荐）
- 前往 [Releases](https://github.com/Dreamtell/Minecraft-Migration-Tool/releases) 下载最新 `.exe` 文件
- 双击运行即可，无需安装 Python 或任何依赖

### 方法二：从源码运行

1. 克隆仓库：
   ```bash
   git clone https://github.com/Dreamtell/Minecraft-Migration-Tool.git
   cd Minecraft-Migration-Tool

2. 安装依赖：
   ```bash
   pip install tendo winotify
   ```

3. 运行：
   ```bash
   python app.py
   ```

> **提示**：`winotify` 用于 Windows 系统通知，如果不需要可移除相关代码。

---

## 🎮 使用指南

### 基本流程

1. **选择源实例根目录**  
   旧版整合包，例如 `D:\.minecraft\versions\1.20.1-old`

2. **选择目标实例根目录**  
   新版整合包，例如 `D:\.minecraft\versions\1.21-new`

3. **填写存档名称**  
   要迁移的存档文件夹名，例如 `我的生存世界`

4. **编辑模组清单**  
   每行一个 `.jar` 文件名，可通过“扫描模组差异”快速生成

5. **编辑 Config 清单**  
   每行一个相对路径，指向 `config` 目录下的文件或文件夹

6. **点击 🚀 开始迁移**

### 高级功能

| 功能 | 说明 |
|------|------|
| 模拟运行 | 勾选后仅显示操作，不实际修改文件 |
| 覆盖已存在的模组 | 勾选后若目标已有同名模组，则覆盖 |
| 扫描模组差异 | 自动比对源和目标 `mods` 目录，一键导入清单 |
| 按状态全选 | 在差异窗口中一键全选“新增/更新/目标独有” |
| 查看模组详情 | 双击差异列表中的任意行，查看完整元数据 |
| 回滚 | 迁移后若出问题，可一键恢复到迁移前的状态 |
| 查看历史 | 查看该目标实例的所有迁移记录 |
| 放大查看 | 大窗口查看/编辑清单，支持搜索和同步 |

---

## 🖥️ 界面预览

![主界面截图](screenshot.png)

---

## 🛠️ 开发与构建

### 项目结构（v3.4+）

```
Minecraft-Migration-Tool/
├── app.py                      # 🚀 程序入口
├── icon.ico                    # 程序图标
├── README.md                   # 项目说明
├── LICENSE                     # MIT 协议
├── utils/                      # 🧰 工具模块
│   ├── __init__.py
│   ├── config.py               # 配置文件路径
│   ├── theme.py                # 主题颜色
│   └── helpers.py              # 通用辅助函数
├── core/                       # ⚙️ 核心逻辑
│   ├── __init__.py
│   ├── migrator.py             # 迁移、备份、回滚
│   └── scanner.py              # 模组扫描、元数据解析
└── ui/                         # 🖥️ 界面模块
    ├── __init__.py
    ├── main_window.py          # 主窗口
    ├── diff_window.py          # 差异列表窗口
    └── dialogs.py              # 进度窗口、详情弹窗
```

### 使用 PyInstaller 打包为 EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --add-data "icon.ico;." --name "Minecraft迁移工具" --hidden-import winotify app.py
```

或使用 `auto-py-to-exe` 图形界面打包（推荐新手）。

### 依赖说明

| 依赖 | 用途 | 是否必需 |
|------|------|----------|
| `tkinter` | 图形界面 | ✅ 必需（Python 自带） |
| `tendo` | 单实例控制 | ✅ 必需 |
| `winotify` | Windows 系统通知 | ✅ 必需（建议） |

---

## 🤝 贡献指南

欢迎提交 Issue 或 Pull Request！

- 请确保代码符合 [PEP 8](https://peps.python.org/pep-0008/) 风格
- 若新增功能，请附带说明文档
- 提交前请测试功能是否正常

---

## 📝 更新日志

### v3.4 (2026-08-23)

**🏗️ 重大重构**
- 项目拆分为模块化结构：`app.py` + `utils/` + `core/` + `ui/`
- 移除 Pygame 启动动画，改用 Tkinter + Windows 系统通知
- 代码量从 2600 行单文件拆分为 8 个模块文件

**✨ 新增功能**
- 差异窗口支持多字段排序（文件名、状态、类型、Mod ID、版本、大小）
- 状态排序按“新增 → 更新 → 目标独有”聚合
- 按状态批量选择（一键全选新增/更新/目标独有）
- 双击差异行查看模组完整详情（作者、描述、依赖等）
- Windows 系统通知提示程序启动（使用 winotify）

**🐛 Bug 修复**
- 修复主窗口启动时从左上角移动到居中的闪烁问题
- 修复“放大查看”按钮高亮误判（空内容不再高亮）
- 修复差异窗口排序时数据索引错乱导致状态混杂的问题
- 修复 config 清单按钮在某些主题下不可见的问题

**🎨 UI 优化**
- 主窗口直接居中显示，无过渡动画
- 按钮风格统一、对齐整齐
- 所有子窗口图标统一

**📦 打包优化**
- 打包体积从 ~60MB 减少到 ~30MB
- 启动速度提升约 50%

---

### v3.3 (2026-07-31)

**🐛 Bug 修复**
- 修复启动画面关闭后主窗口不创建的问题
- 修复进度轮询内存泄漏
- 修复存档检测指向目标而非源的问题
- 修复放大查看窗口不居中及编辑切换重置内容的问题
- 修复 config 清单中 `..` 路径越界的安全隐患

**✨ 新增与优化**
- 开始迁移按钮采用亮绿色渐变，更醒目
- 存档检测状态颜色：存在→绿色，不存在→红色
- 放大窗口取消编辑时保留修改内容
- 日志缓存自动清理（阈值 500 条）
- 备份失败时迁移自动取消

---

### v3.0 (初始版本)
- 基础迁移功能（模组、config、存档、options.txt）
- 模组差异扫描（元数据级）
- 备份与回滚
- 迁移历史记录
- 深色/浅色主题切换

---

## ⚠️ 免责声明与安全提醒

- 本工具完全**免费**，仅供学习交流使用
- 严禁用于**商业用途或转卖**。如遇收费行为，请立即举报
- 使用本工具造成的任何数据损失，作者概不负责
- **建议在操作前手动备份重要数据**

---

## 📧 联系与支持

- **作者**：Dreamtell
- **GitHub**：[Dreamtell](https://github.com/Dreamtell)
- **开源协议**：MIT License
- **问题反馈**：[Issues](https://github.com/Dreamtell/Minecraft-Migration-Tool/issues)

---

**如果觉得有用，请给个 ⭐ Star 支持一下！**

> 本工具由 Dreamtell 与 AI DeepSeek 协作完成，源码完全开源，欢迎 fork 和改进。
# 🧳 Minecraft 存档迁移工具

[![GitHub license](https://img.shields.io/github/license/Dreamtell/Minecraft-Migration-Tool)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/Dreamtell/Minecraft-Migration-Tool)](https://github.com/Dreamtell/Minecraft-Migration-Tool/releases)
[![GitHub issues](https://img.shields.io/github/issues/Dreamtell/Minecraft-Migration-Tool)](https://github.com/Dreamtell/Minecraft-Migration-Tool/issues)
[![GitHub stars](https://img.shields.io/github/stars/Dreamtell/Minecraft-Migration-Tool)](https://github.com/Dreamtell/Minecraft-Migration-Tool/stargazers)

**版本：v3.3** · 开源 · 免费 · 纯中文界面

> 一款用于在不同 Minecraft 整合包实例之间迁移 **模组、配置文件、存档和设置** 的图形化工具。  
> 由 Dreamtell 与 AI DeepSeek 协作完成，源码完全开源。

---

## ✨ 功能特性

- 🚀 **一键迁移**：从旧版整合包迁移到新版，保留你的自定义配置和存档
- 🧩 **智能模组差异扫描**：基于 `modId` 和版本号识别差异，支持 Fabric 与 Forge
- 📁 **灵活清单管理**：手动编辑或自动导入模组和 config 清单
- 🔄 **备份与回滚**：迁移前自动备份目标实例，随时恢复到之前状态
- 📜 **迁移历史记录**：记录每次迁移详情，支持查看和回滚标记
- 🌓 **深色/浅色主题**：随心切换，保护眼睛
- 🧪 **模拟运行**：预览操作效果，避免误操作
- 🔒 **路径安全检查**：防止 config 清单中的 `..` 路径越界
- 🖥️ **放大查看窗口**：支持搜索、编辑和同步，方便管理长清单

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
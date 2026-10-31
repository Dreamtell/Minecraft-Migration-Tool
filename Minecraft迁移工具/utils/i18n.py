# utils/i18n.py
"""界面语言（中文 / English）—— 一层"查得到就翻、查不到原样返回"的兜底翻译。

## 为什么这么做，而不是把两千多处中文逐个改成 `tr("...")`

1. 这个程序的文案**最终都会流经几个固定入口**：Tk 控件构造器（`tk.Label(text=...)` 之类）、
   `tkinter.messagebox`、以及 `utils/helpers.py` 里那几个自绘控件工厂。在这些入口上包一层、
   查同一张表就够了 —— 不用改两千多处调用点，以后新写的文案也会自动被翻到。
2. `_dctest` 里一百多个验证脚本是**断言中文文案**的（按钮文字、页签名、段选标签…）。
   默认语言是中文时这层包装**根本不安装**，行为与以前逐字节一致，脚本一条都不用改。
3. 词条可以一条条补：查不到就返回原文（中文），永远不会出现"翻了一半、界面炸了"。
   找缺口的回路：`_dctest/收集未翻译文案.py`（把界面控件树走一遍，列出还没翻的中文）。

## 语言从哪来、什么时候生效

优先环境变量 `MCTOOL_LANG`（`zh` / `en`，给验证脚本和临时试用用），其次配置文件里的
`language` 键（设置 →「🎨 外观与启动」→ 界面语言）。**语言在启动时定下来**：界面是一次性
建好的，运行中切换要重启程序才看得到效果（设置里会提示）。

## 已知覆盖不到的地方（README 里也写了）

- **f-string 等运行时拼出来的文案**：字典对不上模板。这类要显式写
  `tr("共 {n} 项").format(n=n)`，目前只处理了主界面上最显眼的几处；
- **执行日志正文**：诊断用，量大且几乎都带动态值；
- **Qt 子进程窗口**（放大查看 / 模组差异）：那边是 C++ 控件，没法在构造器上包装，
  要翻得逐个调用点改，属于下一批。
"""
import weakref
import os
import tkinter as tk
from tkinter import messagebox, ttk

ZH, EN = "zh", "en"
ENV_LANG = "MCTOOL_LANG"
LANG_KEY = "language"
# 设置页展示用：(值, 显示名)
CHOICES = ((ZH, "中文"), (EN, "English"))

_语言 = None            # 缓存：读一次环境变量/配置就定下来
_激活 = False           # 翻译是否生效（没激活时 tr() 原样返回）
_已装 = False           # Tk 那层包装装没装
_已装Qt = False         # Qt 那层包装装没装
_原件 = []              # [(对象, 属性名, 原函数)]，卸载时还原

# 中文原文 -> English。**只放"会进控件"的静态文案**；
# 动态拼接的（f-string）在这里对不上，要改调用点，见模块顶部说明。
表 = {
    # ---------- 主界面：路径区 ----------
    "旧版整合包（要迁移出去的源）": "Source instance (the old pack)",
    "新版整合包（迁移目的地）": "Target instance (the new pack)",
    "浏览…": "Browse…",
    "存档文件夹名称": "Save folder name",
    "（例如：新的世界）": "(e.g. New World)",
    "✅ 存档已存在": "✅ Save exists",
    "❌ 存档不存在": "❌ Save not found",
    "⚠ 路径含中文": "⚠ Path contains non-ASCII characters",
    "✅ 有效": "✅ Valid",
    "❌ 无效": "❌ Invalid",
    "⚠ 路径无效": "⚠ Invalid path",

    # ---------- 主界面：清单区 ----------
    "清单（三个页共用一个区，点标签切换）": "Lists (the three tabs share one area — click a tab)",
    "主界面编辑": "Edit lists",
    "直接改动清单文字 · 谨慎使用": "Edit the list text directly — use with care",
    "已开启": "ON",
    "未开启": "OFF",
    # 「快捷链接」那一排（自绘按钮）
    "🐙 GitHub 仓库": "🐙 GitHub repository",
    "🌐 Minecraft 官网": "🌐 Minecraft website",
    # 没配 CurseForge key 时设置页那行状态（走 utils/secrets.status_text）
    "未设置 —— 联网搜索仍然走 Modrinth，功能不受影响":
        "Not set — online search still goes through Modrinth; nothing else is affected",
    "🧩 模组清单": "🧩 Mods",
    "⚙️ config 清单": "⚙️ Config",
    "📦 其它文件": "📦 Other files",
    "🧩 模组清单 {n}": "🧩 Mods {n}",
    "⚙️ config 清单 {n}": "⚙️ Config {n}",
    "📦 其它文件 {n}": "📦 Other files {n}",
    "【免费声明】本工具完全免费，严禁用于商业用途或转卖。":
        "[Free of charge] This tool is completely free; selling or commercial use is prohibited.",
    "如有任何收费行为，请立即举报。作者不会以任何形式向你收费。":
        "If anyone charges you for it, report it immediately — the author never asks you to pay.",
    "从变更日志导入（含 Updated）": "Import from changelog (Updated)",
    "放大查看": "Big view",
    "添加模组": "Add mods",
    "扫描模组差异": "Scan mod diff",
    "清空清单": "Clear list",
    "检查清单模组是否存在（源目录）": "Check mods against source",

    # ---------- 主界面：底栏与日志 ----------
    "模拟运行（仅显示操作）": "Dry run (preview only)",
    "覆盖已存在的模组": "Overwrite existing mods",
    "查看历史": "History",
    "回滚": "Roll back",
    "🔒 迁移期间主界面按钮全部禁用（锁屏遮罩已移除）": "🔒 Buttons are disabled during migration (the lock overlay was removed)",
    "🧹 已清理旧版留下的单一备份（{0} 项）":
        "🧹 Removed the old single backup ({0} items)",
    "⚠️ 旧的单一备份没清掉：{0}":
        "⚠️ Could not remove the old single backup: {0}",
    "旧版目录（源）": "Source folder",
    "新版目录（目标）": "Target folder",
    "{0}不存在：{1}": "{0} does not exist: {1}",
    "{0}不是文件夹：{1}": "{0} is not a folder: {1}",
    "旧版目录（源）读不了：{0}": "Cannot read the source folder: {0}",
    "新版目录（目标）写不进去：{0}": "Cannot write into the target folder: {0}",
    "源目录和目标目录是同一个：{0}": "Source and target are the same folder: {0}",
    "目标目录在源目录里面（会把源自己复制进去）": "The target folder is inside the source (it would copy the source into itself)",
    "源目录在目标目录里面（目标里的旧文件会被当成源）": "The source folder is inside the target (old files there would be treated as source)",
    "{0}：{1} / {2}": "{0}: {1} / {2}",
    "没有填存档名称，存档不会被迁移": "No world name given — the world will not be migrated",
    "源整合包里没有存档「{0}」，目标里已经有了，这个存档不会被改动": "The source pack has no save \"{0}\"; the target already has one, so it is left untouched",
    "源和目标里都没有存档「{0}」，这次不会迁移存档": "Neither side has a save named \"{0}\", so no save will be migrated",
    "源目录里没有 mods 文件夹，清单里的 {0} 个模组都复制不了": "There is no mods folder in the source — none of the {0} listed mods can be copied",
    "读不了源目录的 mods 文件夹：{0}": "Cannot read the mods folder of the source: {0}",
    "清单里有 {0} 个模组在源目录找不到：{1}": "{0} listed mods were not found in the source: {1}",
    "清单里有 {0} 个重复条目（不会重复复制）": "{0} duplicate entries in the list (they will not be copied twice)",
    "源目录里还有 {0} 个 jar 不在清单里，不会被复制（{1}）": "{0} more jars in the source are not in the list and will not be copied ({1})",
    "{0} 等 {1} 个": "{0} and {1} more",
    "config 清单里有 {0} 条在源 config 目录找不到：{1}": "config list: {0} entries not found in the source: {1}",
    "其它文件清单里有 {0} 条在源目录找不到：{1}": "Other files list: {0} entries not found in the source: {1}",
    "目标里已有 {0} 处同名内容，会按「覆盖」处理（覆盖前会先备份）": "{0} items already exist in the target and will be overwritten (backed up first)",
    "目标里已有 {0} 处同名内容，当前是「跳过已存在」，这些不会被覆盖": "{0} items already exist in the target and will be skipped (overwrite is off)",
    "🔎 开始迁移前自检…": "🔎 Running the pre-migration check…",
    "⚠️ 自检没跑完（{0}），已跳过自检继续": "⚠️ The pre-migration check did not finish ({0}); continuing without it",
    "⛔ 自检没通过（{0} 项错误），已拦下，没有动任何文件": "⛔ The pre-migration check failed ({0} errors) — stopped, nothing was touched",
    "迁移前自检没通过": "Pre-migration check failed",
    "下面这些不解决，迁移跑不起来：": "Fix these, otherwise the migration cannot run:",
    "⚠️ 自检有 {0} 项提醒，等用户确认": "⚠️ The pre-migration check raised {0} warnings; waiting for confirmation",
    "ℹ️ 你选择了「先不开始」，这次没有迁移任何文件": "ℹ️ You chose “Not now” — nothing was migrated",
    "❌ 用户取消了回滚操作": "❌ Roll back cancelled by user",
    "ℹ️ 已取消：确认框里点了「取消」，没有动任何文件。": "ℹ️ Cancelled at the confirm dialog — nothing was touched.",
    "迁移前自检有提醒": "Pre-migration check has warnings",
    "这些不影响运行，但结果可能不是你要的：": "These will not block the run, but the result may not be what you expect:",
    "还是要现在开始吗？": "Start anyway?",
    "知道了": "Got it",
    "先不开始": "Not now",
    "⏳ 等你确认…": "⏳ Waiting for you…",
    "✅ 自检通过（{0} 项提示）": "✅ Pre-migration check passed ({0} notes)",
    "迁移": "Migration",
    "迁移准备": "Preparing migration",
    "开始迁移": "Start migration",
    "执行日志": "Log",
    "双击日志行可定位到清单里的那条": "Double-click a log line to locate it in the lists",
    "定位错误": "Locate errors",
    "打开日志文件夹": "Open log folder",
    "清空日志": "Clear log",
    "本工具完全免费，仅供个人学习交流使用。严禁倒卖或用于商业目的。":
        "This tool is completely free, for personal use only. Reselling or commercial use is prohibited.",

    # ---------- 设置：分区与页面 ----------
    "🎨 外观与启动": "🎨 Appearance",
    "🚚 迁移与分类": "🚚 Migration",
    "🗂 放大查看": "🗂 Big view",
    "🔘 界面按钮": "🔘 Buttons",
    "🚪 后台与退出": "🚪 Closing",
    "界面语言": "Language",
    "界面语言：": "Language:",
    "主题：": "Theme:",
    # 「开始迁移」按钮翻转手感（设置 →「🎨 外观与启动」）
    "开始迁移按钮手感：": "Start button feel:",
    "干脆": "Crisp",
    "标准": "Standard",
    "沉": "Heavy",
    "浅色": "Light",
    "深色": "Dark",
    "语言在下次启动程序时生效。": "Takes effect the next time you start the app.",
    "功能与行为": "Behaviour",
    "快速链接": "Links",
    "关闭": "Close",

    # ---------- 设置：常见开关 ----------
    "启动动画": "Splash screen",
    "迁移前把窗口收进托盘": "Hide the window while migrating",
    "复制过去的模组加标记前缀（方便在目标 mods 里一眼认出）":
        "Prefix migrated mods (easy to spot in the target mods folder)",
    "正式迁移前再确认一次": "Confirm before the real migration",
    "联网获取真实分类（Modrinth；默认关闭）":
        "Fetch real categories online (Modrinth; off by default)",
    "CurseForge API Key（可选）": "CurseForge API key (optional)",
    "保存": "Save",
    "测试": "Test",
    "清除": "Clear",

    # ---------- 弹窗 / 提示 ----------
    "提示": "Notice",
    "错误": "Error",
    "警告": "Warning",
    "确定": "OK",
    "取消": "Cancel",
    "是": "Yes",
    "否": "No",
    "关闭窗口": "Close window",

    # ---------- 主界面：自绘按钮（文字画在 Canvas 上，靠 get_text() 收集） ----------
    "🚀 开始迁移": "🚀 Start migration",
    # 「开始迁移」按钮翻面后的文案（见 ui/flip_button.py）
    "🚀 模拟运行": "🚀 Dry run",
    "⏳ 迁移中...": "⏳ Migrating...",
    "⏳ 模拟中...": "⏳ Simulating...",
    "⚠️ 回滚": "⚠️ Roll back",
    "📋 查看历史": "📋 History",
    "📂 放大查看": "📂 Big view",
    "➕ 添加模组": "➕ Add mods",
    "🔍 扫描模组差异": "🔍 Scan mod diff",
    "🗑️ 清空清单": "🗑️ Clear list",
    "🗑️ 清空 config 清单": "🗑️ Clear config list",
    "🗑️ 清空其它文件清单": "🗑️ Clear \"Other files\"",
    "🗑️ 清空日志": "🗑️ Clear log",
    "📂 打开日志文件夹": "📂 Log folder",
    "📍 定位错误": "📍 Locate",
    "📥 从变更日志导入（含Updated）": "📥 Import from changelog",
    "🔎 检查清单模组是否存在（源目录）": "🔎 Check mods",
    "🔎 检查 config 是否存在（源目录）": "🔎 Check config",
    "🔎 检查是否存在（源目录）": "🔎 Check existence",
    "📂 浏览...": "📂 Browse…",
    "📄 浏览添加文件": "📄 Add file…",
    "📁 浏览添加文件夹": "📁 Add folder…",
    "＋ 常用目录 ▾": "＋ Common folders ▾",
    "🗂 卡片视图": "🗂 Card view",
    "📋 列表视图": "📋 List view",
    "📋 表格视图": "📋 Table view",
    "显示 / 隐藏": "Show / hide",
    "↑ 上移": "↑ Move up",
    "↓ 下移": "↓ Move down",
    "恢复默认": "Reset to default",
    "【新】": "[NEW]",
    "⬇ 新版整合包（迁移目的地）": "⬇ Target instance (the new pack)",
    "📤 旧版整合包（要迁移出去的源）": "📤 Source instance (the old pack)",

    # ---------- 设置页：自绘开关 / 分段选择 ----------
    "启用启动动画（下次启动程序生效）": "Enable splash screen (applies on next launch)",
    "延迟浮标大小：": "Float size:",
    "小": "Small",
    "中": "Medium",
    "大": "Large",
    "🔎 延迟浮标大小已设为 {0} px": "🔎 Float size set to {0} px",
    "浮标带阴影": "Float drop shadow",
    "贴边时收纳浮标（只露一条边，鼠标移上去展开）":
        "Dock the float at the window edge (leaves a sliver; hover to expand)",
    "🖼️ 浮标阴影已{0}": "🖼️ Float shadow {0}",
    "📌 贴边收纳已{0}": "📌 Edge docking {0}",
    "启用 PySide6 窗口": "Enable PySide6 windows",
    "关掉 = 纯 Tk 模式：主进程不再加载 Qt（重启后生效）":
        "Off = pure Tk mode: the main process stops loading Qt (after a restart)",
    "点空白处退出主界面编辑": "Leave edit mode when clicking empty space",
    "关闭后只能用编辑开关自己关": "When off, only the edit switch leaves edit mode",
    "迁移完成后按任意键关闭": "Close on any key when the migration finishes",
    "后台静默执行任务（不弹进度/结果窗口，完成后系统通知）":
        "Run tasks silently in the background (no progress/result windows; a system "
        "notification when done)",
    "⚠ 已开启": "⚠ Enabled",
    "覆盖（先备份）": "Overwrite (back up first)",
    "跳过（目标保持不变）": "Skip (keep target)",
    "🪟 经典 Tk 窗口": "🪟 Classic Tk window",
    "🗂 PySide6 试点窗口": "🗂 PySide6 windows",

    # ---------- 设置页：分组标题与小标签 ----------
    "🏷️ 迁移行为": "🏷️ Migration behaviour",
    "🌐 模组分类标签": "🌐 Mod category tags",
    "🔒 任务与锁定": "🔒 Tasks & locking",
    "📦 默认携带的目录": "📦 Default folders",
    "🗂 放大查看窗口": "🗂 Big view windows",
    "🚪 关闭与后台": "🚪 Close & background",
    "🔗 快捷链接": "🔗 Links",
    "🧩 界面按钮（勾选显示 / 上下调整顺序）":
        "🧩 Toolbar buttons (tick to show / reorder)",
    "标记：": "Marker:",
    "打开时用哪个视图：": "Which view to open with:",
    "差异扫描窗口用哪个实现：": "Which implementation for the diff window:",
    "差异窗口打开时用哪个视图：": "Which view to open the diff window with:",
    "目标已有同名文件时：": "When the target already has a file with the same name:",
    "本地分类缓存：": "Local tag cache:",
    " 条（": " entries (",          # ⚠ 键**带一个前导空格**（源码里就是 " 条（"）
    "，删掉它会重新联网查）": ", delete it to re-fetch online)",
    "（未选择）": "(none selected)",
    "一行一条差异，信息密度高": "One row per difference — denser information",
    "打开就是列表，能改勾选/编辑": "Opens as a list; you can tick and edit",
    "圆角卡片 + 原生动画，需已安装 PySide6":
        "Rounded cards + native animations; requires PySide6",
    "✅ 存在": "✅ Present",
    "❌ 缺失": "❌ Missing",
    "⚠️ 重复": "⚠️ Duplicate",

    # ---------- 设置页：说明文字（静态的） ----------
    "💡 双击日志行可定位到清单里的那条":
        "💡 Double-click a log line to locate it",
    # 窄窗口时换成这条（见 mw_pages 里日志工具栏的 <Configure>）：英文完整句 41 字符
    # 约 280px，加上 4 个按钮会超出窗口，left/right 两组就会互相压掉（用户报过）
    "💡 双击日志行可定位": "💡 Double-click to locate",
    # 主界面按钮的**短文案**：按钮上写短句，被省掉的限定语交给 tooltip
    # （`🔎 检查存在` 在模组 / config / 其它文件三处都出现，各自 tooltip 说明检查的是源目录）
    "🔍 扫描差异": "🔍 Scan diff",
    "🔎 检查存在": "🔎 Check existence",
    "📁 添加文件夹": "📁 Add folder",
    "📄 添加文件": "📄 Add file",
    # 设置里的「打开配置文件」
    "📝 打开配置文件": "📝 Open config file",
    "改完要重启程序才生效": "Restart the app for changes to take effect",
    "📝 已打开配置文件：{0}": "📝 Opened config file: {0}",
    "❌ 打开配置文件失败：{0}": "❌ Failed to open config file: {0}",
    "← 填充路径": "← Fill",
    "🏷️ 来源": "🏷️ Source",
    "⟳ 搜索中…": "⟳ Searching…",
    "⟳ 正在搜索…": "⟳ Searching…",
    # 右下角的网络状态灯
    "🌐 网络：检测中…": "🌐 Network: checking…",
    "🌐 网络：通畅": "🌐 Network: OK",
    "🌐 网络：%s 不通": "🌐 Network: %s unreachable",
    "🌐 网络：不通": "🌐 Network: offline",
    "🌐 网络：检测失败": "🌐 Network: check failed",
    # 左下角的网络状态（圆点 + 文字）
    "正在检测网络…": "Checking network…",
    "网络已连接": "Network connected",
    "网络部分不通": "Network partially reachable",
    "网络未连接": "Network offline",
    # 左下角的流量显示
    "↑ {0}  ↓ {1}  ∑ {2}": "↑ {0}  ↓ {1}  ∑ {2}",
    "%d 个请求 / %s": "%d requests / %s",
    "本工具的联网流量（只看自己的请求，不是整机流量）":
        "This tool's own network traffic (not the whole machine)",
    # 搜索刚开始时的即时反馈（两个源串行，慢的时候要几秒）
    "🔍 正在搜索…（Modrinth + CurseForge）":
        "🔍 Searching… (Modrinth + CurseForge)",
    "🔍 正在搜索…（Modrinth + CurseForge%s）":
        "🔍 Searching… (Modrinth + CurseForge%s)",
    "，只看 %s": ", filtered to %s",
    "🌐 联网搜索（Modrinth + CurseForge）· %s":
        "🌐 Online search (Modrinth + CurseForge) · %s",
    "🌐 联网搜索（Modrinth + CurseForge）":
        "🌐 Online search (Modrinth + CurseForge)",
    "🟧 CurseForge %d 条 · 🟩 Modrinth %d 条 ｜ ":
        "🟧 CurseForge %d · 🟩 Modrinth %d ｜ ",
    "🔍 联网搜索模组（Modrinth + CurseForge）":
        "🔍 Search online (Modrinth + CurseForge)",
    # 网络用量折线图
    "📈 用量": "📈 Usage",
    "本工具的网络用量（最近 60 秒折线图）":
        "Network usage of this tool (last 60s)",
    "📈 网络用量（本工具）": "📈 Network usage (this tool)",
    "只统计本工具自己的请求（搜索 / 取版本），不含系统其他程序。":
        "Counts only requests made by this tool (search / version lookups).",
    "当前累计：": "Total: ",
    "60 秒前": "60s ago",
    "现在": "now",
    "↓ 下行": "↓ Down",
    "↑ 上行": "↑ Up",
    "最近几次探测的连接延迟（双击看完整折线图）":
        "Connection latency of recent probes (double-click for the full chart)",
    "📈 网络延迟（最近 %d 次探测）": "📈 Network latency (last %d probes)",
    "最近 %d 次探测 ｜ 当前 %d ms ｜ 平均 %.0f ms ｜ 最高 %d ms":
        "Last %d probes | now %d ms | avg %.0f ms | max %d ms",
    "还没有探测数据（等几秒）": "No probe data yet (wait a few seconds)",
    "⚠ 部分来源没查成：": "⚠ Some sources could not be queried: ",
    "CurseForge：没配置 API Key（可在设置里填）":
        "CurseForge: no API key configured (fill it in Settings)",
    # CurseForge 限流 / 服务端错误的提示（获批后最容易撞 429）
    "CurseForge 限流了（HTTP 429）：请求太频繁或今日额度用完了，"
    "过一会儿再试；批量搜索时把间隔拉长一点":
        "CurseForge rate-limited this request (HTTP 429): too many requests or the "
        "daily quota is used up. Wait a bit and retry; space out batch searches.",
    "CurseForge 服务端出错（HTTP {0}），稍后重试":
        "CurseForge server error (HTTP {0}); try again later.",
    # 本工具的定位：只做迁移，不提供下载（缺失的模组让用户用自己的启动器补）
    "缺失的模组本工具不负责下载，请在你的启动器（如 PCL）里补装。":
        "This tool does not download mods. Install the missing ones in your "
        "launcher (e.g. PCL).",
    # ---------- 悬停提示（tooltip）----------
    # ⚠ 这一类以前整片漏掉：`create_tooltip` 里确实调了 tr()，但静态审计只扫 `tr(...)`
    # 的参数、扫不到 tooltip 的字符串，于是英文界面下提示全是中文（用户报过"tip 没有英文"）。
    "切换浅色 / 深色主题": "Switch light / dark theme",
    "设置": "Settings",
    "你需要提供的是“崩溃助手”模组给予的mod变更列表":
        "Provide the mod changelist exported by the \"Crash Assistant\" mod",
    "检查清单里的模组在“源目录”里是否存在":
        "Check whether the mods in the list exist in the source folder",
    "检查 config 清单里的条目在“源目录”里是否存在":
        "Check whether the config entries exist in the source folder",
    "检查其它文件清单里的条目在“源目录”里是否存在":
        "Check whether the other-file entries exist in the source folder",
    "将右侧“新版”的路径复制到左侧“旧版”栏，用于快速测试或反向操作":
        "Copy the target path into the source field (quick testing / reverse migration)",
    # ---------- 路径校验的悬停说明（都是拼出来的句子，逐段翻）----------
    "尚未选择「{0}」整合包路径": "No \"{0}\" instance selected yet",
    "✅ 有效的 Minecraft 整合包实例": "✅ Valid Minecraft modpack instance",
    "· 有 saves/ 存档目录": "· has a saves/ folder",
    "· 无 saves/ 存档目录": "· no saves/ folder",
    "· 模组 {0} 个": "· {0} mods",
    "· 版本：{0}": "· Versions: {0}",
    "· 加载器：": "· Loader: ",
    "· 启动器：": "· Launcher: ",
    "官方启动器": "Official launcher",
    "❌ 不是有效的整合包实例": "❌ Not a valid modpack instance",
    "· 原因：": "· Reason: ",
    "⚠️ 路径含中文，建议改成纯英文（个别模组/存档读取会出问题）":
        "⚠️ The path contains non-ASCII characters; use plain English instead "
        "(some mods/saves fail to load otherwise)",
    # 其它文件页的说明文字：**合成一整句**（原来是三段写死 `\n`，英文下会按中文的断句
    # 位置换行、右边留一大片空白 —— 用户"右边有空为什么不用"）。合并后交给 wraplength
    # 按容器宽度自动折行，所以这个"整句"必须有自己的词条。
    "每行一个路径，「相对整合包根目录」（文件或文件夹；文件夹会递归复制）。"
    "不会自动带任何东西 —— 想要的自己加。"
    "例：options.txt   servers.dat   shaderpacks/   resourcepacks/   kubejs/":
        "One path per line, relative to the modpack root (a file or a folder; folders "
        "are copied recursively). Nothing is carried automatically — add whatever you "
        "want. e.g. options.txt   servers.dat   shaderpacks/   resourcepacks/   kubejs/",
    "双击一行也能切换显示/隐藏；顺序只在同一排内调整。":
        "Double-clicking a row also toggles it; order is adjusted within a row only.",
    "主界面的按钮改完立刻生效；放大查看 / 日志放大查看这些窗口里的按钮，"
    "是下次打开那个窗口时生效。":
        "Buttons in the main window apply immediately; in windows such as Big view / the log "
        "big view they apply the next time that window is opened.",
    "两个窗口默认都用 PySide6；如果遇到窗口相关的异常，可以把它们切回经典 Tk 实现"
    "（功能和数据完全一样，只是观感旧一些）。":
        "Both windows use PySide6 by default; if you run into window-related problems you can "
        "switch them back to the classic Tk implementation (same features and data, older look).",
    "三个选项都只是「盖不盖遮罩」的区别：迁移期间所有操作按钮一律禁用，"
    "执行日志始终留着，方便看进度。":
        "The three options only differ in whether the overlay is drawn: during a migration "
        "every action button is disabled, and the log stays visible so you can follow progress.",
    "关闭时按关键词推测（标注“推测”的就是它）。开启后扫描会在后台联网查询，"
    "结果缓存到本地；断网或匹配不到时自动沿用推测结果。":
        "When off, categories are guessed from keywords (those are marked \"guessed\"). When on, "
        "scanning queries online in the background and caches the result locally; offline, or "
        "when nothing matches, it falls back to the guess.",
    "跑完不会“啪”地消失：锁屏里先打印迁移总结、边框从红渐变成绿（有失败则变橙）。"
    "开启时停在完成态等你按任意键（或点一下）；关闭则 2 秒后自动收起。":
        "It does not vanish the moment it finishes: the lock overlay prints the summary and the "
        "border fades from red to green (orange if something failed). When on, it waits in that "
        "state for any key (or a click); when off, it closes itself after 2 seconds.",
    "⚠️ 本工具完全免费，请勿上当受骗！如遇收费行为，请立即举报。⚠️":
        "⚠️ This tool is completely free — don't get scammed! Report anyone charging for it. ⚠️",
    "⚠️ 注意：复制将直接覆盖目标 config 中的同名文件/文件夹，请谨慎操作！":
        "⚠️ Note: copying overwrites same-named files/folders in the target config — be careful!",
    "每行一个相对路径，「相对源实例的 config 目录」（例：jei/jei.toml、sodium-options.json）。":
        "One relative path per line, relative to the source instance's config folder "
        "(e.g. jei/jei.toml, sodium-options.json).",
    "每行一个 .jar 文件名（可从变更日志导入、点「添加模组」选，"
    "或直接把 jar / mods 文件夹拖进来）。同名匹配不区分大小写，改过名的也能对上。":
        "One .jar per line (import from a changelog, pick with \"Add mods\", or drag jars / "
        "a whole mods folder in). Name matching ignores case, so renamed files still match.",
    "https://github.com/Dreamtell/Minecraft-Migration-Tool\n欢迎反馈问题或提交建议。":
        "https://github.com/Dreamtell/Minecraft-Migration-Tool\n"
        "Bug reports and suggestions are welcome.",
    "迁移只带「其它文件」清单里列出的东西（还有模组 / config / 存档这三个清单）。\n"
    "清单是唯一依据：不在这里的一律不动。常用目录在「其它文件」页用「＋ 常用目录」加，"
    "加完能看见、能改、能删。":
        "Migration only carries what the \"Other files\" list names (plus the mods / config / "
        "saves lists).\nThe list is the single source of truth: anything not on it is left alone. "
        "Add common folders from the \"Other files\" tab with \"＋ Common folders\" — once added "
        "they are visible, editable and removable.",
    "每行一个路径，「相对整合包根目录」（文件或文件夹；文件夹会递归复制）。\n"
    "不会自动带任何东西 —— 想要的自己加。\n"
    "例：options.txt   servers.dat   shaderpacks/   resourcepacks/   kubejs/":
        "One path per line, relative to the modpack root (a file or a folder; folders are copied "
        "recursively).\nNothing is carried automatically — add whatever you want.\n"
        "e.g. options.txt   servers.dat   shaderpacks/   resourcepacks/   kubejs/",

    # ---------- 动态文案的模板（f-string 拼出来的串在词典里对不上，得写成模板） ----------
    "效果（{state}）：{marker} create-1.20.1-6.0.9.jar":
        "Preview ({state}): {marker} create-1.20.1-6.0.9.jar",
    "PySide6 当前{state}。{why}": "PySide6 is currently {state}. {why}",
    "可用": "available",
    "不可用": "not available",
    "Qt 窗口由独立子进程渲染；它崩了也不会带走主程序。":
        "Qt windows are rendered by a separate process, so a crash there cannot take the app down.",
    "装好 PySide6 后可切到 Qt 版窗口：pip install PySide6":
        "Install PySide6 to switch to the Qt windows: pip install PySide6",
    "已在设置里关掉 PySide6（纯 Tk 模式）：主进程不再加载 Qt。":
        "PySide6 is switched off in Settings (pure Tk mode): the main process no longer loads Qt.",
    "PySide6 探测失败（{kind}: {err}）": "PySide6 detection failed ({kind}: {err})",
    "当前清单是空的：迁移时只带模组 / config / 存档，别的一律不动。":
        "The list is empty: a migration only carries mods / config / saves — nothing else.",
    "清单里 {n} 条；选了源整合包目录后才能显示哪些实际存在。":
        "{n} entries; pick a source modpack folder to see which ones actually exist.",
    "清单里 {n} 条，源目录里找得到 {m} 条": "{n} entries, {m} of them found in the source",
    "（找不到：{names}{more}）": "(missing: {names}{more})",
    " 等": " and more",
    "，这次都会带上 ✅": " — all of them will be carried ✅",
    "{prefix}：{masked}（指纹 {fp}）": "{prefix}: {masked} (fingerprint {fp})",
    "你自己填的": "set by you",
    "来自环境变量": "from an environment variable",
    "程序内置的（想用自己的配额就在上面填一个）":
        "the built-in one (paste your own above to use your own quota)",
    "已保存": "saved",
    "不填也能用：程序内置了一把共享 Key 时直接用它；填了就用你自己的配额"
    "（更稳，共享那把被限流时你还能用）。\n"
    "你填的 Key 只存在这里：{path}，和主配置分开。\n"
    "它不会进日志、报错或临时文件；输入框粘过就清空，保存后只显示末 4 位。":
        "Optional: when the app ships with a built-in shared key it is used as-is; paste your own "
        "to use your own quota (more reliable — if the shared key gets rate-limited, yours still "
        "works).\nYour key is stored only here: {path}, separate from the main config.\n"
        "It never reaches logs, error messages or temp files; the field is cleared as soon as you "
        "save and only the last 4 characters are shown.",

    # ---------- 第 2 批：Qt 窗口（放大查看 / 模组差异）----------
    # 这些是在 Qt 子进程里用的（ui/qt_big_view.py、ui/qt_diff_view.py）。Qt 侧没法包 Tk 那套，
    # 靠"包住会显示文字的 Qt API" + 调用点显式 tr()，词条仍然集中在这一张表里。
    "不限": "Any",
    "卡片": "Cards",
    "存在": "Present",
    "实例": "Instance",
    "文件": "File",
    "新增": "New",
    "更新": "Updated",
    "降级": "Downgrade",
    "版本": "Version",
    "状态": "Status",
    "类型": "Type",
    "缺失": "Missing",
    "表格": "Table",
    "分类：": "Category: ",
    "只看：": "Only: ",
    "可更新": "Updatable",
    "存在 ": "present ",
    "文件名": "File name",
    "文件夹": "Folder",
    "最相似": "Best match",
    "检测中": "Checking",
    "ℹ 详情": "ℹ Details",
    "⇄ 反选": "⇄ Invert",
    "▲ 升序": "▲ Ascending",
    "▼ 降序": "▼ Descending",
    "☑ 全选": "☑ Select all",
    "☑ 选中": "☑ Selected",
    "☑ 选择": "☑ Select",
    "✖ 关闭": "✖ Close",
    "不限版本": "Any version",
    "切到%s": "Switch to %s",
    "搜索词:": "Search:",
    "检测中…": "Checking…",
    "模组详情": "Mod details",
    "添加成功": "Added",
    "添加提示": "Add",
    "目标独有": "Target-only",
    "移出清单": "Remove from list",
    "获取中…": "Fetching…",
    "获取失败": "Fetch failed",
    "👤 作者": "👤 Author",
    "📄 名称": "📄 Name",
    "📋 详情": "📋 Details",
    "📝 备注": "📝 Note",
    "🔖 版本": "🔖 Version",
    "🔵 状态": "🔵 Status",
    "🧩 类型": "🧩 Type",
    "不限加载器": "Any loader",
    "刷新总览条": "Refresh overview bar",
    "已选 %d": "%d selected",
    "总览条跳转": "Overview bar jump",
    "排序依据：": "Sort by: ",
    "🏷️ 类型": "🏷️ Type",
    "📄 文件名": "📄 File name",
    " / 缺失 ": " / missing ",
    " ⬆ 可更新": " ⬆ updatable",
    "▾ 组合选择": "▾ Combined selection",
    "☐ 取消选中": "☐ Deselect",
    "✅ 全选新增": "✅ Select all new",
    "✅ 应用所选": "✅ Apply selected",
    "⬇️ 下载量": "⬇️ Downloads",
    "⬜ 清空勾选": "⬜ Clear ticks",
    "共 %d 项": "%d items",
    "只看 %s；": "only %s; ",
    "大小(KB)": "Size (KB)",
    "更新滚动进度": "Update scroll progress",
    "没有可用版本": "No usable version",
    "🆔 项目ID": "🆔 Project ID",
    "🌐 联网搜索": "🌐 Online search",
    "Modrinth + CurseForge · 正在搜：%s":
        "Modrinth + CurseForge · searching for %s",
    "搜模组名、Mod ID，或者 jar 文件名":
        "Search a mod name, mod ID, or jar file name",
    "📋 表格": "📋 Table",
    "🗂 卡片": "🗂 Cards",
    "💾 大小KB": "💾 Size KB",
    "📁 完整路径": "📁 Full path",
    "🔄 全选更新": "🔄 Select all updated",
    "🔍 联网搜索": "🔍 Online search",
    "🔖 最新版本": "🔖 Latest version",
    "🗑 移出清单": "🗑 Remove from list",
    "差异窗口 %s": "Diff window %s",
    "已选 %d 项": "%d items selected",
    "显示 %d 项": "%d shown",
    "滚动到出错条目": "Scroll to the failing entry",
    "请输入搜索词。": "Please enter a search term.",
    "🌍 打开项目页": "🌍 Open project page",
    "🔍 检测存在性": "🔍 Check existence",
    "⬇️ 打开下载页": "⬇️ Open download page",
    "无 %s 的版本": "No version for %s",
    "没有勾选任何模组": "No mods ticked",
    "自动识别自 %s": "auto-detected from %s",
    "，按 %s 过滤": ", filtered by %s",
    "💾 大小(KB)": "💾 Size (KB)",
    "📂 打开所在位置": "📂 Open file location",
    "📋 复制下载链接": "📋 Copy download link",
    "📌 全选目标独有": "📌 All target-only",
    "🧩 模组差异扫描": "🧩 Mod diff scan",
    "🪟 系统原生窗口": "🪟 Native window",
    "应用所选 %d 个": "Apply %d selected",
    "总计 %d 项差异": "%d differences in total",
    "排序/过滤后回顶部": "Back to top after sorting/filtering",
    "放大查看 - %s": "Big view - %s",
    "新增 + 目标独有": "New + target-only",
    "更新 + 目标独有": "Updated + target-only",
    "联网搜索 - %s": "Online search - %s",
    "这次没有出错的条目": "No failing entries this time",
    "（当前只看 %s）": " (currently showing only %s)",
    "PySide6 试点": "PySide6 preview",
    "只搜这个加载器的项目": "Only search projects for this loader",
    "已复制下载链接：%s": "Download link copied: %s",
    "所有文件 (*.*)": "All files (*.*)",
    "（已按 %s 过滤）": " (filtered by %s)",
    "（该模组未提供描述）": "(this mod provides no description)",
    "★为最相似项，已置顶。": "★ marks the best match, pinned to the top.",
    "★为各平台各自最相似的那条。": "★ marks each source's best match.",
    "先搜出结果、再点一行。": "Search first, then click a row.",
    "已添加 %d 个条目。": "Added %d entries.",
    "已添加 %d 个模组。": "Added %d mods.",
    "已过滤，显示 %d 项": "Filtered, %d shown",
    "退场动画结束后执行删除": "delete after the exit animation",
    "📁 相对路径/完整路径": "📁 Relative/full path",
    "🗂 放大查看 · %s": "🗂 Big view · %s",
    "处理 Ctrl+V 粘贴": "Handle Ctrl+V paste",
    "未找到相似度足够的候选。": "No candidate matched closely enough.",
    "这一项还没拿到下载链接。": "No download link for this entry yet.",
    "这些条目已经在清单里了。": "Those entries are already in the list.",
    "（另有 %d 项被跳过）": "(%d more skipped)",
    "这个项目没有「%s」的构建": "This project has no build for \"%s\"",
    "这个项目没有可用的版本文件": "This project has no usable version files",
    "选择要添加的模组（可多选）": "Choose mods to add (multi-select)",
    "智能模组差异扫描（元数据级）": "Mod diff scan (metadata level)",
    "没认出你的实例版本，可手动选":
        "Could not detect your instance version — pick one manually",
    "该文件不在磁盘上，无法定位。": "That file is not on disk, cannot locate it.",
    "新增 + 更新（排除目标独有）": "New + updated (exclude target-only)",
    "🗂 放大查看 · %s（%d）": "🗂 Big view · %s (%d)",
    "🧩 模组差异扫描 · %d 项": "🧩 Mod diff scan · %d items",
    "只支持拖入 .jar 模组文件。": "Only .jar mod files can be dragged in.",
    "🌐 联网搜索（Modrinth）": "🌐 Search on Modrinth",
    "🔍 搜索（文件名 / 完整路径）": "🔍 Search name or path",
    "搜索中…（%s首次联网可能要几秒）":
        "Searching… (%s the first online request may take a few seconds)",
    "搜索词（模组名 / Mod ID）": "Search term (name / Mod ID)",
    "差异窗口打开详情 row=%d %s": "diff window open details row=%d %s",
    "📍 第 %d/%d 个出错条目：%s": "📍 failing entry %d/%d: %s",
    "DetailDialog/联网搜索标签": "DetailDialog/online search tab",
    "没有找到相关模组%s，换个关键词试试。":
        "No matching mods found%s — try another keyword.",
    "Minecraft 模组 (*.jar)": "Minecraft mods (*.jar)",
    "勾选当前显示的所有条目（不会改清单内容）":
        "Tick every entry currently shown (the list itself is not changed)",
    "取消所有勾选（只清选中态，清单内容不动）":
        "Clear all ticks (selection state only; the list is untouched)",
    "总览：整份列表的缩略图 · 点或拖可跳转":
        "Overview: a thumbnail of the whole list · click or drag to jump",
    "🌐 联网搜索（Modrinth）· %s": "🌐 Search on Modrinth · %s",
    "请先在主界面设置源整合包目录，再往这里拖。":
        "Set the source modpack folder in the main window first, then drag files here.",
    "跳到下一个出错的条目（清单里标红的那几条）":
        "Jump to the next failing entry (the ones marked red in the list)",
    "出错的条目被搜索/过滤挡住了，先清一下搜索框":
        "Failing entries are hidden by the search/filter — clear the search box first",
    "请先选中要移出的条目（单击行/卡片即选中）。":
        "Select the entries to remove first (click a row/card).",
    "detail 复用 row=%d key=%s": "detail reuse row=%d key=%s",
    "把已勾选 / 未勾选反过来（只作用于当前显示）":
        "Invert ticked/unticked (only for what is currently shown)",
    "拖入的文件不在源整合包的 config 目录下。":
        "The dropped file is not inside the source modpack's config folder.",
    "这一项还没拿到下载链接（或该项目没有可用文件）。":
        "No download link for this entry yet (or the project has no usable files).",
    "找到 %d 个结果。%s%s 版本/下载链接加载中…":
        "%d results found. %s%s loading versions/download links…",
    "把选中的 %d 项从清单里移出？（不会删除磁盘文件）":
        "Remove the %d selected entries from the list? (files on disk are not deleted)",
    "版本信息没取到（网络或接口失败），可在外面手动打开项目页看":
        "Could not fetch version info (network or API failure) — open the project page "
        "manually to check",
    "只搜支持这个 MC 版本的项目；清空或选「不限版本」就是不过滤":
        "Only search projects supporting this MC version; clear it or pick \"Any version\" "
        "for no filter",
    "🔍 搜索（文件名 / Mod ID / 版本 / 类型 / 备注）":
        "🔍 Search name / ID / version",
    "找到 %d 个结果。版本/下载链接已全部就绪%s（⬆ = 比本地新）。":
        "%d results found. Versions/download links are ready%s (⬆ = newer than local).",
    "按 %s 没搜到东西 —— 可能是版本认错了，已自动放宽版本再搜一次。":
        "Nothing found for %s — the version may be misdetected; retried without the "
        "version filter.",
    "回车或点「联网搜索」开始；最相似的会置顶并标 ★。双击一行打开项目主页。":
        "Press Enter or click \"Online search\" to start; the best match is pinned and "
        "marked ★. Double-click a row to open the project page.",
    "Mod ID：%s": "Mod ID: %s",
    # 明细区那一整块（六行拼成的一个常量）
    "Mod ID：%s\n版本：%s\n类型：%s\n大小：%s KB\n状态：%s\n路径：%s":
        "Mod ID: %s\nVersion: %s\nType: %s\nSize: %s KB\nStatus: %s\nPath: %s",
    "版本：%s": "Version: %s",
    "类型：%s": "Type: %s",
    "大小：%s KB": "Size: %s KB",
    "状态：%s": "Status: %s",
    "路径：%s": "Path: %s",
    "单击=选中 · 双击=详情 · 右键=菜单 · Ctrl+V=粘贴文件 · Esc=关闭":
        "Click = select · Double-click = details · Right-click = menu · Ctrl+V = paste "
        "files · Esc = close",
    "单击=勾选 · 双击=模组详情 · Ctrl+A=全选 · Ctrl+F=搜索 · Esc=关闭":
        "Click = tick · Double-click = mod details · Ctrl+A = select all · Ctrl+F = "
        "search · Esc = close",

    # ---------- 第 3 批：执行日志正文 ----------
    # 日志全部经 gui.log() 这一个入口（core/ 里的模块也是回调到它），所以：
    #   · 静态串在入口 tr() 一次命中（词条就是下面这些）；
    #   · f-string 拼的串已经机械改成 trp("模板 {0}", 值…)（见 _dctest/改日志模板.py）。
    "退出": "Exit",
    "打开主界面": "Open main window",
    "关闭窗口时收进托盘": "Minimise to tray when closing",
    "开关回调": "Toggle callback",
    "分段选择回调": "Segment select callback",
    "绑定文本框滚动": "Bind text box scrolling",
    "同步编辑锁": "Sync edit lock",
    "锁屏总结": "Lock-screen summary",
    "缺失列表：": "Missing list:",
    "缺失条目：": "Missing entries:",
    "不会实际修改任何文件": "No files will actually be modified",
    "模拟模式: 是": "Dry run: yes",
    "存档名称: {0}": "Save name: {0}",
    "📊 迁移总结": "📊 Migration summary",
    "📊 扫描完成：无差异": "📊 Scan finished: no differences",
    "📊 扫描完成，发现 {0} 项差异": "📊 Scan finished: {0} differences found",
    "📊 模组清单检查结果：总清单项数 {0}":
        "📊 Mod list check: {0} entries in total",
    "📊 config 清单检查结果：总条目 {0}，去重后 {1} 个":
        "📊 config list check: {0} entries, {1} after de-duplication",
    "📊 其它文件清单检查结果：总条目 {0}，去重后 {1} 个":
        "📊 \"Other files\" check: {0} entries, {1} after de-duplication",
    "⚠️ 用户取消了迁移": "⚠️ The user cancelled the migration",
    "⚠️ 当前模组清单为空": "⚠️ The mod list is empty",
    "⚠️ 当前 config 清单为空": "⚠️ The config list is empty",
    "⚠️ 当前其它文件清单为空": "⚠️ The \"Other files\" list is empty",
    "⚠️ 目标路径为空，无法复制": "⚠️ The target path is empty, nothing to copy",
    "⚠️ 卡片视图不可用：{0}": "⚠️ Card view unavailable: {0}",
    "⚠️ 请先选择源整合包实例根目录":
        "⚠️ Pick the source modpack root folder first",
    "⚠️ 请先选择源整合包目录，再往清单里加东西":
        "⚠️ Pick the source modpack folder before adding to the lists",
    "⚠️ 请勿频繁操作！请稍后再试。":
        "⚠️ Too many actions — please wait a moment and try again.",
    "⚠️ 已有迁移在进行中（或正在准备），这次点击已忽略":
        "⚠️ A migration is already running (or being prepared); this click was ignored",
    "⚠️ 已拦截：{0} 进行中，暂不允许启动迁移":
        "⚠️ Blocked: {0} is running, a migration cannot start yet",
    "⚠️ 无法读取目标磁盘信息，已跳过空间检查":
        "⚠️ Could not read target disk info; skipped the space check",
    "⚠️ 警告：已启用主界面编辑模式，直接修改清单可能导致数据错误，请谨慎操作！":
        "⚠️ Warning: main-window edit mode is on — editing the lists directly can "
        "corrupt your data, so be careful!",
    "✅ 存在的条目：{0}": "✅ Entries present: {0}",
    "✅ 存在的模组：{0}": "✅ Mods present: {0}",
    "✅ 已复制: {0}": "✅ Copied: {0}",
    "✅ 已复制 config: {0}": "✅ Copied config: {0}",
    "✅ 已复制 config: {0}/{1}": "✅ Copied config: {0}/{1}",
    "✅ 已复制其它文件: {0}": "✅ Copied other files: {0}",
    "✅ 已添加 {0} 个模组": "✅ Added {0} mods",
    "✅ 已添加 {0} 个 config 文件条目": "✅ Added {0} config file entries",
    "✅ 已添加 {0} 个 config 子文件夹条目": "✅ Added {0} config subfolder entries",
    "✅ 已添加 {0} 个其它文件条目": "✅ Added {0} \"Other files\" entries",
    "✅ 已添加 {0} 个其它文件条目（相对整合包根目录）":
        "✅ Added {0} \"Other files\" entries (relative to the modpack root)",
    "✅ 从差异扫描中导入了 {0} 个模组": "✅ Imported {0} mods from the diff scan",
    "✅ 其它文件条目均存在且无重复。":
        "✅ All \"Other files\" entries exist and there are no duplicates.",
    "✅ 所有 config 条目均存在且无重复。":
        "✅ All config entries exist and there are no duplicates.",
    "✅ 用户确认回滚，开始执行...": "✅ The user confirmed the rollback, starting...",
    "✅ 存档 {0} 已{1}复制完成，共 {2} 个文件":
        "✅ Save {0} {1}copy finished, {2} files",
    "❌ 备份失败：{0}": "❌ Backup failed: {0}",
    "❌ 扫描出错: {0}": "❌ Scan error: {0}",
    "❌ 用户取消了回滚操作": "❌ The user cancelled the rollback",
    "❌ 缺失的条目：{0}": "❌ Missing entries: {0}",
    "❌ 缺失的模组：{0}": "❌ Missing mods: {0}",
    "❌ 日志保存失败：{0}": "❌ Failed to save the log: {0}",
    "❌ 打开文件夹失败：{0}": "❌ Could not open the folder: {0}",
    "❌ 未找到匹配模组: {0}": "❌ No matching mod found: {0}",
    "❌ 复制失败 {0}: {1}": "❌ Copy failed {0}: {1}",
    "❌ 复制 config 失败 {0}: {1}": "❌ Failed to copy config {0}: {1}",
    "❌ 复制其它文件失败 {0}: {1}": "❌ Failed to copy other files {0}: {1}",
    "❌ 复制存档文件 {0} 失败: {1}": "❌ Failed to copy save file {0}: {1}",
    "❌ 创建目录 {0} 失败: {1}": "❌ Could not create folder {0}: {1}",
    "❌ 创建 config 目录 {0} 失败: {1}": "❌ Could not create config folder {0}: {1}",
    "❌ 创建 config 目录 {0}/{1} 失败: {2}":
        "❌ Could not create config folder {0}/{1}: {2}",
    "❌ 复制 config 文件 {0}/{1} 失败: {2}":
        "❌ Failed to copy config file {0}/{1}: {2}",
    "❌ 源整合包目录不存在：{0}": "❌ The source modpack folder does not exist: {0}",
    "❌ 源 mods 目录不存在：{0}": "❌ The source mods folder does not exist: {0}",
    "❌ 源 config 目录不存在：{0}": "❌ The source config folder does not exist: {0}",
    "❌ 源条目不存在: {0}，跳过": "❌ Source entry does not exist: {0}, skipping",
    "❌ 源 config 条目不存在: {0}，跳过":
        "❌ Source config entry does not exist: {0}, skipping",
    "❌ 迁移过程中发生未预期错误: {0}":
        "❌ Unexpected error during migration: {0}",
    "❌ 回滚失败：未选择目标实例根目录":
        "❌ Rollback failed: no target instance root folder selected",
    "❌ 回滚失败：未找到备份目录 {0}":
        "❌ Rollback failed: backup folder {0} not found",
    "❌ 回滚失败：目标路径不存在 {0}":
        "❌ Rollback failed: target path {0} does not exist",
    "❌ 回滚操作失败，目标实例可能处于不完整状态，备份仍然保留，可重新执行回滚":
        "❌ The rollback failed; the target instance may be incomplete. The backup is "
        "kept — you can run the rollback again",
    "🔗 已打开链接：{0}": "🔗 Opened link: {0}",
    "ℹ️ 这次没有出错的条目": "ℹ️ No failing entries this time",
    "ℹ️ config 清单为空，跳过": "ℹ️ The config list is empty, skipping",
    "ℹ️ 其它文件清单为空，跳过": "ℹ️ The \"Other files\" list is empty, skipping",
    "ℹ️ 主界面编辑模式已关闭，清单恢复只读。":
        "ℹ️ Main-window edit mode is off; the lists are read-only again.",
    "ℹ️ 清单窗口都关了，主界面编辑恢复成「开」。":
        "ℹ️ All list windows are closed; main-window editing is back on.",
    "ℹ️ 已经在所在那一排的边界了，无法继续移动":
        "ℹ️ Already at the edge of this row, cannot move further",
    "ℹ️ 这一行没提到清单中的条目，无法定位":
        "ℹ️ This line does not mention any list entry, cannot locate it",
    "ℹ️ 原生对话框不可用，已切换到自定义多选对话框":
        "ℹ️ The native dialog is unavailable; switched to the custom multi-select dialog",
    "ℹ️ 已取消：确认框里点了「取消」，没有动任何文件。":
        "ℹ️ Cancelled: \"Cancel\" was clicked in the confirmation box; nothing was "
        "touched.",
    "ℹ️ 所选文件均已在其它文件清单中，未重复添加":
        "ℹ️ All selected files are already in the \"Other files\" list; nothing added",
    "ℹ️ 所选文件夹均已在其它文件清单中，未重复添加":
        "ℹ️ All selected folders are already in the \"Other files\" list; nothing added",
    "ℹ️ 所选文件均已在 config 清单中，未重复添加":
        "ℹ️ All selected files are already in the config list; nothing added",
    "ℹ️ 所选文件夹均已在 config 清单中，未重复添加":
        "ℹ️ All selected folders are already in the config list; nothing added",
    "ℹ️ 历史记录里没有可标记的迁移记录（可能已经回滚过了）":
        "ℹ️ No migration in the history can be marked (it may have been rolled back "
        "already)",
    "ℹ️ 先在输入框里粘一个 Key，或者先「保存」一个再测。":
        "ℹ️ Paste a key into the box first, or save one before testing.",
    "ℹ️ 输入框是空的：没有保存任何东西（要删掉已保存的 Key 请点「清除」）。":
        "ℹ️ The box is empty: nothing was saved (to delete a saved key, click "
        "\"Clear\").",
    "【步骤1】复制模组...": "[Step 1] Copying mods...",
    "\n【步骤2】复制存档...": "\n[Step 2] Copying saves...",
    "\n【步骤3】复制 config 内容...": "\n[Step 3] Copying config contents...",
    "\n========== 迁移完成 ==========": "\n========== Migration finished ==========",
    "========== 开始迁移（模拟） ==========":
        "========== Migration started (dry run) ==========",
    "已将目标路径复制到源路径": "Copied the target path to the source path",
    "旧版目录（源）: {0}": "Old folder (source): {0}",
    "新版目录（目标）: {0}": "New folder (target): {0}",
    "📁 找到备份目录：{0}": "📁 Backup folder found: {0}",
    "🔄 用户请求执行回滚操作": "🔄 The user requested a rollback",
    "🗑 已从清单移除：{0}": "🗑 Removed from the list: {0}",
    "🗑 已删除本机保存的 CurseForge API Key（联网搜索仍走 Modrinth）。":
        "🗑 Deleted the CurseForge API key stored on this machine (online search still "
        "uses Modrinth).",
    "[模拟] 将复制: {0}": "[dry run] would copy: {0}",
    "[模拟] 将复制 config: {0}": "[dry run] would copy config: {0}",
    "[模拟] 将复制 config: {0}/{1}": "[dry run] would copy config: {0}/{1}",
    "[模拟] 将复制其它文件: {0}": "[dry run] would copy other files: {0}",
    "[模拟] 将创建目录: {0}": "[dry run] would create folder: {0}",
    "[模拟] 将创建目录: {0}/{1}": "[dry run] would create folder: {0}/{1}",
    "⚠️ 重复条目：{0} 行": "⚠️ Duplicate entries: {0} lines",
    "⚠️ 跳过不安全路径: {0}": "⚠️ Skipped unsafe path: {0}",
    "⚠️ 跳过不安全 config 路径: {0}": "⚠️ Skipped an unsafe config path: {0}",
    "⚠️ 跳过不安全的其它文件路径: {0}":
        "⚠️ Skipped an unsafe \"Other files\" path: {0}",
    "⚠️ 条目 {0} 非文件非目录，跳过":
        "⚠️ Entry {0} is neither a file nor a folder, skipping",
    "⚠️ config 条目 {0} 非文件非目录，跳过":
        "⚠️ Config entry {0} is neither a file nor a folder, skipping",
    "⚠️ 旧 mods 目录不存在: {0}，跳过":
        "⚠️ The old mods folder does not exist: {0}, skipping",
    "⚠️ 源存档不存在: {0}，跳过": "⚠️ Source save does not exist: {0}, skipping",
    "⚠️ 源 config 目录不存在: {0}，跳过":
        "⚠️ The source config folder does not exist: {0}, skipping",
    "⚠ Qt 子进程起不来，改用进程内 Tk 差异窗口":
        "⚠ The Qt child process would not start; falling back to the in-process Tk "
        "diff window",
    "⚠ 无法启动 Qt 子进程：{0}": "⚠ Could not start the Qt child process: {0}",
    "⚠ PySide6 窗口事件循环异常：{0}": "⚠ PySide6 window event loop error: {0}",
    "　出错条目（最多列 5 条）：": " Failing entries (up to 5):",
    "　可点主界面“查看失败条目”定位":
        " Click \"View failing entries\" in the main window to locate it",
    "　没有失败条目，可以直接启动游戏了 🎮":
        " No failing entries — you can start the game 🎮",
    "  ... 还有 {0} 个未显示": "  ... {0} more not shown",
    "  ... 还有 {0} 条未显示": "  ... {0} more lines not shown",
    "🔒 迁移锁定方式已改为：{0}": "🔒 Migration lock mode changed to: {0}",
    "⏭️ 跳过已存在的模组: {0}": "⏭️ Skipped an existing mod: {0}",
    "⏭️ 目标已存在，按设置跳过: {0}":
        "⏭️ Already in the target, skipping per settings: {0}",
    "🧩 界面按钮已恢复默认显示与顺序":
        "🧩 Toolbar buttons restored to the default selection and order",
    "🏷️ 迁移标记符号已改为「{0}」": "🏷️ Migration marker changed to \"{0}\"",
    "🏷️ 模组迁移标记已{0}": "🏷️ Migration marker {0}",
    "（前缀「{marker}」）": " (prefix \"{marker}\")",
    "主题已切换为{0}模式": "Theme switched to {0} mode",
    "🔍 开始扫描模组差异，请稍候...": "🔍 Scanning the mod diff, please wait...",
    "🌐 联网分类已关闭：改回按关键词推测分类":
        "🌐 Online categories off: back to guessing from keywords",
    "🌐 联网分类已开启：重开一次“放大查看”就会在后台查询真实分类（结果会缓存到本地）":
        "🌐 Online categories on: reopen Big view once and the real categories are "
        "fetched in the background (results are cached locally)",
    "🌐 正在测试 CurseForge API Key…（首次联网可能要几秒）":
        "🌐 Testing the CurseForge API key… (the first online request may take a few "
        "seconds)",
    "🌐 界面语言已设为「{0}」—— 重启程序后完全生效（此后新打开的窗口会立刻用新语言）":
        "🌐 Interface language set to \"{0}\" — it takes full effect after a restart "
        "(windows opened from now on use it immediately)",
    "🎬 启动动画已{0}（下次启动程序生效）":
        "🎬 Splash screen {0} (applies on next launch)",
    "🤫 后台静默执行已{0}（跑任务时不再弹进度/结果窗口）":
        "🤫 Silent background run {0} (no progress/result windows while working)",
    "🤫 后台静默执行已开启：不显示进度窗口，完成后用系统通知提醒":
        "🤫 Silent background run on: no progress window; a system notification when done",
    "📂 已打开日志文件夹：{0}": "📂 Opened the log folder: {0}",
    "📝 已标记本次回滚到历史记录": "📝 Marked this rollback in the history",
    "📝 已记录迁移历史到 {0}": "📝 Migration history written to {0}",
    "📋 日志已清空（无有效操作记录，不保存文件）":
        "📋 Log cleared (no valid operations to record, no file saved)",
    "📋 日志已清空，有效操作记录已追加至 {0}":
        "📋 Log cleared; the recorded operations were appended to {0}",
    "📍 已定位到「{0}」（{1} 第 {2} 行）": "📍 Located \"{0}\" ({1}, line {2})",
    "☑ 已{0}：当前显示 {1} 项，选中 {2} 项": "☑ {0}: {1} shown, {2} selected",
    "🗂 已打开 Qt 差异窗口（{0} 项，独立进程）":
        "🗂 Opened the Qt diff window ({0} entries, separate process)",
    "🗂 已打开 Qt 放大查看：{0}（{1} 项，独立进程）":
        "🗂 Opened Qt Big view: {0} ({1} entries, separate process)",
    "🗂 已切到卡片视图（只读预览；勾选/编辑请切回表格）":
        "🗂 Switched to card view (read-only preview; switch back to the table to tick "
        "or edit)",
    "📦 待迁移文件 {0} 个，总大小 {1:.1f} MB":
        "📦 {0} files to migrate, {1:.1f} MB in total",
    "🔎 已索引目标 mods（{0} 个 modid，耗时 {1:.1f}s），用于识别旧版本":
        "🔎 Indexed the target mods ({0} modids, {1:.1f}s) to recognise older versions",
    "🔓 按设置未锁定界面（操作按钮仍全部禁用）":
        "🔓 Interface not locked per settings (all action buttons stay disabled)",
    "🧹 已移除 {0} 个同 modid 的旧版本（移入 .migrate_backup/removed_mods，回滚时会自动恢复）":
        "🧹 Removed {0} older versions with the same modid (moved to "
        ".migrate_backup/removed_mods; a rollback restores them)",
    "模组复制完成: 成功 {0} 个, 跳过 {1} 个, 失败 {2} 个":
        "mod copy finished: {0} ok, {1} skipped, {2} failed",
    "config 复制完成: 成功 {0} 个, 失败 {1} 个":
        "config copy finished: {0} ok, {1} failed",
    "其它文件复制完成: 成功 {0} 个, 跳过 {1} 个, 失败 {2} 个":
        "other files copy finished: {0} ok, {1} skipped, {2} failed",
    "实际复制完成，请检查日志中的错误信息。":
        "Copying finished; check the log for any errors.",
    "这是模拟运行，未实际修改任何文件。如需实际执行，请取消勾选【模拟运行】。":
        "This was a dry run; no files were modified. To run for real, untick [Dry run].",
    "从变更日志中提取了 {0} 个模组（Added: {1}, Updated: {2}）":
        "Extracted {0} mods from the changelog (Added: {1}, Updated: {2})",
    # 这些是拼进上面模板里的**值**（原文是中文词），也得有词条
    "中文": "Chinese",
    "启用": "on",
    "关闭": "off",
    "开启": "on",
    "浅色": "light",
    "深色": "dark",
    "全选": "select all",
    "清空勾选": "clear ticks",
    "反选": "invert",
    "模拟": "dry-run ",

    # ---------- 第 4 批：Tk 回退窗口（进度窗 / 放大查看 / 差异窗口）----------
    # 控件文案靠入口包装层自动翻（词条就是下面这些）；拼出来才显示的已经改成 trp 模板。
    "迁移进度": "Migration progress",
    "扫描模组差异进度": "Mod diff scan progress",
    "准备中...": "Preparing...",
    "准备扫描...": "Preparing to scan...",
    "正在取消...": "Cancelling...",
    "扫描正在进行，请等待完成。": "A scan is running, please wait for it to finish.",
    "迁移进行中，暂不能检测存在性。":
        "A migration is running, the existence check is unavailable for now.",
    "0 / 0 个文件": "0 / 0 files",
    "0 / 0 个文件  |  0.0 MB / 0.0 MB": "0 / 0 files  |  0.0 MB / 0.0 MB",
    "检测完成": "Check finished",
    "联网搜索中…": "Searching online…",
    "找不到模组文件": "No mod file found",
    "该行没有可查看的模组文件。": "That row has no mod file to view.",
    "执行日志 - 放大查看": "Execution log - Big view",
    "ℹ 查看详情": "ℹ View details",
    "📂 在文件夹中定位": "📂 Show in folder",
    "📝 描述": "📝 Description",
    "🔄 反选": "🔄 Invert",
    "🗑 从清单移除": "🗑 Remove from list",
    "🗑️ 删除选中": "🗑️ Delete selected",
    "✅ 全选（当前显示）": "✅ Select shown",
    "☐ 取消全选": "☐ Deselect all",
    # 进度窗那行"📦 {step}"：step 由调用方传（现在恒为 None，但传了就翻）
    "复制模组": "Copying mods",
    "复制存档": "Copying saves",
    "复制 config 内容": "Copying config contents",
    "未选择": "Not selected",
    "只看:": "Only:",
    "搜索:": "Search:",
    "（未填）": "(not set)",
    "跳过（目标保持不动）": "Skip (keep the target as it is)",
    "（自动置顶最匹配项）": "(the best match is pinned automatically)",
    "可修改搜索词后回车或点击“联网搜索”。":
        "Edit the search term and press Enter, or click \"Online search\".",
    "所选模组已在清单中。": "The selected mods are already in the list.",
    "请先勾选要删除的模组。": "Tick the mods you want to delete first.",
    "确认开始迁移": "Confirm migration",
    "取消迁移": "Cancel migration",
    "⚠ 即将开始迁移（会覆盖目标整合包里的同名文件）":
        "⚠ About to start the migration (same-named files in the target will be overwritten)",
    "⚠️ 迁移进行中，请勿关闭主窗口！":
        "⚠️ Migration in progress — do not close the main window!",
    "迁移前会自动备份目标实例的 mods / config / saves，出问题可一键回滚。":
        "Before migrating, the target instance's mods / config / saves are backed up "
        "automatically, so you can roll back with one click.",
    "以下为扫描结果，勾选你希望复制到目标的模组（单击切勾选，双击看详情）：":
        "Scan results below — tick the mods you want copied to the target (click to tick, "
        "double-click for details):",
    "不想每次都问：设置 → 🚚 迁移与分类 → 关掉「正式迁移前再确认一次」。":
        "Don't want to be asked every time: Settings → 🚚 Migration & categories → turn "
        "off \"Confirm before a real migration\".",
    "迁移正在进行，关闭这个窗口会中断当前操作，可能导致数据损坏。\n\n确定要取消吗？":
        "A migration is running; closing this window interrupts it and can corrupt your "
        "data.\n\nCancel it anyway?",
    "要让程序继续在后台运行，还是直接退出？":
        "Keep the app running in the background, or quit?",
    "收进托盘 —— 程序留在后台继续跑，点托盘图标可以再打开；\n"
    "直接退出 —— 关掉程序（下次要重新启动）。":
        "Minimise to tray — the app keeps running in the background; click the tray icon "
        "to reopen it.\nQuit — close the app (you will need to start it again next time).",
    "（以后想改：右键任务栏托盘图标，勾选/取消「关闭窗口时收进托盘」）":
        "(To change this later: right-click the tray icon and tick/untick \"Minimise to "
        "tray when closing\")",
    # 上面那批"拼出来才显示"的模板译文（键是原文的拼接形状，值是等价的英文模板）
    "{0} 行": "{0} lines",
    "正在复制: {0}": "Copying: {0}",
    "正在解析: {0}": "Parsing: {0}",
    "{0} / {1} 个文件": "{0} / {1} files",
    "{0} / {1} 个文件  |  {2:.1f} MB / {3:.1f} MB":
        "{0} / {1} files  |  {2:.1f} MB / {3:.1f} MB",
    "本地版本 {0}": "Local version {0}",
    "打开失败：{0}": "Could not open: {0}",
    "复制失败：{0}": "Copy failed: {0}",
    "定位失败：{0}": "Could not locate: {0}",
    "已复制{0}：{1}": "Copied {0}: {1}",
    "没有可复制的{0}。": "Nothing to copy — no {0}.",
    "卡片视图不可用：{0}": "Card view unavailable: {0}",
    "总计 {0} 项差异": "{0} differences in total",
    "{0} 新增": "{0} new",
    "{0} 更新": "{0} updated",
    "{0} 降级": "{0} downgraded",
    "{0} 目标独有": "{0} target-only",
    # 自绘控件里的**分段碎片**（DataText 一段一个色键，拼起来才是整句）
    # 例："共 " + N + " 项" → "Total " + N + " items"
    "共 ": "Total ",
    " 项": " items",
    " 次迁移": " migrations",
    " 次": " times",
    "回滚 ": "Rollback ",
    "　·　": " · ",
    "目标实例：": "Target instance: ",
    "已勾选 ": "Selected ",
    " 个文件夹": " folders",
    " 条": " entries",
    "已选 %d": "%d selected",
    "已过滤，显示 %d 项": "filtered, %d shown",
    "已过滤，显示 {0} 项": "filtered, {0} shown",
    "总计 %d 项差异": "%d differences in total",
    "（已过滤，显示 {0} 项）": " (filtered, {0} shown)",
    " | 降级 {0}": " | {0} downgraded",
    "{0} | 新增 {1} | 更新 {2}{3} | 目标独有 {4}":
        "{0} | {1} new | {2} updated{3} | {4} target-only",
    "✅ 已添加 {0} 个模组。": "✅ Added {0} mods.",
    "✅ 存在性检测完成：共 {0} 项，缺失 {1} 项。":
        "✅ Existence check finished: {0} entries, {1} missing.",
    "模组：{0} ｜ 其它文件：{1}": "Mods: {0} ｜ Other files: {1}",
    "模组 {0} · config {1} · 其它 {2} ｜ 共 {3} 个文件 / {4:.1f} MB":
        "{0} mods · {1} config · {2} other ｜ {3} files / {4:.1f} MB in total",

    # ---------- 第 5 批：剩下的界面文案（迁移/回滚确认、清单选择窗、托盘、锁屏…）----------
    "Minecraft 整合包迁移工具 - 增强版 v5":
        "Minecraft Modpack Migration Tool - Enhanced v5",
    "请先选择目标实例根目录": "Pick the target instance root folder first",
    "请先选择源和目标路径": "Pick the source and target paths first",
    "请选择源和目标实例根目录": "Pick the source and target instance root folders",
    "选择源整合包的实例根目录": "Pick the source modpack's instance root folder",
    "选择目标整合包的实例根目录": "Pick the target modpack's instance root folder",
    "请输入存档名称": "Enter a save name",
    "源目录和目标目录相同，无需比较。":
        "The source and target folders are the same, nothing to compare.",
    "两个 mods 目录完全一致，没有任何差异。":
        "The two mods folders are identical — no differences.",
    "三个清单都是空的，没有可迁移的内容。":
        "All three lists are empty — nothing to migrate.",
    "没有找到需要复制的文件，请检查清单。":
        "No files to copy were found; check the lists.",
    "不安全路径": "Unsafe path",
    "磁盘空间不足": "Not enough disk space",
    "扫描错误": "Scan error",
    "备份错误": "Backup error",
    "迁移正在进行中，请勿重复启动":
        "A migration is already running, don't start another",
    "迁移正在进行中，暂不能回滚。":
        "A migration is running; rollback is unavailable for now.",
    "⏳ 扫描中…": "⏳ Scanning…",
    "⏳ 解析中...": "⏳ Parsing...",
    "迁移历史记录": "Migration history",
    "平滑滚动速度": "Smooth scrolling speed",
    "迁移历史最多保存": "Migration history limit",
    "份（1–15，超出的删最旧）": "(1–15 saves, oldest deleted when over)",
    "开始前倒计时": "Start countdown",
    "秒（1–10，点开始后再点一下可取消）": "seconds (1–10, click again to cancel)",
    "⏳ {0} 秒后开始": "⏳ Starting in {0}s",
    "⏳ 开始前倒计时 {0} 秒：期间再点一下「开始迁移」可取消":
        "⏳ Starting in {0}s — click the button again to cancel",
    "ℹ️ 已取消：倒计时期间又点了一下「开始迁移」，没有动任何文件。":
        "ℹ️ Cancelled: clicked again during the countdown. Nothing was touched.",
    # 设置 →「🎨 外观与启动」→ 右下角消息提示的开关
    "显示消息提示（右下角弹出的小卡片）":
        "Show message toasts (small cards in the bottom-right corner)",
    "档（1 最慢 – 5 最快，3 = 原速）": "1 = slowest, 5 = fastest, 3 = default",
    "当前目标实例没有迁移记录。": "The target instance has no migration history.",
    "越靠上越新 · 滚轮翻阅": "Newest at the top · scroll to browse",
    "↩ 回滚到迁移前": "↩ Roll back to before migration",
    "要把目标实例恢复到最近一次迁移之前吗？": "Restore the target instance to how it was before the last migration?",
    "这一操作会用备份覆盖当前内容，迁移前不存在的文件会被删掉，而且不能撤销。": "This overwrites the current contents with the backup; files that did not exist before the migration are deleted. It cannot be undone.",
    "回滚过程中这扇窗会一直开着，进度看主界面的执行日志。": "This window stays open during the rollback; watch the main window log for progress.",
    "⚠️ 确认回滚": "⚠️ Confirm rollback",
    "回滚失败": "Rollback failed",
    "没有找到可用的备份，无法回滚。": "No usable backup found, cannot roll back.",
    "回滚完成": "Rollback finished",
    "目标实例已恢复到迁移前的状态。":
        "The target instance is back to its pre-migration state.",
    "回滚未能完整完成，详情见日志。\n备份目录仍然保留，可以再次尝试回滚。":
        "The rollback did not complete fully; see the log for details.\nThe backup "
        "folder is kept, so you can try again.",
    "从变更日志提取模组清单": "Extract the mod list from a changelog",
    "请粘贴完整的变更日志文本（包含 'Added mods:' 和 'Updated mods:' 部分）：":
        "Paste the full changelog text (including the 'Added mods:' and 'Updated mods:' "
        "sections):",
    "无结果": "No results",
    "未能提取到模组文件名": "Could not extract any mod file names",
    "未添加任何模组": "No mods were added",
    "发现 Updated mods": "Updated mods found",
    "提取并应用": "Extract and apply",
    "请选择源 config 下的文件夹（可多选）":
        "Pick folders under the source config (multi-select)",
    "选择 config 下的文件夹（可多选）": "Choose folders under config (multi-select)",
    "点击文件夹名即可勾选/取消；点击 ▸ 展开子文件夹；可同时勾选多个。":
        "Click a folder name to tick/untick it; click ▸ to expand subfolders; you can "
        "tick several at once.",
    "📁 文件夹": "📁 Folders",
    "📄 完整相对路径": "📄 Full relative path",
    "请选择源 config 下的文件（可多选）":
        "Pick files under the source config (multi-select)",
    "所选模组已在清单中，未新增。":
        "The selected mods are already in the list; nothing was added.",
    "请选择要一起带走的文件夹（相对整合包根目录，可多选）":
        "Pick folders to carry over (relative to the modpack root, multi-select)",
    "请选择要一起带走的文件夹": "Pick the folders to carry over",
    "请选择要一起带走的文件（可多选，相对整合包根目录）":
        "Pick files to carry over (multi-select, relative to the modpack root)",
    "记住我的选择，以后不再询问": "Remember my choice, don't ask again",
    "📂 打开所在文件夹": "📂 Open containing folder",
    "📋 复制 Mod ID": "📋 Copy Mod ID",
    "📋 复制文件名": "📋 Copy file name",
    "🔗 复制项目链接": "🔗 Copy project link",
    "🌐 打开下载页": "🌐 Open download page",
    "❌ 关闭": "❌ Close",
    "🔄 刷新": "🔄 Refresh",
    "☑ 多选": "☑ Multi-select",
    "⇅ 排序": "⇅ Sort",
    "⚙ 设置": "⚙ Settings",
    "按钮": "Buttons",
    "请先在列表里选中一个按钮。": "Select a button in the list first.",
    "主界面已锁定（操作按钮全部禁用）；下面是执行日志，不用关窗口也能看进度":
        "The main window is locked (all action buttons are disabled); the execution log "
        "is below, so you can follow progress without closing this window",
    "按任意键（或点一下）关闭本界面": "Press any key (or click) to close this screen",
    "2 秒后自动关闭本界面": "Closes itself after 2 seconds",
    "双击间隙诊断": "Double-click gap diagnostic",
    "🖱 双击间隙诊断": "🖱 Double-click gap diagnostic",
    "（没有内容）": "(no content)",
    "日志文件夹不存在，请先执行操作产生日志。":
        "The log folder does not exist; run something first to produce logs.",
    "打开失败": "Could not open",
    "⚠️ 预热": "⚠️ warm-up",
    # —— 上面那批里"拼出来才显示"的模板 ——
    "目标路径不存在：{0}": "Target path does not exist: {0}",
    "源路径不存在：{0}": "Source path does not exist: {0}",
    "⏳ 解析中 ({0}/{1})": "⏳ Parsing ({0}/{1})",
    "扫描过程中发生异常：{0}": "Something went wrong during the scan: {0}",
    "正在执行「{0}」，为避免两个任务同时改动同一批文件，请等它结束后再开始迁移"
    "（模拟运行同样需要等待）。":
        "\"{0}\" is running; to avoid two tasks touching the same files, wait for it to "
        "finish before starting a migration (a dry run has to wait too).",
    "Config 清单中的 '{0}' 包含 '..'，已自动跳过。":
        "Config entry '{0}' contains '..' and was skipped automatically.",
    "目标磁盘剩余空间 {0:.1f} MB，本次迁移约需 {1:.1f} MB（含备份余量）。\n"
    "空间不足，请清理目标磁盘后重试。":
        "The target disk has {0:.1f} MB free; this migration needs about {1:.1f} MB "
        "(including backup headroom).\nNot enough space — free some space on the target "
        "disk and try again.",
    "备份目标实例失败：{0}\n迁移已取消。":
        "Failed to back up the target instance: {0}\nThe migration was cancelled.",
    "即将把目标实例恢复到迁移前的状态，此操作将覆盖当前所有内容！\n\n"
    "目标路径：{0}\n备份路径：{1}\n\n"
    "mods / config / saves 以及「其它文件」清单里复制过的东西都会还原；\n"
    "迁移前不存在的部分会被删掉。\n\n此操作不可撤销！\n确定要继续吗？":
        "This restores the target instance to its pre-migration state and overwrites "
        "everything currently there!\n\nTarget path: {0}\nBackup path: {1}\n\n"
        "mods / config / saves and anything copied from the \"Other files\" list are "
        "restored;\nanything that did not exist before the migration is deleted.\n\n"
        "This cannot be undone!\nContinue?",
    "⚠️ 进行中": "⚠️ In progress",
    # app.py 那几条前面带着**任务名**（busy）：模板里保留了占位符，别写成不带 {0} 的键
    "⚠️ {0}进行中": "⚠️ {0} in progress",
    "{0}任务还在执行，现在退出会中断它，可能导致数据损坏或程序状态异常。\n\n"
    "确定要退出吗？（也可以选“否”，把窗口收进托盘让它跑完）":
        "{0} is still running; quitting now interrupts it and can corrupt data or leave "
        "the app in a bad state.\n\nQuit anyway? (Choosing \"No\" minimises to the tray "
        "so it can finish)",
    "{0}任务正在执行，现在关闭会中断操作，可能导致数据损坏或程序状态异常。\n\n"
    "请等任务结束后再关闭窗口。":
        "{0} is running; closing now interrupts it and can corrupt data or leave the app "
        "in a bad state.\n\nPlease wait for it to finish before closing.",
    "{0}任务正在执行，窗口不能直接关闭。\n\n"
    "点击「是」 → 收进系统托盘，任务在后台继续跑，跑完会弹通知\n"
    "点击「否」 → 返回程序，等任务结束":
        "{0} is running, so the window cannot simply be closed.\n\n"
        "\"Yes\" → minimise to the system tray; the task keeps running in the background "
        "and a notification appears when it is done\n"
        "\"No\" → return to the app and wait for the task",
    "任务还在执行，现在退出会中断它，可能导致数据损坏或程序状态异常。\n\n"
    "确定要退出吗？（也可以选“否”，把窗口收进托盘让它跑完）":
        "A task is still running; quitting now interrupts it and can corrupt data or "
        "leave the app in a bad state.\n\nQuit anyway? (Choosing \"No\" minimises to the "
        "tray so it can finish)",
    "任务正在执行，现在关闭会中断操作，可能导致数据损坏或程序状态异常。\n\n"
    "请等任务结束后再关闭窗口。":
        "A task is running; closing now interrupts it and can corrupt data or leave the "
        "app in a bad state.\n\nPlease wait for it to finish before closing.",
    "任务正在执行，窗口不能直接关闭。\n\n"
    "点击「是」 → 收进系统托盘，任务在后台继续跑，跑完会弹通知\n"
    "点击「否」 → 返回程序，等任务结束":
        "A task is running, so the window cannot simply be closed.\n\n"
        "\"Yes\" → minimise to the system tray; the task keeps running in the background "
        "and a notification appears when it is done\n"
        "\"No\" → return to the app and wait for the task",
    "已提取到 {0} 个 Added 模组，{1} 个 Updated 模组。\n"
    "是否将 Updated 模组也添加到复制清单中？\n\n"
    "点击“是” → 全部添加\n点击“否” → 只添加 Added 模组\n点击“取消” → 不添加任何模组":
        "Extracted {0} added mods and {1} updated mods.\n"
        "Also add the updated mods to the copy list?\n\n"
        "\"Yes\" → add all\n\"No\" → add the added mods only\n\"Cancel\" → add nothing",
    "✅ 已添加 {0} 个模组。": "✅ Added {0} mods.",
    "⚠️ 有 {0} 个非 .jar 文件被跳过。": "⚠️ {0} non-.jar files were skipped.",
    "✅ 已添加 {0} 个 config 条目。": "✅ Added {0} config entries.",
    "✅ 已添加 {0} 个其它文件条目。": "✅ Added {0} \"Other files\" entries.",
    "在下面的方框里，按你平时习惯的速度双击 {0} 次。\n"
    "每次双击之间停一下（程序会自动分对），不用刻意快或慢。":
        "Double-click {0} times in the box below at your normal speed.\nPause between "
        "the double-clicks (the app pairs them up for you); no need to rush or slow down.",
    "已记录 {0} / {1} 次": "{0} / {1} recorded",
    "迁移进行中，暂不能{0}。": "A migration is running, so you cannot {0} yet.",
    "{0} 开着时不能编辑 · 关掉那个窗口即可恢复":
        "Cannot edit while {0} is open · close that window to restore editing",
    "无法打开文件夹：{0}": "Could not open the folder: {0}",
    "无法打开链接：{0}": "Could not open the link: {0}",
    "\n欢迎反馈问题或提交建议。": "\nBug reports and suggestions are welcome.",

    # ---------- 第 6 批：core 产出的"数据词"（分类标签 / 差异状态 / 占位符 / 加载器标注）----------
    # 这些是**数据**（查表、比较都用原文），显示时经控件入口或显示处的 tr() 翻。
    # 占位符（模组元数据读不出来时显示的）
    "未知": "Unknown",
    "无": "None",
    "未知来源": "Unknown source",
    "实例目录名": "instance folder name",
    "文件名推断": "inferred from the file name",
    # 差异扫描的状态词
    "仅存在于源目录": "Only in the source",
    "仅存在于目标（建议保留）": "Only in the target (keep it)",
    "大小变化": "Size changed",
    "modId不同": "Different modId",
    "源更新": "Source is newer",
    # 加载器标注（scanner 写在 type 字段里给界面看的）
    "Fabric(仅文件名)": "Fabric (file name only)",
    "Fabric(占位符)": "Fabric (placeholder)",
    "Fabric(文件名推断)": "Fabric (inferred from file name)",
    "Fabric(未知)": "Fabric (unknown)",
    "Fabric(正则解析)": "Fabric (parsed by pattern)",
    "Forge(占位符)": "Forge (placeholder)",
    "Forge(文件名推断)": "Forge (inferred from file name)",
    # 差异窗口提示气泡里的那些行
    "· 加载器：": "· Loader: ",
    "· 版本：": "· Version: ",
    "· 启动器：": "· Launcher: ",
    "· 原因：": "· Reason: ",
    "· 模组 ": "· Mods ",
    "· 缺失 ": "· Missing ",
    "· 有 saves/ 存档目录": "· Has a saves/ folder",
    "· 无 saves/ 存档目录": "· No saves/ folder",
    # 下载量单位（trp 模板）
    "{0:.1f}万": "{0:.1f}k",
    "{0:.1f}亿": "{0:.1f}M",
    # Modrinth 分类标签（中文站内显示的那套）
    "世界生成": "World generation",
    "优化": "Optimization",
    "冒险": "Adventure",
    "农业": "Farming",
    "前置库": "Library",
    "存储": "Storage",
    "小游戏": "Minigame",
    "建筑": "Building",
    "挑战": "Challenge",
    "整活": "Fun",
    "机制": "Mechanics",
    "生物": "Mobs",
    "画面": "Visuals",
    "社交": "Social",
    "科技": "Technology",
    "管理": "Management",
    "经济": "Economy",
    "装备": "Equipment",
    "辅助": "Utility",
    "运输": "Transport",
    "魔法": "Magic",
    # 「＋ 常用目录」里那些预设的说明
    "光影包（Iris / OptiFine）": "Shaders (Iris / OptiFine)",
    "资源包（材质包）": "Resource packs (texture packs)",
    "投影 / 蓝图（Litematica、WorldEdit）":
        "Schematics / blueprints (Litematica, WorldEdit)",
    "Xaero 世界地图数据": "Xaero world map data",
    "Xaero 路径点": "Xaero waypoints",
    "Xaero 小地图（旧版目录）": "Xaero minimap (legacy folder)",
    "JourneyMap 地图数据": "JourneyMap map data",
    "KubeJS 脚本": "KubeJS scripts",
    "整合包默认配置（Forge / NeoForge）":
        "Modpack default configs (Forge / NeoForge)",
    "本地数据（部分模组自建）": "Local data (created by some mods)",
    "截图": "Screenshots",
    # 标签 chip 的词表（本地关键词推测 _TAG_RULES + 环境标签 TAG_COLORS）
    # ⚠ 这些在代码里是 TAG_COLORS 的**字典键**，改写器按规矩跳过键，所以得手动补
    "客户端": "Client-side",
    "服务端": "Server-side",
    "通用": "Both sides",
    "任务": "Quests",
    "信息显示": "Info display",
    "多人": "Multiplayer",
    "音效": "Audio",
    # 设置页「界面按钮」那棵树：分组名 + 按钮名 + 状态列
    # （这些是 _BUTTON_GROUPS / _BUTTON_LABELS 的词表，整片漏过）
    "路径区（来源）": "Path area (source)",
    "路径区（目标）": "Path area (target)",
    "模组清单区": "Mod list area",
    "Config 清单区": "Config list area",
    "其它文件区": "Other files area",
    "动作按钮区": "Action buttons",
    "执行日志区（左）": "Log area (left)",
    "执行日志区（右）": "Log area (right)",
    "主界面右上角": "Main window top-right",
    "放大查看窗口": "Big view window",
    "日志放大查看": "Log big view",
    "☑ 显示": "☑ Shown",
    "☐ 隐藏": "☐ Hidden",
    "📂 浏览…（来源路径）": "📂 Browse… (source path)",
    "📂 浏览…（目标路径）": "📂 Browse… (target path)",
    "📥 从变更日志导入": "📥 Import from changelog",
    "📂 放大查看（模组清单）": "📂 Big view (mods)",
    "🗑️ 清空清单（模组）": "🗑️ Clear list (mods)",
    "🔎 检查模组是否存在": "🔎 Check mods exist",
    "📂 放大查看（config 清单）": "📂 Big view (config)",
    "🔎 检查 config 是否存在": "🔎 Check config exists",
    "📁 浏览添加文件夹（其它文件）": "📁 Add folder (other files)",
    "📄 浏览添加文件（其它文件）": "📄 Add file (other files)",
    "🔎 检查其它文件是否存在": "🔎 Check other files exist",
    "📂 放大查看（日志）": "📂 Big view (log)",
    "🎨 主题": "🎨 Theme",
    "🗑️ 移出清单 / 删除选中": "🗑️ Remove / delete selected",
    "☑ 多选（Tk）": "☑ Multi-select (Tk)",
    "☑ 全选（Qt）": "☑ Select all (Qt)",
    "⇄ 反选（Qt）": "⇄ Invert (Qt)",
    "⬜ 清空勾选（Qt）": "⬜ Clear ticks (Qt)",
    "🌐 联网搜索（Qt）": "🌐 Online search (Qt)",
    "🗂 卡片 / 表格视图": "🗂 Cards / table view",
    "⇅ 排序（Tk）": "⇅ Sort (Tk)",
    "✖ 关闭（Tk）": "✖ Close (Tk)",
    # 扫描/差异的"备注"（显示在差异窗口的备注列、详情里的来源说明）
    "mods 里的模组元数据（抽查 {0} 个）": "mod metadata in mods ({0} sampled)",
    "模组文件名（{0} 个文件里出现 {1} 次）":
        "mod file name (seen {1} times in {0} files)",
    "版本来自{0}": "version from {0}",
    "加载器来自{0}": "loader from {0}",
    "版本 {0} → {1}": "version {0} → {1}",
    "目标版本更高：{0} → {1}（复制过去会降级，建议保留目标的）":
        "target version is higher: {0} → {1} (copying would be a downgrade; keep the "
        "target one)",
    "服务器列表": "Server list",
    "OptiFine 视频设置": "OptiFine video settings",
    "Iris 光影设置": "Iris shader settings",
    # 其它零星会显示出来的
    "Minecraft 模组": "Minecraft mods",
    "Minecraft 整合包迁移工具": "Minecraft Modpack Migration Tool",
    "Minecraft 整合包迁移工具（后台运行中，点这里打开）":
        "Minecraft Modpack Migration Tool (running in the background — click to open)",
    "增强版 v5": "Enhanced v5",
    "选择文件夹（可多选）": "Choose folders (multi-select)",
    # 锁屏方式 / 关闭方式那几个卡片（画布上自绘的标题与说明）
    "迁移时锁定界面": "Lock the interface while migrating",
    "盖一层遮罩（正式迁移和模拟运行都锁）":
        "Draw an overlay (both a real migration and a dry run)",
    "只锁正式迁移": "Lock real migrations only",
    "模拟运行不盖遮罩（按钮照样禁用）":
        "No overlay for a dry run (buttons are still disabled)",
    "不锁屏": "No lock screen",
    "不盖遮罩，只把按钮全部禁用": "No overlay, just disable all the buttons",
    "收进系统托盘": "Minimise to the system tray",
    "程序继续在后台跑": "the app keeps running in the background",
    "直接退出程序": "Quit the app",
    "每次问我": "Ask me every time",
    "PySide6 窗口": "PySide6 window",
    "PySide6 试点窗口": "PySide6 preview window",
    "2 秒后自动关闭": "Closes itself after 2 seconds",
    "<无法显示的文本>": "<text that cannot be displayed>",
    "读取错误: ": "Read error: ",
    "搜索请求失败: ": "Search request failed: ",
    "还没填 Key": "No key set yet",
    # ---------- 补齐：英文同步（迁移档案 / 回滚 / 消息中心 / 设置 / 提醒条） ----------
    "🗂️ 已保存迁移档案: {0}":
        "🗂️ Migration record saved: {0}",
    "⚠️ 保存迁移档案失败: {0}":
        "⚠️ Could not save the migration record: {0}",
    "迁移历史":
        "Migration history",
    "📦 迁移历史":
        "📦 Migration history",
    "⤢  放大":
        "⤢  Enlarge",
    "❌ 回滚失败：这份档案没有自己的备份（{0}）":
        "❌ Roll-back failed: this record has no backup of its own ({0})",
    "❌ 回滚过程中出现异常：{0}":
        "❌ Roll-back raised an error: {0}",
    "🌐  选择界面语言 / Choose your language":
        "🌐  Choose your language",
    "以后可以随时在「设置 → 🎨 外观与启动」里改":
        "You can change this later in Settings → 🎨 Appearance & startup",
    "消息中心":
        "Message center",
    "还没有消息":
        "No messages yet",
    "操作提示会记在这里，吐司消失后也能回看":
        "Notices are collected here, so you can look back after a toast disappears",
    "灰字为上次运行留下":
        "Grey text is left over from the previous run",
    "清空":
        "Clear",
    "{0} 条":
        "{0}",
    "点消息中心以外就收回":
        "Close the message center when clicking outside",
    "关闭后只有 ✕ / Esc / 再点铃铛能收":
        "Once off, only ✕ / Esc / clicking the bell again can close it",
    "消息中心：最近的提示消息（Ctrl+M）":
        "Message center: recent notices (Ctrl+M)",
    "⚠️ 模组清单有问题：缺 {0} 项 / 重复 {1} 行":
        "⚠️ Mod list has issues: {0} missing / {1} duplicate lines",
    "✅ 模组清单检查完成：{0} 项全部存在":
        "✅ Mod list check done: all {0} entries exist",
    "⚠️ 其它文件清单有问题：缺 {0} 项 / 重复 {1} 行":
        "⚠️ Extra-files list has issues: {0} missing / {1} duplicate lines",
    "✅ 其它文件清单检查完成：{0} 项全部存在":
        "✅ Extra-files list check done: all {0} entries exist",
    "⚠️ config 清单有问题：缺 {0} 项 / 重复 {1} 行":
        "⚠️ Config list has issues: {0} missing / {1} duplicate lines",
    "✅ config 清单检查完成：{0} 项全部存在":
        "✅ Config list check done: all {0} entries exist",
    "● 正在检测网络…":
        "● Checking network…",
    "在主窗口内显示延迟浮标（点浮标打开曲线图，可拖动）":
        "Show the latency widget inside the main window (click it for the chart, drag to move)",
    "🗂 已打开 Qt 放大查看：{0}（{1} 项）":
        "🗂 Opened the Qt detail view: {0} ({1} items)",
    "🗂 已打开 Qt 差异窗口（{0} 项）":
        "🗂 Opened the Qt diff window ({0} items)",
    "\nMC百科：中文模组资料库　·　PCL：整合包启动器　·　欢迎反馈问题或提交建议。":
        "\nMCMod: a Chinese mod database　·　PCL: a modpack launcher　·　Feedback and suggestions are welcome.",
    "✨ 贴边特效已{0}":
        "✨ Edge effect {0}",
    "🖱️ 平滑滚动速度已设为 {0} 档":
        "🖱️ Smooth scrolling speed set to level {0}",
    "🗂️ 迁移历史最多保留 {0} 份":
        "🗂️ Keep at most {0} migration records",
    "同名文件：{0}":
        "Same-name files: {0}",
    "跳过":
        "Skip",
    "迁移前确认已{0}":
        "Pre-migration confirmation {0}",
    "点空白取消焦点已{0}":
        "Unfocus-on-empty-click {0}",
    "点外部收回消息中心已{0}":
        "Close message center on outside click {0}",
    "点空白处取消清单文本框焦点":
        "Click empty space to unfocus the list box",
    "这条提醒不能关闭，只能收起成一个 ⚠ 图标":
        "This notice cannot be closed; it can only collapse into a ⚠ icon",
    "复制 AI 提示词":
        "Copy AI prompt",
    "收起 ▴":
        "Collapse ▴",
    # ---------- 补齐：英文同步（第二轮） ----------
    "整合包更新时，作者可能会删除或替换模组 —— 这种情况下「扫描差异」只能比出文件名和版本的不同，被作者删掉的模组是发现不了的。\n请务必核对更新日志里的模组变更；也可以把更新日志交给 AI 帮你逐条分析。":
        "When the pack is updated, the author may delete or replace mods. In that case “Scan differences” can only compare file names and versions; mods the author removed are invisible to it.\nAlways check the changelog for mod changes; you can also hand the changelog to an AI and have it go through the changes one by one.",
    "贴边时显示边缘特效（绿色光带 + 粒子）":
        "Show an edge effect while docked (green glow + particles)",
    "📚 MC百科":
        "📚 MCMod",
    "🚀 PCL 启动器":
        "🚀 PCL launcher",
    "先不滚":
        "Not yet",
    # ---------- 补齐：搜索窗/版本提示 ----------
    "自动识别自 {0}":
        "Auto-detected from {0}",
    "实例":
        "instance",
    "没认出你的实例版本，可手动选":
        "Could not detect your instance version; pick one manually",
    "无 {0} 的版本":
        "No version for {0}",
    "没有可用版本":
        "No versions available",
    # ---------- 补齐：迁移结果状态 ----------
    "成功":
        "Succeeded",
    "失败":
        "Failed",
    "已取消":
        "Cancelled",
    "进行中":
        "Running",
    # ---------- 补齐：日志整句化（子智能体产出） ----------
    "模拟复制":
        "Simulated copy",
    "目标已存在，跳过":
        "Already exists in the target, skipping",
    "复制成功":
        "Copied successfully",
    "权限不足：无法写入 {0}":
        "Permission denied: cannot write to {0}",
    "系统错误：{0}":
        "System error: {0}",
    "未知错误：{0}":
        "Unknown error: {0}",
    "模拟复制目录":
        "Simulated folder copy",
    "已备份原有目录至：{0}":
        "Backed up the original folder to: {0}",
    "备份失败：{0}":
        "Backup failed: {0}",
    "目录复制成功":
        "Folder copied successfully",
    "复制目录失败：{0}":
        "Failed to copy the folder: {0}",
    "📦 开始备份目标实例...（回滚点：{0}）":
        "📦 Starting to back up the target instance... (rollback point: {0})",
    "📦 开始备份目标实例...":
        "📦 Starting to back up the target instance...",
    "📂 备份 {0} → {1}":
        "📂 Backing up {0} → {1}",
    "ℹ️ {0} 不存在，跳过备份（回滚时会删掉迁移新建的同名目录）":
        "ℹ️ {0} does not exist; skipping its backup (a rollback deletes the same-named folder created by the migration)",
    "ℹ️ {0} 目标里不存在，跳过备份（回滚时会删掉迁移新建的同名条目）":
        "ℹ️ {0} does not exist in the target; skipping its backup (a rollback deletes the same-named entry created by the migration)",
    "📂 备份清单条目 {0} → {1}":
        "📂 Backing up list entry {0} → {1}",
    "备份「其它文件」条目 {0} 失败：{1}":
        "Failed to back up the other-files entry {0}: {1}",
    "🗑️ 替换旧备份（新备份已完整写入）":
        "🗑️ Replacing the old backup (the new backup is fully written)",
    "✅ 备份完成，已备份：{0}":
        "✅ Backup complete; backed up: {0}",
    "❌ 恢复失败：备份目录不存在":
        "❌ Restore failed: the backup folder does not exist",
    "⚠️ 这份备份没有清单（旧版本留下的）：只恢复已有内容，不会清理迁移新建的部分":
        "⚠️ This backup has no manifest (left by an older version): only existing content is restored; anything created by the migration is not cleaned up",
    "🔄 开始从备份恢复...":
        "🔄 Starting the restore from the backup...",
    "📂 恢复备份：{0} → {1}":
        "📂 Restoring from the backup: {0} → {1}",
    "🗑️ 迁移新建的 {0}/，按回滚要求删除":
        "🗑️ Deleting {0}/ created by the migration, as the rollback requires",
    "{0}: 备份不完整（清单说存在，备份里没有）":
        "{0}: the backup is incomplete (the manifest says it existed, but it is missing from the backup)",
    "❌ {0}/ 在清单里标记为存在，但备份里找不到":
        "❌ {0}/ is marked as existing in the manifest, but is missing from the backup",
    "ℹ️ 备份中不存在 {0}，跳过":
        "ℹ️ {0} is not in the backup, skipping",
    "❌ 恢复 {0} 失败: {1}":
        "❌ Failed to restore {0}: {1}",
    "📂 恢复文件：{0} → {1}":
        "📂 Restoring file: {0} → {1}",
    "🗑️ 迁移新建的 {0}，按回滚要求删除":
        "🗑️ Deleting {0} created by the migration, as the rollback requires",
    "❌ {0} 在清单里标记为存在，但备份里找不到":
        "❌ {0} is marked as existing in the manifest, but is missing from the backup",
    "📂 恢复清单条目：{0} → {1}":
        "📂 Restoring list entry: {0} → {1}",
    "🗑️ 迁移新建的清单条目 {0}/，按回滚要求删除":
        "🗑️ Deleting the list entry {0}/ created by the migration, as the rollback requires",
    "🗑️ 迁移新建的清单条目 {0}，按回滚要求删除":
        "🗑️ Deleting the list entry {0} created by the migration, as the rollback requires",
    "❌ 清单条目 {0} 标记为存在，但备份里找不到":
        "❌ The list entry {0} is marked as existing, but is missing from the backup",
    "❌ 恢复清单条目 {0} 失败: {1}":
        "❌ Failed to restore the list entry {0}: {1}",
    "❌ 恢复未完成，失败 {0} 项：{1}":
        "❌ Restore did not finish; {0} items failed: {1}",
    "✅ 恢复完成，已恢复 {0} 项":
        "✅ Restore complete; {0} items restored",
    "✅ 恢复完成，已恢复 {0} 项：{1}":
        "✅ Restore complete; {0} items restored: {1}",
    "✅ 恢复完成，已恢复 {0} 项，清理迁移新建的 {1} 项":
        "✅ Restore complete; {0} items restored, {1} items created by the migration cleaned up",
    "✅ 恢复完成，已恢复 {0} 项，清理迁移新建的 {1} 项：{2}":
        "✅ Restore complete; {0} items restored, {1} items created by the migration cleaned up: {2}",
    "ℹ️ 无法识别 {0} 的 modid，跳过旧版本清理（如目标里已存在同模组的其他版本，请手动确认）":
        "ℹ️ Cannot read the modid of {0}; skipping the old-version cleanup (if another version of the same mod is already in the target, please check manually)",
    "🗑️ 移除目标旧版本：{0}（同 modid: {1}），已移入备份 .migrate_backup/removed_mods":
        "🗑️ Removed an older version from the target: {0} (same modid: {1}); moved into the backup .migrate_backup/removed_mods",
    "⚠️ 移除旧版本 {0} 失败：{1}（该文件保持原样）":
        "⚠️ Failed to remove the older version {0}: {1} (the file is left as it is)",
    "🏷️ 已启用迁移标记：复制过去的模组会加前缀「{0} 」":
        "🏷️ Migration marker on: copied mods get the prefix \"{0} \"",
    "未找到匹配的文件":
        "No matching file found",
    "复制模组 ({0}/{1})":
        "Copying mods ({0}/{1})",
    "复制存档 ({0}/{1})":
        "Copying saves ({0}/{1})",
    "存档/{0}":
        "saves/{0}",
    "源不存在":
        "Source does not exist",
    "复制 config ({0}/{1})":
        "Copying config ({0}/{1})",
    "创建目录失败: {0}":
        "Failed to create the folder: {0}",
    "建目录失败: {0}":
        "Failed to create the folder: {0}",
    "复制其它文件 ({0}/{1})":
        "Copying other files ({0}/{1})",
    "其它/{0}":
        "Other/{0}",
    "实际":
        "Live",
    "模组 {0}　·　config {1}　·　其它 {2}":
        "{0} mods · {1} config · {2} other",
    "耗时 {0} 秒　·　{1}":
        "Took {0}s · {1}",
    "共 {0} 份档案　·　最多保存 {1} 份（可在设置里调整）　·　{2}":
        "{0} records · up to {1} kept (adjustable in Settings) · {2}",
    " 个":
        " items",
    "　跳过 ":
        " · skipped ",
    "　失败 ":
        " · failed ",
    "源目录":
        "Source folder",
    "目标目录":
        "Target folder",
    "模组":
        "Mods",
    "其它":
        "Other",
    "耗时":
        "Duration",
    "⤡  还原":
        "⤡  Restore",
    "没有档案":
        "No records",
    "模拟运行":
        "Dry run",
    "实际迁移":
        "Real migration",
    "{0}　·　{1}":
        "{0} · {1}",
    "{0} 秒":
        "{0}s",
    "{0} 条（详见日志）":
        "{0} entries (see log)",
    "（这份档案没有日志）":
        "(this record has no log)",
    "删除档案":
        "Delete record",
    "确定删除 {0} 这份档案吗？\n（只删记录，不影响任何游戏文件）":
        "Delete the record {0}?\n(only the record is removed — no game files are touched)",
    "已删除":
        "Deleted",
    "要把目标实例恢复到「{0}」这次迁移之前吗？":
        "Restore the target instance to how it was before the “{0}” migration?",
    "会用备份覆盖当前内容：mods / config / saves 以及「其它文件」清单里复制过的部分都会还原，迁移前不存在的东西会被删掉。":
        "The backup will overwrite the current contents: mods / config / saves and everything copied from the “Other files” list will be restored, and anything that did not exist before the migration will be deleted.",
    "此操作不可撤销。回滚过程看主界面的执行日志。":
        "This cannot be undone. Watch the main window log for rollback progress.",
    "这条记录没有自己的备份":
        "This record has no backup of its own",
    "删除这份档案":
        "Delete this record",
    "这条迁移记录没有可用的备份，回滚不了。\n（早期版本的迁移不会按记录留备份）":
        "This migration record has no usable backup, so it cannot be rolled back.\n(older versions did not keep a per-record backup)",
    "❌ 回滚收尾时出错（备份仍保留，可以重新执行回滚）":
        "❌ Roll-back finalisation failed (the backup is kept — you can roll back again)",
    "目标实例已恢复到迁移前的状态":
        "The target instance is back to its pre-migration state",
    "任务已结束，点托盘图标打开主界面查看日志":
        "Task finished — click the tray icon to open the main window and check the log",
    "扫描出错：{0}":
        "Scan error: {0}",
    "两个 mods 目录完全一致，没有差异":
        "The two mods folders are identical — no differences",
    "发现 {0} 项差异，点托盘图标查看":
        "Found {0} differences — click the tray icon to view them",
    "正在执行迁移任务":
        "Running a migration",
    "正在执行迁移任务（模拟运行）":
        "Running a migration (dry run)",
    "后台静默执行已结束，点托盘图标查看日志":
        "Silent background run finished — click the tray icon to check the log",
    "❌ 打开配置文件失败，详见日志":
        "❌ Failed to open the config file; see the log for details",
    "🔔 消息提示已关闭（右下角不再弹卡片）":
        "🔔 Notifications turned off (no more cards pop up in the bottom-right corner)",
    "🔔 消息提示已开启":
        "🔔 Notifications turned on",
    "⌨️ 迁移完成后的收尾方式已改为：按任意键关闭":
        "⌨️ Migration finish behavior changed to: close on any key",
    "⌨️ 迁移完成后的收尾方式已改为：2 秒后自动关闭":
        "⌨️ Migration finish behavior changed to: close automatically after 2 seconds",
    "📦 原先勾选的「默认携带目录」已并入「其它文件」清单（{0} 项：{1}）。以后**清单就是唯一依据** —— 从清单里删掉就不会再带。":
        "📦 The previously checked “Default carried folders” are now merged into the “Other files” list ({0} item(s): {1}). From now on **the list is the only source** — remove an entry from the list and it will no longer be carried.",
    "都在清单里了":
        "all already in the list",
    "🅆 PySide6 已启用":
        "🅆 PySide6 enabled",
    "🅆 PySide6 已关闭（纯 Tk 模式，重启后生效）":
        "🅆 PySide6 disabled (pure Tk mode, takes effect after a restart)",
    "🗂 放大查看窗口已切换为：PySide6 试点窗口":
        "🗂 Big view window switched to: PySide6 preview window",
    "🗂 放大查看窗口已切换为：经典 Tk 窗口":
        "🗂 Big view window switched to: Classic Tk window",
    "🧩 差异窗口已切换为：PySide6 窗口":
        "🧩 Diff window switched to: PySide6 window",
    "🧩 差异窗口已切换为：经典 Tk 窗口":
        "🧩 Diff window switched to: Classic Tk window",
    "🗂 放大查看窗口默认视图：卡片视图":
        "🗂 Big view default view: Card view",
    "🗂 放大查看窗口默认视图：表格视图":
        "🗂 Big view default view: Table view",
    "🧩 模组差异窗口默认视图：卡片视图":
        "🧩 Mod diff default view: Card view",
    "🧩 模组差异窗口默认视图：列表视图":
        "🧩 Mod diff default view: List view",
    "读取失败":
        "Read failed",
    "CurseForge：":
        "CurseForge: ",
    "🔑 CurseForge API Key 已保存：{0}（指纹 {1}）":
        "🔑 CurseForge API key saved: {0} (fingerprint {1})",
    "🔑 CurseForge API Key 已保存":
        "🔑 CurseForge API key saved",
    "❌ CurseForge API Key 保存失败：写不了 {0}":
        "❌ Failed to save the CurseForge API key: cannot write {0}",
    "❌ CurseForge API Key 保存失败，详见日志":
        "❌ Failed to save the CurseForge API key; see the log for details",
    "✅ CurseForge Key 可用：{0}":
        "✅ CurseForge key works: {0}",
    "❌ CurseForge Key 不可用：{0}":
        "❌ CurseForge key does not work: {0}",
    "✅ CurseForge Key 可用":
        "✅ CurseForge key works",
    "❌ CurseForge Key 不可用，详见日志":
        "❌ CurseForge key does not work; see the log for details",
    "❌ 起不了测试线程：{0}":
        "❌ Could not start the test thread: {0}",
    "❌ 起不了测试线程，详见日志":
        "❌ Could not start the test thread; see the log for details",
    "🧪 模拟运行已开启：只显示将要执行的操作，不会修改任何文件":
        "🧪 Dry run turned on: only the operations that would run are shown; no files are changed",
    "🧪 模拟运行已关闭：接下来的迁移会真正写入文件":
        "🧪 Dry run turned off: the next migration will actually write files",
    "🔁 覆盖已存在的模组已开启：同名文件会被覆盖":
        "🔁 Overwrite existing mods turned on: files with the same name will be overwritten",
    "🔁 覆盖已存在的模组已关闭：同名文件会被跳过":
        "🔁 Overwrite existing mods turned off: files with the same name will be skipped",
    "收进托盘继续运行":
        "Keep running in the tray",
    "直接退出":
        "Quit",
    "源":
        "Source",
    "目标":
        "Target",
    "存档":
        "Save",
    "要复制":
        "Copying",
    "同名文件":
        "Same-named files",
    "模组详情 - {0}":
        "Mod details - {0}",
    "（分类来自 Modrinth）":
        "(Categories from Modrinth)",
    "（分类为关键词推测）":
        "(Categories guessed from keywords)",
    "作者":
        "Author",
    "依赖":
        "Dependencies",
    "已在资源管理器中定位该文件。":
        "Revealed the file in Explorer.",
    "按 {0} 没搜到东西 —— 可能是版本认错了，已自动放宽版本再搜一次。":
        "Nothing found for {0} — the version may be wrong, so the version filter was widened and the search re-run.",
    "没有找到相关模组，换个关键词试试。":
        "No matching mods found — try another keyword.",
    "🟧 CurseForge {0} 条 · 🟩 Modrinth {1} 条 ｜ ":
        "🟧 CurseForge {0} · 🟩 Modrinth {1} ｜ ",
    "找到 {0} 个结果。{1} 版本/下载链接加载中…（🔵蓝=最相似, 🟡黄=可更新）":
        "Found {0} results. {1} Loading versions and download links… (🔵 blue = best match, 🟡 yellow = update available)",
    "Modrinth：{0}":
        "Modrinth: {0}",
    "CurseForge：{0}":
        "CurseForge: {0}",
    "没搜到结果":
        "Nothing found",
    "请先选中一条结果（需要它带下载链接）。":
        "Select a result first (it needs a download link).",
    "已复制下载链接到剪贴板。":
        "Download link copied to the clipboard.",
    "已复制项目链接到剪贴板。":
        "Project link copied to the clipboard.",
    "请先选中一条结果。":
        "Select a result first.",
    "version.json（{0}）":
        "version.json ({0})",
    "⚠️ 源目录里没有 {0}，没加进清单":
        "⚠️ {0} isn't in the source folder, so it wasn't added to the list",
    "✅ 已把 {0} 加入「其它文件」清单，迁移时会一起带过去（{1}）":
        "✅ Added {0} to the Other files list; it will be carried over during migration ({1})",
    "✅ 已把 {0} 加入「其它文件」清单，迁移时会一起带过去":
        "✅ Added {0} to the Other files list; it will be carried over during migration",
    "ℹ️ {0} 已经在清单里了":
        "ℹ️ {0} is already in the list",
    "　✅ 已在清单":
        "　✅ Already in the list",
    "检查模组是否存在":
        "check whether the mods exist",
    "⚠️ 有 {0} 项未添加（不在源 config 目录下或不安全）：":
        "⚠️ {0} item(s) were not added (not under the source config folder, or unsafe):",
    "全选顶层":
        "Select top level",
    "全不选":
        "Select none",
    "确定添加":
        "Add selected",
    "尚未设置源整合包实例根目录":
        "The source modpack instance root isn't set yet",
    "基准目录不存在：{0}":
        "The base folder doesn't exist: {0}",
    "尚未设置源实例根目录":
        "The source instance root isn't set yet",
    "🗑 模组清单已清空":
        "🗑 Mod list cleared",
    "🗑 config 清单已清空":
        "🗑 Config list cleared",
    "⚠️ 有 {0} 项未添加（不在整合包目录下或不安全）：":
        "⚠️ {0} item(s) were not added (not under the modpack folder, or unsafe):",
    "🗑 其它文件清单已清空":
        "🗑 Other files list cleared",
    "⏳ 打开中…":
        "⏳ Opening…",
    "⚠ {0}，本次用经典窗口打开":
        "⚠ {0}; opening in the classic window this time",
    "⚠️ Qt 不可用，已用经典窗口打开":
        "⚠️ Qt isn't available; opened in the classic window",
    "模组清单":
        "Mod list",
    "Config清单":
        "Config list",
    "大窗口查看 - {0}":
        "Large view - {0}",
    "显示 {0}/{1} 项":
        "Showing {0}/{1}",
    "共 {0} 项":
        "Total {0}",
    "所有文件":
        "All files",
    "检测模组是否存在":
        "check whether the mods exist",
    "已选 {0} / {1} 项":
        "Selected {0} / {1}",
    "🗂 已切到卡片视图（只读预览）":
        "🗂 Switched to card view (read-only preview)",
    "⚠️ 卡片视图不可用，已切回表格视图，详见日志":
        "⚠️ Card view isn't available; switched back to the table view — see the log for details",
    "按名称":
        "By name",
    "按状态":
        "By status",
    "按版本":
        "By version",
    "按 Mod ID":
        "By mod ID",
    "按大小":
        "By size",
    "正在检测网络连通性…":
        "Checking network connectivity…",
    "⚠ 更新提醒":
        "⚠ Update notice",
    "📈 网络延迟（Modrinth / CurseForge 分开统计）":
        "📈 Network latency (Modrinth / CurseForge tracked separately)",
    "绘图需要 matplotlib：\n\npip install matplotlib\n\n({0})":
        "Drawing requires matplotlib:\n\npip install matplotlib\n\n({0})",
    "秒（先填满，再滚动）":
        "Seconds (fills up first, then scrolls)",
    "{0} 无数据":
        "{0}: no data",
    "{0} 当前 {1} ms":
        "{0}: now {1} ms",
    "{0} 当前 {1} · 平均 {2} · 最高 {3}":
        "{0}: now {1} · avg {2} · max {3}",
    "📌 桌面浮标已关闭":
        "📌 Desktop overlay closed",
    "📌 桌面浮标已显示（可拖动，双击看曲线）":
        "📌 Desktop overlay shown (drag it; double-click for the chart)",
    "❌ 切换浮标失败：{0}":
        "❌ Failed to toggle the overlay: {0}",
    "⚠️ 浮标打不开，详见日志":
        "⚠️ The overlay can't be opened — see the log for details",
    "整合包更新提醒":
        "Modpack update notice",
    "「扫描差异」只比对两件事：清单里写的文件名，和目标目录里实际存在的文件。\n\n作者更新整合包时如果删掉或替换了某个模组，我们的清单里本来就没有它 —— 扫出来是「没有差异」，实际却少了东西。\n\n建议的做法：\n① 打开整合包的更新日志，逐条核对模组变更；\n② 或者点下面的「复制 AI 提示词」，把提示词连同更新日志一起交给 AI 逐条分析。":
        "「Scan for differences」only compares two things: the file names written in the lists, and the files that actually exist in the target folder.\n\nIf the author deletes or replaces a mod when updating the modpack, it was never in our list to begin with — so the scan reports 「no differences」even though something is missing.\n\nSuggested approach:\n① Open the modpack's changelog and check each mod change;\n② Or click 「Copy AI prompt」below and hand the prompt plus the changelog to an AI to analyze item by item.",
    "我在把一个 Minecraft 整合包从旧版本迁移到新版本，需要你帮我从下面的更新日志里找出【与模组相关的变更】。\n\n整合包：{0}\n旧实例目录：{1}\n新实例目录：{2}\n\n请按这个格式输出：\n1. 新增的模组（尽量给出 modid 或中文名）\n2. 移除的模组\n3. 被替换/合并的模组（A 换成了 B）\n4. 依赖或前置发生变化的模组\n5. 需要我手动处理的注意点（配置重置、存档兼容性、存档版本）\n\n更新日志如下：\n（把更新日志粘贴在这里）\n":
        "I'm migrating a Minecraft modpack from an old version to a new one, and I need you to find the mod-related changes in the changelog below.\n\nModpack: {0}\nOld instance folder: {1}\nNew instance folder: {2}\n\nPlease output in this format:\n1. Added mods (give the modid or English name if possible)\n2. Removed mods\n3. Replaced/merged mods (A replaced by B)\n4. Mods whose dependencies or prerequisites changed\n5. Points I need to handle manually (config resets, save compatibility, save version)\n\nThe changelog is below:\n(paste the changelog here)\n",
    "整合包":
        "modpack",
    "已复制 ✓":
        "Copied ✓",
    "展开成横幅":
        "Expand into banner",
    "外观与启动":
        "Appearance & startup",
    "迁移行为":
        "Migration behavior",
    "模组分类标签":
        "Mod categories",
    "任务与锁定":
        "Tasks & locking",
    "默认携带的目录":
        "Directories carried by default",
    "界面按钮（勾选显示 / 上下调整顺序）":
        "Interface buttons (check to show / reorder)",
    "关闭与后台":
        "Closing & background",
    "快捷链接":
        "Quick links",
    "🗂 PySide6 试点窗口（圆角卡片 + 原生动画，需已安装 PySide6）":
        "🗂 PySide6 trial window（Rounded cards + native animation; requires PySide6）",
    "🪟 经典 Tk 窗口（无需额外依赖）":
        "🪟 Classic Tk window（No extra dependencies）",
    "📋 表格视图（打开就是列表，能改勾选/编辑）":
        "📋 Table view（Opens as a list; you can check/edit）",
    "🗂 卡片视图（打开就是卡片，只读预览）":
        "🗂 Card view（Opens as cards; read-only preview）",
    "📋 列表视图（一行一条差异，信息密度高）":
        "📋 List view（One difference per row; dense with information）",
    "🗂 卡片视图（带图标的大卡片，看得清描述）":
        "🗂 Card view（Large cards with icons; descriptions are easy to read）",
    "config 清单":
        "Config list",
    "ℹ️ 「{0}」已经不在清单里了":
        "ℹ️ 「{0}」is no longer in the list",
    "--- 新日志记录 ({0}) ---":
        "--- New log session ({0}) ---",
    "\n===== 启动 {0} =====\n":
        "\n===== Started {0} =====\n",
    "✅ {0}完成":
        "✅ {0} done",
    "点托盘图标打开主界面查看":
        "Click the tray icon to open the main window",
    "已收进系统托盘":
        "Minimized to system tray",
    "程序仍在后台运行。点托盘图标可重新打开，右键可退出。":
        "It is still running in the background. Click the tray icon to reopen, or right-click to quit.",
    "{0}（已自动重试 {1} 次仍然失败）":
        "{0} (still failed after {1} automatic retries)",
    "{0} 失败":
        "{0} failed",
    "未设置 CurseForge API Key":
        "CurseForge API key is not set",
    "CurseForge 拒绝了这个 API Key（HTTP {0}）":
        "CurseForge rejected this API key (HTTP {0})",
    "CurseForge 上没有这个项目（HTTP 404）":
        "This project does not exist on CurseForge (HTTP 404)",
    "CurseForge 请求失败（HTTP {0}）":
        "CurseForge request failed (HTTP {0})",
    "Key 可用（指纹 {0}）":
        "Key works (fingerprint {0})",
    "正在构建界面…":
        "Building the interface…",
    "正在应用主题…":
        "Applying the theme…",
    "正在载入清单…":
        "Loading lists…",
    "正在检查实例路径…":
        "Checking instance paths…",
    "就绪":
        "Ready",
    "{0}：{1}":
        "{0}: {1}",
    "通":
        "Reachable",
    "不通":
        "Unreachable",
    "网络通畅\n":
        "Network is fine\n",
    "📌 已隐藏该按钮":
        "📌 Button hidden",
    "📌 已显示该按钮":
        "📌 Button shown",
    "⚠️ 已经到{0}了":
        "⚠️ Already at the {0}",
    "最前":
        "front",
    "最后":
        "end",
    "🔼 已上移":
        "🔼 Moved up",
    "🔽 已下移":
        "🔽 Moved down",
    "ℹ️ {0} 打开期间，主界面编辑已关闭（清单两边同时改会互相盖）。":
        "ℹ️ While {0} is open, editing in the main window is off (edits on both sides would overwrite each other).",
    "模组差异":
        "Mod differences",
    "qt 窗口关闭 {0}":
        "Qt window closed {0}",
    "打开 Qt 放大查看（子进程）{0} rows={1}":
        "Open Qt big view (subprocess) {0} rows={1}",
    "打开 Qt 差异窗口（子进程）rows={0}":
        "Open Qt diff window (subprocess) rows={0}",
    "⚠ Qt 子进程起不来，已改用进程内 Tk 差异窗口":
        "⚠ Qt subprocess failed to start; switched to the in-process Tk diff window",
    "⚠ 无法启动 Qt 子进程，详见日志":
        "⚠ Cannot start the Qt subprocess; see the log",
    "⚠ Qt 窗口进程异常退出（{0}）{1}":
        "⚠ Qt window process exited unexpectedly ({0}) {1}",
    "⚠ Qt 窗口更新清单失败：{0}":
        "⚠ Failed to update lists in the Qt window: {0}",
    "Qt 窗口主题已切换 kind={0} bg={1}":
        "Qt window theme switched kind={0} bg={1}",
    "⚠ Qt 窗口换主题失败：{0}":
        "⚠ Failed to switch the theme of the Qt window: {0}",
    "打开曲线图":
        "Open chart",
    "复位位置":
        "Reset position",
    "隐藏浮标":
        "Hide widget",
    "加载 PySide6 失败：{0}":
        "Failed to load PySide6: {0}",
    "清单":
        "List",
    "建窗失败：\n":
        "Failed to create the window:\n",
    "系统托盘需要 pywin32：pip install pywin32":
        "The system tray requires pywin32: pip install pywin32",
    "主线程卡住 {0:.2f} 秒 ｜ {1}\n":
        "Main thread stuck for {0:.2f} s ｜ {1}\n",
    "窗口标题：{0}\n":
        "Window title: {0}\n",
    "\n---- 启动 {0} ｜ 构建 {1} ----\n":
        "\n---- Started {0} ｜ Build {1} ----\n",
    "SmoothScroller: {0} 抛异常，这段平滑滚动已停掉并还回定时器精度\n":
        "SmoothScroller: {0} raised an exception; this smooth-scroll run stopped and the timer precision was restored\n",
    "!! {0} 抛异常: {1}":
        "!! {0} raised an exception: {1}",
    "请求失败（细节已省略）":
        "Request failed (details omitted)",
    "存在 {0}":
        "exists {0}",
    "· 缺失 {0}":
        "· missing {0}",
    "其它文件":
        "Other files",
    "🌐 界面语言已切换为「{0}」，立即生效":
        "🌐 UI language switched to \"{0}\" — applied immediately",
    "⚠️ 只认 .jar 文件（收到 {0} 个其它文件）":
        "⚠️ Only .jar files are accepted ({0} other files dropped)",
    "⚠️ 这几个模组已经在清单里了（{0} 个）":
        "⚠️ These mods are already in the list ({0} of them)",
    "⚠️ 文件夹里没找到 .jar（{0} 个文件夹）":
        "⚠️ No .jar found in the folder(s) ({0} folders)",
    "📂 从文件夹里加了 {0} 个模组":
        "📂 Added {0} mods from the folder",
    "　⚠ 拖放不可用（缺少 tkinterdnd2），请用「添加模组」按钮。":
        "  ⚠ Drag & drop unavailable (tkinterdnd2 missing) — use the \"Add mods\" button.",
    "　⚠ 拖放不可用（缺少 tkinterdnd2），请用「添加模组」按钮。":
        "　⚠ Drag-and-drop unavailable (tkinterdnd2 missing) — use the \"Add mods\" button.",
    "⚠️ 模拟中...":
        "⚠️ Dry run…",
    "⚠️ 迁移中...":
        "⚠️ Migrating…",
    "⚠️ 等待中...":
        "⚠️ Waiting…",
    "⚠️ 等待中...":
        "⚠️ Waiting…",
    "🔁 应用上次记录":
        "🔁 Apply last record",
    "🔁 已回填上次的迁移记录":
        "🔁 Last migration record applied",
    "🗂 还没有迁移记录，先跑一次迁移":
        "🗂 No migration records yet — run one first",
    "⚠ 最近那份记录里没有清单内容":
        "⚠ The latest record has no list content",
    "🔁 已应用上一次的迁移记录（模组 {0} / config {1} / 其它 {2}）":
        "🔁 Applied the last migration record (mods {0} / config {1} / other {2})",
    "⚠ 回填上次记录失败：%s":
        "⚠ Could not apply the last record: %s",
    "🔁 应用到主界面":
        "🔁 Apply to main window",
    "🔁 已应用{0}（模组 {1} / config {2} / 其它 {3}）":
        "🔁 Applied {0} (mods {1} / config {2} / other {3})",
    "🔁 已回填{0}":
        "🔁 Filled in {0}",
    "⚠ 这份档案读不出来":
        "⚠ This record cannot be read",
    "⚠ 这份记录里没有清单内容":
        "⚠ This record has no list content",
    "历史里选中的那份档案":
        "the record selected in history",
}


_原键 = "_i18n_原文"          # 挂在控件上的：{属性名: 中文原文}
_重翻中 = False                # 翻树正在自己改文字：这时包装层别再把英文当原文记
_自绘刷新 = []                 # [(弱引用, 刷新函数)]：Canvas 自绘控件自己登记


def 记原文(控件, 键, 原文):
    """把"这个控件的这个属性原本是什么中文"记下来，好在切语言时重翻。

    只记中文原文（已经翻过一次的值不要传进来），否则来回切会越翻越歪。
    """
    if _重翻中 or not isinstance(原文, str) or not 原文:
        return
    try:
        原们 = getattr(控件, _原键, None)
        if 原们 is None:
            原们 = {}
            setattr(控件, _原键, 原们)
        原们[键] = 原文
    except Exception:
        pass


def 注册自绘(控件, 刷新):
    """Canvas 自绘控件登记一个"重按当前语言画一遍"的回调。"""
    if 控件 is None or 刷新 is None:
        return
    try:
        _自绘刷新.append((weakref.ref(控件), 刷新))
    except Exception:
        pass


def 翻树(根):
    """把整棵控件树的文案按当前语言重刷一遍。返回改了多少处。

    `tr()` 收中文原文，所以这里存/取的都是**原文**，来回切不会累积。

    ⚠ 关键一步：中文模式**不装包装层**（零成本路径），所以那时候造的控件身上没有
      原文。第一次切到英文时，控件上显示的那串**就是**中文原文 —— 就地记下来，
      再照着翻。否则整棵树都是"查不到原文、无从翻起"（第一版就是这样，改了 0 处）。
    """
    global _重翻中
    改 = 0

    def 走(w):
        nonlocal 改
        原们 = getattr(w, _原键, None)
        if 原们 is None:
            原们 = {}
            for 键 in ("text", "title"):
                try:
                    现 = w.title() if 键 == "title" else w.cget(键)
                except Exception:
                    continue
                if isinstance(现, str) and 现.strip():
                    原们[键] = 现
            if 原们:
                try:
                    setattr(w, _原键, 原们)
                except Exception:
                    原们 = {}
        for 键, 原 in list(原们.items()):
            try:
                if 键 == "title":
                    if w.title() != tr(原):
                        w.title(tr(原))
                        改 += 1
                else:
                    if w.cget(键) != tr(原):
                        w.configure(**{键: tr(原)})
                        改 += 1
            except Exception:
                pass
        try:
            子们 = w.winfo_children()
        except Exception:
            子们 = []
        for c in 子们:
            走(c)

    _重翻中 = True
    try:
        走(根)
    except Exception:
        pass
    finally:
        _重翻中 = False
    return 改


def 翻自绘():
    """重画所有登记过的自绘控件（页签、渐变按钮、开关、分段…）。"""
    活 = []
    for 弱, 刷新 in _自绘刷新:
        控件 = 弱()
        if 控件 is None:
            continue
        活.append((弱, 刷新))
        try:
            刷新()
        except Exception:
            pass
    _自绘刷新[:] = 活


def language():
    """当前语言（`zh` / `en`）。启动时读一次就缓存下来。"""
    global _语言
    if _语言 is None:
        值 = (os.environ.get(ENV_LANG) or "").strip().lower()
        if 值 not in (ZH, EN):
            值 = ""
            try:
                from utils.config import load_raw_config
                值 = str(load_raw_config().get(LANG_KEY) or "").strip().lower()
            except Exception:
                值 = ""
        _语言 = 值 if 值 in (ZH, EN) else ZH
    return _语言


def active():
    """翻译层在**本进程**里生效了吗（= install()/install_qt() 装过）。

    ⚠ 需要按语言分支的代码（比如下载量单位：中文 万/亿、英文 k/M）必须看这个，
    **不能只看 language()** —— 否则用户把界面切成英文之后，那些本来断言中文格式的
    验证脚本会被带着走（真踩过：验证联网搜索卡片.py 挂在 ⬇231.7M 上）。
    跟着 install() 走就等于：真实程序里按用户语言来，测试/工具进程里一律中文。
    """
    return _激活


def set_language(值):
    """改语言（内存 + 立刻重装包装层）。**界面已经建好的部分不会变**，要重启才看得到。"""
    global _语言
    值 = str(值 or "").strip().lower()
    _语言 = 值 if 值 in (ZH, EN) else ZH
    uninstall()
    install()
    重翻全部()
    return _语言


def tr(文本):
    """查表翻译；查不到就原样返回（中文）。

    ⚠ **只有 install() 过的进程才翻译**。这条不只是省一次查表：
    `_dctest` 里一百多个验证脚本是**直接 import ui.main_window** 建界面的（不走 app.py，
    也就不调 install()），它们断言的是中文文案。如果这里改成"看配置里的语言"，那么
    用户一旦把语言设成英文，整个测试套件就会莫名其妙地挂 —— 所以语言状态由
    `install()` 明确决定，没装就是中文。

    非字符串（None、数字、控件…）原样返回 —— 这些入口什么都可能接到。
    """
    if not isinstance(文本, str) or not 文本:
        return 文本
    if not _激活:
        return 文本
    return 表.get(文本, 文本)


def trf(模板, **kw):
    """带占位符的文案：`trf("共 {n} 项", n=5)`。

    f-string 拼出来的串在字典里对不上，所以动态文案要写成
    `trf("共 {n} 项", n=n)` 这种形式。
    """
    return tr(模板).format(**kw)


def trp(模板, *值):
    """同 trf，但按**位置**填值：`trp("复制完成：{0} 个文件", n)`。

    为什么需要它：日志和控制台里大量是 f-string 拼出来的（`f"复制完成：{n} 个文件"`），
    拼好的串跟词典对不上。把这些调用点机械改成 `trp("复制完成：{0} 个文件", n)` 之后：
      · 中文模式：词典查不到 → 返回 `模板.format(*值)`，**和原来的 f-string 逐字一样**；
      · 英文模式：词典命中 → 英文模板填好值。
    无值的写法（`trp("已清空日志")`）等于 tr()，也支持。
    """
    if not 值:
        return tr(模板)
    if not _激活:
        try:
            return 模板.format(*值)
        except Exception:
            return 模板
    英 = 表.get(模板, 模板)
    try:
        return 英.format(*值)
    except Exception:                      # 占位符对不上时别炸，退回中文模板
        try:
            return 模板.format(*值)
        except Exception:
            # 连中文也填不出来（值是机械改出来的，可能少传一个）：宁可少填也不能抛，
            # 否则一条日志就能把迁移线程带崩
            return 模板


# ------------------------------------------------------------------ 包装层
def _包构造器(cls, 属性们=("text",), 位置们=(), 变换=None):
    """把某个控件的构造器包一层：指定参数先过 tr()。

    · `属性们`：按**关键字**传的那些（`tk.Label(text=...)`）；
    · `位置们`：按**位置**传的那些 —— 自家控件的 `SwitchRow(parent, theme, title, ...)`、
      `SegmentedControl(parent, theme, options, ...)` 都是位置传的，只包关键字会漏掉它们
      （第一版就漏了：开关标题、分段选项全是位置参数）；
    · `变换`：{参数名/下标: 处理函数}，给"参数是列表"的那种用（options=[...]）；
    · 另外兜住 tkinter 的 `Label(master, {"text": "..."})` 写法。

    ⚠ 只能包**类**。函数式工厂（`create_gradient_button`）包模块属性没用 —— 别处早就
    `from ... import` 拿走了原函数，那种得直接改函数本体（见 utils/helpers.py）。
    """
    原 = cls.__init__
    if getattr(原, "_i18n_已包", False):
        return
    变换 = 变换 or {}

    def 新(self, *a, **kw):
        a = list(a)
        for i in 位置们:                              # 位置传的：记不了键名，只翻
            if len(a) > i:
                a[i] = 变换.get(i, tr)(a[i])
        for i, x in enumerate(a):                 # tkinter 允许把选项打包成一个 dict
            if isinstance(x, dict) and any(k in x for k in 属性们):
                for k in 属性们:
                    if k in x:
                        记原文(self, k, x[k])
                a[i] = {k: (变换.get(k, tr)(v) if k in 属性们 else v)
                        for k, v in x.items()}
        for k in 属性们:
            if k in kw:
                记原文(self, k, kw[k])
                kw[k] = 变换.get(k, tr)(kw[k])
        原(self, *a, **kw)

    新._i18n_已包 = True
    _原件.append((cls, "__init__", 原))
    cls.__init__ = 新


def _包方法(cls, 名字, 参数们=(), 变换=None):
    """把某个方法的指定（位置索引或关键字名）参数过一遍 tr()。"""
    原 = getattr(cls, 名字, None)
    if 原 is None or getattr(原, "_i18n_已包", False):
        return
    变换 = 变换 or {}

    位置键 = {i: k for i, k in ((0, "title"),)} if cls is tk.Wm else {}

    def 新(self, *a, **kw):
        a = list(a)
        for i in 参数们:
            if isinstance(i, int) and len(a) > i:
                处理 = 变换.get(i, tr)
                记原文(self, 位置键.get(i, "text"), a[i])
                a[i] = 处理(a[i])
        for k in [x for x in 参数们 if isinstance(x, str)]:
            if k in kw:
                记原文(self, k, kw[k])
                kw[k] = 变换.get(k, tr)(kw[k])
        # ⚠ 必须把原方法的返回值传出去：page() / heading() 之类是有返回值的，
        # 少了这个 return，调用方拿到 None 会当场炸（踩过一次）
        return 原(self, *a, **kw)

    新._i18n_已包 = True
    _原件.append((cls, 名字, 原))
    setattr(cls, 名字, 新)


def _包消息框():
    for 名 in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel",
               "askquestion", "askretrycancel", "askyesnocancel"):
        原 = getattr(messagebox, 名, None)
        if 原 is None or getattr(原, "_i18n_已包", False):
            continue

        def 造(原函数):
            def 新(*a, **kw):
                a = list(a)
                for i in (0, 1):                      # (title, message)
                    if len(a) > i:
                        a[i] = tr(a[i])
                for k in ("title", "message"):
                    if k in kw:
                        kw[k] = tr(kw[k])
                return 原函数(*a, **kw)
            新._i18n_已包 = True
            return 新

        包过 = 造(原)
        _原件.append((messagebox, 名, 原))
        setattr(messagebox, 名, 包过)


def _翻选项(选项):
    """把 [标签] / [(值, 文案)] / [(值, 标题, 说明)] / [(文本, 色键)] 几种形状都翻一遍。"""
    if not isinstance(选项, (list, tuple)):
        return 选项
    出 = []
    for 项 in 选项:
        if isinstance(项, str):
            出.append(tr(项))
        elif isinstance(项, (list, tuple)):
            出.append(tuple(tr(x) if isinstance(x, str) else x for x in 项))
        else:
            出.append(项)
    return 出


def _包自家控件():
    """自家那几个自绘控件：文案在构造器里落下，同样在入口处翻。

    ⚠ 这里只包**类**。函数式的 `create_gradient_button` 包不了 —— 别的模块早就
    `from utils.helpers import create_gradient_button` 拿走了原函数，改模块属性对它无效；
    那个是在函数本体里调 tr() 的（见 utils/helpers.py）。
    """
    try:
        from utils import helpers as H
    except Exception:
        return
    _包构造器(H.SwitchRow, ("title", "desc", "warn_desc"), (2, 3))
    _包构造器(H.OptionCards, ("options",), (2,), {"options": _翻选项, 2: _翻选项})
    _包构造器(H.DataText, ("parts",), (2,), {"parts": _翻选项, 2: _翻选项})
    _包构造器(H.SegmentedControl, ("options",), (2,), {"options": _翻选项, 2: _翻选项})
    # ⚠ "后改文案"的入口也要包：SwitchRow 的文字是**自绘**的（存在 _title/_desc 里，
    # 不走 tk 的 text=），只包构造器的话，set_text() 一改就又是中文原文。
    # （用户抓到的就是这条：主界面编辑开关的说明文字）
    _包方法(H.SwitchRow, "set_text", ("title", "desc"))
    _包方法(H.DataText, "set", (1,))                       # set(index, text)
    _包方法(H.DataText, "set_all", (0,), {0: _翻选项})      # set_all([(文本, 色键), …])


def 重翻全部():
    """按当前语言把已建的界面重刷一遍（切语言时调）。"""
    装 = install()
    if not 装:
        uninstall()
    return 翻自绘()


def install():
    """按当前语言决定要不要装包装层。**中文（默认）时什么都不做。**"""
    global _已装, _激活
    if language() == ZH:
        return False
    _激活 = True                     # tr() 从这里开始生效
    if _已装:
        return True
    # Tk 原生控件
    for cls in (tk.Label, tk.Button, tk.LabelFrame, tk.Checkbutton, tk.Radiobutton,
                tk.Message, tk.Menubutton):
        _包构造器(cls, ("text",))
    for cls in (ttk.Label, ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.LabelFrame):
        _包构造器(cls, ("text",))
    _包方法(tk.Wm, "title", (0,))                 # 窗口标题
    _包方法(tk.Wm, "wm_title", (0,))
    for 名 in ("add_command", "add_cascade", "add_checkbutton", "add_radiobutton"):
        _包方法(tk.Menu, 名, ("label",))
    # `lbl.configure(text=...)` / `lbl.config(text=...)`：文案**后改**的那些走这条路
    # （比如设置页的状态行），不包就只能翻到构造时那一次。
    _包方法(tk.Misc, "configure", ("text",))
    _包方法(tk.Misc, "config", ("text",))
    _包方法(ttk.Notebook, "add", ("text",))
    _包方法(ttk.Treeview, "heading", ("text",))
    # 树的行文字也走语言层：`insert(..., text=…, values=(…,))` / `item(iid, text=…)`。
    # 树里装的多半是数据（文件名/数字），但设置页那棵"按钮列表"整棵是界面文案，
    # values 还是 ("☑ 显示",) 这种状态词 —— 只包 heading 不够（用户报过这页）。
    _包方法(ttk.Treeview, "insert", ("text", "values"), {"values": _翻选项})
    _包方法(ttk.Treeview, "item", ("text", "values"), {"values": _翻选项})
    _包消息框()
    _包自家控件()
    # 自绘页签栏：page() 既用来建页也用来查页，两侧都过 tr()，键仍然一致
    try:
        from ui.rounded_tabs import RoundedTabs
        _包方法(RoundedTabs, "page", (0,))
        _包方法(RoundedTabs, "set_label", (1,))
    except Exception:
        pass
    _已装 = True
    return True


def _包Qt方法(cls, 名字, 位置们=(), 属性们=(), 变换=None, 静态=False):
    """给 PySide6 的类方法 / 静态方法包一层（和 Tk 那套同一个思路）。

    实测 shiboken 生成的 Qt 类**可以**这么改（不像 CPython 内建类型那样只读），
    所以 Qt 侧也不用去改几百个调用点，照样在入口查表。
    """
    原 = getattr(cls, 名字, None)
    if 原 is None or getattr(原, "_i18n_已包", False):
        return
    变换 = 变换 or {}

    def 处理(a, kw):
        a = list(a)
        for i in 位置们:
            if len(a) > i:
                a[i] = 变换.get(i, tr)(a[i])
        for k in 属性们:
            if k in kw:
                kw[k] = 变换.get(k, tr)(kw[k])
        return a

    if 静态:
        def 新(*a, **kw):
            return 原(*处理(a, kw), **kw)
    else:
        def 新(self, *a, **kw):
            return 原(self, *处理(a, kw), **kw)
    新._i18n_已包 = True
    _原件.append((cls, 名字, 原))
    try:
        setattr(cls, 名字, 新)
    except Exception:
        pass


def _包Qt控件():
    """Qt 控件：文案同样在入口翻。

    ⚠ 只在 **Qt 子进程**里调（ui/qt_host.py）—— 主进程一行 Qt 都不许跑（那套 GIL 致命
    错误见 ui/qt_host.py 顶部说明）。所以 import PySide6 写在函数里、由子进程触发。
    """
    try:
        from PySide6 import QtGui, QtWidgets
    except Exception:
        return False
    QW = QtWidgets
    for cls in (QW.QLabel, QW.QPushButton, QW.QCheckBox, QW.QRadioButton, QW.QToolButton):
        _包Qt方法(cls, "__init__", 位置们=(0,))          # QLabel("文字")
        _包Qt方法(cls, "setText", 位置们=(0,))           # 后改文字
    _包Qt方法(QW.QGroupBox, "__init__", 位置们=(0,))
    _包Qt方法(QW.QGroupBox, "setTitle", 位置们=(0,))
    _包Qt方法(QtGui.QAction, "__init__", 位置们=(0,))
    _包Qt方法(QtGui.QAction, "setText", 位置们=(0,))
    _包Qt方法(QW.QWidget, "setWindowTitle", 位置们=(0,))
    _包Qt方法(QW.QWidget, "setToolTip", 位置们=(0,))
    _包Qt方法(QW.QLineEdit, "setPlaceholderText", 位置们=(0,))
    _包Qt方法(QW.QComboBox, "addItem", 位置们=(0,))
    _包Qt方法(QW.QComboBox, "addItems", 位置们=(0,), 变换={0: _翻选项})
    _包Qt方法(QW.QTabWidget, "addTab", 位置们=(1,))
    _包Qt方法(QW.QTabWidget, "setTabText", 位置们=(1,))
    _包Qt方法(QW.QMenu, "addAction", 位置们=(0,))
    _包Qt方法(QW.QPlainTextEdit, "append", 位置们=(0,))
    _包Qt方法(QW.QPlainTextEdit, "setPlainText", 位置们=(0,))
    _包Qt方法(QW.QTextEdit, "append", 位置们=(0,))
    _包Qt方法(QW.QTextEdit, "setPlainText", 位置们=(0,))
    _包Qt方法(QW.QTextEdit, "setHtml", 位置们=(0,))
    _包Qt方法(QW.QTableWidget, "setHorizontalHeaderLabels", 位置们=(0,),
              变换={0: _翻选项})
    _包Qt方法(QW.QTreeWidget, "setHeaderLabels", 位置们=(0,), 变换={0: _翻选项})
    for 名 in ("information", "warning", "critical", "question", "about"):
        _包Qt方法(QW.QMessageBox, 名, 位置们=(1, 2), 静态=True)
    # 自家那个 Qt 按钮（Python 类，直接包构造器）
    try:
        from ui import qt_big_view as QB
        _包Qt方法(QB.AnimButton, "__init__", 位置们=(0,))
    except Exception:
        pass
    return True


def install_qt():
    """Qt 子进程用：只**激活翻译** + 包 Qt 控件，不碰 Tk（子进程里没有 Tk，也不许有）。"""
    global _激活, _已装Qt
    if language() == ZH:
        return False
    _激活 = True
    if _已装Qt:
        return True
    _已装Qt = _包Qt控件()
    return _已装Qt


def uninstall():
    """还原（验证脚本要来回切语言时用）。"""
    global _已装, _已装Qt, _激活
    for 对象, 名字, 原 in reversed(_原件):
        try:
            setattr(对象, 名字, 原)
        except Exception:
            pass
    _原件.clear()
    _已装 = False
    _已装Qt = False
    _激活 = False


def coverage(文本们):
    """给验证脚本/收集脚本用：这批文案里有多少条已经翻过了。"""
    有 = [t for t in 文本们 if isinstance(t, str) and t.strip() and t in 表]
    缺 = [t for t in 文本们 if isinstance(t, str) and t.strip() and t not in 表]
    return 有, 缺

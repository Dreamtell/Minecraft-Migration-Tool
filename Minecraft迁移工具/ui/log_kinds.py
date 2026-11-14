# -*- coding: utf-8 -*-
"""执行日志里「普通信息」行的细分上色。

日志只有五个级别（INFO / WARNING / ERROR / SUCCESS / SIMULATE），而迁移一次要写几百行
INFO：开始自检、各阶段标题、统计数字、自检提示、重复模组的处理说明、迁移结果……
它们全是同一种灰，读的时候没法一眼分出"现在到哪一步了""这行是在报数还是在说明"。

这里不动级别（级别还管着红底、定位错误、历史记录、日志文件、总览条），只在**显示**时
给 INFO 行再叠一个"子类"颜色标签：

    阶段   【复制阶段】… / 开始扫描 / 开始迁移前自检 / 倒计时 / 迁移结束     → 青色，加粗
    统计   📦 待迁移文件 / 📊 检查结果 / 「共 N 个」这类汇总                  → 数据色
    提示   ℹ️ 提示 / 自检提示 / 已自动处理…的说明 / 不在清单里的说明         → 柔和的蓝灰
    操作   📍🔗🗂📋🔑🧹🔁↩🗑🔒🔄📁📝 这类"你刚做了什么"的反馈                → 次要色
    通过   ✅ 自检通过 / ☑ 已勾选…：写成 INFO 但意思是"没问题"                  → 成功绿
           （真正的成功是 SUCCESS 级别，已经是绿的；这里只补 INFO 里的"通过"）

规则只看行内容（图标 + 中英文关键词），中英文两套都认：日志在 `log()` 里已经过了
`i18n.tr`，英文模式下看到的是英文。认不出来就保持灰色（INFO 原样），所以漏判只是
"没上色"，不会染错。

对比度：颜色全部取主题里的键（log_*_fg 专用键；缺了退回 card_sel_bar / data_fg / data_num_fg…），
`LINE_KINDS` 里写明每个子类用哪个键、缺了退回哪个主题里已有的键，深浅两套主题都过 WCAG AA。
"""

import re

# 子类 → (标签名, 主题键, 退回的主题键, 是否加粗)
# 标签名带前缀，免得和 INFO/ERROR 这些级别标签撞名；缩略图和总览条只认级别标签，不受影响。
LINE_KINDS = {
    "phase": ("INFO_PHASE", "log_phase_fg", "card_sel_bar", True),
    "stat": ("INFO_STAT", "log_stat_fg", "data_fg", False),
    "note": ("INFO_NOTE", "log_note_fg", "log_simulate_fg", False),
    "action": ("INFO_ACTION", "log_action_fg", "data_num_fg", False),
    "ok": ("INFO_OK", "log_success_fg", "log_success_fg", False),     # 直接用成功绿，不另起一个色
}
KIND_TAGS = tuple(v[0] for v in LINE_KINDS.values())

# 行首图标 → 子类（这些图标是代码里 log() 调用点自己写的，比关键词稳）
_ICON = {
    "phase": "🔎🔍⏳⛔",
    "stat": "📦📊",
    "note": "ℹ",
    "action": "📍🔗🗂📋🔑🧹🔁↩🗑🔒🔄📁📝",
    "ok": "✅☑",
}

# 图标之外的关键词（中英文）。顺序即优先级：阶段 > 统计 > 提示。
_PHASE = re.compile(
    r"^\s*(【.+?】|\[(copy|backup|scan|cleanup)[^\]]*\])|"        # 【复制阶段】 / [Copy phase]
    r"^\s*(迁移结束|迁移完成|迁移已取消|Migration (finished|complete|cancel)|"
    r"开始扫描|开始迁移|开始前倒计时|Scanning|Starting|Running the pre)",
    re.I)
_STAT = re.compile(
    r"(待迁移文件|总大小|检查结果|总清单项数|去重后|"
    r"\d+\s*(个|项|条|files?|items?|mods?)\b.*\d+(\.\d+)?\s*(MB|GB|KB)|"
    r"files to migrate|in total)", re.I)
_NOTE = re.compile(
    r"^\s*(已自动处理|自动处理重复模组|源目录里还有|目标里已有|不在清单里|"
    r"Automatically resolved|Resolved duplicate|\d+ more jars|\d+ items already exist)|"
    r"(不在清单里，不会被复制|不会被复制|处理明细见)", re.I)


def classify(line):
    """一行 INFO 日志 → 子类名（phase/stat/note/action）或 None（保持灰色）。"""
    s = str(line or "").strip()
    if not s:
        return None
    head = s[:2]
    for kind, icons in _ICON.items():
        if s[0] in icons or (len(head) > 1 and head[0] in icons):
            return kind
    if _PHASE.search(s):
        return "phase"
    if _STAT.search(s):
        return "stat"
    if _NOTE.search(s):
        return "note"
    return None


def kind_color(theme, kind):
    """子类颜色：先找专用键，没有就退回主题里已有的近似色。"""
    _tag, key, fallback, _bold = LINE_KINDS[kind]
    return theme.get(key) or theme.get(fallback) or theme.get("log_info_fg", "#808080")


def configure_tags(widget, theme, font=None):
    """把子类标签配到一个 Text 上（主题切换时再调一次即可）。"""
    for kind, (tag, _key, _fb, bold) in LINE_KINDS.items():
        opts = {"foreground": kind_color(theme, kind)}
        if bold and font:
            try:
                import tkinter.font as _tkfont
                f = _tkfont.Font(font=font)
                f.configure(weight="bold")
                opts["font"] = f
            except Exception:
                pass
        try:
            widget.tag_configure(tag, **opts)
        except Exception:
            pass

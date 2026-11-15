# core/history_view.py
"""迁移历史窗的展示逻辑(Qt 主界面用)。规则同 Tk 版 ui/mw_migration.py 的 `显示` / `_日志级别标签`,
但不 import tkinter,也不碰任何控件:只把档案 dict 变成「该显示什么」。
"""

# 日志级别(档案里存的是大写英文)。Qt 和 Tk 共用同一套主题色键。
LEVELS = ("INFO", "WARNING", "ERROR", "SUCCESS", "SIMULATE")


def infer_level(line):
    """旧档案没有级别字段时,从行里的图标 / 关键字推断该用哪个颜色。顺序与 Tk 的 `_日志级别标签` 一致。"""
    text = str(line or "")
    if "❌" in text or "ERROR" in text:
        return "ERROR"
    if "⚠️" in text or "⏭️" in text:
        return "WARNING"
    if "✅" in text:
        return "SUCCESS"
    if "[模拟]" in text or "[SIMULATE]" in text:
        return "SIMULATE"
    return "INFO"


def log_lines(full):
    """档案里的日志 → [(级别, 文本)]。返回 (行列表, 行数)。

    新档案有 `log_lines`([[级别, 文本], ...]),直接用;旧档案只有一整段 `log`,按行推断级别。
    两者都没有时返回 ([], 0),由界面显示「这份档案没有日志」。
    """
    pairs = full.get("log_lines") if isinstance(full, dict) else None
    if isinstance(pairs, list) and pairs:
        out = []
        for item in pairs:
            # 必须是 [级别, 文本]:裸字符串也能 item[0] / item[1],会被拆成单个字符变成假日志行
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue                      # 档案里混进了坏条目,跳过,不能让整份日志都显示不出来
            level, text = item[0], item[1]
            level = str(level).upper()
            out.append((level if level in LEVELS else "INFO", str(text)))
        return out, len(pairs)
    raw = full.get("log") if isinstance(full, dict) else None
    if isinstance(raw, str) and raw:
        lines = raw.split("\n")
        return [(infer_level(x), x) for x in lines], len(lines)
    return [], 0


def _group(stats, key):
    g = stats.get(key) if isinstance(stats, dict) else None
    return g if isinstance(g, dict) else {}


def stat_rows(rec):
    """统计四行:[(名, 成功, 跳过或 None, 失败)]。「存档」「config」没有跳过这一项,用 None 表示不显示。

    缺哪一组就当 0(早期档案没有 saves)。数字不是整数时按 0 算,不能让展示崩掉。
    """
    stats = (rec or {}).get("stats") or {}

    def n(group, k):
        v = group.get(k, 0)
        return v if isinstance(v, int) and not isinstance(v, bool) else 0

    mods, saves = _group(stats, "mods"), _group(stats, "saves")
    cfg, extra = _group(stats, "config"), _group(stats, "extra")
    return [
        ("模组", n(mods, "success"), n(mods, "skipped"), n(mods, "failed")),
        ("存档", n(saves, "success"), None, n(saves, "failed")),
        ("config", n(cfg, "success"), None, n(cfg, "failed")),
        ("其它", n(extra, "success"), n(extra, "skipped"), n(extra, "failed")),
    ]


def result_kind(rec):
    """结果的颜色档位:成功 → "ok";模拟运行 → "warn";别的(失败、取消…)→ "fail"。同 Tk 的 `显示`。"""
    result = (rec or {}).get("result") or ""
    if result == "成功":
        return "ok"
    if result == "模拟运行":
        return "warn"
    return "fail"


def failed_count(rec):
    """lists.failed 里有几条(没有或不是列表时为 0)。"""
    lists = (rec or {}).get("lists")
    failed = lists.get("failed") if isinstance(lists, dict) else None
    return len(failed) if isinstance(failed, list) else 0

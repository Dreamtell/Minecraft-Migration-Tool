# core/changelog.py
"""从整合包变更日志里提取模组文件名(Tk 主界面和 Qt 主界面共用)。

原来写在 ui/mw_lists.py 的 extract_mods_from_changelog 里,不依赖任何界面状态,
抽到这里后两套界面调同一份;规则逐字未改:
  · `+ xxx.jar` → Added
  · `- xxx.jar` → Updated
  · 其它以 .jar 结尾、且不以 Added/Updated/Removed 开头的行 → Added
  · `Added mods:` / `Updated mods:` 标题行跳过
"""
import re

_ADDED = re.compile(r'^[\s]*\+[\s]*(.+\.jar)', re.IGNORECASE)
_UPDATED = re.compile(r'^[\s]*\-[\s]*(.+\.jar)', re.IGNORECASE)
_ADDED_HEAD = re.compile(r"^Added\s+mods[:：]", re.IGNORECASE)
_UPDATED_HEAD = re.compile(r"^Updated\s+mods[:：]", re.IGNORECASE)


def extract_mods(text):
    """返回 (added, updated) 两个文件名列表,各自保持原顺序。"""
    added, updated = [], []
    for line in str(text or "").splitlines():
        s = line.strip()
        m = _ADDED.match(s)
        if m:
            added.append(m.group(1))
            continue
        m = _UPDATED.match(s)
        if m:
            updated.append(m.group(1))
            continue
        if _ADDED_HEAD.match(s) or _UPDATED_HEAD.match(s):
            continue
        if s.endswith(".jar"):
            if not (s.startswith("Added") or s.startswith("Updated")
                    or s.startswith("Removed")):
                added.append(s)
    return added, updated

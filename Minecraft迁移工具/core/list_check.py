# core/list_check.py
"""「检查清单条目在源实例里是否存在」的判定逻辑(Qt 主界面用;规则与 Tk 版一致)。

Tk 版的三个 check_*_existence 把判定和 Tk 文本框上色写在一起;这里只做判定,
返回每一行的状态,上色交给界面。规则:
  · 模组:源 mods/*.jar 里按 core.migrator.match_mod 匹配(支持 `[前缀]` 清理)
  · config:相对源 config/ 的路径,文件或目录都算存在
  · 其它文件:相对源实例根目录的路径
  · 重复:统一成小写 + 正斜杠(config/其它文件再去掉末尾 /)后出现不止一次 → "dup"
不安全路径(带 .. / 绝对路径)一律算 "missing",不去碰文件系统。
"""
from collections import Counter
from pathlib import Path

OK, MISSING, DUP = "ok", "missing", "dup"


def _lines(text):
    """[(行号0起, 去空白后的条目)],跳过空行和 # 注释。"""
    out = []
    for i, ln in enumerate(str(text or "").splitlines()):
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append((i, s))
    return out


def _rel_ok(base, rel):
    from core.migrator import _is_safe_path
    r = rel.replace("\\", "/").rstrip("/")
    if not r or not _is_safe_path(r):
        return False
    try:
        return (Path(base) / r).exists()
    except OSError:
        return False


def check(kind, source_root, text):
    """kind: "mods" / "config" / "extra"。

    返回 {"rows": {行号: 状态}, "ok": n, "missing": n, "dup": n,
          "missing_names": [...], "error": None 或 说明}
    """
    res = {"rows": {}, "ok": 0, "missing": 0, "dup": 0, "missing_names": [], "error": None}
    root = Path(str(source_root or ""))
    if not str(source_root or "").strip():
        res["error"] = "no_source"
        return res
    base = root / "mods" if kind == "mods" else (root / "config" if kind == "config" else root)
    if not base.is_dir():
        res["error"] = "no_dir"
        res["dir"] = str(base)
        return res
    items = _lines(text)
    if not items:
        res["error"] = "empty"
        return res

    if kind == "mods":
        def norm(e):
            return e.replace("\\", "/").lower()
        from core.migrator import match_mod
        source_files = {f.name: f for f in base.glob("*.jar")}
        name_map = {}
        for orig in source_files:
            clean = orig.split("]", 1)[1].strip() if orig.startswith("[") and "]" in orig else orig
            name_map[clean] = orig
            name_map[orig] = orig

        def exists(e):
            return match_mod(Path(e).name, source_files, name_map) is not None
    else:
        def norm(e):
            return e.replace("\\", "/").lower().rstrip("/")

        def exists(e):
            return _rel_ok(base, e)

    counts = Counter(norm(e) for _, e in items)
    for i, e in items:
        if counts[norm(e)] > 1:
            st = DUP
        elif exists(e):
            st = OK
        else:
            st = MISSING
            res["missing_names"].append(e)
        res["rows"][i] = st
        res[st] += 1
    return res

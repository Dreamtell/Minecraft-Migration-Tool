# core/list_ops.py
"""清单文本的纯逻辑(Qt 主界面用)。规则同 Tk 版 ui/mw_lists.py,但不 import tkinter。

三个清单都是「一行一条」的文本。这里只管文本进、文本出;读写控件、撤销栈、提示都留给界面。
"""
from pathlib import Path


def entry_key(line):
    """去重用的键:正斜杠、去末尾 /、小写。`jei` 和 `jei/` 是同一条。"""
    return str(line).strip().replace("\\", "/").rstrip("/").lower()


def mod_name(line):
    """模组清单一行的「模组名」:正常是文件名,历史遗留可能是整条绝对路径。"""
    line = str(line).strip()
    return Path(line).name if line else ""


def _is_note(line):
    s = line.strip()
    return not s or s.startswith("#")


def normalize_mod_lines(content):
    """把清单里「整条路径」的行收成文件名,并去掉重名。返回 (新内容, 改了几行)。

    为什么会混进整条路径:早期「添加模组」把文件对话框给的绝对路径直接写进清单,
    而清单的约定是「每行一个 .jar 文件名」。于是同一个模组能同时以 `A.jar` 和
    `C:/.../mods/A.jar` 两条存在,自检还会因为按名字找不到而报「源目录里没有」。
    空行和 # 注释原样保留(注释里常有 `/`,不能当路径去取文件名)。
    """
    out, seen, changed = [], set(), 0
    for raw in str(content).split("\n"):
        if _is_note(raw):
            out.append(raw)
            continue
        name = mod_name(raw)
        if name != raw.strip():
            changed += 1
        key = name.lower()
        if key in seen:
            changed += 1
            continue
        seen.add(key)
        out.append(name)
    return "\n".join(out), changed


def merge_mods(content, paths):
    """把若干 .jar 追加进模组清单(按文件名、不分大小写去重)。

    返回 (新内容, 实际新增的文件名列表, 顺手整理的旧行数)。非 .jar 直接忽略。
    """
    jars = [str(p) for p in paths if Path(str(p)).suffix.lower() == ".jar"]
    text, tidied = normalize_mod_lines(content)
    seen = {mod_name(ln).lower() for ln in text.split("\n") if not _is_note(ln)}
    added = []
    for p in jars:
        name = Path(p).name
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        added.append(name)
    if added:
        base = text.rstrip()
        text = (base + "\n" if base else "") + "\n".join(added)
    return text, added, tidied


def mark_config_folders(src_config, content):
    """按源 config 目录检测:文件夹条目在末尾加 `/`(和文件条目区分)。返回 (新内容, 是否改了)。

    幂等:已带 `/` 的行不动;不安全的路径(`..`、绝对路径)不去碰文件系统;
    源 config 目录不存在就原样返回。
    """
    from core.migrator import _is_safe_path
    base = Path(src_config)
    if not base.is_dir():
        return content, False
    out, changed = [], False
    for ln in str(content).split("\n"):
        s = ln.strip()
        if not _is_note(ln) and not s.endswith(("/", "\\")):
            rel = s.replace("\\", "/")
            try:
                if _is_safe_path(rel) and (base / rel).is_dir():
                    out.append(ln.rstrip() + "/")
                    changed = True
                    continue
            except OSError:
                pass
        out.append(ln)
    return "\n".join(out), changed


def missing_mod_rows(src, content):
    """扫描差异之前:清单里「源 mods 里根本没有」的条目。返回 (缺失行号列表0起, 缺失条目名列表)。

    规则同 Tk 的 `_mark_missing_before_scan`:只认源 `<实例>/mods/*.jar`,按 core.migrator.match_mod
    匹配(它自己处理 `[前缀]`:清单写 `B.jar` 能对上源里的 `[CN] B.jar`,反过来也行);空行和 # 注释
    跳过。源 mods 不存在或里面一个 jar 都没有时返回空(这时没法判断谁缺,不能把整张清单都报成缺失)。

    和 Tk 一样**按整行**匹配,不先取文件名;所以清单里的老式整条路径行会被当成缺失,
    这正是要让用户看到、去整理的东西。
    """
    from core.migrator import match_mod
    mods_dir = Path(str(src or "")) / "mods"
    try:
        if not mods_dir.is_dir():
            return [], []
        source_files = {f.name: f for f in mods_dir.glob("*.jar")}
    except OSError:
        return [], []
    if not source_files:
        return [], []
    # name_map 只是 match_mod 的快速通道(精确文件名 → 自己);去前缀的匹配 match_mod 会自己退回线性扫描
    name_map = {orig: orig for orig in source_files}
    rows, names = [], []
    for i, ln in enumerate(str(content).split("\n")):
        s2 = ln.strip()
        if _is_note(ln):
            continue
        if not match_mod(s2, source_files, name_map):
            rows.append(i)
            names.append(s2)
    return rows, names

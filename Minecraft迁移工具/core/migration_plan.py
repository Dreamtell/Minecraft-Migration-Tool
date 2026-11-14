"""One filesystem inventory shared by preview, space checking and execution."""
import os
import stat
from pathlib import Path
from core.migrator import (_checked_path, _check_cancel, _is_safe_world_name,
                           _modid_of, _is_safe_path, match_mod)
from core.scanner import compare_versions, get_mod_metadata
from utils.i18n import trp


def _same_mod_files(paths, check_cancel=None):
    """Exact comparison only when cheap metadata cannot rule out equality."""
    _check_cancel(check_cancel)
    if len(paths) < 2:
        return True
    size = paths[0].stat().st_size
    for path in paths[1:]:
        _check_cancel(check_cancel)
        if path.stat().st_size != size:
            return False
    for path in paths[1:]:
        with paths[0].open("rb") as first, path.open("rb") as other:
            while True:
                _check_cancel(check_cancel)
                chunk = first.read(1024 * 1024)
                if chunk != other.read(1024 * 1024):
                    return False
                if not chunk:
                    break
    return True


def _choose_mod(paths, check_cancel=None):
    """Keep the same selection rules without hashing every full JAR first."""
    metadata = []
    for path in paths:
        _check_cancel(check_cancel)
        metadata.append(get_mod_metadata(path))
    versions = [entry[1] for entry in metadata]
    # Declared versions come from the archive bytes. Different declarations
    # prove inequality; filename-inferred versions deliberately do not.
    declared = {version for _, version, kind in metadata
                if kind in ("Fabric", "Forge", "NeoForge")}
    if len(declared) < 2 and _same_mod_files(paths, check_cancel):
        return paths[0], "identical"
    if any(compare_versions(v, v) is None for v in versions):
        return paths[0], "unknown"
    best = 0
    for index in range(1, len(paths)):
        if compare_versions(versions[index], versions[best]) > 0:
            best = index
    tied = [path for path, version in zip(paths, versions)
            if compare_versions(version, versions[best]) == 0]
    reason = "newest" if _same_mod_files(tied, check_cancel) else "equal_version"
    return paths[best], reason


def _inventory_tree(path, check_cancel=None):
    """One scandir pass, using its cached type/size and rejecting links first."""
    pending = [Path(path)]
    while pending:
        _check_cancel(check_cancel)
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                _check_cancel(check_cancel)
                info = entry.stat(follow_symlinks=False)
                if (stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)):
                    raise ValueError(trp("不支持符号链接或目录联接：{0}", entry.path))
                item = Path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    yield item, True, 0
                    pending.append(item)
                elif stat.S_ISREG(info.st_mode):
                    yield item, False, info.st_size


def build_migration_plan(src, tgt, world, mods, configs, extras=None,
                         overwrite=False, extra_overwrite=True, marker=None,
                         check_cancel=None):
    source, target = Path(src).resolve(), Path(tgt).resolve()
    if (source == target or source.is_relative_to(target)
            or target.is_relative_to(source)):
        raise ValueError(trp("源目录和目标目录不能重叠"))
    if not source.is_dir() or not target.is_dir():
        raise ValueError(trp("源目录和目标目录必须存在"))
    if world and not _is_safe_world_name(world):
        raise ValueError(trp("存档名称必须是单个文件夹名称：{0}", world))
    prefix = str(marker or "").strip()
    if prefix:
        prefix += " "
    plan = {"source": source, "target": target, "files": [], "dirs": [],
            "errors": [], "warnings": [], "mod_resolutions": [],
            "total_files": 0, "total_size": 0}
    seen_files, seen_dirs = {}, set()
    seen_mods = set()
    # Scope directory reuse to this read-only inventory. Execution still calls
    # _checked_path on every endpoint immediately before changing files.
    checked_dirs = {target}

    def check_target(dst):
        parent = dst.parent
        if parent not in checked_dirs:
            _checked_path(target, parent.relative_to(target))
            if parent.exists() and not parent.is_dir():
                raise ValueError(trp("目标路径类型冲突：{0}", parent))
            checked_dirs.add(parent)
        if not _is_safe_path(dst.relative_to(target)):
            raise ValueError(trp("不安全路径：{0}", dst))
        if dst.is_symlink() or dst.is_junction():
            raise ValueError(trp("不支持符号链接或目录联接：{0}", dst))

    def add_file(src_file, dst, category, policy, size=None):
        _check_cancel(check_cancel)
        check_target(dst)
        # A mods/ entry in Extras must not re-add a selected mod without its
        # rename marker or override the dedicated mod list's overwrite policy.
        if (category == "extra" and dst.parent == target / "mods"
                and src_file in seen_mods):
            return
        if dst.is_dir():
            raise ValueError(trp("目标路径类型冲突：{0}", dst))
        key = str(dst).casefold()
        if key in seen_files:
            if seen_files[key] != src_file:
                raise ValueError(trp("多个源文件指向同一个目标：{0}", dst))
            return
        if src_file.is_symlink() or src_file.is_junction():
            raise ValueError(trp("不支持符号链接或目录联接：{0}", src_file))
        if size is None:
            size = src_file.stat().st_size
        seen_files[key] = src_file
        plan["files"].append({"src": src_file, "dst": dst,
                              "category": category, "overwrite": policy,
                              "size": size, "label": str(dst.relative_to(target))})

    def add_dir(src_dir, dst_dir, category):
        check_target(dst_dir)
        if dst_dir.exists() and not dst_dir.is_dir():
            raise ValueError(trp("目标路径类型冲突：{0}", dst_dir))
        checked_dirs.add(dst_dir)
        key = str(dst_dir).casefold()
        if key not in seen_dirs:
            seen_dirs.add(key)
            plan["dirs"].append({"src": src_dir, "dst": dst_dir,
                                 "category": category,
                                 "label": str(dst_dir.relative_to(target))})

    def add_entry(rel, category, policy, optional=False):
        src_entry, dst_entry = _checked_path(source, rel), _checked_path(target, rel)
        if not src_entry.exists():
            if optional:
                plan["warnings"].append(trp("⚠️ 源存档不存在: {0}，跳过", src_entry))
            else:
                plan["errors"].append((category, str(rel), trp("源不存在")))
            return
        if src_entry.is_file():
            add_file(src_entry, dst_entry, category, policy)
            return
        add_dir(src_entry, dst_entry, category)
        for item, is_dir, size in sorted(_inventory_tree(src_entry, check_cancel)):
            _check_cancel(check_cancel)
            child = dst_entry / item.relative_to(src_entry)
            if is_dir:
                add_dir(item, child, category)
            else:
                add_file(item, child, category, policy, size=size)

    src_mods = _checked_path(source, "mods")
    source_files = {p.name: p for p in src_mods.glob("*.jar") if p.is_file()}
    for raw in dict.fromkeys(mods or []):
        _check_cancel(check_cancel)
        direct = Path(raw)
        if direct.is_absolute() and direct.is_file() and direct.suffix.lower() == ".jar":
            src_file = direct.resolve()
        else:
            matched = match_mod(raw, source_files, {})
            if not matched:
                plan["errors"].append(("mods", str(raw), trp("未找到匹配的文件")))
                continue
            src_file = _checked_path(source, Path("mods") / matched)
        if src_file in seen_mods:
            continue
        seen_mods.add(src_file)
        if src_file.is_symlink() or src_file.is_junction():
            raise ValueError(trp("不支持符号链接或目录联接：{0}", src_file))
        dst = _checked_path(target, Path("mods") / (prefix + src_file.name))
        add_file(src_file, dst, "mods", overwrite)
    if world:
        add_entry(Path("saves") / world, "saves", True, optional=True)
    for rel in dict.fromkeys(configs or []):
        # Validate before joining; an absolute/parent path must not discard config/.
        if not _is_safe_path(rel):
            raise ValueError(trp("不安全路径：{0}", rel))
        add_entry(Path("config") / rel, "config", True)
    for rel in dict.fromkeys(extras or []):
        add_entry(rel, "extra", extra_overwrite)

    # Resolve all jars installed in mods/, including files selected via Extras.
    selected_ids = {}
    for item in plan["files"]:
        _check_cancel(check_cancel)
        if item["dst"].parent == target / "mods" and item["dst"].suffix.lower() == ".jar":
            modid = _modid_of(item["src"])
            if modid:
                selected_ids.setdefault(modid, []).append(item)
    excluded = set()
    for modid, items in selected_ids.items():
        if len(items) < 2:
            continue
        _check_cancel(check_cancel)
        chosen, reason = _choose_mod([item["src"] for item in items], check_cancel)
        skipped = [item for item in items if item["src"] != chosen]
        excluded.update(item["dst"] for item in skipped)
        reason_text = trp({
            "identical": "文件内容相同，保留清单中第一份",
            "newest": "优先保留较新版本",
            "equal_version": "版本相同但内容不同，保留该版本在清单中的第一份",
            "unknown": "版本无法可靠比较，保留清单中第一份",
        }[reason])
        plan["mod_resolutions"].append({
            "modid": modid, "kept": str(chosen),
            "skipped": [str(item["src"]) for item in skipped],
            "skipped_categories": [item["category"] for item in skipped],
            "reason": reason,
            "message": trp("自动处理重复模组 {0}：保留 {1}；跳过 {2}。原因：{3}。",
                            modid, chosen.name,
                            " / ".join(item["src"].name for item in skipped), reason_text),
        })
    plan["files"] = [item for item in plan["files"] if item["dst"] not in excluded]
    copied = [item for item in plan["files"] if item["overwrite"] or not item["dst"].exists()]
    plan["total_files"] = len(copied)
    plan["total_size"] = sum(item["size"] for item in copied)
    return plan


def backup_size(target, extras=None, check_cancel=None):
    """Count stored backup ranges, including the separate extras snapshot."""
    from core.migrator import _BACKUP_DIRS, _BACKUP_ROOT_FILES
    target = Path(target).resolve()
    seen, total = set(), 0
    ranges = [("fixed", rel) for rel in list(_BACKUP_DIRS) + list(_BACKUP_ROOT_FILES)]
    ranges.extend(("extras", rel) for rel in (extras or []))
    for group, rel in ranges:
        item = _checked_path(target, rel)
        paths = ([(item, False, item.stat().st_size)] if item.is_file() else
                 _inventory_tree(item, check_cancel) if item.is_dir() else [])
        for child, is_dir, size in paths:
            _check_cancel(check_cancel)
            key = (group, str(child).casefold())
            if not is_dir and key not in seen:
                seen.add(key)
                total += size
    return total

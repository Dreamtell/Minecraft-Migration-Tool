# core/migrator.py
import shutil
import time
import json
import traceback
from pathlib import Path

# 界面语言：日志/文案模板走 trp（中文模式下与原 f-string 逐字一致）
from utils.i18n import trp


# ---------- 工具函数 ----------
def _is_safe_path(rel_path):
    """检查相对路径是否包含 .. 或绝对路径，防止越界"""
    parts = Path(rel_path).parts
    return not any(p == '..' for p in parts) and not Path(rel_path).is_absolute()


def match_mod(item, source_files, name_map):
    """
    在源模组中匹配给定的文件名（支持 [前缀] 清理）
    """
    if item in source_files:
        return item
    if item in name_map:
        return name_map[item]
    clean_item = item
    if clean_item.startswith("[") and "]" in clean_item:
        clean_item = clean_item.split("]", 1)[1].strip()
    for orig in source_files:
        clean_orig = orig
        if clean_orig.startswith("[") and "]" in clean_orig:
            clean_orig = clean_orig.split("]", 1)[1].strip()
        if clean_orig == clean_item:
            return orig
    return None


# ---------- 安全复制 ----------
def safe_copy(src, dst, dry_run, overwrite, is_file=True):
    """
    安全复制文件/目录
    返回: (成功与否, 消息)
    """
    if dry_run:
        return True, "模拟复制"
    if dst.exists() and not overwrite and is_file:
        return False, "目标已存在，跳过"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if is_file:
            shutil.copy2(src, dst)
        else:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        return True, "复制成功"
    except PermissionError:
        return False, f"权限不足：无法写入 {dst}"
    except OSError as e:
        if "No space left" in str(e):
            return False, "磁盘空间不足"
        return False, f"系统错误：{e}"
    except Exception as e:
        return False, f"未知错误：{e}"


def safe_copytree(src, dst, dry_run, log_func=None):
    """
    安全复制目录，冲突时使用时间戳备份
    """
    if dry_run:
        return True, "模拟复制目录"
    if dst.exists():
        # 使用时间戳备份，避免覆盖
        backup = dst.with_suffix(dst.suffix + f".backup_{int(time.time())}")
        try:
            shutil.move(str(dst), str(backup))
            if log_func:
                log_func(f"已备份原有目录至：{backup.name}", "SUCCESS")
        except Exception as e:
            return False, f"备份失败：{e}"
    try:
        shutil.copytree(src, dst)
        return True, "目录复制成功"
    except Exception as e:
        return False, f"复制目录失败：{e}"


# ---------- 历史记录 ----------
def get_history_path(target_path):
    return target_path / ".migration_history.json"


def load_history(target_path):
    hist_path = get_history_path(target_path)
    if hist_path.exists():
        try:
            with open(hist_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []


def save_history(target_path, history):
    hist_path = get_history_path(target_path)
    try:
        with open(hist_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except:
        pass


def add_history_entry(target_path, src_path, modlist, configlist, extralist=None):
    history = load_history(target_path)
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(src_path),
        "target": str(target_path),
        "mod_count": len(modlist),
        "config_count": len(configlist),
        # 「其它文件」清单（相对整合包根目录）
        "extra_count": len(extralist or []),
        "extras": list(extralist or []),
        "mods": modlist[:20],
        "configs": configlist,
        "rolled_back": False,
        "rollback_time": None
    }
    history.append(entry)
    save_history(target_path, history)
    return entry


def mark_rollback(target_path):
    history = load_history(target_path)
    if history:
        for entry in reversed(history):
            if not entry.get("rolled_back", False):
                entry["rolled_back"] = True
                entry["rollback_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                save_history(target_path, history)
                return True
    return False


# ---------- 备份与恢复 ----------
# 备份范围必须和"迁移会动的东西"一一对应：少了 → 回滚不干净；多了 → 会把用户迁移后
# 自己改过的文件一起回退掉。所以这里只列迁移真正会写的东西。
# 迁移会写：mods/ config/ saves/ 三个目录 + 「其它文件」清单里的每一条
# （清单是唯一依据，条目由用户自己加：options.txt、servers.dat、shaderpacks/…）。
# 「其它文件」清单是**动态**的，所以备份时不靠常量，而是把这次迁移的清单传进来 ——
# 不然"覆盖（先备份）"对 options.txt 之外的东西就是空话，回滚也还原不了。
# 注意：**不再自动复制 options.txt**了（用户要求"一切都要自己选"）—— 想带就加进清单；
# 万一清单里也有它，它同样会被 _extras 那份备份覆盖到（_BACKUP_ROOT_FILES 兜底）。
# （以后 run_migration 新增写目标根的文件，必须同步加进 _BACKUP_ROOT_FILES）
_BACKUP_DIRS = ("mods", "config", "saves")
_BACKUP_ROOT_FILES = ("options.txt",)
_BACKUP_FILES_SUBDIR = "_files"
_BACKUP_EXTRAS_SUBDIR = "_extras"      # 「其它文件」清单里各条目的备份（保持相对结构）
_BACKUP_MANIFEST = "manifest.json"


def get_backup_path(target_path):
    return Path(target_path) / ".migrate_backup"


def _norm_rel(rel):
    """清单条目 → 备份用的规范相对路径（正斜杠、无前导/结尾斜杠）。不合规返回 ""。"""
    条目 = str(rel or "").replace("\\", "/").strip().strip("/")
    if not 条目 or not _is_safe_path(条目):
        return ""
    return 条目


def _write_manifest(backup_root, exists_dirs, exists_files, exists_extras=None):
    """记录"迁移前哪些目录/文件存在"，回滚时才知道哪些是迁移新建的、该删掉。

    `extras` 记的是这次迁移清单里每一条在迁移前存不存在（键=相对路径）。
    旧备份没有这个键，回滚时按"没有清单"处理（只恢复固定三目录+options.txt）。
    """
    data = {
        "version": 3,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dirs": {name: bool(exists_dirs.get(name)) for name in _BACKUP_DIRS},
        "files": {name: bool(exists_files.get(name)) for name in _BACKUP_ROOT_FILES},
        "extras": {k: bool(v) for k, v in (exists_extras or {}).items()},
    }
    try:
        with open(backup_root / _BACKUP_MANIFEST, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    return data


def read_manifest(backup_root):
    """读备份清单。旧版本备份没有清单，返回 None，调用方按兼容模式处理。"""
    try:
        with open(Path(backup_root) / _BACKUP_MANIFEST, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def do_backup(target_path, log_func=None, extra_entries=None):
    """
    备份目标实例里"迁移可能改动的部分"，若失败则抛出异常。

    `extra_entries`：这次迁移的「其它文件」清单（相对整合包根目录，文件或目录都行）。
    必须传进来 —— 迁移会覆盖/新建它们，备份漏了的话，"覆盖（先备份）"这句承诺对
    options.txt 之外的东西就是空话，回滚也还原不了。
    """
    target_path = Path(target_path)
    backup_root = get_backup_path(target_path)
    if log_func:
        log_func("📦 开始备份目标实例...", "INFO")

    if backup_root.exists():
        if log_func:
            log_func(f"🗑️ 删除旧备份：{backup_root}", "INFO")
        shutil.rmtree(backup_root)
    backup_root.mkdir(parents=True)

    backed = []
    exists_dirs = {}
    for folder in _BACKUP_DIRS:
        src = target_path / folder
        exists_dirs[folder] = src.exists()
        if src.exists():
            dst = backup_root / folder
            if log_func:
                log_func(f"📂 备份 {folder} → {dst}", "INFO")
            shutil.copytree(src, dst)
            backed.append(folder)
        else:
            if log_func:
                log_func(f"ℹ️ {folder} 不存在，跳过备份"
                         f"（回滚时会删掉迁移新建的同名目录）", "INFO")

    # 根级文件（options.txt 等）：迁移也会覆盖它们，漏了就不是真回滚
    files_root = backup_root / _BACKUP_FILES_SUBDIR
    exists_files = {}
    backed_files = []
    for name in _BACKUP_ROOT_FILES:
        src = target_path / name
        exists_files[name] = src.exists()
        if src.is_file():
            files_root.mkdir(parents=True, exist_ok=True)
            dst = files_root / name
            if log_func:
                log_func(f"📂 备份 {name} → {dst}", "INFO")
            shutil.copy2(src, dst)
            backed_files.append(name)

    # 「其它文件」清单里的每一条（用户自己选的）：迁移会覆盖/新建它们，
    # 所以也要"存在性 + 内容"一起记下来，回滚才能既还原旧内容、又删掉迁移新建的。
    extras_root = backup_root / _BACKUP_EXTRAS_SUBDIR
    exists_extras = {}
    backed_extras = []
    for raw in (extra_entries or []):
        rel = _norm_rel(raw)
        if not rel or rel in exists_extras:
            continue
        src = target_path / rel
        exists_extras[rel] = src.exists()
        if not src.exists():
            if log_func:
                log_func(f"ℹ️ {rel} 目标里不存在，跳过备份"
                         f"（回滚时会删掉迁移新建的同名条目）", "INFO")
            continue
        dst = extras_root / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            backed_extras.append(rel)
            if log_func:
                log_func(f"📂 备份清单条目 {rel} → {dst}", "INFO")
        except Exception as e:
            # 备份不到就不能装作没事：宁可整个备份失败（调用方会拦下迁移）
            raise RuntimeError(f"备份「其它文件」条目 {rel} 失败：{e}")

    _write_manifest(backup_root, exists_dirs, exists_files, exists_extras)

    if log_func:
        done = ", ".join(backed + backed_files + backed_extras)
        log_func(f"✅ 备份完成，已备份：{done if done else '无'}", "SUCCESS")


def do_restore(target_path, log_func=None):
    """
    从备份恢复目标实例
    返回: 是否成功

    和旧版的三点区别：
    1. 目标根的 options.txt 这类文件也在恢复范围内（以前漏了，回滚后它还是源实例的版本）；
    2. 迁移前不存在的目录/文件，回滚时会被清掉，否则全新实例回滚完仍是迁移产物的堆；
    3. 任何一项出错都返回 False，不再"什么都没恢复却报成功"。

    现在再加上：「其它文件」清单里的条目（用户自己加的那些）也一并还原/清理 ——
    迁移会覆盖它们，只恢复固定三目录的话，用户清单里的东西被盖了就回不来。
    旧备份（没有 extras 清单）照样能恢复，只是按老范围来。
    """
    target_path = Path(target_path)
    backup_root = get_backup_path(target_path)
    if not backup_root.exists():
        if log_func:
            log_func("❌ 恢复失败：备份目录不存在", "ERROR")
        return False

    manifest = read_manifest(backup_root)
    if manifest is None and log_func:
        log_func("⚠️ 这份备份没有清单（旧版本留下的）：只恢复已有内容，"
                 "不会清理迁移新建的部分", "WARNING")
    if log_func:
        log_func("🔄 开始从备份恢复...", "INFO")

    restored, cleaned, failed = [], [], []

    for folder in _BACKUP_DIRS:
        target_folder = target_path / folder
        backup_folder = backup_root / folder
        try:
            if backup_folder.is_dir():
                if target_folder.exists():
                    if log_func:
                        log_func(f"🗑️ 删除现有目录：{target_folder}", "INFO")
                    shutil.rmtree(target_folder)
                if log_func:
                    log_func(f"📂 恢复备份：{backup_folder} → {target_folder}", "INFO")
                shutil.copytree(backup_folder, target_folder)
                restored.append(folder + "/")
            elif manifest is not None and not manifest.get("dirs", {}).get(folder, True):
                # 迁移前本来没这个目录 → 现在还留着，说明是迁移建的
                if target_folder.exists():
                    if log_func:
                        log_func(f"🗑️ 迁移新建的 {folder}/，按回滚要求删除", "INFO")
                    shutil.rmtree(target_folder)
                    cleaned.append(folder + "/")
            else:
                if log_func:
                    log_func(f"ℹ️ 备份中不存在 {folder}，跳过", "INFO")
        except Exception as e:
            failed.append(f"{folder}: {e}")
            if log_func:
                log_func(f"❌ 恢复 {folder} 失败: {e}", "ERROR")

    files_root = backup_root / _BACKUP_FILES_SUBDIR
    for name in _BACKUP_ROOT_FILES:
        target_file = target_path / name
        backup_file = files_root / name
        try:
            if backup_file.is_file():
                if log_func:
                    log_func(f"📂 恢复文件：{name} → {target_file}", "INFO")
                shutil.copy2(backup_file, target_file)
                restored.append(name)
            elif manifest is not None and not manifest.get("files", {}).get(name, True):
                if target_file.exists():
                    if log_func:
                        log_func(f"🗑️ 迁移新建的 {name}，按回滚要求删除", "INFO")
                    target_file.unlink()
                    cleaned.append(name)
        except Exception as e:
            failed.append(f"{name}: {e}")
            if log_func:
                log_func(f"❌ 恢复 {name} 失败: {e}", "ERROR")

    # 「其它文件」清单里的条目（用户自己选的）：迁移前存在的要还原，迁移前没有的要删掉
    # —— 只恢复固定三目录+options.txt 的话，用户清单里的东西被覆盖了就回不来。
    extras_root = backup_root / _BACKUP_EXTRAS_SUBDIR
    for rel, 原来有 in sorted((manifest or {}).get("extras", {}).items()):
        rel = _norm_rel(rel)
        if not rel:
            continue
        target_item = target_path / rel
        backup_item = extras_root / rel
        try:
            if 原来有 and backup_item.exists():
                if target_item.is_dir():
                    shutil.rmtree(target_item)
                elif target_item.exists():
                    target_item.unlink()
                target_item.parent.mkdir(parents=True, exist_ok=True)
                if backup_item.is_dir():
                    shutil.copytree(backup_item, target_item, dirs_exist_ok=True)
                else:
                    shutil.copy2(backup_item, target_item)
                restored.append(rel)
                if log_func:
                    log_func(f"📂 恢复清单条目：{rel} → {target_item}", "INFO")
            elif not 原来有:
                if target_item.is_dir():
                    if log_func:
                        log_func(f"🗑️ 迁移新建的清单条目 {rel}/，按回滚要求删除", "INFO")
                    shutil.rmtree(target_item)
                    cleaned.append(rel + "/")
                elif target_item.exists():
                    if log_func:
                        log_func(f"🗑️ 迁移新建的清单条目 {rel}，按回滚要求删除", "INFO")
                    target_item.unlink()
                    cleaned.append(rel)
        except Exception as e:
            failed.append(f"{rel}: {e}")
            if log_func:
                log_func(f"❌ 恢复清单条目 {rel} 失败: {e}", "ERROR")

    if failed:
        if log_func:
            log_func(f"❌ 恢复未完成，失败 {len(failed)} 项：{'; '.join(failed)}", "ERROR")
        return False

    if log_func:
        detail = f"：{', '.join(restored)}" if restored else ""
        log_func(f"✅ 恢复完成，已恢复 {len(restored)} 项"
                 + (f"，清理迁移新建的 {len(cleaned)} 项" if cleaned else "")
                 + detail, "SUCCESS")
    return True


# ---------- 更新模组时的旧版本清理 ----------
# 迁移是"按文件名复制"的：更新版文件名不同（create-6.0.8.jar → create-6.0.9.jar），
# 目标的旧版不会被覆盖，于是同一个 mod 两个版本共存——Forge 启动直接
# Duplicate mod 崩溃，Fabric 报 Duplicate mod ID。这里按 **modid** 找出旧版并移走。
_MODID_CACHE = {}          # {路径: (mtime, size, modid)} 进程内缓存，同一文件只解压一次


def _modid_of(jar_path):
    """读 jar 的 modid；读不到（坏包/占位符/非模组文件）返回 None。"""
    try:
        jar_path = Path(jar_path)
        st = jar_path.stat()
        key = str(jar_path)
        hit = _MODID_CACHE.get(key)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
        from core.scanner import get_mod_metadata
        modid = get_mod_metadata(str(jar_path))[0]
        modid = (modid or "").strip().lower()
        if not modid or modid in ("未知", "unknown", "?", "无"):
            modid = None
        _MODID_CACHE[key] = (st.st_mtime, st.st_size, modid)
        return modid
    except Exception:
        return None


def index_modids(mods_dir):
    """把 mods 目录里的 jar 按 modid 建索引：{modid: [Path, ...]}。

    解析不出 modid 的会被跳过——这类文件绝不参与"旧版本清理"：
    宁可留着让用户自己判断，也不能凭文件名猜错、误删别人的模组。
    """
    index = {}
    try:
        for jar in sorted(Path(mods_dir).glob("*.jar")):
            modid = _modid_of(jar)
            if modid:
                index.setdefault(modid, []).append(jar)
    except Exception:
        pass
    return index


def _record_removed(target_path, modid, moved_names, replaced_by):
    """把"移走了哪些旧版本"记进备份目录，方便用户事后查看（回滚不依赖它）。"""
    try:
        path = get_backup_path(target_path) / "removed_mods" / "_removed.json"
        data = []
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = []
        data.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "modid": modid,
            "removed": list(moved_names),
            "replaced_by": replaced_by,
        })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def remove_old_mod_versions(target_path, target_mods, src_jar, dst_path, index=None,
                            log_func=None, dry_run=False):
    """把目标 mods 里同 modid 的旧版本移进 .migrate_backup/removed_mods/。

    返回被移走的文件名列表。只认 modid；移走而不是删除，回滚时 do_restore 会
    用备份里的 mods 整目录恢复，所以这一步是可逆的。
    """
    if dry_run or index is None:
        return []
    modid = _modid_of(src_jar)
    if not modid:
        # 读不出 modid 就没法判断谁是谁，宁可不清理，也不凭文件名猜
        if log_func:
            log_func(f"ℹ️ 无法识别 {Path(src_jar).name} 的 modid，跳过旧版本清理"
                     f"（如目标里已存在同模组的其他版本，请手动确认）", "INFO")
        return []
    try:
        dst_resolved = Path(dst_path).resolve()
    except Exception:
        dst_resolved = None
    olds = []
    for p in index.get(modid, []):
        try:
            if not p.exists():
                continue
            if dst_resolved is not None and p.resolve() == dst_resolved:
                continue                      # 就是它自己，不用动
            olds.append(p)
        except Exception:
            continue
    if not olds:
        return []
    dest_dir = get_backup_path(target_path) / "removed_mods"
    moved = []
    for old in olds:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / old.name
            n = 1
            while target.exists():
                target = dest_dir / f"{old.stem}.{n}{old.suffix}"
                n += 1
            shutil.move(str(old), str(target))
            moved.append(old.name)
            try:
                index[modid].remove(old)      # 从索引摘掉，别被后面的条目再匹配到
            except ValueError:
                pass
            if log_func:
                log_func(f"🗑️ 移除目标旧版本：{old.name}（同 modid: {modid}）"
                         f"，已移入备份 .migrate_backup/removed_mods", "WARNING")
        except Exception as e:
            if log_func:
                log_func(f"⚠️ 移除旧版本 {old.name} 失败：{e}（该文件保持原样）", "WARNING")
    if moved:
        _record_removed(target_path, modid, moved, Path(dst_path).name)
    return moved


# ---------- 主迁移函数 ----------
def run_migration(
        src_path,
        tgt_path,
        world_name,
        modlist,
        configlist,
        dry_run,
        overwrite,
        progress_callback=None,
        log_callback=None,
        check_cancel=None,
        add_history=True,
        rename_marker=None,
        extralist=None,
        extra_overwrite=True
):
    """
    执行迁移主流程
    rename_marker: 非空时，复制过去的模组会在文件名前加这个前缀（如 "★ "），
                   方便用户在目标目录里一眼认出哪些是本次迁移带过去的。
                   只改目标文件名，不改内容；加载器只认 jar 里的 modid，不受影响。
    extralist:     「其它文件」清单，路径**相对整合包根目录**（不是相对 config），
                   用来带走 mods/config/saves 之外的东西：shaderpacks/、resourcepacks/、
                   options.txt、servers.dat、kubejs/ 之类。文件或文件夹都行，文件夹递归。
    extra_overwrite: 上面这份清单遇到"目标已有同名文件"时怎么办：
                   True=覆盖（先备份，和其它清单一致）；False=跳过，目标保持不动。
    返回: 是否成功完成
    """
    src_path = Path(src_path)
    tgt_path = Path(tgt_path)
    marker = (rename_marker or "").strip()
    if marker:
        marker = marker + " " if not marker.endswith(" ") else marker
        local_log = log_callback
        if local_log:
            local_log(f"🏷️ 已启用迁移标记：复制过去的模组会加前缀「{marker.strip()} 」", "INFO")

    def log(msg, level="INFO"):
        if log_callback:
            log_callback(msg, level)

    def progress(file_index, file_name, copied_bytes, step=None):
        if progress_callback:
            progress_callback(file_index, file_name, copied_bytes, step)

    try:
        copied_bytes = 0
        file_index = 0

        # -------- 步骤1: 复制模组 --------
        log("【步骤1】复制模组...", "INFO")
        src_mods = src_path / "mods"
        tgt_mods = tgt_path / "mods"
        if not src_mods.exists():
            log(trp("⚠️ 旧 mods 目录不存在: {0}，跳过", src_mods), "WARNING")
        else:
            if not dry_run:
                tgt_mods.mkdir(parents=True, exist_ok=True)

            source_files = {f.name: f for f in src_mods.glob("*.jar")}
            name_map = {}
            for orig in source_files:
                clean = orig
                if clean.startswith("[") and "]" in clean:
                    clean = clean.split("]", 1)[1].strip()
                name_map[clean] = orig
                name_map[orig] = orig

            success = 0
            skipped = 0
            removed_old = 0
            failed = []
            total_mods = len(modlist)
            # 目标 mods 的 modid 索引：用来识别"同一个 mod 的旧版本"。
            # 只有真要复制（非模拟）且目标已有 mods 时才建，建一次给全程用。
            target_index = None
            if not dry_run and tgt_mods.exists():
                _t0 = time.perf_counter()
                target_index = index_modids(tgt_mods)
                log(trp("🔎 已索引目标 mods（{0} 个 modid，耗时 {1:.1f}s），用于识别旧版本", len(target_index), time.perf_counter() - _t0), "INFO")
            for idx, item in enumerate(modlist):
                if check_cancel and check_cancel():
                    log("⚠️ 用户取消了迁移", "WARNING")
                    return False

                # 支持“完整路径”条目：直接按该文件复制；否则回退到源目录名匹配
                direct_src = Path(item)
                if direct_src.is_file() and direct_src.suffix.lower() == ".jar":
                    matched = direct_src.name
                    src_file = direct_src
                else:
                    matched = match_mod(item, source_files, name_map)
                    if not matched:
                        failed.append((item, "未找到匹配的文件"))
                        log(trp("❌ 未找到匹配模组: {0}", item), "ERROR")
                        continue
                    src_file = source_files[matched]

                dst_file = tgt_mods / matched
                # 迁移标记：目标文件名前加前缀，方便用户在 mods 目录里辨认
                if marker:
                    dst_file = tgt_mods / (marker + matched)
                if dst_file.exists() and not overwrite and not dry_run:
                    log(trp("⏭️ 跳过已存在的模组: {0}", dst_file.name), "WARNING")
                    skipped += 1
                    continue
                # 复制之前先清掉目标里同 modid 的旧版本（否则两个版本共存会崩）
                removed_old += len(remove_old_mod_versions(
                    tgt_path, tgt_mods, src_file, dst_file,
                    index=target_index, log_func=log, dry_run=dry_run))
                ok, msg = safe_copy(src_file, dst_file, dry_run, overwrite,
                                    is_file=True)
                if ok:
                    success += 1
                    file_index += 1
                    copied_bytes += src_file.stat().st_size
                    if dry_run:
                        log(trp("[模拟] 将复制: {0}", dst_file.name), "SIMULATE")
                    else:
                        log(trp("✅ 已复制: {0}", dst_file.name), "SUCCESS")
                    step = f"复制模组 ({idx + 1}/{total_mods})"
                    progress(file_index, dst_file.name, copied_bytes, step)
                else:
                    failed.append((item, msg))
                    log(trp("❌ 复制失败 {0}: {1}", matched, msg), "ERROR")
            log(trp("模组复制完成: 成功 {0} 个, 跳过 {1} 个, 失败 {2} 个", success, skipped, len(failed)), "INFO")
            if removed_old:
                log(trp("🧹 已移除 {0} 个同 modid 的旧版本（移入 .migrate_backup/removed_mods，回滚时会自动恢复）", removed_old), "SUCCESS")

        # -------- 步骤2: 复制存档 --------
        log("\n【步骤2】复制存档...", "INFO")
        if check_cancel and check_cancel():
            log("⚠️ 用户取消了迁移", "WARNING")
            return False

        src_world = src_path / "saves" / world_name
        dst_world = tgt_path / "saves" / world_name
        if not src_world.exists():
            log(trp("⚠️ 源存档不存在: {0}，跳过", src_world), "WARNING")
        else:
            if not dry_run:
                dst_world.parent.mkdir(parents=True, exist_ok=True)
            world_files = list(src_world.rglob("*"))
            total_world_files = sum(1 for f in world_files if f.is_file())
            for idx, src_file in enumerate(world_files):
                if not src_file.is_file():
                    continue
                rel = src_file.relative_to(src_world)
                dst_file = dst_world / rel
                ok, msg = safe_copy(src_file, dst_file, dry_run, overwrite=True,
                                    is_file=True)
                if ok:
                    file_index += 1
                    copied_bytes += src_file.stat().st_size
                    step = f"复制存档 ({idx + 1}/{total_world_files})"
                    progress(file_index, f"存档/{rel}", copied_bytes, step)
                else:
                    log(trp("❌ 复制存档文件 {0} 失败: {1}", rel, msg), "ERROR")
            log(trp("✅ 存档 {0} 已{1}复制完成，共 {2} 个文件", world_name,
                    trp('模拟') if dry_run else '', total_world_files), "SUCCESS")

        # -------- 步骤3: 复制 config --------
        log("\n【步骤3】复制 config 内容...", "INFO")
        if check_cancel and check_cancel():
            log("⚠️ 用户取消了迁移", "WARNING")
            return False

        src_config = src_path / "config"
        tgt_config = tgt_path / "config"
        if not configlist:
            log("ℹ️ config 清单为空，跳过", "INFO")
        elif not src_config.exists():
            log(trp("⚠️ 源 config 目录不存在: {0}，跳过", src_config), "WARNING")
        else:
            if not dry_run:
                tgt_config.mkdir(parents=True, exist_ok=True)

            success_cfg = 0
            failed_cfg = []
            total_config_entries = len(configlist)
            for idx, entry in enumerate(configlist):
                if check_cancel and check_cancel():
                    log("⚠️ 用户取消了迁移", "WARNING")
                    return False

                if not _is_safe_path(entry):
                    log(trp("⚠️ 跳过不安全 config 路径: {0}", entry), "WARNING")
                    continue

                src_entry = src_config / entry
                if not src_entry.exists():
                    log(trp("❌ 源 config 条目不存在: {0}，跳过", entry), "ERROR")
                    failed_cfg.append((entry, "源不存在"))
                    continue

                dst_entry = tgt_config / entry
                if src_entry.is_file():
                    ok, msg = safe_copy(src_entry, dst_entry, dry_run, overwrite=True,
                                        is_file=True)
                    if ok:
                        success_cfg += 1
                        file_index += 1
                        copied_bytes += src_entry.stat().st_size
                        if dry_run:
                            log(trp("[模拟] 将复制 config: {0}", entry), "SIMULATE")
                        else:
                            log(trp("✅ 已复制 config: {0}", entry), "SUCCESS")
                        step = f"复制 config ({idx + 1}/{total_config_entries})"
                        progress(file_index, f"config/{entry}", copied_bytes, step)
                    else:
                        failed_cfg.append((entry, msg))
                        log(trp("❌ 复制 config 失败 {0}: {1}", entry, msg), "ERROR")
                elif src_entry.is_dir():
                    # 递归复制整个文件夹：目录本身也要建出来（含空文件夹），
                    # 每个文件都打日志、更新进度，并且支持中途取消。
                    try:
                        if not dry_run:
                            dst_entry.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        failed_cfg.append((entry, f"创建目录失败: {e}"))
                        log(trp("❌ 创建 config 目录 {0} 失败: {1}", entry, e), "ERROR")
                        continue

                    for src_item in sorted(src_entry.rglob("*")):
                        if check_cancel and check_cancel():
                            log("⚠️ 用户取消了迁移", "WARNING")
                            return False

                        rel = src_item.relative_to(src_entry)
                        dst_item = dst_entry / rel
                        if src_item.is_dir():
                            # 空文件夹也要保留，否则目标端目录结构不完整
                            if dry_run:
                                log(trp("[模拟] 将创建目录: {0}/{1}", entry, rel), "SIMULATE")
                            else:
                                try:
                                    dst_item.mkdir(parents=True, exist_ok=True)
                                except Exception as e:
                                    failed_cfg.append((f"{entry}/{rel}", f"建目录失败: {e}"))
                                    log(trp("❌ 创建 config 目录 {0}/{1} 失败: {2}", entry, rel, e), "ERROR")
                            continue
                        if not src_item.is_file():
                            continue

                        ok, msg = safe_copy(src_item, dst_item, dry_run, overwrite=True,
                                            is_file=True)
                        if ok:
                            success_cfg += 1
                            file_index += 1
                            copied_bytes += src_item.stat().st_size
                            if dry_run:
                                log(trp("[模拟] 将复制 config: {0}/{1}", entry, rel), "SIMULATE")
                            else:
                                log(trp("✅ 已复制 config: {0}/{1}", entry, rel), "SUCCESS")
                            step = f"复制 config ({idx + 1}/{total_config_entries})"
                            progress(file_index, f"config/{entry}/{rel}", copied_bytes, step)
                        else:
                            failed_cfg.append((f"{entry}/{rel}", msg))
                            log(trp("❌ 复制 config 文件 {0}/{1} 失败: {2}", entry, rel, msg), "ERROR")
                else:
                    log(trp("⚠️ config 条目 {0} 非文件非目录，跳过", entry), "WARNING")

            log(trp("config 复制完成: 成功 {0} 个, 失败 {1} 个", success_cfg, len(failed_cfg)), "INFO")

        # -------- 其它文件（路径相对整合包根目录） --------
        # 带走 mods / config / saves 之外的东西：shaderpacks/、resourcepacks/、
        # options.txt、servers.dat、kubejs/ 这类。
        # 同名冲突按 extra_overwrite：True=覆盖（先备份）；False=跳过，目标保持不动。
        if not extralist:
            log("ℹ️ 其它文件清单为空，跳过", "INFO")
        else:
            success_extra = skipped_extra = 0
            failed_extra = []
            total_extra = len(extralist)
            for idx, raw_entry in enumerate(extralist):
                if check_cancel and check_cancel():
                    log("⚠️ 用户取消了迁移", "WARNING")
                    return False

                entry = str(raw_entry).strip().replace("\\", "/")
                if not entry:
                    continue
                if not _is_safe_path(entry):
                    log(trp("⚠️ 跳过不安全路径: {0}", entry), "WARNING")
                    continue

                src_entry = src_path / entry
                dst_entry = tgt_path / entry
                if not src_entry.exists():
                    failed_extra.append((entry, "源不存在"))
                    log(trp("❌ 源条目不存在: {0}，跳过", entry), "ERROR")
                    continue

                step = f"复制其它文件 ({idx + 1}/{total_extra})"

                if src_entry.is_file():
                    if dst_entry.exists() and not extra_overwrite:
                        skipped_extra += 1
                        log(trp("⏭️ 目标已存在，按设置跳过: {0}", entry), "INFO")
                        continue
                    ok, msg = safe_copy(src_entry, dst_entry, dry_run, overwrite=True,
                                        is_file=True)
                    if ok:
                        success_extra += 1
                        file_index += 1
                        copied_bytes += src_entry.stat().st_size
                        if dry_run:
                            log(trp("[模拟] 将复制其它文件: {0}", entry), "SIMULATE")
                        else:
                            log(trp("✅ 已复制其它文件: {0}", entry), "SUCCESS")
                        progress(file_index, entry, copied_bytes, step)
                    else:
                        failed_extra.append((entry, msg))
                        log(trp("❌ 复制其它文件失败 {0}: {1}", entry, msg), "ERROR")

                elif src_entry.is_dir():
                    try:
                        if not dry_run:
                            dst_entry.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        failed_extra.append((entry, f"创建目录失败: {e}"))
                        log(trp("❌ 创建目录 {0} 失败: {1}", entry, e), "ERROR")
                        continue

                    for src_item in sorted(src_entry.rglob("*")):
                        if check_cancel and check_cancel():
                            log("⚠️ 用户取消了迁移", "WARNING")
                            return False

                        rel = src_item.relative_to(src_entry)
                        rel_s = "%s/%s" % (entry.rstrip("/"),
                                           str(rel).replace(chr(92), "/"))
                        dst_item = dst_entry / rel
                        if src_item.is_dir():
                            # 空文件夹也要保留，否则目标端目录结构不完整
                            if dry_run:
                                log(trp("[模拟] 将创建目录: {0}", rel_s), "SIMULATE")
                            else:
                                try:
                                    dst_item.mkdir(parents=True, exist_ok=True)
                                except Exception as e:
                                    failed_extra.append((rel_s, f"建目录失败: {e}"))
                                    log(trp("❌ 创建目录 {0} 失败: {1}", rel_s, e), "ERROR")
                            continue
                        if not src_item.is_file():
                            continue
                        if dst_item.exists() and not extra_overwrite:
                            skipped_extra += 1
                            log(trp("⏭️ 目标已存在，按设置跳过: {0}", rel_s), "INFO")
                            continue

                        ok, msg = safe_copy(src_item, dst_item, dry_run, overwrite=True,
                                            is_file=True)
                        if ok:
                            success_extra += 1
                            file_index += 1
                            copied_bytes += src_item.stat().st_size
                            if dry_run:
                                log(trp("[模拟] 将复制其它文件: {0}", rel_s), "SIMULATE")
                            else:
                                log(trp("✅ 已复制其它文件: {0}", rel_s), "SUCCESS")
                            progress(file_index, rel_s, copied_bytes, step)
                        else:
                            failed_extra.append((rel_s, msg))
                            log(trp("❌ 复制其它文件失败 {0}: {1}", rel_s, msg), "ERROR")
                else:
                    log(trp("⚠️ 条目 {0} 非文件非目录，跳过", entry), "WARNING")

            log(trp("其它文件复制完成: 成功 {0} 个, 跳过 {1} 个, 失败 {2} 个", success_extra, skipped_extra, len(failed_extra)), "INFO")

        # -------- 记录历史 --------
        if not dry_run and add_history:
            add_history_entry(tgt_path, src_path, modlist, configlist,
                              extralist=extralist)
            log(trp("📝 已记录迁移历史到 {0}", get_history_path(tgt_path)), "INFO")

        # -------- 完成 --------
        log("\n========== 迁移完成 ==========", "INFO")
        if dry_run:
            log("这是模拟运行，未实际修改任何文件。如需实际执行，请取消勾选【模拟运行】。", "INFO")
        else:
            log("实际复制完成，请检查日志中的错误信息。", "INFO")

        if progress_callback:
            progress_callback(None, None, None)

        return True

    except Exception as e:
        log(trp("❌ 迁移过程中发生未预期错误: {0}", e), "ERROR")
        log(traceback.format_exc(), "ERROR")
        if progress_callback:
            progress_callback(None, None, None)
        return False

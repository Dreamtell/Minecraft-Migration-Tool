# core/migrator.py
import json
import os
import shutil
import stat
import sys
import time
import traceback
import tempfile
from pathlib import Path

# 界面语言：日志/文案模板走 trp（中文模式下与原 f-string 逐字一致）
from utils.i18n import trp


# ---------- 工具函数 ----------
# Windows 保留设备名：把它们当文件名写入会落到设备上（比如 "nul" 直接吞掉数据），
# 而不是在目标目录里生成一个文件。
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *("COM%d" % i for i in range(1, 10)),
                   *("LPT%d" % i for i in range(1, 10))}


class MigrationCancelled(Exception):
    """Cooperative cancellation; owned by the worker until it exits."""


def _is_safe_path(rel_path):
    p = str(rel_path or "").strip().replace(chr(92), "/")
    if not p or p.startswith("/"):
        return False
    parts = p.rstrip("/").split("/")
    for part in parts:
        if (not part or part in (".", "..") or part != part.rstrip(" .")
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.split(".")[0].strip().upper() in _RESERVED_NAMES):
            return False
    first = parts[0].casefold()
    return not (first.startswith(".migrate_backup")
                or first == ".migration_history.json")


def _is_safe_world_name(name):
    name = str(name or "")
    return (_is_safe_path(name) and name == name.strip()
            and "/" not in name and chr(92) not in name)


def _checked_path(root, rel):
    """Resolve a child and reject links/junctions before writing or copying."""
    if not _is_safe_path(rel):
        raise ValueError(trp("不安全路径：{0}", rel))
    root = Path(root).resolve()
    child = root / str(rel).replace(chr(92), "/").rstrip("/")
    resolved = child.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(trp("路径超出实例目录：{0}", rel))
    current = root
    for part in child.relative_to(root).parts:
        current /= part
        if current.is_symlink() or current.is_junction():
            raise ValueError(trp("不支持符号链接或目录联接：{0}", current))
    return child


def _check_cancel(check_cancel):
    if check_cancel and check_cancel():
        raise MigrationCancelled(trp("用户取消了迁移"))


def _validate_tree(path, check_cancel=None):
    path = Path(path)
    def fail(error):
        raise error
    for base, dirs, files in os.walk(path, followlinks=False, onerror=fail):
        _check_cancel(check_cancel)
        for name in dirs + files:
            item = Path(base) / name
            if item.is_symlink() or item.is_junction():
                raise ValueError(trp("不支持符号链接或目录联接：{0}", item))


def _rmtree(path):
    """删目录，遇到只读文件先去掉只读属性再删。

    实测：Windows 上 shutil.rmtree 碰到只读文件直接 PermissionError。
    而 shutil.copy2 会**保留只读属性** —— 备份/回滚时很容易踩到，
    结果就是「回滚到一半失败，目标目录留在删了一半的状态」。
    """
    p = Path(path)
    if not p.exists():
        return
    if not p.is_dir() or p.is_symlink():
        p.unlink()
        return

    def _onexc(func, target, exc):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except Exception:
            raise exc

    def _onerror(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except Exception:
            raise exc_info[1]

    if sys.version_info >= (3, 12):
        shutil.rmtree(p, onexc=_onexc)
    else:
        shutil.rmtree(p, onerror=_onerror)


def _unlink(path):
    """删单个文件，只读的先解锁（理由同上）。"""
    p = Path(path)
    try:
        p.unlink()
    except PermissionError:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        p.unlink()


# Windows 上「另一个程序正在使用此文件」多半是暂时的：杀软正在扫、客户端刚在读
# 这个 jar、编辑器刚保存过。实测（_dctest/probe_file_lock.py）：对方以共享读打开时
# 我们照样能复制，只有**独占**打开（dwShareMode=0）才会失败，而那种情况通常一两百
# 毫秒就过去了。所以这里不是"探测占用"，而是复制失败时等一等再试。
_重试错误码 = (5, 32, 33)          # 拒绝访问 / 共享冲突 / 锁冲突
_重试次数 = 3
_重试间隔 = 0.18


def _像是被占用(e):
    """判断异常是不是"文件被别的程序占着"这一类（值得重试的）。"""
    if isinstance(e, PermissionError):
        return True
    return getattr(e, "winerror", None) in _重试错误码


def _atomic_copy_file(src, dst):
    """把文件复制到 dst：先写同目录的临时名，成功后再原子替换。

    以前直接 shutil.copy2(src, dst)：目标是已存在的文件时，copy2 会先把它截断，
    中途断电/磁盘满/被占用就留下一个**半截文件**，而原内容已经没了。
    改成临时文件 + os.replace 之后，失败时目标文件保持原样。

    被别的程序短暂占用（杀软扫描、客户端换 jar）时会等一会儿重试，见上面的常量。
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    for 次 in range(_重试次数):
        tmp = None
        try:
            fd, name = tempfile.mkstemp(prefix=".mctool-", suffix=".tmp", dir=dst.parent)
            os.close(fd)
            tmp = Path(name)
            shutil.copy2(src, tmp)
            if dst.exists():
                try:
                    os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
            os.replace(tmp, dst)
            return
        except Exception as e:
            if 次 + 1 >= _重试次数 or not _像是被占用(e):
                raise
            time.sleep(_重试间隔 * (次 + 1))
        finally:
            if tmp is not None and tmp.exists():
                _unlink(tmp)


def _atomic_write_json(path, data):
    """写 JSON：临时文件 + 原子替换，避免写一半留下坏文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mctool-", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            _unlink(tmp)


def _replace_tree_by_copy(src, target):
    """用 src（备份里的目录）替换 target：先把旧的改名保底，复制成功后才删。

    以前是「rmtree(target) 然后 copytree」—— 复制一旦失败，用户的目标目录
    已经没了，只剩 .migrate_backup 里那份要人工去捞。现在旧目录先改名成
    <name>.mctold 留着，复制失败就把它改回去。
    """
    src, target = Path(src), Path(target)
    # Never delete a user's pre-existing .mctold file.
    tmp = tempfile.mkdtemp(prefix=".mctool-restore-", dir=target.parent)
    try:
        old = Path(tmp) / "previous"
        if target.exists():
            target.rename(old)
        try:
            shutil.copytree(src, target)
        except Exception:
            _rmtree(target)
            if old.exists():
                old.rename(target)
            raise
        _rmtree(old)
    finally:
        # If putting the original back fails, keep it for recovery.
        if not (Path(tmp) / "previous").exists():
            _rmtree(tmp)



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
        return True, trp("模拟复制")
    if dst.exists() and not overwrite and is_file:
        return False, trp("目标已存在，跳过")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if is_file:
            _atomic_copy_file(src, dst)      # 临时文件 + 原子替换，失败不留半截文件
        else:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        return True, trp("复制成功")
    except PermissionError:
        return False, trp("权限不足：无法写入 {0}", dst)
    except OSError as e:
        if "No space left" in str(e):
            return False, trp("磁盘空间不足")
        return False, trp("系统错误：{0}", e)
    except Exception as e:
        return False, trp("未知错误：{0}", e)


def safe_copytree(src, dst, dry_run, log_func=None):
    """
    安全复制目录，冲突时使用时间戳备份
    """
    if dry_run:
        return True, trp("模拟复制目录")
    if dst.exists():
        # 使用时间戳备份，避免覆盖
        backup = dst.with_suffix(dst.suffix + f".backup_{int(time.time())}")
        try:
            shutil.move(str(dst), str(backup))
            if log_func:
                log_func(trp("已备份原有目录至：{0}", backup.name), "SUCCESS")
        except Exception as e:
            return False, trp("备份失败：{0}", e)
    try:
        shutil.copytree(src, dst)
        return True, trp("目录复制成功")
    except Exception as e:
        return False, trp("复制目录失败：{0}", e)


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
        # 原子写：崩在半路也不会留下坏 JSON（坏文件会让 load_history 直接当空历史）
        _atomic_write_json(hist_path, history)
        return True
    except Exception:
        return False


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


def mark_rollback(target_path, record_id=None):
    """把"这次回滚对应哪条迁移"记下来。

    给了 record_id 就同时标在迁移档案上（历史窗口里那份），
    老的目标内 .migration_history.json 也照样标一条（向后兼容）。
    """
    if record_id:
        try:
            from utils import history_store
            history_store.mark_rolled_back(record_id)
        except Exception:
            pass
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
# 回滚时允许"只还原一部分"：这五个键就是用户可勾选的类别。
#   mods/config/saves = 三个固定目录；files = 根目录那几个文件（options.txt 等）；
#   extras = 「其它文件」清单里用户自己加的那些。
_RESTORE_PARTS = ("mods", "config", "saves", "files", "extras")
_RESTORE_PART_NAMES = {
    "mods": "模组（mods/）",
    "config": "配置（config/）",
    "saves": "存档（saves/）",
    "files": "根目录文件（options.txt 等）",
    "extras": "「其它文件」清单里的条目",
}
_BACKUP_MANIFEST = "manifest.json"


def get_backup_path(target_path):
    return Path(target_path) / ".migrate_backup"


# 每条迁移记录各留一份备份：回滚时可以选"回到哪一次之前"。
# 目录形如 <目标>/.migrate_backup/rec_<档案id>/，老的单一备份（.migrate_backup 本身）
# 继续认，回滚时没指定就用它（兼容旧版本留下的备份）。
_KEEP_RECORD_BACKUPS = 5
_RECORD_PREFIX = "rec_"


def get_record_backup_path(target_path, record_id):
    if not _is_safe_world_name(record_id):
        raise ValueError(trp("不安全路径：{0}", record_id))
    return get_backup_path(target_path) / (_RECORD_PREFIX + str(record_id))


def list_record_backups(target_path):
    """返回 {档案id: 备份目录}，只列存在的。"""
    出 = {}
    try:
        for p in get_backup_path(target_path).iterdir():
            if p.is_dir() and p.name.startswith(_RECORD_PREFIX):
                出[p.name[len(_RECORD_PREFIX):]] = p
    except Exception:
        pass
    return 出


def _prune_record_backups(target_path, keep=_KEEP_RECORD_BACKUPS, protect=None):
    """只留最近 keep 份按记录分的备份（按目录修改时间）。"""
    try:
        项 = sorted(list_record_backups(target_path).values(),
                    key=lambda p: p.stat().st_mtime, reverse=True)
        protected = get_record_backup_path(target_path, protect) if protect else None
        if protected in 项:
            项.remove(protected)
            项.insert(0, protected)
        for 旧 in 项[max(1, keep):]:
            if read_manifest(旧) is not None:
                _rmtree(旧)
    except Exception:
        pass


def prune_legacy_backup(target_path, log_func=None):
    """清掉旧版留下的那一份单一备份（顶层那些，不动 rec_ 子目录）。

    只在新备份写成功之后调用 —— 那会儿旧备份才真的多余。
    返回删掉了几项（0 表示本来就没有旧的）。
    """
    根 = get_backup_path(target_path)
    删了 = 0
    # 旧版写盘时先写 .migrate_backup.tmp 再换名，异常时可能留个临时目录
    try:
        临时 = Path(str(根) + ".tmp")
        if 临时.is_dir():
            shutil.rmtree(临时, ignore_errors=True)
            删了 += 1
    except Exception:
        pass
    try:
        项们 = list(根.iterdir())
    except Exception:
        return 删了
    for 项 in 项们:
        if 项.is_dir() and 项.name.startswith(_RECORD_PREFIX):
            continue
        try:
            if 项.is_dir():
                shutil.rmtree(项)
            else:
                项.unlink()
            删了 += 1
        except Exception as e:
            if log_func:
                log_func(trp("⚠️ 旧的单一备份没清掉：{0}", e), "WARNING")
    return 删了


def _norm_rel(rel):
    """清单条目 → 备份用的规范相对路径（正斜杠、无前导/结尾斜杠）。不合规返回 ""。"""
    条目 = str(rel or "").replace("\\", "/").strip().rstrip("/")
    if not 条目 or not _is_safe_path(条目):
        return ""
    return 条目


def _write_manifest(backup_root, exists_dirs, exists_files, exists_extras=None,
                    record_id=None, created_parents=None):
    """记录"迁移前哪些目录/文件存在"，回滚时才知道哪些是迁移新建的、该删掉。

    `extras` 记的是这次迁移清单里每一条在迁移前存不存在（键=相对路径）。
    旧备份没有这个键，回滚时按"没有清单"处理（只恢复固定三目录+options.txt）。
    """
    data = {
        "version": 4,
        "created_parents": sorted(created_parents or []),
        "record": str(record_id or ""),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dirs": {name: bool(exists_dirs.get(name)) for name in _BACKUP_DIRS},
        "files": {name: bool(exists_files.get(name)) for name in _BACKUP_ROOT_FILES},
        "extras": {k: bool(v) for k, v in (exists_extras or {}).items()},
    }
    try:
        _atomic_write_json(backup_root / _BACKUP_MANIFEST, data)
    except Exception:
        # 清单写不进去 = 回滚时无法判断"哪些是迁移新建的"，等于回滚能力残缺。
        # 以前这里静默 pass，现在必须让上层的备份流程失败。
        raise
    return data


def read_manifest(backup_root):
    """读备份清单。旧版本备份没有清单，返回 None，调用方按兼容模式处理。"""
    try:
        with open(Path(backup_root) / _BACKUP_MANIFEST, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def do_backup(target_path, log_func=None, extra_entries=None, record_id=None,
              check_cancel=None):
    """
    备份目标实例里"迁移可能改动的部分"，若失败则抛出异常。

    `extra_entries`：这次迁移的「其它文件」清单（相对整合包根目录，文件或目录都行）。
    必须传进来 —— 迁移会覆盖/新建它们，备份漏了的话，"覆盖（先备份）"这句承诺对
    options.txt 之外的东西就是空话、回滚也还原不了。

    写入顺序：先完整备份到 .migrate_backup.tmp，**成功之后**才用它替换旧备份。
    以前是先 rmtree 旧备份再建新的 —— 新备份一旦失败（空间不足/权限），
    用户连上一次的回滚点都没了。
    """
    target_path = Path(target_path).resolve()
    backup_base = get_backup_path(target_path)
    if backup_base.is_symlink() or backup_base.is_junction():
        raise ValueError(trp("不支持符号链接或目录联接：{0}", backup_base))
    # 有 record_id 就存到 rec_<id>/ 下（一条记录一份，可回滚到任意一次）；
    # 没有就沿用老的单一备份目录。
    backup_root = (get_record_backup_path(target_path, record_id)
                   if record_id else get_backup_path(target_path))
    if log_func:
        if record_id:
            log_func(trp("📦 开始备份目标实例...（回滚点：{0}）", record_id), "INFO")
        else:
            log_func(trp("📦 开始备份目标实例..."), "INFO")

    _check_cancel(check_cancel)
    # Validate all requested ranges before creating staging inside the instance.
    for rel in list(_BACKUP_DIRS) + list(_BACKUP_ROOT_FILES) + list(extra_entries or []):
        item = _checked_path(target_path, rel)
        if item.is_dir():
            _validate_tree(item, check_cancel)

    def copy_file(src, dst):
        _check_cancel(check_cancel)
        return shutil.copy2(src, dst)

    for suffix in ("", ".tmp", ".old"):
        candidate = backup_root.with_name(backup_root.name + suffix)
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError(trp("不支持符号链接或目录联接：{0}", candidate))
    staging = backup_root.with_name(backup_root.name + ".tmp")
    _rmtree(staging)
    staging.mkdir(parents=True)

    try:
        backed = []
        exists_dirs = {}
        for folder in _BACKUP_DIRS:
            src = target_path / folder
            exists_dirs[folder] = src.exists()
            if src.exists():
                if log_func:
                    log_func(trp("📂 备份 {0} → {1}", folder, backup_root / folder), "INFO")
                shutil.copytree(src, staging / folder, copy_function=copy_file)
                backed.append(folder)
            else:
                if log_func:
                    log_func(trp("ℹ️ {0} 不存在，跳过备份（回滚时会删掉迁移新建的同名目录）", folder), "INFO")

        # 根级文件（options.txt 等）：迁移也会覆盖它们，漏了就不是真回滚
        files_root = staging / _BACKUP_FILES_SUBDIR
        exists_files = {}
        backed_files = []
        for name in _BACKUP_ROOT_FILES:
            src = target_path / name
            exists_files[name] = src.exists()
            if src.is_file():
                files_root.mkdir(parents=True, exist_ok=True)
                if log_func:
                    log_func(trp("📂 备份 {0} → {1}", name,
                                 backup_root / _BACKUP_FILES_SUBDIR / name), "INFO")
                _atomic_copy_file(src, files_root / name)
                backed_files.append(name)

        # 「其它文件」清单里的每一条（用户自己选的）：迁移会覆盖/新建它们，
        # 所以也要"存在性 + 内容"一起记下来，回滚才能既还原旧内容、又删掉迁移新建的。
        extras_root = staging / _BACKUP_EXTRAS_SUBDIR
        exists_extras = {}
        created_parents = set()
        backed_extras = []
        for raw in (extra_entries or []):
            rel = _norm_rel(raw)
            if not rel or rel in exists_extras:
                continue
            src = target_path / rel
            exists_extras[rel] = src.exists()
            for parent in src.parents:
                if parent == target_path:
                    break
                if not parent.exists():
                    created_parents.add(parent.relative_to(target_path).as_posix())
            if not src.exists():
                if log_func:
                    log_func(trp("ℹ️ {0} 目标里不存在，跳过备份（回滚时会删掉迁移新建的同名条目）", rel), "INFO")
                continue
            dst = extras_root / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=copy_file)
                else:
                    _atomic_copy_file(src, dst)
                backed_extras.append(rel)
                if log_func:
                    log_func(trp("📂 备份清单条目 {0} → {1}", rel,
                                 backup_root / _BACKUP_EXTRAS_SUBDIR / rel), "INFO")
            except MigrationCancelled:
                raise
            except Exception as e:
                # 备份不到就不能装作没事：宁可整个备份失败（调用方会拦下迁移）
                raise RuntimeError(trp("备份「其它文件」条目 {0} 失败：{1}", rel, e))

        _check_cancel(check_cancel)
        _write_manifest(staging, exists_dirs, exists_files, exists_extras, record_id, created_parents)
    except Exception:
        _rmtree(staging)                 # 失败的半成品不能留着冒充"备份"
        raise

    # 新备份完整了，这时候才动旧备份；换名的过程也是可回退的
    old = backup_root.with_name(backup_root.name + ".old")
    _rmtree(old)
    if backup_root.exists():
        if log_func:
            log_func(trp("🗑️ 替换旧备份（新备份已完整写入）"), "INFO")
        backup_root.rename(old)
    try:
        staging.rename(backup_root)
    except Exception:
        if old.exists() and not backup_root.exists():
            old.rename(backup_root)      # 换名失败就把旧备份放回去
        raise
    _rmtree(old)

    if log_func:
        done = ", ".join(backed + backed_files + backed_extras)
        log_func(trp("✅ 备份完成，已备份：{0}", done if done else trp("无")), "SUCCESS")


def backup_parts_available(backup_root):
    """这份备份里实际存在哪些类别（给"回滚选哪些"的对话框用）。

    返回 {键: 数量}，键见 `_RESTORE_PARTS`；数量对目录类就是目录数，对文件类就是文件数。
    """
    backup_root = Path(backup_root)
    出 = {}
    try:
        manifest = read_manifest(backup_root) or {}
    except Exception:
        manifest = {}
    for folder in _BACKUP_DIRS:
        try:
            目 = backup_root / folder
            count = sum(1 for _ in 目.iterdir()) if 目.is_dir() else 0
            # An absent/empty pre-migration category still has a rollback action.
            出[folder] = count if count else (-1 if folder in manifest.get("dirs", {}) else 0)
        except Exception:
            出[folder] = 0
    try:
        文件们 = backup_root / _BACKUP_FILES_SUBDIR
        出["files"] = sum(1 for n in _BACKUP_ROOT_FILES
                          if (文件们 / n).is_file() or n in manifest.get("files", {}))
    except Exception:
        出["files"] = 0
    try:
        出["extras"] = len((manifest.get("extras") or {}))
    except Exception:
        出["extras"] = 0
    return 出


def do_restore(target_path, log_func=None, backup_dir=None, parts=None):
    """
    从备份恢复目标实例
    返回: 是否成功

    和旧版的三点区别：
    1. 目标根的 options.txt 这类文件也在恢复范围内（以前漏了，回滚后它还是源实例的版本）；
    2. 迁移前不存在的目录/文件，回滚时会被清掉，否则全新实例回滚完仍是迁移产物的堆；
    3. 任何一项出错都返回 False，不再"什么都没恢复却报成功"。

    再加上：「其它文件」清单里的条目（用户自己加的那些）也一并还原/清理 ——
    迁移会覆盖它们，只恢复固定三目录的话，用户清单里的东西被盖了就回不来。
    旧备份（没有 extras 清单）照样能恢复，只是按老范围来。

    安全性（这轮加固）：
      · 恢复目录不再「先 rmtree 再 copytree」——目标目录先改名保底，复制成功才删；
        复制失败会把旧目录改回来，不会出现"目录没了备份也没恢复上"；
      · 删目录/删文件都先摘掉只读属性（copy2 会保留只读，Windows 上直接删会失败）；
      · 单文件恢复走原子替换，失败不会留下半截文件。
    """
    # parts=None 表示"全都还原"（旧行为）；给了就只动列表里的类别。
    # 传给它的键必须是 `_RESTORE_PARTS` 里的，别的忽略。
    if parts is None:
        选 = set(_RESTORE_PARTS)
    else:
        选 = {str(x) for x in parts} & set(_RESTORE_PARTS)
        if not 选:
            选 = set(_RESTORE_PARTS)      # 空集合当"没选"处理，免得点一下什么都没发生

    target_path = Path(target_path)
    # backup_dir 给了就用它（历史窗口里"回滚到这一次"），否则用老的单份备份
    backup_root = Path(backup_dir) if backup_dir else get_backup_path(target_path)
    if not backup_root.exists():
        if log_func:
            log_func(trp("❌ 恢复失败：备份目录不存在"), "ERROR")
        return False

    manifest = read_manifest(backup_root)
    if manifest is None and log_func:
        log_func(trp("⚠️ 这份备份没有清单（旧版本留下的）：只恢复已有内容，不会清理迁移新建的部分"),
                 "WARNING")
    if log_func:
        log_func(trp("🔄 开始从备份恢复..."), "INFO")

    restored, cleaned, failed = [], [], []

    跳过 = [f for f in _BACKUP_DIRS if f not in 选]
    if 跳过 and log_func:
        log_func(trp("⏭️ 按你的选择跳过：{0}", " / ".join(f + "/" for f in 跳过)), "INFO")

    for folder in _BACKUP_DIRS:
        if folder not in 选:
            continue
        backup_folder = backup_root / folder
        try:
            target_folder = _checked_path(target_path, folder)
            if target_folder.is_dir():
                _validate_tree(target_folder)
            if backup_folder.is_dir():
                _validate_tree(backup_folder)
                if log_func:
                    log_func(trp("📂 恢复备份：{0} → {1}", backup_folder, target_folder), "INFO")
                # 旧目录先改名保底，复制成功后再删（失败会自己改回来）
                _replace_tree_by_copy(backup_folder, target_folder)
                restored.append(folder + "/")
            elif manifest is not None and not manifest.get("dirs", {}).get(folder, True):
                # 迁移前本来没这个目录 → 现在还留着，说明是迁移建的
                if target_folder.exists():
                    if log_func:
                        log_func(trp("🗑️ 迁移新建的 {0}/，按回滚要求删除", folder), "INFO")
                    _rmtree(target_folder)
                    cleaned.append(folder + "/")
            elif manifest is not None and manifest.get("dirs", {}).get(folder, True):
                # 清单说"迁移前有"，备份里却没有 → 这份备份不完整，不能默默跳过
                failed.append(trp("{0}: 备份不完整（清单说存在，备份里没有）", folder))
                if log_func:
                    log_func(trp("❌ {0}/ 在清单里标记为存在，但备份里找不到", folder), "ERROR")
            else:
                if log_func:
                    log_func(trp("ℹ️ 备份中不存在 {0}，跳过", folder), "INFO")
        except Exception as e:
            failed.append(f"{folder}: {e}")
            if log_func:
                log_func(trp("❌ 恢复 {0} 失败: {1}", folder, e), "ERROR")

    files_root = backup_root / _BACKUP_FILES_SUBDIR
    if "files" not in 选 and log_func:
        log_func(trp("⏭️ 按你的选择跳过：根目录文件（options.txt 等）"), "INFO")
    for name in (() if "files" not in 选 else _BACKUP_ROOT_FILES):
        backup_file = files_root / name
        try:
            target_file = _checked_path(target_path, name)
            if backup_file.is_file():
                if log_func:
                    log_func(trp("📂 恢复文件：{0} → {1}", name, target_file), "INFO")
                _atomic_copy_file(backup_file, target_file)
                restored.append(name)
            elif manifest is not None and not manifest.get("files", {}).get(name, True):
                if target_file.exists():
                    if log_func:
                        log_func(trp("🗑️ 迁移新建的 {0}，按回滚要求删除", name), "INFO")
                    _unlink(target_file)
                    cleaned.append(name)
            elif manifest is not None:
                failed.append(trp("{0}: 备份不完整（清单说存在，备份里没有）", name))
                if log_func:
                    log_func(trp("❌ {0} 在清单里标记为存在，但备份里找不到", name), "ERROR")
        except Exception as e:
            failed.append(f"{name}: {e}")
            if log_func:
                log_func(trp("❌ 恢复 {0} 失败: {1}", name, e), "ERROR")

    # 「其它文件」清单里的条目（用户自己选的）：迁移前存在的要还原，迁移前没有的要删掉
    # —— 只恢复固定三目录+options.txt 的话，用户清单里的东西被覆盖了就回不来。
    extras_root = backup_root / _BACKUP_EXTRAS_SUBDIR
    if "extras" not in 选 and log_func:
        log_func(trp("⏭️ 按你的选择跳过：「其它文件」清单里的条目"), "INFO")
    for rel, 原来有 in (sorted((manifest or {}).get("extras", {}).items())
                        if "extras" in 选 else []):
        rel = _norm_rel(rel)
        if not rel:
            continue
        backup_item = extras_root / rel
        try:
            target_item = _checked_path(target_path, rel)
            if target_item.is_dir():
                _validate_tree(target_item)
            if 原来有 and backup_item.exists():
                if backup_item.is_dir():
                    _replace_tree_by_copy(backup_item, target_item)
                else:
                    _atomic_copy_file(backup_item, target_item)
                restored.append(rel)
                if log_func:
                    log_func(trp("📂 恢复清单条目：{0} → {1}", rel, target_item), "INFO")
            elif not 原来有:
                if target_item.is_dir():
                    if log_func:
                        log_func(trp("🗑️ 迁移新建的清单条目 {0}/，按回滚要求删除", rel), "INFO")
                    _rmtree(target_item)
                    cleaned.append(rel + "/")
                elif target_item.exists():
                    if log_func:
                        log_func(trp("🗑️ 迁移新建的清单条目 {0}，按回滚要求删除", rel), "INFO")
                    _unlink(target_item)
                    cleaned.append(rel)
            else:
                # 清单说迁移前有、备份里却没有 → 这份备份不完整
                failed.append(trp("{0}: 备份不完整（清单说存在，备份里没有）", rel))
                if log_func:
                    log_func(trp("❌ 清单条目 {0} 标记为存在，但备份里找不到", rel), "ERROR")
        except Exception as e:
            failed.append(f"{rel}: {e}")
            if log_func:
                log_func(trp("❌ 恢复清单条目 {0} 失败: {1}", rel, e), "ERROR")

    # Remove only empty parents known to be absent before migration.
    if "extras" in 选 and not failed:
        for rel in sorted((manifest or {}).get("created_parents", []),
                          key=lambda value: len(Path(value).parts), reverse=True):
            try:
                parent = _checked_path(target_path, rel)
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception as error:
                failed.append(f"{rel}: {error}")

    if failed:
        if log_func:
            log_func(trp("❌ 恢复未完成，失败 {0} 项：{1}", len(failed), "; ".join(failed)),
                     "ERROR")
        return False

    if log_func:
        detail = ", ".join(restored)
        if cleaned and detail:
            log_func(trp("✅ 恢复完成，已恢复 {0} 项，清理迁移新建的 {1} 项：{2}",
                         len(restored), len(cleaned), detail), "SUCCESS")
        elif cleaned:
            log_func(trp("✅ 恢复完成，已恢复 {0} 项，清理迁移新建的 {1} 项",
                         len(restored), len(cleaned)), "SUCCESS")
        elif detail:
            log_func(trp("✅ 恢复完成，已恢复 {0} 项：{1}", len(restored), detail), "SUCCESS")
        else:
            log_func(trp("✅ 恢复完成，已恢复 {0} 项", len(restored)), "SUCCESS")
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
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
        from core.scanner import get_mod_metadata
        modid = get_mod_metadata(jar_path)[0]
        modid = (modid or "").strip().lower()
        if not modid or modid in ("未知", "unknown", "?", "无"):
            modid = None
        _MODID_CACHE[key] = (st.st_mtime_ns, st.st_size, modid)
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
        _atomic_write_json(path, data)
    except Exception:
        pass


def _install_mod(target_path, src, dst, index, log_func):
    """Stage the new JAR, then retire old versions as one reversible operation."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    modid = _modid_of(src)
    olds = [p for p in index.get(modid, []) if p.exists() and p != dst]
    if dst.exists():
        olds.append(dst)
    dest_dir = get_backup_path(target_path) / "removed_mods"
    for candidate in (dest_dir.parent, dest_dir):
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError(trp("不支持符号链接或目录联接：{0}", candidate))
    # Stage beside the destination so the final replace stays on one filesystem.
    with tempfile.TemporaryDirectory(prefix=".mctool-mod-", dir=dst.parent) as tmp:
        staged = Path(tmp) / "new.jar"
        ok, message = safe_copy(src, staged, False, True)
        if not ok:
            return False, message
        moved = []
        try:
            if olds:
                dest_dir.mkdir(parents=True, exist_ok=True)
            for old in dict.fromkeys(olds):
                target = dest_dir / old.name
                n = 1
                while target.exists():
                    target = dest_dir / f"{old.stem}.{n}{old.suffix}"
                    n += 1
                shutil.move(str(old), str(target))
                moved.append((old, target))
            os.replace(staged, dst)
        except Exception:
            for original, saved in reversed(moved):
                os.replace(saved, original)
            raise
    if modid:
        index[modid] = [dst]
    if moved:
        _record_removed(target_path, modid, [p.name for p, _ in moved], dst.name)
    return True, trp("复制成功")


# ---------- 主迁移函数 ----------
def run_migration(src_path, tgt_path, world_name, modlist, configlist, dry_run,
                  overwrite, progress_callback=None, log_callback=None,
                  check_cancel=None, add_history=True, rename_marker=None,
                  extralist=None, extra_overwrite=True, record_id=None, plan=None):
    """Execute a validated inventory; False includes partial failure/cancellation."""
    from core.migration_plan import build_migration_plan
    src_path, tgt_path = Path(src_path).resolve(), Path(tgt_path).resolve()
    started = time.perf_counter()
    logs, failures = [], []
    stats = {key: {"success": 0, "skipped": 0, "failed": 0}
             for key in ("mods", "saves", "config", "extra")}
    outcome = "失败"
    copied_bytes = file_index = 0

    def log(message, level="INFO"):
        logs.append([level, str(message)])
        if log_callback:
            log_callback(message, level)

    def failed(category, label, error):
        stats[category]["failed"] += 1
        failures.append((label, str(error)))
        log(trp("❌ 复制失败 {0}: {1}", label, error), "ERROR")

    try:
        _check_cancel(check_cancel)
        plan = plan or build_migration_plan(
            src_path, tgt_path, world_name, modlist, configlist, extralist,
            overwrite, extra_overwrite, rename_marker, check_cancel)
        if plan["source"] != src_path.resolve() or plan["target"] != tgt_path.resolve():
            raise ValueError(trp("迁移计划与当前实例路径不一致"))
        if not plan["files"] and not plan["dirs"] and not plan["errors"]:
            raise ValueError(trp("没有找到需要复制的文件"))
        # Revalidate endpoints immediately before any mutation (plans may wait on confirmation).
        for item in plan["files"] + plan["dirs"]:
            _checked_path(tgt_path, item["dst"].relative_to(plan["target"]))
        for category, label, error in plan["errors"]:
            failed(category, label, error)
        for warning in plan["warnings"]:
            log(warning, "WARNING")
        for resolution in plan.get("mod_resolutions", []):
            for category in resolution.get("skipped_categories", ["mods"] * len(resolution["skipped"])):
                stats[category]["skipped"] += 1
            log(resolution["message"], "WARNING" if resolution["reason"] in
                ("unknown", "equal_version") else "INFO")

        index = index_modids(tgt_path / "mods") if not dry_run else {}
        for category in ("mods", "saves", "config", "extra"):
            log(trp("【复制阶段】{0}", category), "INFO")
            for item in plan["dirs"]:
                if item["category"] != category:
                    continue
                _check_cancel(check_cancel)
                if not dry_run:
                    try:
                        _checked_path(tgt_path, item["dst"].relative_to(plan["target"]))
                        item["dst"].mkdir(parents=True, exist_ok=True)
                    except OSError as error:
                        failed(category, item["label"], error)
            for item in plan["files"]:
                if item["category"] != category:
                    continue
                _check_cancel(check_cancel)
                src, dst, label = item["src"], item["dst"], item["label"]
                _checked_path(tgt_path, dst.relative_to(plan["target"]))
                if dst.exists() and not item["overwrite"]:
                    stats[category]["skipped"] += 1
                    log(trp("⏭️ 目标已存在，按设置跳过: {0}", label), "INFO")
                    continue
                try:
                    if (dst.parent == tgt_path / "mods" and dst.suffix.lower() == ".jar"
                            and not dry_run):
                        ok, message = _install_mod(tgt_path, src, dst, index, log)
                    else:
                        ok, message = safe_copy(src, dst, dry_run, item["overwrite"])
                except Exception as error:
                    ok, message = False, str(error)
                if not ok:
                    failed(category, label, message)
                    continue
                stats[category]["success"] += 1
                file_index += 1
                copied_bytes += item["size"]
                log(trp("[模拟] 将复制: {0}", label) if dry_run
                    else trp("✅ 已复制: {0}", label), "SIMULATE" if dry_run else "SUCCESS")
                if progress_callback:
                    progress_callback(file_index, label, copied_bytes,
                                      trp("复制文件 ({0}/{1})", file_index, plan["total_files"]))

        _check_cancel(check_cancel)
        outcome = "有失败" if failures else ("模拟运行" if dry_run else "成功")
        if not dry_run and add_history:
            add_history_entry(tgt_path, src_path, modlist, configlist, extralist)
        log(trp("迁移结束：{0}", outcome), "ERROR" if failures else "INFO")
        return not failures
    except MigrationCancelled:
        outcome = "已取消"
        log(trp("⚠️ 用户取消了迁移"), "WARNING")
        return False
    except Exception as error:
        failed("extra", trp("迁移任务"), error)
        log(traceback.format_exc(), "ERROR")
        return False
    finally:
        if add_history:
            try:
                from utils import history_store
                history_store.save_record({
                    "id": record_id, "mode": "dry" if dry_run else "real",
                    "result": outcome, "source": str(src_path), "target": str(tgt_path),
                    "world": world_name, "duration": round(time.perf_counter() - started, 1),
                    "stats": stats,
                    "mod_resolutions": plan.get("mod_resolutions", []) if plan else [],
                    "lists": {"mods": list(modlist or [])[:history_store.LIST_MAX],
                              "configs": list(configlist or [])[:history_store.LIST_MAX],
                              "extras": list(extralist or [])[:history_store.LIST_MAX],
                              "failed": failures[:100]},
                    "log": "\n".join(line for _, line in logs), "log_lines": logs[-2000:],
                })
            except Exception as error:
                log(trp("⚠️ 保存迁移档案失败: {0}", error), "WARNING")
        if progress_callback:
            progress_callback(None, None, None)

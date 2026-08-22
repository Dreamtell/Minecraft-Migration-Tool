# core/migrator.py
import shutil
import time
import json
import traceback
from pathlib import Path
import threading


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


def add_history_entry(target_path, src_path, modlist, configlist):
    history = load_history(target_path)
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(src_path),
        "target": str(target_path),
        "mod_count": len(modlist),
        "config_count": len(configlist),
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
def do_backup(target_path, log_func=None):
    """
    备份目标实例，若失败则抛出异常
    """
    if log_func:
        log_func("📦 开始备份目标实例...", "INFO")
    backup_root = target_path / ".migrate_backup"

    if backup_root.exists():
        if log_func:
            log_func(f"🗑️ 删除旧备份：{backup_root}", "INFO")
        shutil.rmtree(backup_root)
    backup_root.mkdir(parents=True)

    backed = []
    for folder in ["mods", "config", "saves"]:
        src = target_path / folder
        if src.exists():
            dst = backup_root / folder
            if log_func:
                log_func(f"📂 备份 {folder} → {dst}", "INFO")
            shutil.copytree(src, dst)
            backed.append(folder)
        else:
            if log_func:
                log_func(f"ℹ️ {folder} 不存在，跳过备份", "INFO")

    if log_func:
        log_func(f"✅ 备份完成，已备份：{', '.join(backed) if backed else '无'}", "SUCCESS")


def do_restore(target_path, log_func=None):
    """
    从备份恢复目标实例
    返回: 是否成功
    """
    backup_root = target_path / ".migrate_backup"
    if not backup_root.exists():
        if log_func:
            log_func("❌ 恢复失败：备份目录不存在", "ERROR")
        return False

    if log_func:
        log_func("🔄 开始从备份恢复...", "INFO")
    restored = []

    for folder in ["mods", "config", "saves"]:
        target_folder = target_path / folder
        backup_folder = backup_root / folder

        if backup_folder.exists():
            if target_folder.exists():
                if log_func:
                    log_func(f"🗑️ 删除现有目录：{target_folder}", "INFO")
                shutil.rmtree(target_folder)
            if log_func:
                log_func(f"📂 恢复备份：{backup_folder} → {target_folder}", "INFO")
            shutil.copytree(backup_folder, target_folder)
            restored.append(folder)
        else:
            if log_func:
                log_func(f"ℹ️ 备份中不存在 {folder}，跳过", "INFO")

    if log_func:
        log_func(f"✅ 恢复完成，已恢复：{', '.join(restored) if restored else '无'}", "SUCCESS")
    return True


# ---------- 主迁移函数 ----------
def run_migration(
    src_path,
    tgt_path,
    world_name,
    modlist,
    configlist,
    dry_run,
    overwrite,
    progress_callback=None,   # 接收 (file_index, file_name, copied_bytes)
    log_callback=None,        # 接收 (message, level)
    check_cancel=None,        # 返回 True 表示已取消
    add_history=True
):
    """
    执行迁移主流程
    返回: 是否成功完成
    """
    # 将路径转为 Path 对象
    src_path = Path(src_path)
    tgt_path = Path(tgt_path)

    def log(msg, level="INFO"):
        if log_callback:
            log_callback(msg, level)

    def progress(file_index, file_name, copied_bytes):
        if progress_callback:
            progress_callback(file_index, file_name, copied_bytes)

    try:
        copied_bytes = 0
        file_index = 0

        # -------- 步骤1: 复制模组 --------
        log("【步骤1】复制模组...", "INFO")
        src_mods = src_path / "mods"
        tgt_mods = tgt_path / "mods"
        if not src_mods.exists():
            log(f"⚠️ 旧 mods 目录不存在: {src_mods}，跳过", "WARNING")
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
            failed = []
            for item in modlist:
                if check_cancel and check_cancel():
                    log("⚠️ 用户取消了迁移", "WARNING")
                    return False

                matched = match_mod(item, source_files, name_map)
                if matched:
                    src_file = source_files[matched]
                    dst_file = tgt_mods / matched
                    if dst_file.exists() and not overwrite and not dry_run:
                        log(f"⏭️ 跳过已存在的模组: {matched}", "WARNING")
                        skipped += 1
                        continue
                    ok, msg = safe_copy(src_file, dst_file, dry_run, overwrite, is_file=True)
                    if ok:
                        success += 1
                        file_index += 1
                        copied_bytes += src_file.stat().st_size
                        progress(file_index, matched, copied_bytes)
                        if dry_run:
                            log(f"[模拟] 将复制: {matched}", "SIMULATE")
                        else:
                            log(f"✅ 已复制: {matched}", "SUCCESS")
                    else:
                        failed.append((item, msg))
                        log(f"❌ 复制失败 {matched}: {msg}", "ERROR")
                else:
                    failed.append((item, "未找到匹配的文件"))
                    log(f"❌ 未找到匹配模组: {item}", "ERROR")
            log(f"模组复制完成: 成功 {success} 个, 跳过 {skipped} 个, 失败 {len(failed)} 个", "INFO")

        # -------- 步骤2: 复制 options.txt --------
        log("\n【步骤2】复制 options.txt...", "INFO")
        if check_cancel and check_cancel():
            log("⚠️ 用户取消了迁移", "WARNING")
            return False

        src_opts = src_path / "options.txt"
        dst_opts = tgt_path / "options.txt"
        if src_opts.exists():
            ok, msg = safe_copy(src_opts, dst_opts, dry_run, overwrite=True, is_file=True)
            if ok:
                file_index += 1
                copied_bytes += src_opts.stat().st_size
                progress(file_index, "options.txt", copied_bytes)
                log(f"{'[模拟]' if dry_run else '✅'} 已复制 options.txt",
                    "SUCCESS" if not dry_run else "SIMULATE")
            else:
                log(f"❌ 复制 options.txt 失败: {msg}", "ERROR")
        else:
            log("⚠️ 源 options.txt 不存在，跳过", "WARNING")

        # -------- 步骤3: 复制存档 --------
        log("\n【步骤3】复制存档...", "INFO")
        if check_cancel and check_cancel():
            log("⚠️ 用户取消了迁移", "WARNING")
            return False

        src_world = src_path / "saves" / world_name
        dst_world = tgt_path / "saves" / world_name
        if not src_world.exists():
            log(f"⚠️ 源存档不存在: {src_world}，跳过", "WARNING")
        else:
            if not dry_run:
                dst_world.parent.mkdir(parents=True, exist_ok=True)
            world_files = list(src_world.rglob("*"))
            total_world_files = sum(1 for f in world_files if f.is_file())
            for src_file in world_files:
                if not src_file.is_file():
                    continue
                rel = src_file.relative_to(src_world)
                dst_file = dst_world / rel
                ok, msg = safe_copy(src_file, dst_file, dry_run, overwrite=True, is_file=True)
                if ok:
                    file_index += 1
                    copied_bytes += src_file.stat().st_size
                    progress(file_index, f"存档/{rel}", copied_bytes)
                    if not dry_run:
                        log(f"✅ 复制存档文件: {rel}", "SUCCESS")
                else:
                    log(f"❌ 复制存档文件 {rel} 失败: {msg}", "ERROR")
            log(f"✅ 存档 {world_name} 已{'模拟' if dry_run else ''}复制完成，共 {total_world_files} 个文件", "SUCCESS")

        # -------- 步骤4: 复制 config --------
        log("\n【步骤4】复制 config 内容...", "INFO")
        if check_cancel and check_cancel():
            log("⚠️ 用户取消了迁移", "WARNING")
            return False

        src_config = src_path / "config"
        tgt_config = tgt_path / "config"
        if not configlist:
            log("ℹ️ config 清单为空，跳过", "INFO")
        elif not src_config.exists():
            log(f"⚠️ 源 config 目录不存在: {src_config}，跳过", "WARNING")
        else:
            if not dry_run:
                tgt_config.mkdir(parents=True, exist_ok=True)

            success_cfg = 0
            failed_cfg = []
            for entry in configlist:
                if check_cancel and check_cancel():
                    log("⚠️ 用户取消了迁移", "WARNING")
                    return False

                if not _is_safe_path(entry):
                    log(f"⚠️ 跳过不安全 config 路径: {entry}", "WARNING")
                    continue

                src_entry = src_config / entry
                if not src_entry.exists():
                    log(f"❌ 源 config 条目不存在: {entry}，跳过", "ERROR")
                    failed_cfg.append((entry, "源不存在"))
                    continue

                dst_entry = tgt_config / entry
                if src_entry.is_file():
                    ok, msg = safe_copy(src_entry, dst_entry, dry_run, overwrite=True, is_file=True)
                    if ok:
                        success_cfg += 1
                        file_index += 1
                        copied_bytes += src_entry.stat().st_size
                        progress(file_index, f"config/{entry}", copied_bytes)
                        if dry_run:
                            log(f"[模拟] 将复制 config: {entry}", "SIMULATE")
                        else:
                            log(f"✅ 已复制 config: {entry}", "SUCCESS")
                    else:
                        failed_cfg.append((entry, msg))
                        log(f"❌ 复制 config 失败 {entry}: {msg}", "ERROR")
                elif src_entry.is_dir():
                    dir_files = list(src_entry.rglob("*"))
                    for src_file in dir_files:
                        if not src_file.is_file():
                            continue
                        rel = src_file.relative_to(src_entry)
                        dst_file = dst_entry / rel
                        ok, msg = safe_copy(src_file, dst_file, dry_run, overwrite=True, is_file=True)
                        if ok:
                            success_cfg += 1
                            file_index += 1
                            copied_bytes += src_file.stat().st_size
                            progress(file_index, f"config/{entry}/{rel}", copied_bytes)
                            if not dry_run:
                                log(f"✅ 复制 config 文件: {entry}/{rel}", "SUCCESS")
                        else:
                            failed_cfg.append((f"{entry}/{rel}", msg))
                            log(f"❌ 复制 config 文件 {entry}/{rel} 失败: {msg}", "ERROR")
                else:
                    log(f"⚠️ config 条目 {entry} 非文件非目录，跳过", "WARNING")

            log(f"config 复制完成: 成功 {success_cfg} 个, 失败 {len(failed_cfg)} 个", "INFO")

        # -------- 记录历史 --------
        if not dry_run and add_history:
            add_history_entry(tgt_path, src_path, modlist, configlist)
            log(f"📝 已记录迁移历史到 {get_history_path(tgt_path)}", "INFO")

        # -------- 完成 --------
        log("\n========== 迁移完成 ==========", "INFO")
        if dry_run:
            log("这是模拟运行，未实际修改任何文件。如需实际执行，请取消勾选【模拟运行】。", "INFO")
        else:
            log("实际复制完成，请检查日志中的错误信息。", "INFO")

        if progress_callback:
            progress_callback(None, None, None)  # 发送结束信号

        return True

    except Exception as e:
        log(f"❌ 迁移过程中发生未预期错误: {e}", "ERROR")
        log(traceback.format_exc(), "ERROR")
        if progress_callback:
            progress_callback(None, None, None)
        return False
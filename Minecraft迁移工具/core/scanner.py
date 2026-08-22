# core/scanner.py
import zipfile
import json
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def normalize_mod_name(name):
    """去除文件名中的 [前缀] 部分"""
    if name.startswith("[") and "]" in name:
        return name.split("]", 1)[1].strip()
    return name


def get_mod_metadata(jar_path):
    """
    从 jar 中读取 modid、version、mod_type
    返回: (modid, version, mod_type)
    """
    def is_placeholder(v):
        if not v:
            return True
        return any(x in v for x in ('${', '$', '{', '}'))

    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            has_fabric = 'fabric.mod.json' in zf.namelist()
            if has_fabric:
                try:
                    with zf.open('fabric.mod.json') as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        try:
                            data = json.loads(content)
                            modid = data.get('id')
                            version = data.get('version')
                            if modid and not is_placeholder(version):
                                return modid, version or "?", "Fabric"
                            elif modid:
                                filename = jar_path.name
                                ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                                if ver_match:
                                    return modid, ver_match.group(1), "Fabric(文件名推断)"
                                return modid, "?", "Fabric(占位符)"
                        except json.JSONDecodeError:
                            modid_match = re.search(r'"id"\s*:\s*"([^"]+)"', content)
                            version_match = re.search(r'"version"\s*:\s*"([^"]+)"', content)
                            if modid_match:
                                modid = modid_match.group(1)
                                version = version_match.group(1) if version_match else None
                                if is_placeholder(version):
                                    filename = jar_path.name
                                    ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                                    if ver_match:
                                        return modid, ver_match.group(1), "Fabric(正则解析)"
                                    return modid, "?", "Fabric(正则解析)"
                                return modid, version or "?", "Fabric(正则解析)"
                except Exception:
                    pass

                filename = jar_path.name
                ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                if ver_match:
                    return "未知", ver_match.group(1), "Fabric(仅文件名)"
                return "未知", "?", "Fabric(未知)"

            try:
                with zf.open('META-INF/mods.toml') as f:
                    content = f.read().decode('utf-8', errors='ignore')
                    modid_match = re.search(r'modId\s*=\s*"([^"]+)"', content)
                    version_match = re.search(r'version\s*=\s*"([^"]+)"', content)
                    if not modid_match:
                        modid_match = re.search(r"modId\s*=\s*'([^']+)'", content)
                    if not version_match:
                        version_match = re.search(r"version\s*=\s*'([^']+)'", content)
                    if modid_match:
                        modid = modid_match.group(1)
                        version = version_match.group(1) if version_match else None
                        if is_placeholder(version):
                            filename = jar_path.name
                            ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
                            if ver_match:
                                return modid, ver_match.group(1), "Forge(文件名推断)"
                            return modid, "?", "Forge(占位符)"
                        return modid, version or "?", "Forge"
            except (KeyError, zipfile.BadZipFile):
                pass

            filename = jar_path.name
            version_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)', filename)
            if version_match:
                return "未知", version_match.group(1), "文件名推断"
            return None, None, None
    except Exception:
        return None, None, None


def get_full_mod_metadata(jar_path):
    """
    从 jar 中读取完整的模组元数据（用于详情展示）
    返回字典: {modid, version, mod_type, name, description, authors, dependencies}
    """
    info = {
        "modid": "未知",
        "version": "未知",
        "mod_type": "未知",
        "name": "未知",
        "description": "无",
        "authors": "无",
        "dependencies": "无"
    }
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            if 'fabric.mod.json' in zf.namelist():
                with zf.open('fabric.mod.json') as f:
                    content = f.read().decode('utf-8', errors='ignore')
                    try:
                        data = json.loads(content)
                        info["modid"] = data.get("id", "未知")
                        info["version"] = data.get("version", "未知")
                        info["name"] = data.get("name", "未知")
                        info["description"] = data.get("description", "无")
                        info["authors"] = ", ".join(data.get("authors", [])) if data.get("authors") else "无"
                        info["dependencies"] = ", ".join(data.get("depends", {}).keys()) if data.get("depends") else "无"
                        info["mod_type"] = "Fabric"
                    except:
                        pass
            elif 'META-INF/mods.toml' in zf.namelist():
                with zf.open('META-INF/mods.toml') as f:
                    content = f.read().decode('utf-8', errors='ignore')
                    modid = re.search(r'modId\s*=\s*"([^"]+)"', content)
                    version = re.search(r'version\s*=\s*"([^"]+)"', content)
                    name = re.search(r'displayName\s*=\s*"([^"]+)"', content)
                    desc = re.search(r'description\s*=\s*"([^"]+)"', content)
                    author = re.search(r'author\s*=\s*"([^"]+)"', content)
                    info["modid"] = modid.group(1) if modid else "未知"
                    info["version"] = version.group(1) if version else "未知"
                    info["name"] = name.group(1) if name else "未知"
                    info["description"] = desc.group(1) if desc else "无"
                    info["authors"] = author.group(1) if author else "无"
                    info["mod_type"] = "Forge"
    except Exception as e:
        info["description"] = f"读取错误: {e}"
    return info


def scan_mod_differences(src_path, tgt_path, progress_queue=None, total=0):
    """
    扫描两个 mods 目录的差异，返回差异列表
    每个元素: (display_name, status, real_name, size_kb, note, modid, version, mod_type, file_path)
    status: "新增" / "更新" / "目标独有"
    """
    src_path = Path(src_path)
    tgt_path = Path(tgt_path)

    if src_path == tgt_path:
        return []
    if not src_path.exists() or not tgt_path.exists():
        return None

    src_mods = src_path / "mods"
    tgt_mods = tgt_path / "mods"

    if not src_mods.exists() or not tgt_mods.exists():
        return None

    def load_cache(cache_path):
        cache = {}
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
            except:
                pass
        return cache

    def save_cache(cache_path, cache):
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2)
        except:
            pass

    src_cache_file = src_path / "mods_meta_cache.json"
    tgt_cache_file = tgt_path / "mods_meta_cache.json"
    src_cache = load_cache(src_cache_file)
    tgt_cache = load_cache(tgt_cache_file)

    src_files = {}
    tgt_files = {}

    src_paths = list(src_mods.glob("*.jar"))
    tgt_paths = list(tgt_mods.glob("*.jar"))
    total_files = len(src_paths) + len(tgt_paths)
    if total == 0:
        total = total_files

    def parse_jar(file_path, is_source):
        modid, version, mod_type = get_mod_metadata(file_path)
        stat = file_path.stat()
        return {
            "name": file_path.name,
            "path": file_path,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "norm": normalize_mod_name(file_path.name),
            "modid": modid,
            "version": version,
            "mod_type": mod_type
        }

    current = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_path = {executor.submit(parse_jar, p, True): p for p in src_paths}
        for future in as_completed(future_to_path):
            with lock:
                current += 1
                result = future.result()
                src_files[result["name"]] = result
                key = result["name"]
                fingerprint = f"{result['mtime']}_{result['size']}"
                src_cache[key] = {
                    "fingerprint": fingerprint,
                    "modid": result["modid"],
                    "version": result["version"],
                    "mod_type": result["mod_type"]
                }
                if progress_queue:
                    progress_queue.put((current, result["name"]))
    save_cache(src_cache_file, src_cache)

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_path = {executor.submit(parse_jar, p, False): p for p in tgt_paths}
        for future in as_completed(future_to_path):
            with lock:
                current += 1
                result = future.result()
                tgt_files[result["name"]] = result
                key = result["name"]
                fingerprint = f"{result['mtime']}_{result['size']}"
                tgt_cache[key] = {
                    "fingerprint": fingerprint,
                    "modid": result["modid"],
                    "version": result["version"],
                    "mod_type": result["mod_type"]
                }
                if progress_queue:
                    progress_queue.put((current, result["name"]))
    save_cache(tgt_cache_file, tgt_cache)

    src_by_modid = {info["modid"]: name for name, info in src_files.items() if info["modid"]}
    tgt_by_modid = {info["modid"]: name for name, info in tgt_files.items() if info["modid"]}
    src_by_norm = {info["norm"]: name for name, info in src_files.items()}
    tgt_by_norm = {info["norm"]: name for name, info in tgt_files.items()}

    results = []
    processed_tgt_names = set()

    for src_name, src_info in src_files.items():
        matched = False
        tgt_name = None
        if src_name in tgt_files:
            tgt_name = src_name
            matched = True
        elif src_info["modid"] and src_info["modid"] in tgt_by_modid:
            potential_tgt_name = tgt_by_modid[src_info["modid"]]
            potential_tgt_info = tgt_files[potential_tgt_name]
            if potential_tgt_info.get("mod_type") == src_info.get("mod_type"):
                tgt_name = potential_tgt_name
                matched = True
        if not matched and src_info["norm"] in tgt_by_norm:
            tgt_name = tgt_by_norm[src_info["norm"]]
            matched = True

        if matched:
            tgt_info = tgt_files[tgt_name]
            processed_tgt_names.add(tgt_name)
            update_reason = []
            if src_info["modid"] and tgt_info["modid"] and src_info["modid"] != tgt_info["modid"]:
                update_reason.append("modId不同")
            if (src_info["version"] and src_info["version"] != "?" and
                    tgt_info["version"] and tgt_info["version"] != "?" and
                    src_info["version"] != tgt_info["version"]):
                update_reason.append(f"版本 {tgt_info['version']} → {src_info['version']}")
            if src_info["size"] != tgt_info["size"]:
                update_reason.append("大小变化")
            if src_info["mtime"] > tgt_info["mtime"]:
                update_reason.append("源更新")

            if update_reason:
                results.append((
                    src_name,
                    "更新",
                    src_name,
                    round(src_info["size"] / 1024, 1),
                    ", ".join(update_reason),
                    src_info["modid"] or "?",
                    src_info["version"] or "?",
                    src_info["mod_type"] or "未知",
                    str(src_info["path"])
                ))
        else:
            results.append((
                src_name,
                "新增",
                src_name,
                round(src_info["size"] / 1024, 1),
                "仅存在于源目录",
                src_info["modid"] or "?",
                src_info["version"] or "?",
                src_info["mod_type"] or "未知",
                str(src_info["path"])
            ))

    for tgt_name, tgt_info in tgt_files.items():
        if tgt_name not in processed_tgt_names:
            if tgt_info["modid"] and tgt_info["modid"] in src_by_modid:
                continue
            if tgt_info["norm"] in src_by_norm:
                continue
            results.append((
                tgt_name,
                "目标独有",
                tgt_name,
                round(tgt_info["size"] / 1024, 1),
                "仅存在于目标（建议保留）",
                tgt_info["modid"] or "?",
                tgt_info["version"] or "?",
                tgt_info["mod_type"] or "未知",
                str(tgt_info["path"])
            ))

    if progress_queue:
        progress_queue.put(None)
    return results
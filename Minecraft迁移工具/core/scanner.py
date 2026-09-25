# core/scanner.py
import io
import zipfile
import json
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 模组图标缓存目录（和日志文件一样放用户目录，不动用户实例里的东西）
ICON_CACHE_DIR = Path.home() / ".minecraft_migrate_icons"


def normalize_mod_name(name):
    """去除文件名中的 [前缀] 部分"""
    if name.startswith("[") and "]" in name:
        return name.split("]", 1)[1].strip()
    return name


def split_cn_name(filename):
    """把 "[中文名] 英文名-版本.jar" 拆成 (中文名, 英文名)。

    中文名不是从 jar 里读的——整合包习惯把中文名写在方括号里，直接拿它当
    "中文名"用，比联网翻译省事也准。
    注意：方括号里必须**含汉字**才算中文名，否则像 "[1.20.1] SecurityCraft"
    这种版本方括号会被误当成名字。
    """
    stem = Path(filename).stem
    if stem.startswith("[") and "]" in stem:
        inside = stem[1:stem.index("]")].strip()
        rest = stem[stem.index("]") + 1:].strip()
        if inside and any("\u4e00" <= ch <= "\u9fff" for ch in inside):
            return inside, (rest or stem)
    return "", stem


# 分类标签的"推测"规则：jar 里没有分类信息（那是 PCL2 自己的在线资料库），
# 这里只能按描述/名字里的关键词猜，界面上要标明是推测。
_TAG_RULES = (
    ("优化", ("optimiz", "performance", "fps", "lag", "faster", "smooth", "优化", "性能")),
    ("画面", ("shader", "graphic", "visual", "render", "光影", "画质", "texture")),
    ("信息显示", ("hud", "tooltip", "overlay", "minimap", "jei", "rei ", "显示", "信息")),
    ("科技", ("machine", "energy", "factory", "tech", "engineering", "kinetic",
              "contraption", "机械", "能源", "科技")),
    ("魔法", ("magic", "spell", "arcane", "ritual", "魔法")),
    ("冒险", ("dungeon", "adventure", "structure", "dimension", "biome", "冒险", "地牢", "维度")),
    ("装备", ("weapon", "armor", "sword", "combat", "装备", "武器", "战斗")),
    ("存储", ("storage", "backpack", "container", "drawer", "存储", "背包", "容器")),
    ("建筑", ("building", "decoration", "furniture", "建筑", "装饰")),
    ("生物", ("mob ", "creature", "animal", "boss", "entity", "生物", "怪物")),
    ("农业", ("farm", "crop", "cooking", "agriculture", "农业", "作物", "食物")),
    ("任务", ("quest", "mission", "任务")),
    ("音效", ("sound", "music", "audio", "音效", "音乐")),
    ("前置库", ("library", "api", "framework", "前置", "库")),
    ("多人", ("multiplayer", "network", "联机", "服务器")),
)


def guess_tags(desc="", name="", modid=""):
    """按关键词推测分类标签（离线、零维护，界面上要写明"推测"）。"""
    text = f"{desc} {name} {modid}".lower()
    tags = [tag for tag, keys in _TAG_RULES if any(k in text for k in keys)]
    return tags[:3]


def get_mod_icon(jar_path, cache_dir=None):
    """取模组图标（返回缓存 PNG 的路径；没有图标返回 None）。

    图标来源：Fabric 的 `icon` 字段 / Forge 的 `logoFile` → 包根 icon.png →
    根目录唯一的小图。结果按 路径+mtime+size 缓存：有图标存 PNG、没有就写个
    .none 标记，所以只有第一次会解压，之后都读缓存。
    """
    jar_path = Path(jar_path)
    # cache_dir 允许传字符串（测试/临时目录都这么用），这里统一转 Path，
    # 不然 "str" / "文件名" 会在下面直接 TypeError。
    cache = Path(cache_dir) if cache_dir else Path(ICON_CACHE_DIR)
    try:
        st = jar_path.stat()
        key = f"{abs(hash((str(jar_path), int(st.st_mtime), st.st_size))):x}"
    except Exception:
        return None
    png = cache / f"{key}.png"
    none_mark = cache / f"{key}.none"
    if png.exists():
        return png
    if none_mark.exists():
        return None
    entry = None
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = zf.namelist()
            lower = {n.lower(): n for n in names}
            if "fabric.mod.json" in names:
                try:
                    data = json.loads(zf.read("fabric.mod.json").decode("utf-8", "ignore"))
                    icon = data.get("icon")
                    if isinstance(icon, dict):
                        for size in sorted(icon, key=lambda x: -int(x) if str(x).isdigit() else 0):
                            if str(icon[size]).lower() in lower:
                                entry = lower[str(icon[size]).lower()]
                                break
                    elif isinstance(icon, str) and icon.lower() in lower:
                        entry = lower[icon.lower()]
                except Exception:
                    pass
            if entry is None and "meta-inf/mods.toml" in lower:
                try:
                    text = zf.read(lower["meta-inf/mods.toml"]).decode("utf-8", "ignore")
                    m = re.search(r'logoFile\s*=\s*"([^"]+)"', text)
                    if m and m.group(1).lower() in lower:
                        entry = lower[m.group(1).lower()]
                except Exception:
                    pass
            if entry is None and "icon.png" in lower:
                entry = lower["icon.png"]
            if entry is None:
                roots = [n for n in names
                         if "/" not in n and n.lower().endswith((".png", ".jpg", ".jpeg"))]
                if len(roots) == 1:
                    entry = roots[0]
            raw = zf.read(entry) if entry else None
    except Exception:
        raw = None
    try:
        cache.mkdir(parents=True, exist_ok=True)
        if not raw:
            none_mark.write_bytes(b"")
            return None
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        im.thumbnail((96, 96), Image.LANCZOS)
        im.save(png, "PNG")
        return png
    except Exception:
        try:
            none_mark.write_bytes(b"")
        except Exception:
            pass
        return None


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
                                ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)',
                                                      filename)
                                if ver_match:
                                    return modid, ver_match.group(1), "Fabric(文件名推断)"
                                return modid, "?", "Fabric(占位符)"
                        except json.JSONDecodeError:
                            modid_match = re.search(r'"id"\s*:\s*"([^"]+)"', content)
                            version_match = re.search(r'"version"\s*:\s*"([^"]+)"',
                                                      content)
                            if modid_match:
                                modid = modid_match.group(1)
                                version = version_match.group(1) if version_match else None
                                if is_placeholder(version):
                                    filename = jar_path.name
                                    ver_match = re.search(r'[-_]v?(\d+\.\d+(\.\d+)?)',
                                                          filename)
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


def _clean_meta(value, default="未知"):
    """清理占位符/空值（如 ${file.jarVersion}），返回默认值。"""
    if value is None:
        return default
    s = str(value).strip()
    if not s or s == default:
        return default
    # Maven/Gradle 占位符：${file.jarVersion} 或 $xxx
    if s.startswith("$"):
        return default
    return s


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
                        info["modid"] = _clean_meta(data.get("id", "未知"))
                        info["version"] = _clean_meta(data.get("version", "未知"))
                        info["name"] = _clean_meta(data.get("name", "未知"))
                        info["description"] = data.get("description", "无")
                        info["authors"] = ", ".join(data.get("authors",
                                                             [])) if data.get("authors") else "无"
                        info["dependencies"] = ", ".join(data.get("depends",
                                                                  {}).keys()) if data.get("depends") else "无"
                        info["mod_type"] = "Fabric"
                        # 只有 Fabric 有可靠的 client/server 声明；Forge 那边判不准，留空
                        env = str(data.get("environment", "") or "").lower()
                        info["env"] = {"client": "客户端", "server": "服务端"}.get(env, "")
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
                    info["modid"] = _clean_meta(modid.group(1) if modid else "未知")
                    info["version"] = _clean_meta(version.group(1) if version else "未知")
                    info["name"] = _clean_meta(name.group(1) if name else "未知")
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

    src_by_modid = {info["modid"]: name for name,
                    info in src_files.items() if info["modid"]}
    tgt_by_modid = {info["modid"]: name for name,
                    info in tgt_files.items() if info["modid"]}
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
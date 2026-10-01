# core/scanner.py
import io
import itertools
import zipfile
import json
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 界面语言：备注/来源这类拼出来才显示的文案走 trp 模板
from utils.i18n import trp

# 模组图标缓存目录（和日志文件一样放用户目录，不动用户实例里的东西）
ICON_CACHE_DIR = Path.home() / ".minecraft_migrate_icons"


def normalize_mod_name(name):
    """去除文件名中的 [前缀] 部分"""
    if name.startswith("[") and "]" in name:
        return name.split("]", 1)[1].strip()
    return name


def _version_parts(v):
    """把版本号切成可比较的段：数字段是 (0, 数值)，字母段是 (1, 小写串)。

    支持 `1.20.1`、`0.14.21`、`2.0.0-beta.1`、`1.19.2-0.14.21`、`v1.2` 这类写法。
    解析不出来（空串 / "?" / 全是符号）返回 None。
    """
    s = str(v or "").strip().lstrip("vV")
    if not s or s == "?":
        return None
    out = []
    for p in re.split(r"[.\-_+ ]+", s):
        if not p:
            continue
        if p.isdigit():
            out.append((0, int(p)))
            continue
        m = re.match(r"^(\d+)([a-zA-Z].*)$", p)      # 1a / 2b3 这种黏在一起的
        if m:
            out.append((0, int(m.group(1))))
            out.append((1, m.group(2).lower()))
        else:
            out.append((1, p.lower()))
    # 一个数字段都没有（"abc"、"beta" 这种）就当没法比，别硬给个大小结论
    return out if any(p[0] == 0 for p in out) else None


def compare_versions(a, b):
    """比较两个版本号：a > b 返回 1，a < b 返回 -1，相等 0，**没法比返回 None**。

    规则：逐段比，数字段按数值；数字段排在字母段前面（所以 1.0 > 1.0-beta）；
    某一边少一段时，缺的当"正式版"看 —— 没有后缀比带 beta/rc 的大，补 0 则算相等
    （`1.0` == `1.0.0`）。
    """
    pa, pb = _version_parts(a), _version_parts(b)
    if pa is None or pb is None:
        return None
    for i in range(max(len(pa), len(pb))):
        if i >= len(pa):                    # a 没有这一段了
            y = pb[i]
            if y[0] == 1:
                return 1                    # b 是预发布后缀 → a 更大
            if y[1] != 0:
                return -1
            continue
        if i >= len(pb):
            x = pa[i]
            if x[0] == 1:
                return -1
            if x[1] != 0:
                return 1
            continue
        x, y = pa[i], pb[i]
        if x == y:
            continue
        if x[0] != y[0]:                    # 一个数字段一个字母段：数字段在前
            return -1 if x[0] < y[0] else 1
        return -1 if x[1] < y[1] else 1
    return 0


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


# 并发解析 jar 元数据的线程数。取多少是量出来的（`_dctest/基准_扫描线程.py`，
# 450 个真 jar / 108MB）：
#     线程数        1     2     3     4     6     8    12
#     扫描耗时   0.40  0.42  0.42  0.41  0.43  0.43  0.43 秒   ← 基本不变
#     界面最坏延迟 13   13    13    14    15    17    17  ms  ← 线程越多界面越挤
# 解析是"解 zip + 解 JSON"的 Python 活，**卡在 GIL 上**，多开线程换不来速度；
# 却会让界面线程在 GIL 上排队。3 和 Qt 那侧调好的并发数一致（见 ui/qt_big_view.py）。
SCAN_WORKERS = 3


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

    def 收一条(result, 是源):
        """一条解析完了：写进对应容器 + 缓存 + 报进度（都在锁里）。

        注意：这里**只碰普通对象和队列**，不碰 Tk —— 这个方法在 worker 线程上跑。
        """
        nonlocal current
        current += 1
        if 是源:
            src_files[result["name"]] = result
            src_cache[result["name"]] = {
                "fingerprint": f"{result['mtime']}_{result['size']}",
                "modid": result["modid"],
                "version": result["version"],
                "mod_type": result["mod_type"],
            }
        else:
            tgt_files[result["name"]] = result
            tgt_cache[result["name"]] = {
                "fingerprint": f"{result['mtime']}_{result['size']}",
                "modid": result["modid"],
                "version": result["version"],
                "mod_type": result["mod_type"],
            }
        if progress_queue:
            progress_queue.put((current, result["name"]))

    # 源和目标分两批跑（实测合成一个池没有收益，见 SCAN_WORKERS 上面的对照表）
    for 是源, 路径们, 缓存文件 in ((True, src_paths, src_cache_file),
                                  (False, tgt_paths, tgt_cache_file)):
        with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
            future_to_path = {executor.submit(parse_jar, p, 是源): p for p in 路径们}
            for future in as_completed(future_to_path):
                with lock:
                    收一条(future.result(), 是源)
        save_cache(缓存文件, src_cache if 是源 else tgt_cache)

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
                update_reason.append(trp("版本 {0} → {1}", tgt_info['version'],
                                             src_info['version']))
            if src_info["size"] != tgt_info["size"]:
                update_reason.append("大小变化")
            if src_info["mtime"] > tgt_info["mtime"]:
                update_reason.append("源更新")

            if update_reason:
                # 光"版本不同"还不够：**目标版本比源高**时复制过去是降级，
                # 不能叫"更新"（用户反馈）。比不出来（版本号缺失/格式古怪）就按原样算更新。
                状态 = "更新"
                备注 = ", ".join(update_reason)
                比 = compare_versions(src_info.get("version"), tgt_info.get("version"))
                if 比 is not None and 比 < 0:
                    状态 = "降级"
                    备注 = trp("目标版本更高：{0} → {1}（复制过去会降级，建议保留目标的）",
                              tgt_info["version"], src_info["version"])
                results.append((
                    src_name,
                    状态,
                    src_name,
                    round(src_info["size"] / 1024, 1),
                    备注,
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


# --------------------------------------------------------------------------- #
# 实例环境探测（MC 版本 + 加载器）
# --------------------------------------------------------------------------- #
# 用途：联网搜索按"你这个实例能用的版本/加载器"过滤候选池。
# 关键是**宁可不猜也不要猜错** —— 猜错会把本该搜得到的模组过滤掉，
# 那比多显示几条不兼容的结果糟糕得多。所以每一层拿不准就返回空串、放弃过滤。
_MOD_LOADERS = ("neoforge", "fabric", "quilt", "forge")     # 判定顺序：先长后短
# MC 版本号的形状。**必须收紧**：整合包自己的版本号也长成 5.11.9 / 2.0.0 这样，
# 松一点点（"数字.数字"就算）就会把整合包版本当成 MC 版本，再拿去过滤 → 一条都搜不到。
#   1.20.1 / 1.21   经典 1.x
#   26.3            年份式新版本号（两位数年份起）
#   24w14a          快照
_MC_VERSION_RE = re.compile(
    r"^(?:1\.\d{1,2}(?:\.\d{1,2})?|[2-9]\d\.\d+(?:\.\d+)?|\d{2}w\d{2}[a-z]?)$")
# 模组**文件名**里只认经典 1.x 和快照：文件名里 23.9.7 这种更像是模组自己的版本号
# （Xaero's Minimap 就是这命名），年份式的留给 version.json / 目录名那两层去认。
_MC_FILENAME_RE = re.compile(r"^(?:1\.\d{1,2}(?:\.\d{1,2})?|\d{2}w\d{2}[a-z]?)$")

_ENV_CACHE = {}
_ENV_CACHE_LOCK = threading.Lock()
_JAR_SAMPLE = 8          # 抽查几个 jar 判加载器就够，别为几百个模组挨个开压缩包
_NAME_SAMPLE = 40        # 文件名只看名字、不用解压，多抽几个让"投票"更准


def _loader_of(text):
    """从一段文字里认加载器（文件名 / 库名 / 目录名都适用）。

    必须先查 neoforge 再查 forge："neoforge" 里含 "forge"，
    顺序反了会把 NeoForge 认成 Forge。
    """
    s = str(text or "").lower()
    for name in _MOD_LOADERS:
        if name in s:
            return name
    return ""


def _mc_of(text):
    """从一段文字里挑出像 MC 版本号的那一段（1.20.1 / 1.21 / 26.3 / 24w14a）。"""
    for piece in re.split(r"[^0-9A-Za-z.]+", str(text or "")):
        if _MC_VERSION_RE.match(piece):
            return piece
    return ""


def _mc_exact(value):
    """只认"整个字段就是这个版本"。`>=1.20.1`、`~1.20.1`、`*` 这类范围一律不认。"""
    s = str(value or "").strip()
    return s if _MC_VERSION_RE.match(s) else ""


def _env_from_version_json(root):
    """第 1 层：versions/*/version.json（最权威，里面直接写着版本和库）。"""
    候选 = []
    vdir = root / "versions"
    if vdir.is_dir():
        try:
            for d in sorted(vdir.iterdir()):
                j = d / "version.json"
                if d.is_dir() and j.is_file():
                    候选.append((d.name, j))
        except Exception:
            pass
    # 有的整合包根目录本身就是个版本目录（<根目录名>.json 跟它同名）
    自己 = root / (root.name + ".json")
    if 自己.is_file():
        候选.append((root.name, 自己))

    for 名, j in 候选:
        try:
            data = json.loads(j.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        mc = _mc_of(data.get("inheritsFrom")) or _mc_of(data.get("id")) or _mc_of(名)
        loader = ""
        for lib in (data.get("libraries") or []):
            lib_name = lib.get("name") if isinstance(lib, dict) else lib
            loader = _loader_of(lib_name)
            if loader:
                break
        if not loader:
            loader = _loader_of(名)
        if mc or loader:
            return mc, loader, "version.json（%s）" % 名
    return "", "", ""


def _env_from_jars(root):
    """第 3 层：抽查 mods 里的 jar，看它带的是哪家的元数据。

    只能判加载器；MC 版本顺带看 fabric.mod.json 的 depends.minecraft，
    但那通常是 ">=1.20.1" 这种范围，认不出就不认。
    """
    mods = root / "mods"
    if not mods.is_dir():
        return "", "", ""
    try:
        # 不排序：模组多的实例排几千个路径纯属浪费，抽查哪几个都行
        jars = list(itertools.islice(mods.glob("*.jar"), _JAR_SAMPLE))
    except Exception:
        return "", "", ""
    if not jars:
        return "", "", ""

    票 = {}
    mc = ""
    for jar in jars:
        try:
            with zipfile.ZipFile(jar, "r") as zf:      # 只开一次：先看名单，再读需要的那份
                名单 = set(zf.namelist())
                if "fabric.mod.json" in 名单:
                    票["fabric"] = 票.get("fabric", 0) + 1
                    if not mc:
                        try:
                            meta = json.loads(zf.read("fabric.mod.json")
                                              .decode("utf-8", errors="ignore"))
                            # 只认写死的版本；">=1.20.1" 这种范围不能当成实例的版本
                            mc = _mc_exact((meta.get("depends") or {}).get("minecraft"))
                        except Exception:
                            pass
                    continue
                if "META-INF/neoforge.mods.toml" in 名单:
                    票["neoforge"] = 票.get("neoforge", 0) + 1
                elif "META-INF/mods.toml" in 名单:
                    # Forge 和 NeoForge 都有 mods.toml，只能看里面提没提 neoforge
                    try:
                        文本 = zf.read("META-INF/mods.toml").decode("utf-8", errors="ignore")
                        键 = "neoforge" if "neoforge" in 文本.lower() else "forge"
                    except Exception:
                        键 = "forge"
                    票[键] = 票.get(键, 0) + 1
        except Exception:
            continue
    if not 票:
        return "", "", ""
    loader = max(票.items(), key=lambda kv: kv[1])[0]
    return mc, loader, trp("mods 里的模组元数据（抽查 {0} 个）", len(jars))


def _env_from_filenames(root):
    """第 3 层：从模组**文件名**里认 MC 版本（不用解压，最便宜的一层）。

    很多整合包的 jar 名里就写着 MC 版本：`sodium-fabric-0.5.8+mc1.20.1.jar`、
    `AppleSkin-mc1.20.1-forge-2.5.1.jar`、`Xaeros_Minimap_23.9.7_Fabric_1.20.jar`。
    取"出现次数最多的那个"：模组自己的版本号各不相同，MC 版本会反复出现，投票能把它顶上来。
    只有一个候选、它又只出现一次、旁边还有别的候选 —— 那就不猜。
    """
    mods = root / "mods"
    if not mods.is_dir():
        return "", ""
    try:
        names = [p.name for p in itertools.islice(mods.glob("*.jar"), _NAME_SAMPLE)]
    except Exception:
        return "", ""
    票 = {}
    for 名 in names:
        干净 = 名[:-4] if 名.lower().endswith(".jar") else 名
        干净 = re.sub(r"[^0-9A-Za-z.]+", " ", 干净)      # 分隔符统一成空格
        干净 = re.sub(r"\bmc(?=\d)", " ", 干净, flags=re.I)   # "mc1.20.1" → "1.20.1"
        for tok in 干净.split():
            if _MC_FILENAME_RE.match(tok):
                票[tok] = 票.get(tok, 0) + 1
    if not 票:
        return "", ""
    最多 = max(票.values())
    if 最多 < 2 and len(票) > 1:
        return "", ""
    for tok, 次 in 票.items():
        if 次 == 最多:
            return tok, trp("模组文件名（{0} 个文件里出现 {1} 次）", len(names), 次)
    return "", ""


def detect_instance_env(root):
    """尽力识别一个整合包实例的 MC 版本与加载器。

    返回 {"mc": "1.20.1", "loader": "fabric", "src": "…说明从哪认出来的"}；
    认不出来的字段是空串（调用方就不要拿它过滤）。

    依次尝试（可靠度从高到低）：
      1. versions/*/version.json —— 里面直接写着 inheritsFrom 和 libraries
      2. 实例目录名 —— "1.20.1-fabric" 这种，几乎都带版本号
      3. mods 里的**文件名** —— `sodium-fabric-0.5.8+mc1.20.1.jar` 这种（投票）
      4. mods 里抽查几个 jar 的元数据 —— 只能判加载器（顺带碰运气认版本）

    **整合包自己的版本号（5.11.9 这种）不能被当成 MC 版本** —— 目录名里很常见，
    认错了拿去过滤就是"一条都搜不到"。所以版本号的形状卡得很死（见 _MC_VERSION_RE）。
    """
    if not root:
        return {"mc": "", "loader": "", "src": ""}
    try:
        键 = str(Path(root).resolve())
    except Exception:
        键 = str(root)
    with _ENV_CACHE_LOCK:
        hit = _ENV_CACHE.get(键)
    if hit is not None:
        return dict(hit)

    p = Path(root)
    mc, loader = "", ""
    mc_src, loader_src = "", ""      # 版本和加载器可能来自不同的层，分别记来源
    if p.is_dir():
        版_mc, 版_loader, 版_src = _env_from_version_json(p)
        if 版_mc:
            mc, mc_src = 版_mc, 版_src
        if 版_loader:
            loader, loader_src = 版_loader, 版_src
        名_mc, 名_loader = _mc_of(p.name), _loader_of(p.name)
        if not mc and 名_mc:
            mc, mc_src = 名_mc, "实例目录名"
        if not loader and 名_loader:
            loader, loader_src = 名_loader, "实例目录名"
        if not mc:
            文件_mc, 文件_src = _env_from_filenames(p)
            if 文件_mc:
                mc, mc_src = 文件_mc, 文件_src
        if not loader:
            罐_mc, 罐_loader, 罐_src = _env_from_jars(p)
            if not mc and 罐_mc:
                mc, mc_src = 罐_mc, 罐_src
            if not loader and 罐_loader:
                loader, loader_src = 罐_loader, 罐_src

    来源 = []
    if mc:
        来源.append(trp("版本来自{0}", trp(mc_src) if mc_src else trp("未知来源")))
    if loader:
        来源.append(trp("加载器来自{0}", trp(loader_src) if loader_src else trp("未知来源")))
    结果 = {"mc": mc, "loader": loader, "src": "；".join(来源)}
    with _ENV_CACHE_LOCK:
        _ENV_CACHE[键] = dict(结果)
    return 结果

# core/mod_search.py
"""联网搜索模组信息（参考 PCL2 的 扫描本地->提取身份->联网匹配 思路）。

两个来源：

  · **Modrinth**（默认）：公开 API，免费、无需密钥，一直在用；
  · **CurseForge**（可选）：需要用户自己申请并填入 API Key，见文件末尾那段。
    key 的存放与脱敏全部交给 utils/secrets.py —— 程序不携带 key，也不让它进
    日志 / 报错 / 子进程参数 / 仓库。
"""
import json
import os
import re
import threading
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path

# 界面语言：下载量单位（万/亿 ↔ k/M）走 trp 模板
from utils import i18n
from utils.i18n import trp

USER_AGENT = "MinecraftMigrateTool/1.0 (contact: local)"

MODRINTH_SEARCH = "https://api.modrinth.com/v2/search?query={query}&limit={limit}&index=downloads"
# 取版本列表：**必须带 limit=1**。实测 JEI 这类版本极多的项目，不带参数会一次拉回
# 6.58MB / 53.9 秒；带 limit=1 只要 8KB / 0.82 秒（65 倍），而且返回的就是同一条最新版本
# （该接口默认按 date_published 倒序，versions[0] 就是最新）。
MODRINTH_VERSIONS = "https://api.modrinth.com/v2/project/{project_id}/version?limit=1"


def _json_get(url, timeout=15):
    """带 UA 的 GET，返回解析后的 JSON。失败时抛异常。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _search_url(query, limit, mc_version="", loader=""):
    """拼搜索 URL；给了 MC 版本 / 加载器就加 facets（两组之间是 AND）。

    实测 `sodium` 不加过滤有 354 个候选，限成 1.20.1 + fabric 只剩 38 个 ——
    候选池干净了，前 8 条才可能是"你装得上"的那些。
    """
    url = MODRINTH_SEARCH.format(query=urllib.parse.quote(str(query).strip()),
                                 limit=int(limit))
    facets = []
    if mc_version:
        facets.append(["versions:%s" % mc_version])
    if loader:
        facets.append(["categories:%s" % loader])
    if facets:
        url += "&facets=" + urllib.parse.quote(json.dumps(facets))
    return url


def _versions_url(project_id, mc_version="", loader=""):
    """拼"最新版本"URL。带上版本/加载器参数，否则会拿到别的版本/别的加载器的构建。"""
    url = MODRINTH_VERSIONS.format(project_id=project_id)
    params = {}
    if mc_version:
        params["game_versions"] = json.dumps([mc_version])
    if loader:
        params["loaders"] = json.dumps([loader])
    if params:
        url += "&" + urllib.parse.urlencode(params)
    return url


def _fetch_latest_version(project_id, timeout=12, mc_version="", loader=""):
    """取单个项目的最新版本号与下载链接。返回 (版本号, 下载链接, 状态)。

    状态：
      ok       拿到了
      no_match 项目存在，但没有"这个版本 + 这个加载器"的构建（过滤后为空）
      error    接口/网络失败
    以前只返回 ("", "")，界面上分不清"没有匹配版本"和"请求失败"，会一直显示"获取中…"。
    """
    try:
        versions = _json_get(_versions_url(project_id, mc_version, loader),
                             timeout=timeout)
    except Exception:
        return "", "", "error"
    if not versions:
        return "", "", "no_match"
    v0 = versions[0]
    files = v0.get("files") or []
    url = files[0].get("url", "") if files else ""
    return (v0.get("version_number", ""), url, "ok")


def search_modrinth(query, limit=8, mc_version="", loader=""):
    """快速搜索 Modrinth，按下载量排序，只返回搜索元数据（不含版本细节），速度快。

    mc_version / loader 可选：给了就只搜"这个版本 + 这个加载器"的项目。
    返回每个项目：title/slug/project_id/author/description/downloads/project_url，
    以及空的 latest_version/download_url（需用 fetch_project_latest 再取）。
    """
    if not query or not query.strip():
        return []

    try:
        data = _json_get(_search_url(query, limit, mc_version, loader))
    except Exception as e:
        raise RuntimeError(f"搜索请求失败: {e}")

    results = []
    for hit in data.get("hits", []):
        pid = hit.get("project_id")
        slug = hit.get("slug") or pid
        results.append({
            "title": hit.get("title", ""),
            "slug": slug,
            "project_id": pid,
            "author": hit.get("author", ""),
            "description": (hit.get("description") or "")[:120],
            "downloads": hit.get("downloads", 0),
            "project_url": "https://modrinth.com/mod/%s" % slug,
            # 卡片视图要拿它做标签 chip（列表视图不用，但顺手带上不额外花请求）
            "categories": list(hit.get("categories") or []),
            "latest_version": "",
            "download_url": "",
        })

    return results


def categories_cn(slugs):
    """Modrinth 的分类 slug → 中文标签（认不出来的丢掉，别在界面上摆英文）。"""
    out = []
    for s in (slugs or []):
        cn = _CATEGORY_CN.get(str(s).lower())
        if cn and cn not in out:
            out.append(cn)
    return out


def fetch_project_latest(project_id, timeout=12, mc_version="", loader=""):
    """取单个项目的最新版本号与下载链接（供点击/复制时按需获取）。

    给了 mc_version / loader 就只取适配这一组的最新构建 —— 否则会拿到
    "别的 MC 版本、别的加载器"的构建（实测 Sodium 会给出 mc26.3 的 neoforge alpha）。
    """
    vnum, url, status = _fetch_latest_version(project_id, timeout=timeout,
                                              mc_version=mc_version, loader=loader)
    return {"latest_version": vnum, "download_url": url, "status": status}


_MC_PREFIX_RE = re.compile(r"^mc[-\s]?\d+(?:\.\d+){0,2}[-_+ ]?", re.I)
_LOADER_TAIL_RE = re.compile(r"[-_+ ](?:fabric|forge|neoforge|quilt)\b.*$", re.I)


def normalize_online_version(v):
    """把 Modrinth 的版本号压成"能和本地版本比大小"的形式。

    Modrinth 的 version_number 常带 MC 版本和加载器，本地 jar 里只有裸版本号：
      "mc1.20.1-0.5.13-fabric" → "0.5.13"
      "1.7.6+1.20.1"           → "1.7.6"
      "0.5.8"                  → "0.5.8"
    不归一化的话，"本地 0.5.13 / 线上 mc1.20.1-0.5.13-fabric" 字符串永远不相等，
    每一行都会被标成"可更新"（等于这个标记没用了）。
    """
    s = str(v or "").strip()
    s = _MC_PREFIX_RE.sub("", s)          # 去掉开头的 mc1.20.1-
    s = s.split("+", 1)[0]                # 去掉 +1.20.1 这种 MC 标记
    s = _LOADER_TAIL_RE.sub("", s)        # 去掉结尾的 -fabric / -neoforge
    return s.strip() or str(v or "").strip()


def format_downloads(n):
    """把下载量格式化成 2.1亿 / 9000万 之类的可读形式。

    界面语言：中文用「亿 / 万」，英文用 M / k —— **阈值也不一样**（英文是 1e6 / 1e3，
    否则会出现"9000.0k"这种别扭写法）。中文模式下与原来一字不差。
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if i18n.language() == i18n.EN:
        if n >= 1000000:
            return trp("{0:.1f}M", n / 1000000)
        if n >= 1000:
            return trp("{0:.1f}k", n / 1000)
        return str(n)
    if n >= 100000000:
        return trp("{0:.1f}亿", n / 100000000)
    if n >= 10000:
        return trp("{0:.1f}万", n / 10000)
    return str(n)


# ------------------------------------------------------------------ 在线分类
# Modrinth 的分类是英文 slug，映射成界面里的中文标签；没映射到的直接忽略，
# 免得出现一屏看不懂的英文。键和 ui/card_list.TAG_COLORS 对齐，颜色就能复用。
_CATEGORY_CN = {
    "technology": "科技", "magic": "魔法", "adventure": "冒险", "decoration": "建筑",
    "optimization": "优化", "storage": "存储", "food": "农业", "mobs": "生物",
    "equipment": "装备", "library": "前置库", "utility": "辅助", "worldgen": "世界生成",
    "game-mechanics": "机制", "management": "管理", "minigame": "小游戏",
    "social": "社交", "transportation": "运输", "economy": "经济",
    "challenge": "挑战", "combat": "装备", "cursed": "整活", "shaders": "画面",
}

_TAG_CACHE = None
_TAG_CACHE_LOCK = threading.Lock()
TAG_CACHE_FILE = Path.home() / ".minecraft_migrate_tags.json"

# 熔断：断网时别让几百个模组各等一次超时（6 个扫描线程会被一起拖住）。
# 连续失败 3 次后就当"没网"，本次运行不再尝试；成功一次即清零。
_NET_FAILS = [0]
_NET_DEAD = [False]
_NET_FAIL_LIMIT = 3


def _tag_cache():
    """分类查询的磁盘缓存（一次查询永久生效，省得每次扫描都联网）。"""
    global _TAG_CACHE
    if _TAG_CACHE is None:
        try:
            with open(TAG_CACHE_FILE, "r", encoding="utf-8") as f:
                _TAG_CACHE = json.load(f)
            if not isinstance(_TAG_CACHE, dict):
                _TAG_CACHE = {}
        except Exception:
            _TAG_CACHE = {}
    return _TAG_CACHE


def _save_tag_cache():
    try:
        cache = _tag_cache()
        tmp = str(TAG_CACHE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, TAG_CACHE_FILE)
    except Exception:
        pass


def fetch_categories(modid="", name="", timeout=10):
    """联网取某个模组在 Modrinth 上的分类，返回中文标签列表；取不到返回 None。

    先用 modid 精确匹配（Modrinth 的 slug 通常就是 modid），没中就再用名字精确匹配。
    只认"slug 或标题完全一致"，绝不拿搜索结果的第一条凑数——那样会把不相干模组的
    分类贴到本地模组上。断网、超时、查不到都返回 None，调用方继续用"关键词推测"。
    """
    if _NET_DEAD[0]:
        return None
    for query in (modid, name):
        q = str(query or "").strip()
        if not q:
            continue
        try:
            data = _json_get(MODRINTH_SEARCH.format(
                query=urllib.parse.quote(q), limit=5), timeout=timeout)
            _NET_FAILS[0] = 0
        except Exception:
            _NET_FAILS[0] += 1
            if _NET_FAILS[0] >= _NET_FAIL_LIMIT:
                _NET_DEAD[0] = True
            return None
        hits = data.get("hits") or []
        if not hits:
            continue
        low = q.lower()
        best = None
        for h in hits:
            if (h.get("slug") or "").lower() == low or (h.get("title") or "").lower() == low:
                best = h
                break
        if best is None:
            # 只认精确匹配（slug 或标题和我们对得上）。以前这里会退化成"取第一条搜索结果"，
            # 结果是把不相干模组的分类贴到本地模组上 —— 宁可没有分类，也别标错。
            continue
        out = []
        for c in (best.get("categories") or []):
            cn = _CATEGORY_CN.get(str(c).lower())
            if cn and cn not in out:
                out.append(cn)
        return out or None
    return None


def fetch_categories_cached(key, modid="", name="", timeout=10):
    """带磁盘缓存的分类查询；key 用 Mod ID（没有就用文件名的去扩展名形式）。

    缓存里存空列表表示"查过了但没有"，下次不再联网重查。
    """
    key = str(key or "").strip().lower()
    if not key:
        return None
    with _TAG_CACHE_LOCK:
        cache = _tag_cache()
        if key in cache:
            hit = cache[key]
            return list(hit) if hit else None
    cats = fetch_categories(modid=modid, name=name, timeout=timeout)
    with _TAG_CACHE_LOCK:
        _tag_cache()[key] = list(cats or [])
        _save_tag_cache()
    return cats


def tag_cache_size():
    """缓存条数（设置窗口里显示用）。"""
    with _TAG_CACHE_LOCK:
        return len(_tag_cache())


# ------------------------------------------------------------------ CurseForge
# 需要 API Key（申请见 https://support.curseforge.com/en/support/solutions/
# articles/9000208346-about-the-curseforge-api-and-how-to-apply-for-a-key ）。
# key 由**用户自己填**，存在用户主目录的独立文件里（见 utils/secrets.py）：
# 程序不携带、仓库里没有、日志里不出现。
#
# 三条刻意的设计，正好对着 Overwolf 审核关心的三件事（作者收入 / 服务器压力 / 作者同意）：
#   1. key 只走 `x-api-key` 请求头，**不进 URL**（URL 会进代理日志、浏览器历史、异常信息）；
#   2. **不用 API 返回的 downloadUrl**（那是 CDN 直链，会绕开作者的下载页和广告分成），
#      只给官网页面链接，下载由用户在 CurseForge 上自己完成；
#   3. **不落盘缓存**：ToS 3.1 明确禁止 save or cache API 数据，所以这里一次请求一次结果。
#      （Modrinth 那套本地分类缓存保持原样，那是另一家的公开接口，不受这条约束。）
# 另外：所有请求都是"用户点了搜索/详情"才发，没有后台轮询、没有批量爬。
CURSEFORGE_API = "https://api.curseforge.com/v1"
CURSEFORGE_GAME_ID = 432          # 432 = Minecraft
CURSEFORGE_MOD_CLASS = 6          # classId 6 = Mods
_CURSEFORGE_SITE = "https://www.curseforge.com/minecraft/mc-mods/%s"
_CF_LOADER_ID = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
_CF_SORT_DOWNLOADS = 2            # sortField 2 = 总下载量


class CurseForgeNoKey(RuntimeError):
    """没配 key —— 调用方应当安静地退回 Modrinth，而不是弹红框。"""


def curseforge_available():
    """配了 key 没有（界面据此决定显不显示 CurseForge 那一栏）。"""
    from utils import secrets
    return secrets.has_key()


def _cf_get(路径, 参数=None, timeout=15, key=None):
    """发一次 CurseForge 请求，返回解析后的 JSON。

    key **只出现在请求头里**。异常也一律脱敏后再往上抛：HTTPError 的原文可能回显
    请求信息，不能直接扔给界面或日志。
    """
    from utils import secrets
    k = secrets.clean(key) if key is not None else secrets.get_key()
    if not k:
        raise CurseForgeNoKey("未设置 CurseForge API Key")
    url = CURSEFORGE_API + 路径
    if 参数:
        url += "?" + urllib.parse.urlencode(参数)
    req = urllib.request.Request(url, headers={
        "x-api-key": k,                      # ← 全项目唯一一处把 key 交出去的地方
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("CurseForge 拒绝了这个 API Key（HTTP %d）" % e.code)
        if e.code == 404:
            raise RuntimeError("CurseForge 上没有这个项目（HTTP 404）")
        raise RuntimeError("CurseForge 请求失败（HTTP %d）" % e.code)
    except Exception as e:
        raise RuntimeError(secrets.redact_exc(e))


def test_curseforge_key(key=None, timeout=12):
    """试一下这个 key 能不能用（设置页的「测试」按钮）。返回 (ok, 说明文字)。

    只发一次最小请求（搜索 limit=1），**不写入磁盘**；说明里也不含 key 本身。
    传了 key 就测传进来的那一份（测"还没保存"的输入框内容），不传就测当前生效的。
    """
    from utils import secrets
    k = secrets.clean(key) if key is not None else secrets.get_key()
    if not k:
        return False, "还没填 Key"
    try:
        _cf_get("/mods/search",
                {"gameId": CURSEFORGE_GAME_ID, "classId": CURSEFORGE_MOD_CLASS,
                 "searchFilter": "jei", "pageSize": 1},
                timeout=timeout, key=k)
    except CurseForgeNoKey:
        return False, "还没填 Key"
    except Exception as e:
        return False, secrets.redact_exc(e)
    return True, "Key 可用（指纹 %s）" % secrets.fingerprint(k)


def search_curseforge(query, limit=8, mc_version="", loader="", timeout=15):
    """搜 CurseForge 模组（只读元数据）。

    返回结构和 search_modrinth 一致（title / slug / project_id / author /
    description / downloads / project_url / categories / latest_version /
    download_url），界面那套渲染逻辑两边共用，不用再写一份。
    """
    q = str(query or "").strip()
    if not q:
        return []
    参数 = {"gameId": CURSEFORGE_GAME_ID, "classId": CURSEFORGE_MOD_CLASS,
            "searchFilter": q, "pageSize": max(1, min(int(limit), 50)),
            "sortField": _CF_SORT_DOWNLOADS, "sortOrder": "desc"}
    if mc_version:
        参数["gameVersion"] = str(mc_version)
    lid = _CF_LOADER_ID.get(str(loader or "").lower())
    if lid:
        参数["modLoaderType"] = lid
    data = _cf_get("/mods/search", 参数, timeout=timeout)
    结果 = []
    for m in (data.get("data") or []):
        slug = str((m.get("links") or {}).get("slug") or "").strip()
        作者 = (m.get("authors") or [{}])[0].get("name", "")
        结果.append({
            "title": m.get("name", ""),
            "slug": slug or str(m.get("id", "")),
            "project_id": m.get("id"),
            "author": 作者,
            "description": (m.get("summary") or "")[:120],
            "downloads": m.get("downloadCount", 0),
            "project_url": _CURSEFORGE_SITE % (slug or m.get("id")),
            "categories": [c.get("name", "") for c in (m.get("categories") or [])],
            "latest_version": "",
            "download_url": "",
        })
    return 结果


def fetch_project_latest_curseforge(project_id, timeout=12, mc_version="", loader="",
                                    slug=""):
    """取 CurseForge 项目的最新文件，返回和 fetch_project_latest 同一个形状
    （latest_version / download_url / status，状态同样叫 ok / no_match / error）。

    走 /mods/{id}/files 而不是搜索接口里的 latestFiles —— 搜索返回的那个经常是空的。
    `download_url` **刻意指向官网文件页**（不是 API 给的 CDN 直链）：直链绕开作者的
    下载页和广告分成，而那正是 CurseForge 审核最关心的一条。
    """
    if not project_id:
        return {"latest_version": "", "download_url": "", "status": "error"}
    参数 = {"pageSize": 50}
    if mc_version:
        参数["gameVersion"] = str(mc_version)
    lid = _CF_LOADER_ID.get(str(loader or "").lower())
    if lid:
        参数["modLoaderType"] = lid
    try:
        data = _cf_get("/mods/%s/files" % project_id, 参数, timeout=timeout)
    except Exception:
        return {"latest_version": "", "download_url": "", "status": "error"}
    files = data.get("data") or []
    if not files:
        return {"latest_version": "", "download_url": "", "status": "no_match"}
    f0 = files[0]
    地址 = ""
    if slug:
        地址 = "%s/files/%s" % (_CURSEFORGE_SITE % slug, f0.get("id"))
    return {"latest_version": f0.get("displayName") or f0.get("fileName") or "",
            "download_url": 地址, "status": "ok"}

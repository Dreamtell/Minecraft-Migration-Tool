# core/mod_search.py
"""联网搜索模组信息（参考 PCL2 的 扫描本地->提取身份->联网匹配 思路）。

当前实现以 Modrinth 公开 API 为主（免费、无需密钥）。CurseForge 需要申请
API Key，接入时只需扩展一个同结构的 *_curseforge 函数即可。
"""
import json
import urllib.request
import urllib.parse

USER_AGENT = "MinecraftMigrateTool/1.0 (contact: local)"

MODRINTH_SEARCH = "https://api.modrinth.com/v2/search?query={query}&limit={limit}&index=downloads"
MODRINTH_VERSIONS = "https://api.modrinth.com/v2/project/{project_id}/version"


def _json_get(url, timeout=15):
    """带 UA 的 GET，返回解析后的 JSON。失败时抛异常。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _fetch_latest_version(project_id, timeout=12):
    """取单个项目的最新版本号与下载链接，失败返回 ("", "")。"""
    try:
        versions = _json_get(MODRINTH_VERSIONS.format(project_id=project_id),
                             timeout=timeout)
        if versions:
            v0 = versions[0]
            files = v0.get("files") or []
            url = files[0].get("url", "") if files else ""
            return (v0.get("version_number", ""), url)
    except Exception:
        pass
    return ("", "")


def search_modrinth(query, limit=8):
    """快速搜索 Modrinth，按下载量排序，只返回搜索元数据（不含版本细节），速度快。

    返回每个项目：title/slug/project_id/author/description/downloads/project_url，
    以及空的 latest_version/download_url（需用 fetch_project_latest 再取）。
    """
    if not query or not query.strip():
        return []

    try:
        data = _json_get(MODRINTH_SEARCH.format(
            query=urllib.parse.quote(query.strip()), limit=int(limit)))
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
            "latest_version": "",
            "download_url": "",
        })

    return results


def fetch_project_latest(project_id, timeout=12):
    """取单个项目的最新版本号与下载链接（供点击/复制时按需获取）。"""
    vnum, url = _fetch_latest_version(project_id, timeout=timeout)
    return {"latest_version": vnum, "download_url": url}


def format_downloads(n):
    """把下载量格式化成 2.1亿 / 9000万 之类的可读形式。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 100000000:
        return "%.1f亿" % (n / 100000000)
    if n >= 10000:
        return "%.1f万" % (n / 10000)
    return str(n)

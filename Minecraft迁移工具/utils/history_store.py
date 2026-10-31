# utils/history_store.py
"""迁移历史档案（Archive）存储。

设计目标：
  · **一次迁移一份档案**，文件名就是时间戳（`20261022-233015-482.json`），
    用户随时可以翻，不依赖目标实例还在不在；
  · 真实迁移和**模拟运行都存**（模拟的标成 "dry"，用不同颜色区分），
    因为"我刚才模拟过什么"本身也是有用的信息；
  · 保存份数有上限（默认 10，最少 1、最多 15），超出后删最旧的；
  · 全部是原子写：崩在写一半不会留下坏档案。

档案内容（JSON）大致是：
    时间 / 模式 / 源目录 / 目标目录 / 耗时 / 结果
    统计：模组 / config / 其它文件 的 复制成功 / 跳过 / 失败
    清单：本次的 mods、config 条目、其它文件、失败明细
    日志：整段执行日志（截断到 200k 字符）
"""

import json
import os
import time
from pathlib import Path

from utils.config import CONFIG_FILE, load_raw_config, save_raw_config

DIR_NAME = "migration_history"
DEFAULT_LIMIT = 10
MIN_LIMIT = 1
MAX_LIMIT = 15
LOG_MAX_CHARS = 200_000
LIST_MAX = 800          # 单个清单最多记这么多条，防止档案膨胀


def _root():
    """档案目录：和配置文件放同一层（MCTOOL_CONFIG 指到别处时也跟着走）。"""
    return Path(CONFIG_FILE).parent / DIR_NAME


def _ensure():
    try:
        d = _root()
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return None


# ---------------- 上限 ----------------
def get_limit():
    try:
        n = int((load_raw_config() or {}).get("history_limit", DEFAULT_LIMIT))
    except Exception:
        n = DEFAULT_LIMIT
    return max(MIN_LIMIT, min(MAX_LIMIT, n))


def set_limit(n):
    try:
        n = int(n)
    except Exception:
        n = DEFAULT_LIMIT
    n = max(MIN_LIMIT, min(MAX_LIMIT, n))
    save_raw_config({"history_limit": n})
    prune(n)
    return n


# ---------------- 写 ----------------
def new_id():
    return time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)


def save_record(record, limit=None):
    """写一份档案并裁剪到上限。返回档案 id（失败返回 None）。"""
    d = _ensure()
    if d is None:
        return None
    rec = dict(record or {})
    rec.setdefault("id", new_id())
    rec.setdefault("time", time.strftime("%Y-%m-%d %H:%M:%S"))
    rec.setdefault("epoch", time.time())
    清单 = rec.get("lists") or {}
    for k, v in list(清单.items()):
        if isinstance(v, list) and len(v) > LIST_MAX:
            rec["lists"][k] = v[:LIST_MAX]
    lg = rec.get("log")
    if isinstance(lg, str) and len(lg) > LOG_MAX_CHARS:
        rec["log"] = lg[-LOG_MAX_CHARS:]

    path = d / ("%s.json" % rec["id"])
    try:
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        return None

    prune(get_limit() if limit is None else int(limit))
    return rec["id"]


# ---------------- 读 ----------------
def list_records(include_log=False):
    """按时间倒序返回档案列表（默认不带 log，列表页不需要那么大的内容）。"""
    d = _root()
    out = []
    try:
        files = sorted(d.glob("*.json"), reverse=True)
    except Exception:
        return out
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                continue
            rec.setdefault("id", p.stem)
            if not include_log:
                rec.pop("log", None)
            out.append(rec)
        except Exception:
            continue
    out.sort(key=lambda r: r.get("epoch") or 0, reverse=True)
    return out


def load_record(rec_id):
    """读一份完整档案（含 log）。"""
    try:
        p = _root() / ("%s.json" % rec_id)
        with open(p, "r", encoding="utf-8") as f:
            rec = json.load(f)
        if isinstance(rec, dict):
            rec.setdefault("id", rec_id)
            return rec
    except Exception:
        pass
    return None


def mark_rolled_back(rec_id):
    """把某份档案标成"已回滚过"。返回是否改到了。"""
    rec = load_record(rec_id)
    if not rec:
        return False
    rec["rolled_back"] = True
    rec["rollback_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return bool(save_record(rec, limit=max(count() + 1, get_limit())))


def delete_record(rec_id):
    try:
        (_root() / ("%s.json" % rec_id)).unlink()
        return True
    except Exception:
        return False


def clear_all():
    d = _root()
    n = 0
    try:
        for p in d.glob("*.json"):
            try:
                p.unlink()
                n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n


def count():
    try:
        return len(list(_root().glob("*.json")))
    except Exception:
        return 0


def prune(limit=None):
    """只保留最近 limit 份，返回删掉的份数。"""
    limit = get_limit() if limit is None else max(MIN_LIMIT, int(limit))
    d = _root()
    try:
        files = sorted(d.glob("*.json"), reverse=True)
    except Exception:
        return 0
    删 = 0
    for p in files[limit:]:
        try:
            p.unlink()
            删 += 1
        except Exception:
            pass
    return 删


def format_size(n):
    try:
        n = float(n)
    except Exception:
        return "0 B"
    if n < 1024:
        return "%d B" % int(n)
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.1f MB" % (n / (1024.0 * 1024))

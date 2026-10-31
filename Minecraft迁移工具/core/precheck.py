# core/precheck.py
"""迁移前自检：把"等跑起来才发现不对"的问题提前摆出来。

只做**便宜且确定**的检查（列目录、stat、试着写一个临时文件），不读 jar 内容、
不算总大小 —— 那些留给正式流程。检查结果分三档：

  fail  必须停下来（路径不存在、目标不可写、源和目标重叠）
  warn  能跑但结果可能不是用户想要的（清单里的模组找不到、存档不存在…）
  info  只是提示（清单外还有多少 jar 不会被复制）

对外只有一个入口 `run_precheck(...)`，返回
    {"items": [{"level": ..., "text": ...}], "fail": n, "warn": n, "info": n}
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from utils.i18n import trp

# 一次最多列几个名字，避免对话框被撑爆
_列举上限 = 5


def _条(level, text):
    return {"level": level, "text": text}


def _列名(名字们):
    """把几个名字拼成"a.jar、b.jar 等 3 个"。"""
    名单 = list(名字们)
    if not 名单:
        return ""
    头 = "、".join(名单[:_列举上限])
    if len(名单) > _列举上限:
        return trp("{0} 等 {1} 个", 头, len(名单))
    return 头


def _查路径(src_path, tgt_path):
    """源/目标本身：存在、是目录、能读、能写。"""
    条们 = []
    for 名, 路径 in ((trp("旧版目录（源）"), src_path), (trp("新版目录（目标）"), tgt_path)):
        if not 路径.exists():
            条们.append(_条("fail", trp("{0}不存在：{1}", 名, 路径)))
        elif not 路径.is_dir():
            条们.append(_条("fail", trp("{0}不是文件夹：{1}", 名, 路径)))
    if any(c["level"] == "fail" for c in 条们):
        return 条们

    # 读：列一次目录就知道（权限不足会抛 PermissionError/OSError）
    try:
        with os.scandir(src_path) as 迭:
            next(迭, None)
    except OSError as e:
        条们.append(_条("fail", trp("旧版目录（源）读不了：{0}", e)))

    # 写：在目标根下建一个临时文件再删（比 os.access 可靠，Windows 上 access 会骗人）
    临时 = tgt_path / ".mctool_write_test"
    try:
        临时.write_bytes(b"mctool")
        临时.unlink()
    except OSError as e:
        条们.append(_条("fail", trp("新版目录（目标）写不进去：{0}", e)))
    finally:
        try:
            if 临时.exists():
                临时.unlink()
        except OSError:
            pass
    return 条们


def _查重叠(src_path, tgt_path):
    """源和目标相同、或者一个套在另一个里面 —— 迁移会把自己复制进去。"""
    try:
        源 = src_path.resolve()
        标 = tgt_path.resolve()
    except OSError:
        return []
    if 源 == 标:
        return [_条("fail", trp("源目录和目标目录是同一个：{0}", 源))]
    for 甲, 乙, 说法 in ((源, 标, trp("目标目录在源目录里面（会把源自己复制进去）")),
                         (标, 源, trp("源目录在目标目录里面（目标里的旧文件会被当成源）"))):
        try:
            if 乙 == 甲 or 甲 in 乙.parents:
                return [_条("fail", trp("{0}：{1} / {2}", 说法, 源, 标))]
        except OSError:
            continue
    return []


def _查存档(src_path, tgt_path, world):
    """存档在不在（不在就只跳过存档，不该挡住迁移）。

    路径是 `saves/<名字>`：migrator 复制存档查的是 `src/saves/<名字>`
    （core/migrator.py:912），界面那个「存档已存在」徽标查的也是它
    （ui/mw_paths.py:372）。这里以前写成 `world/<名字>`，于是别人的存档明明
    在，自检却报「存档目录不存在」—— 和徽标自相矛盾。
    """
    if not world:
        return [_条("warn", trp("没有填存档名称，存档不会被迁移"))]
    源存档 = src_path / "saves" / world
    if 源存档.is_dir():
        return []
    目标存档 = tgt_path / "saves" / world
    if 目标存档.is_dir():
        return [_条("info", trp("源整合包里没有存档「{0}」，目标里已经有了，这个存档不会被改动",
                                 world))]
    return [_条("warn", trp("源和目标里都没有存档「{0}」，这次不会迁移存档", world))]


def _查模组清单(src_path, modlist):
    """清单里的 jar 在源目录里找不找得到；顺带报一下清单外还有多少。"""
    条们 = []
    模组目录 = src_path / "mods"
    if not modlist:
        return 条们
    if not 模组目录.is_dir():
        return [_条("fail", trp("源目录里没有 mods 文件夹，清单里的 {0} 个模组都复制不了",
                                len(modlist)))]
    try:
        实际 = {p.name.lower(): p.name for p in 模组目录.iterdir() if p.is_file()}
    except OSError as e:
        return [_条("warn", trp("读不了源目录的 mods 文件夹：{0}", e))]

    缺 = [n for n in modlist if n.lower() not in 实际]
    if 缺:
        条们.append(_条("warn", trp("清单里有 {0} 个模组在源目录找不到：{1}",
                                    len(缺), _列名(缺))))
    重复 = len(modlist) - len({n.lower() for n in modlist})
    if 重复 > 0:
        条们.append(_条("info", trp("清单里有 {0} 个重复条目（不会重复复制）", 重复)))
    清单外 = [v for k, v in 实际.items() if k.endswith(".jar") and
              k not in {n.lower() for n in modlist}]
    if 清单外:
        条们.append(_条("info", trp("源目录里还有 {0} 个 jar 不在清单里，不会被复制（{1}）",
                                    len(清单外), _列名(清单外))))
    return 条们


def _查相对清单(基准, 清单, 模板):
    """条目是相对 基准 目录的路径，看看还在不在。

    注意两类清单的基准不一样：
      · config 清单：相对 `<源>/config/`（迁移时也是复制到 `<目标>/config/`）
      · 其它文件清单：相对整合包根目录（shaderpacks/、resourcepacks/ 这种）
    一开始两类都按根目录查，结果用户的 15 条 config 全被判成"找不到"——
    真机核对过：源 config/ 下 15 条一个不缺。
    """
    if not 清单:
        return []
    缺 = [x for x in 清单 if not (基准 / x).exists()]
    if not 缺:
        return []
    return [_条("warn", trp(模板, len(缺), _列名(缺)))]


def _查覆盖(src_path, tgt_path, world, modlist, configlist, extralist,
            overwrite, extra_overwrite):
    """目标里已经有同名内容时，分开说清哪些会被覆盖、哪些不会。

    三类东西的覆盖策略不一样（对着 migrator 里的 safe_copy 调用核过）：
      · 模组     跟随「覆盖已存在的模组」开关
      · 存档 / config  总是覆盖（覆盖前会先备份）
      · 其它文件 跟随 extra_conflict
    以前一律按 overwrite 说，用户配置成 overwrite=False + extra_conflict=overwrite
    时就会说反（说 shaderpacks 不会被覆盖，其实会）。
    """
    if not tgt_path.is_dir():
        return []
    会覆盖 = 0
    会跳过 = 0
    try:
        if world and (tgt_path / "saves" / world).is_dir():
            会覆盖 += 1
        模组目录 = tgt_path / "mods"
        if modlist and 模组目录.is_dir():
            目标名 = {p.name.lower() for p in 模组目录.iterdir() if p.is_file()}
            同名的 = sum(1 for n in modlist if n.lower() in 目标名)
            if overwrite:
                会覆盖 += 同名的
            else:
                会跳过 += 同名的
        for 条目 in list(configlist):
            if (tgt_path / "config" / 条目).exists():
                会覆盖 += 1
        for 条目 in list(extralist):
            if (tgt_path / 条目).exists():
                if extra_overwrite:
                    会覆盖 += 1
                else:
                    会跳过 += 1
    except OSError:
        return []
    条们 = []
    if 会覆盖 > 0:
        条们.append(_条("info", trp("目标里已有 {0} 处同名内容，会按「覆盖」处理（覆盖前会先备份）",
                                 会覆盖)))
    if 会跳过 > 0:
        条们.append(_条("warn", trp("目标里已有 {0} 处同名内容，当前是「跳过已存在」，这些不会被覆盖",
                                 会跳过)))
    return 条们


def run_precheck(src, tgt, world, modlist, configlist, extralist, overwrite=False,
                 extra_overwrite=False):
    """跑一遍自检。参数都是原始值（路径字符串 / 清单列表），返回统计与条目。"""
    src_path = Path(str(src or "")).expanduser()
    tgt_path = Path(str(tgt or "")).expanduser()
    items = []
    items += _查路径(src_path, tgt_path)
    if not any(c["level"] == "fail" for c in items):
        items += _查重叠(src_path, tgt_path)
        items += _查存档(src_path, tgt_path, str(world or "").strip())
        items += _查模组清单(src_path, list(modlist or []))
        items += _查相对清单(src_path / "config", list(configlist or []),
                             "config 清单里有 {0} 条在源 config 目录找不到：{1}")
        items += _查相对清单(src_path, list(extralist or []),
                             "其它文件清单里有 {0} 条在源目录找不到：{1}")
        items += _查覆盖(src_path, tgt_path, str(world or "").strip(),
                         list(modlist or []), list(configlist or []),
                         list(extralist or []), bool(overwrite),
                         bool(extra_overwrite))
    统计 = {"fail": 0, "warn": 0, "info": 0}
    for c in items:
        统计[c["level"]] = 统计.get(c["level"], 0) + 1
    统计["items"] = items
    return 统计

# core/instance_check.py
"""「这个路径是不是一个有效的 Minecraft 整合包实例」的判定 + 路径行状态文案(Qt 主界面用)。

规则逐条对应 Tk 版 ui/mw_paths.py 的 `_is_valid_instance` / `validate_path`。那个模块顶层
import tkinter,Qt 进程不能碰,所以这里是一份独立实现;tools/test_qt_main_input_parity.py
里有一条用例拿同一批目录两边各跑一遍对账,防止两份规则悄悄分叉。
"""
from pathlib import Path

from utils.file_io import check_writable
from utils.i18n import tr, trp


def is_valid_instance(path_str):
    """返回 (是否有效, 原因, 细节 dict)。原因是中文原文,显示时再 tr()。"""
    p = Path(path_str)
    details = {}

    # ----- 第1层:基础路径检查 -----
    if not p.exists():
        return False, "路径不存在", details
    if not p.is_dir():
        return False, "不是目录", details
    try:
        check_writable(p)
        details["read_write"] = True
    except Exception:
        details["read_write"] = False
        return False, "无读写权限，请以管理员身份运行", details

    # 路径含中文只是警告,不算失败
    if any("一" <= ch <= "鿿" for ch in str(p)):
        details["has_chinese"] = True

    # ----- 第2层:核心标识 -----
    has_mods = (p / "mods").is_dir()
    has_config = (p / "config").is_dir()
    has_saves = (p / "saves").is_dir()
    has_libraries = (p / "libraries").is_dir()
    has_versions = (p / "versions").is_dir()
    details["has_mods"] = has_mods
    details["has_config"] = has_config
    details["has_saves"] = has_saves
    details["has_libraries"] = has_libraries

    has_options = (p / "options.txt").exists()
    details["has_options"] = has_options
    details["has_launcher_profiles"] = (p / "launcher_profiles.json").exists()

    version_dirs = []
    if has_versions:
        for v_dir in (p / "versions").iterdir():
            if v_dir.is_dir() and (v_dir / "version.json").exists():
                version_dirs.append(v_dir.name)
        details["valid_versions"] = version_dirs

    if has_mods:
        mods_dir = p / "mods"
        details["mod_count"] = len(list(mods_dir.glob("*.jar")))
        fabric = list(mods_dir.glob("*fabric*.jar")) + list(mods_dir.glob("*.fabric.mod.json*"))
        forge = list(mods_dir.glob("*forge*.jar"))
        details["fabric_mods"] = len(fabric) > 0
        details["forge_mods"] = len(forge) > 0

    details["has_pcl_ini"] = (p / "PCL.ini").exists()

    # ----- 第3层:综合判断 -----
    if not has_mods:
        return False, "缺少 mods 目录（不是有效的整合包）", details
    if not has_config:
        return False, "缺少 config 目录（不是有效的整合包）", details
    if details.get("mod_count", 0) == 0:
        return False, "mods 目录为空（没有模组文件）", details
    # 没有 version.json 时,只要有 options.txt 也算(旧版整合包)
    if not version_dirs and not has_options:
        return False, "缺少 version.json 或 options.txt，无法识别为有效实例", details

    details["is_valid"] = True
    return True, "✅ 有效实例目录", details


def status_for(path_str, which):
    """路径行右侧的状态:返回 (kind, 文字, 悬停说明)。

    kind: "muted"(没填) / "ok" / "warn"(有效但路径含中文) / "fail";文字只留一个图标,
    详细结论全进悬停说明(和 Tk 版一样,路径框本来就被浏览按钮挤)。
    which 是「源」或「目标」。
    """
    if not path_str:
        return "muted", tr("（未选择）"), trp("尚未选择「{0}」整合包路径", which)

    valid, reason, d = is_valid_instance(path_str)
    tip = [f"📁 {path_str}", ""]
    if not valid:
        tip.append(tr("❌ 不是有效的整合包实例"))
        tip.append(tr("· 原因：") + tr(reason))
        return "fail", "❌", "\n".join(tip)

    tip.append(tr("✅ 有效的 Minecraft 整合包实例"))
    tip.append(tr("· 有 saves/ 存档目录") if d.get("has_saves") else tr("· 无 saves/ 存档目录"))
    tip.append(trp("· 模组 {0} 个", d.get("mod_count", 0)))
    if d.get("valid_versions"):
        tip.append(trp("· 版本：{0}", ", ".join(d["valid_versions"][:3])))
    loaders = [n for n, k in (("Fabric", "fabric_mods"), ("Forge", "forge_mods")) if d.get(k)]
    if loaders:
        tip.append(tr("· 加载器：") + ", ".join(loaders))
    launchers = []
    if d.get("has_launcher_profiles"):
        launchers.append(tr("官方启动器"))
    if d.get("has_pcl_ini"):
        launchers.append("PCL2")
    if launchers:
        tip.append(tr("· 启动器：") + ", ".join(launchers))
    if d.get("has_chinese"):
        tip += ["", tr("⚠️ 路径含中文，建议改成纯英文（个别模组/存档读取会出问题）")]
        return "warn", "⚠️", "\n".join(tip)
    return "ok", "✅", "\n".join(tip)

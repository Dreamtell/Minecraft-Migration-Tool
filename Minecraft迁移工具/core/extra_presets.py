# core/extra_presets.py
"""「其它文件」清单的「＋ 常用目录」预设表。

原来放在 ui/mw_common.py 里,但那个模块顶层 import tkinter,Qt 主界面不能碰,
所以抽到这里,两边共用同一份(Tk 侧在 ui/mw_common.py 里原名 re-export)。

⚠ 说明文字用 trp()(i18n 的"带入占位符的翻译"),这里不能 import 任何界面模块。
"""
from utils.i18n import trp

# (配置键, 条目(相对整合包根目录), 中文说明)
# 清单是唯一依据:要带什么由用户自己加(菜单里点一下就写进清单)。
# 配置键也被老配置 extra_defaults 的一次性迁移用到,别改名。
EXTRA_PRESETS = (
    ("shaderpacks",    "shaderpacks/",    trp("光影包（Iris / OptiFine）")),
    ("resourcepacks",  "resourcepacks/",  trp("资源包（材质包）")),
    ("schematics",     "schematics/",     trp("投影 / 蓝图（Litematica、WorldEdit）")),
    ("XaeroWorldMap",  "XaeroWorldMap/",  trp("Xaero 世界地图数据")),
    ("XaeroWaypoints", "XaeroWaypoints/", trp("Xaero 路径点")),
    ("xaero",          "xaero/",          trp("Xaero 小地图（旧版目录）")),
    ("journeymap",     "journeymap/",     trp("JourneyMap 地图数据")),
    ("kubejs",         "kubejs/",         trp("KubeJS 脚本")),
    ("defaultconfigs", "defaultconfigs/", trp("整合包默认配置（Forge / NeoForge）")),
    ("local",          "local/",          trp("本地数据（部分模组自建）")),
    ("screenshots",    "screenshots/",    trp("截图")),
    ("servers.dat",    "servers.dat",     trp("服务器列表")),
    ("optionsof.txt",  "optionsof.txt",   trp("OptiFine 视频设置")),
    ("iris.properties", "iris.properties", trp("Iris 光影设置")),
)


def norm_entry(entry):
    """比较用的归一形态:正斜杠、去末尾 /、小写(和 Tk 版 _open_extra_preset_menu 一致)。

    ⚠ 「已在清单」徽章和真正添加必须用**同一个**归一函数：徽章那边归一（忽略大小写），
    添加那边却按原样比对，就会出现"徽章说已在、点一下又添一行"。
    """
    return str(entry or "").strip().replace("\\", "/").rstrip("/").lower()


_norm = norm_entry            # 老名字，模块内部还用


def preset_rows(existing_lines):
    """菜单要显示的行:[(条目, 说明, 是否已在清单里), ...]。

    existing_lines:清单现有的每一行(原样传进来,这里负责归一和去空行)。
    """
    have = {_norm(x) for x in existing_lines if str(x).strip()}
    return [(rel, desc, _norm(rel) in have) for _k, rel, desc in EXTRA_PRESETS]


def describe(entry):
    """某个预设条目的说明文字;不是预设就返回空串。"""
    return next((d for _k, rel, d in EXTRA_PRESETS if rel == entry), "")

# 展示分组（只影响「＋ 常用目录」弹窗里的顺序和分组标题）。
# 没列进来的新预设自动落到最后一组，不会因为忘了加而消失。
# ⚠ 键要和 EXTRA_PRESETS 里的条目**逐字一致**（文件夹带末尾 /）：按 "shaderpacks"
# 去匹配 "shaderpacks/" 永远匹配不上，会整组掉进「未分组」。
_GROUPS = (
    (trp("画面与资源"), ("shaderpacks/", "resourcepacks/", "optionsof.txt", "iris.properties")),
    (trp("地图与投影"), ("schematics/", "XaeroWorldMap/", "XaeroWaypoints/", "xaero/", "journeymap/")),
    (trp("模组与脚本"), ("kubejs/", "defaultconfigs/", "local/")),
    (trp("其它"), ("screenshots/", "servers.dat")),
)


def preset_groups(existing_lines):
    """按用途分组后的行：[(组标题, [(条目, 说明, 是否已在清单), ...]), ...]。"""
    rows = preset_rows(existing_lines)
    by = {r[0]: r for r in rows}
    used = set()
    out = []
    for title, keys in _GROUPS:
        items = [by[k] for k in keys if k in by]
        used.update(k for k in keys if k in by)
        if items:
            out.append((title, items))
    rest = [r for r in rows if r[0] not in used]
    if rest:
        out.append((trp("未分组"), rest))
    return out

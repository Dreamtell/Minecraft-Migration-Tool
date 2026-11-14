# utils/fx.py
"""特效强度：一个 4 档的开关，统管启动动画 / 浮标粒子 / 翻转手感 / 消息卡片动画。

    off       关闭   —— 不要动画：不闪屏、不画粒子、按钮最干脆、消息卡片直接出现
    light     轻     —— 都保留，但更快更少
    standard  标准   —— 默认，和旧版观感一致
    full      全开   —— 闪屏更久、粒子更多、按钮更沉、卡片更慢

为什么不让各模块自己读配置：
- 这些值在**每次动画**里都要用（一秒几十次），读文件太贵；
  所以启动时 bootstrap(cfg) 把档位放进模块级缓存，动画只读缓存。
- 派生的旧键（splash / float_edge_fx / start_btn_feel）在设置生效时由
  apply_to() 一起写回配置，Tk 版和 Qt 版因此不需要各写一套判断。

这个模块只依赖标准库（不 import Qt / tkinter），闪屏子进程也能用。
"""

LEVELS = ("off", "light", "standard", "full")

# 档位在界面上的叫法（i18n 里也有一份，见 utils/i18n.py）
LABELS = {
    "off": "关闭",
    "light": "轻",
    "standard": "标准",
    "full": "全开",
}

DEFAULT = "standard"

# anim     —— 动画时长倍率（0 = 不要动画）
# particle —— 粒子数量倍率（0 = 不画）
# splash   —— 是否显示启动闪屏
# edge_fx  —— 浮标贴边时是否显示绿光带 + 粒子
# feel     —— 「开始迁移」按钮翻转手感（ui/flip_button.py 的 PRESETS 键）
# toast    —— 消息卡片淡入淡出的基准毫秒数（0 = 立刻出现）
PRESETS = {
    "off": {"anim": 0.0, "particle": 0.0, "splash": False, "edge_fx": False,
            "feel": "crisp", "toast": 0},
    "light": {"anim": 0.60, "particle": 0.50, "splash": True, "edge_fx": True,
              "feel": "crisp", "toast": 110},
    "standard": {"anim": 1.0, "particle": 1.0, "splash": True, "edge_fx": True,
                 "feel": "standard", "toast": 160},
    "full": {"anim": 1.40, "particle": 1.80, "splash": True, "edge_fx": True,
             "feel": "heavy", "toast": 260},
}

_cache = {"level": DEFAULT}


def normalize(level):
    """把任意输入收成 4 档之一；认不出来一律回默认档。"""
    # 也接受数字（老配置/手改配置里写了 0~3）—— 注意 0 会被 `or ""` 吃掉，先判
    if isinstance(level, int) and not isinstance(level, bool) and 0 <= level <= 3:
        return LEVELS[level]
    try:
        s = str(level if level is not None else "").strip().lower()
    except Exception:
        return DEFAULT
    if s in ("0", "1", "2", "3"):
        return LEVELS[int(s)]
    return s if s in PRESETS else DEFAULT


def current():
    """当前生效的档位（启动时由 bootstrap() 填好）。"""
    return _cache["level"]


def set_level(level):
    """只改缓存，不动配置（设置窗在用户点 OK 时自己写盘）。"""
    _cache["level"] = normalize(level)
    return _cache["level"]


def bootstrap(cfg):
    """启动时调一次：从配置里读档位填缓存。返回档位字符串。"""
    try:
        level = normalize((cfg or {}).get("fx_level"))
    except Exception:
        level = DEFAULT
    return set_level(level)


def preset(level=None):
    """取某一档的完整参数；不传就用当前档。"""
    return PRESETS[normalize(level if level is not None else current())]


def anim_scale():
    """动画时长倍率（0 表示不要动画）。"""
    return float(preset()["anim"])


def particle_scale():
    """粒子数量倍率（0 表示不画）。"""
    return float(preset()["particle"])


def toast_ms(default=160):
    """消息卡片淡入淡出的基准毫秒数（档位没配就用 default）。"""
    v = preset()["toast"]
    return int(v if v is not None else default)


def ms(base_ms, floor=1.0):
    """按当前档位缩放一个毫秒数；关闭档给 floor（默认 1ms，等于立刻）。"""
    scale = anim_scale()
    if scale <= 0.0:
        return int(max(0, round(floor)))
    return int(max(1, round(float(base_ms) * scale)))


def derived(level=None):
    """设置生效时要写进配置的派生键（旧键名，Tk/Qt 两边都认）。"""
    p = preset(level)
    return {
        "fx_level": normalize(level if level is not None else current()),
        "splash": bool(p["splash"]),
        "float_edge_fx": bool(p["edge_fx"]),
        "start_btn_feel": str(p["feel"]),
    }


def apply_to(cfg, level=None):
    """把档位 + 派生键写进一个配置 dict（就地改，也返回它）。"""
    if not isinstance(cfg, dict):
        return cfg
    cfg.update(derived(level))
    return cfg


def from_config(cfg):
    """如果配置里已经有 fx_level，就按它反推三档派生值；没有就原样返回。

    用于「老配置升级」：老配置只有 splash / start_btn_feel / float_edge_fx
    这些散键，没人给 fx_level 时保留用户原来的选择，不擅自改。
    """
    if not isinstance(cfg, dict) or "fx_level" not in cfg:
        return cfg
    out = dict(cfg)
    out.update(derived(cfg.get("fx_level")))
    return out

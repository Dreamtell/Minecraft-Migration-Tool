# utils/secrets.py
"""API Key 的存取与脱敏 —— "防 Key 暴露"这件事的全部脏活。

先说清楚能做到什么、做不到什么：**key 只要随程序发出去，就一定能被扒出来**
（Python 打包后 `.pyc` 能反编译、进程内存能 dump、抓包能看见请求头）。所以本模块
**不做**异或 / base64 之类的"加密" —— 那只会给已经从这台机器上拿到文件的人增加
五分钟工作量，却让人误以为安全（反而更糟）。

真正管用的是两条，本模块只负责把这两条落实到位：

  1. **仓库里永远没有 key**：key 有个优先级链（见 `get_key()`）——
     用户自己填的（存在用户主目录的独立文件里）> 环境变量 > **构建时注入的内置 key**。
     内置那一份只有发布出去的 exe 里才有，源码仓库里是个占位文件、且被 .gitignore 挡住，
     所以审核人点开 Git URL 看到的是干净的仓库；
  2. **不主动泄露**：不写日志、不进报错、不进崩溃记录、不进临时文件、不进子进程参数。

关于第 1 条最后那半句"exe 里仍然带着一把"：这是**明知故为**，因为桌面程序里 key
藏不住 —— 反编译 `.pyc`、dump 内存、抓包都能拿到它。整个生态（Prism Launcher、
PCL、HMCL 这些）都是这么跑的，审核真正评估的是"这个应用的流量和行为可不可控、
有没有损害作者"，而不是"你能不能藏住 key"。所以这里的取舍是：**让它不值得被滥用**
（只在用户点搜索时请求、不落盘缓存、不用 CDN 直链绕开作者收益），同时给用户留一条
"用我自己的 key"的路 —— 内置那把万一被限流，用户还能自救。

第 2 条靠下面的 `redact()` / `scrub_obj()` 加上几个钩子实现，每个钩子都对应一个
真实的泄露途径：

  · `ui/mw_log.py` 的 `log()`        主界面日志 / 锁屏日志 / ~/.minecraft_migrate_last_log.txt
  · `helpers.trace_line` / `trace_exc`  点击轨迹 ~/.minecraft_migrate_clicks.log
  · Qt 子进程                        请求 JSON 是**落在临时目录里的明文**，所以那里必须
                                     没有 key；子进程要用 key 就自己 import 本模块读文件
                                     —— 那两个进程本来就在同一台机器、同一个用户下，
                                     不需要也不应该"传递"它
  · 请求本身                        key 走 `x-api-key` 请求头，**不放 URL**：URL 会进代理
                                     日志、浏览器历史、异常信息，请求头不会
  · 配置文件                        和 ~/.minecraft_migrate_config.json **分开存**：那份配置
                                     用户会贴进 issue 里求助（里面已经有模组清单和路径），
                                     不能再多一个 key
"""
import base64
import hashlib
import json
import os
import re
from pathlib import Path

# 密钥文件：默认在用户主目录。`MCTOOL_SECRET_FILE` 供验证脚本指到临时目录
# （和 utils/config.py 的 MCTOOL_CONFIG 一个套路），免得动到用户真实的 key。
SECRET_FILE = Path(os.environ.get("MCTOOL_SECRET_FILE")
                   or (Path.home() / ".minecraft_migrate_secret.json"))

# 开发 / 自动化用：环境变量（子进程会自动继承，跑验证脚本时不用碰真实文件）。
ENV_KEY = "MCTOOL_CURSEFORGE_KEY"
# 打包脚本用的环境变量名（只在构建那一刻读，见 打包_命令行.bat）
ENV_BUILD_KEY = "MCTOOL_CF_KEY"
FIELD = "curseforge_api_key"

# 构建时注入的"内置 Key"：
#   · 源码仓库里**永远没有真值** —— 那是个占位文件，由打包脚本在构建那一刻从本机
#     环境变量生成，且已被 .gitignore 挡住（审核人要点的 Git URL 上干干净净）；
#   · 发布出去的 exe 里才带着它，普通用户开箱即用；
#   · 正常 clone 下来开发时这个文件不存在，自动当"没有内置 Key"。
#   · **进程内只读一次**（import 那一刻就定了）：换了内置 Key 必须重启程序才生效 ——
#     实际路径是"重新打包 + 用户重开"，正常用不到这条。
# 为什么要有它：Key 在桌面程序里藏不住（.pyc 可反编译、内存可 dump），所以"让
# 它不可提取"是做不到的；能做的是"让它不值得被滥用"—— 详见本模块顶部说明。
#
# 存的是 BLOB（`_包()` 编出来的），不是明文：这样 `strings exe | findstr 你的key`
# 搜不到东西。**再强调一次这不是加密** —— 仓库是开源的，_解() 就在下面，
# 谁愿意读一眼代码就能还原。它挡的是"搜一下就完事"的那一档。
try:
    from utils._build_seed import BLOB as _BUILTIN_BLOB          # noqa: F401
except Exception:
    _BUILTIN_BLOB = ""

# 内置 key 的混淆用的固定盐（改它会让旧构建解不出来，别乱动）
_SEED_MASK = "MinecraftMigrateTool/builtin-seed-v1::"

MASK = "***"
# 兜底：key 被写进 header 文本或查询串时也要认出来（防的是"以后有人图省事这么写"）。
# 值太短或有中文的不动它 —— 免得把"请在设置里填 api_key: 你的密钥"这种说明文字抹了。
_RE_HEADER = re.compile(r"(?i)((?:x-api-key|api[-_]?key)\s*[:=]\s*)([^\s,;'\"&]+)")
_RE_QUERY = re.compile(r"(?i)((?:api[-_]?key|cf[-_]?key)=)([^\s&'\"]+)")


def _抹长值(m):
    v = m.group(2)
    return m.group(1) + (MASK if len(v) >= 6 and v.isascii() else v)


# 读了就缓存：log() 每行都要过 redact()，不能每次都去读盘。
# set_key / clear_key 会清掉它；子进程一直开着时换了 key 要重开窗口才生效 ——
# 这点写在文档里就够了，不值得为它加文件监视。
_缓存 = {"值": None, "来源": ""}


def clean(raw):
    """洗一遍用户粘进来的 key：去空白、去首尾引号、去 BOM。

    从网页/剪贴板复制常见这几种脏东西，留着会让请求 401，而用户完全看不出原因。
    """
    s = str(raw or "")
    for ch in ("\ufeff", "\u200b", "\u00a0"):     # BOM / 零宽空格 / 不间断空格
        s = s.replace(ch, "")
    # 顺序很重要：**先**去掉前后空白，再去引号 —— 反过来 `  "key"  ` 这种
    # （从网页上复制最常见的形态）首尾字符是空格和换行，引号根本去不掉。
    s = s.strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _解(blob):
    """把 `打包用_编码()` 编出来的 BLOB 还原成 key。

    ⚠ **这不是解密**：盐是常量、算法就这几行、仓库还是开源的。它只保证一件事 ——
    直接在 exe / .pyc 里搜那串 key 搜不到。别把任何"安全性"寄托在它上面，
    真正管用的是模块顶部那两条（仓库里没有 / 不主动泄露）。
    """
    try:
        raw = base64.b64decode(str(blob or "").strip())
        if not raw:
            return ""
        掩码 = hashlib.sha256(_SEED_MASK.encode("utf-8")).digest()
        return clean(bytes(b ^ 掩码[i % len(掩码)] for i, b in enumerate(raw))
                     .decode("utf-8"))
    except Exception:
        return ""


def 打包用_编码(key):
    """`_解()` 的逆运算 —— 构建时用，见仓库根的 `生成内置Key.py` 和 `打包_命令行.bat`。

    放在这里而不是构建脚本里，是为了让"编码/解码"永远只有一份实现：两边各写一份
    迟早会对不上（改了盐、改了算法，构建出来的包就再也解不出 key，而且很难查）。
    """
    原文 = clean(key).encode("utf-8")
    掩码 = hashlib.sha256(_SEED_MASK.encode("utf-8")).digest()
    return base64.b64encode(
        bytes(b ^ 掩码[i % len(掩码)] for i, b in enumerate(原文))).decode("ascii")


# 内置 key：从构建时生成的那份 BLOB 还原出来（文件不在就是空）
BUILTIN_KEY = _解(_BUILTIN_BLOB)


def _从文件读():
    """读用户自己填的那份（文件不存在或读坏了都算空）。"""
    try:
        data = json.loads(SECRET_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return clean(data.get(FIELD) or "")
    except Exception:
        pass
    return ""


def get_key(默认=""):
    """取当前可用的 key，优先级：

        用户自己填的（密钥文件） > 环境变量 > 构建时注入的内置 Key > 空

    用户自己填的排最前：那是他在这台机器上的**显式选择**，不能被别的渠道悄悄盖掉
    —— 否则"我明明在设置里填了 key，却总说无效"这种问题根本没法排查。
    环境变量排在内置之前，是为了开发/自动化时能拿自己的 key 跑发布版构建，
    不用为了试一下重新打包。
    """
    if _缓存["值"] is not None:
        return _缓存["值"] or 默认
    值, 来源 = _从文件读(), "用户设置"
    if not 值:
        值, 来源 = clean(os.environ.get(ENV_KEY) or ""), "环境变量"
    if not 值:
        值, 来源 = clean(BUILTIN_KEY), "内置"
    if not 值:
        来源 = ""
    _缓存.update({"值": 值, "来源": 来源})
    return 值 or 默认


def key_source():
    """这个 key 是从哪儿来的：用户设置 / 环境变量 / 内置 / 空字符串。

    设置页那行状态会显示它 —— 用户得能一眼看出"现在用的是我填的，还是程序内置的"。
    """
    get_key()                     # 触发一次解析（结果就在缓存里）
    return _缓存.get("来源") or ""


def has_key():
    """有没有配 key（没配时联网搜索照旧走 Modrinth，功能不受影响）。"""
    return bool(get_key())


def set_key(raw):
    """保存 key（空字符串 = 删除）。返回 True/False。

    权限：POSIX 下按 0600 创建（只有本人可读）；Windows 下 chmod 只动只读位、
    基本是 no-op，那边靠的是"文件在用户主目录里，别的用户本来就没权限进去"。
    """
    值 = clean(raw)
    if not 值:
        return clear_key()
    try:
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(SECRET_FILE), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({FIELD: 值}, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(SECRET_FILE, 0o600)
        except Exception:
            pass
        _缓存["值"] = None
        return True
    except Exception:
        return False


def clear_key():
    """删掉密钥文件。"""
    try:
        if SECRET_FILE.exists():
            SECRET_FILE.unlink()
        _缓存["值"] = None
        return True
    except Exception:
        return False


def mask(key=None):
    """给界面/日志看的形态：只留末 4 位（不够长就全遮掉）。"""
    值 = clean(key) if key is not None else get_key()
    if not 值:
        return "未设置"
    if len(值) <= 8:
        return "•" * 8
    return "•" * 8 + 值[-4:]


def fingerprint(key=None):
    """key 的短指纹（sha256 前 8 位）：排查"到底在用哪一个 key"时写进日志用。

    它反推不出 key 本身，所以可以放心落盘 —— 比"末 4 位"更适合排查
    （末 4 位可能撞车，指纹不会）。
    """
    值 = clean(key) if key is not None else get_key()
    if not 值:
        return ""
    return hashlib.sha256(值.encode("utf-8")).hexdigest()[:8]


def redact(text, *额外):
    """把文本里出现的 key 换成 ***。所有要上屏 / 落盘的文本都该过它。"""
    try:
        s = text if isinstance(text, str) else str(text)
    except Exception:
        return "<无法显示的文本>"
    if not s:
        return s
    for k in [get_key()] + [clean(x) for x in 额外 if x]:
        if k and len(k) >= 6 and k in s:
            s = s.replace(k, MASK)
    # 快路径：没有 "key" 字样就不必扫正则（log() 每行都走这里，省一点是一点）
    if "key" in s.lower():
        s = _RE_HEADER.sub(_抹长值, s)
        s = _RE_QUERY.sub(_抹长值, s)
    return s


def redact_exc(exc):
    """异常 → 脱敏后的单行说明（异常里最可能夹着 URL / 请求头）。"""
    try:
        return redact("%s: %s" % (type(exc).__name__, exc))
    except Exception:
        return "请求失败（细节已省略）"


def scrub_obj(值):
    """递归脱敏一个 JSON 可序列化的对象（Qt 子进程的请求/命令就是这个形状）。

    防的是"以后有人顺手把 key 塞进载荷里"：请求文件是落在临时目录的明文，
    一旦写进去就等于落盘了。这里兜住它。
    """
    if isinstance(值, str):
        return redact(值)
    if isinstance(值, dict):
        return {k: scrub_obj(v) for k, v in 值.items()}
    if isinstance(值, (list, tuple)):
        return [scrub_obj(v) for v in 值]
    return 值


def status_text():
    """设置页那一行状态（不含 key 本身，只说"从哪儿来的 + 末 4 位 + 指纹"）。"""
    from utils import i18n
    if not has_key():
        return i18n.tr("未设置 —— 联网搜索仍然走 Modrinth，功能不受影响")
    前缀 = {"用户设置": "你自己填的",
            "环境变量": "来自环境变量",
            "内置": "程序内置的（想用自己的配额就在上面填一个）"}.get(key_source(), "已保存")
    return i18n.trf("{prefix}：{masked}（指纹 {fp}）",
                    prefix=i18n.tr(前缀), masked=mask(), fp=fingerprint())


def where_text():
    """密钥文件的路径，给设置页显示用（用户能自己去看/删）。"""
    return str(SECRET_FILE)

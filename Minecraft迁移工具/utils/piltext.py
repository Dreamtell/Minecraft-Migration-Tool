# -*- coding: utf-8 -*-
"""用 PIL 画文字时，逐字符挑一支"真的有这个字形"的字体。

为什么需要这个
--------------
微软雅黑没有 emoji 字形（😀 🚀 ✅ 一个都没有），所以表情必须换 Segoe UI Emoji 画；
可反过来，Segoe UI Emoji 里又缺 ★ ☑ ⚙ 这类"看着像 emoji、其实是普通符号"的字，
而它们恰好落在 emoji 的码位区段里。于是无论"整串用一支字体"还是"按码位区间
一刀切"，都会有一批字符被画成豆腐块（用户报的就是这个：消息文本里的特殊字符
显示不正常）。

这里的做法是**按字形投票**：每个字符依次问候选字体"你这儿到底有没有这个字"，
第一个答"有"的胜出。缺字的判断办法是拿一个私用区字符（U+E000，正常字体里
都不会有）当基准 —— 画出来跟它一模一样的，就是这个字体的 .notdef 豆腐块。

缓存
----
1) 字号 → 字体对象（truetype 加载很贵）；
2) (字号, 字符) → 选中的字体（吐司每帧都在画同一批字，不缓存会显著拖慢）；
3) 字体 → 豆腐块的位图指纹。
"""
from __future__ import annotations

from PIL import ImageFont

# 字体链的三支：正文（中文）、符号、彩色表情
_正文字体候选 = (r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
                 r"C:\Windows\Fonts\msyhbd.ttc",    # 微软雅黑 Bold
                 r"C:\Windows\Fonts\simhei.ttf")    # 黑体（兜底）
_符号字体路径 = r"C:\Windows\Fonts\seguisym.ttf"    # Segoe UI Symbol：★ ☑ ⚙ ♂
_表情字体路径 = r"C:\Windows\Fonts\seguiemj.ttf"    # Segoe UI Emoji：彩色表情

_链缓存 = {}
_选字体缓存 = {}
_字形缓存 = {}
_豆腐缓存 = {}

# 表情符号的码位范围。只用来决定"先问哪一支"，不再直接决定用哪支。
_表情区段 = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF))
_表情单字 = {0x2139, 0x2328, 0x2705, 0x274C, 0x2764, 0x203C, 0xFE0F, 0x200D}


def 是表情(ch):
    """这个字符是不是"优先当表情画"（只影响挑字体的顺序，不影响有没有字形）。"""
    cp = ord(ch)
    if cp in _表情单字:
        return True
    for 低, 高 in _表情区段:
        if 低 <= cp <= 高:
            return True
    return False


def 字体链(号):
    """[正文, 符号, 彩色表情] 三支字体；取不到的位是 None。"""
    键 = int(号)
    链 = _链缓存.get(键)
    if 链 is not None:
        return 链
    链 = [None, None, None]
    for 名 in _正文字体候选:
        try:
            链[0] = ImageFont.truetype(名, 键)
            break
        except Exception:
            continue
    for i, 名 in ((1, _符号字体路径), (2, _表情字体路径)):
        try:
            链[i] = ImageFont.truetype(名, 键)
        except Exception:
            链[i] = None
    if 链[0] is None and 链[1] is None and 链[2] is None:
        try:
            链[0] = ImageFont.load_default()
        except Exception:
            pass
    _链缓存[键] = 链
    return 链


def 主字体(号):
    return 字体链(号)[0]


def 符号字体(号):
    return 字体链(号)[1]


def 表情字体(号):
    return 字体链(号)[2]


def _指纹(形, ch):
    """画一个字符，拿它的位图当指纹；画不出来返回 None。"""
    try:
        遮 = 形.getmask(ch, mode="L")
        return (遮.size, bytes(遮))
    except Exception:
        return None


def 有字形(形, ch):
    """这支字体里到底有没有 ch 的字形（没有就是豆腐块）。"""
    if 形 is None:
        return False
    if ch in " \u3000\t\n\r":
        return True                       # 空白字符画出来本来就是空的，别当成缺字
    键 = (id(形), ch)
    有 = _字形缓存.get(键)
    if 有 is not None:
        return 有
    基准 = _豆腐缓存.get(id(形))
    if 基准 is None:
        基准 = _指纹(形, "\ue000")        # 私用区：正常字体里都不会有这个字
        _豆腐缓存[id(形)] = 基准
    指纹 = _指纹(形, ch)
    有 = bool(指纹 is not None and 指纹 != 基准)
    _字形缓存[键] = 有
    return 有


def 选字体(ch, 号):
    """这个字符该用链里哪一支画（第一个"真有这个字形"的；都没有就用正文那支）。"""
    键 = (int(号), ch)
    形 = _选字体缓存.get(键)
    if 形 is not None:
        return 形
    链 = 字体链(号)
    序 = (2, 1, 0) if 是表情(ch) else (0, 1, 2)
    形 = None
    for i in 序:
        if 有字形(链[i], ch):
            形 = 链[i]
            break
    if 形 is None:
        形 = 链[0] or 链[1] or 链[2]
    _选字体缓存[键] = 形
    return 形


def 分段(文本, 号):
    """按"该用哪支字体"切段，相邻同字体的合并 → [(文本片段, 字体)]。"""
    段 = []
    for ch in 文本:
        形 = 选字体(ch, 号)
        if 段 and 段[-1][1] is 形:
            段[-1][0].append(ch)
        else:
            段.append([[ch], 形])
    return [("".join(t), f) for t, f in 段]


def 量文本(文本, 号):
    """按字体实际测宽（每段各用各的字体）。"""
    宽 = 0.0
    for t, 形 in 分段(文本, 号):
        try:
            宽 += 形.getlength(t) if 形 is not None else len(t) * 号
        except Exception:
            宽 += len(t) * 号
    return 宽


def 画文本(画, x, y, 文本, 色, 号, anchor="lm"):
    """逐段画（锚点跟 ImageDraw.text 一致），返回画完后的 x。"""
    彩 = 表情字体(号)
    for t, 形 in 分段(文本, 号):
        if 形 is None:
            continue
        try:
            if 形 is 彩:
                画.text((x, y), t, font=形, fill=色, anchor=anchor,
                        embedded_color=True)
            else:
                画.text((x, y), t, font=形, fill=色, anchor=anchor)
        except Exception:
            try:
                画.text((x, y), t, font=形, fill=色, anchor=anchor)
            except Exception:
                pass
        try:
            x += 形.getlength(t)
        except Exception:
            x += len(t) * 号
    return x

# -*- coding: utf-8 -*-
"""自己画的迷你国旗。

为什么不用 emoji：Windows 上没有任何一款字体会把 🇨🇳 画成旗子 ——
Segoe UI Emoji / Segoe UI Symbol 会退化成 "CN"/"US" 两个字母，
微软雅黑直接出豆腐块（实测见 `_dctest/flag_pil.png`、`flag_tk.png`）。
所以按像素自己画，尺寸固定成 18x12，贴到分段选择、语言选择框里。
"""
from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw

宽, 高 = 18, 12
超采样 = 4


def _星(笔, 中心, 半径, 色, 角=5):
    """五角星：外半径 / 内半径 0.382（黄金比例那种尖角）。"""
    cx, cy = 中心
    点 = []
    for i in range(角 * 2):
        r = 半径 if i % 2 == 0 else 半径 * 0.382
        a = -math.pi / 2 + i * math.pi / 角
        点.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    笔.polygon(点, fill=色)


def _中国(笔, w, h):
    笔.rectangle([0, 0, w - 1, h - 1], fill=(222, 41, 16))
    _星(笔, (w * 0.22, h * 0.30), h * 0.20, (255, 222, 0))
    for i, (fx, fy) in enumerate(((0.40, 0.12), (0.46, 0.26), (0.46, 0.42), (0.40, 0.56))):
        _星(笔, (w * fx, h * fy), h * 0.075, (255, 222, 0))


def _美国(笔, w, h):
    条 = 13
    for i in range(条):
        起 = round(i * h / 条)
        终 = round((i + 1) * h / 条) - 1
        笔.rectangle([0, 起, w - 1, 终],
                     fill=(178, 34, 52) if i % 2 == 0 else (255, 255, 255))
    笔.rectangle([0, 0, round(w * 0.42) - 1, round(h * 7 / 条) - 1], fill=(58, 59, 110))
    for 行 in range(3):
        for 列 in range(4):
            笔.ellipse([3 + 列 * 3.4, 2.2 + 行 * 3.0, 4.6 + 列 * 3.4, 3.8 + 行 * 3.0],
                       fill=(255, 255, 255))


def _英国(笔, w, h):
    笔.rectangle([0, 0, w - 1, h - 1], fill=(1, 33, 105))
    笔.line([0, 0, w - 1, h - 1], fill=(255, 255, 255), width=3)
    笔.line([0, h - 1, w - 1, 0], fill=(255, 255, 255), width=3)
    笔.line([0, 0, w - 1, h - 1], fill=(200, 16, 46), width=2)
    笔.line([0, h - 1, w - 1, 0], fill=(200, 16, 46), width=2)
    笔.rectangle([w / 2 - 1.6, 0, w / 2 + 1.6, h - 1], fill=(255, 255, 255))
    笔.rectangle([0, h / 2 - 2, w - 1, h / 2 + 2], fill=(255, 255, 255))
    笔.rectangle([w / 2 - 0.9, 0, w / 2 + 0.9, h - 1], fill=(200, 16, 46))
    笔.rectangle([0, h / 2 - 1.2, w - 1, h / 2 + 1.2], fill=(200, 16, 46))


_画法 = {"cn": _中国, "us": _美国, "gb": _英国}


@lru_cache(maxsize=16)
def 国旗图(种类, 宽_=宽, 高_=高, 圆角=2):
    """返回一张 RGBA 国旗小图（按超采样放大再缩回，边缘干净）。"""
    画 = _画法.get(str(种类).lower())
    图 = Image.new("RGBA", (宽_ * 超采样, 高_ * 超采样), (0, 0, 0, 0))
    笔 = ImageDraw.Draw(图)
    if 画 is None:
        return 图.resize((宽_, 高_), Image.LANCZOS)
    画(笔, 宽_ * 超采样, 高_ * 超采样)
    图 = 图.resize((宽_, 高_), Image.LANCZOS)
    if 圆角:
        罩 = Image.new("L", (宽_, 高_), 0)
        ImageDraw.Draw(罩).rounded_rectangle([0, 0, 宽_ - 1, 高_ - 1],
                                             radius=圆角, fill=255)
        图.putalpha(罩)
    return 图


def 国旗种类(语言):
    """语言代号 -> 国旗种类。"""
    语 = str(语言 or "").lower()
    return "cn" if 语.startswith("zh") else ("us" if 语.startswith("en") else "gb")

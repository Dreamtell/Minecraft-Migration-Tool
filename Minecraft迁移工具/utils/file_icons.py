# utils/file_icons.py
"""config 清单卡片的图标：文件夹 / 文件（带扩展名角标），纯 PIL 现画。

Tk 卡片（ui/card_list.py 的后台解码线程）和 Qt 放大查看（ui/qt_big_view.py）共用这一份，
所以这里**不 import tkinter 也不 import Qt**。

为什么不用 emoji / 系统图标：emoji 在 Canvas/QPainter 里的大小和基线各版本 Windows 不一致，
系统图标要走 shell32 + 句柄转换，打包后还容易拿不到；现画最稳，且深浅主题下都看得清。
"""
from functools import lru_cache

# 扩展名角标的底色（白字）。没列到的走默认靛蓝。
_EXT_COLORS = {
    "json": "#e09a1a", "json5": "#e09a1a",
    "toml": "#8d6e63",
    "cfg": "#546e7a", "conf": "#546e7a", "ini": "#546e7a", "properties": "#546e7a",
    "txt": "#78909c", "log": "#78909c",
    "yml": "#c2185b", "yaml": "#c2185b",
    "js": "#c79a00", "zs": "#7b1fa2",
    "xml": "#2e7d32", "snbt": "#00838f", "nbt": "#00838f", "dat": "#00838f",
}
_EXT_DEFAULT = "#5c6bc0"


_DIR_WORDS = ("文件夹", "folder")
_FILE_WORDS = ("文件", "file")


def config_entry_is_dir(entry: str, type_str: str = "") -> bool:
    """清单条目是不是文件夹。

    依据的先后：结尾带 / 或 \\（清单约定，`_mark_config_folders` 会补上）→
    扫描结果（磁盘上真实是什么）→ 都没有（还没扫完 / 源里已经没了）时按名字猜：
    没有扩展名的当文件夹（config 下的文件几乎都带 .json/.toml/.cfg 之类）。
    """
    e = str(entry or "").strip()
    if e.endswith("/") or e.endswith("\\"):
        return True
    t = str(type_str or "").strip().lower()
    if t in _DIR_WORDS:
        return True
    if t in _FILE_WORDS:
        return False
    return _ext_of(e) == ""


def _ext_of(name: str) -> str:
    n = str(name or "").strip().rstrip("/\\")
    n = n.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in n.strip("."):
        return ""
    return n.rsplit(".", 1)[-1].lower()


def _font(px: int):
    from PIL import ImageFont
    for f in ("arialbd.ttf", "msyhbd.ttc", "segoeuib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, px)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


@lru_cache(maxsize=64)
def config_icon(is_dir: bool, ext: str = "", size: int = 44, dark: bool = False):
    """画一张 size×size 的 RGBA 图标（PIL.Image）。按参数缓存，调用方别改返回的图（要改先 copy）。

    文件夹：琥珀色文件夹（后片深、前片浅，带标签页）。
    文件：带折角的纸张 + 底部扩展名角标（json/toml/cfg… 各一种颜色，最多 4 个字母）。
    """
    from PIL import Image, ImageDraw
    S = 4
    s = size * S
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def u(v):                      # 以 44px 设计稿为基准换算到当前尺寸
        return int(round(v * s / 44.0))

    if is_dir:
        back, front = ("#c98a12", "#f4b942") if not dark else ("#b07a14", "#e9aa35")
        edge = "#a8730d" if not dark else "#7a5510"
        # 后片 + 标签页
        d.rounded_rectangle([u(4), u(9), u(19), u(17)], radius=u(2.5), fill=back)
        d.rounded_rectangle([u(4), u(12), u(40), u(36)], radius=u(3.5), fill=back)
        # 前片（略矮，露出后片的上沿）
        d.rounded_rectangle([u(4), u(16), u(40), u(36)], radius=u(3.5), fill=front,
                            outline=edge, width=max(1, u(1)))
        # 前片上沿一道高光
        d.line([u(7), u(19), u(37), u(19)], fill=(255, 255, 255, 110), width=max(1, u(1.2)))
    else:
        paper = "#ffffff" if not dark else "#e9edf1"
        line_c = "#8f9aa5" if not dark else "#5f6b76"
        fold = "#d5dbe1" if not dark else "#b9c2cb"
        x0, y0, x1, y1, f = u(9), u(4), u(35), u(40), u(9)
        d.polygon([(x0 + u(2), y0), (x1 - f, y0), (x1, y0 + f), (x1, y1 - u(2)),
                   (x1 - u(2), y1), (x0 + u(2), y1), (x0, y1 - u(2)), (x0, y0 + u(2))],
                  fill=paper, outline=line_c)
        # 边线加粗：polygon 的 outline 只有 1px（超采样后等于 0.25px，看不见）
        w = max(1, u(1.3))
        d.line([(x0 + u(2), y0), (x1 - f, y0), (x1, y0 + f), (x1, y1 - u(2))], fill=line_c, width=w)
        d.line([(x1, y1 - u(2)), (x1 - u(2), y1), (x0 + u(2), y1), (x0, y1 - u(2)),
                (x0, y0 + u(2)), (x0 + u(2), y0)], fill=line_c, width=w)
        # 折角
        d.polygon([(x1 - f, y0), (x1 - f, y0 + f), (x1, y0 + f)], fill=fold)
        d.line([(x1 - f, y0), (x1 - f, y0 + f), (x1, y0 + f)], fill=line_c, width=w)
        # 两道"文字行"
        for yy in (u(14), u(19)):
            d.line([(x0 + u(5), yy), (x1 - u(5) - (f if yy < y0 + f + u(2) else 0), yy)],
                   fill=fold, width=max(1, u(1.6)))
        # 扩展名角标
        label = (ext or "").upper()[:4]
        if label:
            bc = _EXT_COLORS.get(ext.lower(), _EXT_DEFAULT)
            bx0, bx1 = u(3), u(41)
            by0, by1 = u(24), u(35)
            d.rounded_rectangle([bx0, by0, bx1, by1], radius=u(2.5), fill=bc)
            fnt = _font(u(8.5) if len(label) <= 3 else u(7.5))
            if fnt is not None:
                try:
                    l, t, r, b = d.textbbox((0, 0), label, font=fnt)
                    tw, th = r - l, b - t
                    d.text(((bx0 + bx1 - tw) / 2 - l, (by0 + by1 - th) / 2 - t), label,
                           fill="#ffffff", font=fnt)
                except Exception:
                    pass
    return im.resize((size, size), Image.LANCZOS)


def config_entry_icon(entry: str, type_str: str = "", size: int = 44, dark: bool = False):
    """按清单条目取图标：文件夹 / 文件（带扩展名）。画不出来返回 None。"""
    try:
        is_dir = config_entry_is_dir(entry, type_str)
        return config_icon(is_dir, "" if is_dir else _ext_of(entry), int(size), bool(dark))
    except Exception:
        return None

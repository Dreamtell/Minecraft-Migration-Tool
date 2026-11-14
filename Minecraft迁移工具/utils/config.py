# utils/config.py
import json
import os
from pathlib import Path
from utils.file_io import atomic_json

# 配置默认放用户主目录；`MCTOOL_CONFIG` 可以指到别处 ——
# 验证脚本跑真程序（比如测"开第二个实例"）时用它指向临时文件，
# 免得动到用户的真实配置（模组清单/存档名那些）。
#
# 注意这里是个**代理对象**而不是算好的 Path：以前写成
#     CONFIG_FILE = Path(os.environ.get("MCTOOL_CONFIG") or ...)
# 只在模块第一次 import 时算一次，之后谁再改环境变量都不生效；全量跑测试时
# "谁先 import 谁设的临时配置就定下来了"，于是单跑通过、全量跑失败。
# 代理每次访问都重新看环境变量，load/save 与各模块（`from utils.config import
# CONFIG_FILE` 拿到的也是同一个代理）都会跟着变。
_默认配置 = lambda: Path.home() / ".minecraft_migrate_config.json"


class _ConfigPath:
    """指向"当前"配置文件的路径代理（等价于 Path，但每次重新解析环境变量）。"""

    @property
    def _path(self):
        指定 = os.environ.get("MCTOOL_CONFIG")
        return Path(指定) if 指定 else _默认配置()

    def __fspath__(self):
        return str(self._path)

    def __str__(self):
        return str(self._path)

    def __repr__(self):
        return f"ConfigPath({self._path})"

    def __eq__(self, other):
        return str(self._path) == str(other)

    def __hash__(self):
        return hash(str(self._path))

    def __getattr__(self, name):
        return getattr(self._path, name)


CONFIG_FILE = _ConfigPath()


def load_raw_config():
    """直接读配置字典。

    app.py 需要在主界面建起来之前就知道"要不要放启动动画"，那时还没有
    MigrationGUI 实例，所以这里单独给一个不依赖任何 UI 的读取入口。
    """
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_raw_config(data):
    """把整份配置写回去（先读-改-写，别把别的键冲掉）。

    给独立的工具用（比如"双击间隙测试"要写自己测出来的间隔），不经过主界面实例。
    主界面自己保存时是整体重建字典的，所以新加的键也必须出现在 save_config() 里，
    否则用户下次在主界面里改任何设置都会把它抹掉。
    """
    try:
        merged = load_raw_config()
        merged.update(dict(data or {}))
        # 原子写：直接 write_text 的话，写一半崩溃会留下坏 JSON，
        # 而 load_raw_config 对坏文件是静默返回 {} —— 用户的全部设置就这么"没了"。
        atomic_json(CONFIG_FILE, merged)
        return True
    except Exception:
        return False

# utils/config.py
import json
from pathlib import Path

CONFIG_FILE = Path.home() / ".minecraft_migrate_config.json"


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

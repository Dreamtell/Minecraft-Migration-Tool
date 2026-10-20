# utils/config.py
import json
import os
from pathlib import Path

# 配置默认放用户主目录；`MCTOOL_CONFIG` 可以指到别处 ——
# 验证脚本跑真程序（比如测"开第二个实例"）时用它指向临时文件，
# 免得动到用户的真实配置（模组清单/存档名那些）。
CONFIG_FILE = Path(os.environ.get("MCTOOL_CONFIG")
                   or (Path.home() / ".minecraft_migrate_config.json"))


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
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".mctmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_FILE)
        return True
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False

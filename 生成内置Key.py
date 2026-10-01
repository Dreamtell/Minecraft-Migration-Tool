# -*- coding: utf-8 -*-
"""构建时把 MCTOOL_CF_KEY 写成 `Minecraft迁移工具/utils/_build_seed.py`。

由 `打包_命令行.bat` 调用（也可以手动跑）：

    set MCTOOL_CF_KEY=你的key
    python 生成内置Key.py

写进去的**不是明文** —— 是 `utils.secrets.打包用_编码()` 编出来的 BLOB（XOR + base64），
所以拿 `strings` 在打出来的 exe 里搜 key 是搜不到的。

⚠ 这不是加密：算法和盐都写在 utils/secrets.py 里、仓库也是公开的，读一眼代码就能还原。
它挡的只是"搜一下就完事"的那一档，别把安全性寄托在它上面（详见 secrets.py 顶部）。

没设 MCTOOL_CF_KEY 时会把旧文件删掉，也就是构建出一个"没有内置 key"的包 ——
那种包要用户在设置里自己填 key 才能用 CurseForge。
"""
import os
import sys
from pathlib import Path

根 = Path(__file__).resolve().parent
项目 = 根 / "Minecraft迁移工具"
种子文件 = 项目 / "utils" / "_build_seed.py"

sys.path.insert(0, str(项目))

头部 = (
    "# 构建时自动生成，**不要提交**（已在 .gitignore 里）。\n"
    "# 内容是内置 CurseForge API Key 的混淆形态，解码见 utils/secrets.py 的 _解()。\n"
    "# 生成者：仓库根的 生成内置Key.py（打包_命令行.bat 会调它）；源头：环境变量 MCTOOL_CF_KEY。\n"
)


def main():
    key = (os.environ.get("MCTOOL_CF_KEY") or "").strip()
    if not key:
        if 种子文件.exists():
            种子文件.unlink()
            print("[key] MCTOOL_CF_KEY 没设 —— 已删掉旧的 _build_seed.py")
        else:
            print("[key] MCTOOL_CF_KEY 没设 —— 这次构建不含内置 key")
        return 0
    from utils import secrets                     # 编码/解码全项目只有这一份实现
    try:
        blob = secrets.打包用_编码(key)
    except Exception as e:
        print("[key] 编码失败：%s" % (e,))
        return 1
    # 自检：编出来必须能解回原样，否则宁可不出包也别出一个"key 永远无效"的包
    if not blob or secrets._解(blob) != secrets.clean(key):
        print("[key] 自检失败：编码/解码对不上，没有写文件")
        return 1
    种子文件.write_text(头部 + 'BLOB = "%s"\n' % blob, encoding="utf-8")
    print("[key] 内置 key 已写入 utils/%s（指纹 %s，长度 %d，明文搜不到）"
          % (种子文件.name, secrets.fingerprint(key), len(secrets.clean(key))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

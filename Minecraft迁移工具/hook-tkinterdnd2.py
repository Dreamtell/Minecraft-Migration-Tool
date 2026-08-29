# hook-tkinterdnd2.py
# ---------------------------------------------------------------------------
# PyInstaller hook：让 tkinterdnd2 的"非 Python 资源"（tkdnd 动态库 + tcl 脚本）
# 被正确收进打包产物。
#
# ⚠️ 文件名必须用连字符 hook-tkinterdnd2.py，PyInstaller 只识别 hook-*.py
#    （imphook.py: glob.glob(hook_dir/'hook-*.py')）。用下划线 hook_tkinterdnd2.py
#    不会被当作 tkinterdnd2 的 hook，导致打包后拖拽失效。
#
# tkinterdnd2 除 .py 外，还在包目录下带有平台相关的 tkdnd\win-x64\ 等子目录，
# 内含 libtkdnd2.10.1.dll 和多个 *.tcl 脚本。运行时 TkinterDnD.py 通过
#   os.path.join(os.path.dirname(__file__), 'tkdnd', <平台目录>)
# 定位并加载它们。PyInstaller 不会自动收这些数据文件。
#
# 本 hook 直接向 PyInstaller 注册 "收集该包的全部数据文件"，
# 目标路径 tkinterdnd2\tkdnd\... 与运行时解析完全一致，无需手工指定。
#
# 用 auto-py-to-exe / pyinstaller 时指定：
#   --additional-hooks-dir <本文件所在目录>
# 即可自动生效。等价于命令行 --collect-data tkinterdnd2。
# ---------------------------------------------------------------------------
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files('tkinterdnd2')

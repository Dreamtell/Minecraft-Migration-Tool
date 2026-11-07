# ui/qt_main/__main__.py
"""Qt主界面的独立预览入口。

用法(在 Minecraft迁移工具/ 目录下):
    python -m ui.qt_main

这是第一阶段的独立预览:不碰Tk版启动流程、不开托盘/闪屏/单实例锁,
纯粹把Qt主窗口画出来验证布局与core调用。完整集成(app.py 的
main_window_backend 分支)在骨架稳定后再接。
"""
import sys
from pathlib import Path

# 让 `python -m ui.qt_main` 能在项目根目录直接跑:把项目根加进路径
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# _qt(PySide6 便携目录)
_QT_DIR = _ROOT / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))


def main():
    try:
        from PySide6 import QtWidgets
    except ImportError:
        print("错误: 找不到 PySide6。")
        print('请运行: pip install "PySide6==6.11.2" --target _qt')
        return 1

    from ui.qt_main.window import QtMainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)   # 预览模式:关窗即退出
    app.setFont(QtWidgets.QApplication.font())

    win = QtMainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

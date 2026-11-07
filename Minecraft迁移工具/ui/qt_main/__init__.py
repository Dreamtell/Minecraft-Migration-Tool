# ui/qt_main/__init__.py
"""Qt版主界面(独立进程顶层入口)。

与Tk版(ui/main_window.py + 12个mixin)平行,两套通过配置 main_window_backend: tk/qt 切换。
Tk版保持不动,Qt版从头实现所有控件但复用 core/ 业务层。

架构:
  - 独立顶层进程(不是Tk主进程的子进程)
  - 直接在进程内用 qt_big_view.QtBigView、qt_diff_view(不再需要子进程)
  - 托盘/闪屏复用现有组件;迁移进度用Qt实现

模块:
  - window.py: QMainWindow + 顶层布局组装
  - paths.py:  路径区
  - lists.py:  三个清单页
  - migration.py: 迁移执行/历史/回滚
  - log.py:    执行日志区
"""
from .window import QtMainWindow

__all__ = ["QtMainWindow"]

# ui/qt_main/settings.py
"""Qt 设置对话框:主题/语言/默认迁移选项/历史份数/启动动画。

只负责「收集用户选择」并回传一个普通 dict(values())。真正的落地(改主题、写配置、
设历史份数、刷新界面)由主窗口的 _apply_settings 负责,保持单一写入口。
"""
import sys
from pathlib import Path

# 与 window.py 一致:确保 _qt 在路径里,便于独立导入(测试/工具进程)
_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtWidgets  # noqa: E402

from utils import i18n  # noqa: E402
from utils.i18n import tr  # noqa: E402
from utils import history_store  # noqa: E402


def ask_close_action(parent):
    """点 ❌ 时问一次:收进托盘 / 直接退出 / 取消。返回 (action|None, remember)。

    对齐 Tk 版 ui/dialogs.ask_close_action 的语义:action 为 "tray"/"exit",取消返回 None。
    """
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(tr("关闭窗口"))
    v = QtWidgets.QVBoxLayout(dlg)
    v.addWidget(QtWidgets.QLabel(tr("要把程序收进系统托盘继续运行,还是直接退出?")))
    remember = QtWidgets.QCheckBox(tr("记住我的选择,以后不再询问"))
    v.addWidget(remember)
    row = QtWidgets.QHBoxLayout()
    btn_tray = QtWidgets.QPushButton(tr("收进托盘继续运行"))
    btn_exit = QtWidgets.QPushButton(tr("直接退出"))
    btn_cancel = QtWidgets.QPushButton(tr("取消"))
    for b in (btn_tray, btn_exit, btn_cancel):
        row.addWidget(b)
    v.addLayout(row)
    result = {"action": None}

    def pick(action):
        result["action"] = action
        dlg.accept()

    btn_tray.clicked.connect(lambda: pick("tray"))
    btn_exit.clicked.connect(lambda: pick("exit"))
    btn_cancel.clicked.connect(dlg.reject)
    btn_tray.setDefault(True)
    dlg.exec()
    return result["action"], remember.isChecked()


class SettingsDialog(QtWidgets.QDialog):
    """设置对话框。读入当前 config,OK 后用 values() 取回选择。"""

    def __init__(self, parent, config):
        super().__init__(parent)
        self.setWindowTitle(tr("设置"))
        self.setMinimumWidth(380)
        cfg = dict(config or {})

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(16, 16, 16, 8)
        form.setSpacing(10)

        # 主题
        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItem(tr("浅色"), "light")
        self.theme_combo.addItem(tr("深色"), "dark")
        theme_val = str(cfg.get("theme", "light") or "light")
        self._select_by_data(self.theme_combo, theme_val, 0)
        form.addRow(tr("主题"), self.theme_combo)

        # 语言(重启生效)
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItem(tr("中文"), "zh")
        self.lang_combo.addItem(tr("English"), "en")
        self._select_by_data(self.lang_combo, i18n.language(), 0)
        form.addRow(tr("语言"), self.lang_combo)
        lang_note = QtWidgets.QLabel(tr("※ 语言切换在下次启动后完全生效"))
        lang_note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", lang_note)

        # 其它文件同名冲突策略
        self.conflict_combo = QtWidgets.QComboBox()
        self.conflict_combo.addItem(tr("覆盖(先备份)"), "overwrite")
        self.conflict_combo.addItem(tr("跳过"), "skip")
        conflict_val = str(cfg.get("extra_conflict", "overwrite") or "overwrite")
        if conflict_val not in ("overwrite", "skip"):
            conflict_val = "overwrite"
        self._select_by_data(self.conflict_combo, conflict_val, 0)
        form.addRow(tr("其它文件冲突"), self.conflict_combo)

        # 历史保存份数
        self.history_spin = QtWidgets.QSpinBox()
        self.history_spin.setRange(history_store.MIN_LIMIT, history_store.MAX_LIMIT)
        self.history_spin.setValue(history_store.get_limit())
        form.addRow(tr("历史保存份数"), self.history_spin)

        # 默认开关
        self.dry_run_check = QtWidgets.QCheckBox(tr("默认开启模拟运行"))
        self.dry_run_check.setChecked(bool(cfg.get("dry_run", True)))
        form.addRow("", self.dry_run_check)

        self.overwrite_check = QtWidgets.QCheckBox(tr("默认覆盖已存在的模组"))
        self.overwrite_check.setChecked(bool(cfg.get("overwrite", False)))
        form.addRow("", self.overwrite_check)

        self.confirm_check = QtWidgets.QCheckBox(tr("正式迁移前再确认一次"))
        self.confirm_check.setChecked(bool(cfg.get("confirm_migrate", True)))
        form.addRow("", self.confirm_check)

        # 开始前倒计时(和 Tk 版同一个键,范围 1~10)
        self.countdown_spin = QtWidgets.QSpinBox()
        self.countdown_spin.setRange(1, 10)
        self.countdown_spin.setSuffix(tr(" 秒"))
        try:
            self.countdown_spin.setValue(max(1, min(10, int(cfg.get("start_countdown", 5)))))
        except Exception:
            self.countdown_spin.setValue(5)
        form.addRow(tr("开始前倒计时"), self.countdown_spin)

        self.splash_check = QtWidgets.QCheckBox(tr("启动时显示动画"))
        self.splash_check.setChecked(bool(cfg.get("splash", True)))
        form.addRow("", self.splash_check)

        # 网络浮标(与 Tk 版同键:net_float_on / net_float_size)
        self.float_check = QtWidgets.QCheckBox(tr("显示网络延迟浮标"))
        self.float_check.setChecked(bool(cfg.get("net_float_on", False)))
        form.addRow("", self.float_check)
        self.float_size = QtWidgets.QSpinBox()
        self.float_size.setRange(40, 96)
        self.float_size.setSuffix(" px")
        try:
            self.float_size.setValue(max(40, min(96, int(cfg.get("net_float_size", 56)))))
        except Exception:
            self.float_size.setValue(56)
        form.addRow(tr("浮标大小"), self.float_size)
        self.float_dock_check = QtWidgets.QCheckBox(tr("贴边时收纳浮标（只露一条边，鼠标移上去展开）"))
        self.float_dock_check.setChecked(bool(cfg.get("net_float_dock", True)))
        form.addRow("", self.float_dock_check)
        self.float_fx_check = QtWidgets.QCheckBox(tr("贴边时显示边缘特效（绿色光带 + 粒子）"))
        self.float_fx_check.setChecked(bool(cfg.get("float_edge_fx", True)))
        form.addRow("", self.float_fx_check)

        # 「开始迁移」按钮翻转手感(与 Tk 版同键 start_btn_feel)
        self.feel_combo = QtWidgets.QComboBox()
        for code, label in (("crisp", "干脆"), ("standard", "标准"), ("heavy", "沉")):
            self.feel_combo.addItem(tr(label), code)
        self._select_by_data(self.feel_combo, str(cfg.get("start_btn_feel", "standard")), 1)
        form.addRow(tr("开始迁移按钮手感："), self.feel_combo)

        # 右下角消息卡片总开关(与 Tk 版同键 toast_on;关掉后消息照样进消息中心)
        self.toast_check = QtWidgets.QCheckBox(tr("显示消息提示（右下角弹出的小卡片）"))
        self.toast_check.setChecked(bool(cfg.get("toast_on", True)))
        form.addRow("", self.toast_check)

        # 关闭窗口时的行为(ask/tray/exit,与 Tk 版同一个配置键)
        self.close_combo = QtWidgets.QComboBox()
        self.close_combo.addItem(tr("每次询问"), "ask")
        self.close_combo.addItem(tr("收进系统托盘"), "tray")
        self.close_combo.addItem(tr("直接退出"), "exit")
        close_val = str(cfg.get("close_action", "ask") or "ask")
        self._select_by_data(self.close_combo, close_val, 0)
        form.addRow(tr("关闭窗口时"), self.close_combo)

        # 主界面实现(重启后生效)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItem(tr("PySide6 主界面(当前)"), "qt")
        self.backend_combo.addItem(tr("经典 Tk 主界面"), "tk")
        self._select_by_data(self.backend_combo,
                             str(cfg.get("main_window_backend", "qt") or "qt"), 0)
        form.addRow(tr("主界面实现"), self.backend_combo)
        backend_note = QtWidgets.QLabel(tr("※ 切换主界面实现在下次启动后生效"))
        backend_note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", backend_note)

        # 按钮
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QtWidgets.QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

    @staticmethod
    def _select_by_data(combo, data, fallback_index):
        """按 userData 选中下拉项;找不到则退回 fallback_index。"""
        idx = combo.findData(data)
        combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

    def values(self):
        """返回用户选择(普通 dict,键名与 config 对齐)。"""
        return {
            "theme": self.theme_combo.currentData(),
            "language": self.lang_combo.currentData(),
            "extra_conflict": self.conflict_combo.currentData(),
            "history_limit": int(self.history_spin.value()),
            "dry_run": self.dry_run_check.isChecked(),
            "overwrite": self.overwrite_check.isChecked(),
            "splash": self.splash_check.isChecked(),
            "confirm_migrate": self.confirm_check.isChecked(),
            "start_countdown": int(self.countdown_spin.value()),
            "net_float_on": self.float_check.isChecked(),
            "net_float_size": int(self.float_size.value()),
            "net_float_dock": self.float_dock_check.isChecked(),
            "float_edge_fx": self.float_fx_check.isChecked(),
            "start_btn_feel": self.feel_combo.currentData(),
            "toast_on": self.toast_check.isChecked(),
            "close_action": self.close_combo.currentData(),
            "main_window_backend": self.backend_combo.currentData(),
        }

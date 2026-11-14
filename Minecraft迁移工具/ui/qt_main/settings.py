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

from PySide6 import QtCore, QtWidgets  # noqa: E402

from utils import i18n  # noqa: E402
from ui.qt_win import fit_window
from utils.i18n import tr  # noqa: E402
from utils import history_store  # noqa: E402
from utils import fx  # noqa: E402
from ui.qt_main.star_slider import StarSliderCard  # noqa: E402


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
        # 表单在滚动区里（见文件末尾），所以最小高度可以放低到 560 而不会切内容
        fit_window(self, 480, 560, 520, 860)
        cfg = dict(config or {})
        self._cfg = cfg                      # 试听结束后据此还原音效开关

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

        # 特效强度：一个 4 档的星空滑块，统管启动动画 / 浮标粒子 / 翻转手感 / 消息卡片动画。
        # 档位值落成 0、1/3、2/3、1（左=关闭，右=全开），和 Tk 版的四段选择一一对应。
        self._fx_level = fx.normalize(cfg.get("fx_level"))
        self.fx_card = StarSliderCard(
            tr("特效强度"), tr(fx.LABELS[self._fx_level]), self,
            dark=(theme_val == "dark"), value=self._level_to_value(self._fx_level),
            stops=len(fx.LEVELS))
        self.fx_card.slider().valueChanged.connect(self._on_fx_value)
        form.addRow(self.fx_card)
        fx_note = QtWidgets.QLabel(tr("※ 关掉后没有启动动画、边缘粒子和消息卡片动画，按钮翻得最干脆"))
        fx_note.setWordWrap(True)
        fx_note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", fx_note)

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

        # 迁移行为(与 Tk 版同键,两边切换时设置互通)
        self.rename_check = QtWidgets.QCheckBox(
            tr("复制过去的模组加标记前缀（方便在目标 mods 里一眼认出）"))
        self.rename_check.setChecked(bool(cfg.get("rename_migrated_mods", False)))
        form.addRow("", self.rename_check)
        self.marker_combo = QtWidgets.QComboBox()
        self.marker_combo.setEditable(False)
        for m in ("★", "☆", "▶", "◆", "●", "✦", "✚", "【新】", "NEW_"):
            self.marker_combo.addItem(m, m)
        self._select_by_data(self.marker_combo, str(cfg.get("rename_marker", "★") or "★"), 0)
        self.marker_combo.setEnabled(self.rename_check.isChecked())
        self.rename_check.toggled.connect(self.marker_combo.setEnabled)
        form.addRow(tr("标记符号"), self.marker_combo)
        self.silent_check = QtWidgets.QCheckBox(
            tr("后台静默执行任务（不弹进度/结果窗口，完成后系统通知）"))
        self.silent_check.setChecked(bool(cfg.get("silent_background", False)))
        form.addRow("", self.silent_check)


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
        # 浮标阴影(与 Tk 版同键 net_float_shadow)
        self.float_shadow_check = QtWidgets.QCheckBox(tr("浮标带阴影"))
        self.float_shadow_check.setChecked(bool(cfg.get("net_float_shadow", True)))
        form.addRow("", self.float_shadow_check)


        # 右下角消息卡片总开关(与 Tk 版同键 toast_on;关掉后消息照样进消息中心)
        self.toast_check = QtWidgets.QCheckBox(tr("显示消息提示（右下角弹出的小卡片）"))
        self.toast_check.setChecked(bool(cfg.get("toast_on", True)))
        form.addRow("", self.toast_check)
        # 点消息中心以外的地方就收起它(与 Tk 版同键 msg_click_outside_close,默认关)
        self.msg_outside_check = QtWidgets.QCheckBox(tr("点消息中心以外就收回"))
        self.msg_outside_check.setChecked(bool(cfg.get("msg_click_outside_close", False)))
        form.addRow("", self.msg_outside_check)
        # 主界面编辑:点空白处是否让清单失焦(与 Tk 版同键 blank_exit_edit,默认开)
        self.blank_exit_check = QtWidgets.QCheckBox(tr("点空白处取消清单文本框焦点"))
        self.blank_exit_check.setToolTip(tr("关闭后只能用编辑开关自己关"))
        self.blank_exit_check.setChecked(bool(cfg.get("blank_exit_edit", True)))
        form.addRow("", self.blank_exit_check)
        # 按钮音效(与 Tk 版同键 sfx_on;音源路径 sfx_hover / sfx_press 沿用配置,默认用随包的合成音)
        sfx_row = QtWidgets.QHBoxLayout()
        self.sfx_check = QtWidgets.QCheckBox(tr("🔊 按钮音效（悬停 / 按下）"))
        self.sfx_check.setChecked(bool(cfg.get("sfx_on", False)))
        sfx_row.addWidget(self.sfx_check)
        self.sfx_test_btn = QtWidgets.QPushButton(tr("▶ 试听"))
        self.sfx_test_btn.clicked.connect(self._test_sfx)
        sfx_row.addWidget(self.sfx_test_btn)
        sfx_row.addStretch()
        form.addRow("", sfx_row)
        self._sfx_hover = str(cfg.get("sfx_hover") or "")
        self._sfx_press = str(cfg.get("sfx_press") or "")

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

        # 设置项多，屏幕矮（或缩放 150%）时一屏放不下：表单放进滚动区，
        # 按钮固定在底部 —— 这样窗口哪怕压到最小尺寸也不会把下面几项切掉。
        holder = QtWidgets.QWidget()
        holder.setLayout(form)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(scroll, 1)
        bar = QtWidgets.QWidget()
        bar_lay = QtWidgets.QHBoxLayout(bar)
        bar_lay.setContentsMargins(16, 8, 16, 12)
        bar_lay.addStretch(1)
        bar_lay.addWidget(buttons)
        root.addWidget(bar)

    @staticmethod
    def _level_to_value(level):
        """档位 → 滑块位置（0 / 1⁄3 / 2⁄3 / 1）。"""
        try:
            return fx.LEVELS.index(fx.normalize(level)) / 3.0
        except ValueError:
            return 2.0 / 3.0

    def _fx_value(self):
        """滑块位置 → 档位（就近取档）。"""
        v = float(self.fx_card.value())
        idx = int(round(max(0.0, min(1.0, v)) * 3))
        return fx.LEVELS[max(0, min(3, idx))]

    def _on_fx_value(self, _v):
        self.fx_card.set_sub(tr(fx.LABELS[self._fx_value()]))

    @staticmethod
    def _select_by_data(combo, data, fallback_index):
        """按 userData 选中下拉项;找不到则退回 fallback_index。"""
        idx = combo.findData(data)
        combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

    def _test_sfx(self):
        """先按当前路径播一下,让用户当场判断音量与音色(不改开关本身,取消设置窗就还原)。"""
        from utils import sfx
        sfx.设置(enabled=True, hover=self._sfx_hover, press=self._sfx_press)
        sfx.预热()
        if not sfx.可用():
            QtWidgets.QMessageBox.warning(self, tr("提示"), tr("⚠ 音效文件不存在"))
            sfx.设置(enabled=self._sfx_was_on())
            return
        sfx.play("hover")
        QtCore.QTimer.singleShot(320, lambda: sfx.play("press"))
        QtCore.QTimer.singleShot(900, lambda: sfx.设置(enabled=self._sfx_was_on()))

    def _sfx_was_on(self):
        return bool(self._cfg.get("sfx_on", False))

    def values(self):
        """返回用户选择(普通 dict,键名与 config 对齐)。"""
        level = self._fx_value()
        d = fx.derived(level)
        return {
            "theme": self.theme_combo.currentData(),
            "fx_level": level,
            "splash": d["splash"],
            "float_edge_fx": d["float_edge_fx"],
            "start_btn_feel": d["start_btn_feel"],
            "language": self.lang_combo.currentData(),
            "extra_conflict": self.conflict_combo.currentData(),
            "history_limit": int(self.history_spin.value()),
            "dry_run": self.dry_run_check.isChecked(),
            "overwrite": self.overwrite_check.isChecked(),
            "confirm_migrate": self.confirm_check.isChecked(),
            "start_countdown": int(self.countdown_spin.value()),
            "rename_migrated_mods": self.rename_check.isChecked(),
            "rename_marker": self.marker_combo.currentData(),
            "silent_background": self.silent_check.isChecked(),
            "net_float_on": self.float_check.isChecked(),
            "net_float_size": int(self.float_size.value()),
            "net_float_dock": self.float_dock_check.isChecked(),
            "net_float_shadow": self.float_shadow_check.isChecked(),
            "msg_click_outside_close": self.msg_outside_check.isChecked(),
            "blank_exit_edit": self.blank_exit_check.isChecked(),
            "sfx_on": self.sfx_check.isChecked(),
            "toast_on": self.toast_check.isChecked(),
            "close_action": self.close_combo.currentData(),
            "main_window_backend": self.backend_combo.currentData(),
        }

# ui/qt_main/settings.py
"""Qt 设置对话框:主题/语言/默认迁移选项/历史份数/启动动画。

只负责「收集用户选择」并回传一个普通 dict(values())。真正的落地(改主题、写配置、
设历史份数、刷新界面)由主窗口的 _apply_settings 负责,保持单一写入口。
"""
import sys
import threading
from pathlib import Path

# 与 window.py 一致:确保 _qt 在路径里,便于独立导入(测试/工具进程)
_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtCore, QtWidgets  # noqa: E402

from utils import i18n  # noqa: E402
from ui.qt_win import fit_window
from utils.i18n import tr, trp  # noqa: E402
from utils import history_store  # noqa: E402
from utils import fx  # noqa: E402
from utils import secrets  # noqa: E402
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

    _cf_test_done = QtCore.Signal(bool, str)        # 后台测试线程 → 主线程
    # 回滚类别的固定顺序,和 core.migrator._RESTORE_PARTS 一致(测试里有断言钉住)
    _ROLLBACK_ORDER = ("mods", "config", "saves", "files", "extras")

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

        # 回滚时默认还原哪几类(与 Tk 版同键 rollback_parts;回滚对话框里勾「记住我的选择」会覆盖它)
        form.addRow(QtWidgets.QLabel(tr("回滚时默认还原：")))
        rollback_items = (("mods", tr("模组（mods/）")),
                          ("config", tr("配置（config/）")),
                          ("saves", tr("存档（saves/）")),
                          ("files", tr("根目录文件（options.txt 等）")),
                          ("extras", tr("「其它文件」清单里的条目")))
        saved_parts = cfg.get("rollback_parts")
        if isinstance(saved_parts, (list, tuple)) and saved_parts:
            saved_parts = set(saved_parts)
        else:
            saved_parts = {k for k, _ in rollback_items}          # 没存过 = 全勾
        self._rollback_checks = {}
        for part_key, part_text in rollback_items:
            cb = QtWidgets.QCheckBox(part_text)
            cb.setChecked(part_key in saved_parts)
            cb.toggled.connect(lambda on, k=part_key: self._on_rollback_toggled(k, on))
            self._rollback_checks[part_key] = cb
            form.addRow("", cb)
        rollback_note = QtWidgets.QLabel(
            tr("注：回滚对话框里勾「记住我的选择」会用那一次的选择覆盖这里。"))
        rollback_note.setWordWrap(True)
        rollback_note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", rollback_note)
        if not any(cb.isChecked() for cb in self._rollback_checks.values()):
            self._rollback_checks["mods"].setChecked(True)        # 脏配置(类别名全不认识)至少留 mods

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
        # 平滑滚动速度(与 Tk 版同键 scroll_speed:1 最慢 … 5 最快,3 = 原速)
        self.scroll_speed_spin = QtWidgets.QSpinBox()
        self.scroll_speed_spin.setRange(1, 5)
        self.scroll_speed_spin.setToolTip(tr("档（1 最慢 – 5 最快，3 = 原速）"))
        try:
            self.scroll_speed_spin.setValue(max(1, min(5, int(cfg.get("scroll_speed", 3)))))
        except Exception:
            self.scroll_speed_spin.setValue(3)
        form.addRow(tr("平滑滚动速度"), self.scroll_speed_spin)

        # 联网获取真实分类(与 Tk 版同键 online_tags,默认关;下次打开放大查看时生效)
        self.tags_check = QtWidgets.QCheckBox(tr("联网获取真实分类（Modrinth；默认关闭）"))
        self.tags_check.setChecked(bool(cfg.get("online_tags", False)))
        form.addRow("", self.tags_check)
        tags_note = QtWidgets.QLabel(tr(
            "关闭时按关键词推测（标注“推测”的就是它）。开启后扫描会在后台联网查询，"
            "结果缓存到本地；断网或匹配不到时自动沿用推测结果。"))
        tags_note.setWordWrap(True)
        tags_note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", tags_note)
        self.tag_cache_label = QtWidgets.QLabel()
        self.tag_cache_label.setWordWrap(True)
        self.tag_cache_label.setStyleSheet("color: #888; font-size: 11px;")
        self._update_tag_cache_label()
        form.addRow("", self.tag_cache_label)

        # CurseForge API Key(可选)。和其它项不同:点按钮就生效(同 Tk 版),不等「确定」,
        # 而且只写密钥文件(~/.minecraft_migrate_secret.json),不进主配置、不进日志。
        form.addRow(QtWidgets.QLabel(tr("CurseForge API Key（可选）")))
        key_row = QtWidgets.QHBoxLayout()
        self.cf_key_edit = QtWidgets.QLineEdit()
        self.cf_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        key_row.addWidget(self.cf_key_edit, 1)
        self.cf_save_btn = QtWidgets.QPushButton(tr("保存"))
        self.cf_test_btn = QtWidgets.QPushButton(tr("测试"))
        self.cf_clear_btn = QtWidgets.QPushButton(tr("清除"))
        for b in (self.cf_save_btn, self.cf_test_btn, self.cf_clear_btn):
            b.setAutoDefault(False)          # 在输入框里回车别顺手触发「确定」
            key_row.addWidget(b)
        self.cf_save_btn.clicked.connect(self._save_cf_key)
        self.cf_test_btn.clicked.connect(self._test_cf_key)
        self.cf_clear_btn.clicked.connect(self._clear_cf_key)
        form.addRow(key_row)
        self.cf_status_label = QtWidgets.QLabel()
        self.cf_status_label.setWordWrap(True)
        self.cf_status_label.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow(self.cf_status_label)
        self._cf_testing = False
        self._cf_test_done.connect(self._on_cf_test_done)
        self._update_cf_status()
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

    def _on_rollback_toggled(self, key, on):
        """回滚默认还原至少留一类(全不勾回滚就什么都不做):取消最后一个时把它勾回去。"""
        if on or any(cb.isChecked() for cb in self._rollback_checks.values()):
            return
        cb = self._rollback_checks[key]
        cb.blockSignals(True)
        cb.setChecked(True)
        cb.blockSignals(False)

    def _update_tag_cache_label(self):
        """显示本地分类缓存条数,让用户知道「查过一次就不会再联网」。"""
        try:
            from core.mod_search import tag_cache_size, TAG_CACHE_FILE
            self.tag_cache_label.setText(
                tr("本地分类缓存：") + str(tag_cache_size()) + tr(" 条（")
                + str(TAG_CACHE_FILE) + tr("，删掉它会重新联网查）"))
        except Exception:
            self.tag_cache_label.setText("")

    # ---- CurseForge API Key:界面在这里,存/取/脱敏全在 utils/secrets.py ----
    def _cf_toast(self, text, level="info"):
        parent = self.parent()
        if parent is not None and hasattr(parent, "toast"):
            try:
                parent.toast(text, level)
            except Exception:
                pass

    def _cf_log(self, text, level="INFO"):
        parent = self.parent()
        if parent is not None and hasattr(parent, "log"):
            try:
                parent.log(text, level)
            except Exception:
                pass

    def _update_cf_status(self):
        """Key 状态那一行:只显示「来源 + 末 4 位 + 指纹」,不显示 key 本身。"""
        try:
            text = secrets.status_text()
        except Exception:
            text = tr("读取失败")
        self.cf_status_label.setText(tr("CurseForge：") + text)

    def _save_cf_key(self):
        """把输入框里的 key 存进密钥文件(不进主配置、不进日志、不进仓库)。"""
        raw = self.cf_key_edit.text()
        if not secrets.clean(raw):
            self._cf_toast(tr("ℹ️ 输入框是空的：没有保存任何东西（要删掉已保存的 Key 请点「清除」）。"),
                           "warn")
            return
        if secrets.set_key(raw):
            self.cf_key_edit.clear()          # 存完立刻清空:明文没必要一直摆在界面上
            self._cf_log(trp("🔑 CurseForge API Key 已保存：{0}（指纹 {1}）",
                             secrets.mask(), secrets.fingerprint()), "INFO")
            self._cf_toast(tr("🔑 CurseForge API Key 已保存"), "ok")
        else:
            self._cf_log(trp("❌ CurseForge API Key 保存失败：写不了 {0}",
                             secrets.where_text()), "ERROR")
            self._cf_toast(tr("❌ CurseForge API Key 保存失败，详见日志"), "warn")
        self._update_cf_status()

    def _clear_cf_key(self):
        """删掉本机保存的 key。"""
        self.cf_key_edit.clear()
        if secrets.clear_key():
            msg = tr("🗑 已删除本机保存的 CurseForge API Key（联网搜索仍走 Modrinth）。")
            self._cf_log(msg, "INFO")
            self._cf_toast(msg, "ok")
        self._update_cf_status()

    def _test_cf_key(self):
        """测一下 Key 能不能用:后台线程发一次最小请求,别卡住设置窗口。"""
        if self._cf_testing:
            return
        pending = secrets.clean(self.cf_key_edit.text())
        if not pending and not secrets.has_key():
            self._cf_toast(tr("ℹ️ 先在输入框里粘一个 Key，或者先「保存」一个再测。"), "warn")
            return
        self._cf_toast(tr("🌐 正在测试 CurseForge API Key…（首次联网可能要几秒）"), "info")
        self._cf_testing = True
        self.cf_test_btn.setEnabled(False)

        def run():
            try:
                from core.mod_search import test_curseforge_key
                ok, note = test_curseforge_key(pending or None)
            except Exception as e:
                ok, note = False, secrets.redact_exc(e)
            try:
                self._cf_test_done.emit(bool(ok), str(note))
            except RuntimeError:              # 设置窗已经关了,对象没了
                pass

        try:
            threading.Thread(target=run, daemon=True).start()
        except Exception as e:
            self._cf_testing = False
            self.cf_test_btn.setEnabled(True)
            self._cf_log(trp("❌ 起不了测试线程：{0}", secrets.redact_exc(e)), "ERROR")
            self._cf_toast(tr("❌ 起不了测试线程，详见日志"), "warn")

    @QtCore.Slot(bool, str)
    def _on_cf_test_done(self, ok, note):
        self._cf_testing = False
        self.cf_test_btn.setEnabled(True)
        self._cf_log(trp("✅ CurseForge Key 可用：{0}", note) if ok
                     else trp("❌ CurseForge Key 不可用：{0}", note),
                     "INFO" if ok else "ERROR")
        self._cf_toast(tr("✅ CurseForge Key 可用") if ok
                       else tr("❌ CurseForge Key 不可用，详见日志"),
                       "ok" if ok else "warn")
        self._update_cf_status()

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
            "scroll_speed": int(self.scroll_speed_spin.value()),
            "online_tags": self.tags_check.isChecked(),
            "rollback_parts": [k for k in self._ROLLBACK_ORDER
                               if self._rollback_checks[k].isChecked()],
            "sfx_on": self.sfx_check.isChecked(),
            "toast_on": self.toast_check.isChecked(),
            "close_action": self.close_combo.currentData(),
            "main_window_backend": self.backend_combo.currentData(),
        }

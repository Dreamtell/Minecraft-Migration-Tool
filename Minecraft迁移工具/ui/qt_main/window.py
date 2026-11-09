# ui/qt_main/window.py
"""Qt主窗口顶层框架:布局组装、配置管理、主题切换(完整版:含三清单页+迁移执行)。"""
import sys
import os
import threading
from pathlib import Path

# 确保 _qt 在路径里
_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError as e:
    raise ImportError(
        "Qt主界面需要 PySide6。请运行: pip install \"PySide6==6.11.2\" --target _qt"
    ) from e

from utils.config import load_raw_config, save_raw_config, CONFIG_FILE
from utils.theme import LIGHT_THEME, DARK_THEME
from utils import i18n
from utils.i18n import tr, trp


class ListEdit(QtWidgets.QTextEdit):
    """清单文本框:拖进文件/文件夹时不插入文字,而是交给 on_files(paths) 处理。

    HANDOFF 里记的「Qt 窗口一拖就崩」是 Tk+Qt 同进程时的病(OLE 回调绕过 Tk 的
    after 泵);这里是纯 Qt 进程,走 Qt 自己的拖放没有那个问题。
    """

    def __init__(self, on_files=None, parent=None):
        super().__init__(parent)
        self._on_files = on_files
        self.setAcceptRichText(False)
        self.setAcceptDrops(True)
        # 逐步撤销(对齐 Tk 版 mw_lists._setup_custom_undo):Qt 原生撤销会把连续输入/删除
        # 合并成一块,删了三行只能一次全还原。这里每次内容变化记一份快照,Ctrl+Z 逐个恢复,
        # 恢复时保住滚动位置和光标(不然撤销后视图"置顶")。上限 200 步。
        self.setUndoRedoEnabled(False)
        self._undo_stack = []
        self._redo_stack = []
        self._last = ""
        self._restoring = False
        self.textChanged.connect(self._snapshot)

    UNDO_LIMIT = 200

    def _snapshot(self):
        if self._restoring:
            return
        cur = self.toPlainText()
        if cur != self._last:
            self._undo_stack.append(self._last)
            if len(self._undo_stack) > self.UNDO_LIMIT:
                self._undo_stack.pop(0)
            self._last = cur
            self._redo_stack.clear()

    def reset_undo(self):
        """载入配置后调:把当前内容当起点,不让"空 → 载入的清单"成为第一步撤销。"""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._last = self.toPlainText()

    def _restore(self, text):
        bar = self.verticalScrollBar()
        pos, caret = bar.value(), self.textCursor().position()
        self._restoring = True
        try:
            self.setPlainText(text)
        finally:
            self._restoring = False
        self._last = text
        cur = self.textCursor()
        cur.setPosition(min(caret, len(text)))
        self.setTextCursor(cur)
        bar.setValue(min(pos, bar.maximum()))

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self.toPlainText())
        self._restore(self._undo_stack.pop())

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self.toPlainText())
        self._restore(self._redo_stack.pop())

    def keyPressEvent(self, e):
        if e.matches(QtGui.QKeySequence.Undo):
            self.undo()
            e.accept()
            return
        if e.matches(QtGui.QKeySequence.Redo) or (
                e.key() == QtCore.Qt.Key_Y and e.modifiers() == QtCore.Qt.ControlModifier):
            self.redo()
            e.accept()
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        # 右键菜单里的「撤销/重做」接到自己的栈上(原生撤销已关,否则那两项永远是灰的)
        menu = self.createStandardContextMenu()
        for act in menu.actions():
            name = act.objectName()
            if name == "edit-undo":
                act.setEnabled(bool(self._undo_stack))
                act.triggered.connect(self.undo)
            elif name == "edit-redo":
                act.setEnabled(bool(self._redo_stack))
                act.triggered.connect(self.redo)
        menu.exec(e.globalPos())
        menu.deleteLater()

    def _paths(self, mime):
        if not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def dragEnterEvent(self, e):
        if self._on_files and self._paths(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._on_files and self._paths(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        paths = self._paths(e.mimeData()) if self._on_files else []
        if paths:
            e.acceptProposedAction()
            try:
                self._on_files(paths)
            except Exception:
                pass
        else:
            super().dropEvent(e)

    def insertFromMimeData(self, source):
        # 粘贴文件(资源管理器 Ctrl+C → 这里 Ctrl+V)和拖放走同一条路
        paths = self._paths(source) if self._on_files else []
        if paths:
            self._on_files(paths)
            return
        super().insertFromMimeData(source)


class LogEdit(QtWidgets.QTextEdit):
    """执行日志框:双击某一行时把那一行的块(QTextBlock)交给 on_double_click。"""

    def __init__(self, on_double_click=None, parent=None):
        super().__init__(parent)
        self._on_dbl = on_double_click

    def mouseDoubleClickEvent(self, e):
        if self._on_dbl is not None:
            try:
                blk = self.cursorForPosition(e.position().toPoint()).block()
                self._on_dbl(blk)
                return
            except Exception:
                pass
        super().mouseDoubleClickEvent(e)


class QtMainWindow(QtWidgets.QMainWindow):
    """Qt版主界面(第一阶段:主流程骨架)。

    职责:
      - 顶层布局组装(路径区 + 清单页 + 底栏 + 日志)
      - 配置读写(继承Tk版的配置键名,无缝切换)
      - 主题管理(LIGHT_THEME/DARK_THEME)
      - 托盘/闪屏的宿主(复用现有组件)

    不含业务逻辑:迁移/扫描/回滚都调用 core/,和Tk版共享同一套实现。
    """

    # 后台扫描线程 → 主线程。Signal(object) 可以传任意 Python 对象(list / None),
    # invokeMethod 的 Q_ARG(object, ...) 在 PySide6 里不行(没有 QMetaType)。
    _scan_done = QtCore.Signal(object)
    _scan_failed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Minecraft 整合包迁移工具 - 增强版 v5"))
        # 信号的接收者是 self(主线程的 QObject),所以默认 AutoConnection 会排队到主线程
        self._scan_done.connect(self._show_diff_result)
        self._scan_failed.connect(self._on_scan_failed)
        self.resize(1000, 1080)

        # 加载配置
        self.config = load_raw_config()

        # 装语言层(中文时什么都不做)。⚠ 只能用 Qt 版接口:set_language/install
        # 会往进程里装 Tk 控件钩子(app_qt.run 已经装过一次,这里重复调用是幂等的,
        # 留着是为了 `python -m ui.qt_main` 独立预览时也能翻译)
        lang = self.config.get("language", "zh")
        i18n.set_language_qt(lang)
        i18n.install_qt()

        # 主题
        theme_name = self.config.get("theme", "light")
        self.theme = DARK_THEME if theme_name == "dark" else LIGHT_THEME
        self._apply_theme()

        # 配置变量(QVariantMap的Qt惯用法;Tk版用StringVar/BooleanVar)
        self.source_path = self.config.get("source", "")
        self.target_path = self.config.get("target", "")
        self.world_name = self.config.get("world", tr("老子的世界"))
        self.dry_run = self.config.get("dry_run", True)
        self.overwrite_mods = self.config.get("overwrite", False)
        self.extra_conflict = self.config.get("extra_conflict", "overwrite")
        if self.extra_conflict not in ("overwrite", "skip"):
            self.extra_conflict = "overwrite"
        # 关闭行为:ask / tray / exit(与 Tk 版同一个配置键)
        self.close_action = self.config.get("close_action", "ask")
        if self.close_action not in ("ask", "tray", "exit"):
            self.close_action = "ask"
        # 托盘由 app.py 注入(attach_tray);独立预览模式下没有托盘
        self._tray = None
        self._notify_cb = None
        self._hidden_in_tray = False
        self._quitting = False
        self._scanning = False

        # 迁移运行时状态
        self._migration_running = False
        self._worker = None
        self._worker_thread = None
        self._progress_dialog = None
        self._diff_view = None        # 持有差异窗口引用,防止被GC

        # UI构建
        self._build_ui()

        # 从配置载入清单
        self._load_lists_from_config()

        # 启动时主动检测一次实例环境(构造函数setText不触发textChanged,
        # 所以从配置载入的源路径不会自动触发检测,这里补一次)
        if self.source_path:
            self._detect_env()
        self._update_world_status()

    def _apply_theme(self):
        """应用主题色到窗口调色板。"""
        try:
            self.setStyleSheet(f"""
                QMainWindow {{
                    background: {self.theme.get("bg", "#f0f0f0")};
                    color: {self.theme.get("fg", "#000")};
                }}
                QTextEdit {{
                    background: {self.theme.get("text_bg", "#fff")};
                    color: {self.theme.get("text_fg", "#000")};
                    border: 1px solid {self.theme.get("muted_fg", "#888")};
                    border-radius: 4px;
                    padding: 4px;
                }}
                QLineEdit {{
                    background: {self.theme.get("entry_bg", "#fff")};
                    color: {self.theme.get("entry_fg", "#000")};
                    border: 1px solid {self.theme.get("muted_fg", "#888")};
                    border-radius: 4px;
                    padding: 4px;
                }}
                QPushButton {{
                    background: {self.theme.get("button_bg", "#e0e0e0")};
                    color: {self.theme.get("button_fg", "#000")};
                    border: 1px solid {self.theme.get("muted_fg", "#888")};
                    border-radius: 4px;
                    padding: 6px 12px;
                    min-height: 24px;
                }}
                QPushButton:hover {{
                    background: {self.theme.get("hover_bg", "#e9eef5")};
                }}
                QLabel {{
                    color: {self.theme.get("fg", "#000")};
                }}
                QGroupBox {{
                    border: 1px solid {self.theme.get("muted_fg", "#888")};
                    border-radius: 6px;
                    margin-top: 10px;
                    padding-top: 8px;
                    font-weight: bold;
                }}
                QGroupBox::title {{
                    subcontrol-origin: margin;
                    subcontrol-position: top left;
                    left: 10px;
                    padding: 0 4px;
                    color: {self.theme.get("accent_fg", self.theme.get("fg", "#000"))};
                }}
                QTabWidget::pane {{
                    border: 1px solid {self.theme.get("muted_fg", "#888")};
                    border-radius: 4px;
                    top: -1px;
                }}
                QTabBar::tab {{
                    background: {self.theme.get("button_bg", "#e0e0e0")};
                    color: {self.theme.get("fg", "#000")};
                    padding: 6px 14px;
                    border: 1px solid {self.theme.get("muted_fg", "#888")};
                    border-bottom: none;
                    border-top-left-radius: 4px;
                    border-top-right-radius: 4px;
                }}
                QTabBar::tab:selected {{
                    background: {self.theme.get("text_bg", "#fff")};
                    color: {self.theme.get("accent_fg", self.theme.get("fg", "#000"))};
                }}
                QProgressBar {{
                    border: 1px solid {self.theme.get("muted_fg", "#888")};
                    border-radius: 4px;
                    text-align: center;
                    background: {self.theme.get("text_bg", "#fff")};
                    color: {self.theme.get("fg", "#000")};
                }}
                QProgressBar::chunk {{
                    background: {self.theme.get("accent_fg", "#4a90d9")};
                    border-radius: 3px;
                }}
                QCheckBox {{
                    color: {self.theme.get("fg", "#000")};
                    spacing: 6px;
                }}
            """)
        except Exception as e:
            print(f"[Qt主窗口] 应用主题失败: {e}")

    def _theme_btn_label(self):
        """主题按钮文案:显示“点击会切到哪个主题”。"""
        is_dark = self.theme is DARK_THEME
        return tr("☀️ 浅色") if is_dark else tr("🌙 深色")

    def _toggle_theme(self):
        """浅/深主题切换:换 theme → 重套样式表 → 刷新手动上色的控件 → 存配置。"""
        is_dark = self.theme is DARK_THEME
        self.theme = LIGHT_THEME if is_dark else DARK_THEME
        new_name = "light" if is_dark else "dark"
        self.config["theme"] = new_name
        self._apply_theme()
        self._after_theme_change()
        try:
            save_raw_config({"theme": new_name})
        except Exception:
            pass
        self.log(trp("主题已切换为: {0}", new_name), "INFO")
        self.toast(tr("🌙 已切换到深色主题") if new_name == "dark" else tr("☀️ 已切换到浅色主题"),
                   "ok", key="theme")

    def _after_theme_change(self):
        """换主题后:刷新那些按状态手动上色、不走全局 QSS 的控件(都是幂等的)。"""
        for fn in (self._detect_env, self._update_world_status, self._refresh_msg_badge):
            try:
                fn()
            except Exception:
                pass
        if hasattr(self, "theme_btn"):
            self.theme_btn.setText(self._theme_btn_label())
        dock = getattr(self, "_msg_dock", None)
        if dock is not None:
            dock.set_theme(self.theme)
        if hasattr(self, "start_btn") and hasattr(self.start_btn, "set_dark"):
            self.start_btn.set_dark(self.theme is DARK_THEME)
        host = getattr(self, "_toast_host", None)
        if host is not None:
            host.set_theme(self.theme)
        chart = getattr(self, "_net_chart", None)
        if chart is not None:
            chart.set_theme(self.theme)
        # 网络灯的颜色也是手动设的:重新探一次就会按新主题上色
        if hasattr(self, "net_label") and not getattr(self, "_net_probe_running", False):
            self._probe_network()

    def _open_settings(self):
        """打开设置对话框,OK 后统一经 _apply_settings 落地。"""
        try:
            from ui.qt_main.settings import SettingsDialog
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, tr("设置"), trp("无法打开设置: {0}", str(e)))
            return
        dlg = SettingsDialog(self, self.config)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self._apply_settings(dlg.values())

    def _apply_settings(self, vals):
        """把设置对话框的选择落地:更新内存属性/界面/主题/历史份数,再持久化。"""
        # 1) 主题(仅在变化时重套,避免无谓刷新)
        new_theme_name = vals.get("theme", "light")
        cur_theme_name = "dark" if self.theme is DARK_THEME else "light"
        if new_theme_name != cur_theme_name:
            self.theme = DARK_THEME if new_theme_name == "dark" else LIGHT_THEME
            self._apply_theme()
            self._after_theme_change()
        self.config["theme"] = new_theme_name

        # 2) 默认迁移选项 → 内存属性 + 同步底栏勾选框
        self.dry_run = bool(vals.get("dry_run", True))
        self.overwrite_mods = bool(vals.get("overwrite", False))
        self.extra_conflict = vals.get("extra_conflict", "overwrite")
        if self.extra_conflict not in ("overwrite", "skip"):
            self.extra_conflict = "overwrite"
        if hasattr(self, "dry_run_check"):
            self.dry_run_check.setChecked(self.dry_run)
        if hasattr(self, "overwrite_check"):
            self.overwrite_check.setChecked(self.overwrite_mods)
        self.config["dry_run"] = self.dry_run
        self.config["overwrite"] = self.overwrite_mods
        self.config["extra_conflict"] = self.extra_conflict

        # 3) 启动动画(仅存配置,app.py 启动时读)
        self.config["splash"] = bool(vals.get("splash", True))
        self.config["confirm_migrate"] = bool(vals.get("confirm_migrate", True))
        try:
            self.config["start_countdown"] = max(1, min(10, int(vals.get("start_countdown", 5))))
        except Exception:
            self.config["start_countdown"] = 5

        # 3a) 网络浮标:开关 + 大小 + 贴边收纳 + 边缘特效,立即生效
        self.config["net_float_on"] = bool(vals.get("net_float_on", False))
        try:
            self.config["net_float_size"] = max(40, min(96, int(vals.get("net_float_size", 56))))
        except Exception:
            self.config["net_float_size"] = 56
        self.config["net_float_dock"] = bool(vals.get("net_float_dock", True))
        self.config["float_edge_fx"] = bool(vals.get("float_edge_fx", True))
        self._sync_net_float()

        # 3c) 开始按钮手感 + 右下角消息卡片(与 Tk 版同键,立即生效)
        feel = vals.get("start_btn_feel", "standard")
        if feel in ("crisp", "standard", "heavy"):
            self.config["start_btn_feel"] = feel
            if hasattr(self.start_btn, "set_feel"):
                self.start_btn.set_feel(feel)
        self.config["rename_migrated_mods"] = bool(vals.get("rename_migrated_mods", False))
        self.config["rename_marker"] = str(vals.get("rename_marker", "★") or "★")
        self.config["silent_background"] = bool(vals.get("silent_background", False))
        self.config["toast_on"] = bool(vals.get("toast_on", True))
        if not self.config["toast_on"] and getattr(self, "_toast_host", None):
            self._toast_host.clear()

        # 3b) 关闭行为 + 主界面实现(后者重启生效)
        self.set_close_action(vals.get("close_action", "ask"), save=False)
        backend = vals.get("main_window_backend", "qt")
        if backend in ("qt", "tk"):
            if backend != self.config.get("main_window_backend", "qt"):
                self.log(tr("主界面实现已更改,重启后生效"), "INFO")
            self.config["main_window_backend"] = backend

        # 4) 历史份数:走 history_store.set_limit(它自己写配置并夹紧范围)
        try:
            from utils import history_store
            history_store.set_limit(int(vals.get("history_limit",
                                                 history_store.DEFAULT_LIMIT)))
        except Exception:
            pass

        # 5) 语言:改内存(界面已建部分需重启才全变);存配置
        new_lang = vals.get("language", "zh")
        if new_lang != i18n.language():
            try:
                i18n.set_language_qt(new_lang)   # 只改内存值,不装 Tk 钩子;重启生效
            except Exception:
                pass
            self.log(tr("语言已更改,重启后完全生效"), "INFO")
        self.config["language"] = new_lang

        # 6) 持久化(save_config 以 self.config 为底重建回写)
        self.save_config()
        self.toast(tr("⚙️ 设置已保存"), "ok", key="settings")

    def _build_ui(self):
        """构建整体布局(第一阶段简化版:路径+单个清单+底栏)。"""
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # === 顶部工具条(设置 / 主题切换) ===
        topbar = QtWidgets.QHBoxLayout()
        topbar.addStretch()
        self.buttons_btn = QtWidgets.QPushButton(tr("🔘 界面按钮"))
        self.buttons_btn.setToolTip(tr("显示/隐藏、调整各区按钮的顺序"))
        self.buttons_btn.clicked.connect(self._open_button_prefs)
        topbar.addWidget(self.buttons_btn)
        self.settings_btn = QtWidgets.QPushButton(tr("⚙️ 设置"))
        self.settings_btn.clicked.connect(self._open_settings)
        topbar.addWidget(self.settings_btn)
        # 按钮文案显示“点了会变成什么”:浅色主题时提示切到深色,反之亦然
        self.theme_btn = QtWidgets.QPushButton(self._theme_btn_label())
        self.theme_btn.clicked.connect(self._toggle_theme)
        topbar.addWidget(self.theme_btn)
        layout.addLayout(topbar)

        # === 路径区 ===
        paths_group = QtWidgets.QGroupBox(tr("路径设置"))
        paths_layout = QtWidgets.QFormLayout(paths_group)

        # 源路径
        src_row = QtWidgets.QHBoxLayout()
        self.source_edit = QtWidgets.QLineEdit(self.source_path)
        self.source_edit.textChanged.connect(self._on_source_changed)
        src_row.addWidget(self.source_edit)
        src_btn = QtWidgets.QPushButton(tr("浏览"))
        src_btn.clicked.connect(self._browse_source)
        src_row.addWidget(src_btn)
        paths_layout.addRow(tr("源实例:"), src_row)

        # 目标路径
        tgt_row = QtWidgets.QHBoxLayout()
        self.target_edit = QtWidgets.QLineEdit(self.target_path)
        self.target_edit.textChanged.connect(self._on_target_changed)
        tgt_row.addWidget(self.target_edit)
        tgt_btn = QtWidgets.QPushButton(tr("浏览"))
        tgt_btn.clicked.connect(self._browse_target)
        tgt_row.addWidget(tgt_btn)
        paths_layout.addRow(tr("目标实例:"), tgt_row)

        # 存档名 + 实时"存在/不存在"状态
        world_row = QtWidgets.QHBoxLayout()
        self.world_edit = QtWidgets.QLineEdit(self.world_name)
        self.world_edit.textChanged.connect(self._on_world_changed)
        world_row.addWidget(self.world_edit)
        self.world_status = QtWidgets.QLabel("")
        self.world_status.setMinimumWidth(140)
        world_row.addWidget(self.world_status)
        paths_layout.addRow(tr("存档名称:"), world_row)

        # 实例状态(简化:只显示文字,不做检测)
        self.env_label = QtWidgets.QLabel(tr("未检测"))
        self.env_label.setStyleSheet(f"color: {self.theme.get('muted_fg', '#888')};")
        paths_layout.addRow(tr("实例环境:"), self.env_label)

        layout.addWidget(paths_group)

        # === 三个清单页(模组 / config / 其它文件) ===
        self.tabs = QtWidgets.QTabWidget()

        # 模组清单
        self.mod_text = ListEdit(on_files=self._drop_mods)
        self.mod_text.setPlaceholderText(tr("每行一个 .jar 文件名(可拖入 jar 或 mods 文件夹)"))
        self.mod_text.textChanged.connect(self._refresh_badges)
        mod_page = self._make_list_page(
            self.mod_text,
            [("add_mods", tr("➕ 添加模组"), self._add_mods),
             ("changelog", tr("📥 从变更日志导入"), self._import_changelog),
             ("scan_diff", tr("🔍 扫描差异"), self._scan_diff),
             ("mod_magnify", tr("📂 放大查看"), lambda: self._open_big_view("mod")),
             ("clear_mods", tr("🗑️ 清空"), lambda: self._clear_list(self.mod_text)),
             ("check_mods", tr("🔎 检查是否存在"), lambda: self._check_list("mods"))],
            group="mods")
        self._mod_tab_index = self.tabs.addTab(mod_page, tr("模组清单"))

        # config清单
        self.config_text = ListEdit(on_files=lambda ps: self._add_paths_to("config", ps))
        self.config_text.setPlaceholderText(tr("每行一个 config 下的相对路径(可拖入)"))
        self.config_text.textChanged.connect(self._refresh_badges)
        config_page = self._make_list_page(
            self.config_text,
            [("add_cfg_dir", tr("📁 添加文件夹"), self._add_config_dir),
             ("add_cfg_file", tr("📄 添加文件"), self._add_config_files),
             ("cfg_magnify", tr("📂 放大查看"), lambda: self._open_big_view("config")),
             ("clear_cfg", tr("🗑️ 清空"), lambda: self._clear_list(self.config_text)),
             ("check_cfg", tr("🔎 检查是否存在"), lambda: self._check_list("config"))],
            group="config")
        self._config_tab_index = self.tabs.addTab(config_page, tr("config 清单"))

        # 其它文件清单
        self.extra_text = ListEdit(on_files=lambda ps: self._add_paths_to("extra", ps))
        self.extra_text.setPlaceholderText(tr("每行一个相对整合包根目录的路径(文件或目录,可拖入)"))
        self.extra_text.textChanged.connect(self._refresh_badges)
        extra_page = self._make_list_page(
            self.extra_text,
            [("add_extra_options", tr("＋ options.txt"), lambda: self._add_extra_rel("options.txt")),
             ("add_extra_dir", tr("📁 添加文件夹"), self._add_extra_dir),
             ("add_extra_file", tr("📄 添加文件"), self._add_extra_files),
             ("clear_extra", tr("🗑️ 清空"), lambda: self._clear_list(self.extra_text)),
             ("check_extra", tr("🔎 检查是否存在"), lambda: self._check_list("extra"))],
            group="extra")
        self._extra_tab_index = self.tabs.addTab(extra_page, tr("其它文件"))

        # 恢复上次停留的页签
        try:
            self.tabs.setCurrentIndex(int(self.config.get("list_tab", 0) or 0))
        except Exception:
            pass
        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tabs, stretch=3)

        # === 执行日志 ===
        log_group = QtWidgets.QGroupBox(tr("执行日志"))
        log_layout = QtWidgets.QVBoxLayout(log_group)

        self.log_text = LogEdit(on_double_click=self._locate_from_log_line)
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText(tr("操作日志将在此显示(双击错误/警告行可定位到清单条目)"))
        log_layout.addWidget(self.log_text)

        log_tools = QtWidgets.QHBoxLayout()
        self.fail_btn = QtWidgets.QPushButton(tr("📍 定位错误"))
        self.fail_btn.setEnabled(False)          # 没有出错条目时置灰(同 Tk 版)
        self.fail_btn.clicked.connect(self._goto_next_fail)
        clear_log = QtWidgets.QPushButton(tr("🧹 清空日志"))
        clear_log.clicked.connect(self._clear_log)
        log_layout.addLayout(log_tools)
        self._register_button_row("log", log_tools,
                                  [("fail_locate", self.fail_btn), ("log_clear", clear_log)],
                                  stretch_end=True)
        self._failed_items = []     # [(页索引, 条目名)]
        self._fail_cursor = 0

        layout.addWidget(log_group, stretch=2)

        # === 底栏(开关+按钮) ===
        bottom = QtWidgets.QHBoxLayout()

        self.dry_run_check = QtWidgets.QCheckBox(tr("🧪 模拟运行(不实际复制)"))
        self.dry_run_check.setChecked(self.dry_run)
        self.dry_run_check.toggled.connect(self._on_dry_run_toggled)
        bottom.addWidget(self.dry_run_check)

        self.overwrite_check = QtWidgets.QCheckBox(tr("覆盖同名模组"))
        self.overwrite_check.setChecked(self.overwrite_mods)
        self.overwrite_check.toggled.connect(self._on_overwrite_toggled)
        bottom.addWidget(self.overwrite_check)

        # 查看历史按钮
        self.history_btn = QtWidgets.QPushButton(tr("📜 查看历史"))
        self.history_btn.clicked.connect(self._view_history)

        # 开始迁移按钮:3D 翻转(正面绿/模拟运行黄,迁移中翻到背面红;手感与 Tk 共用 start_btn_feel)
        from ui.qt_main.flip_button import FlipButton
        self.start_btn = FlipButton(tr("🚀 开始迁移"), dark=(self.theme is DARK_THEME),
                                    feel=str(self.config.get("start_btn_feel", "standard")),
                                    dry_run=self.dry_run)
        self.start_btn.setMinimumWidth(120)
        self.start_btn.clicked.connect(self._start_migration)

        # 两个开关固定在左边;右边这一排可在「界面按钮」里调顺序/隐藏
        action_row = QtWidgets.QHBoxLayout()
        bottom.addLayout(action_row, 1)
        self._register_button_row("action", action_row,
                                  [("history", self.history_btn), ("start", self.start_btn)],
                                  stretch_start=True)

        layout.addLayout(bottom)

        # === 状态栏:网络连通性(复用 ui/mw_net 的纯函数探测,不消耗 API 配额) ===
        self.net_label = QtWidgets.QLabel(tr("● 正在检测网络…"))
        self.net_label.setStyleSheet(f"color: {self.theme.get('muted_fg', '#888')};")
        self.statusBar().addPermanentWidget(self.net_label)

        # === 消息中心:铃铛 + 右侧停靠面板(和 Tk 版共用 utils.message_store 的存档) ===
        try:
            from utils.message_store import MessageStore
            self._msg_store = MessageStore()
        except Exception:
            self._msg_store = None
        self._msg_dock = None
        self.msg_bell = QtWidgets.QToolButton()
        self.msg_bell.setAutoRaise(True)
        self.msg_bell.setToolTip(tr("消息中心 (Ctrl+M)"))
        self.msg_bell.clicked.connect(self.toggle_message_center)
        self.statusBar().addPermanentWidget(self.msg_bell)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+M"), self,
                        activated=self.toggle_message_center)
        self._refresh_msg_badge()
        self._net_probe_running = False
        self._net_timer = QtCore.QTimer(self)
        self._net_timer.timeout.connect(self._probe_network)
        self._net_timer.start(5000)
        QtCore.QTimer.singleShot(300, self._probe_network)
        # 状态栏的网络灯可点:打开延迟曲线(浮标关着的时候也能看)
        self.net_label.setCursor(QtCore.Qt.PointingHandCursor)
        self.net_label.mousePressEvent = lambda _e: self._open_net_chart()
        self._net_float = None
        self._net_chart = None
        self._net_last = None
        # 浮标要等主窗口有位置以后再建(它跟着主窗口的客户区定位)
        QtCore.QTimer.singleShot(0, self._sync_net_float)

        # 初始日志
        self.log(tr("Qt主界面已就绪(第一阶段完整版:迁移已接入)"), "INFO")
        self.log(trp("主题: {0}", self.config.get("theme", "light")), "INFO")
        self._refresh_badges()

    def _make_list_page(self, text_edit, buttons, group=None):
        """造一个清单页:文本框 + 一排操作按钮。

        buttons = [(key, 文案, 回调), ...];key 对应 ui/qt_main/buttons.py 的登记表,
        用于「界面按钮」设置里的显示/隐藏和排序。
        """
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(6, 6, 6, 6)
        v.addWidget(text_edit)
        row = QtWidgets.QHBoxLayout()
        v.addLayout(row)
        self._register_button_row(group, row, buttons, stretch_end=True)
        return page

    # ---------------- 可自定义的按钮排(显示/隐藏 + 顺序) ----------------
    def _register_button_row(self, group, row, buttons, stretch_end=False, stretch_start=False):
        """建一排按钮并登记;之后 _apply_button_prefs() 会按配置重排这一排。"""
        rows = self.__dict__.setdefault("_btn_rows", {})
        widgets = self.__dict__.setdefault("_btn_widgets", {})
        keys = []
        for item in buttons:
            if len(item) == 3:
                key, label, cb = item
                b = QtWidgets.QPushButton(label)
                b.clicked.connect(cb)
            else:                         # (key, 现成控件)
                key, b = item
            widgets[key] = b
            keys.append(key)
        rows[group] = {"layout": row, "keys": keys,
                       "stretch_end": stretch_end, "stretch_start": stretch_start}
        self._layout_button_row(group)

    def _layout_button_row(self, group):
        info = self._btn_rows.get(group)
        if not info:
            return
        from ui.qt_main.buttons import order_for
        row = info["layout"]
        # 先全部摘下来(不删控件),再按配置顺序放回
        while row.count():
            row.takeAt(0)
        hidden = set(self.config.get("buttons_hidden", []) or [])
        saved = self.config.get("button_order", {}) or {}
        ordered = [k for k in order_for(group, saved) if k in info["keys"]]
        ordered += [k for k in info["keys"] if k not in ordered]   # 登记表里没有的兜底
        if info["stretch_start"]:
            row.addStretch()
        for k in ordered:
            w = self._btn_widgets[k]
            row.addWidget(w)
            w.setVisible(k not in hidden)
        if info["stretch_end"]:
            row.addStretch()

    def _apply_button_prefs(self):
        for g in list(getattr(self, "_btn_rows", {})):
            self._layout_button_row(g)

    def _open_button_prefs(self):
        from ui.qt_main.buttons import ButtonPrefsDialog, merge_into_config
        dlg = ButtonPrefsDialog(self, self.config.get("buttons_hidden", []),
                                self.config.get("button_order", {}))
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        qt_hidden, qt_order = dlg.values()
        hidden, order = merge_into_config(self.config.get("buttons_hidden", []),
                                          self.config.get("button_order", {}),
                                          qt_hidden, qt_order)
        self.config["buttons_hidden"] = hidden
        self.config["button_order"] = order
        self._apply_button_prefs()
        self.save_config()
        self.toast(tr("🔘 界面按钮已更新"), "ok", key="buttons")

    def _list_lines(self, text_edit):
        """取清单里的有效行(去空行/注释)。"""
        return [x.strip() for x in text_edit.toPlainText().splitlines()
                if x.strip() and not x.strip().startswith("#")]

    def _refresh_badges(self):
        """在页签标题上显示每个清单的条数。"""
        try:
            n_mod = len(self._list_lines(self.mod_text))
            n_cfg = len(self._list_lines(self.config_text))
            n_extra = len(self._list_lines(self.extra_text))
            self.tabs.setTabText(self._mod_tab_index,
                                 trp("模组清单 ({0})", n_mod) if n_mod else tr("模组清单"))
            self.tabs.setTabText(self._config_tab_index,
                                 trp("config 清单 ({0})", n_cfg) if n_cfg else tr("config 清单"))
            self.tabs.setTabText(self._extra_tab_index,
                                 trp("其它文件 ({0})", n_extra) if n_extra else tr("其它文件"))
        except Exception:
            pass

    def _on_tab_changed(self, index):
        """记住当前页签(保存到配置时写回 list_tab)。"""
        self.config["list_tab"] = index

    def _update_world_status(self):
        """实时检测存档是否已存在于源实例的 saves 下。"""
        try:
            if not hasattr(self, "world_status"):
                return
            src = self.source_path
            world = self.world_name
            if not src or not world:
                self.world_status.setText("")
                return
            saves = Path(src) / "saves" / world
            if saves.is_dir():
                self.world_status.setText(tr("✅ 存档存在"))
                self.world_status.setStyleSheet(
                    f"color: {self.theme.get('ok_fg', '#2e7d32')};")
            else:
                self.world_status.setText(tr("⚠️ 源中无此存档"))
                self.world_status.setStyleSheet(
                    f"color: {self.theme.get('warn_fg', '#e65100')};")
        except Exception:
            pass

    def _browse_source(self):
        """浏览源路径。"""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr("选择源实例目录"), self.source_path
        )
        if path:
            self.source_edit.setText(path)

    def _browse_target(self):
        """浏览目标路径。"""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr("选择目标实例目录"), self.target_path
        )
        if path:
            self.target_edit.setText(path)

    def _on_source_changed(self, text):
        """源路径变化:更新变量并检测环境+存档状态。"""
        self.source_path = text.strip()
        self._detect_env()
        self._update_world_status()

    def _on_target_changed(self, text):
        """目标路径变化:更新变量。"""
        self.target_path = text.strip()

    def _on_world_changed(self, text):
        """存档名变化:更新变量并刷新存档状态。"""
        self.world_name = text.strip()
        self._update_world_status()

    def _on_dry_run_toggled(self, checked):
        """模拟运行开关。"""
        self.dry_run = checked
        btn = getattr(self, "start_btn", None)
        if btn is not None and hasattr(btn, "set_dry_run"):
            btn.set_dry_run(checked)        # 正面换黄 / 换回绿
        self.toast(tr("🧪 模拟运行已开启:只预览,不改动任何文件") if checked
                   else tr("⚠️ 模拟运行已关闭:下次迁移会真正复制文件"),
                   "info" if checked else "warn", key="dry_run")

    def _on_overwrite_toggled(self, checked):
        """覆盖模组开关。"""
        self.overwrite_mods = checked
        self.toast(tr("覆盖同名模组:开") if checked else tr("覆盖同名模组:关"),
                   "info", key="overwrite")

    def _detect_env(self):
        """调用 core.scanner.detect_instance_env 检测实例环境。"""
        try:
            from core.scanner import detect_instance_env
            if not self.source_path or not Path(self.source_path).is_dir():
                self.env_label.setText(tr("路径无效"))
                self.env_label.setStyleSheet(f"color: {self.theme.get('fail_fg', '#c62828')};")
                return
            result = detect_instance_env(self.source_path)
            mc, loader, src = result.get("mc", ""), result.get("loader", ""), result.get("src", "")
            if mc or loader:
                text = f"✅ {mc or '?'} / {loader or '?'}"
                if src:
                    text += f" ({src})"
                self.env_label.setText(text)
                self.env_label.setStyleSheet(f"color: {self.theme.get('ok_fg', '#2e7d32')};")
            else:
                self.env_label.setText(tr("未识别到版本/加载器"))
                self.env_label.setStyleSheet(f"color: {self.theme.get('warn_fg', '#e65100')};")
        except Exception as e:
            self.env_label.setText(tr("检测失败: {0}").format(str(e)))
            self.env_label.setStyleSheet(f"color: {self.theme.get('fail_fg', '#c62828')};")

    # ---------------- 消息(吐司) / 消息中心 ----------------
    def toast(self, text, level="info", ms=None, key=None):
        """统一的操作反馈入口(对齐 Tk 版 MigrationGUI.toast 的签名)。

        右下角浮层卡片(ui/qt_main/toast.py,规则同 Tk 版),同时记进消息中心。
        level: ok / info / warn / error;key 相同的消息原地更新,不新增卡片。
        配置键 toast_on 关掉后只记进消息中心,不弹卡片。
        """
        msg = str(text or "").strip()
        if not msg:
            return
        store = getattr(self, "_msg_store", None)
        if store is not None:
            try:
                store.add(msg, level, key=key)
            except Exception:
                pass
            dock = getattr(self, "_msg_dock", None)
            if dock is not None and dock.isVisible():
                store.标记已读()
                dock.refresh()
            self._refresh_msg_badge()
        if not bool(self.config.get("toast_on", True)):
            return
        host = getattr(self, "_toast_host", None)
        if host is None:
            try:
                from ui.qt_main.toast import ToastHost
                host = self._toast_host = ToastHost(self, self.theme)
            except Exception:
                host = self._toast_host = False
        if host:
            host.show(msg, {"warning": "warn", "err": "error"}.get(level, level), ms=ms, key=key)
        else:
            self.statusBar().showMessage(msg, int(ms or 3000))

    def _refresh_msg_badge(self):
        bell = getattr(self, "msg_bell", None)
        if bell is None:
            return
        store = getattr(self, "_msg_store", None)
        n = int(getattr(store, "未读", 0) or 0)
        bell.setText(("🔔 %d" % n) if n else "🔔")
        color = self.theme.get("warn_fg", "#ffa000") if n else self.theme.get("muted_fg", "#888")
        bell.setStyleSheet(f"color: {color}; border: none; padding: 0 6px;")

    def toggle_message_center(self):
        store = getattr(self, "_msg_store", None)
        if store is None:
            return
        dock = getattr(self, "_msg_dock", None)
        if dock is None:
            from ui.qt_main.messages import MessageDock
            dock = MessageDock(self, store, self.theme)
            self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
            self._msg_dock = dock
            dock.show()
            return
        dock.setVisible(not dock.isVisible())

    # ---------------- 网络状态 ----------------
    def _probe_network(self):
        """子线程跑 TCP 探测(DNS+连接可能要几秒),结果用 invokeMethod 排回主线程。"""
        if self._net_probe_running:
            return
        self._net_probe_running = True

        def task():
            import json as _json
            try:
                from ui import mw_net
                res = mw_net.probe()
                try:
                    mw_net.record(res)
                except Exception:
                    pass
            except Exception:
                res = {}
            QtCore.QMetaObject.invokeMethod(
                self, "_on_net_result", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, _json.dumps(res)))
        threading.Thread(target=task, daemon=True).start()

    # ---------------- 网络浮标 + 延迟曲线 ----------------
    def _sync_net_float(self):
        """按配置开/关/调整浮标大小。幂等:开着就保持,大小变了就重建。"""
        want = bool(self.config.get("net_float_on", False))
        try:
            size = max(40, min(96, int(self.config.get("net_float_size", 56))))
        except Exception:
            size = 56
        cur = getattr(self, "_net_float", None)
        if cur is not None and (not want or cur._d != size):
            cur.close()
            cur.deleteLater()
            self._net_float = cur = None
        if want and cur is None:
            from ui.qt_main.net_float import NetFloat
            rel = (self.config.get("net_float_x", -1), self.config.get("net_float_y", -1))
            try:
                rel = (int(rel[0]), int(rel[1]))
            except Exception:
                rel = None
            f = NetFloat(self, size=size, rel=rel,
                         dock=bool(self.config.get("net_float_dock", True)),
                         edge_fx=bool(self.config.get("float_edge_fx", True)))
            f.clicked.connect(self._open_net_chart)
            f.moved.connect(self._on_float_moved)
            last = getattr(self, "_net_last", None)
            if last is not None:
                f.update_state(last)
            self._net_float = f
        elif cur is not None:
            cur.set_dock_enabled(bool(self.config.get("net_float_dock", True)))
            cur.set_edge_fx(bool(self.config.get("float_edge_fx", True)))

    @QtCore.Slot(int, int)
    def _on_float_moved(self, dx, dy):
        self.config["net_float_x"] = int(dx)
        self.config["net_float_y"] = int(dy)
        try:
            save_raw_config({"net_float_x": int(dx), "net_float_y": int(dy)})
        except Exception:
            pass

    def _open_net_chart(self):
        dlg = getattr(self, "_net_chart", None)
        if dlg is not None:
            if dlg.isVisible():
                dlg.hide()            # 再点一次 = 关(同 Tk 版的"三态点击"简化版)
                return
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        from ui.qt_main.net_float import NetChartDialog
        dlg = NetChartDialog(self, self.theme)
        self._net_chart = dlg
        dlg.show()

    @QtCore.Slot(str)
    def _on_net_result(self, payload):
        import json as _json
        self._net_probe_running = False
        try:
            res = _json.loads(payload) or {}
        except Exception:
            res = {}
        self._net_last = res
        f = getattr(self, "_net_float", None)
        if f is not None:
            f.update_state(res)
        ok = [k for k, v in res.items() if v]
        if res and len(ok) == len(res):
            text, key = tr("● 网络已连接"), "ok_fg"
        elif ok:
            text, key = tr("● 网络部分不通"), "warn_fg"
        else:
            text, key = tr("● 网络未连接"), "fail_fg"
        self.net_label.setText(text)
        self.net_label.setStyleSheet(f"color: {self.theme.get(key, '#888')};")
        self.net_label.setToolTip("\n".join(
            "%s:%s" % (k, ("%d ms" % v) if v else tr("不通")) for k, v in res.items()))

    def _import_changelog(self):
        """粘贴整合包变更日志 → 提取 Added/Updated 模组 → 替换模组清单(同 Tk 版)。"""
        from core.changelog import extract_mods
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(tr("从变更日志提取模组清单"))
        dlg.resize(800, 600)
        v = QtWidgets.QVBoxLayout(dlg)
        v.addWidget(QtWidgets.QLabel(
            tr("请粘贴完整的变更日志文本(包含 'Added mods:' 和 'Updated mods:' 部分):")))
        box = QtWidgets.QPlainTextEdit()
        v.addWidget(box, 1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        go = QtWidgets.QPushButton(tr("提取并应用"))
        cancel = QtWidgets.QPushButton(tr("取消"))
        row.addWidget(go)
        row.addWidget(cancel)
        v.addLayout(row)
        cancel.clicked.connect(dlg.reject)

        def apply():
            added, updated = extract_mods(box.toPlainText())
            if not added and not updated:
                QtWidgets.QMessageBox.warning(dlg, tr("无结果"), tr("未能提取到模组文件名"))
                return
            mods = added
            if updated:
                r = QtWidgets.QMessageBox.question(
                    dlg, tr("发现 Updated mods"),
                    trp("已提取到 {0} 个 Added 模组,{1} 个 Updated 模组。\n"
                        "是否将 Updated 模组也添加到复制清单中?\n\n"
                        "「是」→ 全部添加\n「否」→ 只添加 Added 模组\n「取消」→ 不添加",
                        len(added), len(updated)),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                    | QtWidgets.QMessageBox.Cancel)
                if r == QtWidgets.QMessageBox.Cancel:
                    return
                if r == QtWidgets.QMessageBox.Yes:
                    mods = added + updated
            if not mods:
                QtWidgets.QMessageBox.information(dlg, tr("提示"), tr("未添加任何模组"))
                return
            self._replace_text(self.mod_text, "\n".join(mods))
            self._refresh_badges()
            self.save_config()
            self.log(trp("从变更日志中提取了 {0} 个模组(Added: {1}, Updated: {2})",
                         len(mods), len(added), len(updated)), "OK")
            dlg.accept()

        go.clicked.connect(apply)
        dlg.exec()

    # ---------------- 清空 / 检查是否存在 ----------------
    def _clear_list(self, box):
        """清空走 _replace_text,所以 Ctrl+Z 能撤回来(Qt 的 clear() 会清掉撤销栈)。"""
        if not box.toPlainText().strip():
            return
        self._replace_text(box, "")
        self.toast(tr("🗑️ 已清空(Ctrl+Z 可撤销)"), "info")

    def _replace_text(self, box, text):
        """整段替换文本但**保留撤销栈**:setPlainText 会清空撤销历史,
        用光标 select-all + insertText 则是一次可撤销的编辑。"""
        cur = QtGui.QTextCursor(box.document())
        cur.beginEditBlock()
        cur.select(QtGui.QTextCursor.Document)
        cur.insertText(text)
        cur.endEditBlock()
        box.setExtraSelections([])
        self._refresh_badges()

    def _check_list(self, kind):
        """检查清单条目在源实例里是否存在:逐行上色(存在=绿,缺失=红,重复=黄)。"""
        from core.list_check import check
        box = {"mods": self.mod_text, "config": self.config_text,
               "extra": self.extra_text}[kind]
        label = {"mods": tr("模组"), "config": tr("config"), "extra": tr("其它文件")}[kind]
        r = check(kind, self.source_path, box.toPlainText())
        err = r.get("error")
        if err == "no_source":
            self.toast(tr("⚠️ 请先选择源整合包实例根目录"), "warn")
            return
        if err == "no_dir":
            self.log(trp("❌ 源目录不存在:{0}", r.get("dir", "")), "ERROR")
            self.toast(trp("❌ 源目录不存在:{0}", r.get("dir", "")), "warn")
            return
        if err == "empty":
            self.toast(trp("⚠️ 当前{0}清单为空", label), "warn")
            return

        colors = {"ok": self.theme.get("ok_fg", "#2e7d32"),
                  "missing": self.theme.get("fail_fg", "#c62828"),
                  "dup": self.theme.get("warn_fg", "#e65100")}
        sels = []
        doc = box.document()
        for line_no, st in r["rows"].items():
            blk = doc.findBlockByNumber(line_no)
            if not blk.isValid():
                continue
            sel = QtWidgets.QTextEdit.ExtraSelection()
            cur = QtGui.QTextCursor(blk)
            cur.select(QtGui.QTextCursor.LineUnderCursor)
            sel.cursor = cur
            sel.format.setForeground(QtGui.QColor(colors[st]))
            if st != "ok":
                bg = QtGui.QColor(colors[st])
                bg.setAlpha(40)
                sel.format.setBackground(bg)
            sels.append(sel)
        box.setExtraSelections(sels)
        # 文本一改,颜色就和内容对不上了:下次编辑时清掉。
        # 用标志记"连没连过":对没连过的槽 disconnect,PySide6 会打 RuntimeWarning
        if not box.property("_check_marks_hooked"):
            box.textChanged.connect(self._clear_check_marks)
            box.setProperty("_check_marks_hooked", True)

        self.log(trp("🔎 {0}清单检查:存在 {1} · 缺失 {2} · 重复 {3}",
                     label, r["ok"], r["missing"], r["dup"]),
                 "WARNING" if (r["missing"] or r["dup"]) else "OK")
        for name in r["missing_names"][:30]:
            self.log("  - " + name, "ERROR")
        if len(r["missing_names"]) > 30:
            self.log(trp("  … 另有 {0} 条缺失未列出", len(r["missing_names"]) - 30), "WARNING")
        self.toast(trp("🔎 存在 {0} · 缺失 {1} · 重复 {2}", r["ok"], r["missing"], r["dup"]),
                   "warn" if r["missing"] else "ok")

    @QtCore.Slot()
    def _clear_check_marks(self):
        box = self.sender()
        if isinstance(box, QtWidgets.QTextEdit) and box.property("_check_marks_hooked"):
            box.setExtraSelections([])
            box.textChanged.disconnect(self._clear_check_marks)
            box.setProperty("_check_marks_hooked", False)

    def _drop_mods(self, paths):
        """拖进模组清单:jar 取文件名;文件夹(比如整个 mods)展开里面的 jar。"""
        names = []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                names += sorted(x.name for x in pp.glob("*.jar"))
            elif pp.suffix.lower() == ".jar":
                names.append(pp.name)
        if not names:
            self.log(tr("⚠️ 拖入的内容里没有 .jar 文件"), "WARNING")
            return
        self._append_lines(self.mod_text, names, tr("模组清单"))

    def _add_mods(self):
        """添加模组:打开文件对话框选jar。"""
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, tr("选择模组文件"), self.source_path,
            tr("Jar文件 (*.jar)")
        )
        if not files:
            return
        # 只取文件名(不带路径)
        names = [Path(f).name for f in files]
        existing = self.mod_text.toPlainText().strip()
        if existing:
            combined = existing + "\n" + "\n".join(names)
        else:
            combined = "\n".join(names)
        self._replace_text(self.mod_text, combined)
        self.log(trp("已添加 {0} 个模组", len(names)), "INFO")

    # ---------------- config / 其它文件清单的添加(路径转成相对路径) ----------------
    def _rel_under(self, base, path):
        """path 相对 base 的路径(正斜杠);不在 base 下返回 None。"""
        try:
            rel = Path(path).resolve().relative_to(Path(base).resolve())
        except Exception:
            return None
        s = rel.as_posix().strip("/")
        if not s or s == ".":
            return None
        try:
            from core.migrator import _is_safe_path
            if not _is_safe_path(s):
                return None
        except Exception:
            pass
        return s

    def _append_lines(self, text_edit, items, label):
        """往清单末尾追加(去重),返回实际加了几条。"""
        cur = self._list_lines(text_edit)
        have = set(cur)
        new = [x for x in items if x and x not in have]
        if not new:
            return 0
        text = text_edit.toPlainText().rstrip()
        self._replace_text(text_edit, (text + "\n" if text else "") + "\n".join(new))
        self._refresh_badges()
        self.log(trp("{0}:已添加 {1} 条", label, len(new)), "INFO")
        self.toast(trp("➕ {0}:已添加 {1} 条", label, len(new)), "ok")
        return len(new)

    def _add_paths_to(self, which, paths):
        """which: "config" / "extra"。paths 是绝对路径;转相对路径后加进清单。"""
        if not self.source_path:
            QtWidgets.QMessageBox.warning(self, tr("提示"), tr("请先选择源实例目录"))
            return 0
        base = Path(self.source_path) / "config" if which == "config" else Path(self.source_path)
        text_edit = self.config_text if which == "config" else self.extra_text
        label = tr("config 清单") if which == "config" else tr("其它文件")
        ok, bad = [], []
        for p in paths:
            rel = self._rel_under(base, p)
            (ok if rel else bad).append(rel or str(p))
        if bad:
            self.log(trp("⚠️ 有 {0} 项不在 {1} 下,已跳过:{2}", len(bad), str(base),
                         "、".join(Path(b).name for b in bad[:5])), "WARNING")
        return self._append_lines(text_edit, ok, label)

    def _add_config_dir(self):
        start = str(Path(self.source_path) / "config") if self.source_path else ""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, tr("选择 config 下的文件夹"), start)
        if d:
            self._add_paths_to("config", [d])

    def _add_config_files(self):
        start = str(Path(self.source_path) / "config") if self.source_path else ""
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, tr("选择 config 下的文件"), start)
        if files:
            self._add_paths_to("config", files)

    def _add_extra_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, tr("选择要带走的文件夹"),
                                                       self.source_path)
        if d:
            self._add_paths_to("extra", [d])

    def _add_extra_files(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, tr("选择要带走的文件"),
                                                          self.source_path)
        if files:
            self._add_paths_to("extra", files)

    def _add_extra_rel(self, rel):
        """一键加一个相对整合包根目录的条目(源里没有就拒绝,和 Tk 版一致)。"""
        if not self.source_path:
            QtWidgets.QMessageBox.warning(self, tr("提示"), tr("请先选择源实例目录"))
            return
        if not (Path(self.source_path) / rel).exists():
            QtWidgets.QMessageBox.warning(self, tr("提示"),
                                          trp("源实例里没有 {0},没有加入清单", rel))
            return
        self._append_lines(self.extra_text, [rel], tr("其它文件"))

    def _load_lists_from_config(self):
        """从配置加载三份清单内容。"""
        mod_list = self.config.get("mod_list", "").strip()
        if mod_list:
            self.mod_text.setPlainText(mod_list)
        config_list = self.config.get("config_list", "").strip()
        if config_list:
            self.config_text.setPlainText(config_list)
        extra_list = self.config.get("extra_list", "").strip()
        if extra_list:
            self.extra_text.setPlainText(extra_list)
        for box in (self.mod_text, self.config_text, self.extra_text):
            box.reset_undo()            # 载入的清单是撤销的起点,不是第一步
        self._refresh_badges()

    def _start_migration(self):
        """开始迁移(主线程):校验→自检→倒计时→后台准备→确认→执行。

        倒计时期间再点一下按钮 = 取消(对齐 Tk 版:误点有后悔窗口)。
        """
        if getattr(self, "_countdown_timer", None) is not None:
            self._cancel_countdown()
            return
        if self._migration_running:
            QtWidgets.QMessageBox.warning(self, tr("提示"),
                                          tr("迁移正在进行中,请勿重复启动"))
            return
        src, tgt = self.source_path, self.target_path
        world = self.world_name
        if not src or not tgt:
            QtWidgets.QMessageBox.critical(self, tr("错误"),
                                           tr("请选择源和目标实例根目录"))
            return
        if not world:
            QtWidgets.QMessageBox.critical(self, tr("错误"), tr("请输入存档名称"))
            return
        src_path, tgt_path = Path(src), Path(tgt)
        if not src_path.exists():
            QtWidgets.QMessageBox.critical(self, tr("错误"),
                                           trp("源路径不存在: {0}", src))
            return
        if not tgt_path.exists():
            QtWidgets.QMessageBox.critical(self, tr("错误"),
                                           trp("目标路径不存在: {0}", tgt))
            return
        modlist = self._list_lines(self.mod_text)
        configlist = self._list_lines(self.config_text)
        extralist = self._list_lines(self.extra_text)
        if not modlist and not configlist and not extralist:
            QtWidgets.QMessageBox.warning(self, tr("提示"),
                                          tr("三个清单都是空的,没有可迁移的内容"))
            return

        if self._scanning:
            QtWidgets.QMessageBox.warning(self, tr("提示"),
                                          tr("扫描模组差异还在进行,等它结束再开始迁移"))
            return

        # ① 迁移前自检(只列目录/stat,便宜,主线程跑)
        from ui.qt_main.migration import show_precheck
        self.log(tr("🔎 开始迁移前自检…"), "INFO")
        try:
            from core import precheck
            result = precheck.run_precheck(
                src, tgt, world, modlist, configlist, extralist,
                self.overwrite_mods, self.extra_conflict != "skip")
        except Exception as e:
            self.log(trp("⚠️ 自检没跑完({0}),已跳过自检继续", e), "WARNING")
            result = {"items": []}
        for c in result.get("items") or []:
            icon = {"fail": "❌ ", "warn": "⚠️ ", "info": "ℹ️ "}.get(c.get("level"), "")
            lvl = {"fail": "ERROR", "warn": "WARNING"}.get(c.get("level"), "INFO")
            self.log(icon + str(c.get("text", "")), lvl)
        if not show_precheck(self, result):
            self.log(tr("⛔ 自检没通过或你选择了先不开始,没有动任何文件"), "WARNING")
            return

        # 搭建 worker 参数
        from utils import history_store
        cancel_event = threading.Event()
        params = {
            "src_path": src_path,
            "tgt_path": tgt_path,
            "world": world,
            "modlist": modlist,
            "configlist": configlist,
            "extralist": extralist,
            "dry_run": self.dry_run,
            "overwrite": self.overwrite_mods,
            "extra_overwrite": self.extra_conflict != "skip",
            # 与 Tk 版同键:Tk 里开了「复制过去的模组加标记前缀」,Qt 里迁移也要照做
            "marker": (str(self.config.get("rename_marker", "★") or "★")
                       if bool(self.config.get("rename_migrated_mods", False)) else None),
            "silent": bool(self.config.get("silent_background", False)),
            "record_id": history_store.new_id(),
            "cancel_event": cancel_event,
        }

        # ② 倒计时(配置键 start_countdown,1~10 秒,默认 5;和 Tk 版同一个键)
        try:
            secs = max(0, min(10, int(self.config.get("start_countdown", 5))))
        except Exception:
            secs = 5
        if secs > 0:
            self._begin_countdown(secs, params)
            return
        self._begin_prepare(params)

    # ---------------- 开始前倒计时 ----------------
    def _begin_countdown(self, secs, params):
        self._countdown_left = secs
        self._countdown_params = params
        # 其它控件先灰掉;开始按钮保持可点(点它 = 取消)
        self.dry_run_check.setEnabled(False)
        self.overwrite_check.setEnabled(False)
        self._countdown_timer = QtCore.QTimer(self)
        self._countdown_timer.timeout.connect(self._countdown_tick)
        self._countdown_timer.start(1000)
        self._show_countdown()
        self.log(trp("⏳ 开始前倒计时 {0} 秒:期间再点一下「开始迁移」可取消", secs), "INFO")

    def _show_countdown(self):
        self.start_btn.setText(trp("⏳ {0} 秒后开始(点击取消)", self._countdown_left))

    def _countdown_tick(self):
        self._countdown_left -= 1
        if self._countdown_left > 0:
            self._show_countdown()
            return
        self._stop_countdown_timer()
        params, self._countdown_params = self._countdown_params, None
        self._begin_prepare(params)

    def _stop_countdown_timer(self):
        t = getattr(self, "_countdown_timer", None)
        if t is not None:
            t.stop()
            t.deleteLater()
        self._countdown_timer = None

    def _cancel_countdown(self):
        self._stop_countdown_timer()
        self._countdown_params = None
        self.start_btn.setText(tr("🚀 开始迁移"))
        self.dry_run_check.setEnabled(True)
        self.overwrite_check.setEnabled(True)
        self.log(tr("ℹ️ 已取消:倒计时期间又点了一下「开始迁移」,没有动任何文件"), "INFO")

    def _begin_prepare(self, params):
        """后台准备:建计划 + 磁盘空间检查。期间锁住界面,防止重复点击。"""
        self._migration_running = True
        self.start_btn.setEnabled(False)
        self.dry_run_check.setEnabled(False)
        self.overwrite_check.setEnabled(False)
        self.start_btn.setText(tr("🚀 开始迁移"))     # 正面文字复位(倒计时改过它)
        self.start_btn.set_progress(None)
        self.start_btn.flip_to_back(tr("⏳ 准备中..."))
        self._clear_fail_marks()        # 新一轮迁移:清掉上一轮的出错条目
        self.log(tr("📋 正在统计要迁移的文件..."), "INFO")
        from ui.qt_main.migration import PrepareWorker
        self._prep_worker = PrepareWorker(params)
        self._prep_thread = QtCore.QThread()
        self._prep_worker.moveToThread(self._prep_thread)
        self._prep_thread.started.connect(self._prep_worker.run)
        # ⚠ 必须连到 self 的 Slot 方法,不能连 lambda:lambda 没有接收对象,
        # Qt 判不出该排到哪个线程,会直接在 worker 线程里执行 —— 那里碰界面就是越线程
        self._prep_params = params
        self._prep_worker.finished_signal.connect(self._on_prepared_slot)
        self._prep_thread.start()

    @QtCore.Slot(object)
    def _on_prepared_slot(self, res):
        params = getattr(self, "_prep_params", None)
        self._prep_params = None
        if params is not None:
            self._on_prepared(params, res)

    def _unlock_after_prepare(self):
        self._migration_running = False
        self.start_btn.setEnabled(True)
        self.dry_run_check.setEnabled(True)
        self.overwrite_check.setEnabled(True)
        self.start_btn.flip_to_front(tr("🚀 开始迁移"))

    def _on_prepared(self, params, res):
        """主线程:准备完成 → 磁盘检查 → 二次确认 → 真正开跑。"""
        th = getattr(self, "_prep_thread", None)
        if th is not None:
            th.quit()
            th.wait(2000)
        self._prep_thread = None
        self._prep_worker = None

        if res.get("error"):
            self._unlock_after_prepare()
            level = "WARNING" if res.get("cancelled") else "ERROR"
            self.log(trp("❌ 准备迁移失败:{0}", res["error"]), level)
            if not res.get("cancelled"):
                QtWidgets.QMessageBox.critical(self, tr("迁移错误"), res["error"])
            return
        plan, disk = res["plan"], res["disk"]
        if not plan["files"] and not plan["dirs"]:
            self._unlock_after_prepare()
            details = "\n".join("%s: %s" % (lab, err) for _, lab, err in plan.get("errors", []))
            QtWidgets.QMessageBox.warning(self, tr("提示"),
                                          tr("没有找到需要复制的文件") + ("\n" + details if details else ""))
            return
        mb = 1024 * 1024
        self.log(trp("📦 待迁移文件 {0} 个,总大小 {1:.1f} MB",
                     plan["total_files"], plan["total_size"] / mb), "INFO")
        if not disk[0]:
            self._unlock_after_prepare()
            QtWidgets.QMessageBox.critical(
                self, tr("磁盘空间不足"),
                trp("目标磁盘剩余空间 {0:.1f} MB,本次迁移约需 {1:.1f} MB(含备份余量)。",
                    disk[1] / mb, disk[2] / mb))
            return

        # ③ 正式迁移前二次确认(设置键 confirm_migrate,默认开;模拟运行不问)
        if not params["dry_run"] and bool(self.config.get("confirm_migrate", True)):
            from ui.qt_main.migration import ask_confirm
            mods_n = sum(1 for it in plan["files"]
                         if it["dst"].parent == plan["target"] / "mods"
                         and it["dst"].suffix.lower() == ".jar")
            ok = ask_confirm(self, {
                "src": str(params["src_path"]), "tgt": str(params["tgt_path"]),
                "world": params["world"], "mods": mods_n,
                "configs": len(params["configlist"]), "extras": len(params["extralist"]),
                "files": plan["total_files"], "size": plan["total_size"],
                "overwrite": params["overwrite"], "extra_overwrite": params["extra_overwrite"]})
            if not ok:
                self._unlock_after_prepare()
                self.log(tr("ℹ️ 你选择了「先不开始」,这次没有迁移任何文件"), "INFO")
                return

        params["plan"] = plan
        self._launch_migration(params, plan["total_files"], plan["total_size"])

    def _launch_migration(self, params, total_files, total_size):
        """真正起迁移 worker(界面在准备阶段已锁住)。"""
        src, tgt, world = str(params["src_path"]), str(params["tgt_path"]), params["world"]
        modlist, configlist, extralist = (params["modlist"], params["configlist"],
                                          params["extralist"])
        cancel_event = params["cancel_event"]

        # 建 worker + 线程
        from ui.qt_main.migration import MigrationWorker, ProgressDialog
        self._worker = MigrationWorker(params)
        self._worker_thread = QtCore.QThread()
        self._worker.moveToThread(self._worker_thread)

        # 连信号
        self._worker_thread.started.connect(self._worker.run)
        self._worker.log_signal.connect(self.log)
        self._worker.progress_signal.connect(self._on_worker_progress)
        self._worker.finished_signal.connect(self._on_migration_finished)

        # 迁移中:禁用界面(准备阶段已经锁过,这里再确认一次);按钮停在背面红
        self._migration_running = True
        self.start_btn.setEnabled(False)
        self.dry_run_check.setEnabled(False)
        self.overwrite_check.setEnabled(False)
        self._mig_total_files = max(1, int(total_files))
        self.start_btn.set_progress(None if params["dry_run"] else 0.0)
        self.start_btn.flip_to_back(tr("⏳ 模拟中...") if params["dry_run"] else tr("⏳ 迁移中..."))

        # 进度对话框(模拟运行、后台静默执行都不显示)。总数来自准备阶段的计划,进度条才走得准
        self._silent_run = bool(params.get("silent"))
        if not params["dry_run"] and not self._silent_run:
            self._progress_dialog = ProgressDialog(self, max(1, int(total_files)),
                                                   max(1, int(total_size)), self.theme)
            self._progress_dialog.set_cancel_callback(lambda: cancel_event.set())
            self._progress_dialog.show()
        else:
            self._progress_dialog = None

        self.log(tr("🚀 开始迁移..."), "INFO")
        self.log(trp("源: {0}", src), "INFO")
        self.log(trp("目标: {0}", tgt), "INFO")
        self.log(trp("存档: {0}", world), "INFO")
        self.log(trp("模拟运行: {0}", tr("是") if params["dry_run"] else tr("否")), "INFO")
        self.log(trp("模组 {0} 项 / config {1} 项 / 其它文件 {2} 项",
                     len(modlist), len(configlist), len(extralist)), "INFO")

        # 启动后台线程
        self._worker_thread.start()

    @QtCore.Slot(int, str, float, str)
    def _on_worker_progress(self, index, filename, size, step):
        """收到worker的进度信号(已在主线程)。"""
        if self._progress_dialog:
            self._progress_dialog.update_progress(index, filename, size, step)
        try:
            self.start_btn.set_progress(index / float(getattr(self, "_mig_total_files", 1) or 1))
        except Exception:
            pass

    @QtCore.Slot(bool, str)
    def _on_migration_finished(self, success, error_message):
        """迁移跑完(不论成功/失败/取消)。"""
        self._migration_running = False
        if self._progress_dialog:
            # 先摘掉取消回调:close() 会触发 closeEvent,否则又 set 一次 cancel_event
            # (迁移已结束,无害,但保持干净)
            dlg, self._progress_dialog = self._progress_dialog, None
            dlg.set_cancel_callback(None)
            if success:
                # 收尾那一跳(最后一批文件 → 100%)和关窗挨得太近,用户看到的是条走到
                # 2/3 就消失了。先把条推满,停 150ms 让这一帧真画出来,再关。
                dlg.finish(on_done=dlg.close)
            else:
                dlg.close()
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait(1000)
            self._worker_thread = None
        self._worker = None

        # 恢复界面:按钮翻回正面
        self.start_btn.setEnabled(True)
        self.dry_run_check.setEnabled(True)
        self.overwrite_check.setEnabled(True)
        self.start_btn.set_progress(1.0)
        self.start_btn.flip_to_front(tr("🚀 开始迁移"))

        # 系统通知:挂在托盘里时发(对齐 Tk 版 notify_task_done);
        # 后台静默执行没有进度窗和结果框,不管窗口在不在托盘都要发,否则用户收不到任何反馈
        silent, self._silent_run = bool(getattr(self, "_silent_run", False)), False
        if silent:
            self._notify(tr("✅ 迁移完成") if success else tr("⚠️ 迁移结束"),
                         tr("后台静默执行已结束，点托盘图标查看日志"))
        elif getattr(self, "_hidden_in_tray", False):
            self._notify(tr("✅ 迁移完成") if success else tr("⚠️ 迁移结束"),
                         tr("点托盘图标打开主界面查看"))

        # 结果(静默模式只写日志 + 吐司,不弹模态框)
        if error_message:
            if silent:
                self.log(trp("❌ 迁移任务失败:{0}", error_message), "ERROR")
                self.toast(trp("❌ 迁移任务失败:{0}", error_message), "error")
            else:
                QtWidgets.QMessageBox.critical(self, tr("迁移错误"), error_message)
        elif success:
            self.log(tr("✅ 迁移完成"), "OK")
            self.toast(tr("✅ 迁移完成"), "ok")
            if not silent:
                QtWidgets.QMessageBox.information(self, tr("完成"), tr("迁移已成功完成!"))
        else:
            # run_migration 返回 False 有两种:有条目复制失败 / 被取消。
            # 有出错条目就说出错,别笼统写"可能被取消"(用户看着像是自己点错了)
            n_fail = len(getattr(self, "_failed_items", []) or [])
            if n_fail:
                msg = trp("⚠️ 迁移完成但有 {0} 个条目出错(点「📍 定位错误」查看)", n_fail)
            else:
                msg = tr("⚠️ 迁移未完成(被取消或有失败,详见日志)")
            self.log(msg, "WARNING")
            self.toast(msg, "warn")

    def _view_history(self):
        """打开历史与回滚窗口。"""
        try:
            from ui.qt_main.history import HistoryDialog
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, tr("历史"), trp("无法打开历史窗口: {0}", str(e)))
            return
        HistoryDialog(self).exec()

    # ---------------- 放大查看(进程内直接用 QtBigView) ----------------
    def _open_big_view(self, which):
        """which: "mod" / "config"。同一个清单已开着就叫到前面,不重复开窗。"""
        views = self.__dict__.setdefault("_big_views", {})
        old = views.get(which)
        if old is not None:
            try:
                old.show()
                old.raise_()
                old.activateWindow()
                return
            except Exception:
                views.pop(which, None)

        text_edit = self.mod_text if which == "mod" else self.config_text
        title = tr("模组清单") if which == "mod" else tr("config 清单")
        entries = self._list_lines(text_edit)
        try:
            from ui.qt_big_view import QtBigView
            from ui.qt_host import _make_hooks
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, tr("错误"),
                                           trp("放大查看不可用: {0}", str(e)))
            return

        hooks = _make_hooks(QtCore)       # 纯 Qt 进程:defer 直接执行即可

        def write_back(entries_now, added=None, removed=None):
            # 只应用增删到**当前**文本上(主界面同时可能被编辑过,整份覆盖会盖掉)
            self._apply_entries_delta(text_edit, list(added or []), list(removed or []))

        hooks["write_back"] = write_back
        hooks["failed"] = lambda: []
        try:
            view = QtBigView(entries, which == "mod", self.source_path, title,
                             self.theme, online_tags=bool(self.config.get("online_tags", False)),
                             hooks=hooks,
                             cards=(self.config.get("big_view_view", "table") == "cards"))
        except Exception as e:
            self.log(trp("打开放大查看失败: {0}", str(e)), "ERROR")
            return
        view.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        view.destroyed.connect(lambda *_, k=which: views.pop(k, None))
        views[which] = view
        view.show_centered()

    def _apply_entries_delta(self, text_edit, added, removed):
        """把放大查看的增删应用到清单文本:删掉 removed(每条删一次)、末尾追加 added。"""
        lines = text_edit.toPlainText().splitlines()
        for name in removed:
            for i, ln in enumerate(lines):
                if ln.strip() == name:
                    del lines[i]
                    break
        have = {ln.strip() for ln in lines}
        for name in added:
            if name and name not in have:
                lines.append(name)
                have.add(name)
        new_text = "\n".join(ln for ln in lines if ln.strip() or ln == "")
        if new_text.strip() != text_edit.toPlainText().strip():
            self._replace_text(text_edit, new_text.strip())
            self._refresh_badges()
            if added or removed:
                self.log(trp("放大查看写回:新增 {0} / 移除 {1}", len(added), len(removed)),
                         "INFO")

    def _scan_diff(self):
        """扫描模组差异:调用现有 qt_diff_view(进程内,不需要子进程了)。"""
        src, tgt = self.source_path, self.target_path
        if not src or not tgt:
            QtWidgets.QMessageBox.warning(self, tr("提示"),
                                          tr("请先选择源和目标实例路径"))
            return
        src_p, tgt_p = Path(src), Path(tgt)
        if not src_p.is_dir() or not tgt_p.is_dir():
            QtWidgets.QMessageBox.critical(self, tr("错误"),
                                           tr("源或目标路径不存在"))
            return

        if self._scanning:
            self.log(tr("⚠️ 已有扫描在进行中"), "WARNING")
            return
        self._scanning = True
        self.log(tr("🔍 开始扫描模组差异..."), "INFO")

        # 在后台线程扫描(避免阻塞主界面);progress_queue 传 None(可选)。
        # ⚠ 结果是 list/None,不能用 invokeMethod + Q_ARG(object, ...) 送回主线程:
        #   PySide6 报 "Unable to find a QMetaType for object"(用户实测)。
        #   改走 Signal(object):worker 线程 emit,Qt 自动排队到主线程的槽。
        def scan_task():
            from core.scanner import scan_mod_differences
            try:
                result = scan_mod_differences(src_p, tgt_p, None, 0)
            except Exception as e:
                self._scan_failed.emit(str(e))
                return
            self._scan_done.emit(result)
        threading.Thread(target=scan_task, daemon=True).start()

    @QtCore.Slot()
    def _on_diff_view_destroyed(self):
        # 窗口关了(WA_DeleteOnClose)就放掉引用,下次扫描新建
        self._diff_view = None

    @QtCore.Slot(str)
    def _on_scan_failed(self, msg):
        self._scanning = False
        self.log(trp("扫描失败: {0}", msg), "ERROR")

    @QtCore.Slot()
    def _scan_ended(self):
        """扫描失败时复位忙态(成功路径在 _show_diff_result 里复位)。"""
        self._scanning = False

    @QtCore.Slot(object)
    def _show_diff_result(self, result):
        """主线程:扫描完成,打开差异窗口。"""
        self._scanning = False
        # scan_mod_differences 约定:None=路径/mods目录不存在,[]=无差异,list=有差异
        if result is None:
            self.log(tr("⚠️ 扫描失败:源或目标缺少 mods 目录"), "WARNING")
            QtWidgets.QMessageBox.warning(
                self, tr("提示"), tr("源或目标实例下找不到 mods 目录,无法扫描差异"))
            return
        self.log(trp("✅ 扫描完成,共 {0} 项差异", len(result)), "OK")
        if not result:
            QtWidgets.QMessageBox.information(self, tr("提示"),
                                              tr("源和目标的模组列表一致,没有差异"))
            return

        # 调用 qt_diff_view(Qt主窗口直接在进程内用,不需要子进程/pump)
        try:
            from ui import qt_diff_view
            if not qt_diff_view.available():
                raise ImportError("qt_diff_view not available")

            def apply_cb(selected):
                """用户从差异窗口点「应用选中」:checked_data() 返回显示名字符串列表。"""
                names = [str(x) for x in (selected or []) if x]
                if names:
                    existing = self.mod_text.toPlainText().strip()
                    combined = (existing + "\n" if existing else "") + "\n".join(names)
                    self._replace_text(self.mod_text, combined)
                    self.log(trp("已添加 {0} 个模组到清单", len(names)), "INFO")
                    self._refresh_badges()

            # 直接在进程内用 apply_callback(Qt主窗口不需要 hooks["defer"]):
            # defer 是给"Qt跑在Tk进程里"用的,这里是纯Qt进程,回调直接在主线程执行即可
            # ⚠ 不能传 parent=self:QtDiffView 是普通 QWidget(没设 Qt.Window),
            #   带了 parent 就会被画进主窗口里而不是弹出来。原设计就是无 parent 的顶层窗口。
            # ⚠ hooks 不能给 None:图标懒加载 / 动画都走 hooks["after"],给空就静默失效。
            #   和放大查看一样用 qt_host._make_hooks(纯 Qt 进程里 defer 直接执行)。
            from ui.qt_host import _make_hooks
            old = getattr(self, "_diff_view", None)
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            view = qt_diff_view.QtDiffView(
                result, self.theme, hooks=_make_hooks(QtCore), apply_callback=apply_cb,
                cards=(self.config.get("diff_view", "table") == "cards"),
                source_path=self.source_path)
            view.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            view.destroyed.connect(self._on_diff_view_destroyed)
            self._diff_view = view
            view.show_centered()
        except Exception as e:
            self.log(trp("打开差异窗口失败: {0}", str(e)), "ERROR")
            QtWidgets.QMessageBox.critical(self, tr("错误"),
                                           trp("差异窗口不可用: {0}", str(e)))

    # ---------------- 出错条目:标记 / 定位(规则同 Tk 版 mw_log) ----------------
    # 日志行级别 → QTextBlock.userState。0 = 普通;只有 2/3 的行响应双击定位
    _LOG_LEVEL_CODES = {"INFO": 1, "WARNING": 2, "ERROR": 3, "OK": 1, "SUCCESS": 1,
                        "SIMULATE": 1}

    def _list_pages(self):
        return ((0, self.mod_text, tr("模组清单")),
                (1, self.config_text, tr("config 清单")),
                (2, self.extra_text, tr("其它文件")))

    def _find_entry_in(self, text):
        """不解析日志格式:拿三个清单的条目名去这段文字里找,第一个被提到的就是它。

        返回 (页索引, 行号0起, 条目名) 或 None。
        """
        if not text:
            return None
        for idx, box, _ in self._list_pages():
            for i, line in enumerate(box.toPlainText().splitlines()):
                name = Path(line.strip()).name
                if name and name in text:
                    return idx, i, name
        return None

    def _mark_failed_from_log(self, text):
        hit = self._find_entry_in(text)
        if hit is None:
            return
        idx, _, name = hit
        if all(name != n for _, n in self._failed_items):
            self._failed_items.append((idx, name))
            self._refresh_fail_button()

    def _clear_fail_marks(self):
        self._failed_items = []
        self._fail_cursor = 0
        self._refresh_fail_button()

    def _refresh_fail_button(self):
        n = len(self._failed_items)
        if hasattr(self, "fail_btn"):
            self.fail_btn.setText(tr("📍 定位错误") + ((" (%d)" % n) if n else ""))
            self.fail_btn.setEnabled(bool(n))

    def _goto_next_fail(self):
        if not self._failed_items:
            return
        idx, name = self._failed_items[self._fail_cursor % len(self._failed_items)]
        self._fail_cursor = (self._fail_cursor + 1) % len(self._failed_items)
        box = self._list_pages()[idx][1]
        for i, line in enumerate(box.toPlainText().splitlines()):
            if Path(line.strip()).name == name:
                self._goto_list_line(idx, box, i)
                return
        self.log(trp("ℹ️ 「{0}」已经不在清单里了", name), "INFO")

    def _locate_from_log_line(self, block):
        """双击日志行:只有错误/警告行才定位(普通行双击没反应,同 Tk 版)。"""
        try:
            if block.userState() not in (2, 3):
                return
            text = block.text().strip()
        except Exception:
            return
        hit = self._find_entry_in(text)
        if hit is None:
            self.statusBar().showMessage(tr("这一行没提到清单中的条目,无法定位"), 2500)
            return
        self._goto_list_line(hit[0], self._list_pages()[hit[0]][1], hit[1])

    def _goto_list_line(self, idx, box, line_no):
        """切到对应页签 → 选中那一行 → 滚到可见 → 闪三下(240ms 一次)。"""
        self.tabs.setCurrentIndex(idx)
        blk = box.document().findBlockByNumber(line_no)
        if not blk.isValid():
            return
        cur = QtGui.QTextCursor(blk)
        cur.select(QtGui.QTextCursor.LineUnderCursor)
        box.setTextCursor(cur)
        box.ensureCursorVisible()
        box.setFocus()
        color = QtGui.QColor(self.theme.get("fail_fg", "#e53935"))
        color.setAlpha(90)

        def flash(i):
            sel = QtWidgets.QTextEdit.ExtraSelection()
            sel.cursor = QtGui.QTextCursor(blk)
            sel.format.setBackground(color)
            sel.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            box.setExtraSelections([sel] if i % 2 == 0 else [])
            if i < 5:
                QtCore.QTimer.singleShot(240, lambda: flash(i + 1))
        flash(0)

    def _clear_log(self):
        self.log_text.clear()

    @QtCore.Slot(str, str)
    def log(self, message, level="INFO"):
        """写日志到执行日志区(支持级别配色)。

        加 @Slot 是为了能被 QMetaObject.invokeMethod 从后台线程跨线程调用
        (扫描线程报错时走这条路)。worker的日志走 log_signal→log,属正常信号连接。
        """
        color_map = {
            "INFO": self.theme.get("log_info_fg", "#808080"),
            "WARNING": self.theme.get("log_warning_fg", "#e65100"),
            "ERROR": self.theme.get("log_error_fg", "#c62828"),
            "OK": self.theme.get("log_success_fg", "#2e7d32"),
            "SUCCESS": self.theme.get("log_success_fg", "#2e7d32"),
            "SIMULATE": self.theme.get("log_simulate_fg", "#1565c0"),
        }
        color = color_map.get(level, self.theme.get("fg", "#000"))

        cursor = self.log_text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)

        # 插入带颜色的文本;块级别记进 userState,双击定位时据此只认错误/警告行
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(QtGui.QColor(color))
        cursor.insertText(str(message) + "\n", fmt)
        try:
            blk = cursor.block().previous()      # 刚插完的那一行(光标已到下一空行)
            blk.setUserState(self._LOG_LEVEL_CODES.get(level, 0))
        except Exception:
            pass
        if level == "ERROR":
            self._mark_failed_from_log(str(message))

        # 自动滚到底部
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()

    def save_config(self):
        """保存配置(继承Tk版的键名,无缝切换)。"""
        try:
            config = dict(self.config)  # 保留原有键
            config.update({
                "source": self.source_path,
                "target": self.target_path,
                "world": self.world_name,
                "dry_run": self.dry_run,
                "overwrite": self.overwrite_mods,
                "extra_conflict": self.extra_conflict,
                "mod_list": self.mod_text.toPlainText().strip(),
                "config_list": self.config_text.toPlainText().strip(),
                "extra_list": self.extra_text.toPlainText().strip(),
                "list_tab": self.tabs.currentIndex(),
            })
            save_raw_config(config)
            self.log(tr("配置已保存"), "OK")
        except Exception as e:
            self.log(trp("保存配置失败: {0}", str(e)), "ERROR")

    # ---------------- 托盘 / 关闭行为(对齐 Tk 版 app.py 的 on_closing) ----------------
    def attach_tray(self, tray, notify_cb=None):
        """由 app.py 注入托盘(QtTray);没有托盘时不调,关窗即退出。"""
        self._tray = tray
        self._notify_cb = notify_cb
        tray.open_requested.connect(self.show_from_tray)
        tray.exit_requested.connect(self._tray_exit)
        tray.close_to_tray_toggled.connect(
            lambda on: self.set_close_action("tray" if on else "exit"))
        tray.set_close_to_tray(self.close_action != "exit")

    def set_close_action(self, action, save=True):
        if action not in ("ask", "tray", "exit"):
            return
        self.close_action = action
        self.config["close_action"] = action
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.set_close_to_tray(action != "exit")
        if save:
            self.save_config()

    def busy_task_name(self):
        """当前在跑的文件任务名;没有返回 None。关窗/托盘退出都问这一句。"""
        if self._migration_running:
            return tr("迁移")
        if getattr(self, "_scanning", False):
            return tr("扫描模组差异")
        if getattr(self, "_countdown_timer", None) is not None:
            return tr("开始前倒计时")
        rb = getattr(self, "_rollback_busy", None)
        if callable(rb) and rb():
            return tr("回滚")
        return None

    def show_from_tray(self):
        self._hidden_in_tray = False
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.set_tooltip(tr("Minecraft 整合包迁移工具"))
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self._force_foreground()

    def _force_foreground(self):
        """Win32 级置顶(同 utils.helpers.force_foreground,但那个模块顶层 import tkinter,
        纯 Qt 进程里不能碰,所以在这里重写一份)。

        Windows 的前台锁会让「不是前台进程」的 SetForegroundWindow 被忽略,
        Qt 的 raise_()+activateWindow() 在二次启动叫回主窗口时就是任务栏闪一下、窗口还压在后面。
        做法:最小化先还原 → SetForegroundWindow → 临时 HWND_TOPMOST 再摘掉(能绕开前台锁)。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            u32 = ctypes.windll.user32
            hwnd = int(self.winId())
            SW_RESTORE, SW_SHOW = 9, 5
            HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
            SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040
            u32.ShowWindow(hwnd, SW_RESTORE if u32.IsIconic(hwnd) else SW_SHOW)
            u32.SetForegroundWindow(hwnd)
            u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                             SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
            u32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
        except Exception:
            pass

    def hide_to_tray(self):
        self._hidden_in_tray = True
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.set_tooltip(tr("Minecraft 整合包迁移工具(后台运行中,点这里打开)"))
        self.hide()
        if not getattr(self, "_tray_tip_shown", False):
            self._tray_tip_shown = True
            self._notify(tr("已收进系统托盘"),
                         tr("程序仍在后台运行。点托盘图标可重新打开,右键可退出。"))

    def _notify(self, title, msg):
        cb = getattr(self, "_notify_cb", None)
        if cb is not None:
            try:
                cb(title, msg)
                return
            except Exception:
                pass
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.notify(title, msg)

    def _tray_exit(self):
        busy = self.busy_task_name()
        if busy:
            r = QtWidgets.QMessageBox.warning(
                self, trp("⚠️ {0}进行中", busy),
                trp("{0}任务还在执行,现在退出会中断它,可能导致数据损坏。\n\n确定要退出吗?", busy),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if r != QtWidgets.QMessageBox.Yes:
                return
        self.request_quit()

    def request_quit(self):
        """真正退出:存盘 → 撤托盘 → 退出事件循环。"""
        self._quitting = True
        self.save_config()
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.hide()
        QtWidgets.QApplication.instance().quit()

    def closeEvent(self, event):
        """点 ❌:任务进行中只能收进托盘或取消;否则按 close_action 处理。"""
        if getattr(self, "_quitting", False):
            event.accept()
            return
        tray = getattr(self, "_tray", None)
        busy = self.busy_task_name()
        if busy:
            event.ignore()
            if tray is None:
                QtWidgets.QMessageBox.warning(
                    self, trp("⚠️ {0}进行中", busy),
                    trp("{0}任务正在执行,现在关闭会中断操作。\n\n请等任务结束后再关闭窗口。", busy))
                return
            r = QtWidgets.QMessageBox.question(
                self, trp("⚠️ {0}进行中", busy),
                trp("{0}任务正在执行,窗口不能直接关闭。\n\n「是」→ 收进系统托盘继续跑\n「否」→ 返回程序", busy),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if r == QtWidgets.QMessageBox.Yes:
                self.hide_to_tray()
            return

        if tray is None:
            self.save_config()
            event.accept()
            return

        action = self.close_action
        if action == "ask":
            from ui.qt_main.settings import ask_close_action
            action, remember = ask_close_action(self)
            if action is None:
                event.ignore()
                return
            if remember:
                self.set_close_action(action)
        event.ignore()
        if action == "tray":
            self.hide_to_tray()
        else:
            self.request_quit()

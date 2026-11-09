# ui/qt_main/history.py
"""Qt 历史与回滚窗口。

- HistoryDialog:列出迁移档案(utils.history_store),右侧看详情,底部可回滚/删除/清空。
- RollbackWorker:后台 QThread 执行 core.migrator.do_restore(回滚读写大量文件,
  绝不能在主线程跑),日志用 Signal 送回主线程 —— 与 migration.py 同一套线程模式。
- PartPickerDialog:回滚前问「还原哪些类别」,只勾这份备份里实际有的。

回滚的 core 语义完全对齐 Tk 版 mw_migration.action_rollback:
  优先用「这条记录自己的备份」(get_record_backup_path);
  do_restore(parts=...) 执行;成功后 mark_rollback(target, record_id) 标记。
"""
import sys
from pathlib import Path

_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtWidgets, QtCore  # noqa: E402

from utils.i18n import tr, trp  # noqa: E402
from utils import history_store  # noqa: E402

# 回滚类别的中文标签(键见 core.migrator._RESTORE_PARTS)
_PART_LABELS = {
    "mods": tr("模组 (mods)"),
    "config": tr("配置 (config)"),
    "saves": tr("存档 (saves)"),
    "files": tr("根目录文件 (options.txt 等)"),
    "extras": tr("其它文件(你清单里加的)"),
}
_PART_ORDER = ("mods", "config", "saves", "files", "extras")


class RollbackWorker(QtCore.QObject):
    """后台回滚 worker。放进 QThread,通过信号把日志/结果送回主线程。"""

    log_signal = QtCore.Signal(str, str)        # (message, level)
    finished_signal = QtCore.Signal(bool, str)  # (success, record_id)

    def __init__(self, tgt_path, backup_dir, record_id, parts):
        super().__init__()
        self.tgt_path = tgt_path
        self.backup_dir = backup_dir
        self.record_id = record_id or ""
        self.parts = parts

    def _log(self, message, level="INFO"):
        self.log_signal.emit(str(message), str(level))

    @QtCore.Slot()
    def run(self):
        from core.migrator import do_restore
        success = False
        try:
            success = bool(do_restore(self.tgt_path, log_func=self._log,
                                      backup_dir=self.backup_dir, parts=self.parts))
        except Exception as error:
            self._log(trp("❌ 回滚过程中出现异常:{0}", error), "ERROR")
        self.finished_signal.emit(bool(success), self.record_id)


class PartPickerDialog(QtWidgets.QDialog):
    """回滚前问还原哪些类别。available = {键: 数量};0 表示这份备份里没有,置灰。"""

    def __init__(self, parent, available, default=None, remember=False):
        """default:默认勾哪些(上次「记住」的选择,与 Tk 版同键 rollback_parts);None = 有的全勾。"""
        super().__init__(parent)
        self.setWindowTitle(tr("选择回滚内容"))
        self.setMinimumWidth(360)
        self._checks = {}

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(QtWidgets.QLabel(tr("要把哪些内容还原到迁移前?")))

        for key in _PART_ORDER:
            cnt = int(available.get(key, 0) or 0)
            cb = QtWidgets.QCheckBox(self._label_for(key, cnt))
            if cnt == 0:
                cb.setEnabled(False)       # 这份备份里没有 → 不能选
                cb.setChecked(False)
            else:
                cb.setChecked(default is None or key in default)
            self._checks[key] = cb
            root.addWidget(cb)

        self.remember_check = QtWidgets.QCheckBox(
            tr("记住我的选择（以后回滚都按这个，可在设置里改）"))
        self.remember_check.setChecked(bool(remember))
        root.addWidget(self.remember_check)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText(tr("开始回滚"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _label_for(key, cnt):
        base = _PART_LABELS.get(key, key)
        if cnt == 0:
            return trp("{0} — 这份备份里没有", base)
        if cnt < 0:
            return trp("{0} — 迁移前为空(回滚将清除)", base)
        return trp("{0} — {1} 项", base, cnt)

    def selected_parts(self):
        """返回勾选且可用的类别集合。"""
        return {k for k, cb in self._checks.items()
                if cb.isChecked() and cb.isEnabled()}


class HistoryDialog(QtWidgets.QDialog):
    """历史记录窗口:左列表 + 右详情 + 底部回滚/删除/清空。

    window:Qt 主窗口,用它拿 target_path 和 log()(回滚日志回灌到主界面日志区)。
    """

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.records = []
        self._rb_thread = None
        self._rb_worker = None
        # 让主窗口的 busy_task_name() 知道"回滚还在跑"(托盘退出时要拦)
        window._rollback_busy = lambda: self._rb_thread is not None
        self.finished.connect(lambda *_: setattr(window, "_rollback_busy", None))
        self.setWindowTitle(tr("迁移历史"))
        self.resize(820, 540)

        root = QtWidgets.QVBoxLayout(self)
        split = QtWidgets.QHBoxLayout()
        root.addLayout(split, 1)

        # 左:记录列表
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setMinimumWidth(260)
        self.list_widget.currentRowChanged.connect(self._on_select)
        split.addWidget(self.list_widget)

        # 右:详情
        self.detail = QtWidgets.QTextEdit()
        self.detail.setReadOnly(True)
        split.addWidget(self.detail, 1)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        self.rollback_btn = QtWidgets.QPushButton(tr("⏪ 回滚到这一次之前"))
        self.rollback_btn.clicked.connect(self._do_rollback)
        btn_row.addWidget(self.rollback_btn)
        self.delete_btn = QtWidgets.QPushButton(tr("🗑 删除这份档案"))
        self.delete_btn.clicked.connect(self._delete_current)
        btn_row.addWidget(self.delete_btn)
        self.clear_btn = QtWidgets.QPushButton(tr("清空全部"))
        self.clear_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch()
        close_btn = QtWidgets.QPushButton(tr("关闭"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._refresh()

    # ---------- 列表 ----------
    def _refresh(self, keep_row=0):
        """重新从磁盘读档案并填列表。"""
        try:
            self.records = history_store.list_records()
        except Exception:
            self.records = []
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for rec in self.records:
            self.list_widget.addItem(self._row_label(rec))
        self.list_widget.blockSignals(False)
        if self.records:
            row = min(max(0, keep_row), len(self.records) - 1)
            self.list_widget.setCurrentRow(row)
        else:
            self.detail.setPlainText(tr("还没有任何迁移记录。"))
            self._set_buttons_for(None)

    @staticmethod
    def _row_label(rec):
        icon = "🧪" if rec.get("mode") == "dry" else "📦"
        if rec.get("rolled_back"):
            icon = "↩️"
        when = rec.get("time", "?")
        world = rec.get("world") or tr("(无世界)")
        return "%s  %s  %s" % (icon, when, world)

    def _current(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.records):
            return self.records[row]
        return None

    def _on_select(self, row):
        rec = self.records[row] if 0 <= row < len(self.records) else None
        self.detail.setHtml(self._detail_html(rec) if rec else "")
        self._set_buttons_for(rec)
    # ---------- 详情 ----------
    def _detail_html(self, rec):
        import html

        def esc(v):
            return html.escape(str(v))

        rows = []

        def add(label, value):
            rows.append("<tr><td style='color:#888;padding-right:12px;"
                        "vertical-align:top'>%s</td><td>%s</td></tr>"
                        % (esc(label), esc(value)))

        mode = tr("模拟运行") if rec.get("mode") == "dry" else tr("实际迁移")
        add(tr("时间"), rec.get("time", "?"))
        add(tr("类型"), mode)
        add(tr("结果"), rec.get("result", "?"))
        if rec.get("rolled_back"):
            add(tr("已回滚"), rec.get("rollback_time") or tr("是"))
        add(tr("世界"), rec.get("world") or tr("(无)"))
        add(tr("源"), rec.get("source", ""))
        add(tr("目标"), rec.get("target", ""))
        if rec.get("duration") is not None:
            add(tr("耗时"), trp("{0} 秒", rec.get("duration")))

        stats = rec.get("stats") or {}
        if isinstance(stats, dict) and stats:
            parts = ", ".join("%s=%s" % (esc(k), esc(v)) for k, v in stats.items())
            add(tr("统计"), parts)

        lists = rec.get("lists") or {}
        if isinstance(lists, dict):
            for key, label in (("mods", tr("模组")), ("configs", tr("配置")),
                               ("extras", tr("其它文件")), ("failed", tr("失败"))):
                items = lists.get(key) or []
                if items:
                    add(label, trp("{0} 项", len(items)))

        table = "<table style='border-collapse:collapse'>%s</table>" % "".join(rows)
        return "<div style='font-family:sans-serif;font-size:13px'>%s</div>" % table

    def _set_buttons_for(self, rec):
        """回滚钮只有在「这条记录有自己的备份」时才可点(早期记录没有)。"""
        busy = self._rb_thread is not None
        has = bool(rec) and not busy
        self.delete_btn.setEnabled(has)
        self.clear_btn.setEnabled(bool(self.records) and not busy)
        can_rb = False
        if rec and not busy:
            can_rb = self._record_backup_dir(rec) is not None
        self.rollback_btn.setEnabled(can_rb)
        if rec and not can_rb and not busy:
            self.rollback_btn.setToolTip(tr("这条记录没有自己的备份,无法回滚"))
        else:
            self.rollback_btn.setToolTip("")

    def _record_backup_dir(self, rec):
        """返回这条记录可用的备份目录(Path),没有则 None。

        对齐 Tk 版:优先按记录分的 rec_<id>/,其存在才算「可回滚到这一次」。
        """
        tgt = (self.window.target_path or "").strip()
        rid = str(rec.get("id") or "")
        if not tgt or not rid:
            return None
        try:
            from core.migrator import get_record_backup_path
            p = get_record_backup_path(Path(tgt), rid)
            return p if p and p.exists() else None
        except Exception:
            return None
    # ---------- 删除 / 清空 ----------
    def _delete_current(self):
        rec = self._current()
        if not rec:
            return
        if QtWidgets.QMessageBox.question(
                self, tr("删除"), tr("确定删除这份迁移档案?")) \
                != QtWidgets.QMessageBox.Yes:
            return
        try:
            history_store.delete_record(str(rec.get("id") or ""))
        except Exception:
            pass
        self._refresh(keep_row=self.list_widget.currentRow())

    def _clear_all(self):
        if not self.records:
            return
        if QtWidgets.QMessageBox.question(
                self, tr("清空"), tr("确定清空全部迁移历史?此操作不可撤销。")) \
                != QtWidgets.QMessageBox.Yes:
            return
        try:
            history_store.clear_all()
        except Exception:
            pass
        self._refresh()

    # ---------- 回滚 ----------
    def _do_rollback(self):
        rec = self._current()
        if not rec:
            return
        tgt = (self.window.target_path or "").strip()
        if not tgt or not Path(tgt).exists():
            QtWidgets.QMessageBox.critical(
                self, tr("回滚失败"), tr("请先在主界面选择一个存在的目标实例目录。"))
            return
        backup_dir = self._record_backup_dir(rec)
        if backup_dir is None:
            QtWidgets.QMessageBox.critical(
                self, tr("回滚失败"), tr("这条迁移记录没有可用的备份,回滚不了。"))
            return

        # 这份备份里实际有哪些类别 → 让用户勾
        try:
            from core.migrator import backup_parts_available
            available = backup_parts_available(backup_dir)
        except Exception:
            available = {}
        cfg = self.window.config
        saved = cfg.get("rollback_parts")
        default = set(saved) if isinstance(saved, list) and saved else None
        picker = PartPickerDialog(self, available, default,
                                  bool(cfg.get("rollback_parts_remember", False)))
        if picker.exec() != QtWidgets.QDialog.Accepted:
            return
        parts = picker.selected_parts()
        if not parts:
            QtWidgets.QMessageBox.information(
                self, tr("回滚"), tr("没有选择任何内容,已取消。"))
            return
        if picker.remember_check.isChecked():
            # 同 Tk 版:勾了「记住」就把这次的选择写成默认(设置页也读这个键)
            cfg["rollback_parts"] = sorted(parts)
            cfg["rollback_parts_remember"] = True
            try:
                self.window.save_config()
            except Exception:
                pass

        if QtWidgets.QMessageBox.question(
                self, tr("确认回滚"),
                trp("即将把目标实例还原到这次迁移之前(还原:{0})。继续?",
                    " / ".join(sorted(parts)))) != QtWidgets.QMessageBox.Yes:
            return

        self._start_rollback_worker(tgt, str(backup_dir),
                                    str(rec.get("id") or ""), parts)

    def _start_rollback_worker(self, tgt, backup_dir, record_id, parts):
        """起后台 QThread 跑 do_restore;日志经信号回灌主窗口日志区。"""
        self.window.log("=" * 40, "INFO")
        self.window.log(tr("🔄 开始回滚..."), "INFO")
        self._rb_worker = RollbackWorker(tgt, backup_dir, record_id, parts)
        self._rb_thread = QtCore.QThread()
        self._rb_worker.moveToThread(self._rb_thread)
        self._rb_worker.log_signal.connect(self.window.log)
        self._rb_worker.finished_signal.connect(self._on_rollback_done)
        self._rb_thread.started.connect(self._rb_worker.run)
        self._set_buttons_for(self._current())  # busy → 全灰
        self.rollback_btn.setText(tr("回滚中..."))
        self._rb_thread.start()

    @QtCore.Slot(bool, str)
    def _on_rollback_done(self, success, record_id):
        # 收尾线程
        if self._rb_thread:
            self._rb_thread.quit()
            self._rb_thread.wait()
        self._rb_thread = None
        self._rb_worker = None
        self.rollback_btn.setText(tr("⏪ 回滚到这一次之前"))

        if success:
            try:
                from core.migrator import mark_rollback
                mark_rollback(Path(self.window.target_path.strip()),
                              record_id or None)
            except Exception:
                pass
            self.window.log(tr("✅ 回滚完成"), "SUCCESS")
            if hasattr(self.window, "toast"):
                self.window.toast(tr("⏪ 回滚完成"), "ok")
            QtWidgets.QMessageBox.information(
                self, tr("回滚完成"), tr("已还原到这次迁移之前。"))
        else:
            self.window.log(tr("❌ 回滚失败,目标实例可能处于不完整状态"), "ERROR")
            if hasattr(self.window, "toast"):
                self.window.toast(tr("❌ 回滚失败,请查看日志"), "error")
            QtWidgets.QMessageBox.critical(
                self, tr("回滚失败"), tr("回滚未成功,请查看主界面日志。"))
        self._refresh(keep_row=self.list_widget.currentRow())

    def closeEvent(self, event):
        # 回滚进行中不许关窗(避免线程还在写文件时窗口被销毁)
        if self._rb_thread is not None:
            QtWidgets.QMessageBox.warning(
                self, tr("请稍候"), tr("回滚正在进行,完成后再关闭。"))
            event.ignore()
            return
        event.accept()

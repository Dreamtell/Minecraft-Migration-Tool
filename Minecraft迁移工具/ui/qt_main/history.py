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

from ui.qt_win import fit_window
from utils.i18n import tr, trp  # noqa: E402
from utils import history_store  # noqa: E402
from ui.qt_main.dialog_style import apply_button_styles, set_button_kind, theme_for  # noqa: E402

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
        fit_window(self, 420, 240, 440, 320)
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
        ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        ok_button.setText(tr("开始回滚"))
        set_button_kind(ok_button, "danger")
        set_button_kind(buttons.button(QtWidgets.QDialogButtonBox.Cancel), "neutral")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        apply_button_styles(self, theme_for(self))

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


def pick_rollback_parts(parent, window, backup_dir):
    """问用户这次还原哪些类别;返回类别集合,取消或什么都没选返回 None。

    按配置里记住的 rollback_parts 预勾,勾了「记住我的选择」就写回(Tk 设置页读同一个键)。
    单独成函数是为了不用起窗口就能测。
    """
    try:
        from core.migrator import backup_parts_available
        available = backup_parts_available(backup_dir)
    except Exception:
        available = {}
    cfg = window.config
    saved = cfg.get("rollback_parts")
    default = set(saved) if isinstance(saved, list) and saved else None
    picker = PartPickerDialog(parent, available, default,
                              bool(cfg.get("rollback_parts_remember", False)))
    if picker.exec() != QtWidgets.QDialog.Accepted:
        return None
    parts = picker.selected_parts()
    if not parts:
        QtWidgets.QMessageBox.information(
            parent, tr("回滚"), tr("没有选择任何内容,已取消。"))
        return None
    if picker.remember_check.isChecked():
        cfg["rollback_parts"] = sorted(parts)
        cfg["rollback_parts_remember"] = True
        try:
            window.save_config()
        except Exception:
            pass
    return parts


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
        fit_window(self, 700, 440, 820, 540)

        root = QtWidgets.QVBoxLayout(self)
        # 头部:共几份、最多保存几份、存在哪(同 Tk 版)
        self.header = QtWidgets.QLabel("")
        self.header.setWordWrap(True)
        root.addWidget(self.header)
        split = QtWidgets.QHBoxLayout()
        root.addLayout(split, 1)

        # 左:记录列表
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setMinimumWidth(260)
        self.list_widget.currentRowChanged.connect(self._on_select)
        split.addWidget(self.list_widget)

        # 右:上面是概览(标题 / 源 / 目标 / 统计 / 耗时 / 失败),下面是按级别上色的完整日志
        right = QtWidgets.QVBoxLayout()
        split.addLayout(right, 1)
        self.detail = QtWidgets.QTextEdit()          # 概览(只读,富文本)
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(210)
        right.addWidget(self.detail)
        log_bar = QtWidgets.QHBoxLayout()
        log_bar.addWidget(QtWidgets.QLabel(tr("执行日志")))
        self.log_count = QtWidgets.QLabel("")
        log_bar.addStretch(1)
        log_bar.addWidget(self.log_count)
        self.zoom_btn = QtWidgets.QPushButton(tr("⤢  放大"))
        set_button_kind(self.zoom_btn, "neutral")
        self.zoom_btn.setCheckable(True)
        self.zoom_btn.clicked.connect(self._toggle_zoom)
        log_bar.addWidget(self.zoom_btn)
        right.addLayout(log_bar)
        self.log_view = QtWidgets.QTextEdit()        # 日志(只读,逐行上色)
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        mono = self.log_view.font()
        mono.setFamily("Consolas")
        self.log_view.setFont(mono)
        right.addWidget(self.log_view, 1)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        self.rollback_btn = QtWidgets.QPushButton(tr("⏪ 回滚到这一次之前"))
        set_button_kind(self.rollback_btn, "danger")
        self.rollback_btn.clicked.connect(self._do_rollback)
        btn_row.addWidget(self.rollback_btn)
        self.apply_btn = QtWidgets.QPushButton(tr("🔁 应用到主界面"))
        set_button_kind(self.apply_btn, "neutral")
        self.apply_btn.setToolTip(tr("把这份档案里的路径、存档名和三个清单回填到主界面（不会开始迁移）"))
        self.apply_btn.clicked.connect(self._apply_current)
        btn_row.addWidget(self.apply_btn)
        self.delete_btn = QtWidgets.QPushButton(tr("🗑 删除这份档案"))
        set_button_kind(self.delete_btn, "danger")
        self.delete_btn.clicked.connect(self._delete_current)
        btn_row.addWidget(self.delete_btn)
        self.clear_btn = QtWidgets.QPushButton(tr("清空全部"))
        set_button_kind(self.clear_btn, "danger")
        self.clear_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch()
        close_btn = QtWidgets.QPushButton(tr("关闭"))
        set_button_kind(close_btn, "neutral")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)
        apply_button_styles(self, theme_for(self))

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
        self._update_header()
        if self.records:
            row = min(max(0, keep_row), len(self.records) - 1)
            # 先把信号关着选行,再手动刷一次:不管这一行是不是原来那行,都只渲染一遍
            # (行没变时 currentRowChanged 不发信号,不手动刷就会停在旧内容上;
            #  行变了信号又会再发一次,开着信号选行就会渲染两遍 —— 日志有几千行,别浪费)
            self.list_widget.blockSignals(True)
            self.list_widget.setCurrentRow(row)
            self.list_widget.blockSignals(False)
            self._on_select(row)
        else:
            self.detail.setPlainText(tr("还没有任何迁移记录。"))
            self.log_view.clear()
            self.log_count.setText("")
            self._set_buttons_for(None)

    def _update_header(self):
        """头部一行:共 N 份档案 · 最多保存 M 份(可在设置里调整) · 档案目录。同 Tk 版。"""
        try:
            limit = history_store.get_limit()
            where = history_store._root()
        except Exception:
            limit, where = "?", ""
        self.header.setText(trp("共 {0} 份档案　·　最多保存 {1} 份（可在设置里调整）　·　{2}",
                                len(self.records), limit, where))

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
        self._show_log(rec)
        self._set_buttons_for(rec)

    # ---------- 日志(按级别上色) ----------
    def _theme(self):
        return getattr(self.window, "theme", None) or {}

    def _level_colors(self):
        t = self._theme()
        return {
            "INFO": t.get("log_info_fg", "#808080"),
            "WARNING": t.get("log_warning_fg", "#e65100"),
            "ERROR": t.get("log_error_fg", "#c62828"),
            "SUCCESS": t.get("log_success_fg", "#2e7d32"),
            "SIMULATE": t.get("log_simulate_fg", "#1565c0"),
        }

    def _show_log(self, rec):
        """右下角完整日志:新档案按存下来的级别上色,旧档案按行里的图标 / 关键字推断。

        列表页拿到的记录不带 log(太大),所以这里按 id 再读一次完整档案。
        """
        from core.history_view import log_lines
        from PySide6 import QtGui
        self.log_view.clear()
        if not rec:
            self.log_count.setText("")
            return
        full = None
        try:
            full = history_store.load_record(str(rec.get("id") or ""))
        except Exception:
            full = None
        lines, count = log_lines(full or rec)
        if not lines:
            self.log_view.setPlainText(tr("（这份档案没有日志）"))
            self.log_count.setText("")
            return
        colors = self._level_colors()
        cursor = self.log_view.textCursor()
        fmts = {}
        for level, text in lines:
            fmt = fmts.get(level)
            if fmt is None:
                fmt = QtGui.QTextCharFormat()
                fmt.setForeground(QtGui.QColor(colors.get(level, colors["INFO"])))
                fmts[level] = fmt
            cursor.insertText(text + "\n", fmt)
        self.log_view.moveCursor(QtGui.QTextCursor.Start)
        self.log_count.setText(trp("{0} 行", count))

    def _toggle_zoom(self):
        """放大 / 还原日志:放大时藏起左边的列表和上面的概览,让日志占满窗口(同 Tk,不另开窗口)。"""
        on = self.zoom_btn.isChecked()
        self.list_widget.setVisible(not on)
        self.detail.setVisible(not on)
        self.zoom_btn.setText(tr("⤡  还原") if on else tr("⤢  放大"))
    # ---------- 详情 ----------
    def _detail_html(self, rec):
        """概览:标题(时间 · 类型)、结果、源 / 目标、四行统计(数字上色)、耗时、失败条数。"""
        import html
        from core.history_view import stat_rows, result_kind, failed_count
        t = self._theme()
        muted = t.get("muted_fg", "#888")
        num = t.get("data_num_fg", "#6a1b9a")
        warn = t.get("log_warning_fg", "#e65100")
        ok = t.get("ok_fg", "#2e7d32")
        bad = t.get("fail_fg", "#c62828")

        def esc(v):
            return html.escape(str(v))

        def span(color, text):
            return "<span style='color:%s'>%s</span>" % (color, esc(text))

        rows = []

        def add(label, value_html):
            rows.append("<tr><td style='color:%s;padding-right:12px;vertical-align:top'>%s</td>"
                        "<td>%s</td></tr>" % (muted, esc(label), value_html))

        mode = tr("模拟运行") if rec.get("mode") == "dry" else tr("实际迁移")
        result = rec.get("result") or ""
        kind_color = {"ok": ok, "warn": warn, "fail": bad}[result_kind(rec)]
        title = trp("{0}　·　{1}", rec.get("time", "?"), mode)
        head = ("<div style='font-size:15px;font-weight:bold'>%s</div>"
                "<div style='color:%s'>%s</div>"
                % (esc(title), kind_color, esc(tr(result) if result else "—")))
        if rec.get("rolled_back"):
            add(tr("已回滚"), esc(rec.get("rollback_time") or tr("是")))
        add(tr("世界"), esc(rec.get("world") or tr("(无)")))
        add(tr("源目录"), esc(rec.get("source") or "—"))
        add(tr("目标目录"), esc(rec.get("target") or "—"))
        for name, success, skipped, failed in stat_rows(rec):
            parts = [span(num, success), span(muted, tr(" 个"))]
            if skipped is not None:
                parts += [span(muted, tr("　跳过 ")), span(warn if skipped else muted, skipped)]
            parts += [span(muted, tr("　失败 ")), span(bad if failed else ok, failed)]
            add(tr(name), "".join(parts))
        duration = rec.get("duration")
        add(tr("耗时"), span(num, trp("{0} 秒", duration if duration is not None else "—")))
        nfail = failed_count(rec)
        add(tr("失败"), span(bad, trp("{0} 条（详见日志）", nfail)) if nfail else span(ok, tr("无")))

        table = "<table style='border-collapse:collapse'>%s</table>" % "".join(rows)
        return "<div style='font-family:sans-serif;font-size:13px'>%s%s</div>" % (head, table)

    def _set_buttons_for(self, rec):
        """回滚钮只有在「这条记录有自己的备份」时才可点(早期记录没有)。"""
        busy = self._rb_thread is not None
        has = bool(rec) and not busy
        self.delete_btn.setEnabled(has)
        self.apply_btn.setEnabled(has)                    # 没选中 / 回滚在跑时都不能应用
        self.clear_btn.setEnabled(bool(self.records) and not busy)
        can_rb = False
        if rec and not busy:
            can_rb = self._record_backup_dir(rec) is not None
        self.rollback_btn.setEnabled(can_rb)
        if rec and not can_rb and not busy:
            self.rollback_btn.setToolTip(tr("这条记录没有自己的备份,无法回滚"))
        else:
            self.rollback_btn.setToolTip("")

    def _apply_current(self):
        """把选中的那份档案回填到主界面,成功了就关掉历史窗(同 Tk 版:应用完窗口就收)。

        只回填、不启动迁移。回填失败(档案读不出来 / 没清单 / 主界面在忙)时窗口留着,
        主窗口已经弹了原因,用户可以换一份再试。
        """
        rec = self._current()
        if rec is None or self._rb_thread is not None:
            return
        if self.window._apply_record(rec, source=tr("历史里选中的那份档案")):
            self.accept()

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
                self, tr("删除档案"),
                trp("确定删除 {0} 这份档案吗？\n（只删记录，不影响任何游戏文件）",
                    rec.get("time", ""))) != QtWidgets.QMessageBox.Yes:
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

        # 这份备份里实际有哪些类别 → 让用户勾(按记住的 rollback_parts 预勾)
        parts = pick_rollback_parts(self, self.window, backup_dir)
        if not parts:
            return

        if not self._confirm_rollback(rec, parts):
            return

        self._start_rollback_worker(tgt, str(backup_dir),
                                    str(rec.get("id") or ""), parts)

    def _confirm_rollback(self, rec, parts):
        """回滚是破坏性的:要把话说全(还原哪次、会覆盖什么、不可撤销)。文案同 Tk 版。"""
        when = str(rec.get("time") or "")
        head = (trp("要把目标实例恢复到「{0}」这次迁移之前吗？", when) if when
                else tr("要把目标实例恢复到最近一次迁移之前吗？"))
        body = (head + "\n\n"
                + tr("会用备份覆盖当前内容：mods / config / saves 以及「其它文件」清单里复制过的部分"
                     "都会还原，迁移前不存在的东西会被删掉。")
                + "\n\n" + trp("本次还原：{0}", " / ".join(sorted(parts)))
                + "\n\n" + tr("此操作不可撤销。回滚过程看主界面的执行日志。"))
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle(tr("↩ 回滚到迁移前"))
        box.setText(body)
        go = box.addButton(tr("回滚"), QtWidgets.QMessageBox.AcceptRole)
        cancel = box.addButton(tr("先不滚"), QtWidgets.QMessageBox.RejectRole)
        set_button_kind(go, "danger")
        set_button_kind(cancel, "neutral")
        apply_button_styles(box, theme_for(self))
        box.setDefaultButton(box.buttons()[-1])          # 默认落在「先不滚」,回车不会误回滚
        box.exec()
        return box.clickedButton() is go

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
        self._refresh_main_busy()

    def _refresh_main_busy(self):
        """回滚开始 / 结束时让主窗口按最新忙态重新锁定 / 解锁(主窗口的 busy_task_name 会问回滚)。"""
        fn = getattr(self.window, "_refresh_busy_state", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    @QtCore.Slot(bool, str)
    def _on_rollback_done(self, success, record_id):
        # 收尾线程
        if self._rb_thread:
            self._rb_thread.quit()
            self._rb_thread.wait()
        self._rb_thread = None
        self._rb_worker = None
        self.rollback_btn.setText(tr("⏪ 回滚到这一次之前"))
        self._refresh_main_busy()

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

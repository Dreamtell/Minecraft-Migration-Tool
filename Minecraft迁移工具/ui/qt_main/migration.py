# ui/qt_main/migration.py
"""Qt版迁移执行:后台QThread worker + 进度对话框。

与Tk版(mw_migration.py)的区别:
  - Tk版用 root.after 队列泵把worker线程的日志/进度送回主线程
  - Qt版用 Signal:worker在子线程emit,Qt按AutoConnection自动队列到主线程
    (这是Qt线程安全的标准做法,不能在worker线程里直接碰控件)

调用的core业务层和Tk版完全一致:
  do_backup → prune_legacy_backup → run_migration
"""
import threading
from pathlib import Path

from PySide6 import QtWidgets, QtCore, QtGui
from utils.i18n import tr, trp


class MigrationWorker(QtCore.QObject):
    """后台迁移worker。放进QThread里跑,通过信号把进度/日志/结果送回主线程。"""

    # (message, level) —— 一条日志
    log_signal = QtCore.Signal(str, str)
    # (index, filename, size_bytes, step_text) —— 一次进度更新。
    # step 用 str 而非 object:跨线程队列连接传内置类型最稳,object 在部分
    # PySide6 版本要先 qRegisterMetaType,str 则天然支持。
    progress_signal = QtCore.Signal(int, str, float, str)
    # (success, error_message) —— 跑完了
    finished_signal = QtCore.Signal(bool, str)

    def __init__(self, params):
        super().__init__()
        self.params = params
        self.cancel_event = params.get("cancel_event") or threading.Event()

    def _log(self, message, level="INFO"):
        """给core当log_callback:在worker线程里被调,emit信号送回主线程。"""
        self.log_signal.emit(str(message), str(level))

    def _progress(self, index, filename, size, step=None):
        """给core当progress_callback。

        core 有两种调用:4参(index,label,bytes,step文案) 和 3参收尾(None,None,None)。
        step 默认值兜住3参调用;index=None 的收尾不emit(进度窗由finished_signal关)。
        """
        if index is not None:
            self.progress_signal.emit(int(index), str(filename or ""),
                                      float(size or 0), str(step or ""))

    @QtCore.Slot()
    def run(self):
        """在QThread里执行迁移(镜像Tk版 _run_migration_thread 的流程)。"""
        from core.migrator import (run_migration, do_backup, prune_legacy_backup,
                                    MigrationCancelled)
        p = self.params
        event = self.cancel_event
        success, error_message = False, None
        try:
            if not p["dry_run"]:
                # 备份:迁移会动的部分(mods/config/saves + 其它文件清单)
                do_backup(p["tgt_path"], log_func=self._log,
                          extra_entries=p["extralist"], record_id=p["record_id"],
                          check_cancel=event.is_set)
                if event.is_set():
                    raise MigrationCancelled()
                prune_legacy_backup(p["tgt_path"], log_func=self._log)
            success = run_migration(
                p["src_path"], p["tgt_path"], p["world"], p["modlist"],
                p["configlist"], p["dry_run"], p["overwrite"],
                progress_callback=self._progress, log_callback=self._log,
                check_cancel=event.is_set, record_id=p["record_id"],
                plan=p.get("plan"), rename_marker=p.get("marker"),
                extralist=p["extralist"], extra_overwrite=p["extra_overwrite"])
        except MigrationCancelled:
            self._log(tr("⚠️ 用户取消了迁移"), "WARNING")
        except Exception as error:
            error_message = str(error)
            self._log(trp("❌ 迁移任务失败:{0}", error), "ERROR")
        finally:
            self.finished_signal.emit(bool(success), error_message or "")

    def cancel(self):
        """请求取消(主线程调,worker通过check_cancel看到)。"""
        self.cancel_event.set()


class PrepareWorker(QtCore.QObject):
    """后台准备:建迁移计划 + 磁盘空间检查(对齐 Tk 版 _prepare_migration)。

    建计划要遍历源目录、stat 每个文件,大整合包要几秒,不能在主线程跑。
    结果通过 finished_signal(object) 送回主线程:
      {"plan": dict, "disk": (够不够, 剩余字节, 需要字节)} 或 {"error": str}
    """

    finished_signal = QtCore.Signal(object)

    def __init__(self, params):
        super().__init__()
        self.params = params

    @QtCore.Slot()
    def run(self):
        import shutil
        from core.migration_plan import build_migration_plan, backup_size
        from core.migrator import MigrationCancelled
        p = self.params
        cancel = p["cancel_event"].is_set
        try:
            plan = build_migration_plan(
                p["src_path"], p["tgt_path"], p["world"], p["modlist"],
                p["configlist"], p["extralist"], p["overwrite"],
                p["extra_overwrite"], p.get("marker"), cancel)
            if p["dry_run"]:
                disk = (True, -1, -1)
            else:
                try:
                    free = shutil.disk_usage(str(p["tgt_path"])).free
                    need = int((plan["total_size"] + backup_size(
                        p["tgt_path"], p["extralist"], cancel)) * 1.2) + 100 * 1024 * 1024
                    disk = (free >= need, free, need)
                except OSError:
                    disk = (True, -1, -1)
            self.finished_signal.emit({"plan": plan, "disk": disk})
        except MigrationCancelled:
            self.finished_signal.emit({"error": tr("用户取消了迁移"), "cancelled": True})
        except Exception as e:
            self.finished_signal.emit({"error": str(e)})


def ask_confirm(parent, info):
    """正式迁移前的二次确认(对齐 Tk 版 ask_migrate_confirm 展示的内容)。返回 bool。"""
    mb = 1024 * 1024
    lines = [
        trp("源:{0}", info["src"]),
        trp("目标:{0}", info["tgt"]),
        trp("存档:{0}", info["world"] or tr("(不迁移存档)")),
        "",
        trp("模组 {0} 个 · config {1} 条 · 其它文件 {2} 条",
            info["mods"], info["configs"], info["extras"]),
        trp("共 {0} 个文件,约 {1:.1f} MB", info["files"], info["size"] / mb),
        "",
        trp("同名模组:{0}", tr("覆盖") if info["overwrite"] else tr("跳过")),
        trp("其它文件同名:{0}",
            tr("覆盖(先备份)") if info["extra_overwrite"] else tr("跳过")),
        "",
        tr("迁移前会先自动备份目标实例,出问题可以在「查看历史」里回滚。"),
    ]
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(tr("确认开始迁移"))
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setText(tr("即将对目标实例执行真实迁移:"))
    box.setInformativeText("\n".join(lines))
    go = box.addButton(tr("开始迁移"), QtWidgets.QMessageBox.AcceptRole)
    box.addButton(tr("先不开始"), QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(go)
    box.exec()
    return box.clickedButton() is go


def show_precheck(parent, result):
    """展示自检结果。有 fail → 拦下返回 False;只有 warn → 问继续与否;否则 True。"""
    items = result.get("items") or []
    fails = [c["text"] for c in items if c.get("level") == "fail"]
    warns = [c["text"] for c in items if c.get("level") == "warn"]
    if fails:
        QtWidgets.QMessageBox.critical(
            parent, tr("迁移前自检没通过"),
            tr("下面这些不解决,迁移跑不起来:") + "\n\n"
            + "\n".join("· " + x for x in fails[:12]))
        return False
    if warns:
        r = QtWidgets.QMessageBox.warning(
            parent, tr("迁移前自检有提醒"),
            tr("这些不影响运行,但结果可能不是你要的:") + "\n\n"
            + "\n".join("· " + x for x in warns[:12]) + "\n\n" + tr("还是要现在开始吗?"),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes)
        return r == QtWidgets.QMessageBox.Yes
    return True


class ProgressDialog(QtWidgets.QDialog):
    """迁移进度对话框(非模拟运行时显示)。

    对应Tk版的 ui/dialogs.ProgressWindow,但用Qt原生控件。
    """

    def __init__(self, parent, total_files, total_size, theme=None):
        super().__init__(parent)
        self.theme = theme or {}
        self.total_files = total_files
        self.total_size = max(1, total_size)
        self._cancel_cb = None
        self.setWindowTitle(tr("迁移进度"))
        self.setMinimumWidth(480)
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.step_label = QtWidgets.QLabel(tr("准备中..."))
        f = self.step_label.font()
        f.setBold(True)
        self.step_label.setFont(f)
        layout.addWidget(self.step_label)

        self.file_label = QtWidgets.QLabel("")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)

        self.stats_label = QtWidgets.QLabel(
            trp("0 / {0} 个文件", total_files))
        layout.addWidget(self.stats_label)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QtWidgets.QPushButton(tr("取消迁移"))
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

    def set_cancel_callback(self, cb):
        self._cancel_cb = cb

    def _on_cancel(self):
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText(tr("正在取消..."))
        if self._cancel_cb:
            self._cancel_cb()

    @QtCore.Slot(int, str, float, str)
    def update_progress(self, index, filename, size, step):
        """收到worker的进度信号(已在主线程)。"""
        pct = int(min(100, index * 100 / max(1, self.total_files)))
        self.bar.setValue(pct)
        if step:
            self.step_label.setText(str(step))
        self.file_label.setText(filename)
        self.stats_label.setText(trp("{0} / {1} 个文件", index, self.total_files))

    def closeEvent(self, event):
        # 关闭进度窗 = 取消(和Tk版一致,点X要先确认由调用方处理)
        if self._cancel_cb:
            self._cancel_cb()
        event.accept()


# ui/qt_main/pack_warning.py
"""整合包更新提醒（Qt 版，对齐 Tk 版 ui/pack_warning.py）：只能收起、不能关。

为什么必须常驻：「扫描差异」只比对清单里的文件名和目标目录里实际存在的文件。
作者更新整合包时悄悄删掉或替换某个模组，我们的清单里本来就没有它，扫出来是「没有差异」，
实际却少了东西。所以这条提醒不提供「不再提示」，最多收成标签行右端的一颗 ⚠ 药丸。

形态：
  · 展开：清单页签上方一条红色警示条（⚠ + 说明 + 「复制 AI 提示词」+「收起 ▴」）
  · 收起：整条摘掉，标签行右端出现「⚠ 更新提醒」药丸，点开是完整说明弹窗
文案与提示词沿用 Tk 版（同一批 i18n 词条），配置键 pack_warning_collapsed 与 Tk 共用。

⚠ 只 import PySide6 与 utils.i18n，不碰 tkinter。
"""
import sys
from pathlib import Path

_QT_DIR = Path(__file__).resolve().parent.parent.parent / "_qt"
if _QT_DIR.is_dir() and str(_QT_DIR) not in sys.path:
    sys.path.insert(0, str(_QT_DIR))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ui.qt_win import fit_window
from utils.i18n import tr, trp  # noqa: E402

# 文案原文与 Tk 版 ui/pack_warning.py 逐字一致（i18n 表里已有这些词条）
BANNER_TEXT = (
    "整合包更新时，作者可能会删除或替换模组 —— 这种情况下「扫描差异」只能比出文件名和"
    "版本的不同，被作者删掉的模组是发现不了的。\n"
    "请务必核对更新日志里的模组变更；也可以把更新日志交给 AI 帮你逐条分析。")

FULL_TEXT = (
    "「扫描差异」只比对两件事：清单里写的文件名，和目标目录里实际存在的文件。\n\n"
    "作者更新整合包时如果删掉或替换了某个模组，我们的清单里本来就没有它 —— "
    "扫出来是「没有差异」，实际却少了东西。\n\n"
    "建议的做法：\n"
    "① 打开整合包的更新日志，逐条核对模组变更；\n"
    "② 或者点下面的「复制 AI 提示词」，把提示词连同更新日志一起交给 AI 逐条分析。")

PROMPT = (
    "我在把一个 Minecraft 整合包从旧版本迁移到新版本，需要你帮我从下面的"
    "更新日志里找出【与模组相关的变更】。\n\n"
    "整合包：{0}\n旧实例目录：{1}\n新实例目录：{2}\n\n"
    "请按这个格式输出：\n"
    "1. 新增的模组（尽量给出 modid 或中文名）\n"
    "2. 移除的模组\n"
    "3. 被替换/合并的模组（A 换成了 B）\n"
    "4. 依赖或前置发生变化的模组\n"
    "5. 需要我手动处理的注意点（配置重置、存档兼容性、存档版本）\n\n"
    "更新日志如下：\n（把更新日志粘贴在这里）\n")


def build_prompt(source, target):
    """「复制 AI 提示词」的正文：整合包名取源目录最后一段。"""
    source, target = (source or "").strip(), (target or "").strip()
    name = source.replace("\\", "/").rstrip("/").split("/")[-1] or tr("整合包")
    return trp(PROMPT, name, source or tr("（未选择）"), target or tr("（未选择）"))


def palette(dark):
    """红色是「警示语义色」，不随主题变。深浅两套的取值与 Tk 版一致。"""
    if dark:
        return {"bg": "#4d1517", "edge": "#f0545a", "text": "#ffd9d9", "icon": "#ff6b70",
                "main": "#ff9c9c", "weak": "#c89595", "hover": "#5f1b1e"}
    return {"bg": "#f7cfc7", "edge": "#b71c1c", "text": "#5d0f0b", "icon": "#b71c1c",
            "main": "#a51e18", "weak": "#8a5a55", "hover": "#f1bdb2"}


class _LinkLabel(QtWidgets.QLabel):
    """下划线小链接（点击发 clicked 信号，悬停换底色）。"""

    clicked = QtCore.Signal()

    def __init__(self, text, color, hover_bg, bg, parent=None):
        super().__init__(text, parent)
        self._style = "color: %s; background: %s; text-decoration: underline; padding: 2px 4px;"
        self._color, self._hover, self._bg = color, hover_bg, bg
        self.setStyleSheet(self._style % (color, bg))
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def enterEvent(self, e):
        self.setStyleSheet(self._style % (self._color, self._hover))
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setStyleSheet(self._style % (self._color, self._bg))
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class PackWarningBar(QtWidgets.QFrame):
    """红色警示条。collapsed_changed(bool) 在用户收起 / 展开时发出，由主窗口负责摆位和存配置。"""

    collapsed_changed = QtCore.Signal(bool)

    def __init__(self, parent=None, dark=True, get_paths=None, collapsed=False):
        super().__init__(parent)
        self._dark = bool(dark)
        self._get_paths = get_paths or (lambda: ("", ""))
        self._collapsed = bool(collapsed)
        self._dialog = None
        self._copy_label = None
        self.setObjectName("packWarningBar")
        self._build()

    # ---------- 状态 ----------
    def is_collapsed(self):
        return self._collapsed

    def set_collapsed(self, collapsed, emit=True):
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if emit:
            self.collapsed_changed.emit(collapsed)

    def set_dark(self, dark):
        self._dark = bool(dark)
        self._build()
        if self._dialog is not None:
            self._dialog.close()

    # ---------- 横幅 ----------
    def _build(self):
        old = self.layout()
        if old is not None:
            while old.count():
                item = old.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            QtWidgets.QWidget().setLayout(old)     # 摘下旧布局，下面才能装新的
        c = palette(self._dark)
        self.setStyleSheet(
            "QFrame#packWarningBar { background: %s; border: 1px solid %s;"
            " border-left: 4px solid %s; }" % (c["bg"], c["edge"], c["edge"]))
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        icon = QtWidgets.QLabel("⚠")
        icon.setStyleSheet("color: %s; font-size: 18px; background: transparent; border: none;"
                           % c["icon"])
        icon.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(icon)
        text = QtWidgets.QLabel(tr(BANNER_TEXT))
        text.setWordWrap(True)
        text.setStyleSheet("color: %s; background: transparent; border: none;" % c["text"])
        row.addWidget(text, 1)
        side = QtWidgets.QVBoxLayout()
        self._copy_label = _LinkLabel(tr("复制 AI 提示词"), c["main"], c["hover"], c["bg"])
        self._copy_label.clicked.connect(self.copy_prompt)
        fold = _LinkLabel(tr("收起 ▴"), c["weak"], c["hover"], c["bg"])
        fold.clicked.connect(lambda: self.set_collapsed(True))
        side.addWidget(self._copy_label)
        side.addWidget(fold)
        side.addStretch()
        row.addLayout(side)

    # ---------- 复制提示词 ----------
    def copy_prompt(self):
        try:
            source, target = self._get_paths()
        except Exception:
            source = target = ""
        QtWidgets.QApplication.clipboard().setText(build_prompt(source, target))
        self._flash_copied()

    def _flash_copied(self):
        lbl = self._copy_label
        if lbl is None:
            return
        c = palette(self._dark)
        lbl.setText(tr("已复制 ✓"))
        lbl._color = c["edge"]
        lbl.setStyleSheet(lbl._style % (c["edge"], c["bg"]))

        def restore():
            try:
                lbl.setText(tr("复制 AI 提示词"))
                lbl._color = c["main"]
                lbl.setStyleSheet(lbl._style % (c["main"], c["bg"]))
            except RuntimeError:        # 横幅在 1.6 秒内被重建了
                pass
        QtCore.QTimer.singleShot(1600, restore)

    # ---------- 完整说明弹窗 ----------
    def open_dialog(self):
        if self._dialog is not None:
            try:
                self._dialog.raise_()
                self._dialog.activateWindow()
                return
            except RuntimeError:
                self._dialog = None
        c = palette(self._dark)
        dlg = QtWidgets.QDialog(self.window())
        dlg.setWindowTitle(tr("整合包更新提醒"))
        fit_window(dlg, 520, 280, 640, 380)
        v = QtWidgets.QVBoxLayout(dlg)
        head = QtWidgets.QFrame()
        head.setStyleSheet("QFrame { background: %s; border: 1px solid %s; }"
                           % (c["bg"], c["edge"]))
        hrow = QtWidgets.QHBoxLayout(head)
        icon = QtWidgets.QLabel("⚠")
        icon.setStyleSheet("color: %s; font-size: 22px; border: none;" % c["icon"])
        hrow.addWidget(icon)
        col = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel(tr("整合包更新提醒"))
        title.setStyleSheet("color: %s; font-weight: bold; font-size: 14px; border: none;"
                            % c["text"])
        sub = QtWidgets.QLabel(tr("这条提醒不能关闭，只能收起成一个 ⚠ 图标"))
        sub.setStyleSheet("color: %s; font-size: 11px; border: none;" % c["weak"])
        col.addWidget(title)
        col.addWidget(sub)
        hrow.addLayout(col, 1)
        v.addWidget(head)
        body = QtWidgets.QLabel(tr(FULL_TEXT))
        body.setWordWrap(True)
        v.addWidget(body)
        btns = QtWidgets.QHBoxLayout()
        copy_btn = QtWidgets.QPushButton(tr("复制 AI 提示词"))
        copy_btn.clicked.connect(self.copy_prompt)
        btns.addWidget(copy_btn)
        if self._collapsed:
            expand = QtWidgets.QPushButton(tr("展开成横幅"))
            expand.clicked.connect(lambda: (dlg.close(), self.set_collapsed(False)))
            btns.addWidget(expand)
        btns.addStretch()
        close = QtWidgets.QPushButton(tr("关闭"))
        close.clicked.connect(dlg.close)
        btns.addWidget(close)
        v.addLayout(btns)
        dlg.finished.connect(lambda *_: setattr(self, "_dialog", None))
        self._dialog = dlg
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        dlg.show()

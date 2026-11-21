"""Compact, clickable log overview for the Qt main window."""

from PySide6 import QtCore, QtGui, QtWidgets


_LEVELS = {
    2: ("WARNING", "log_warning_fg", 1),
    3: ("ERROR", "log_error_fg", 0),
    4: ("SUCCESS", "log_success_fg", 2),
    5: ("SIMULATE", "log_simulate_fg", 3),
}
_TICK_H = 4
_MIN_BOX_H = 12
_HIT_PAD = 2


def _mix(a, b, amount):
    ca, cb = QtGui.QColor(a), QtGui.QColor(b)
    return QtGui.QColor(*(round(x + (y - x) * amount) for x, y in zip(
        (ca.red(), ca.green(), ca.blue()), (cb.red(), cb.green(), cb.blue()))))


class LogOverview(QtWidgets.QWidget):
    """Paint log-level marks and a draggable viewport indicator beside a QTextEdit."""

    def __init__(self, editor, theme, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.theme = dict(theme)
        self._pixmap = QtGui.QPixmap()
        self._markers = []
        self._segments = []
        self._dragging = False
        self._grab_offset = 0.0
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(80)
        self._refresh_timer.timeout.connect(self._rebuild)
        self.setFixedWidth(14)
        self.setMinimumHeight(1)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._editor_viewport = self.editor.viewport()

        document = self.editor.document()
        document.contentsChanged.connect(self.schedule_rebuild)
        document.documentLayout().documentSizeChanged.connect(self.schedule_rebuild)
        self.editor.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.editor.verticalScrollBar().rangeChanged.connect(self.schedule_rebuild)
        self.editor.installEventFilter(self)
        self._editor_viewport.installEventFilter(self)
        self.schedule_rebuild()

    def set_theme(self, theme):
        self.theme = dict(theme)
        self.schedule_rebuild()

    def schedule_rebuild(self, *_args):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def eventFilter(self, watched, event):
        if (watched is self.editor or watched is self._editor_viewport) and \
                event.type() == QtCore.QEvent.Resize:
            self.schedule_rebuild()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_rebuild()

    def _view_fraction(self):
        view_h = max(1, self.editor.viewport().height())
        doc_h = max(float(self.editor.document().documentLayout().documentSize().height()),
                    float(view_h))
        return min(1.0, view_h / doc_h)

    def _viewport_center_fraction(self):
        bar = self.editor.verticalScrollBar()
        maximum = bar.maximum()
        view_fraction = self._view_fraction()
        top = (bar.value() / maximum) * (1.0 - view_fraction) if maximum else 0.0
        return top + view_fraction / 2.0

    def _viewport_box(self):
        bar = self.editor.verticalScrollBar()
        height = max(1, self.height())
        maximum = max(0, bar.maximum())
        view_fraction = self._view_fraction()
        if maximum == 0 or view_fraction >= 1.0:
            return None

        box_h = max(_MIN_BOX_H, min(height, height * view_fraction))
        center = self._viewport_center_fraction() * height
        # Markers are painted on integer rows.  Expanding the hit box to the
        # surrounding integer rows keeps a marker and its viewport indicator
        # aligned at fractional document positions (notably at 125% scaling).
        y0 = max(0.0, min(height - box_h, center - box_h / 2.0))
        y1 = min(float(height), y0 + box_h)
        return float(max(0, int(y0))), float(min(height, int(y1 + 0.9999)))

    def _rebuild(self):
        width, height = max(1, self.width()), max(1, self.height())
        dpr = max(1.0, self.devicePixelRatioF())
        pixmap = QtGui.QPixmap(round(width * dpr), round(height * dpr))
        pixmap.setDevicePixelRatio(dpr)
        painter = QtGui.QPainter(pixmap)
        bg = QtGui.QColor(self.theme.get("bg", "#2e2e2e"))
        fg = QtGui.QColor(self.theme.get("fg", "#ffffff"))
        painter.fillRect(QtCore.QRectF(0, 0, width, height), _mix(bg, fg, 0.10))

        doc = self.editor.document()
        doc_layout = doc.documentLayout()
        doc_h = max(1.0, float(doc_layout.documentSize().height()))
        markers = []
        block = doc.begin()
        while block.isValid():
            state = block.userState()
            if state in _LEVELS:
                rect = doc_layout.blockBoundingRect(block)
                # Match Tk's line-start position: a wrapped paragraph is marked at its
                # first visual line, not at the paragraph's vertical midpoint.
                y = max(0, min(height - 1, round(rect.top() * height / doc_h)))
                markers.append((y, block.blockNumber(), state))
            block = block.next()

        ranks = {state: level[2] for state, level in _LEVELS.items()}
        rows = [None] * height
        for marker in markers:
            y, _block_number, state = marker
            y0 = max(0, min(height - _TICK_H, round(y - _TICK_H / 2)))
            y1 = min(height, y0 + _TICK_H)
            for row in range(y0, y1):
                current = rows[row]
                if current is None or ranks[state] < ranks[current[0]]:
                    rows[row] = (state, [marker])
                elif state == current[0] and marker not in current[1]:
                    current[1].append(marker)

        palette = {state: QtGui.QColor(self.theme.get(key, "#888888"))
                   for state, (_name, key, _priority) in _LEVELS.items()}
        segments = []
        row = 0
        while row < height:
            current = rows[row]
            if current is None:
                row += 1
                continue
            state = current[0]
            y0 = row
            marks = list(current[1])
            row += 1
            while row < height and rows[row] is not None and rows[row][0] == state:
                for marker in rows[row][1]:
                    if marker not in marks:
                        marks.append(marker)
                row += 1
            y1 = row
            marks.sort(key=lambda item: (item[0], item[1]))
            segments.append((y0, y1, state, marks))
            painter.fillRect(QtCore.QRectF(1, y0, width - 2, y1 - y0), palette[state])

        painter.end()
        self._pixmap = pixmap
        self._markers = markers
        self._segments = segments
        self.update()

    def _accent_color(self):
        """视口框的强调色。与 Tk 侧同一取法:theme 里没有 "accent" 就用 #4a90d9。

        不能用 accent_fg —— 那是「accent_bg 上的文字色」(浅色主题黑、深色白)。
        """
        return QtGui.QColor(self.theme.get("accent") or "#4a90d9")

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        if not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)
        box = self._viewport_box()
        if box is not None:
            y0, y1 = box
            accent = self._accent_color()
            fill = QtGui.QColor(accent)
            fill.setAlpha(70)
            painter.fillRect(QtCore.QRectF(1, y0, self.width() - 2, y1 - y0), fill)
            painter.setPen(QtGui.QPen(accent, 1))
            painter.drawRect(QtCore.QRectF(1.5, y0 + 0.5, self.width() - 3,
                                           max(1.0, y1 - y0 - 1)))

    def _snap(self, y):
        nearest = None
        nearest_distance = None
        for y0, y1, state, marks in self._segments:
            distance = 0 if y0 <= y < y1 else min(abs(y - y0), abs(y - y1))
            if distance <= _HIT_PAD and (nearest_distance is None or
                                         distance < nearest_distance):
                nearest = (state, marks)
                nearest_distance = distance
        if nearest is None:
            return None
        state, marks = nearest
        marker = min((item for item in marks if item[2] == state),
                     key=lambda item: abs(item[0] - y), default=None)
        return (marker[1], marker[2]) if marker is not None else None

    def _block_start_visible(self, block_number):
        block = self.editor.document().findBlockByNumber(block_number)
        if not block.isValid():
            return False
        rect = self.editor.cursorRect(QtGui.QTextCursor(block))
        return rect.bottom() >= 0 and rect.top() < self.editor.viewport().height()

    def _goto_block(self, block_number):
        block = self.editor.document().findBlockByNumber(block_number)
        if not block.isValid():
            return
        line_y = self.editor.cursorRect(QtGui.QTextCursor(block)).top()
        center_y = self.editor.viewport().height() / 2.0
        bar = self.editor.verticalScrollBar()
        bar.setValue(max(0, min(bar.maximum(), round(bar.value() + line_y - center_y))))

    def _center_on(self, center_fraction):
        bar = self.editor.verticalScrollBar()
        maximum = bar.maximum()
        view_fraction = self._view_fraction()
        travel_fraction = 1.0 - view_fraction
        if maximum <= 0 or travel_fraction <= 0:
            return
        top = max(0.0, min(travel_fraction, center_fraction - view_fraction / 2.0))
        bar.setValue(round(top / travel_fraction * maximum))

    def _frac_at(self, y):
        return max(0.0, min(1.0, float(y) / max(1, self.height())))

    def _jump_to(self, y):
        marker = self._snap(round(y))
        if marker is not None:
            self._goto_block(marker[0])
        else:
            self._center_on(self._frac_at(y))

    def _on_scroll(self, *_args):
        self.update()

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)

        y = event.position().y()
        marker = self._snap(round(y))
        box = self._viewport_box()
        if box is not None and box[0] <= y <= box[1] and \
                (marker is None or self._block_start_visible(marker[0])):
            self._dragging = True
            self._grab_offset = self._frac_at(y) - self._viewport_center_fraction()
            event.accept()
            return

        if marker is not None:
            self._goto_block(marker[0])
        else:
            self._center_on(self._frac_at(y))
        self._dragging = True
        self._grab_offset = self._frac_at(y) - self._viewport_center_fraction()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return super().mouseMoveEvent(event)
        center = self._frac_at(event.position().y()) - self._grab_offset
        self._center_on(center)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._grab_offset = 0.0
        super().mouseReleaseEvent(event)

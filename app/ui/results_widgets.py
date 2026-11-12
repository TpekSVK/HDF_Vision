"""Touch presentation for history cards and overlays."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QSizePolicy, QDateEdit
from app.ui.run_options_overlay import RunOptionsOverlay


class HistoryDateEdit(QDateEdit):
    """Keep native date editing/popups with a visible calendar affordance."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor('#b8c2cc'), 1.5))
        x, y = self.width() - 30, (self.height() - 16) // 2
        painter.drawRoundedRect(x, y, 18, 16, 2, 2)
        painter.drawLine(x, y + 5, x + 18, y + 5)
        for offset in (5, 13):
            painter.drawLine(x + offset, y - 3, x + offset, y + 2)
        for offset in (5, 9, 13):
            painter.drawPoint(x + offset, y + 10)


class HistoryCard(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('historyCard')
        self.setCheckable(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.heading = QLabel()
        self.caption = QLabel()
        self.caption.setProperty('role', 'secondary')
        text.addWidget(self.heading)
        text.addWidget(self.caption)
        layout.addLayout(text, 1)
        self.status = QLabel()
        layout.addWidget(self.status)
        for label in (self.heading, self.caption, self.status):
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.record = None

    def set_record(self, record, view_name):
        from datetime import datetime
        self.record = record
        stamp = datetime.fromtimestamp(record['ts_ms'] / 1000)
        self.heading.setText(f'{stamp:%H:%M:%S} · {view_name}')
        self.caption.setText(f'{stamp:%d.%m.} · {record["station_name"]} · {record.get("recipe") or "—"}')
        self.status.setText('OK' if record['ok'] else 'NOK')
        self.status.setStyleSheet('color: #24c76a;' if record['ok'] else 'color: #ff5353;')
        self.setAccessibleName(f'{self.heading.text()} · {self.caption.text()} · {self.status.text()}')
        self.setToolTip(self.accessibleName())


class ResultsOverlay(RunOptionsOverlay):
    def __init__(self, parent, title, *, scrollable=False):
        super().__init__(parent, 'Výsledky', title=title, scrollable=scrollable)
        self.setObjectName('resultsOverlay')
        self._content.setObjectName('resultsOverlayContent')

    def _place(self):
        parent = self.parentWidget()
        width = max(1, min(760, parent.width() - 24))
        height = max(1, min(parent.height() - 24, self._content.sizeHint().height() + 86))
        self.setGeometry(max(0, (parent.width() - width) // 2),
                         max(0, (parent.height() - height) // 2), width, height)

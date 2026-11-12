"""Station-local RUN options drawn above the image, outside its layout."""
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget


class RunOptionsOverlay(QFrame):
    closed = Signal()

    def __init__(self, image, station_name, *, title=None, scrollable=True):
        super().__init__(image)
        self.setObjectName("runOptionsOverlay")
        self.setProperty("role", "panel")
        self.setAccessibleName(f"Ďalšie možnosti · {station_name}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        header = QHBoxLayout()
        self.title = QLabel(title or f"Ďalšie možnosti · {station_name}")
        self.title.setWordWrap(True)
        self.title.setProperty("role", "panelHeader")
        header.addWidget(self.title, 1)
        self.close_button = QPushButton("Zavrieť")
        self.close_button.clicked.connect(self.hide)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        content = QWidget()
        self.controls = QVBoxLayout(content)
        self.controls.setContentsMargins(0, 0, 0, 0)
        self.controls.setSpacing(10)
        if scrollable:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            layout.addWidget(scroll)
        else:
            layout.addWidget(content, 1)
        self._content = content
        self._anchor = None
        image.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        self.hide()

    def open(self, anchor):
        self._anchor = anchor
        self._place()
        self.show()
        self.raise_()
        self.close_button.setFocus(Qt.OtherFocusReason)

    def _place(self):
        image = self.parentWidget()
        width = max(1, min(520, image.width() - 24))
        height = min(image.height() - 24, self._content.sizeHint().height() + 94)
        self.setGeometry(max(0, image.width() - width - 12),
                         max(0, image.height() - height - 12), width, max(1, height))

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()

    def eventFilter(self, watched, event):
        # Application-wide filters also run during destruction/reparenting of
        # dynamic Qt forms. No overlay work is needed for a transient wrapper.
        if not isinstance(event, QEvent):
            return False
        kind = event.type()
        if watched is self.parentWidget() and kind == QEvent.Resize:
            self._place()
        if self.isVisible():
            popup = QApplication.activePopupWidget()
            if popup is not None:
                return False  # A combo popup owns its own selection and Escape.
            if kind == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self.hide()
                if self._anchor is not None:
                    self._anchor.setFocus(Qt.OtherFocusReason)
                return True
            if kind in (QEvent.MouseButtonPress, QEvent.TouchBegin) and isinstance(watched, QWidget):
                # Ignored child presses can propagate to the underlying QLabel.
                # Hit-test the position so that propagation cannot dismiss us.
                points = event.points() if kind == QEvent.TouchBegin else []
                position = (points[0].globalPosition() if points else event.globalPosition()).toPoint()
                inside = QRect(self.mapToGlobal(QPoint()), self.size()).contains(position)
                anchor = self._anchor is not None and QRect(
                    self._anchor.mapToGlobal(QPoint()), self._anchor.size()).contains(position)
                if not inside and not anchor:
                    self.hide()
        return False

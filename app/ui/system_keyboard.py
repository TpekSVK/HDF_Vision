"""Open the existing GNOME keyboard when an editable Qt field is touched."""
import json
import logging
import os
import sys

from PySide6.QtCore import QEvent, QObject, QProcess, QTimer, Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QLineEdit,
                              QPlainTextEdit, QTextEdit, QWidget)
from shiboken6 import isValid


class SystemKeyboard(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._pending = None
        self._stopping = False
        self._enabled = all(os.environ.get(key) for key in
                            ('IBUS_ADDRESS', 'HDF_DESKTOP_UID', 'HDF_DESKTOP_GID'))
        self.process = QProcess(self)
        self.process.started.connect(self._send_pending)
        self.process.readyReadStandardError.connect(self._read_error)
        app.focusChanged.connect(self._focus_changed)
        app.aboutToQuit.connect(self.close)
        app.installEventFilter(self)

    @staticmethod
    def editable(widget):
        if not isinstance(widget, QWidget) or not isValid(widget):
            return False
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            return not widget.isReadOnly() and widget.isEnabled()
        if isinstance(widget, QComboBox):
            return widget.isEditable() and widget.isEnabled()
        return False

    def request(self, widget):
        if self._stopping or not self.editable(widget) or not widget.isVisible():
            return
        cursor = widget.inputMethodQuery(Qt.ImCursorRectangle)
        if not hasattr(cursor, 'topLeft'):
            cursor = widget.rect()
        point = cursor.topLeft().toPoint() if hasattr(cursor.topLeft(), 'toPoint') else cursor.topLeft()
        point = widget.mapToGlobal(point)
        self._pending = {'op': 'show', 'rect': [point.x(), point.y(),
                          max(1, int(cursor.width())), max(1, int(cursor.height()))]}
        if not self._enabled:
            QApplication.inputMethod().show()
            return
        if self.process.state() == QProcess.NotRunning:
            self.process.start(sys.executable, ['-m', 'app.tools.system_keyboard_helper'])
        elif self.process.state() == QProcess.Running:
            self._send_pending()

    def _send_pending(self):
        if self._pending is not None and self.process.state() == QProcess.Running:
            self.process.write((json.dumps(self._pending)+'\n').encode())

    def _focus_changed(self, old, new):
        if self.editable(new):
            QTimer.singleShot(0, lambda field=new: self.request(field))
        else:
            self._pending = {'op': 'hide'}
            self._send_pending()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.MouseButtonPress, QEvent.TouchBegin) and self.editable(watched):
            QTimer.singleShot(0, lambda field=watched: self.request(field))
        return False

    def _read_error(self):
        message = bytes(self.process.readAllStandardError()).decode(errors='replace').strip()
        if message and not self._stopping:
            logging.getLogger(__name__).warning('Systémová klávesnica: %s', message)

    def close(self):
        self._stopping = True
        if self.process.state() != QProcess.NotRunning:
            self._pending = {'op': 'hide'}
            self._send_pending()
            self.process.closeWriteChannel()
            if not self.process.waitForFinished(1500):
                self.process.terminate()
                self.process.waitForFinished(300)

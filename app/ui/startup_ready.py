"""Tell the desktop cover when the first usable application frame is ready."""
import logging
from pathlib import Path
from PySide6.QtCore import QObject, QTimer


class StartupReady(QObject):
    def __init__(self, window, ready_file):
        super().__init__(window)
        self.window = window
        self.ready_file = Path(ready_file)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(80)

    def check(self):
        handle = self.window.windowHandle()
        if not self.window.isVisible() or handle is None or not handle.isExposed():
            return
        if any(workspace.runtime_worker.busy or workspace._pending_runtime_action
               for workspace in self.window.workspaces):
            return
        # Let the completed preparation update and paint its widgets first.
        self.timer.stop()
        QTimer.singleShot(120, self.announce)

    def announce(self):
        try:
            self.ready_file.write_text('ready\n', encoding='utf-8')
        except OSError:
            logging.getLogger(__name__).exception('Unable to signal startup readiness')

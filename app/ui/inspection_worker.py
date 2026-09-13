"""One bounded worker lane; delivery is always queued onto the Qt owner thread."""
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Signal, Slot, Qt


class InspectionWorker(QObject):
    completed = Signal(str, object, object)
    _delivered = Signal(str, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='inspection')
        self.busy = False
        self._closed = False
        self._delivered.connect(self._deliver, Qt.QueuedConnection)

    def submit(self, kind, operation):
        # Called only by the owning UI thread. No queue behind the active job.
        if self.busy or self._closed:
            return False
        self.busy = True
        def work():
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, exc
            self._delivered.emit(kind, result, error)
        try:
            self._pool.submit(work)
        except Exception:
            self.busy = False
            raise
        return True

    @Slot(str, object, object)
    def _deliver(self, kind, result, error):
        self.busy = False
        self.completed.emit(kind, result, error)

    def shutdown(self):
        if self.busy:
            raise RuntimeError('Pracovník ešte dokončuje kontrolu.')
        self._closed = True
        self._pool.shutdown(wait=False)

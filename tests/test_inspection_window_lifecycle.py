"""Real queued delivery: slow hardware never occupies the Qt UI thread."""
import threading
import time
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication
from app.ui.inspection_worker import InspectionWorker
from app.services.inspection_controller import InspectionController


def drain(app, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.001)
    assert predicate()


def test_worker_has_one_job_and_delivers_on_ui_thread():
    app = QApplication.instance() or QApplication([])
    ui_thread = threading.get_ident()
    release = threading.Event()
    entered = threading.Event()
    worker = InspectionWorker()
    events = []
    worker.completed.connect(lambda *args: events.append((threading.get_ident(), args)))
    def slow():
        entered.set()
        assert release.wait(2)
        return threading.get_ident()
    assert worker.submit('cycle', slow)
    assert entered.wait(1)
    assert not worker.submit('other', lambda: pytest.fail('queued work'))
    app.processEvents()  # UI remains free while the hardware callback waits.
    assert not events and worker.busy
    release.set()
    drain(app, lambda: bool(events))
    assert events[0][0] == ui_thread
    assert events[0][1][1] != ui_thread
    assert not worker.busy
    worker.shutdown()


def test_prepare_failure_delivered_without_ready_state():
    app = QApplication.instance() or QApplication([])
    worker = InspectionWorker()
    controller = InspectionController()
    events = []
    def fail():
        raise RuntimeError('invalid profile')
    worker.completed.connect(lambda *args: events.append(args))
    worker.submit('prepare', lambda: controller.prepare(fail, lambda: None))
    drain(app, lambda: bool(events))
    assert events[0][1] is False
    assert controller.snapshot()['state'] == 'error'
    worker.shutdown()

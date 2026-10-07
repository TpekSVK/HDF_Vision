import time
from threading import Event
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication, QMessageBox

from app.services.camera_position import CameraPositionResult, PositionLimits, PositionReference
from app.models.schema import RecipeV2, RecipeView
from app.ui.troubleshooting_page import TroubleshootingPage


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def pump(app, predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents(QEventLoop.AllEvents, 5)
        if predicate():
            return
        time.sleep(.001)
    assert predicate(), 'Qt operation did not complete'


def page(app):
    view = RecipeView(id='view_1', name='Front')
    recipe = RecipeV2(views=[view])
    image = np.zeros((60, 80), np.uint8)
    ref = PositionReference('Test', recipe, view, None, image, image, PositionLimits(12, 10, .4))
    queued = []
    service = SimpleNamespace(load_reference=lambda *a: ref,
        capture=lambda *a: (image, CameraPositionResult(overall_ok=True)))
    widget = TroubleshootingPage(service, lambda kind, operation: queued.append((kind, operation)) or True)
    widget.hardware_mode = 'master'
    widget.show()
    widget.activate('Test')
    kind, operation = queued.pop()
    widget.completed(kind, operation(), None)
    app.processEvents()
    return widget, queued


def test_defaults_render_and_inclusive_ok_border(app):
    widget, queued = page(app)
    try:
        assert widget.capture_mode.currentText() == 'Manuálne'
        assert widget.interval.currentData() == 1000
        assert widget.display_mode.currentText() == 'VEDĽA SEBA'
        assert '±12 px' in widget.limits_label.text()
        widget.capture()
        kind, operation = queued.pop()
        widget.completed(kind, operation(), None)
        assert widget.status.text() == 'POLOHA KAMERY OK'
        assert '#30b66b' in widget.current_view.styleSheet()
    finally:
        widget.close()


def test_automatic_never_queues_parallel_requests_and_leaving_stops_timers(app):
    widget, queued = page(app)
    try:
        widget.capture_mode.setCurrentIndex(1)
        assert widget.capture_timer.isActive()
        widget.capture_timer.timeout.emit()
        assert widget.busy
        assert not widget.capture_timer.isActive()
        widget.capture_timer.timeout.emit()
        widget.capture()
        assert len(queued) == 1
        kind, operation = queued.pop()
        widget.completed(kind, operation(), None)
        assert widget.capture_timer.isActive()
        widget.display_mode.setCurrentIndex(1)
        assert widget.blink_timer.isActive()
        widget.hide()
        assert not widget.active
        assert not widget.capture_timer.isActive()
        assert not widget.blink_timer.isActive()
        assert widget.capture_mode.currentIndex() == 0
    finally:
        widget.close()


def test_late_result_is_discarded_and_failure_clears_old_advice(app):
    widget, queued = page(app)
    try:
        widget.capture()
        kind, operation = queued.pop()
        widget.deactivate()
        widget.completed(kind, operation(), None)
        assert widget.current is None and widget.result is None
        widget.activate('Test')
        kind, operation = queued.pop()
        widget.completed(kind, operation(), None)
        widget.directions.setText('OLD ADVICE')
        widget.capture()
        assert not widget.directions.text()
        kind, _ = queued.pop()
        widget.completed(kind, None, RuntimeError('capture timeout'))
        assert 'SPOĽAHLIVO' in widget.status.text()
        assert not widget.directions.text()
    finally:
        widget.close()


def test_switching_preserves_same_viewport_scale_and_center(app):
    widget, queued = page(app)
    try:
        widget.capture()
        kind, operation = queued.pop()
        widget.completed(kind, operation(), None)
        widget.display_mode.setCurrentIndex(1)
        app.processEvents()
        view = widget.blink_view
        before = view.transform(), view.mapToScene(view.viewport().rect().center())
        widget._blink()
        assert widget.blink_label.text() == 'AKTUÁLNA'
        assert (view.transform(), view.mapToScene(view.viewport().rect().center())) == before
        widget._blink()
        assert widget.blink_label.text() == 'GOLDEN'
        assert (view.transform(), view.mapToScene(view.viewport().rect().center())) == before
    finally:
        widget.close()


def test_main_window_pauses_run_and_defers_resume_until_diagnostic_finishes(app, tmp_path, monkeypatch):
    from app.ui.main_window import MainWindow
    from app.services.inspection_controller import InspectionState
    from app.services.camera_position import CameraPositionService
    from app.services.storage_service import save_golden
    with monkeypatch.context() as patch:
        patch.setattr(MainWindow, '_start_production', lambda self: False)
        patch.setattr(MainWindow, '_refresh_manual_light', lambda self: None)
        window = MainWindow(data_root=tmp_path)
    top = window.top_bar.layout()
    positions = [top.indexOf(button) for button in (
        window.btn_mode_run, window.mode_btn, window.btn_results, window.btn_troubleshooting)]
    assert all(index >= 0 for index in positions)
    assert positions == sorted(positions)
    assert top.indexOf(window.cmb_recipe) > positions[-1]
    save_golden(np.zeros((40, 50), np.uint8), 'default', base_dir=tmp_path)
    window.show()
    calls = []
    window.runtime.quiesce = lambda: calls.append('pause')
    window.runtime.prepare = lambda *a: calls.append('prepare')
    window.runtime.capture_mode = 'master'
    window.runtime.shutdown = lambda: calls.append('close')
    window.inspection.prepare(lambda: None, lambda: None)
    window.mode = 'RUN'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.Yes)
    release, started = Event(), Event()
    def capture(self, ref, mode):
        started.set()
        assert release.wait(2)
        return np.zeros((40, 50), np.uint8), CameraPositionResult(overall_ok=True)
    monkeypatch.setattr(CameraPositionService, 'capture', capture)
    monkeypatch.setattr(window.runtime, 'run', lambda *a: pytest.fail('diagnostic entered RUN'))
    monkeypatch.setattr(window.modbus, 'signal_result', lambda *a: pytest.fail('diagnostic Modbus OK/NOK'))
    try:
        counts = window.inspection.snapshot()['counts'].copy()
        window._request_mode('TROUBLESHOOTING')
        pump(app, lambda: window.panel_troubleshooting is not None and not window.runtime_worker.busy)
        page = window.panel_troubleshooting
        assert window.mode == 'SETUP' and window.inspection.state == InspectionState.PAUSED
        assert page.active
        assert window.stack.currentWidget() is page
        window._handle_pico_trigger('IN1')
        window._handle_modbus_trigger(1)
        assert window.inspection.snapshot()['counts'] == counts
        assert not window.cmb_recipe.isEnabled()
        page.capture_mode.setCurrentIndex(1)
        page.capture()
        assert started.wait(1)
        assert page.capture() is False
        window._request_mode('RUN')
        assert not page.active and not page.capture_timer.isActive()
        assert 'prepare' not in calls
        assert window.mode == 'SETUP'
        release.set()
        pump(app, lambda: window.mode == 'RUN' and not window.runtime_worker.busy)
        assert window.inspection.state == InspectionState.READY
        assert calls.count('prepare') == 1
        assert page.result is None  # Discard the result after navigating away.
        assert window.inspection.snapshot()['counts'] == counts
    finally:
        release.set()
        pump(app, lambda: not window.runtime_worker.busy)
        window.close()
        pump(app, lambda: window._close_ready)

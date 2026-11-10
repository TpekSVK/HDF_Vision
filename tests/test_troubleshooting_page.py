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
    service = SimpleNamespace(open_reference=lambda *a: ref,
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


@pytest.mark.parametrize('destination', ['RUN', 'SETUP', 'RESULTS', 'CLOSE', 'HIDE', 'TROUBLESHOOTING'])
def test_main_window_pauses_run_and_defers_resume_until_diagnostic_finishes(app, tmp_path, monkeypatch, destination):
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
    monkeypatch.setattr(CameraPositionService, 'prepare_session', lambda *a: None)
    window.show()
    calls = []
    monkeypatch.setattr(CameraPositionService, 'close_session', lambda self: calls.append('diagnostic_close'))
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
        assert window.stack.currentWidget() is window.panel_troubleshooting_home
        assert window.panel_troubleshooting is None
        assert not calls
        assert window.inspection.state == InspectionState.READY
        window.panel_troubleshooting_home.position_button.click()
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
        calls.clear()
        if destination == 'CLOSE':
            window.close()
        elif destination == 'TROUBLESHOOTING':
            window._request_mode('TROUBLESHOOTING')
        elif destination == 'HIDE':
            page.hide()
            app.processEvents()
        else:
            window._request_mode(destination)
        assert not page.active and not page.capture_timer.isActive()
        assert 'prepare' not in calls
        assert 'diagnostic_close' not in calls  # Never close under an active capture.
        assert window.mode == 'SETUP'
        release.set()
        pump(app, lambda: 'diagnostic_close' in calls and not window.runtime_worker.busy
             and window._pending_runtime_action is None)
        assert window.inspection.state == (InspectionState.READY if destination == 'RUN'
             else InspectionState.CLOSED if destination == 'CLOSE' else InspectionState.PAUSED)
        assert calls.count('prepare') == (1 if destination == 'RUN' else 0)
        assert calls.count('diagnostic_close') == 1
        if destination == 'RUN':
            assert calls.index('diagnostic_close') < calls.index('prepare')
        if destination == 'TROUBLESHOOTING':
            assert window.stack.currentWidget() is window.panel_troubleshooting_home
        if destination == 'RESULTS':
            assert window.stack.currentWidget() is window.panel_results
        assert page.result is None  # Discard the result after navigating away.
        assert window.inspection.snapshot()['counts'] == counts
    finally:
        release.set()
        pump(app, lambda: not window.runtime_worker.busy)
        window.close()
        pump(app, lambda: window._close_ready)


def test_arrow_layer_is_current_only_and_preserves_raw_pixels(app):
    widget, queued = page(app)
    try:
        widget.capture()
        kind, operation = queued.pop()
        image, _ = operation()
        result = CameraPositionResult(overall_ok=False, corrections=('→ POSUŇ KAMERU DOPRAVA', '↺ OTOČ KAMERU'))
        widget.completed(kind, (image, result), None)
        original = widget.current_view._pixmap_item.pixmap().toImage().copy()
        assert widget.current_view.corrections == ('→', '↺')
        widget.resize(960, 600)
        app.processEvents()
        widget.grab()  # Exercise the actual painter, including curved arrows.
        assert widget.current_view._pixmap_item.pixmap().toImage() == original
        assert np.array_equal(widget.current, image)
        widget.display_mode.setCurrentIndex(1)
        assert widget.blink_view.corrections == ()
        widget._blink()
        assert widget.blink_view.corrections == ('→', '↺')
        widget.arrow_toggle.setChecked(False)
        assert not widget.current_view.corrections and not widget.blink_view.corrections
        widget.arrow_toggle.setChecked(True)
        widget.result = CameraPositionResult(invalid_reason='Low confidence', corrections=result.corrections)
        widget._render_result()
        assert not widget.current_view.corrections and not widget.blink_view.corrections
        assert widget.limits_label.parent() is widget.golden_info
        assert widget.metrics.parent() is widget.current_info
        assert widget.directions.parent() is widget.current_info
    finally:
        widget.close()


@pytest.mark.parametrize('height', [768,704])
def test_touch_layout_and_overlay_fit_without_back_or_scroll(app, height):
    from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QScrollArea,QPushButton
    from app.ui.theme import DARK_STYLE
    app.setStyleSheet(DARK_STYLE)
    widget, queued = page(app)
    shell = QWidget()
    layout = QVBoxLayout(shell)
    header = QLabel('HDF VISION · DIAGNOSTIKA\nKamera 1 · SETUP')
    header.setFixedHeight(125)
    layout.addWidget(header)
    layout.addWidget(widget,1)
    shell.setFixedSize(1366,height); shell.show()
    widget.activate('Test')
    kind,operation=queued.pop(); widget.completed(kind,operation(),None)
    try:
        widget.capture()
        kind,operation=queued.pop()
        image,_=operation()
        result=CameraPositionResult(dx_px=28.4,dy_px=-6.2,rotation_deg=.18,confidence=.984,
            x_within_tolerance=False,y_within_tolerance=True,rotation_within_tolerance=True,
            overall_ok=False,corrections=('→ POSUŇ KAMERU DOPRAVA',))
        widget.completed(kind,(image,result),None)
        app.processEvents()
        assert not widget.findChildren(QScrollArea)
        assert not any('Späť' in b.text() for b in widget.findChildren(QPushButton))
        assert widget.images.height()>=240
        for control in [widget.capture_button,widget.automatic_button,widget.arrow_toggle,widget.details_button]:
            assert control.height()>=44
            assert shell.rect().contains(control.mapTo(shell,control.rect().bottomRight()))
        assert widget.metric_values[0].text()=='+28.40 px'
        assert widget.metric_values[3].text()=='98.4 %'
        before=widget.images.geometry()
        widget.details_button.click();app.processEvents()
        assert widget.detail_overlay.isVisible() and widget.images.geometry()==before
        widget.detail_overlay.hide()
        widget.grab().save(f'/testdeps/diagnostics-touch-{height}.png')
        widget.display_group.button(1).click();app.processEvents()
        assert widget.blink_timer.isActive()
        before=widget.blink_label.text()
        widget.blink_timer.timeout.emit()
        assert widget.blink_label.text()!=before
        widget.grab().save(f'/testdeps/diagnostics-switching-{height}.png')
    finally:
        shell.close();widget.close();app.processEvents()


def test_auto_capture_interval_remains_editable_and_applies_immediately(app):
    widget,queued=page(app)
    try:
        widget.automatic_button.click()
        assert widget.interval.isVisible() and widget.interval_label.isVisible()
        widget.interval.setCurrentIndex(widget.interval.findData(5000))
        assert widget.capture_timer.interval()==5000
        widget.interval.setCurrentIndex(widget.interval.findData(500))
        assert widget.capture_timer.interval()==500
        widget.capture_timer.timeout.emit()
        assert len(queued)==1 and widget.busy
        widget.interval.setCurrentIndex(widget.interval.findData(2000))
        assert not widget.capture_timer.isActive()
        kind,operation=queued.pop(); widget.completed(kind,operation(),None)
        assert widget.capture_timer.interval()==2000 and widget.capture_timer.isActive()
        widget.automatic_button.click()
        assert not widget.capture_timer.isActive() and not widget.interval.isVisible()
    finally:
        widget.close()

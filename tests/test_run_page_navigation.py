"""History navigation must not interrupt production or rearm the camera."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from numbers import Integral
from typing import Any

import pytest
from app.services.inspection_controller import InspectionController


_patches = []

@pytest.fixture(autouse=True)
def restore_pages():
    yield
    while _patches:
        _patches.pop().undo()


def window(mode='RUN', capture_mode='trigger'):
    from app.ui.main_window import MainWindow
    import app.ui.main_window as module
    from types import MethodType
    calls = []
    box = SimpleNamespace(Yes=1, No=2, question=lambda *args: 2)
    class History:
        def __init__(self, *args): pass
        def set_production_active(self, value): self.active = value
        def activate(self): calls.append('history')
    # Replace only dialog/page constructors; exercised methods are normal imports.
    patches = pytest.MonkeyPatch()
    patches.setattr(module, 'QMessageBox', box)
    patches.setattr(module, 'ResultsPage', History)
    _patches.append(patches)
    w = SimpleNamespace(mode=mode, capture_mode=capture_mode)
    from threading import Event
    w._recovery_cancel = Event()
    for name in ('_request_mode', '_sync_mode_chrome', 'toggle_mode', '_handle_external_trigger',
                 '_start_production', '_request_runtime_stop', '_dispatch_runtime_stop', '_runtime_completed'):
        setattr(w, name, MethodType(getattr(MainWindow, name), w))
    w.inspection = InspectionController()
    if mode == 'RUN': w.inspection.prepare(lambda: None, lambda: None)
    w.lbl_status = SimpleNamespace(setText=lambda text: calls.append(('status', text)))
    w.trigger_rejected = SimpleNamespace(emit=lambda text: calls.append(('rejected', text)))
    w.panel_run = object(); w.panel_setup = object(); w.panel_results = None
    w.stack = SimpleNamespace(page=w.panel_run if mode == 'RUN' else w.panel_setup)
    w.stack.currentWidget = lambda: w.stack.page
    w.stack.setCurrentWidget = lambda page: setattr(w.stack, 'page', page)
    w.stack.addWidget = lambda page: None
    def button():
        b = SimpleNamespace(checked=False)
        b.setChecked = lambda v: setattr(b, 'checked', v)
        b.setEnabled = lambda v: None
        b.setText = lambda v: None
        return b
    w.chk_filtered_roi = button()
    w.btn_mode_run = button(); w.mode_btn = button(); w.btn_results = button(); w.btn_live = button()
    w.db = SimpleNamespace(db_path='unused'); w._logger = logging.getLogger(__name__)
    def prepare(*args):
        calls.extend(['reset_sequence', 'prepare' if capture_mode == 'trigger' else 'master'])
    w.runtime = SimpleNamespace(capture_mode=capture_mode, prepare=prepare, quiesce=lambda: calls.append('stop' if capture_mode == 'trigger' else 'idle'))
    w.runtime_worker = SimpleNamespace(busy=False)
    def submit(kind, work):
        w.runtime_worker.busy = True
        try: result, error = work(), None
        except Exception as exc: result, error = None, exc
        w.runtime_worker.busy = False
        w._runtime_completed(kind, result, error)
        return True
    w.runtime_worker.submit = submit
    w._pending_runtime_action = None
    w._active_inspection_request = None
    w._runtime_was_live = False
    w._active_view_id = 'one'
    w.current_recipe_name = lambda: 'test'
    w._set_runtime_controls = lambda busy: None
    w._sync_capture_mode_ui = lambda: None
    w.cam = SimpleNamespace(arm_master_frame=lambda: 'reserved')
    w.get_capture_mode = lambda: capture_mode
    w.external_triggered = SimpleNamespace(emit=lambda *args: calls.append(('event', args)))
    w.live_enabled = False
    w._run_timer = SimpleNamespace(stop=lambda: None)
    return w, calls, box


@pytest.mark.parametrize('capture_mode', ['master', 'trigger'])
def test_history_preserves_external_capture_and_return(capture_mode):
    w, calls, _ = window(capture_mode=capture_mode)
    for _ in range(2):
        w._request_mode('RESULTS')
        assert w.mode == 'RUN' and w.panel_results.active
        w._handle_external_trigger('modbus', input_index=2)
        assert calls[-1][0] == 'event'
        w.inspection.finish(calls[-1][1][1]['inspection_request'])
        w._request_mode('RUN')
        assert w.stack.page is w.panel_run
    assert not any(c in calls for c in ['stop', 'prepare', 'idle', 'master', 'reset_sequence', 'light', 'profile'])


@pytest.mark.parametrize('from_history', [False, True])
def test_cancel_setup_keeps_production_and_page(from_history):
    w, calls, _ = window()
    if from_history: w._request_mode('RESULTS')
    page = w.stack.page
    w._request_mode('SETUP')
    assert w.mode == 'RUN' and w.stack.page is page
    assert 'stop' not in calls
    assert not w.mode_btn.checked


@pytest.mark.parametrize('capture_mode', ['master', 'trigger'])
def test_confirm_setup_pauses_and_return_restarts(capture_mode):
    w, calls, box = window(capture_mode=capture_mode)
    w._request_mode('RESULTS')
    box.question = lambda *args: box.Yes
    w._request_mode('SETUP')
    assert w.mode == 'SETUP' and w.stack.page is w.panel_setup
    assert ('stop' if capture_mode == 'trigger' else 'idle') in calls
    before = len(calls)
    w._handle_external_trigger('modbus', input_index=2)
    assert len(calls) == before
    w._request_mode('RESULTS')
    assert not w.panel_results.active and w.mode == 'SETUP'
    w._request_mode('RUN')
    assert w.mode == 'RUN'
    assert calls.count('reset_sequence') == 1
    assert ('prepare' if capture_mode == 'trigger' else 'master') in calls


def test_history_from_setup_does_not_start_production():
    w, calls, box = window(mode='SETUP')
    box.question = lambda *args: pytest.fail('Already paused: no confirmation needed')
    w._request_mode('RESULTS')
    assert w.mode == 'SETUP' and not w.panel_results.active
    w._request_mode('SETUP')
    assert w.stack.page is w.panel_setup
    assert calls == ['history']


def test_setup_waits_for_accepted_cycle_before_idle():
    w, calls, box = window()
    request, _ = w.inspection.admit('pico')
    w.runtime_worker.busy = True
    w._active_inspection_request = request
    w._display_inspection_result = lambda result: calls.append('display')
    box.question = lambda *args: box.Yes
    w._request_mode('SETUP')
    assert w.mode == 'SETUP'
    assert 'stop' not in calls
    assert w._pending_runtime_action == 'pause'
    w.runtime_worker.busy = False
    w._runtime_completed('cycle', {}, None)
    assert calls.index('display') < calls.index('stop')
    assert w.inspection.snapshot()['state'] == 'paused'
    assert w._pending_runtime_action is None


def test_pause_covers_admitted_event_still_waiting_for_qt_delivery():
    w, calls, box = window()
    request, _ = w.inspection.admit('pico')
    box.question = lambda *args: box.Yes
    w._request_mode('SETUP')
    assert not w.runtime_worker.busy
    assert 'stop' not in calls
    assert w._pending_runtime_action == 'pause'
    assert w.inspection.owns(request)


def test_close_waits_for_capture_then_quiesces_and_closes():
    w, calls, box = window()
    request, _ = w.inspection.admit('pico')
    w.runtime_worker.busy = True
    w._active_inspection_request = request
    w._display_inspection_result = lambda result: calls.append('display')
    w.runtime.shutdown = lambda: calls.extend(['idle', 'hardware_closed'])
    w.close = lambda: calls.append('window_closed')
    w._request_runtime_stop(close=True)
    assert 'hardware_closed' not in calls
    w.runtime_worker.busy = False
    w._runtime_completed('cycle', {}, None)
    assert calls.index('display') < calls.index('idle') < calls.index('hardware_closed') < calls.index('window_closed')
    assert w.inspection.snapshot()['state'] == 'closed'


def test_display_error_does_not_strand_controller_or_pending_stop():
    w, calls, box = window()
    request, _ = w.inspection.admit('manual')
    w._active_inspection_request = request
    def fail(_):
        raise RuntimeError('display failed')
    w._display_inspection_result = fail
    w._pending_runtime_action = 'pause'
    w._runtime_completed('cycle', {}, None)
    assert 'stop' in calls
    assert w.inspection.snapshot()['state'] == 'paused'


def test_pair_failure_recovers_without_replaying_cycle(tmp_path):
    from app.services.capture_errors import IncompletePioPair
    w, calls, _ = window()
    w._apply_run_status_style = lambda status: calls.append(('style', status))
    w._run_status_message = SimpleNamespace(setText=lambda text: calls.append(('message', text)))
    w.recovery_notice = SimpleNamespace(setVisible=lambda value: None,
        setText=lambda text: calls.append(('recovery', text)))
    w.recovery_progress = SimpleNamespace(emit=lambda row: calls.append(('audit', row['event'])))
    w.runtime.data_root = tmp_path
    w.runtime.camera_id = 'camera_1'; w.runtime.pico_id = 'pico_1'
    w.runtime.pico = SimpleNamespace(quiesce=lambda: calls.append('idle'))
    w.runtime.cam = SimpleNamespace(pio=SimpleNamespace(recover_trigger_mode=lambda *a, **kw: calls.append('recover_hardware')))
    w._active_inspection_request, _ = w.inspection.admit('pico')
    w._runtime_completed('cycle', None, IncompletePioPair(1, 2))
    assert calls.count('recover_hardware') == 1
    assert w.inspection.state.value == 'ready'
    assert w.inspection.snapshot()['counts']['failed'] == 1
    assert w.inspection.snapshot()['counts'].get('completed', 0) == 0
    assert any(x[0] == 'recovery' and 'neoverený' in x[1] for x in calls if isinstance(x, tuple))


def test_pending_setup_prevents_automatic_recovery():
    from app.services.capture_errors import IncompletePioPair
    w, calls, _ = window()
    w._apply_run_status_style = lambda status: None
    w._run_status_message = SimpleNamespace(setText=lambda text: None)
    w._active_inspection_request, _ = w.inspection.admit('pico')
    w._pending_runtime_action = 'pause'
    w._runtime_completed('cycle', None, IncompletePioPair(0, 2))
    assert w.inspection.state.value == 'paused'
    assert calls.count('stop') == 1

"""History navigation must not interrupt production or rearm the camera."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from numbers import Integral
from typing import Any

import pytest


def window(mode='RUN', capture_mode='trigger'):
    tree = ast.parse(Path('app/ui/main_window.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    names = {'_request_mode', '_sync_mode_chrome', 'toggle_mode', '_handle_external_trigger'}
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    cls.bases = []; cls.decorator_list = []
    calls = []
    box = SimpleNamespace(Yes=1, No=2, question=lambda *args: 2)
    class History:
        def __init__(self, *args): pass
        def set_production_active(self, value): self.active = value
        def activate(self): calls.append('history')
    ns = dict(QMessageBox=box, ResultsPage=History, Integral=Integral, Any=Any)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), 'main_window.py', 'exec'), ns)
    w = ns['MainWindow']()
    w.mode = mode; w.capture_mode = capture_mode
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
    w.btn_mode_run = button(); w.mode_btn = button(); w.btn_results = button(); w.btn_live = button()
    w.db = SimpleNamespace(db_path='unused'); w._logger = logging.getLogger(__name__)
    w._exit_run_trigger_session = lambda **kwargs: calls.append('stop')
    w._enter_run_trigger_session = lambda: calls.append('prepare')
    w._reset_external_sequence_state = lambda: calls.append('reset_sequence')
    w._refresh_manual_light = lambda: calls.append('light')
    w._apply_run_camera_profile = lambda: calls.append('profile')
    w.cam = SimpleNamespace(arm_master_frame=lambda: 'reserved')
    w.pico = SimpleNamespace(quiesce=lambda: calls.append('idle'), prepare_master=lambda cam: calls.append('master'))
    w.get_capture_mode = lambda: capture_mode
    w.external_triggered = SimpleNamespace(emit=lambda *args: calls.append(('event', args)))
    w.live_enabled = False
    return w, calls, box


@pytest.mark.parametrize('capture_mode', ['master', 'trigger'])
def test_history_preserves_external_capture_and_return(capture_mode):
    w, calls, _ = window(capture_mode=capture_mode)
    for _ in range(2):
        w._request_mode('RESULTS')
        assert w.mode == 'RUN' and w.panel_results.active
        w._handle_external_trigger('modbus', input_index=2)
        assert calls[-1][0] == 'event'
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

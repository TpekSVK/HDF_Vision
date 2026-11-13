from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from app.ui.golden_wizard.golden_wizard import GoldenWizard
from app.ui.main_window import MainWindow


def test_standalone_leaving_run_quiesces_pico_without_closing_camera():
    calls = []
    host = SimpleNamespace(
        pico=SimpleNamespace(quiesce=lambda: calls.append('idle')),
        cam=SimpleNamespace(exit_trigger_session=lambda **kw: pytest.fail('healthy stream closed')),
        _run_trigger_session_active=True)
    MainWindow._exit_run_trigger_session(host)
    assert calls == ['idle'] and not host._run_trigger_session_active


class FakeButton:
    def __init__(self, checked=False):
        self.checked = checked
        self.text = ""

    def blockSignals(self, _blocked):
        pass

    def setChecked(self, checked):
        self.checked = checked

    def setToolTip(self, text):
        self.tooltip = text

    def setText(self, text):
        self.text = text


def _host(method_owner, pico, checked=False):
    host = SimpleNamespace(pico=pico, btn_manual_light=FakeButton(checked))
    host._set_manual_light_ui = method_owner._set_manual_light_ui.__get__(host)
    return host


def test_run_manual_light_status_on_and_off():
    for state, text in ((True, "Svetlo zapnuté"), (False, "Svetlo vypnuté")):
        host = _host(MainWindow, SimpleNamespace(manual_light_status=lambda: state))
        MainWindow._refresh_manual_light(host)
        assert host.btn_manual_light.checked is state
        assert host.btn_manual_light.text == text


def test_run_manual_light_toggle_success(monkeypatch):
    calls = []
    host = _host(
        MainWindow,
        SimpleNamespace(set_manual_light=lambda value: calls.append(value) or True, last_error=""),
    )
    MainWindow._toggle_manual_light(host, True)
    MainWindow._toggle_manual_light(host, False)
    assert calls == [True, False]
    assert not host.btn_manual_light.checked


def test_run_manual_light_toggle_failure_rolls_back(monkeypatch):
    host = _host(
        MainWindow,
        SimpleNamespace(set_manual_light=lambda _value: False, last_error="command failed"),
    )
    monkeypatch.setattr("app.ui.main_window.QMessageBox.critical", lambda *_args: None)
    MainWindow._toggle_manual_light(host, True)
    assert not host.btn_manual_light.checked
    assert host.btn_manual_light.text == "Svetlo vypnuté"


def test_golden_manual_light_uses_injected_service_and_close_quiesces_channel():
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")
    constructor = source[source.index("class GoldenWizard"):source.index("# ---------- Live ----------")]
    assert "PicoService()" not in constructor
    calls = []
    host = SimpleNamespace(
        _has_unsaved_changes=lambda: False,
        _stop_preview_session=lambda: calls.append('stop'),
        pico=SimpleNamespace(set_manual_light=lambda enabled: calls.append(('light', enabled))),
    )
    event = SimpleNamespace(accept=lambda: calls.append('accept'))
    GoldenWizard.closeEvent(host, event)
    assert calls == ['stop', ('light', False), 'accept']


def test_golden_manual_light_status_and_toggle_are_independent():
    calls = []
    pico = SimpleNamespace(
        manual_light_status=lambda: True,
        set_manual_light=lambda value: calls.append(value) or True,
        last_error="",
    )
    host = _host(GoldenWizard, pico)
    host._err = lambda _message: None

    GoldenWizard._refresh_manual_light(host)
    assert host.btn_manual_light.checked
    GoldenWizard._toggle_manual_light(host, False)
    assert calls == [False]
    assert not host.btn_manual_light.checked


def test_final_close_does_not_reconnect_pico_after_runtime_shutdown():
    calls = []
    host = SimpleNamespace(_close_ready=True,
        _stop_live_and_light=lambda: pytest.fail('closed Pico was reopened'),
        runtime_worker=SimpleNamespace(shutdown=lambda: calls.append('worker')),
        workcell=None, _jetson_stats_service=SimpleNamespace(stop=lambda: None),
        db=SimpleNamespace(close=lambda: calls.append('db')),
        station_closed=SimpleNamespace(emit=lambda ident: calls.append(ident)),
        camera_id='camera_1', _power_exit_code=None)
    MainWindow.closeEvent(host, SimpleNamespace(accept=lambda: calls.append('accepted')))
    assert calls == ['worker', 'db', 'camera_1', 'accepted']

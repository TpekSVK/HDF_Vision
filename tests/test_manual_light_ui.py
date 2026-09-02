from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from app.ui.golden_wizard.golden_wizard import GoldenWizard
from app.ui.main_window import MainWindow


class FakeButton:
    def __init__(self, checked=False):
        self.checked = checked
        self.text = ""

    def blockSignals(self, _blocked):
        pass

    def setChecked(self, checked):
        self.checked = checked

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


def test_golden_manual_light_uses_injected_service_and_close_does_not_switch_it_off():
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")
    constructor = source[source.index("class GoldenWizard"):source.index("# ---------- Live ----------")]
    close = source[source.index("def closeEvent", source.index("class GoldenWizard")):]

    assert "PicoService()" not in constructor
    assert "set_manual_light(False)" not in close


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

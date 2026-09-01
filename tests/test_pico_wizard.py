import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from app.services.pico_config_service import PicoConfigService
from app.ui.pico_wizard import PicoWizard


class FakePico:
    def __init__(self):
        self.callbacks = []
        self.unregister_calls = []

    def connect(self):
        return True

    def is_available(self):
        return True

    def status(self):
        return {
            "connected": True,
            "port": "/dev/ttyACM0",
            "device_status": "FIRMWARE pico_hdf_controller 3.2.0-master-capture\nV1_MODE MASTER\nEND",
        }

    def inputs(self):
        return "INPUTS IN1=ACTIVE IN2=OFF IN3=OFF IN4=ACTIVE IN5=OFF IN6=OFF IN7=OFF IN8=OFF\nEND"

    def register_trigger_callback(self, callback):
        self.callbacks.append(callback)

    def unregister_trigger_callback(self, callback):
        self.unregister_calls.append(callback)
        self.callbacks.remove(callback)


def _app():
    return QApplication.instance() or QApplication([])


def test_status_and_inputs_parsers_are_tolerant():
    assert PicoWizard.parse_firmware("PINS x\nFIRMWARE controller 1.2.3\nEND") == "controller 1.2.3"
    assert PicoWizard.parse_firmware("malformed") is None
    assert PicoWizard.parse_inputs("INPUTS IN1=ACTIVE IN2=OFF junk\nEND") == {1: "ACTIVE", 2: "OFF"}


def test_wizard_shows_diagnostics_and_unregisters_callback(tmp_path):
    app = _app()
    pico = FakePico()
    wizard = PicoWizard(pico, PicoConfigService(tmp_path / "pico.json"))

    assert wizard.lbl_firmware.text() == "pico_hdf_controller 3.2.0-master-capture"
    assert wizard._input_states[1].text() == "ACTIVE"
    pico.callbacks[0](4)
    app.processEvents()
    assert wizard.lbl_last_event.text() == "Posledný event: IN4"

    callback = pico.callbacks[0]
    wizard.reject()
    assert pico.unregister_calls == [callback]
    assert pico.callbacks == []


def test_wizard_saves_enabled_inputs_locally(tmp_path):
    _app()
    path = tmp_path / "pico.json"
    pico = FakePico()
    wizard = PicoWizard(pico, PicoConfigService(path))
    wizard._enabled_checks[1].setChecked(True)
    wizard._enabled_checks[3].setChecked(True)
    wizard._save()

    assert PicoConfigService(path).get_enabled_inputs() == {1, 3}

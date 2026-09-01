import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.services.pico_config_service import PicoConfigService
from app.ui.pico_wizard import PicoWizard


class FakePico:
    def __init__(self, failures=()):
        self.callbacks = []
        self.unregister_calls = []
        self.failures = set(failures)
        self.commands = []
        self.last_error = ""

    def connect(self):
        return True

    def is_available(self):
        return True

    def status(self):
        return {
            "connected": True,
            "port": "/dev/ttyACM0",
            "device_status": "FIRMWARE pico_hdf_controller 3.2.0-master-capture\nV1_MODE MASTER\nV2_MODE TRIGGER\nEND",
        }

    def inputs(self):
        return "INPUTS IN1=ACTIVE IN2=OFF IN3=OFF IN4=ACTIVE IN5=OFF IN6=OFF IN7=OFF IN8=OFF\nEND"

    def register_trigger_callback(self, callback):
        self.callbacks.append(callback)

    def unregister_trigger_callback(self, callback):
        self.unregister_calls.append(callback)
        self.callbacks.remove(callback)

    def set_view_mode(self, view, mode):
        self.commands.append((view, mode))
        if view in self.failures:
            self.last_error = f"failed {view}"
            return False
        return True

    def save_config(self):
        self.commands.append(("SAVE",))
        if "SAVE" in self.failures:
            self.last_error = "failed SAVE"
            return False
        return True


def _app():
    return QApplication.instance() or QApplication([])


def test_status_and_inputs_parsers_are_tolerant():
    assert PicoWizard.parse_firmware("PINS x\nFIRMWARE controller 1.2.3\nEND") == "controller 1.2.3"
    assert PicoWizard.parse_firmware("malformed") is None
    assert PicoWizard.parse_inputs("INPUTS IN1=ACTIVE IN2=OFF junk\nEND") == {1: "ACTIVE", 2: "OFF"}


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            "FIRMWARE pico_hdf_controller 3.2.0-master-capture\n"
            "V1_MODE MASTER\nV2_MODE TRIGGER\nEND",
            {"V1": "MASTER", "V2": "TRIGGER"},
        ),
        ("v1_mode trigger\nv2_mode master", {"V1": "TRIGGER", "V2": "MASTER"}),
        ("V1_MODE MASTER", {"V1": "MASTER"}),
        ("V1_MODE AUTO\nV2_MODE invalid", {}),
        ("", {}),
    ],
)
def test_parse_view_modes(response, expected):
    assert PicoWizard.parse_view_modes(response) == expected


def test_wizard_shows_diagnostics_and_unregisters_callback(tmp_path):
    app = _app()
    pico = FakePico()
    wizard = PicoWizard(pico, PicoConfigService(tmp_path / "pico.json"))

    assert wizard.lbl_firmware.text() == "pico_hdf_controller 3.2.0-master-capture"
    assert wizard.cmb_v1_mode.currentText() == "MASTER"
    assert wizard.cmb_v2_mode.currentText() == "TRIGGER"
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
    assert pico.commands == [("V1", "MASTER"), ("V2", "TRIGGER"), ("SAVE",)]
    assert wizard.result() == QDialog.Accepted


@pytest.mark.parametrize(
    ("failures", "expected_commands"),
    [
        ({"V1"}, [("V1", "MASTER")]),
        ({"V2"}, [("V1", "MASTER"), ("V2", "TRIGGER")]),
        ({"SAVE"}, [("V1", "MASTER"), ("V2", "TRIGGER"), ("SAVE",)]),
    ],
)
def test_wizard_does_not_save_local_config_when_pico_command_fails(
    tmp_path, monkeypatch, failures, expected_commands
):
    _app()
    path = tmp_path / "pico.json"
    pico = FakePico(failures)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    wizard = PicoWizard(pico, PicoConfigService(path))
    wizard._enabled_checks[1].setChecked(True)

    wizard._save()

    assert pico.commands == expected_commands
    assert not path.exists()
    assert wizard.result() != QDialog.Accepted
    assert errors and pico.last_error in errors[0]
    wizard.reject()

"""Golden view UI stores sensor exposure and rejects unsupported PIO profiles."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from app.ui.golden_wizard.view_config_dialog import ViewConfigDialog, _DEFAULT_CAMERA_RESOLUTIONS
from app.utils.cu55_pio import CU55_EXPOSURES_US

@pytest.fixture
def dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    dialogs = []
    def make(**kwargs):
        options = dict(view_id="view_1", name="Predná strana", available_resolutions=_DEFAULT_CAMERA_RESOLUTIONS,
                       current_camera=dict(width=1920, height=1080, fps=60, pixel_format="Y8", exposure_us=1000),
                       camera_model="See3CAM CU55", capture_mode="trigger", trigger_interval_ms=100)
        options.update(kwargs)
        d = ViewConfigDialog(**options)
        dialogs.append(d)
        return d, errors
    yield make
    for d in dialogs:
        d.close()
        d.deleteLater()
    app.processEvents()

@pytest.mark.parametrize("exposure", CU55_EXPOSURES_US)
def test_exposure_selection_roundtrips(dialog, exposure):
    d, errors = dialog(camera_profile={"exposure_us": exposure})
    assert not d._exposure_combo.isEditable()
    assert tuple(d._exposure_combo.itemData(i) for i in range(d._exposure_combo.count())) == CU55_EXPOSURES_US
    assert d._exposure_combo.currentData() == exposure
    d.accept()
    assert not errors
    assert d.result() == QDialog.Accepted
    assert d._result["camera_profile"].exposure_us == exposure


def test_legacy_exposure_requires_explicit_choice(dialog):
    d, errors = dialog(camera_profile={"exposure_us": 8000}, trigger_gap_ms=20.5)
    assert d._exposure_combo.currentIndex() == -1
    assert "8 ms" in d._exposure_combo.placeholderText()
    d.accept()
    assert errors and d._result is None
    d._exposure_combo.setCurrentIndex(d._exposure_combo.findData(5000))
    d.accept()
    assert d._result["camera_profile"].exposure_us == 5000
    assert d._result["trigger_gap_ms"] == 20.5


def test_vga_long_exposure_uses_automatic_period(dialog):
    d, errors = dialog(camera_profile=dict(width=640, height=480, fps=112, exposure_us=16000))
    assert d._selected_pio_profile().period_us == 16100
    d.accept()
    assert not errors
    assert d._result["camera_profile"].width == 640

@pytest.mark.parametrize("mode", ["master", "trigger"])
def test_pixel_format_mode_validation(dialog, mode):
    d, errors = dialog(capture_mode=mode, camera_profile={"width": 1920, "height": 1080, "fps": 60, "pixel_format": "Y12", "exposure_us": 1000})
    assert not hasattr(d, "_pixel_format_combo")
    d.accept()
    assert bool(errors) == (mode == "trigger")
    assert (d._result is None) == (mode == "trigger")


def test_unsupported_saved_resolution_cannot_be_accepted(dialog):
    d, errors = dialog(camera_profile=dict(width=800, height=600, fps=30, exposure_us=1000))
    d.accept()
    assert errors and d._result is None
    for i in range(d._resolution_combo.count()):
        data = d._resolution_combo.itemData(i)
        if isinstance(data, dict) and data.get("width") == 1280:
            d._resolution_combo.setCurrentIndex(i)
            break
    d.accept()
    assert d._result["camera_profile"].width == 1280


def test_resolutions_and_trigger_controls(dialog):
    d, _ = dialog(trigger_mode="external", external_source="pico")
    assert len(_DEFAULT_CAMERA_RESOLUTIONS) == 4
    labels = " ".join(label for label, _ in _DEFAULT_CAMERA_RESOLUTIONS)
    assert "640 × 480 · 112 fps · Y8" in labels
    assert "BUG" not in labels and "setup" not in labels
    assert not hasattr(d, "_trigger_exposure_edit")
    assert not d._pico_profile_combo.isEnabled()
    assert "Impulzy a blesk riadi aplikácia" in d._pico_timing_info.text()


def test_removed_controls_and_legacy_timing(dialog):
    d, errors = dialog(settle_ms=500, camera_profile={"exposure_us": 1000, "flash_mode": 2})
    for field in ("_settle_edit", "_flash_mode_combo", "_pixel_format_combo"):
        assert not hasattr(d, field)
    d.accept()
    assert not errors
    assert d.values()["settle_ms"] is None
    assert d.values()["camera_profile"].flash_mode is None

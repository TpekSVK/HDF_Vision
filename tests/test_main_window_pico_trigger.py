import logging
import re
from pathlib import Path
from types import SimpleNamespace
from numbers import Integral
import time

import pytest
from app.services.inspection_controller import InspectionController


from app.ui.main_window import MainWindow as ProductionWindow


class MainWindow:
    """Bind the real UI adapters without constructing camera-backed widgets."""


for _method in ('_request_software_trigger', '_handle_modbus_trigger',
                '_handle_pico_trigger', '_handle_external_trigger', '_handle_master_flash_capture_flow'):
    setattr(MainWindow, _method, getattr(ProductionWindow, _method))


class _SignalSpy:
    def __init__(self) -> None:
        self.count = 0
        self.events = []

    def emit(self, *args) -> None:
        self.count += 1
        self.events.append(args)


def _handler_window(*, mode: str = "RUN"):
    window = MainWindow.__new__(MainWindow)
    window.mode = mode
    window.inspection = InspectionController()
    if mode == "RUN":
        window.inspection.prepare(lambda: None, lambda: None)
    window.trigger_rejected = _SignalSpy()
    window._logger = logging.getLogger("test.main_window.pico")
    window._pending_trigger_source = None
    window._pending_trigger_input_index = None
    window.external_triggered = _SignalSpy()
    window.get_capture_mode = lambda: "master"
    window._consume_software_pico_request = lambda source: None
    window.cam = SimpleNamespace(arm_master_frame=lambda: "reserved")
    return window


def test_pico_callback_starts_run_trigger_and_preserves_metadata() -> None:
    window = _handler_window()

    window._handle_pico_trigger("IN3")

    assert window.inspection.owns(window.external_triggered.events[0][1].pop("inspection_request"))
    assert window.external_triggered.events == [("pico", {"input_index": 3, "spec": None, "frame_request": "reserved"})]
    assert window.external_triggered.count == 1


def test_pico_callback_is_ignored_outside_run() -> None:
    window = _handler_window(mode="SETUP")

    window._handle_pico_trigger("IN3")

    assert window._pending_trigger_source is None
    assert window._pending_trigger_input_index is None
    assert window.external_triggered.count == 0


@pytest.mark.parametrize("input_index", [0, 9, None])
def test_pico_callback_safely_ignores_invalid_input(input_index) -> None:
    window = _handler_window()

    window._handle_pico_trigger(input_index)

    assert window._pending_trigger_source is None
    assert window._pending_trigger_input_index is None
    assert window.external_triggered.count == 0


def test_pico_master_capture_does_not_fire_light_again() -> None:
    window = MainWindow.__new__(MainWindow)
    window.cam = SimpleNamespace()
    window.pico_config = SimpleNamespace()
    window._active_view_id = "view_1"
    window._logger = logging.getLogger("test.main_window.pico")
    fired: list[str] = []
    window.pico = SimpleNamespace(
        fire=lambda view_id: fired.append(view_id),
        last_error="",
    )
    view = SimpleNamespace(id="view_1", flash_delay_ms=0, settle_ms=0)

    with pytest.raises(RuntimeError, match="rezervovaný"):
        window._handle_master_flash_capture_flow(
            view=view, capture_request_source="pico",
        )

    assert fired == []


def test_modbus_master_capture_still_fires_pico_light() -> None:
    window = MainWindow.__new__(MainWindow)
    window.pico_config = SimpleNamespace()
    window._active_view_id = "view_2"
    window._logger = logging.getLogger("test.main_window.modbus")
    fired: list[str] = []
    window.pico = SimpleNamespace(
        capture_master=lambda target, camera: fired.append(target) or "fresh frame",
        last_error="",
    )
    view = SimpleNamespace(id="view_2", pico_profile="V2", flash_delay_ms=0, settle_ms=0)
    window.cam = SimpleNamespace(prepare_master_capture=lambda: None)

    window._handle_master_flash_capture_flow(
        view=view,
        capture_request_source="modbus",
    )

    assert fired == ["V2"]


def test_modbus_trigger_still_preserves_its_input_index() -> None:
    window = _handler_window()

    window._handle_modbus_trigger(5)

    assert window.inspection.owns(window.external_triggered.events[0][1].pop("inspection_request"))
    assert window.external_triggered.events == [("modbus", {"input_index": 5, "spec": None, "frame_request": None})]
    assert window.external_triggered.count == 1


def test_manual_run_trigger_remains_independent_of_external_sources() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "self.btn_trigger.clicked.connect(self._request_software_trigger)" in source
    window = _handler_window()
    window.get_capture_mode = lambda: "trigger"
    calls = []
    window.manual_trigger = lambda *args: calls.append(args)
    window._request_software_trigger()
    assert calls == [("manual", {"software_button": True})]


def test_pico_callback_is_registered_and_service_is_closed() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "self.pico.register_trigger_callback(self._handle_pico_trigger)" in source
    assert "self.pico.close()" in Path("app/services/inspection_runtime.py").read_text()


def test_external_burst_does_not_fill_qt_queue():
    window = _handler_window()
    for _ in range(100):
        window._handle_pico_trigger('IN3')
    assert window.external_triggered.count == 1
    assert window.inspection.snapshot()['counts']['rejected_busy'] == 99
    assert window.trigger_rejected.count == 1


def test_frame_reservation_failure_releases_token_and_blocks_next_capture():
    window = _handler_window()
    def fail():
        raise RuntimeError('frame reservation failed')
    window.cam.arm_master_frame = fail
    outputs = []
    window._signal_outputs = outputs.append
    window.pico = SimpleNamespace(quiesce=lambda: outputs.append("idle"))
    window._handle_pico_trigger('IN3')
    state = window.inspection.snapshot()
    assert state['state'] == 'error'
    assert state['request_id'] is None
    assert state['counts']['failed'] == 1
    assert outputs == ['nok', 'idle']
    assert window.external_triggered.count == 0

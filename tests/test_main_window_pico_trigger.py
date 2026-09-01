import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from numbers import Integral
import time

import pytest


def _load_trigger_harness():
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_window = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    method_names = {
        "_handle_gpio_trigger",
        "_handle_modbus_trigger",
        "_handle_pico_trigger",
        "_handle_external_trigger",
        "_handle_master_flash_capture_flow",
    }
    methods = [
        node for node in main_window.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in method_names
    ]
    harness = ast.ClassDef(
        name="TriggerHarness",
        bases=[],
        keywords=[],
        body=methods,
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
    namespace = {"Integral": Integral, "time": time, "Any": object}
    exec(compile(module, "app/ui/main_window.py", "exec"), namespace)
    return namespace["TriggerHarness"]


MainWindow = _load_trigger_harness()


class _SignalSpy:
    def __init__(self) -> None:
        self.count = 0

    def emit(self) -> None:
        self.count += 1


def _handler_window(*, mode: str = "RUN"):
    window = MainWindow.__new__(MainWindow)
    window.mode = mode
    window._logger = logging.getLogger("test.main_window.pico")
    window._pending_trigger_source = None
    window._pending_trigger_input_index = None
    window.external_triggered = _SignalSpy()
    return window


def test_pico_callback_starts_run_trigger_and_preserves_metadata() -> None:
    window = _handler_window()

    window._handle_pico_trigger(3)

    assert window._pending_trigger_source == "pico"
    assert window._pending_trigger_input_index == 3
    assert window.external_triggered.count == 1


def test_pico_callback_is_ignored_outside_run() -> None:
    window = _handler_window(mode="SETUP")

    window._handle_pico_trigger(3)

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
    window._active_view_id = "view_1"
    window._logger = logging.getLogger("test.main_window.pico")
    fired: list[str] = []
    window.pico = SimpleNamespace(
        fire=lambda view_id: fired.append(view_id),
        last_error="",
    )
    view = SimpleNamespace(id="view_1", flash_delay_ms=0, settle_ms=0)

    window._handle_master_flash_capture_flow(
        view=view,
        capture_request_source="pico",
    )

    assert fired == []


def test_modbus_trigger_still_preserves_its_input_index() -> None:
    window = _handler_window()

    window._handle_modbus_trigger(5)

    assert window._pending_trigger_source == "modbus"
    assert window._pending_trigger_input_index == 5
    assert window.external_triggered.count == 1


def test_manual_run_trigger_remains_independent_of_external_sources() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "self.btn_trigger.clicked.connect(self.manual_trigger)" in source
    assert 'self._pending_trigger_source or "manual"' in source


def test_pico_callback_is_registered_and_service_is_closed() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "self.pico.register_trigger_callback(self._handle_pico_trigger)" in source
    assert "self.pico.close()" in source

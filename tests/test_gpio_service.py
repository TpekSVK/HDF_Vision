from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - test environment shim
    sys.path.insert(0, str(ROOT))

from app.services import gpio_service as gpio_mod


def test_pulse_physical_pin_toggles_output_capable_pin(monkeypatch):
    stub = gpio_mod._StubDriver()
    monkeypatch.setattr(gpio_mod, "_load_driver", lambda: (stub, False))

    service = gpio_mod.GPIOService()
    try:
        ok = service.pulse_physical_pin(7, pulse_seconds=0.01)
        assert ok is True
        assert bool(stub._outputs.get(7)) is True
    finally:
        service.close()


def test_pulse_physical_pin_rejects_input_only_pin(monkeypatch):
    stub = gpio_mod._StubDriver()
    monkeypatch.setattr(gpio_mod, "_load_driver", lambda: (stub, False))

    service = gpio_mod.GPIOService()
    try:
        ok = service.pulse_physical_pin(29, pulse_seconds=0.01)
        assert ok is False
    finally:
        service.close()

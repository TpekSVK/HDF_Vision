"""GPIO service with fixed central pin mapping for Jetson BOARD numbering."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from app.config.gpio_pins import (
    TRIGGER_OUT_PIN,
    TRIGGER_PULSE_MS,
    GPIO_BOARD,
    TRIGGER_IN_PIN,
    HEARTBEAT_OUT_PIN,
    RESULT_OK_OUT_PIN,
    RESULT_NOK_OUT_PIN,
    FLASH1_OUT_PIN,
    FLASH2_OUT_PIN,
)

__all__ = ["GPIOService", "PinDefinition"]


_PIN_GROUPS: Mapping[str, Tuple[int, ...]] = {
    "output": (7, 11, 12, 13, 15, 16, 18, 22),
    "input": (29, 31, 37, 40),
    "bidirectional": (19, 21),
}

_PIN_CAPABILITIES: Dict[int, Tuple[str, ...]] = {}
for group, pins in _PIN_GROUPS.items():
    capability = "output" if group == "output" else "input" if group == "input" else None
    for pin in pins:
        entry = list(_PIN_CAPABILITIES.get(pin, ()))
        if capability:
            if capability not in entry:
                entry.append(capability)
        else:
            for cap in ("output", "input"):
                if cap not in entry:
                    entry.append(cap)
        _PIN_CAPABILITIES[pin] = tuple(sorted(entry))


@dataclass(frozen=True)
class PinDefinition:
    physical: int
    label: str
    description: str
    is_gpio: bool = False


class _BaseDriver:
    OUT = "out"
    IN = "in"
    HIGH = True
    LOW = False
    PUD_DOWN = None
    PUD_UP = None

    def setmode_board(self) -> None:
        pass

    def setup(self, pin: int, mode: object, *, initial: Optional[object] = None, pull_up_down: Optional[object] = None) -> None:
        pass

    def output(self, pin: int, value: object) -> None:
        pass

    def input(self, pin: int) -> bool:
        return False

    def add_event_detect(self, pin: int, edge: str, callback: Callable[[int], None], *, bouncetime: int = 200) -> None:
        pass

    def remove_event_detect(self, pin: int) -> None:
        pass

    def cleanup(self) -> None:
        pass


class _JetsonDriver(_BaseDriver):
    def __init__(self) -> None:
        import Jetson.GPIO as GPIO  # type: ignore

        self._gpio = GPIO
        self.OUT = GPIO.OUT
        self.IN = GPIO.IN
        self.HIGH = GPIO.HIGH
        self.LOW = GPIO.LOW
        self.PUD_DOWN = getattr(GPIO, "PUD_DOWN", None)
        self.PUD_UP = getattr(GPIO, "PUD_UP", None)

    def setmode_board(self) -> None:
        GPIO = self._gpio
        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)

    def setup(self, pin: int, mode: object, *, initial: Optional[object] = None, pull_up_down: Optional[object] = None) -> None:
        kwargs = {}
        if initial is not None:
            kwargs["initial"] = initial
        if pull_up_down is not None:
            kwargs["pull_up_down"] = pull_up_down
        self._gpio.setup(pin, mode, **kwargs)

    def output(self, pin: int, value: object) -> None:
        self._gpio.output(pin, value)

    def input(self, pin: int) -> bool:
        return bool(self._gpio.input(pin))

    def add_event_detect(self, pin: int, edge: str, callback: Callable[[int], None], *, bouncetime: int = 200) -> None:
        GPIO = self._gpio
        mode = GPIO.RISING if edge == "rising" else GPIO.FALLING if edge == "falling" else GPIO.BOTH

        def _cb(channel: int) -> None:
            callback(int(channel))

        GPIO.add_event_detect(pin, mode, callback=_cb, bouncetime=bouncetime)

    def remove_event_detect(self, pin: int) -> None:
        try:
            self._gpio.remove_event_detect(pin)
        except Exception:
            pass

    def cleanup(self) -> None:
        try:
            self._gpio.cleanup()
        except Exception:
            pass


class _StubDriver(_BaseDriver):
    def __init__(self) -> None:
        self._modes: Dict[int, object] = {}
        self._outputs: Dict[int, bool] = {}
        self._inputs: Dict[int, bool] = {}
        self._events: Dict[int, Callable[[int], None]] = {}

    def setmode_board(self) -> None:
        return

    def setup(self, pin: int, mode: object, *, initial: Optional[object] = None, pull_up_down: Optional[object] = None) -> None:
        self._modes[int(pin)] = mode
        if mode == self.OUT:
            self._outputs[int(pin)] = bool(initial) if initial is not None else False
        else:
            self._inputs.setdefault(int(pin), False)

    def output(self, pin: int, value: object) -> None:
        self._outputs[int(pin)] = bool(value)

    def input(self, pin: int) -> bool:
        return bool(self._inputs.get(int(pin), False))

    def add_event_detect(self, pin: int, edge: str, callback: Callable[[int], None], *, bouncetime: int = 200) -> None:
        self._events[int(pin)] = callback

    def remove_event_detect(self, pin: int) -> None:
        self._events.pop(int(pin), None)

    def cleanup(self) -> None:
        self._modes.clear()
        self._outputs.clear()
        self._inputs.clear()
        self._events.clear()

    def simulate_input(self, pin: int, value: bool) -> None:
        self._inputs[int(pin)] = bool(value)
        cb = self._events.get(int(pin))
        if cb:
            cb(int(pin))


def _load_driver() -> tuple[_BaseDriver, bool]:
    try:
        return _JetsonDriver(), True
    except Exception:
        return _StubDriver(), False


class GPIOService:
    """GPIO runtime service with fixed, centrally defined pin mapping."""

    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._driver, self._is_hw = _load_driver()
        self._lock = threading.RLock()
        self._trigger_callbacks: list[Callable[[], None]] = []
        self._heartbeat_state = False
        self._outputs_by_role: dict[str, list[int]] = {
            "heartbeat": [HEARTBEAT_OUT_PIN] if HEARTBEAT_OUT_PIN is not None else [],
            "ok": [RESULT_OK_OUT_PIN] if RESULT_OK_OUT_PIN is not None else [],
            "nok": [RESULT_NOK_OUT_PIN] if RESULT_NOK_OUT_PIN is not None else [],
            "flash1": [FLASH1_OUT_PIN] if FLASH1_OUT_PIN is not None else [],
            "flash2": [FLASH2_OUT_PIN] if FLASH2_OUT_PIN is not None else [],
        }
        self._trigger_input_pin = TRIGGER_IN_PIN
        self._init_fixed_mapping()

    def _init_fixed_mapping(self) -> None:
        with self._lock:
            try:
                self._driver.cleanup()
            except Exception:
                pass
            self._driver.setmode_board()
            for pins in self._outputs_by_role.values():
                for pin in pins:
                    self._driver.setup(int(pin), self._driver.OUT, initial=self._driver.LOW)
            if self._trigger_input_pin is not None:
                self._driver.setup(int(self._trigger_input_pin), self._driver.IN)
                self._driver.add_event_detect(
                    int(self._trigger_input_pin),
                    "falling",
                    self._handle_trigger_event,
                    bouncetime=50,
                )

    def is_hardware_ready(self) -> bool:
        return self._is_hw

    def register_trigger_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._trigger_callbacks:
            self._trigger_callbacks.append(callback)

    def unregister_trigger_callback(self, callback: Callable[[], None]) -> None:
        if callback in self._trigger_callbacks:
            self._trigger_callbacks.remove(callback)

    # backward compatibility with removed per-recipe profile API
    def set_active_recipe(self, recipe: str) -> None:
        return

    def rename_profile(self, old: str, new: str) -> None:
        return

    def delete_profile(self, name: str) -> None:
        return

    def pulse_trigger_output(self) -> bool:
        self._logger.info(
            "Using fixed GPIO trigger pin BOARD=%s pulse_ms=%s board=%s",
            TRIGGER_OUT_PIN,
            TRIGGER_PULSE_MS,
            GPIO_BOARD,
        )
        return self.pulse_physical_pin(TRIGGER_OUT_PIN, pulse_seconds=TRIGGER_PULSE_MS / 1000.0)

    def emit_heartbeat(self) -> None:
        with self._lock:
            self._heartbeat_state = not self._heartbeat_state
            state = self._driver.HIGH if self._heartbeat_state else self._driver.LOW
            for pin in self._outputs_by_role.get("heartbeat", []):
                self._driver.output(pin, state)

    def signal_result(self, status: str, *, pulse_seconds: float = 0.2) -> None:
        role = "ok" if (status or "").strip().lower() == "ok" else "nok"
        other = "nok" if role == "ok" else "ok"
        for pin in self._outputs_by_role.get(other, []):
            self._driver.output(pin, self._driver.LOW)
        for pin in self._outputs_by_role.get(role, []):
            self._pulse_pin(pin, pulse_seconds)

    def pulse_physical_pin(self, pin: int, *, pulse_seconds: float = 0.01) -> bool:
        board_pin = int(pin)
        if "output" not in _PIN_CAPABILITIES.get(board_pin, ()):  # keep safe guard
            return False
        with self._lock:
            try:
                self._driver.setup(board_pin, self._driver.OUT, initial=self._driver.LOW)
                self._pulse_pin(board_pin, float(pulse_seconds))
                return True
            except Exception:
                return False

    def close(self) -> None:
        try:
            if self._trigger_input_pin is not None:
                self._driver.remove_event_detect(int(self._trigger_input_pin))
        except Exception:
            pass
        try:
            self._driver.cleanup()
        except Exception:
            pass

    def _pulse_pin(self, pin: int, duration: float) -> None:
        self._driver.output(pin, self._driver.HIGH)
        timer = threading.Timer(duration, lambda: self._driver.output(pin, self._driver.LOW))
        timer.daemon = True
        timer.start()

    def _handle_trigger_event(self, pin: int) -> None:
        del pin
        for callback in list(self._trigger_callbacks):
            try:
                callback()
            except Exception:
                pass

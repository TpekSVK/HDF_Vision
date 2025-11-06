"""Abstraction over Jetson GPIO pins with configuration storage."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

__all__ = [
    "GPIOService",
    "PinDefinition",
]


_CONFIG_PATH = Path("/data/gpio_config.json")
_DEFAULT_RECIPE = "default"
_DEFAULT_CONFIG: dict[str, object] = {
    "board": "JETSON_ORIN_NANO",
    "profiles": {},
}

_OUTPUT_ROLES = ("heartbeat", "ok", "nok", "flash1", "flash2")
_INPUT_ROLES = ("trigger",)
_ALL_ROLES = _OUTPUT_ROLES + _INPUT_ROLES

_ROLE_LABELS: Mapping[str, str] = {
    "none": "Voľné",
    "heartbeat": "Heartbeat (výstup)",
    "ok": "OK signál (výstup)",
    "nok": "NOK signál (výstup)",
    "flash1": "Flash #1 (výstup)",
    "flash2": "Flash #2 (výstup)",
    "trigger": "Trigger (vstup)",
}


_PIN_GROUPS: Mapping[str, Tuple[int, ...]] = {
    # Na základe predvoleného pinmux nastavenia Jetson Orin Nano poskytujeme
    # nasledujúce skupiny: pevne smerované výstupy, pevné vstupy a piny, ktoré
    # zvládnu obidva smery. Hodnoty sú fyzické čísla hlavičky.
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
            # bidirectional pins support both input and output
            for cap in ("output", "input"):
                if cap not in entry:
                    entry.append(cap)
        _PIN_CAPABILITIES[pin] = tuple(sorted(entry))


@dataclass(frozen=True)
class PinDefinition:
    """Static metadata describing a single header pin."""

    physical: int
    label: str
    description: str
    is_gpio: bool = False


def _load_pinout() -> List[PinDefinition]:
    """Return metadata for the Jetson Orin Nano 40-pin header."""

    # The header layout matches the Jetson 40-pin expansion connector.
    # We expose the most common signal name and mark safe GPIO-capable pins.
    rows: List[PinDefinition] = [
        PinDefinition(1, "3V3", "3.3 V power", False),
        PinDefinition(2, "5V", "5 V power", False),
        PinDefinition(3, "I2C SDA", "I2C0 SDA / GPIO3", False),
        PinDefinition(4, "5V", "5 V power", False),
        PinDefinition(5, "I2C SCL", "I2C0 SCL / GPIO2", False),
        PinDefinition(6, "GND", "Ground", False),
        PinDefinition(7, "GPIO4", "GPIO (PWM0)", True),
        PinDefinition(8, "UART TX", "UART2 TX / GPIO8", True),
        PinDefinition(9, "GND", "Ground", False),
        PinDefinition(10, "UART RX", "UART2 RX / GPIO9", True),
        PinDefinition(11, "GPIO17", "GPIO (I2S FS)", True),
        PinDefinition(12, "GPIO18", "GPIO (I2S CLK / PWM1)", True),
        PinDefinition(13, "GPIO27", "GPIO (I2S LRCK / PWM2)", True),
        PinDefinition(14, "GND", "Ground", False),
        PinDefinition(15, "GPIO22", "GPIO (I2S SDI)", True),
        PinDefinition(16, "GPIO23", "GPIO (I2S SDO)", True),
        PinDefinition(17, "3V3", "3.3 V power", False),
        PinDefinition(18, "GPIO24", "GPIO (SPI1 CS0)", True),
        PinDefinition(19, "SPI MOSI", "SPI1 MOSI / GPIO10", True),
        PinDefinition(20, "GND", "Ground", False),
        PinDefinition(21, "SPI MISO", "SPI1 MISO / GPIO12", True),
        PinDefinition(22, "GPIO25", "GPIO (SPI1 CS1)", True),
        PinDefinition(23, "SPI SCLK", "SPI1 SCLK / GPIO11", True),
        PinDefinition(24, "SPI CS0", "SPI1 CS0 / GPIO7", True),
        PinDefinition(25, "GND", "Ground", False),
        PinDefinition(26, "SPI CS1", "SPI1 CS1 / GPIO6", True),
        PinDefinition(27, "I2C SDA", "I2C1 SDA / GPIO0", False),
        PinDefinition(28, "I2C SCL", "I2C1 SCL / GPIO1", False),
        PinDefinition(29, "GPIO5", "GPIO (CAM0 PWDN)", True),
        PinDefinition(30, "GND", "Ground", False),
        PinDefinition(31, "GPIO6", "GPIO (CAM1 PWDN)", True),
        PinDefinition(32, "GPIO12", "GPIO (PWM0)", True),
        PinDefinition(33, "GPIO13", "GPIO (PWM1)", True),
        PinDefinition(34, "GND", "Ground", False),
        PinDefinition(35, "GPIO19", "GPIO (I2S SDO)", True),
        PinDefinition(36, "GPIO16", "GPIO (SPI2 CS1)", True),
        PinDefinition(37, "GPIO26", "GPIO (SPI2 CS0)", True),
        PinDefinition(38, "GPIO20", "GPIO (SPI2 MOSI)", True),
        PinDefinition(39, "GND", "Ground", False),
        PinDefinition(40, "GPIO21", "GPIO (SPI2 MISO)", True),
    ]
    return rows


class _BaseDriver:
    """Common driver interface for real or stub GPIO backends."""

    OUT = "out"
    IN = "in"
    HIGH = True
    LOW = False
    PUD_DOWN = None
    PUD_UP = None

    def setmode_board(self) -> None:  # pragma: no cover - overridden
        pass

    def setup(
        self,
        pin: int,
        mode: object,
        *,
        initial: Optional[object] = None,
        pull_up_down: Optional[object] = None,
    ) -> None:  # pragma: no cover - overridden
        pass

    def output(self, pin: int, value: object) -> None:  # pragma: no cover - overridden
        pass

    def input(self, pin: int) -> bool:  # pragma: no cover - overridden
        return False

    def add_event_detect(
        self,
        pin: int,
        edge: str,
        callback: Callable[[int], None],
        *,
        bouncetime: int = 200,
    ) -> None:  # pragma: no cover - overridden
        pass

    def remove_event_detect(self, pin: int) -> None:  # pragma: no cover - overridden
        pass

    def cleanup(self) -> None:  # pragma: no cover - overridden
        pass


class _JetsonDriver(_BaseDriver):
    def __init__(self) -> None:
        import Jetson.GPIO as GPIO  # type: ignore[import]

        self._gpio = GPIO
        self.OUT = GPIO.OUT
        self.IN = GPIO.IN
        self.HIGH = GPIO.HIGH
        self.LOW = GPIO.LOW
        self.PUD_DOWN = getattr(GPIO, "PUD_DOWN", None)
        self.PUD_UP = getattr(GPIO, "PUD_UP", None)
        self._callbacks: dict[int, Callable[[int], None]] = {}

    def setmode_board(self) -> None:
        GPIO = self._gpio
        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)

    def setup(
        self,
        pin: int,
        mode: object,
        *,
        initial: Optional[object] = None,
        pull_up_down: Optional[object] = None,
    ) -> None:
        GPIO = self._gpio
        kwargs: dict[str, object] = {}
        if initial is not None:
            kwargs["initial"] = initial
        if pull_up_down is not None:
            kwargs["pull_up_down"] = pull_up_down
        GPIO.setup(pin, mode, **kwargs)

    def output(self, pin: int, value: object) -> None:
        self._gpio.output(pin, value)

    def input(self, pin: int) -> bool:
        return bool(self._gpio.input(pin))

    def add_event_detect(
        self,
        pin: int,
        edge: str,
        callback: Callable[[int], None],
        *,
        bouncetime: int = 200,
    ) -> None:
        GPIO = self._gpio
        mode = GPIO.RISING if edge == "rising" else GPIO.FALLING if edge == "falling" else GPIO.BOTH

        def _cb(channel: int) -> None:
            try:
                callback(channel)
            except Exception:
                pass

        self._callbacks[pin] = _cb
        GPIO.add_event_detect(pin, mode, callback=_cb, bouncetime=bouncetime)

    def remove_event_detect(self, pin: int) -> None:
        cb = self._callbacks.pop(pin, None)
        try:
            self._gpio.remove_event_detect(pin)
        except Exception:
            pass
        if cb:
            try:
                del cb  # hint for GC
            except Exception:
                pass

    def cleanup(self) -> None:
        try:
            self._gpio.cleanup()
        except Exception:
            pass
        self._callbacks.clear()


class _StubDriver(_BaseDriver):
    """Fallback driver used when Jetson.GPIO is not available."""

    def __init__(self) -> None:
        self._outputs: dict[int, object] = {}
        self._events: dict[int, Callable[[int], None]] = {}
        self.PUD_DOWN = "down"
        self.PUD_UP = "up"

    def setmode_board(self) -> None:
        pass

    def setup(
        self,
        pin: int,
        mode: object,
        *,
        initial: Optional[object] = None,
        pull_up_down: Optional[object] = None,
    ) -> None:
        if initial is not None:
            self._outputs[pin] = initial

    def output(self, pin: int, value: object) -> None:
        self._outputs[pin] = value

    def input(self, pin: int) -> bool:
        return bool(self._outputs.get(pin, False))

    def add_event_detect(
        self,
        pin: int,
        edge: str,
        callback: Callable[[int], None],
        *,
        bouncetime: int = 200,
    ) -> None:
        self._events[pin] = callback

    def remove_event_detect(self, pin: int) -> None:
        self._events.pop(pin, None)

    def cleanup(self) -> None:
        self._outputs.clear()
        self._events.clear()

    # helper for tests/dev
    def simulate_event(self, pin: int) -> None:
        cb = self._events.get(pin)
        if cb:
            cb(pin)


def _load_driver() -> tuple[_BaseDriver, bool]:
    try:
        driver = _JetsonDriver()
        return driver, True
    except Exception:
        return _StubDriver(), False


class GPIOService:
    """Configure GPIO pins and expose helper signals for the application."""

    def __init__(self) -> None:
        self._pinout = _load_pinout()
        self._driver, self._is_hw = _load_driver()
        self._lock = threading.RLock()
        self._trigger_callbacks: list[Callable[[], None]] = []
        self._trigger_monitor_stop = threading.Event()
        self._trigger_monitor_thread: threading.Thread | None = None
        self._last_trigger_levels: dict[int, bool] = {}
        self._last_trigger_edge_ts: dict[int, float] = {}
        self._outputs: dict[str, list[int]] = {role: [] for role in _OUTPUT_ROLES}
        self._inputs: dict[str, list[int]] = {role: [] for role in _INPUT_ROLES}
        self._heartbeat_state = False
        self._recipe = _DEFAULT_RECIPE
        self._config: dict[str, object] = self._load_config()
        self._ensure_profile(self._recipe)
        self._apply_assignments()

    # ------------------------------------------------------------------
    # Properties / metadata
    def is_hardware_ready(self) -> bool:
        return self._is_hw

    def list_pins(self) -> List[PinDefinition]:
        return list(self._pinout)

    def available_roles(self) -> Mapping[str, str]:
        return _ROLE_LABELS

    def output_roles(self) -> Tuple[str, ...]:
        return _OUTPUT_ROLES

    def input_roles(self) -> Tuple[str, ...]:
        return _INPUT_ROLES

    def pin_groups(self) -> Mapping[str, Tuple[int, ...]]:
        return _PIN_GROUPS

    def pin_capabilities(self) -> Mapping[int, Tuple[str, ...]]:
        return _PIN_CAPABILITIES

    def active_recipe(self) -> str:
        return self._recipe

    # ------------------------------------------------------------------
    # Configuration persistence
    def get_assignments(self) -> Dict[int, str]:
        profiles = self._profiles()
        raw = profiles.get(self._recipe, {})
        if isinstance(raw, Mapping):
            result: Dict[int, str] = {}
            for key, value in raw.items():
                try:
                    pin = int(key)
                except Exception:
                    continue
                if isinstance(value, str):
                    result[pin] = value
            return result
        return {}

    def update_assignments(self, assignments: Mapping[int, str]) -> None:
        normalized: dict[str, str] = {}
        for pin, role in assignments.items():
            if role and role != "none":
                normalized[str(int(pin))] = str(role)
        profiles = self._profiles()
        profiles[self._recipe] = normalized
        self._save_config(self._config)
        self._apply_assignments()

    def set_active_recipe(self, recipe: str) -> None:
        name = self._normalize_recipe(recipe)
        with self._lock:
            if name == self._recipe and name in self._profiles():
                return
            self._recipe = name
            self._ensure_profile(self._recipe)
            self._save_config(self._config)
            self._apply_assignments()

    def rename_profile(self, old: str, new: str) -> None:
        src = self._normalize_recipe(old)
        dst = self._normalize_recipe(new)
        if src == dst:
            return
        with self._lock:
            profiles = self._profiles()
            if src not in profiles:
                return
            profiles[dst] = profiles.pop(src)
            if self._recipe == src:
                self._recipe = dst
            self._save_config(self._config)
            self._apply_assignments()

    def delete_profile(self, name: str) -> None:
        target = self._normalize_recipe(name)
        with self._lock:
            profiles = self._profiles()
            if target not in profiles:
                return
            profiles.pop(target, None)
            if self._recipe == target:
                self._recipe = _DEFAULT_RECIPE
                self._ensure_profile(self._recipe)
            self._save_config(self._config)
            self._apply_assignments()

    # ------------------------------------------------------------------
    def register_trigger_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._trigger_callbacks:
            self._trigger_callbacks.append(callback)

    def unregister_trigger_callback(self, callback: Callable[[], None]) -> None:
        try:
            self._trigger_callbacks.remove(callback)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    def emit_heartbeat(self) -> None:
        with self._lock:
            self._heartbeat_state = not self._heartbeat_state
            state = self._driver.HIGH if self._heartbeat_state else self._driver.LOW
            for pin in self._outputs.get("heartbeat", []):
                self._driver.output(pin, state)

    def signal_result(self, status: str, *, pulse_seconds: float = 0.2) -> None:
        status_lc = (status or "").strip().lower()
        targets: list[str] = []
        if status_lc == "ok":
            targets.append("ok")
            other = "nok"
        else:
            targets.append("nok")
            other = "ok"
        # ensure mutual exclusion
        for pin in self._outputs.get(other, []):
            self._driver.output(pin, self._driver.LOW)
        for role in targets:
            for pin in self._outputs.get(role, []):
                self._pulse_pin(pin, pulse_seconds)

    def set_flash(self, channel: int, enabled: bool) -> None:
        role = f"flash{int(channel)}"
        level = self._driver.HIGH if enabled else self._driver.LOW
        for pin in self._outputs.get(role, []):
            self._driver.output(pin, level)

    def pulse_flash(self, channel: int, seconds: float = 0.05) -> None:
        role = f"flash{int(channel)}"
        for pin in self._outputs.get(role, []):
            self._pulse_pin(pin, seconds)

    # ------------------------------------------------------------------
    # Diagnostic helpers
    def configured_output_pins(self) -> Dict[int, str]:
        """Return mapping of configured output pins to their logical role."""

        with self._lock:
            result: Dict[int, str] = {}
            for role, pins in self._outputs.items():
                for pin in pins:
                    result[pin] = role
            return result

    def pulse_outputs(self, pins: Iterable[int], *, pulse_seconds: float = 0.2) -> None:
        """Trigger a short pulse on the provided output pins if they are configured."""

        with self._lock:
            configured = {pin for values in self._outputs.values() for pin in values}
            for pin in pins:
                if pin in configured:
                    self._pulse_pin(int(pin), pulse_seconds)

    def set_outputs_level(self, pins: Iterable[int], *, level: bool) -> None:
        """Drive configured output pins to a fixed logic level."""

        with self._lock:
            configured = {pin for values in self._outputs.values() for pin in values}
            target = self._driver.HIGH if level else self._driver.LOW
            for pin in pins:
                if pin in configured:
                    self._driver.output(int(pin), target)

    def read_pin_states(self, pins: Iterable[int]) -> Dict[int, bool]:
        """Read the current logic level for provided pins."""

        states: Dict[int, bool] = {}
        with self._lock:
            for pin in pins:
                try:
                    states[int(pin)] = bool(self._driver.input(int(pin)))
                except Exception:
                    states[int(pin)] = False
        return states

    # ------------------------------------------------------------------
    def close(self) -> None:
        self._stop_trigger_monitor()
        try:
            self._driver.cleanup()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _pulse_pin(self, pin: int, duration: float) -> None:
        self._driver.output(pin, self._driver.HIGH)
        timer = threading.Timer(duration, lambda: self._driver.output(pin, self._driver.LOW))
        timer.daemon = True
        timer.start()

    def _load_config(self) -> dict[str, object]:
        if _CONFIG_PATH.exists():
            try:
                with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, MutableMapping):
                        cfg = dict(_DEFAULT_CONFIG)
                        cfg.update(data)
                        cfg.pop("assignments", None)
                        cfg["profiles"] = self._sanitize_profiles(cfg.get("profiles"))
                        if not cfg["profiles"]:
                            cfg["profiles"][_DEFAULT_RECIPE] = {}
                        return cfg
            except Exception:
                pass
        cfg = dict(_DEFAULT_CONFIG)
        cfg["profiles"] = {_DEFAULT_RECIPE: {}}
        return cfg

    def _save_config(self, data: Mapping[str, object]) -> None:
        try:
            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _profiles(self) -> dict[str, dict[str, str]]:
        profiles = self._config.setdefault("profiles", {})
        if not isinstance(profiles, MutableMapping):
            profiles = {}
            self._config["profiles"] = profiles
        # ensure sanitized mapping
        sanitized = self._sanitize_profiles(profiles)
        self._config["profiles"] = sanitized
        return sanitized

    def _ensure_profile(self, recipe: str) -> None:
        profiles = self._profiles()
        profiles.setdefault(recipe, {})

    def _sanitize_profiles(self, profiles: object) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        if isinstance(profiles, Mapping):
            for name, mapping in profiles.items():
                if not isinstance(mapping, Mapping):
                    continue
                norm_name = self._normalize_recipe(str(name))
                sanitized_mapping: dict[str, str] = {}
                for pin, role in mapping.items():
                    if not isinstance(role, str):
                        continue
                    sanitized_mapping[str(pin)] = role
                result[norm_name] = sanitized_mapping
        return result

    def _normalize_recipe(self, recipe: str) -> str:
        name = (recipe or "").strip()
        return name or _DEFAULT_RECIPE

    def _apply_assignments(self) -> None:
        with self._lock:
            assignments = self.get_assignments()
            try:
                self._driver.cleanup()
            except Exception:
                pass
            self._driver.setmode_board()
            self._outputs = {role: [] for role in _OUTPUT_ROLES}
            self._inputs = {role: [] for role in _INPUT_ROLES}

            for pin, role in assignments.items():
                if role not in _ALL_ROLES:
                    continue
                try:
                    board_pin = int(pin)
                except Exception:
                    continue
                capabilities = _PIN_CAPABILITIES.get(board_pin, ())
                if role in _OUTPUT_ROLES and "output" not in capabilities:
                    continue
                if role in _INPUT_ROLES and "input" not in capabilities:
                    continue
                if role in _OUTPUT_ROLES:
                    self._driver.setup(board_pin, self._driver.OUT, initial=self._driver.LOW)
                    self._outputs.setdefault(role, []).append(board_pin)
                elif role in _INPUT_ROLES:
                    pud = self._driver.PUD_DOWN if self._driver.PUD_DOWN is not None else None
                    try:
                        self._driver.setup(board_pin, self._driver.IN, pull_up_down=pud)
                    except TypeError:
                        self._driver.setup(board_pin, self._driver.IN)
                    except Exception:
                        self._driver.setup(board_pin, self._driver.IN)
                    self._inputs.setdefault(role, []).append(board_pin)

            # register trigger callbacks last
            for pins in self._inputs.values():
                for pin in pins:
                    self._driver.add_event_detect(pin, "rising", self._handle_trigger_event)

            trigger_pins = tuple(self._inputs.get("trigger", []))

        self._restart_trigger_monitor(trigger_pins)

    def _handle_trigger_event(self, pin: int) -> None:
        self._last_trigger_edge_ts[pin] = time.monotonic()
        callbacks = list(self._trigger_callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def _restart_trigger_monitor(self, trigger_pins: tuple[int, ...]) -> None:
        self._stop_trigger_monitor()
        if not trigger_pins:
            return
        self._trigger_monitor_stop = threading.Event()
        self._trigger_monitor_thread = threading.Thread(
            target=self._poll_trigger_inputs,
            daemon=True,
        )
        self._trigger_monitor_thread.start()

    def _stop_trigger_monitor(self) -> None:
        self._trigger_monitor_stop.set()
        thread = self._trigger_monitor_thread
        if thread and thread.is_alive():
            thread.join(timeout=0.5)
        self._trigger_monitor_thread = None
        self._last_trigger_levels.clear()

    def _poll_trigger_inputs(self) -> None:
        debounce_seconds = 0.02
        while not self._trigger_monitor_stop.wait(0.01):
            with self._lock:
                pins = tuple(self._inputs.get("trigger", ()))
            now = time.monotonic()
            for pin in pins:
                try:
                    level = bool(self._driver.input(pin))
                except Exception:
                    level = False
                last_level = self._last_trigger_levels.get(pin, False)
                if level and not last_level:
                    last_edge = self._last_trigger_edge_ts.get(pin, 0.0)
                    if now - last_edge >= debounce_seconds:
                        self._last_trigger_edge_ts[pin] = now
                        self._handle_trigger_event(pin)
                self._last_trigger_levels[pin] = level
            for pin in list(self._last_trigger_levels.keys()):
                if pin not in pins:
                    self._last_trigger_levels.pop(pin, None)


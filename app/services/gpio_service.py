"""Safe stub GPIO service that keeps configuration but performs no hardware I/O."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, MutableMapping, Tuple

__all__ = [
    "GPIOService",
    "PinDefinition",
]


# Configuration paths / defaults
_CONFIG_PATH = Path("/data/gpio_config.json")
_DEFAULT_RECIPE = "default"
_DEFAULT_CONFIG: dict[str, object] = {
    "board": "JETSON_ORIN_NANO",
    "profiles": {},
}

_OUTPUT_ROLES = ("heartbeat", "ok", "nok", "flash1", "flash2")
_INPUT_ROLES = ("trigger",)
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
    # Pôvodné pinmux rozloženie Jetson Orin Nano 40-pin hlavičky
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
    """Static metadata describing a single header pin."""

    physical: int
    label: str
    description: str
    is_gpio: bool = False


def _load_pinout() -> List[PinDefinition]:
    """Return metadata for the Jetson Orin Nano 40-pin header."""

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


class GPIOService:
    """Configuration-preserving stub that replaces Jetson.GPIO usage."""

    def __init__(self) -> None:
        self._pinout = _load_pinout()
        self._lock = threading.RLock()
        self._trigger_callbacks: list[Callable[[], None]] = []
        self._heartbeat_state = False
        self._recipe = _DEFAULT_RECIPE
        self._config: dict[str, object] = self._load_config()
        self._ensure_profile(self._recipe)
        self._outputs: dict[str, list[int]] = {role: [] for role in _OUTPUT_ROLES}
        self._inputs: dict[str, list[int]] = {role: [] for role in _INPUT_ROLES}
        self._apply_assignments()

    # ------------------------------------------------------------------
    # Properties / metadata
    def is_hardware_ready(self) -> bool:
        return False

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
    # Triggers (no-op)
    def register_trigger_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._trigger_callbacks:
            self._trigger_callbacks.append(callback)

    def unregister_trigger_callback(self, callback: Callable[[], None]) -> None:
        try:
            self._trigger_callbacks.remove(callback)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Output helpers (no-op)
    def emit_heartbeat(self) -> None:
        self._heartbeat_state = not self._heartbeat_state
        print("[GPIO] emit_heartbeat (stub, no hardware)")

    def signal_result(self, status: str, *, pulse_seconds: float = 0.2) -> None:
        print(f"[GPIO] signal_result(status={status}, pulse={pulse_seconds}) (stub)")

    def set_flash(self, channel: int, enabled: bool) -> None:
        print(f"[GPIO] set_flash(channel={channel}, enabled={enabled}) (stub)")

    def pulse_flash(self, channel: int, seconds: float = 0.05) -> None:
        print(f"[GPIO] pulse_flash(channel={channel}, seconds={seconds}) (stub)")

    # ------------------------------------------------------------------
    # Diagnostic helpers
    def configured_output_pins(self) -> Dict[int, str]:
        with self._lock:
            result: Dict[int, str] = {}
            for role, pins in self._outputs.items():
                for pin in pins:
                    result[pin] = role
            return result

    def pulse_outputs(self, pins: Iterable[int], *, pulse_seconds: float = 0.2) -> None:
        print(f"[GPIO] pulse_outputs(pins={list(pins)}, pulse={pulse_seconds}) (stub)")

    def set_outputs_level(self, pins: Iterable[int], *, level: bool) -> None:
        print(f"[GPIO] set_outputs_level(pins={list(pins)}, level={level}) (stub)")

    def read_pin_states(self, pins: Iterable[int]) -> Dict[int, bool]:
        states: Dict[int, bool] = {}
        for pin in pins:
            try:
                states[int(pin)] = False
            except Exception:
                continue
        return states

    # ------------------------------------------------------------------
    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal helpers (config only, no hardware)
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
            self._outputs = {role: [] for role in _OUTPUT_ROLES}
            self._inputs = {role: [] for role in _INPUT_ROLES}
            for pin, role in assignments.items():
                if role not in _ROLE_LABELS:
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
                    self._outputs.setdefault(role, []).append(board_pin)
                elif role in _INPUT_ROLES:
                    self._inputs.setdefault(role, []).append(board_pin)
            print(f"[GPIO] Assignments applied (stub): outputs={self._outputs}, inputs={self._inputs}")

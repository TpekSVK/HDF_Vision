"""Local HDF_Vision configuration for allowed Raspberry Pi Pico inputs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


class PicoConfigService:
    """Persist which physical Pico inputs may be used by future trigger mapping."""

    DEFAULT_PATH = Path("/data/pico_config.json")

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else self.DEFAULT_PATH

    @staticmethod
    def _validate(inputs: Iterable[int]) -> set[int]:
        values = set(inputs)
        if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8 for value in values):
            raise ValueError("Pico input numbers must be integers from 1 to 8")
        return values

    def get_enabled_inputs(self) -> set[int]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return self._validate(data.get("enabled_inputs", []))
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
            return set()

    def set_enabled_inputs(self, inputs: Iterable[int]) -> None:
        values = self._validate(inputs)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"enabled_inputs": sorted(values)}, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._path)

    def is_input_enabled(self, input_index: int) -> bool:
        return input_index in self.get_enabled_inputs()


__all__ = ["PicoConfigService"]

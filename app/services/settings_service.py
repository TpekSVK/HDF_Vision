"""Runtime session settings exposed to UI and services."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

__all__ = [
    "DEFAULT_LOG_DIR",
    "SessionSettings",
    "get_session_settings",
    "update_session_settings",
]


DEFAULT_LOG_DIR = Path("/data/logs")


@dataclass(frozen=True)
class SessionSettings:
    """Mutable-in-practice container for session-scoped toggles."""

    logging_enabled: bool = True
    logging_path: Path = DEFAULT_LOG_DIR
    export_artifacts: bool = True
    export_overlay: bool = True


_CURRENT_SETTINGS = SessionSettings()


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _normalize_path(value: Path | str) -> Path:
    if isinstance(value, Path):
        path = value
    else:
        path = Path(str(value))
    return path.expanduser()


def get_session_settings() -> SessionSettings:
    """Return a defensive copy of the current session settings."""

    return replace(_CURRENT_SETTINGS)


def update_session_settings(**updates: object) -> SessionSettings:
    """Update runtime session settings and return the normalized copy."""

    global _CURRENT_SETTINGS

    allowed: set[str] = {"logging_enabled", "logging_path", "export_artifacts", "export_overlay"}
    unknown: Iterable[str] = [key for key in updates if key not in allowed]
    if unknown:
        unknown_list = ", ".join(sorted(str(k) for k in unknown))
        raise KeyError(f"Unsupported session setting(s): {unknown_list}")

    normalized: dict[str, object] = {}
    for key, value in updates.items():
        if key == "logging_path":
            normalized[key] = _normalize_path(value)  # type: ignore[arg-type]
        else:
            normalized[key] = _to_bool(value)

    _CURRENT_SETTINGS = replace(_CURRENT_SETTINGS, **normalized)
    return get_session_settings()

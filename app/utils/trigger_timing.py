"""Shared trigger timing defaults for See3CAM_CU55 behavior."""

from __future__ import annotations

from typing import Optional

# (width, height, fps) -> minimum trigger period in ms
_TRIGGER_MIN_PERIOD_MS: dict[tuple[int, int, int], float] = {
    (2592, 1944, 30): 33.33,
    (1920, 1080, 60): 16.67,
    (1280, 720, 60): 16.67,
    (640, 480, 112): 8.93,
}

# (width, height, fps) -> user-facing default gap in ms
_TRIGGER_DEFAULT_GAP_MS: dict[tuple[int, int, int], float] = {
    (2592, 1944, 30): 40.0,
    (1920, 1080, 60): 20.0,
    (1280, 720, 60): 20.0,
    (640, 480, 112): 12.0,
}

# (width, height, fps) -> safe internal exposure absolute units for trigger mode
_SAFE_TRIGGER_EXPOSURE_ABS: dict[tuple[int, int, int], int] = {
    (2592, 1944, 30): 400,
    (1920, 1080, 60): 200,
    (1280, 720, 60): 200,
    (640, 480, 112): 120,
}


def _key(width: object, height: object, fps: object) -> Optional[tuple[int, int, int]]:
    try:
        return (int(width), int(height), int(fps))
    except Exception:
        return None


def get_trigger_min_period_ms(width: object, height: object, fps: object) -> float:
    key = _key(width, height, fps)
    if key is None:
        return 16.67
    return float(_TRIGGER_MIN_PERIOD_MS.get(key, 16.67))


def get_default_trigger_gap_ms(width: object, height: object, fps: object) -> float:
    key = _key(width, height, fps)
    if key is None:
        return 20.0
    return float(_TRIGGER_DEFAULT_GAP_MS.get(key, 20.0))


def get_safe_trigger_exposure_abs(width: object, height: object, fps: object) -> int:
    key = _key(width, height, fps)
    if key is None:
        return 200
    return int(_SAFE_TRIGGER_EXPOSURE_ABS.get(key, 200))

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


def get_trigger_runtime_fps(width: object, height: object, fps: object, pixel_format: object) -> float:
    """Return effective CU55 runtime fps for timing calculations."""
    key = _key(width, height, fps)
    try:
        requested_fps = max(1.0, float(fps))
    except Exception:
        requested_fps = 60.0

    if key is None:
        return requested_fps

    pix_fmt = str(pixel_format or "Y8").upper()
    profile_max_fps: dict[str, dict[tuple[int, int], float]] = {
        "Y8": {
            (2592, 1944): 30.0,
            (1920, 1080): 60.0,
            (1280, 720): 60.0,
            (640, 480): 112.0,
        },
        "Y12": {
            (2592, 1944): 14.0,
            (1920, 1080): 30.0,
            (1280, 720): 60.0,
            (640, 480): 112.0,
        },
    }
    max_fps = profile_max_fps.get(pix_fmt, {}).get((key[0], key[1]))
    if max_fps is None:
        return requested_fps
    return min(requested_fps, float(max_fps))


def get_trigger_frame_time_ms(width: object, height: object, fps: object, pixel_format: object) -> float:
    runtime_fps = get_trigger_runtime_fps(width, height, fps, pixel_format)
    return 1000.0 / max(1.0, float(runtime_fps))


def get_safe_priming_gap_ms(
    width: object,
    height: object,
    fps: object,
    pixel_format: object,
    *,
    configured_min_priming_gap_ms: float = 5.0,
    safety_margin_ms: float = 3.0,
) -> float:
    """Calculate conservative priming gap: max(frame_time + margin, configured minimum)."""
    frame_time_ms = get_trigger_frame_time_ms(width, height, fps, pixel_format)
    base = frame_time_ms + max(0.0, float(safety_margin_ms))
    return max(base, max(0.0, float(configured_min_priming_gap_ms)))

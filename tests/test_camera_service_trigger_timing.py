from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - test environment shim
    sys.path.insert(0, str(ROOT))

import types

if "cv2" not in sys.modules:  # pragma: no cover - optional dependency shim
    cv2_stub = types.SimpleNamespace(
        COLOR_BGR2GRAY=0,
        cvtColor=lambda frame, _code: frame,
        convertScaleAbs=lambda frame, alpha=1.0: frame,
    )
    sys.modules["cv2"] = cv2_stub

from app.services.camera_service import CameraService


def _build_camera(width: int, height: int, fps: int, pixel_format: str, exposure_us: int) -> CameraService:
    cam = CameraService(device="/dev/video0", width=width, height=height, fps=fps)
    cam.pixel_format = pixel_format
    cam.exposure_us = exposure_us
    return cam


def test_compute_trigger_timing_y8_1080p60() -> None:
    cam = _build_camera(1920, 1080, 60, "Y8", 8000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 16.67
    assert round(timing.effective_trigger_gap_ms, 2) == 19.67


def test_compute_trigger_timing_y12_1080p_uses_30fps_runtime() -> None:
    cam = _build_camera(1920, 1080, 60, "Y12", 32000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 33.33
    assert round(timing.effective_trigger_gap_ms, 2) == 36.33


def test_compute_trigger_timing_y12_5mp_uses_14fps_runtime() -> None:
    cam = _build_camera(2592, 1944, 60, "Y12", 70000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 71.43
    assert round(timing.effective_trigger_gap_ms, 2) == 74.43


def test_resolve_trigger_timeout_extends_too_short_value() -> None:
    cam = _build_camera(1920, 1080, 60, "Y8", 20000)
    timing = cam._compute_trigger_timing()

    timeout_s = cam._resolve_trigger_timeout_s(0.005, timing)

    assert round(timeout_s * 1000.0, 2) == round(timing.timeout_min_ms, 2)


def test_compute_trigger_timing_y8_5mp_runtime_cap() -> None:
    cam = _build_camera(2592, 1944, 60, "Y8", 20000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 33.33
    assert round(timing.effective_trigger_gap_ms, 2) == 36.33


def test_compute_trigger_timing_y12_720p60() -> None:
    cam = _build_camera(1280, 720, 60, "Y12", 14000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 16.67
    assert round(timing.effective_trigger_gap_ms, 2) == 19.67


def test_compute_trigger_timing_y12_640x480_112() -> None:
    cam = _build_camera(640, 480, 120, "Y12", 5000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 8.93
    assert round(timing.effective_trigger_gap_ms, 2) == 11.93

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - test environment shim
    sys.path.insert(0, str(ROOT))

import types

import numpy as np

if "cv2" not in sys.modules:  # pragma: no cover - optional dependency shim
    cv2_stub = types.SimpleNamespace(
        COLOR_BGR2GRAY=0,
        cvtColor=lambda frame, _code: frame,
        convertScaleAbs=lambda frame, alpha=1.0: frame,
    )
    sys.modules["cv2"] = cv2_stub

from app.services.camera_service import CameraService
from app.utils.trigger_timing import (
    get_default_trigger_gap_ms,
    get_safe_priming_gap_ms,
    get_safe_trigger_exposure_abs,
    get_trigger_min_period_ms,
)


def _build_camera(width: int, height: int, fps: int, pixel_format: str, exposure_us: int) -> CameraService:
    cam = CameraService(device="/dev/video0", width=width, height=height, fps=fps)
    cam.pixel_format = pixel_format
    cam.exposure_us = exposure_us
    return cam


def test_compute_trigger_timing_y8_1080p60() -> None:
    cam = _build_camera(1920, 1080, 60, "Y8", 8000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 16.67
    assert round(timing.trigger_gap_ms, 2) == 19.67
    assert timing.priming_gap_ms >= timing.frame_time_ms


def test_compute_trigger_timing_y12_1080p_uses_30fps_runtime() -> None:
    cam = _build_camera(1920, 1080, 60, "Y12", 32000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 33.33
    assert round(timing.trigger_gap_ms, 2) == 36.33


def test_compute_trigger_timing_y12_5mp_uses_14fps_runtime() -> None:
    cam = _build_camera(2592, 1944, 60, "Y12", 70000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 71.43
    assert round(timing.trigger_gap_ms, 2) == 74.43


def test_resolve_trigger_timeout_extends_too_short_value() -> None:
    cam = _build_camera(1920, 1080, 60, "Y8", 20000)
    timing = cam._compute_trigger_timing()

    timeout_s = cam._resolve_trigger_timeout_s(0.005, timing)

    assert round(timeout_s * 1000.0, 2) == round(timing.timeout_min_ms, 2)


def test_compute_trigger_timing_y8_5mp_runtime_cap() -> None:
    cam = _build_camera(2592, 1944, 60, "Y8", 20000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 33.33
    assert round(timing.trigger_gap_ms, 2) == 36.33


def test_compute_trigger_timing_y12_720p60() -> None:
    cam = _build_camera(1280, 720, 60, "Y12", 14000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 16.67
    assert round(timing.trigger_gap_ms, 2) == 19.67


def test_compute_trigger_timing_y12_640x480_112() -> None:
    cam = _build_camera(640, 480, 120, "Y12", 5000)

    timing = cam._compute_trigger_timing()

    assert round(timing.frame_time_ms, 2) == 8.93
    assert round(timing.trigger_gap_ms, 2) == 11.93


def test_compute_trigger_timing_uses_explicit_gap() -> None:
    cam = _build_camera(1280, 720, 60, "Y8", 8000)

    timing = cam._compute_trigger_timing(trigger_gap_ms=20.0, pulse_ms=10.0)

    assert round(timing.trigger_gap_ms, 2) == 20.0
    assert round(timing.effective_period_ms, 2) == 30.0
    assert round(timing.priming_gap_ms, 2) == 19.67


def test_trigger_defaults_and_safe_exposure_mapping() -> None:
    assert get_default_trigger_gap_ms(1280, 720, 60) == 20.0
    assert get_trigger_min_period_ms(1280, 720, 60) == 16.67
    assert get_safe_trigger_exposure_abs(1280, 720, 60) == 200
    assert round(get_safe_priming_gap_ms(1280, 720, 60, "Y8"), 2) == 19.67


def test_perform_trigger_sequence_discards_priming_frames(monkeypatch) -> None:
    cam = _build_camera(1280, 720, 60, "Y8", 8000)
    timing = cam._compute_trigger_timing(trigger_gap_ms=20.0, pulse_ms=10.0)

    events: list[str] = []
    cam._clear_trigger_sample_state = lambda: events.append("clear")  # type: ignore[method-assign]

    def fake_trigger(*, trigger_fn=None, note: str, timing=None, gap_ms=None):
        _ = trigger_fn, note, timing, gap_ms
        events.append("trigger")

    samples = iter(
        [
            np.full((2, 2), 10, dtype=np.uint8),
            np.full((2, 2), 20, dtype=np.uint8),
            np.full((2, 2), 30, dtype=np.uint8),
        ]
    )

    def fake_wait(_timeout: float):
        events.append("wait")
        return next(samples, None)

    sleeps: list[float] = []

    def fake_sleep(seconds: float):
        sleeps.append(float(seconds))
        events.append("sleep")

    cam._trigger_via_hw = fake_trigger  # type: ignore[method-assign]
    cam._wait_for_sample = fake_wait  # type: ignore[method-assign]
    monkeypatch.setattr("app.services.camera_service.time.sleep", fake_sleep)

    frame = cam._perform_trigger_sequence(trigger_fn=None, timeout_s=0.3, timing=timing)

    assert frame is not None
    assert int(frame[0, 0]) == 30
    assert events == [
        "clear",
        "trigger", "wait", "sleep",
        "trigger", "wait", "sleep",
        "trigger", "wait",
    ]
    assert len(sleeps) == 2
    assert round(sleeps[0] * 1000.0, 2) == round(timing.priming_gap_ms, 2)
    assert round(sleeps[1] * 1000.0, 2) == round(timing.priming_gap_ms, 2)


def test_trigger_via_hw_does_not_sleep(monkeypatch) -> None:
    cam = _build_camera(1280, 720, 60, "Y8", 8000)
    timing = cam._compute_trigger_timing(trigger_gap_ms=20.0, pulse_ms=10.0)

    fired: list[str] = []
    cam._fire_trigger = lambda **_kwargs: fired.append("fire")  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    def fake_sleep(seconds: float):
        sleep_calls.append(float(seconds))

    monkeypatch.setattr("app.services.camera_service.time.sleep", fake_sleep)

    cam._trigger_via_hw(trigger_fn=None, note="test", timing=timing, gap_ms=timing.trigger_gap_ms)

    assert fired == ["fire"]
    assert sleep_calls == []


def test_capture_trigger_frame_uses_three_pulse_sequence(monkeypatch) -> None:
    cam = _build_camera(1280, 720, 60, "Y8", 8000)
    timing = cam._compute_trigger_timing(trigger_gap_ms=20.0, pulse_ms=10.0)
    expected = np.full((3, 3), 7, dtype=np.uint8)

    monkeypatch.setattr(cam, "get_stream_mode", lambda: 1)
    monkeypatch.setattr(cam, "ensure_trigger_session", lambda **_kwargs: True)
    monkeypatch.setattr(cam, "_compute_trigger_timing", lambda **_kwargs: timing)
    monkeypatch.setattr(cam, "_resolve_trigger_timeout_s", lambda _timeout, _timing: 0.8)

    called: list[tuple[float, float, float]] = []

    def fake_sequence(*, trigger_fn=None, timeout_s: float, timing):
        called.append((float(timeout_s), float(timing.priming_gap_ms), float(timing.trigger_gap_ms)))
        return expected

    monkeypatch.setattr(cam, "_perform_trigger_sequence", fake_sequence)

    frame = cam.capture_trigger_frame(trigger_gap_ms=20.0, trigger_mode_label="manual_gpio")

    assert np.array_equal(frame, expected)
    assert called == [(0.8, timing.priming_gap_ms, timing.trigger_gap_ms)]


def test_perform_trigger_sequence_returns_none_on_second_pulse_timeout(monkeypatch) -> None:
    cam = _build_camera(1280, 720, 60, "Y8", 8000)
    timing = cam._compute_trigger_timing(trigger_gap_ms=20.0, pulse_ms=10.0)

    events: list[str] = []
    cam._clear_trigger_sample_state = lambda: events.append("clear")  # type: ignore[method-assign]

    def fake_trigger(*, trigger_fn=None, note: str, timing=None, gap_ms=None):
        _ = trigger_fn, note, timing, gap_ms
        events.append("trigger")

    samples = iter([
        np.full((2, 2), 10, dtype=np.uint8),
        None,
    ])

    def fake_wait(_timeout: float):
        events.append("wait")
        return next(samples, None)

    sleeps: list[float] = []

    def fake_sleep(seconds: float):
        sleeps.append(float(seconds))
        events.append("sleep")

    cam._trigger_via_hw = fake_trigger  # type: ignore[method-assign]
    cam._wait_for_sample = fake_wait  # type: ignore[method-assign]
    monkeypatch.setattr("app.services.camera_service.time.sleep", fake_sleep)

    frame = cam._perform_trigger_sequence(trigger_fn=None, timeout_s=0.3, timing=timing)

    assert frame is None
    assert events == ["clear", "trigger", "wait", "sleep", "trigger", "wait"]
    assert len(sleeps) == 1
    assert round(sleeps[0] * 1000.0, 2) == round(timing.priming_gap_ms, 2)


def test_compute_trigger_timing_defaults_production_gap_without_recipe_value() -> None:
    cam = _build_camera(1280, 720, 60, "Y8", 200)

    timing = cam._compute_trigger_timing(trigger_gap_ms=None, pulse_ms=10.0)

    assert round(timing.trigger_gap_ms, 2) == 19.67
    assert timing.trigger_gap_ms >= timing.frame_time_ms

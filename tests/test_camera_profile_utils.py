from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - test environment shim
    sys.path.insert(0, str(ROOT))

if "app.utils.imaging" not in sys.modules:  # pragma: no cover - test shim
    imaging_stub = types.ModuleType("app.utils.imaging")
    imaging_stub.encode_mask_to_blob = lambda value: value
    imaging_stub.decode_mask_from_blob = lambda value: value
    sys.modules["app.utils.imaging"] = imaging_stub

from app.models.schema import ViewCameraProfile
from app.ui.camera_profile_utils import (
    apply_camera_state,
    apply_view_camera_profile,
    resolve_view_camera_state,
    snapshot_camera_state,
)


class DummyCamera:
    def __init__(
        self,
        width: int,
        height: int,
        fps: int,
        pixel_format: str,
        exposure_us: int | None = None,
        gain_db: float | None = None,
        *,
        fail_resolution: bool = False,
        fail_device_switch: bool = False,
        device: str = "/dev/video0",
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.pixel_format = pixel_format
        self.exposure_us = exposure_us
        self.gain_db = gain_db
        self._fail_resolution = fail_resolution
        self._fail_device_switch = fail_device_switch
        self.device = device

    def apply_resolution(
        self,
        *,
        width: int,
        height: int,
        fps: int,
        pixel_format: str | None = None,
    ) -> None:
        if self._fail_resolution:
            raise RuntimeError("hardware resolution error")
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        if pixel_format is not None:
            self.pixel_format = pixel_format

    def select_device(self, device: str) -> None:
        if self._fail_device_switch:
            raise RuntimeError("switch error")
        self.device = str(device)

    def set_manual_exposure_us(self, value: int) -> None:
        self.exposure_us = int(value)

    def set_gain_db(self, value: int) -> None:
        self.gain_db = int(value)


def test_snapshot_camera_state_reads_camera_properties():
    cam = DummyCamera(1280, 720, 60, "y8", exposure_us=8500, gain_db=3.25)

    state = snapshot_camera_state(cam)

    assert state["width"] == 1280
    assert state["height"] == 720
    assert state["fps"] == 60
    assert state["pixel_format"] == "Y8"
    assert state["exposure_us"] == 8500
    assert state["gain_db"] == pytest.approx(3.25)
    assert state["device"] == "/dev/video0"


def test_apply_camera_state_updates_camera_configuration():
    cam = DummyCamera(640, 480, 30, "Y8", exposure_us=4000, gain_db=1)

    apply_camera_state(
        cam,
        {
            "width": 1280,
            "height": 720,
            "fps": 60,
            "pixel_format": "y12",
            "exposure_us": 9000,
            "gain_db": 4.4,
        },
    )

    assert (cam.width, cam.height, cam.fps) == (1280, 720, 60)
    assert cam.pixel_format == "Y12"
    assert cam.exposure_us == 9000
    assert cam.gain_db == 4


def test_apply_camera_state_uses_warning_callback_on_failure():
    cam = DummyCamera(640, 480, 30, "Y8", fail_resolution=True)
    messages: list[str] = []

    apply_camera_state(
        cam,
        {"width": 1280, "height": 720, "fps": 60},
        warn=messages.append,
    )

    assert messages
    assert "Zmena rozlíšenia" in messages[0]
    assert (cam.width, cam.height, cam.fps) == (640, 480, 30)


def test_apply_view_camera_profile_merges_base_with_overrides():
    cam = DummyCamera(640, 480, 30, "Y8", exposure_us=5000, gain_db=2)
    base = {
        "width": 640,
        "height": 480,
        "fps": 30,
        "pixel_format": "Y8",
        "exposure_us": 5000,
        "gain_db": 2.0,
    }
    profile = ViewCameraProfile(height=720, fps=60, exposure_us=9000)

    apply_view_camera_profile(cam, base, profile)

    assert (cam.width, cam.height, cam.fps) == (640, 720, 60)
    assert cam.pixel_format == "Y8"
    assert cam.exposure_us == 9000
    assert cam.gain_db == 2


def test_resolve_view_camera_state_prefers_profile_device_override():
    state = resolve_view_camera_state({"device": "/dev/video0"}, ViewCameraProfile(device="/dev/video1"))

    assert state["device"] == "/dev/video1"


def test_apply_view_camera_profile_switches_camera_device():
    cam = DummyCamera(640, 480, 30, "Y8", device="/dev/video0")

    apply_view_camera_profile(cam, {"device": "/dev/video0"}, {"device": "/dev/video1"})

    assert cam.device == "/dev/video1"

from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - test environment shim
    sys.path.insert(0, str(ROOT))

if "cv2" not in sys.modules:  # pragma: no cover - test shim
    cv2_stub = types.ModuleType("cv2")
    cv2_stub.CAP_V4L2 = 200
    cv2_stub.CAP_PROP_BUFFERSIZE = 38

    class _DummyCap:
        def __init__(self, *args, **kwargs):
            pass

        def isOpened(self):
            return False

        def set(self, *_args, **_kwargs):
            return False

        def read(self):
            return False, None

        def release(self):
            return None

    cv2_stub.VideoCapture = _DummyCap
    sys.modules["cv2"] = cv2_stub

from app.services.camera_service import CameraService


def test_list_video_nodes_sorted_and_numeric_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.camera_service.glob.glob",
        lambda pattern: [
            "/dev/video10",
            "/dev/video2",
            "/dev/videoX",
            "/dev/video0",
        ],
    )

    devices = CameraService._list_video_nodes()

    assert devices == ["/dev/video0", "/dev/video2", "/dev/video10"]


def test_enumerate_capture_devices_prefers_selected_and_filters_non_capture(monkeypatch):
    svc = CameraService.__new__(CameraService)

    monkeypatch.setattr(
        svc,
        "_list_video_nodes",
        lambda: ["/dev/video0", "/dev/video1", "/dev/video2", "/dev/video4"],
    )

    readable = {"/dev/video0", "/dev/video2"}
    monkeypatch.setattr(svc, "_can_read_single_frame", lambda dev: dev in readable)

    devices = svc._enumerate_capture_devices(preferred="/dev/video2")

    assert devices == ["/dev/video2", "/dev/video0"]

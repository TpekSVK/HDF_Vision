"""Resource ownership and capture parity after the A6 extraction."""
from types import SimpleNamespace
import threading
import time
import numpy as np
import pytest
from app.services.camera_service import CameraService
from app.services.view_capture import ViewCapture
from app.ui.roi.history import EditHistory


def test_two_cameras_have_isolated_frame_reservations_and_pio_generations():
    a, b = CameraService('/dev/camera-a'), CameraService('/dev/camera-b')
    ra, rb = a.arm_master_frame(), b.arm_master_frame()
    image = np.ones((3, 4), np.uint8)
    a.stream._publish_master_frame(image, time.monotonic())
    np.testing.assert_array_equal(a.wait_master_frame(ra, .01), image)
    assert rb['frame'] is None
    b.finish_master_frame(rb)
    with pytest.raises(RuntimeError):
        b.wait_master_frame(rb, .01)
    before = b.pio._pio_generation
    a.pio._invalidate_pio()
    assert b.pio._pio_generation == before
    assert a.devices == ['/dev/camera-a'] and b.devices == ['/dev/camera-b']


def test_disconnected_camera_does_not_open_other_device(monkeypatch):
    a = CameraService('/dev/missing-camera')
    attempts = []
    monkeypatch.setattr('app.services.camera_stream._GST_OK', False)
    a.stream._start_v4l2 = lambda dev: attempts.append(dev) or False
    with pytest.raises(RuntimeError):
        a.start()
    assert attempts == ['/dev/missing-camera']


def test_history_snapshots_are_not_mutated_by_later_edits():
    h = EditHistory(2)
    original = {'points': [1, 2]}
    h.record(original)
    original['points'][0] = 99
    assert h.undo({'points': [3, 4]}) == {'points': [1, 2]}
    assert h.redo({'points': [1, 2]}) == {'points': [3, 4]}
    h.record({'points': [5]})
    assert not h.future


def test_shared_capture_rejects_unknown_mode_before_any_pulse():
    pico = SimpleNamespace(prepare_trigger=lambda camera: pytest.fail('unexpected pulse'))
    with pytest.raises(ValueError, match='režim'):
        ViewCapture(SimpleNamespace(), pico, None, 'unknown').capture(
            view=SimpleNamespace(id='v'), trigger_mode_label='test', master_caller='test')


def test_retired_stream_callbacks_cannot_deliver_or_invalidate_new_request(monkeypatch):
    from app.services import camera_stream
    a = CameraService('/dev/camera-a')
    old_sink, new_sink = object(), object()
    old_bus, new_bus = object(), object()
    a.stream._sink, a.stream._bus = new_sink, new_bus
    a.pio._pio_ready = ('ready',)
    monkeypatch.setattr(camera_stream, 'Gst', SimpleNamespace(FlowReturn=SimpleNamespace(OK='ignored')))
    a.stream._consume_gst_sample = lambda sink: pytest.fail('retired frame consumed')
    assert a.stream._on_new_sample(old_sink) == 'ignored'
    a.stream._gst_bus_cb(old_bus, SimpleNamespace())
    a.stream.stop(expected_bus=old_bus)
    assert a.pio._pio_ready == ('ready',)
    assert a.stream._bus is new_bus


def test_two_real_gstreamer_test_sources_keep_independent_loops_and_delivery(monkeypatch):
    from app.services import camera_stream
    if not camera_stream._GST_OK:
        pytest.skip('GStreamer unavailable')
    cameras = [CameraService(f'/dev/synthetic-{i}', width=16, height=12, fps=30) for i in range(2)]
    for i, camera in enumerate(cameras):
        pipeline = (f'videotestsrc is-live=true pattern={i} ! '
            'video/x-raw,format=GRAY8,width=16,height=12,framerate=30/1 ! '
            'appsink name=sink emit-signals=true sync=false drop=true max-buffers=2')
        monkeypatch.setattr(camera.stream, '_gst_pipeline_str', lambda *a, pipe=pipeline, **kw: pipe)
    try:
        assert cameras[0].stream._start_gst('synthetic')
        assert cameras[1].stream._start_gst('synthetic')
        assert cameras[0].stream._loop.get_context() != cameras[1].stream._loop.get_context()
        for camera in cameras:
            frame = camera.wait_master_frame(camera.arm_master_frame(), 2)
            assert frame.shape == (12, 16)
        cameras[0].stop(caller='test')
        frame = cameras[1].wait_master_frame(cameras[1].arm_master_frame(), 2)
        assert frame.shape == (12, 16)
    finally:
        for camera in cameras:
            camera.stop(caller='test_cleanup')

from collections import deque
from types import SimpleNamespace
from app.services.camera_pio import PioCapture
import pytest


def test_live_frames_reach_preview_without_invalidating_trigger():
    camera = SimpleNamespace(stream=SimpleNamespace(_ring=deque(maxlen=5)))
    capture = PioCapture(camera)
    signature = object()
    capture._pio_ready = signature
    capture._live_preview = True
    frame = object()
    capture._publish_pio_frame(frame, 10)
    assert camera.stream._ring[-1] is frame
    assert capture._pio_ready is signature
    assert not capture._pio_unexpected
    capture._live_preview = False
    capture._publish_pio_frame(object(), 11)
    assert capture._pio_unexpected
    assert capture._pio_ready is None


def test_live_drain_failure_invalidates_readiness_and_exits_preview(monkeypatch):
    ring = deque([object()], maxlen=5)
    camera = SimpleNamespace(stream=SimpleNamespace(_ring=ring, _clear_ring=ring.clear))
    capture = PioCapture(camera)
    capture._live_preview = True
    capture._pio_ready = object()
    def fail(**kwargs):
        raise RuntimeError('late frames never stopped')
    monkeypatch.setattr(capture, '_pio_quiet', fail)
    with pytest.raises(RuntimeError, match='late frames'):
        capture.end_live_preview()
    assert capture._pio_ready is None and capture._pio_recovery_required
    assert not capture._live_preview and not ring


def test_first_prime_is_never_published_to_live_ring():
    ring=deque([object()],maxlen=5)
    camera=SimpleNamespace(stream=SimpleNamespace(_ring=ring,_clear_ring=ring.clear))
    capture=PioCapture(camera)
    capture.begin_live_preview()
    assert not ring
    capture._publish_pio_frame(object(),11)
    assert not ring and capture._live_discarded==1
    frame=object();capture._publish_pio_frame(frame,12)
    capture.wait_live_preview(timeout_s=.01)
    assert ring[-1] is frame


def test_live_stop_keeps_late_frames_out_of_production(monkeypatch):
    ring=deque(maxlen=5)
    camera=SimpleNamespace(stream=SimpleNamespace(_ring=ring,_clear_ring=ring.clear))
    capture=PioCapture(camera);capture.begin_live_preview();ready=object();capture._pio_ready=ready
    def drain(**kwargs):
        assert capture._live_preview
        capture._publish_pio_frame(object(),11)
        capture._publish_pio_frame(object(),12)
    monkeypatch.setattr(capture,'_pio_quiet',drain)
    capture.end_live_preview()
    assert not capture._live_preview and capture._pio_ready is ready
    assert not ring
    assert not capture._pio_unexpected

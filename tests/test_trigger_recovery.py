from threading import Event, RLock
from types import SimpleNamespace
import json
import pytest
from app.services.capture_errors import IncompletePioPair
from app.services.inspection_controller import InspectionController, InspectionState
from app.services.trigger_recovery import recover_trigger
from app.services.camera_pio import PioCapture


def test_two_attempts_audit_and_failed_part_preserved(tmp_path):
    controller = InspectionController('camera_2')
    controller.prepare(lambda: None, lambda: None)
    request, _ = controller.admit('pico')
    failure = IncompletePioPair(1, 2)
    controller.finish(request, error=failure)
    budget = controller.begin_recovery()
    calls = []
    def recover(*args, **kwargs):
        assert controller.admit('modbus')[1] == 'preparing'
        calls.append('attempt')
        if len(calls) == 1:
            raise failure
    runtime = SimpleNamespace(data_root=tmp_path, camera_id='camera_2', pico_id='pico_2',
        cam=SimpleNamespace(pio=SimpleNamespace(recover_trigger_mode=recover)),
        pico=SimpleNamespace(quiesce=lambda: None))
    result = recover_trigger(runtime, request.id, failure, budget, Event(), lambda _: None)
    controller.finish_recovery(result)
    assert result['ok'] and result['attempts'] == 2
    assert controller.snapshot()['counts']['failed'] == 1
    assert controller.state == InspectionState.READY
    rows = [json.loads(x) for x in next((tmp_path/'logs').glob('*.jsonl')).read_text().splitlines()]
    assert [r['event'] for r in rows] == ['started', 'attempt_started', 'attempt_failed', 'attempt_started', 'succeeded']
    assert all(r['request_id'] == request.id and r['camera_id'] == 'camera_2' for r in rows)
    # A successful verification pair must not reset the consecutive budget.
    request, _ = controller.admit('manual')
    controller.finish(request, error=failure)
    assert controller.begin_recovery() == 0
    assert controller.state == InspectionState.ERROR


def test_successful_inspection_resets_budget():
    c = InspectionController()
    c.prepare(lambda: None, lambda: None)
    req, _ = c.admit('pico'); c.finish(req, error=IncompletePioPair(0, 2))
    assert c.begin_recovery() == 2
    c.finish_recovery(dict(ok=True, attempts=1, error=''))
    req, _ = c.admit('pico'); c.finish(req)
    req, _ = c.admit('pico'); c.finish(req, error=IncompletePioPair(1, 2))
    assert c.begin_recovery() == 2


@pytest.mark.parametrize('cancelled', [False, True])
def test_failure_or_cancellation_never_loops(tmp_path, cancelled):
    cancel = Event()
    if cancelled: cancel.set()
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise RuntimeError('HID unavailable')
    runtime = SimpleNamespace(data_root=tmp_path, camera_id='one', pico_id='p',
        cam=SimpleNamespace(pio=SimpleNamespace(recover_trigger_mode=fail)),
        pico=SimpleNamespace(quiesce=lambda: None))
    result = recover_trigger(runtime, 'req', IncompletePioPair(0, 2), 2, cancel, lambda _: None)
    assert not result['ok']
    assert len(calls) == (0 if cancelled else 2)


def test_mode_recovery_retries_get_not_set_and_preserves_stream():
    reads = iter([1, RuntimeError('Invalid HID command echo: 0x00'), 0, 0, 1, 1])
    writes, retries, bursts, sessions = [], [], [], []
    def get():
        item = next(reads)
        if isinstance(item, Exception): raise item
        return item
    hid = SimpleNamespace(get_stream_mode=get, set_stream_mode=writes.append)
    cam = SimpleNamespace(stream=SimpleNamespace(_pipeline=object()),
        gst_start_count=lambda: 1, is_pipeline_open=lambda: True, _ensure_hid=lambda: hid,
        width=1920, height=1080, fps=60, pixel_format='Y8', exposure_us=1000,
        set_manual_exposure_us=lambda _: None)
    pio = PioCapture(cam)
    pio._pio_wait_master_frames=lambda: None
    pio._pio_quiet=lambda: None
    pio._pio_burst=lambda *a, **kw: bursts.append(kw)
    pio._pio_signature=lambda: 'verified'
    pico = SimpleNamespace(_capture_request_lock=RLock(), set_session_mode=sessions.append)
    pio.recover_trigger_mode(pico, cancel=Event(), diagnostic=lambda **kw: retries.append(kw))
    assert writes == [0, 1]
    assert len(retries) == 1
    assert len(bursts) == 2 and bursts[-1]['settling']
    assert sessions == ['IDLE', 'TRIGGER']
    assert pio._pio_ready == 'verified' and not pio._pio_recovery_required

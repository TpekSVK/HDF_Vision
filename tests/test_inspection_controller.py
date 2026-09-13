from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from app.services.inspection_controller import InspectionController, InspectionState


def ready():
    controller = InspectionController()
    assert controller.prepare(lambda: None, lambda: None)
    return controller


def test_readiness_waits_for_hardware_and_failure_quiesces():
    controller = InspectionController()
    observations = []
    def prepare():
        observations.append(controller.state)
        assert controller.admit('pico')[1] == 'preparing'
        raise RuntimeError('camera profile failed')
    assert not controller.prepare(prepare, lambda: observations.append('idle'))
    assert observations == [InspectionState.PREPARING, 'idle']
    assert controller.state == InspectionState.ERROR
    assert controller.admit('manual')[0] is None
    assert controller.prepare(lambda: None, lambda: None)
    assert controller.state == InspectionState.READY


def test_concurrent_admission_has_one_owner_no_queue():
    controller = ready()
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(controller.admit, ['pico'] * 100))
    accepted = [request for request, _ in answers if request]
    assert len(accepted) == 1
    assert controller.snapshot()['counts'] == {'received': 100, 'accepted': 1, 'rejected_busy': 99}
    assert not controller.pause(lambda: pytest.fail('Busy hardware stopped'))
    controller.finish(accepted[0])
    with pytest.raises(ValueError):
        controller.finish(accepted[0])
    assert controller.snapshot()['counts']['completed'] == 1


def test_sequence_without_window_keeps_request_identity_and_rejects_branch_loop():
    controller = ready()
    request, _ = controller.admit('modbus')
    first = {'view': SimpleNamespace(id='one'), 'index': 0}
    second = {'view': SimpleNamespace(id='two'), 'index': 1}
    state = {'views_to_process': [first, second]}
    executed = []
    final = []
    controller.run_sequence(request, state,
                            lambda spec, state: executed.append(spec['view'].id) or {},
                            lambda state: final.append(state['request_id']))
    assert executed == ['one', 'two']
    assert final == [request.id]
    with pytest.raises(ValueError, match='opakuje'):
        controller.run_sequence(request, state, lambda *_: {'replace_queue': [first]}, lambda _: None)
    controller.finish(request, error='cycle')
    assert controller.state == InspectionState.ERROR


def test_pause_and_close_disable_admission_before_hardware():
    controller = ready()
    states = []
    assert controller.pause(lambda: states.append(controller.admit('pico')[1]))
    assert states == ['preparing']
    assert controller.admit('pico')[1] == 'paused'
    assert controller.pause(lambda: None, close=True)
    assert not controller.prepare(lambda: pytest.fail('closed restarted'), lambda: None)
    assert controller.admit('manual')[1] == 'closed'


def test_failed_stop_does_not_claim_paused():
    controller = ready()
    def fail():
        raise RuntimeError('Pico busy')
    assert not controller.pause(fail)
    assert controller.state == InspectionState.ERROR

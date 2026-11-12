from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models.schema import RecipeView
from app.services.dual_camera_rules import validate_view_routing
from app.services.inspection_groups import InspectionGroups


def views():
    return [RecipeView(id=f'view_{i}', name=f'Pohľad {i}', camera_id=f'camera_{i}',
                       output_group='group_1', output_mode='AND', and_timeout_ms=100)
            for i in (1, 2)]


def test_camera_routing_survives_serialization_and_copy():
    view = views()[1]
    assert RecipeView.from_dict(view.to_dict()).copy().to_dict() == view.to_dict()


def test_one_camera_waits_peer_without_retrigger_or_premature_output():
    output = []
    groups = InspectionGroups(views(), output.append, timers=False)
    first, _ = groups.admit(['view_1'])
    groups.complete(first, {'view_1': 'ok'})
    assert output == []
    assert groups.admit(['view_1'])[0] is None
    second, reason = groups.admit(['view_2'])
    assert second is not None, reason
    groups.complete(second, {'view_2': 'nok'})
    assert len(output) == 1 and output[0].status == 'nok'
    assert output[0].missing_views == ()
    assert groups.admit(['view_1'])[0] is not None


def test_timeout_releases_waiting_group_and_late_result_cannot_complete_next_cycle():
    now, output = [0], []
    groups = InspectionGroups(views(), output.append, clock=lambda: now[0], timers=False)
    first, _ = groups.admit(['view_1'])
    second, _ = groups.admit(['view_2'])
    groups.complete(first, {'view_1': 'ok'})
    now[0] = .101
    groups.expire()
    assert output[0].status == 'nok' and output[0].missing_views == ('view_2',)
    new_first, _ = groups.admit(['view_1'])
    assert new_first.cycles != first.cycles
    # The previous camera-2 acquisition still owns its lane until it returns.
    assert groups.admit(['view_2'])[0] is None
    groups.complete(second, {'view_2': 'ok'})
    assert len(output) == 1
    groups.complete(new_first, {'view_1': 'ok'})
    assert len(output) == 1
    new_second, _ = groups.admit(['view_2'])
    groups.complete(new_second, {'view_2': 'ok'})
    assert len(output) == 2 and output[1].status == 'ok'


def test_simultaneous_results_publish_exactly_once():
    output = []
    groups = InspectionGroups(views(), output.append, timers=False)
    tickets = [groups.admit([f'view_{i}'])[0] for i in (1, 2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda ticket: groups.complete(ticket, {ticket.view_ids[0]: 'ok'}), tickets))
    assert len(output) == 1 and output[0].status == 'ok'
    assert groups.complete(tickets[0], {'view_1': 'ok'}) == []


def test_independent_cameras_publish_without_waiting():
    configured = views()
    for i, view in enumerate(configured, 1):
        view.output_mode, view.output_group = 'independent', f'group_{i}'
    output = []
    groups = InspectionGroups(configured, output.append, timers=False)
    first, _ = groups.admit(['view_1'])
    second, _ = groups.admit(['view_2'])
    groups.complete(first, {'view_1': 'ok'})
    assert len(output) == 1 and output[0].group_id == 'group_1'
    groups.complete(second, {'view_2': 'nok'})
    assert output[1].group_id == 'group_2'


@pytest.mark.parametrize('change', ['mixed', 'branch', 'reuse', 'timeout'])
def test_invalid_group_rules_rejected(change):
    configured = views()
    if change == 'mixed': configured[1].output_mode = 'independent'
    if change == 'branch': configured[0].branch_enabled = True
    if change == 'reuse': configured[0].frame_source_view_id = configured[1].id
    if change == 'timeout': configured[1].and_timeout_ms = 50
    with pytest.raises(ValueError):
        validate_view_routing(configured, complete=True)


def test_stop_closes_incomplete_group_with_nok_and_rejects_new_work():
    output = []
    groups = InspectionGroups(views(), output.append, timers=False)
    first, _ = groups.admit(['view_1'])
    groups.complete(first, {'view_1': 'ok'})
    groups.close()
    assert len(output) == 1 and output[0].status == 'nok'
    assert groups.admit(['view_2'])[0] is None


def test_timeout_publication_blocks_reuse_and_close_waits_for_output():
    import threading
    entered, release = threading.Event(), threading.Event()
    now = [0]
    def publish(outcome):
        entered.set()
        assert release.wait(3)
    groups = InspectionGroups(views(), publish, clock=lambda: now[0], timers=False)
    ticket, _ = groups.admit(['view_1'])
    groups.complete(ticket, {'view_1': 'ok'})
    now[0] = 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        expired = pool.submit(groups.expire)
        assert entered.wait(1) and groups.processing
        assert groups.admit(['view_1'])[0] is None
        groups.close()
        waiter = pool.submit(groups.wait_publications)
        assert not waiter.done()
        release.set()
        expired.result(timeout=2); waiter.result(timeout=2)
    assert not groups.processing


def test_failed_publication_releases_all_pending_groups():
    configured = views()
    for i, view in enumerate(configured, 1):
        view.output_mode, view.output_group = 'independent', f'group_{i}'
    calls = []
    def failed(outcome):
        calls.append(outcome.group_id)
        raise OSError('PLC unavailable')
    groups = InspectionGroups(configured, failed, timers=False)
    ticket, _ = groups.admit(['view_1', 'view_2'])
    with pytest.raises(OSError): groups.complete(ticket, {'view_1':'ok', 'view_2':'ok'})
    assert calls == ['group_1', 'group_2']
    assert not groups.processing

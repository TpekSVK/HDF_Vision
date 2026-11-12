from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import numpy as np
import pytest

from app.models.schema import RecipeV2, RecipeView, ToolRoi
from app.services.camera_registry import CameraRegistry
from app.services.modbus_service import ModbusConfig
from app.services.tool_registry import ToolRegistry
from app.services.workcell_runtime import WorkcellRuntime


def workcell(tmp_path, monkeypatch, *, mode='AND'):
    import app.services.inspection_runtime as single
    import app.services.workcell_runtime as module
    tool = ToolRegistry.make_default_tool('mse')
    tool.roi = ToolRoi({'x': 0, 'y': 0, 'w': 20, 'h': 16})
    views = [RecipeView(id=f'view_{i}', camera_id=f'camera_{i}', tools=[tool.copy()],
        output_group='group_1' if mode == 'AND' else f'group_{i}', output_mode=mode,
        trigger_mode='external', external_trigger_mode='explicit', external_source='modbus',
        external_request_input=i, and_timeout_ms=1000) for i in (1, 2)]
    config = RecipeV2(views=views, logging_enabled=False)
    monkeypatch.setattr(single, 'load_recipe_config', lambda *a, **kw: config)
    monkeypatch.setattr(module, 'load_recipe_config', lambda *a, **kw: config)
    monkeypatch.setattr(single, 'snapshot_camera_state', lambda camera: {})
    cameras = {f'camera_{i}': SimpleNamespace(width=20, height=16, fps=60,
        gst_start_count=lambda: 0, is_pipeline_open=lambda: True, get_stream_mode=lambda: 0,
        begin_trigger_capture=lambda: None, end_trigger_capture=lambda: None,
        exit_trigger_session=lambda **kw: None, stop=lambda **kw: None) for i in (1, 2)}
    picos = {key: SimpleNamespace(require_pio=lambda: None, is_available=lambda: True,
        prepare_master=lambda camera: None, prepare_trigger=lambda camera: None, quiesce=lambda: None)
        for key in cameras}
    signals = []
    modbus = SimpleNamespace(get_config=ModbusConfig,
        signal_result=lambda status, **kw: signals.append((status, kw)),
        emit_heartbeat=lambda **kw: None, close=lambda: None)
    registry = CameraRegistry(cameras, picos)
    runtime = WorkcellRuntime(registry, SimpleNamespace(is_input_enabled=lambda value: True),
        modbus, tmp_path / 'results.db', data_root=tmp_path)
    image = np.arange(320, dtype=np.uint8).reshape(16, 20)
    for lane in runtime.runtimes.values():
        lane._load_view_golden_array = lambda *a: image.copy()
        lane._capture_frame_for_view = lambda **kw: image.copy()
    runtime.prepare('test', 'master', 'view_1')
    return runtime, signals, config


def test_one_recipe_runs_both_real_pipelines_concurrently_with_one_and_verdict(tmp_path, monkeypatch):
    runtime, signals, _ = workcell(tmp_path, monkeypatch)
    gate = threading.Barrier(2)
    for lane in runtime.runtimes.values():
        capture = lane._capture_frame_for_view
        def both(*, original=capture, **kw):
            gate.wait(timeout=2)
            return original(**kw)
        lane._capture_frame_for_view = both
    jobs, reason = runtime.admit('manual', {'active_view_id': 'view_1'})
    assert len(jobs) == 2, reason
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(runtime.execute, jobs))
    assert all('error' not in result for result in results), results
    assert signals == []
    assert results[0]['cycle_id'] == results[1]['cycle_id']
    assert {result['records'][0]['camera_id'] for result in results} == {'camera_1', 'camera_2'}
    outcomes = runtime.complete(jobs, results)
    assert len(outcomes) == 1 and signals == [('ok', {'group_id': 'group_1'})]
    runtime.quiesce()


def test_camera_preparation_and_stop_overlap_on_separate_threads(tmp_path, monkeypatch):
    runtime, _, _ = workcell(tmp_path, monkeypatch)
    runtime.quiesce()
    prepare_gate, stop_gate = threading.Barrier(2), threading.Barrier(2)
    threads = set()
    for lane in runtime.runtimes.values():
        original = lane.prepare
        def prepare(*args, original=original):
            threads.add(threading.get_ident())
            prepare_gate.wait(timeout=2)
            return original(*args)
        monkeypatch.setattr(lane, 'prepare', prepare)
    runtime.prepare('test', 'master', 'view_1')
    assert len(threads) == 2
    for lane in runtime.runtimes.values():
        original = lane.quiesce
        def stop(original=original):
            stop_gate.wait(timeout=2)
            return original()
        monkeypatch.setattr(lane, 'quiesce', stop)
    runtime.quiesce()
    assert all(controller.state.value == 'paused' for controller in runtime.controllers.values())


def test_setup_pause_keeps_camera_streams_and_diagnostic_close_preserves_them(tmp_path, monkeypatch):
    from app.services.camera_registry import CameraRouter, PicoRouter
    from app.services.camera_position import CameraPositionService
    runtime, _, _ = workcell(tmp_path, monkeypatch)
    for camera in runtime.registry.cameras.values():
        monkeypatch.setattr(camera, 'exit_trigger_session', lambda **kw: pytest.fail('normal pause closed stream'))
    runtime.capture_mode = 'trigger'
    for lane in runtime.runtimes.values():
        lane.capture_mode = 'trigger'
    runtime.quiesce()
    diagnostic = CameraPositionService(None, CameraRouter(runtime.registry), PicoRouter(runtime.registry), None)
    diagnostic._session_mode, diagnostic._session_key = 'trigger', ('trigger',)
    diagnostic.close_session()
    assert diagnostic._session_mode is None
    runtime.prepare('test', 'trigger', 'view_1')
    assert all(controller.state.value == 'ready' for controller in runtime.controllers.values())


def test_failed_preparation_waits_for_peer_before_global_cleanup(tmp_path, monkeypatch):
    runtime, _, _ = workcell(tmp_path, monkeypatch)
    runtime.quiesce()
    started, release, peer_stopped = threading.Event(), threading.Event(), threading.Event()
    def slow(*args):
        started.set()
        assert release.wait(3)
    def fail(*args):
        assert started.wait(2)
        raise RuntimeError('camera failed')
    monkeypatch.setattr(runtime.runtimes['camera_1'], 'prepare', fail)
    monkeypatch.setattr(runtime.runtimes['camera_2'], 'prepare', slow)
    monkeypatch.setattr(runtime.runtimes['camera_2'], 'quiesce', peer_stopped.set)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(runtime.prepare, 'test', 'master', 'view_1')
        try:
            assert started.wait(2)
            assert not peer_stopped.is_set()
        finally:
            release.set()
        with pytest.raises(RuntimeError, match='camera failed'):
            result.result(timeout=3)
    assert peer_stopped.is_set()
    assert all(controller.state.value == 'paused' for controller in runtime.controllers.values())


def test_asynchronous_external_and_rejects_completed_camera_but_accepts_missing_peer(tmp_path, monkeypatch):
    runtime, signals, _ = workcell(tmp_path, monkeypatch)
    first, reason = runtime.admit('modbus', {'input_index': 1})
    assert len(first) == 1, reason
    runtime.complete(first, [runtime.execute(first[0])])
    assert not signals
    assert runtime.admit('modbus', {'input_index': 1})[0] == []
    second, reason = runtime.admit('modbus', {'input_index': 2})
    assert len(second) == 1, reason
    assert second[0].options['cycle_id'] == first[0].options['cycle_id']
    runtime.complete(second, [runtime.execute(second[0])])
    assert signals == [('ok', {'group_id': 'group_1'})]
    runtime.quiesce()


def test_independent_camera_failure_does_not_stop_other_camera_or_write_its_output(tmp_path, monkeypatch):
    runtime, signals, _ = workcell(tmp_path, monkeypatch, mode='independent')
    def failed(**kw): raise RuntimeError('camera 1 disconnected')
    runtime.runtimes['camera_1']._capture_frame_for_view = failed
    one, _ = runtime.admit('modbus', {'input_index': 1})
    two, _ = runtime.admit('modbus', {'input_index': 2})
    runtime.complete(one, [runtime.execute(one[0])])
    runtime.complete(two, [runtime.execute(two[0])])
    assert signals == [('nok', {'group_id': 'group_1'}), ('ok', {'group_id': 'group_2'})]
    runtime.quiesce()


def test_missing_camera_blocks_preparation(tmp_path, monkeypatch):
    runtime, _, _ = workcell(tmp_path, monkeypatch)
    runtime.quiesce()
    runtime.registry.cameras.pop('camera_2')
    with pytest.raises(ValueError, match='nie je priradená'):
        runtime.prepare('test', 'master', 'view_1')


def test_stop_never_closes_pico_while_camera_is_processing(tmp_path, monkeypatch):
    runtime, _, _ = workcell(tmp_path, monkeypatch)
    jobs, _ = runtime.admit('manual', {'active_view_id': 'view_1'})
    with pytest.raises(RuntimeError, match='dokončujú'):
        runtime.quiesce()
    runtime.complete(jobs, [runtime.execute(job) for job in jobs])
    runtime.quiesce()


def test_same_input_for_independent_cameras_keeps_success_when_peer_fails(tmp_path, monkeypatch):
    runtime, signals, config = workcell(tmp_path, monkeypatch, mode='independent')
    config.views[1].external_request_input = 1
    runtime.quiesce(); runtime.prepare('test', 'master', 'view_1')
    runtime.runtimes['camera_1']._capture_frame_for_view = lambda **kw: None
    jobs, reason = runtime.admit('modbus', {'input_index': 1})
    assert len(jobs) == 2, reason
    results = [runtime.execute(job) for job in jobs]
    runtime.complete(jobs, results)
    assert ('nok', {'group_id': 'group_1'}) in signals
    assert ('ok', {'group_id': 'group_2'}) in signals
    runtime.quiesce()


def test_all_and_views_of_a_camera_run_in_one_accepted_request(tmp_path, monkeypatch):
    runtime, signals, config = workcell(tmp_path, monkeypatch)
    third = config.views[0].copy(); third.id = 'view_3'
    third.external_request_input, third.frame_source_view_id = 3, 'view_1'
    config.views.append(third)
    runtime.quiesce(); runtime.prepare('test', 'master', 'view_1')
    captures = []
    capture = runtime.runtimes['camera_1']._capture_frame_for_view
    runtime.runtimes['camera_1']._capture_frame_for_view = lambda **kw: captures.append(kw['view'].id) or capture(**kw)
    first, reason = runtime.admit('modbus', {'input_index': 1})
    assert first[0].options['spec']['view_ids'] == ['view_1', 'view_3'], reason
    result = runtime.execute(first[0])
    assert 'error' not in result, result
    runtime.complete(first, [result])
    second, _ = runtime.admit('modbus', {'input_index': 2})
    runtime.complete(second, [runtime.execute(second[0])])
    assert captures == ['view_1']
    assert signals == [('ok', {'group_id': 'group_1'})]
    runtime.quiesce()


def test_second_camera_branch_uses_final_target_verdict(tmp_path, monkeypatch):
    runtime, signals, config = workcell(tmp_path, monkeypatch, mode='independent')
    second = config.views[1]
    third = second.copy(); third.id = 'view_3'; third.tools = []
    third.external_request_input = 3
    second.branch_enabled, second.branch_targets = True, {'nok': 'view_3'}
    config.views.append(third)
    runtime.quiesce(); runtime.prepare('test', 'master', 'view_2')
    runtime.runtimes['camera_2']._capture_frame_for_view = lambda **kw: np.zeros((16,20), np.uint8)
    jobs, _ = runtime.admit('modbus', {'input_index': 2})
    result = runtime.execute(jobs[0])
    assert 'error' not in result, result
    assert [record['status'] for record in result['records']] == ['nok', 'ok']
    assert result['status'] == 'ok'
    runtime.complete(jobs, [result])
    assert signals == [('ok', {'group_id': 'group_2'})]
    runtime.quiesce()


def test_typed_capture_failure_recovers_only_affected_camera(tmp_path, monkeypatch):
    from app.services.capture_errors import IncompletePioPair
    from app.services.inspection_controller import InspectionState
    runtime, signals, _ = workcell(tmp_path, monkeypatch, mode='independent')
    runtime.capture_mode = 'trigger'
    def incomplete(**kw): raise IncompletePioPair(1,2)
    runtime.runtimes['camera_1']._capture_frame_for_view = incomplete
    jobs, _ = runtime.admit('modbus', {'input_index':1})
    runtime.complete(jobs, [runtime.execute(jobs[0])])
    assert runtime.processing and runtime.controllers['camera_1'].state == InspectionState.PREPARING
    assert runtime.controllers['camera_2'].state == InspectionState.READY
    observed=[]
    def recover(lane, request, error, budget, cancel, notify):
        observed.append((lane.camera_id, budget, cancel.is_set()))
        return dict(ok=True, attempts=1, error='')
    monkeypatch.setattr('app.services.trigger_recovery.recover_trigger', recover)
    runtime.recover('camera_1')
    assert observed == [('camera_1',2,False)]
    assert runtime.controllers['camera_1'].state == InspectionState.READY
    assert signals == [('nok',{'group_id':'group_1'})]
    runtime.quiesce()


def test_capture_error_is_filterable_without_image_and_board_fault_blocks_requests(tmp_path, monkeypatch):
    from app.services.results_history import query_history
    runtime, _, config = workcell(tmp_path, monkeypatch, mode='independent')
    config.logging_enabled=True
    def failed(**kw): raise RuntimeError('camera unplugged')
    runtime.runtimes['camera_1']._capture_frame_for_view = failed
    jobs, _ = runtime.admit('modbus', {'input_index':1})
    runtime.complete(jobs, [runtime.execute(jobs[0])])
    rows = query_history([dict(id='camera_1', camera_id='camera_1', name='A', db_path=runtime.db_path)],
                         dict(start=0,end=9999999999999), limit=10)
    assert len(rows) == 1 and not rows[0]['ok'] and rows[0]['group_status']=='nok'
    assert 'camera unplugged' in rows[0]['meta_json'] and not rows[0]['full_path']
    runtime.hardware_fault('Pico restarted')
    assert runtime.admit('modbus', {'input_index':2}) == ([], 'Pico restarted')
    runtime.quiesce()


def test_explicit_camera_does_not_suppress_peer_sequential_input(tmp_path, monkeypatch):
    runtime, _, config = workcell(tmp_path, monkeypatch, mode='independent')
    config.views[1].external_trigger_mode='sequential'
    jobs, reason = runtime.admit('modbus', {'input_index':1})
    assert {job.camera_id for job in jobs}=={'camera_1','camera_2'},reason
    runtime.complete(jobs,[runtime.execute(job) for job in jobs])
    assert runtime.admit('modbus', {'input_index':0})[0]==[]
    runtime.quiesce()

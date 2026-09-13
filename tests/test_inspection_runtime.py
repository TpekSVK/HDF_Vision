from types import SimpleNamespace
import threading
from pathlib import Path
import numpy as np
import pytest

from app.models.schema import RecipeV2, RecipeView, ToolRoi
from app.services.inspection_controller import InspectionController
from app.services.inspection_runtime import InspectionRuntime
from app.services.tool_registry import ToolRegistry


def runtime_fixture(tmp_path, monkeypatch, *, source=False):
    import app.services.inspection_runtime as module
    tool = ToolRegistry.make_default_tool('mse')
    tool.roi = ToolRoi({'x': 0, 'y': 0, 'w': 20, 'h': 16})
    views = [RecipeView(id='one', tools=[tool], trigger_mode='external', external_source='modbus')]
    if source:
        views.append(RecipeView(id='two', tools=[tool.copy()], trigger_mode='external', external_source='modbus', frame_source_view_id='one'))
    config = RecipeV2(views=views, logging_enabled=False)
    monkeypatch.setattr(module, 'load_recipe_config', lambda _, **kwargs: config)
    monkeypatch.setattr(module, 'snapshot_camera_state', lambda _: {})
    image = np.arange(16 * 20, dtype=np.uint8).reshape(16, 20)
    outputs, captures = [], []
    camera = SimpleNamespace(width=20, height=16, fps=60, gst_start_count=lambda: 0,
        begin_trigger_capture=lambda: None, end_trigger_capture=lambda: None)
    pico = SimpleNamespace(quiesce=lambda: outputs.append('idle'))
    modbus = SimpleNamespace(signal_result=outputs.append, emit_heartbeat=lambda: None)
    runtime = InspectionRuntime(camera, pico, SimpleNamespace(is_input_enabled=lambda _: True), modbus, tmp_path / 'test.db')
    runtime._load_view_golden_array = lambda *args: image
    runtime._capture_frame_for_view = lambda **kw: captures.append(kw['view'].id) or image.copy()
    controller = InspectionController()
    controller.prepare(lambda: None, lambda: None)
    options = dict(recipe_name='test', capture_mode='master', active_view_id='one', input_index=1)
    return runtime, controller, options, captures, outputs, config


def run(runtime, controller, options):
    request, reason = controller.admit('modbus')
    assert reason is None
    result = runtime.run(controller, request, options)
    controller.finish(request)
    return result


def test_real_pipeline_without_main_window(tmp_path, monkeypatch):
    runtime, controller, options, captures, outputs, _ = runtime_fixture(tmp_path, monkeypatch)
    result = run(runtime, controller, options)
    assert captures == ['one']
    assert outputs == ['ok']
    assert result['status'] == 'ok'
    assert result['records'][0]['reports'][0]['metrics']['mse'] == 0
    assert result['frame_ids']['one'].startswith(result['request_id'])


def test_stepped_sequence_reuses_only_its_own_capture_and_cycle_id(tmp_path, monkeypatch):
    runtime, controller, options, captures, outputs, _ = runtime_fixture(tmp_path, monkeypatch, source=True)
    first = run(runtime, controller, options)
    second = run(runtime, controller, options)
    third = run(runtime, controller, options)
    assert captures == ['one', 'one']
    assert first['request_id'] != second['request_id']
    assert first['cycle_id'] == second['cycle_id'] != third['cycle_id']
    assert second['frame_ids']['two'] == first['frame_ids']['one']


def test_capture_failure_never_outputs_ok(tmp_path, monkeypatch):
    runtime, controller, options, _, outputs, _ = runtime_fixture(tmp_path, monkeypatch)
    runtime._capture_frame_for_view = lambda **kw: None
    request, _ = controller.admit('modbus')
    with pytest.raises(RuntimeError, match='nevrátila'):
        runtime.run(controller, request, options)
    assert outputs == ['nok', 'idle']
    assert runtime._sequence_contexts == {}


def test_storage_failure_is_a_production_fault(tmp_path, monkeypatch):
    import app.services.inspection_runtime as module
    runtime, controller, options, _, outputs, config = runtime_fixture(tmp_path, monkeypatch)
    config.logging_enabled = True
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(module, 'save_production_result', fail)
    request, _ = controller.admit('modbus')
    with pytest.raises(OSError, match='disk full'):
        runtime.run(controller, request, options)
    assert outputs == ['nok', 'idle']
    assert runtime.db is None


def test_sqlite_connection_and_pipeline_belong_to_worker(tmp_path, monkeypatch):
    import app.services.inspection_runtime as module
    from concurrent.futures import ThreadPoolExecutor
    from app.services.db_service import DbService
    runtime, controller, options, _, outputs, config = runtime_fixture(tmp_path, monkeypatch)
    config.logging_enabled = True
    observed = []
    main_thread = threading.get_ident()
    class OwnedDatabase(DbService):
        def __init__(self, path):
            observed.append(('open', threading.get_ident()))
            super().__init__(path)
        def insert_result(self, **kw):
            observed.append(('write', threading.get_ident()))
            return super().insert_result(**kw)
        def close(self):
            observed.append(('close', threading.get_ident()))
            super().close()
    monkeypatch.setattr(module, 'DbService', OwnedDatabase)
    monkeypatch.setattr(module, 'save_production_result', lambda frame, meta, recipe, **kw:
        dict(thumb='', full=None, meta_payload=meta, ts_ms=12345, view_id=kw['view_id'], run_id=kw['run_id']))
    request, _ = controller.admit('modbus')
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(runtime.run, controller, request, options).result(timeout=5)
    controller.finish(request)
    assert [kind for kind, _ in observed] == ['open', 'write', 'close']
    assert len({ident for _, ident in observed}) == 1
    assert observed[0][1] != main_thread
    import sqlite3, json
    with sqlite3.connect(tmp_path / 'test.db') as connection:
        metadata = json.loads(connection.execute('SELECT meta_json FROM results').fetchone()[0])
    assert metadata['request_id'] == request.id
    assert metadata['cycle_id'] == result['cycle_id']
    assert metadata['frame_id'] == result['frame_ids']['one']


def test_switching_recipe_starts_new_source_sequence(tmp_path, monkeypatch):
    runtime, controller, options, captures, outputs, _ = runtime_fixture(tmp_path, monkeypatch, source=True)
    first = run(runtime, controller, options)
    changed = run(runtime, controller, dict(options, recipe_name='different'))
    assert captures == ['one', 'one']
    assert changed['cycle_id'] != first['cycle_id']


def test_runtime_preparation_uses_existing_pio_profile_path(tmp_path, monkeypatch):
    import app.services.inspection_runtime as module
    runtime, controller, options, captures, outputs, config = runtime_fixture(tmp_path, monkeypatch)
    order = []
    runtime.pico.is_available = lambda: True
    runtime.pico.prepare_trigger = lambda camera: order.append('pio_trigger')
    runtime.pico.prepare_master = lambda camera: order.append('pio_master')
    runtime.cam.is_pipeline_open = lambda: True
    monkeypatch.setattr(module, 'apply_view_camera_profile', lambda *args: order.append('profile'))
    runtime.prepare('test', 'trigger', 'one')
    assert order == ['profile', 'pio_trigger']
    order.clear()
    runtime.prepare('test', 'master', 'one')
    assert order == ['profile', 'pio_master']

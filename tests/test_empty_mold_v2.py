"""Independent V2 region, model, storage, activation and legacy regression tests."""
import json
import numpy as np
import pytest
from app.models.schema import ToolRoi, ToolMask, RecipeV2
from app.services.tool_registry import ToolRegistry
from app.services.empty_mold_v2.regions import zones_for, annotation_masks, OUTSIDE
from app.services.empty_mold_v2.model import build
from app.services.empty_mold_v2.evaluator import evaluate, DEFAULTS, sensitivity_threshold
from app.services.empty_mold_v2.workflow import Workflow, bind_store
from app.services.empty_mold_v2.feedback import add_feedback
from app.services.empty_mold_v2.storage import load_model
from app.services.learning_context import learning_signature
from app.services.tool_pipeline import run_pipeline


def scene():
    tool = ToolRegistry.make_default_tool('mold.protection_v2')
    tool.roi = ToolRoi({'x': 2, 'y': 2, 'w': 56, 'h': 44})
    tool.params.values['cavities'] = [dict(name='K2', roi=dict(shape='polygon', points=[[10, 10], [29, 10], [29, 35], [10, 35]]))]
    tool.params.values['min_blob_area'] = 12
    golden = np.full((48, 64), 100, 'uint8')
    return tool, golden


def test_masks_roi_cavities_ignore_outside():
    tool, frame = scene()
    ignore = np.zeros_like(frame)
    ignore[15:20, 20:25] = 255
    tool.ignore_mask = ToolMask(ignore)
    zones = zones_for(tool, frame.shape)
    assert zones['K2'][10, 10]
    assert not zones['K2'][16, 21]
    assert not zones[OUTSIDE][10, 10]
    assert zones[OUTSIDE][5, 5]
    assert not np.logical_or.reduce(list(zones.values()))[0, 0]
    assert not (zones['K2'] & zones[OUTSIDE]).any()
    assert sum(mask.sum() for mask in zones.values()) == 56*44-25


def test_polygon_and_ellipse_roi():
    tool, frame = scene()
    tool.params.values['cavities'] = []
    tool.roi = ToolRoi({'shape': 'polygon', 'points': [[5, 5], [40, 5], [5, 40]]})
    assert zones_for(tool, frame.shape)[OUTSIDE][8, 8]
    assert not zones_for(tool, frame.shape)[OUTSIDE][35, 35]
    tool.roi = ToolRoi({'shape': 'ellipse', 'x': 5, 'y': 5, 'w': 30, 'h': 30})
    assert not zones_for(tool, frame.shape)[OUTSIDE][5, 5]
    assert zones_for(tool, frame.shape)[OUTSIDE][20, 20]


def test_variability_stable_variable_and_anomaly():
    tool, frame = scene()
    zones = zones_for(tool, frame.shape)
    frames = []
    for delta in [-20, -10, 0, 10, 20]:
        sample = frame.copy()
        sample[15:25, 15:25] = 100 + delta
        frames.append(sample)
    model = build(frames, zones, variability_floor=3, variability_cap=20)
    assert model.variability.dtype == np.float32
    assert model.variability[5, 5] == 3
    assert model.variability[20, 20] > 10
    sample = frame.copy()
    sample[15:25, 15:25] += 20
    assert evaluate(sample, model, zones, DEFAULTS)['status'] == 'ok'
    sample[30:35, 40:45] += 30
    assert evaluate(sample, model, zones, DEFAULTS)['status'] == 'nok'


def test_blob_across_cavity_outside_is_one():
    tool, frame = scene()
    zones = zones_for(tool, frame.shape)
    model = build([frame, frame], zones)
    defect = frame.copy()
    defect[20:25, 26:36] = 200
    result = evaluate(defect, model, zones, {**DEFAULTS, 'min_blob_area': 40})
    assert result['blob_count'] == 1
    assert result['blobs'][0]['area'] == 50
    assert set(result['blobs'][0]['zones']) == {'K2', OUTSIDE}
    assert result['status'] == 'nok'


@pytest.mark.parametrize('polarity,value,expected', [('bright', 200, 'nok'), ('bright', 0, 'ok'), ('dark', 0, 'nok'), ('dark', 200, 'ok'), ('both', 0, 'nok')])
def test_polarity(polarity, value, expected):
    tool, frame = scene()
    zones = zones_for(tool, frame.shape)
    model = build([frame, frame], zones)
    defect = frame.copy()
    defect[20:25, 25:35] = value
    assert evaluate(defect, model, zones, {**DEFAULTS, 'polarity': polarity})['status'] == expected


def fixture_workflow(tmp_path):
    tool, golden = scene()
    recipe = RecipeV2(tools=[tool], on_locator_failure='fail')
    tool = recipe.tools[0]
    signature = learning_signature(golden, recipe.views[0], tool, recipe.tools)
    store = bind_store(tool, tmp_path / 'HDF_Vision.db', tmp_path / 'recipes', 'test', recipe.views[0].id)
    flow = Workflow(tool, store, golden, signature, lambda: True)
    for value in (99, 100, 101):
        flow.add_sample(np.full_like(golden, value), 'training_ok')
    ok = golden.copy()
    ok[3, 3] += 1
    flow.add_sample(ok, 'validation_ok')
    nok = golden.copy()
    nok[20:25, 26:36] = 200
    polygon = [[[26, 20], [35, 20], [35, 24], [26, 24]]]
    flow.add_sample(nok, 'validation_nok', polygon)
    return tool, golden, recipe, store, flow, nok, polygon


def test_candidate_model_requires_explicit_activation(tmp_path):
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    before = run_pipeline(golden, golden, recipe)
    assert before.per_tool[0].status == 'nok'
    model = flow.build()
    assert 'v2_active_model' not in tool.params.values
    assert run_pipeline(golden, golden, recipe).per_tool[0].status == 'nok'
    with pytest.raises(ValueError, match='otestujte'):
        flow.activate()
    result = flow.test()
    assert result['false_ok'] == result['false_nok'] == 0
    flow.activate()
    assert run_pipeline(golden, golden, recipe).per_tool[0].status == 'ok'
    assert run_pipeline(golden, nok, recipe).per_tool[0].status == 'nok'
    active = tool.params.values['v2_active_model']
    flow.build()
    assert tool.params.values['v2_active_model'] == active
    tool.params.values['sensitivity'] = 1
    assert run_pipeline(golden, nok, recipe).per_tool[0].status == 'nok'
    assert len(store.models()) == 3


@pytest.mark.parametrize('label,state,status', [('false_nok','candidate_ok','nok'), ('false_ok','candidate_nok','ok')])
def test_feedback_never_changes_active_model(tmp_path, label, state, status):
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    flow.build(); flow.test(); flow.activate()
    active = tool.params.values['v2_active_model']
    report = run_pipeline(golden, nok if status == 'nok' else golden, recipe).per_tool[0]
    frame = (nok if status == 'nok' else golden).copy()
    frame[0, 0] = 3  # independent production frame
    ident = add_feedback(store.db_path, {'id': 17, 'recipe_id': 1},
                         {'empty_mold_v2_alignment': {'valid': True, 'T_total': None}},
                         {'id': 'tool', 'type': tool.type, 'status': status, 'metrics': report.metrics}, label, frame=frame)
    sample = next(s for s in store.samples() if s['id'] == ident)
    assert sample['state'] == state
    assert tool.params.values['v2_active_model'] == active
    assert len(store.models()) == 2
    with pytest.raises(ValueError, match='polyg'):
        flow.review(ident, 'training_nok')
    flow.review(ident, 'training_ok')
    assert tool.params.values['v2_active_model'] == active


def test_training_excludes_validation_nok_and_candidates(tmp_path):
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    candidate = golden.copy(); candidate[10:40, 10:40] = 250
    store.add(candidate, 'candidate_ok', flow.metadata())
    model = flow.build()
    assert model.metadata['sample_count'] == 3
    assert model.center[20, 20] == 100
    assert len(model.metadata['training_ids']) == 3


def test_normalization_consistent_and_ignores_excluded_pixels():
    tool, frame = scene()
    zones = zones_for(tool, frame.shape)
    model = build([frame, frame + 10], zones, normalize=True)
    shifted = frame + 20
    shifted[0, :] = 255
    assert evaluate(shifted, model, zones, DEFAULTS)['status'] == 'ok'
    shifted[20:25, 26:36] = 200
    assert evaluate(shifted, model, zones, DEFAULTS)['status'] == 'nok'


def test_annotation_each_polygon_must_be_hit(tmp_path):
    from app.services.empty_mold_v2.validation import validate
    tool, frame = scene()
    zones = zones_for(tool, frame.shape)
    model = build([frame, frame], zones)
    defect = frame.copy(); defect[20:25, 26:36] = 200
    sample = dict(id='one', state='validation_nok', annotations=[[[26,20],[35,20],[35,24],[26,24]], [[40,30],[45,30],[45,35],[40,35]]])
    report = validate([sample], lambda _: defect, model, zones, DEFAULTS)
    assert report['false_ok'] == 1
    assert report['missed_polygons'] == 1


def test_permissions_and_duplicate_split(tmp_path):
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    with pytest.raises(ValueError, match='Rovnaká'):
        flow.add_sample(golden, 'validation_ok')
    flow.authorize = lambda: False
    with pytest.raises(PermissionError): flow.build()
    with pytest.raises(PermissionError): flow.add_sample(golden, 'training_ok')
    with pytest.raises(PermissionError): flow.activate()


def test_legacy_roundtrip_and_registry_separate():
    legacy = ToolRegistry.make_default_tool('mold.protection_v1')
    original = RecipeV2(tools=[legacy]).to_dict()
    loaded = RecipeV2.from_dict(original)
    assert loaded.to_dict() == original
    assert loaded.tools[0].type == 'mold.protection_v1'
    assert type(ToolRegistry.create_tool('mold.protection_v1')) is not type(ToolRegistry.create_tool('mold.protection_v2'))


def test_sensitivity_monotonic():
    assert sensitivity_threshold(1) == 8
    assert sensitivity_threshold(100) == 1.5
    assert sensitivity_threshold(80) < sensitivity_threshold(60)


def test_recommendation_is_read_only_and_validation_is_held_out(tmp_path):
    from copy import deepcopy
    tool, golden, recipe, store, flow, nok, polygon = fixture_workflow(tmp_path)
    training_nok = nok.copy(); training_nok[0, 0] = 2
    flow.add_sample(training_nok, 'training_nok', polygon)
    flow.build()
    before = deepcopy(tool.to_dict())
    recommendation = flow.suggest()
    assert tool.to_dict() == before
    assert all(row['expected'].startswith('training_') for row in recommendation['summary']['samples'])
    flow.apply_recommended()
    assert 'v2_active_model' not in tool.params.values
    flow.undo()
    assert tool.params.values['sensitivity'] == before['params']['sensitivity']
    assert all(row['expected'].startswith('validation_') for row in flow.test()['samples'])


def test_geometry_change_and_locator_failure_fail_closed(tmp_path):
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    flow.build(); flow.test(); flow.activate()
    tool.params.values['cavities'][0]['roi']['points'][0][0] += 1
    report = run_pipeline(golden, golden, recipe).per_tool[0]
    assert report.status == 'nok' and report.metrics['inspection_fault']


def test_dataset_export_contains_masks_and_lossless_frames(tmp_path):
    import zipfile
    from app.services.empty_mold_v2.export import export_dataset
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    destination = tmp_path / 'dataset.zip'
    export_dataset(store, destination)
    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert len(manifest['samples']) == 5
        assert any(name.endswith('masks.npz') for name in archive.namelist())
        assert all(s['frame_path'] in archive.namelist() for s in manifest['samples'])
    np.testing.assert_array_equal(store.frame(store.samples()[1]), golden)


def test_ignore_morphology_and_per_zone_threshold():
    tool, frame = scene()
    ignored = np.zeros_like(frame); ignored[20:25,26:36] = 255
    tool.ignore_mask = ToolMask(ignored)
    zones = zones_for(tool, frame.shape)
    model = build([frame, frame], zones)
    defect = frame.copy(); defect[20:25,26:36] = 255
    assert evaluate(defect, model, zones, {**DEFAULTS,'dilate': 2})['status'] == 'ok'
    defect[10:15,10:15] = 130
    result = evaluate(defect, model, zones, {**DEFAULTS,'zone_thresholds': {'K2': 20}})
    assert result['status'] == 'ok'
    result = evaluate(defect, model, zones, {**DEFAULTS,'dilate': 3, 'morph_close': 2})
    assert not result['binary'][ignored > 0].any()


def test_database_migration_preserves_existing_results(tmp_path):
    import sqlite3
    from app.services.db_service import DbService
    path = tmp_path / 'HDF_Vision.db'
    db = DbService(path)
    ident = db.ensure_recipe('legacy')
    db.conn().execute('INSERT INTO results(ts_ms,recipe_id,ok) VALUES(?,?,?)', (123, ident, 1))
    db.conn().commit(); db.close()
    db = DbService(path)
    assert db.conn().execute('SELECT ok FROM results').fetchone()[0] == 1
    assert db.conn().execute("SELECT name FROM sqlite_master WHERE name='empty_mold_v2_models'").fetchone()
    db.close()


@pytest.mark.parametrize('apply_alignment', [True, False])
def test_existing_locator_alignment_matches_learning_and_run(tmp_path, apply_alignment):
    from app.utils import imaging
    from app.services.empty_mold_v2.alignment import capture_aligned
    rng = np.random.default_rng(15)
    golden = rng.integers(30, 200, (64, 80), dtype=np.uint8)
    locator = ToolRegistry.make_default_tool('locator.template_match')
    locator.roi = ToolRoi({'x': 5,'y': 5,'w': 65,'h': 50})
    locator.params.values.update(template_roi={'x': 20,'y': 20,'w': 16,'h': 16}, apply_alignment=apply_alignment)
    locator.order = 0
    target = ToolRegistry.make_default_tool('mold.protection_v2')
    target.order = 1
    target.roi = ToolRoi({'x': 20,'y': 20,'w': 30,'h': 25})
    recipe = RecipeV2(tools=[locator,target], on_locator_failure='fail')
    target = recipe.tools[1]
    shifted = imaging.warp_by_translation_u8(golden, 3, -2)
    aligned = capture_aligned(golden, shifted, recipe.tools)
    np.testing.assert_array_equal(aligned[20:45,20:50], golden[20:45,20:50])
    signature = learning_signature(golden, recipe.views[0], target, recipe.tools)
    store = bind_store(target, tmp_path / 'HDF_Vision.db', tmp_path / 'recipes', 'test', recipe.views[0].id)
    flow = Workflow(target, store, golden, signature, lambda: True)
    for value in (1,2):
        sample = aligned.copy(); sample[0,0] = value
        flow.add_sample(sample, 'training_ok')
    sample = aligned.copy(); sample[0,0] = 3
    flow.add_sample(sample, 'validation_ok')
    defect = aligned.copy(); defect[35:40,30:40] = 255
    flow.add_sample(defect, 'validation_nok', [[[30,35],[39,35],[39,39],[30,39]]])
    flow.build(); flow.test(); flow.activate()
    result = run_pipeline(golden, shifted, recipe)
    assert result.per_tool[1].status == 'ok'
    locator_in_recipe = recipe.tools[0]
    locator_in_recipe.params.values['reference_min_coverage'] = .75
    assert run_pipeline(golden, shifted, recipe).per_tool[1].status == 'nok'


def test_repeated_feedback_reuses_frame_but_keeps_audit(tmp_path):
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    first = store.add(golden, 'candidate_ok', flow.metadata())
    second = store.add(golden, 'candidate_nok', flow.metadata())
    samples = {s['id']: s for s in store.samples()}
    assert samples[first]['frame_path'] == samples[second]['frame_path']
    assert first != second
    with pytest.raises(ValueError, match='potvrdená'):
        flow.review(first, 'training_ok')


def test_changed_annotation_invalidates_validation(tmp_path):
    tool, golden, recipe, store, flow, nok, polygon = fixture_workflow(tmp_path)
    flow.build(); flow.test()
    sample = store.samples({'validation_nok'})[0]
    store.review(sample['id'], 'validation_nok', polygon, {'annotation_review': 2})
    with pytest.raises(ValueError, match='dataset sa zmenil'):
        flow.activate()


def test_no_full_history_frame_no_candidate(tmp_path):
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    flow.build(); flow.test(); flow.activate()
    report = run_pipeline(golden, golden, recipe).per_tool[0]
    before = len(store.samples())
    with pytest.raises(ValueError, match='Plná produkčná'):
        add_feedback(store.db_path, {}, {}, {'type':tool.type, 'status':'ok', 'metrics':report.metrics}, 'false_ok')
    assert len(store.samples()) == before


def test_v2_recipe_serialization_retains_active_version_and_geometry(tmp_path):
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    flow.build(); flow.test(); flow.activate()
    payload = json.loads(json.dumps(recipe.with_tools([tool]).to_dict()))
    loaded = RecipeV2.from_dict(payload)
    assert loaded.tools[0].params.values['cavities'] == tool.params.values['cavities']
    assert loaded.tools[0].params.values['v2_active_model'] == tool.params.values['v2_active_model']
    assert run_pipeline(golden, nok, loaded).per_tool[0].status == 'nok'


def test_recipe_service_publish_keeps_active_version_until_explicit_update(tmp_path):
    from app.services.recipe_service import RecipeService
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    flow.build(); flow.test(); flow.activate()
    service = RecipeService(tmp_path)
    service.create('published')
    service.save_tools('published', [tool])
    service.publish_recipe('published')
    active = service.get_published_tools('published')[0].params.values['v2_active_model']
    flow.build()
    service.save_tools('published', [tool])
    assert service.get_published_tools('published')[0].params.values['v2_active_model'] == active
    flow.test(); flow.activate()
    service.save_tools('published', [tool])
    assert service.get_published_tools('published')[0].params.values['v2_active_model'] == active
    service.publish_recipe('published')
    assert service.get_published_tools('published')[0].params.values['v2_active_model'] != active
    service.db.close()


def test_production_v2_full_image_lossless_legacy_policy_unchanged(tmp_path, monkeypatch):
    from app.services import storage_service
    calls = []
    monkeypatch.setattr(storage_service.iio, 'imwrite', lambda *args, **kwargs: calls.append(kwargs))
    frame = np.zeros((10,10), 'uint8')
    for meta in ({}, {'empty_mold_v2_alignment': {'valid':True}}):
        storage_service._do_save_production(frame, tmp_path/'thumb.jpg', tmp_path/'full.webp', tmp_path/'meta.json', meta, True)
    assert 'lossless' not in calls[1]
    assert calls[3]['lossless'] is True


def test_v2_overlays_map_back_to_original_coordinates():
    from app.services.empty_mold_v2.overlays import items
    tool, _ = scene()
    metrics = {'blobs': [{'x':20,'y':20,'width':10,'height':5,'zones':['K2',OUTSIDE]}]}
    aligned = items(tool, metrics)
    original = items(tool, metrics, affine=np.array([[1,0,4],[0,1,-3]], 'float32'))
    a = [i for i in aligned if i.z_index >= 30][0]
    b = [i for i in original if i.z_index >= 30][0]
    assert a.rect[:2] == (20,20)
    # The shared renderer represents transformed rectangles as polygons.
    assert b.points is not None
    np.testing.assert_allclose(b.points[0], [24,17])


def test_inspection_runtime_v2_keeps_feedback_without_logging(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.services.inspection_controller import InspectionController
    from app.services.inspection_runtime import InspectionRuntime
    import app.services.inspection_runtime as runtime_module
    tool, golden, recipe, store, flow, nok, _ = fixture_workflow(tmp_path)
    flow.build(); flow.test(); flow.activate()
    config = recipe.with_tools([tool])
    config.logging_enabled = False
    monkeypatch.setattr(runtime_module, 'load_recipe_config', lambda *args, **kwargs: config)
    monkeypatch.setattr(runtime_module, 'snapshot_camera_state', lambda _: {})
    camera = SimpleNamespace(width=64, height=48, fps=60, gst_start_count=lambda:0,
                             begin_trigger_capture=lambda:None, end_trigger_capture=lambda:None)
    pico = SimpleNamespace(quiesce=lambda:None)
    modbus = SimpleNamespace(signal_result=lambda _:None, emit_heartbeat=lambda:None)
    runtime = InspectionRuntime(camera, pico, SimpleNamespace(is_input_enabled=lambda _:True), modbus, tmp_path/'HDF_Vision.db')
    runtime._load_view_golden_array = lambda *args:golden
    runtime._capture_frame_for_view = lambda **kwargs:golden.copy()
    controller = InspectionController()
    controller.prepare(lambda:None, lambda:None)
    request, _ = controller.admit('manual')
    result = runtime.run(controller, request, dict(recipe_name='test', capture_mode='master', active_view_id='view_1'))
    controller.finish(request)
    payload = result['records'][0]['v2_feedback']
    np.testing.assert_array_equal(payload['frame'], golden)
    assert payload['metadata']['empty_mold_v2_alignment']['valid']
    assert payload['metadata']['recipe_version']
    assert payload['metadata']['ts_ms'] > 0
    assert payload['metadata']['per_tool'][0]['status'] == 'ok'
    ident = add_feedback(store.db_path, {}, payload['metadata'], payload['metadata']['per_tool'][0],
                         'false_ok', frame=payload['frame'])
    assert next(s for s in store.samples() if s['id']==ident)['state'] == 'candidate_nok'

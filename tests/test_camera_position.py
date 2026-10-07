from types import SimpleNamespace
import cv2
import numpy as np
import pytest
import imageio.v3 as iio

from app.models.schema import RecipeV2, RecipeView, Tool, ToolRoi, ToolParams, ToolThresholds
from app.services.camera_position import (CameraPositionService, PositionLimits,
    evaluate_alignment, alignment_limits, camera_correction_from_alignment)
from app.services.storage_service import save_recipe_config


@pytest.mark.parametrize('axis', range(3))
@pytest.mark.parametrize('sign', [-1, 1])
@pytest.mark.parametrize('outside', [False, True])
def test_each_saved_axis_inclusive_tolerance(axis, sign, outside):
    values = [0., 0., 0.]
    limits = (12., 10., .4)
    values[axis] = sign * (limits[axis] + (0.01 if outside else 0))
    result = evaluate_alignment(*values, .99, PositionLimits(*limits))
    checks = (result.x_within_tolerance, result.y_within_tolerance, result.rotation_within_tolerance)
    assert checks[axis] is not outside
    assert result.overall_ok is not outside
    assert bool(result.corrections) is outside


@pytest.mark.parametrize('camera_x,camera_y,camera_roll,expected', [
    (35, 0, 0, 'DOĽAVA'), (-35, 0, 0, 'DOPRAVA'),
    (0, -35, 0, 'DOLE'), (0, 35, 0, 'HORE'),
    (0, 0, 2, 'PROTI SMERU'), (0, 0, -2, 'V SMERE')])
def test_physical_camera_motion_recommends_opposite_correction(camera_x, camera_y, camera_roll, expected):
    # Fixed target moves opposite to physical camera translation/roll.
    result = evaluate_alignment(-camera_x, -camera_y, -camera_roll, .99, PositionLimits(12, 10, .4))
    assert len(result.corrections) == 1
    assert expected in result.corrections[0]


def test_only_outside_axes_and_rotated_view_axes_are_corrected():
    result = evaluate_alignment(35, 4, -.8, .99, PositionLimits(12, 10, .4))
    assert len(result.corrections) == 2
    assert 'DOPRAVA' in result.corrections[0]
    assert 'PROTI SMERU' in result.corrections[1]
    assert 'HORE' in camera_correction_from_alignment(35, 0, 0, (True, False, False), 90)[0]


@pytest.mark.parametrize('values', [(20, 0, 0), (float('nan'), 0, 0), (0, float('inf'), 0)])
def test_invalid_alignment_has_no_movement_advice(values):
    result = evaluate_alignment(*values, .1, PositionLimits(12, 10, .4), reliable=False)
    assert result.overall_ok is None
    assert result.invalid_reason
    assert not result.corrections
    assert result.dx_px is None


def locator(mode='translation'):
    params = {'alignment_mode': mode, 'use_golden_crop': False,
              'template_roi': {'x': 30, 'y': 25, 'w': 20, 'h': 20},
              'angle_range_deg': 10, 'angle_step_deg': 1}
    return Tool(type='locator.template_match', name='Position',
        roi=ToolRoi({'x': 25, 'y': 20, 'w': 30, 'h': 30}),
        params=ToolParams(params), thresholds=ToolThresholds(
            {'max_shift_x': 12, 'max_shift_y': 10, 'threshold_corr': .75}))


def reference(tmp_path, mode='translation', rotation=0):
    tool = locator(mode)
    recipe = RecipeV2(views=[RecipeView(id='view_1', name='Front', tools=[tool], image_rotation=rotation)])
    save_recipe_config('test', recipe, base_dir=tmp_path)
    golden = np.zeros((100, 120), np.uint8)
    golden[25:45, 30:50] = np.random.default_rng(42).integers(10, 250, (20, 20), dtype=np.uint8)
    iio.imwrite(tmp_path / 'recipes/test/golden.png', golden)
    service = CameraPositionService(SimpleNamespace(base=tmp_path), None, None, None)
    return service, service.load_reference('test'), golden


@pytest.mark.parametrize('mode', ['translation', 'template_rotation', 'guided_edge'])
def test_limits_never_use_search_range_or_invent_defaults(mode):
    tool = locator(mode)
    limits = alignment_limits(tool)
    assert (limits.x, limits.y, limits.rotation) == (12, 10, None)
    tool.params.values['reference_max_angle_deg'] = .4
    assert alignment_limits(tool).rotation == (.4 if mode == 'guided_edge' else None)
    tool.thresholds.values.clear()
    assert alignment_limits(tool).x is None
    assert evaluate_alignment(0, 0, None, .99, PositionLimits(12, 10, None)).overall_ok is None


@pytest.mark.parametrize('dx,dy', [(35, 0), (-20, 0), (0, 25), (0, -20)])
def test_shared_locator_measures_uncompensated_shift_beyond_production_search(tmp_path, dx, dy):
    service, ref, golden = reference(tmp_path)
    before = ref.recipe.to_dict()
    path = tmp_path / 'recipes/test/recipe.json'
    disk_before = path.read_bytes()
    current = cv2.warpAffine(golden, np.float32([[1, 0, dx], [0, 1, dy]]), (120, 100))
    result = service.compare(ref, current)
    assert result.invalid_reason is None
    assert result.dx_px == pytest.approx(dx)
    assert result.dy_px == pytest.approx(dy)
    assert result.overall_ok is False
    expected = ('DOPRAVA' if dx > 0 else 'DOĽAVA') if dx else ('DOLE' if dy > 0 else 'HORE')
    assert expected in result.corrections[0]
    assert ref.recipe.to_dict() == before
    assert path.read_bytes() == disk_before
    np.testing.assert_array_equal(ref.golden, golden)


@pytest.mark.parametrize('angle', [-4, 4])
def test_existing_rotation_locator_sign_is_golden_to_current_clockwise(tmp_path, angle):
    service, ref, golden = reference(tmp_path, 'template_rotation')
    # Positive OpenCV angle is image CCW; existing locator reports image CW.
    matrix = cv2.getRotationMatrix2D((40, 35), angle, 1)
    current = cv2.warpAffine(golden, matrix, (120, 100))
    result = service.compare(ref, current)
    assert result.invalid_reason is None
    assert result.rotation_deg == pytest.approx(-angle, abs=1)
    assert result.rotation_within_tolerance is None  # No saved rotation acceptance limit.


def test_low_confidence_and_dimension_mismatch_never_generate_advice(tmp_path):
    service, ref, _ = reference(tmp_path)
    for image in (np.zeros((100, 120), np.uint8), np.zeros((20, 30), np.uint8)):
        result = service.compare(ref, image)
        assert result.invalid_reason
        assert not result.corrections


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_stored_view_orientation_is_undone_for_raw_golden(tmp_path, rotation):
    service, ref, golden = reference(tmp_path, rotation=rotation)
    raw = np.rot90(golden, k=rotation // 90).copy()
    np.testing.assert_array_equal(raw, ref.raw_golden)
    result = service.compare(ref, raw)
    assert result.dx_px == pytest.approx(0)
    assert result.dy_px == pytest.approx(0)


@pytest.mark.parametrize('mode', ['master', 'trigger'])
def test_capture_uses_shared_view_capture_without_any_run_result(tmp_path, monkeypatch, mode):
    service, ref, golden = reference(tmp_path)
    calls = []
    cam = SimpleNamespace(begin_trigger_capture=lambda: calls.append('begin'),
        end_trigger_capture=lambda: calls.append('end'),
        exit_trigger_session=lambda **kw: calls.append(('exit', kw)))
    pico = SimpleNamespace(is_available=lambda: True, quiesce=lambda: calls.append('idle'),
        prepare_master=lambda camera: calls.append('master'))
    service.camera, service.pico = cam, pico
    monkeypatch.setattr('app.services.camera_position.apply_view_camera_profile', lambda *a: None)
    def capture(self, **kwargs):
        assert self.cam is cam and self.pico is pico
        assert kwargs['image_rotation_override'] == 0
        assert kwargs['capture_request_source'] == 'diagnostic'
        calls.append('capture')
        return golden
    monkeypatch.setattr('app.services.camera_position.ViewCapture.capture', capture)
    monkeypatch.setattr('app.services.inspection_runtime.InspectionRuntime.run', lambda *a, **k: pytest.fail('RUN called'))
    monkeypatch.setattr('app.services.modbus_service.ModbusService.signal_result', lambda *a: pytest.fail('Modbus result'))
    monkeypatch.setattr('app.services.db_service.DbService.insert_result', lambda *a, **k: pytest.fail('production DB'), raising=False)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    image, result = service.capture(ref, mode)
    assert result.invalid_reason is None
    assert calls == (['master', 'capture', 'idle'] if mode == 'master' else ['begin', 'capture', 'idle', 'end', ('exit', {'restore_master': False})])
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_capture_failure_still_quiesces_and_releases_trigger(tmp_path, monkeypatch):
    service, ref, _ = reference(tmp_path)
    calls = []
    service.camera = SimpleNamespace(begin_trigger_capture=lambda: calls.append('begin'),
        end_trigger_capture=lambda: calls.append('end'), exit_trigger_session=lambda **kw: calls.append('exit'))
    service.pico = SimpleNamespace(is_available=lambda: True, quiesce=lambda: calls.append('idle'))
    monkeypatch.setattr('app.services.camera_position.apply_view_camera_profile', lambda *a: None)
    monkeypatch.setattr('app.services.camera_position.ViewCapture.capture', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('timeout')))
    with pytest.raises(RuntimeError, match='timeout'):
        service.capture(ref, 'trigger')
    assert calls == ['begin', 'idle', 'end', 'exit']


@pytest.mark.parametrize('angle,ok', [(0, True), (.4, True), (-.4, True), (.41, False), (-.41, False)])
def test_guided_edge_saved_rotation_limit_is_evaluated_without_clipping_measurement(tmp_path, monkeypatch, angle, ok):
    service, ref, image = reference(tmp_path, 'guided_edge')
    # Save the existing supported parameter through fixture construction only.
    recipe = ref.recipe
    recipe.views[0].tools[0].params.values['reference_max_angle_deg'] = .4
    save_recipe_config('test', recipe, base_dir=tmp_path)
    ref = service.load_reference('test')
    before = ref.recipe.to_dict()
    def match(golden, current, params, thresholds, roi):
        assert params['reference_max_angle_deg'] == 90
        assert thresholds['max_shift_x'] == float('inf')
        assert params.get('reference_min_coverage') == ref.locator.params.values.get('reference_min_coverage')
        return None, dict(dx=0, dy=0, theta_deg=angle, corr=.99, threshold_corr=.75, found=True)
    monkeypatch.setattr('app.services.camera_position.run_locator_template_match', match)
    result = service.compare(ref, image)
    assert result.overall_ok is ok
    assert result.rotation_deg == angle
    assert ref.recipe.to_dict() == before


def test_full_search_preserves_legacy_golden_crop_and_template_geometry(tmp_path):
    service, ref, image = reference(tmp_path)
    recipe = ref.recipe
    tool = recipe.views[0].tools[0]
    tool.params.values['use_golden_crop'] = True
    tool.roi = ToolRoi({'x': 30, 'y': 25, 'w': 20, 'h': 20})
    save_recipe_config('test', recipe, base_dir=tmp_path)
    ref = service.load_reference('test')
    current = cv2.warpAffine(image, np.float32([[1, 0, 35], [0, 1, 0]]), (120, 100))
    result = service.compare(ref, current)
    assert result.dx_px == pytest.approx(35)
    assert ref.locator.params.values['use_golden_crop'] is True


def test_ambiguous_locator_does_not_choose_a_production_transform(tmp_path):
    service, ref, image = reference(tmp_path)
    recipe = ref.recipe
    recipe.views[0].tools.append(locator())
    save_recipe_config('test', recipe, base_dir=tmp_path)
    ref = service.load_reference('test')
    result = service.compare(ref, image)
    assert result.invalid_reason
    assert not result.corrections

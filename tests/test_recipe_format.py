from pathlib import Path
import json
import pytest
from app.models.schema import RecipeV2
from app.services.recipe_format import RecipeFormatError, validate_recipe_document
from app.services.recipe_service import RecipeService
from app.services.storage_service import load_recipe_config

@pytest.mark.parametrize('version', [None, 1, 2, 4, True, '3'])
def test_unsupported_versions(version):
    data = RecipeV2().to_dict()
    data['format_version'] = version
    with pytest.raises(RecipeFormatError, match='verzia'):
        validate_recipe_document(data)

@pytest.mark.parametrize('damage', ['views', 'duplicate', 'reference', 'tool', 'nan', 'mode'])
def test_malformed_document(damage):
    data = RecipeV2().to_dict()
    if damage == 'views': data['views'] = []
    if damage == 'duplicate': data['views'] *= 2
    if damage == 'reference': data['views'][0]['frame_source_view_id'] = 'missing'
    if damage == 'tool': data['views'][0]['tools'] = [{'type': 'absdiff'}]
    if damage == 'nan': data['views'][0]['trigger_gap_ms'] = float('nan')
    if damage == 'mode': data['views'][0]['trigger_mode'] = 'unknown'
    with pytest.raises(RecipeFormatError): validate_recipe_document(data)

def test_rejected_load_preserves_active_recipe_and_database(tmp_path):
    service = RecipeService(tmp_path)
    service.create('good')
    service.load('good')
    folder = tmp_path / 'recipes' / 'old'
    folder.mkdir()
    path = folder / 'recipe.json'
    original = '{"tools": []}'
    path.write_text(original)
    before = service.list()
    with pytest.raises(RecipeFormatError): service.load('old')
    assert service.tool.recipe == 'good'
    assert service.list() == before
    assert path.read_text() == original

def test_missing_file_not_created(tmp_path):
    with pytest.raises(RecipeFormatError): load_recipe_config('missing', base_dir=tmp_path)
    assert not (tmp_path / 'recipes').exists()

def test_new_and_published_recipe_roundtrip(tmp_path):
    service = RecipeService(tmp_path)
    service.create('new')
    service.publish_recipe('new')
    draft = service._load_recipe_config('new')
    published = service._load_published_recipe_config('new')
    assert draft.to_dict() == published.to_dict()
    assert draft.to_dict()['format_version'] == 3
    path = tmp_path / 'recipes' / 'new' / 'recipe.published.json'
    path.write_text('{broken')
    with pytest.raises(RecipeFormatError): service._load_published_recipe_config('new')

def test_create_does_not_overwrite_old_recipe(tmp_path):
    service = RecipeService(tmp_path)
    folder = tmp_path / 'recipes' / 'old'
    folder.mkdir()
    path = folder / 'recipe.json'
    path.write_text('{}')
    with pytest.raises(ValueError): service.create('old')
    assert path.read_text() == '{}'
    assert 'old' not in service.list()

def test_corrupt_golden_does_not_switch_active_tool(tmp_path):
    service = RecipeService(tmp_path)
    service.create('good')
    service.load('good')
    service.create('bad')
    (tmp_path / 'recipes' / 'bad' / 'golden.png').write_bytes(b'not an image')
    with pytest.raises(Exception): service.tool.load_recipe('bad')
    assert service.tool.recipe == 'good'

def test_invalid_run_recipe_rejected_before_camera_pause(monkeypatch):
    from types import SimpleNamespace
    from app.services.inspection_runtime import InspectionRuntime
    host = SimpleNamespace(data_root=Path('/data'), current_recipe_name=lambda: 'old')
    def reject(_, **kwargs):
        raise RecipeFormatError('old recipe')
    monkeypatch.setattr('app.services.inspection_runtime.load_recipe_config', reject)
    with pytest.raises(RecipeFormatError):
        InspectionRuntime._prepare_run_trigger(host, trigger_source="manual")

@pytest.mark.parametrize('payload', [None, {}, {'tools': []}, {'format_version': 2}])
def test_model_parser_cannot_bypass_format_gate(payload):
    with pytest.raises(RecipeFormatError):
        RecipeV2.from_dict(payload)

@pytest.mark.parametrize('kind', ['ssd', 'absdiff', 'light_presence', 'template_match'])
def test_removed_tools_rejected_by_model_registry_and_pipeline(kind):
    import numpy as np
    from app.models.schema import Tool
    from app.services.tool_registry import ToolRegistry
    from app.services.tool_pipeline import run_pipeline
    with pytest.raises(RecipeFormatError):
        Tool(type=kind)
    with pytest.raises(KeyError):
        ToolRegistry.create_tool(kind)
    recipe = RecipeV2(tools=[ToolRegistry.make_default_tool('ssim')], logging_enabled=False)
    recipe.tools[0].type = kind  # Even post-construction mutation cannot bypass RUN.
    with pytest.raises(ValueError):
        run_pipeline(np.zeros((8, 8), dtype=np.uint8), np.zeros((8, 8), dtype=np.uint8), recipe)

@pytest.mark.parametrize('params', [
    {'angle_enabled': False}, {'rotation_enabled': True}, {'angle_roi': {}},
    {'alignment_mode': 'legacy_angle'}, {'alignment_mode': 'unknown'},
])
def test_legacy_locator_rejected_at_model_and_direct_runner(params):
    import numpy as np
    from app.models.schema import Tool, ToolParams
    from app.services.tools.locator_template import run_locator_template_match
    with pytest.raises(RecipeFormatError):
        Tool(type='locator.template_match', params=ToolParams(params))
    image = np.zeros((8, 8), dtype=np.uint8)
    with pytest.raises(RecipeFormatError):
        run_locator_template_match(image, image, params, {}, {'x': 0, 'y': 0, 'w': 8, 'h': 8})

@pytest.mark.parametrize('mode', ['translation', 'template_rotation', 'guided_edge'])
def test_current_locator_format_roundtrip(mode):
    from app.services.tool_registry import ToolRegistry
    tool = ToolRegistry.make_default_tool('locator.template_match')
    tool.params.values['alignment_mode'] = mode
    recipe = RecipeV2(tools=[tool])
    restored = RecipeV2.from_dict(recipe.to_dict())
    assert restored.to_dict() == recipe.to_dict()
    assert restored.tools[0].params.values['alignment_mode'] == mode

@pytest.mark.parametrize('field', ['roi', 'ignore_mask', '__roi__'])
def test_geometry_in_params_is_rejected(field):
    from app.models.schema import Tool, ToolParams
    with pytest.raises(RecipeFormatError):
        Tool(type='ssim', params=ToolParams({field: {}}))

@pytest.mark.parametrize('mask', [
    {'type': 'ndarray', 'shape': [1, 1], 'data': [255]},
    {'encoding': 'png_base64', 'data': 'invalid'},
])
def test_invalid_or_old_mask_never_becomes_no_mask(mask):
    from app.models.schema import ToolMask
    with pytest.raises(RecipeFormatError):
        ToolMask.from_obj(mask)
    with pytest.raises(RecipeFormatError):
        ToolMask(mask)


def test_invalid_recipe_stops_golden_capture_and_camera_profile(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.ui.main_window import MainWindow
    def reject(_, **kwargs):
        raise RecipeFormatError('rejected')
    monkeypatch.setattr('app.ui.main_window.load_recipe_config', reject)
    capture = Mock()
    host = SimpleNamespace(data_root=Path('/data'), current_recipe_name=lambda: 'old',
        _capture_frame_for_view=capture, _logger=Mock())
    with pytest.raises(RecipeFormatError):
        MainWindow.capture_frame_for_golden(host)
    capture.assert_not_called()
    from app.services.inspection_runtime import InspectionRuntime
    monkeypatch.setattr('app.services.inspection_runtime.load_recipe_config', reject)
    apply = Mock()
    monkeypatch.setattr('app.services.inspection_runtime.apply_view_camera_profile', apply)
    with pytest.raises(RecipeFormatError):
        InspectionRuntime.prepare(host, 'old', 'trigger', 'view_1')
    apply.assert_not_called()


def test_every_current_catalog_tool_roundtrips_without_legacy_fields():
    from app.services.tool_registry import ToolRegistry
    from app.services.recipe_format import validate_recipe_document
    for kind in ToolRegistry.list_tool_types():
        tool = ToolRegistry.make_default_tool(kind)
        document = RecipeV2(tools=[tool]).to_dict()
        validate_recipe_document(document)
        assert RecipeV2.from_dict(document).to_dict() == document


def test_new_recipe_does_not_create_legacy_threshold_storage(tmp_path):
    service = RecipeService(tmp_path)
    service.create('current')
    service.load('current')
    service.publish_recipe('current')
    folder = tmp_path / 'recipes' / 'current'
    assert not (folder / 'thresholds.json').exists()
    assert not (folder / 'regions.json').exists()
    assert service.db.conn().execute("SELECT name FROM sqlite_master WHERE name='thresholds'").fetchone() is None

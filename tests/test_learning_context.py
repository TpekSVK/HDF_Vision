from types import SimpleNamespace

import numpy as np
import pytest

from app.models.schema import RecipeV2, ToolRoi, ViewCameraProfile
from app.services.learning_context import learning_signature, bind_samples, samples_match
from app.services.presence_absence_v2_service import save_sample, reset_learning_assets, save_model
from app.services.tool_registry import ToolRegistry
from app.services.tool_pipeline import run_pipeline, run_tool_test


def fixture():
    golden = np.random.default_rng(42).integers(0, 255, (32, 40), dtype=np.uint8)
    target = ToolRegistry.make_default_tool('presence.absence_v2')
    target.roi = ToolRoi({'x': 4, 'y': 6, 'w': 12, 'h': 10})
    recipe = RecipeV2(tools=[target], logging_enabled=False)
    view = recipe.views[0]
    target = recipe.tools[0]
    return golden, view, target, recipe


@pytest.mark.parametrize('change', ['golden', 'rotation', 'profile', 'pico', 'roi', 'mask', 'locator'])
def test_pixel_context_changes_invalidate_signature(change):
    golden, view, target, recipe = fixture()
    before = learning_signature(golden, view, target, recipe.tools)
    if change == 'golden':
        golden[0, 0] ^= 255
    elif change == 'rotation':
        view.image_rotation = 180
    elif change == 'profile':
        view.camera_profile = ViewCameraProfile(exposure_us=2000)
    elif change == 'pico':
        view.pico_profile = 'V2'
    elif change == 'roi':
        target.roi.data['x'] += 1
    elif change == 'mask':
        target.ignore_mask.value = np.ones_like(golden)
    else:
        locator = ToolRegistry.make_default_tool('locator.template_match')
        recipe.tools.append(locator)
    assert learning_signature(golden, view, target, recipe.tools) != before


def test_labels_thresholds_and_learning_bookkeeping_do_not_change_signature():
    golden, view, target, recipe = fixture()
    before = learning_signature(golden, view, target, recipe.tools)
    target.name = 'Nový názov'
    view.name = 'Nový pohľad'
    target.order = 9
    target.thresholds.values['sensitivity'] = 75
    target.params.values.update(reference_model_ready=True, sample_count_ok=33, polarity='bright')
    assert learning_signature(golden.copy(), view, target, recipe.tools) == before


def test_manifest_refuses_mixed_and_old_samples_without_deleting(tmp_path):
    bind_samples(tmp_path, 'first')
    path = save_sample(np.zeros((8, 8), dtype=np.uint8), tmp_path / 'ok')
    assert samples_match(tmp_path, 'first')
    with pytest.raises(ValueError, match='inému'):
        bind_samples(tmp_path, 'changed')
    assert path.exists()
    (tmp_path / 'sample_context.json').write_text('broken')
    assert not samples_match(tmp_path, 'first')
    with pytest.raises(ValueError):
        bind_samples(tmp_path, 'first')
    assert reset_learning_assets(tmp_path) == (True, None)
    assert not (tmp_path / 'sample_context.json').exists()
    bind_samples(tmp_path, 'changed')
    assert samples_match(tmp_path, 'changed')


def test_fast_sample_batch_preserves_every_frame(tmp_path, monkeypatch):
    monkeypatch.setattr('app.services.presence_absence_v2_service.time.time', lambda: 1)
    paths = [save_sample(np.full((4, 4), i, dtype=np.uint8), tmp_path) for i in range(20)]
    assert len(set(paths)) == len(list(tmp_path.glob('*.png'))) == 20


@pytest.mark.parametrize('runner', [run_pipeline, run_tool_test])
@pytest.mark.parametrize('change', ['none', 'rename', 'threshold', 'golden', 'rotation', 'missing'])
def test_real_runners_check_model_signature(tmp_path, runner, change):
    golden, view, target, recipe = fixture()
    signature = learning_signature(golden, view, target, recipe.tools)
    stats = {'sample_preparation_version': 2}
    if change != 'missing':
        stats['learning_signature'] = signature
    save_model(tmp_path / 'model', golden[6:16, 4:16], np.ones((10, 12)), stats)
    target.params.values.update(reference_model_ready=True, reference_assets_dir=str(tmp_path))
    if change == 'rename':
        target.name = 'Kontrola'
    elif change == 'threshold':
        target.thresholds.values['sensitivity'] = 80
    elif change == 'golden':
        golden[0, 0] ^= 255
    elif change == 'rotation':
        view.image_rotation = 180
    result = runner(golden, golden.copy(), recipe)
    reports = getattr(result, 'per_tool', None) or result.reports
    assert reports[-1].metrics['model_ready'] is (change in {'none', 'rename', 'threshold'})


def test_locator_config_order_and_enabled_state_are_part_of_signature():
    golden, view, target, recipe = fixture()
    a = ToolRegistry.make_default_tool('locator.template_match')
    b = a.copy()
    a.order, b.order = 0, 1
    b.params.values['apply_alignment'] = False
    tools = [a, b, target]
    before = learning_signature(golden, view, target, tools)
    a.name = 'Premenovaný locator'
    assert learning_signature(golden, view, target, tools) == before
    a.order = 2
    assert learning_signature(golden, view, target, tools) != before
    a.order = 0
    a.enabled = False
    assert learning_signature(golden, view, target, tools) != before


def wizard_fixture(golden, view, target, recipe):
    from app.ui.golden_wizard.golden_wizard import GoldenWizard
    warnings = []
    wizard = SimpleNamespace(
        _active_view_id=view.id,
        _current_golden_image=lambda: golden,
        _view_by_id=lambda _: view,
        _current_recipe_name=lambda: 'test',
        recipes=SimpleNamespace(get_draft_tools=lambda *args: recipe.tools),
        _warn=warnings.append,
        _presence_v2_expected_shape=lambda tool: (10, 12),
        _presence_v2_ignore_mask=lambda tool: None,
    )
    wizard._presence_learning_signature = lambda tool, view_id: GoldenWizard._presence_learning_signature(wizard, tool, view_id)
    return wizard, warnings


def test_wizard_rebuild_checks_dataset_provenance_and_records_model_signature(tmp_path):
    from app.ui.golden_wizard.golden_wizard import GoldenWizard
    from app.services.presence_absence_v2_service import ensure_assets_dirs, load_model
    golden, view, target, recipe = fixture()
    wizard, warnings = wizard_fixture(golden, view, target, recipe)
    signature = learning_signature(golden, view, target, recipe.tools)
    dirs = ensure_assets_dirs(tmp_path)
    bind_samples(tmp_path, signature)
    for _ in range(15):
        save_sample(golden[6:16, 4:16], dirs['ok'])
    target.params.values['sample_preparation_version'] = 2
    assert GoldenWizard._rebuild_presence_v2_model(wizard, target, dirs, view.id) is not None
    assert not warnings
    assert load_model(dirs['model']).stats['learning_signature'] == signature
    view.image_rotation = 180
    assert GoldenWizard._rebuild_presence_v2_model(wizard, target, dirs, view.id) is None
    assert warnings
    assert len(list(dirs['ok'].glob('*.png'))) == 15
    assert load_model(dirs['model']).stats['learning_signature'] == signature


def test_renamed_tool_keeps_existing_dataset_location(tmp_path):
    from app.ui.golden_wizard.golden_wizard import GoldenWizard
    from app.services.presence_absence_v2_service import resolve_assets_dir
    golden, view, target, recipe = fixture()
    assets = resolve_assets_dir(tmp_path, 'test', view.id, '0:old_name')
    target.params.values['reference_assets_dir'] = str(assets)
    target.name = 'Nový názov'
    wizard, _ = wizard_fixture(golden, view, target, recipe)
    wizard.recipes.base = tmp_path
    wizard._selected_tool_row = 0
    assert GoldenWizard._presence_v2_context(wizard)[-1] == assets

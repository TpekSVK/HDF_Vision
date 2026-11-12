from types import SimpleNamespace

import numpy as np
import pytest

from app.models.schema import ToolParams
from app.services.shared_mold_ok import SharedMoldOK
from app.services.tool_registry import ToolRegistry
from app.services.presence_absence_v2_service import save_sample, load_samples
from app.services.capture_batch import CaptureBatch
from app.ui.golden_wizard.golden_wizard import GoldenWizard


@pytest.mark.parametrize('bulk,cancel', [(True, False), (False, False), (True, True)])
def test_reset_scope_and_fresh_capture(tmp_path, monkeypatch, bulk, cancel):
    import app.ui.golden_wizard.golden_wizard as module
    a = ToolRegistry.make_default_tool('mold.protection_v1')
    b = a.copy(); b.name = 'second'; b.order = 1
    other = ToolRegistry.make_default_tool('ssim')
    tools = [a, b, other]
    assets = [tmp_path / 'local' / str(i) for i in range(3)]
    for tool, directory in zip(tools, assets):
        save_sample(np.zeros((8, 8), np.uint8), directory / 'ok')
        tool.params = ToolParams(dict(reference_model_ready=True, sample_count_ok=1))
    current = SharedMoldOK(tmp_path, 'r', 'v1', 'current')
    old = SharedMoldOK(tmp_path, 'r', 'v1', 'old')
    other_view = SharedMoldOK(tmp_path, 'r', 'v2', 'current')
    other_recipe = SharedMoldOK(tmp_path, 'other', 'v1', 'current')
    for pool in (current, old, other_view, other_recipe):
        pool.add(np.zeros((8, 8), np.uint8))

    class Confirmation:
        Question = 0
        Yes = 1
        No = 2
        def __init__(self, *args): pass
        def __getattr__(self, name): return lambda *args: None
        def exec(self): return self.No if cancel else self.Yes
    class Choice:
        def __init__(self, *args): pass
        def setChecked(self, value): assert value
        def isChecked(self): return bulk
    monkeypatch.setattr(module, 'QMessageBox', Confirmation)
    monkeypatch.setattr(module, 'QCheckBox', Choice)
    monkeypatch.setattr(module, 'compute_tool_identity', lambda t:(str(t.order), '', 0))
    monkeypatch.setattr(module, 'resolve_assets_dir', lambda base,r,v,ident:assets[int(ident)])
    def update(recipe, row, tool, view_id):
        assert (recipe, view_id) == ('r', 'v1')
        tools[row] = tool
    w = SimpleNamespace(
        _presence_v2_context=lambda:('r', 'v1', 0, a, assets[0]),
        _authorize_write=lambda:True,
        recipes=SimpleNamespace(base=tmp_path, get_draft_tools=lambda *args:tools, update_tool=update),
        _presence_tuning_results={}, _update_dirty_state=lambda *args:None,
        _refresh_tools_table=lambda:None, _warn=lambda msg:pytest.fail(msg))
    GoldenWizard._on_presence_v2_learning(w, 'reset')
    assert len(list(other_view.frames())) == len(list(other_recipe.frames())) == 1
    assert len(load_samples(assets[2] / 'ok')) == 1
    assert len(load_samples(assets[0] / 'ok')) == (1 if cancel else 0)
    assert len(load_samples(assets[1] / 'ok')) == (0 if bulk and not cancel else 1)
    assert len(list(current.frames())) == len(list(old.frames())) == (0 if bulk and not cancel else 1)
    if bulk and not cancel:
        # The original bug: reset -> 15 new frames -> share must stay at 15.
        batch = CaptureBatch(assets[0], 'ok', 'sig', current.directory)
        image = np.zeros((8, 8), np.uint8)
        ids = [batch.add(image, image) for _ in range(15)]
        assert batch.commit(ids).result(timeout=5) == 15
        batch.discard(); batch.cleanup_future.result(timeout=5)
        assert current.import_into(assets[0], 'sig', lambda f:f) == 0
        assert current.import_into(assets[1], 'sig', lambda f:f) == 15
        assert len(load_samples(assets[0] / 'ok')) == len(load_samples(assets[1] / 'ok')) == 15


def test_clear_view_missing_and_failure(tmp_path, monkeypatch):
    import app.services.shared_mold_ok as module
    SharedMoldOK.clear_view(tmp_path, 'r', 'v')
    def denied(path):
        raise PermissionError('denied')
    monkeypatch.setattr(module.shutil, 'rmtree', denied)
    with pytest.raises(PermissionError, match='denied'):
        SharedMoldOK.clear_view(tmp_path, 'r', 'v')

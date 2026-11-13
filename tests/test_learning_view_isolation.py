import hashlib

import imageio.v3 as iio
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from app.models.learning_contract import SAMPLE_PREPARATION_VERSION
from app.models.schema import ToolRoi
from app.services.learning_context import bind_samples, learning_signature
from app.services.learning_dataset import LearningDataset
from app.services.presence_absence_v2_service import ensure_assets_dirs, resolve_assets_dir, save_sample
from app.services.recipe_service import RecipeService
from app.services.tool_registry import ToolRegistry
from app.ui.golden_wizard.golden_wizard import GoldenWizard
from app.utils.tool_identity import compute_tool_identity


@pytest.mark.parametrize('capture_mode', [None, 'capture_ok', 'capture_nok'])
def test_switching_views_preserves_models_trained_on_different_golden_images(tmp_path, monkeypatch, capture_mode):
    app = QApplication.instance() or QApplication([])
    recipes = RecipeService(tmp_path)
    recipes.create('default')
    recipes.add_view('default', view_id='view_2', view_name='diel', camera_id='camera_2')
    snapshots = {}
    for index, view_id in enumerate(('view_1', 'view_2')):
        view = recipes.get_view('default', view_id)
        golden = np.full((32, 32), 40 + index * 140, np.uint8)
        iio.imwrite(tmp_path / 'recipes/default' / view.golden_path, golden)
        tool = ToolRegistry.make_default_tool('mold.protection_v1')
        tool.roi = ToolRoi(dict(x=0, y=0, w=16, h=16))
        tool.params.values.update(sample_preparation_version=SAMPLE_PREPARATION_VERSION, min_ok_samples=2)
        recipes.add_tool('default', tool, view_id=view_id)
        tool = recipes.get_draft_tools('default', view_id)[0]
        identity, _, _ = compute_tool_identity(tool)
        assets = resolve_assets_dir(tmp_path, 'default', view_id, identity)
        signature = learning_signature(golden, view, tool, [tool])
        bind_samples(assets, signature)
        dirs = ensure_assets_dirs(assets)
        for _ in range(2):
            save_sample(golden[:16, :16], dirs['ok'])
        LearningDataset().rebuild(tool, dirs, view_id, signature)
        tool.params.values['reference_assets_dir'] = str(assets)
        recipes.update_tool('default', 0, tool, view_id=view_id)
        snapshots.update({path: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in assets.rglob('*') if path.is_file()})
    wizard = GoldenWizard(None, recipes)
    monkeypatch.setattr(wizard, '_has_unsaved_changes', lambda: False)
    try:
        for view_id in ('view_2', 'view_1', 'view_2', 'view_1'):
            wizard._switch_active_view(view_id)
            tool = recipes.get_draft_tools('default', view_id)[0]
            assert not tool.params.values['reference_model_invalidated'], view_id
            assert tool.params.values['reference_model_ready'], view_id
            assert 'Pripravený' in wizard._tool_panel._presence_model_state.text()
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
                   for path, digest in snapshots.items())
        if capture_mode:
            from app.ui.golden_wizard import golden_wizard as module

            class CaptureDialog:
                def __init__(self, *, batch, validate_commit, crop_fn, **kwargs):
                    self.batch, self.validate_commit, self.crop_fn = batch, validate_commit, crop_fn
                    self.saved_count = 0

                def exec(self):
                    self.validate_commit()
                    frame = wizard.current_img.copy()
                    ident = self.batch.add(self.crop_fn(frame), frame)
                    self.saved_count = self.batch.commit([ident]).result(timeout=5)
                    return QDialog.Accepted

                def deleteLater(self):
                    pass

            monkeypatch.setattr(module, 'PresenceV2SampleCaptureDialog', CaptureDialog)
            monkeypatch.setattr(wizard, '_authorize_write', lambda: True)
            wizard._on_presence_v2_learning(capture_mode)
            first = recipes.get_draft_tools('default', 'view_1')[0]
            assert first.params.values['reference_model_needs_rebuild']
            assert not first.params.values['reference_model_ready']
            wizard._switch_active_view('view_2')
            second = recipes.get_draft_tools('default', 'view_2')[0]
            assert second.params.values['reference_model_ready']
            assert not second.params.values['reference_model_invalidated']
            assert second.params.values['sample_count_ok'] == 2
            assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
                       for path, digest in snapshots.items() if 'view_2' in path.parts)
    finally:
        wizard.close()
        wizard.deleteLater()
        app.processEvents()

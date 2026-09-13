import numpy as np
import pytest
from app.models.schema import RecipeV2, ToolRoi, ToolMask
from app.services.tool_registry import ToolRegistry
from app.services.tool_pipeline import run_pipeline
from app.services.statistical_learning import LearningSamplePreparer
from app.services.presence_absence_v2_service import save_model, compute_roi_hash
from app.utils import imaging
from app.services.learning_context import learning_signature


def scene(apply_alignment=True, kind='presence.absence_v2'):
    golden = np.random.default_rng(51).integers(0, 255, (64, 80), dtype=np.uint8)
    frame = imaging.warp_by_translation_u8(golden, 4, -3)
    locator = ToolRegistry.make_default_tool('locator.template_match')
    locator.roi = ToolRoi({'x': 10, 'y': 10, 'w': 40, 'h': 40})
    locator.params.values.update(template_roi={'x': 20, 'y': 20, 'w': 16, 'h': 16}, apply_alignment=apply_alignment)
    target = ToolRegistry.make_default_tool(kind)
    target.roi = ToolRoi({'x': 22, 'y': 22, 'w': 10, 'h': 10})
    mask = np.zeros_like(golden)
    mask[25:27, 26:28] = 255
    target.ignore_mask = ToolMask(mask)
    locator.order = 0
    target.order = 1
    return golden, frame, locator, target


@pytest.mark.parametrize('apply_alignment', [True, False])
@pytest.mark.parametrize('kind', ['presence.absence_v2', 'mold.protection_v1'])
def test_learning_pixels_equal_runtime_pixels_and_golden(tmp_path, monkeypatch, apply_alignment, kind):
    from app.services.tools import presence_absence_v2 as runtime
    golden, frame, locator, target = scene(apply_alignment, kind)
    sample = LearningSamplePreparer().sample(golden, frame, target, [locator, target])
    np.testing.assert_array_equal(sample, golden[22:32, 22:32])
    model_hash = compute_roi_hash(target.roi, target.ignore_mask.value)
    save_model(tmp_path / 'model', sample, np.ones_like(sample, dtype=np.float32), {'roi_hash': model_hash, 'sample_preparation_version': 2,
        'learning_signature': learning_signature(golden, RecipeV2().views[0], target, [locator, target])})
    target.params.values.update(reference_assets_dir=str(tmp_path), reference_model_ready=True, roi_hash=model_hash)
    observed = []
    evaluate = runtime.evaluate_sample
    def record(pixels, *args, **kwargs):
        observed.append(pixels.copy())
        return evaluate(pixels, *args, **kwargs)
    monkeypatch.setattr(runtime, 'evaluate_sample', record)
    run_pipeline(golden, frame, RecipeV2(tools=[locator, target], logging_enabled=False))
    assert len(observed) == 1
    np.testing.assert_array_equal(observed[0], sample)


def test_failed_locator_never_enters_learning():
    golden, frame, locator, target = scene()
    with pytest.raises(ValueError, match='Zarovnanie'):
        LearningSamplePreparer().sample(golden, np.zeros_like(frame), target, [locator, target])


def test_disabled_locator_is_not_applied():
    golden, frame, locator, target = scene()
    locator.enabled = False
    target.ignore_mask = ToolMask(None)
    sample = LearningSamplePreparer().sample(golden, frame, target, [locator, target])
    np.testing.assert_array_equal(sample, frame[22:32, 22:32])


def test_invalidated_model_is_not_evaluated(tmp_path, monkeypatch):
    from app.services.tools import presence_absence_v2 as runtime
    golden, frame, locator, target = scene()
    sample = golden[22:32, 22:32]
    model_hash = compute_roi_hash(target.roi, target.ignore_mask.value)
    save_model(tmp_path / 'model', sample, np.ones_like(sample), {'roi_hash': model_hash, 'sample_preparation_version': 2,
        'learning_signature': learning_signature(golden, RecipeV2().views[0], target, [locator, target])})
    target.params.values.update(reference_assets_dir=str(tmp_path), reference_model_ready=True,
                               reference_model_invalidated=True, roi_hash=model_hash)
    def unexpected(*args, **kwargs):
        pytest.fail('Invalidated model was evaluated')
    monkeypatch.setattr(runtime, 'evaluate_sample', unexpected)
    result = run_pipeline(golden, frame, RecipeV2(tools=[locator, target], logging_enabled=False))
    assert result.per_tool[-1].metrics['model_ready'] is False
    assert result.per_tool[-1].status != 'ok'


def test_old_preparation_model_requires_new_learning(tmp_path, monkeypatch):
    from app.services.tools import presence_absence_v2 as runtime
    golden, frame, locator, target = scene()
    model_hash = compute_roi_hash(target.roi, target.ignore_mask.value)
    save_model(tmp_path / 'model', golden[22:32, 22:32], np.ones((10, 10)), {'roi_hash': model_hash})
    target.params.values.update(reference_assets_dir=str(tmp_path), reference_model_ready=True, roi_hash=model_hash)
    monkeypatch.setattr(runtime, 'evaluate_sample', lambda *args, **kwargs: pytest.fail('Old model evaluated'))
    result = run_pipeline(golden, frame, RecipeV2(tools=[locator, target], logging_enabled=False))
    assert result.per_tool[-1].metrics['model_ready'] is False


def test_capture_dialog_stops_auto_collection_on_preparation_failure(monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.ui.golden_wizard.presence_v2_sample_capture_dialog import PresenceV2SampleCaptureDialog
    app = QApplication.instance() or QApplication([])
    messages = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: messages.append(args[2]))
    def reject(frame):
        raise ValueError('Zarovnanie zlyhalo')
    dialog = PresenceV2SampleCaptureDialog(title='Test', capture_fn=lambda: np.ones((8, 8)),
                                         crop_fn=reject, default_mode='auto')
    dialog._auto_timer.start(10000)
    dialog._capture_once()
    assert not dialog._auto_timer.isActive()
    assert dialog.samples() == []
    assert messages == ['Zarovnanie zlyhalo']
    dialog.close()


@pytest.mark.parametrize('kind', ['presence.absence_v2', 'mold.protection_v1'])
def test_runtime_refuses_unaligned_model_after_locator_failure(tmp_path, monkeypatch, kind):
    from app.services.tools import presence_absence_v2 as runtime
    golden, frame, locator, target = scene(kind=kind)
    signature = learning_signature(golden, RecipeV2().views[0], target, [locator, target])
    save_model(tmp_path / 'model', golden[22:32, 22:32], np.ones((10, 10)),
               {'sample_preparation_version': 2, 'learning_signature': signature})
    target.params.values.update(reference_assets_dir=str(tmp_path), reference_model_ready=True)
    monkeypatch.setattr(runtime, 'evaluate_sample', lambda *args, **kwargs: pytest.fail('Unaligned sample evaluated'))
    result = run_pipeline(golden, np.zeros_like(frame), RecipeV2(tools=[locator, target], logging_enabled=False,
                                                              on_locator_failure='continue_without_alignment'))
    assert result.per_tool[-1].metrics['model_ready'] is False
    assert 'Zarovnanie' in result.per_tool[-1].diagnostics['message']

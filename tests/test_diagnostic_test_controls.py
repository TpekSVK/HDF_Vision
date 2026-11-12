from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.models.schema import RecipeV2, ToolRoi, ToolParams, ToolThresholds
from app.services.learning_context import learning_signature
from app.models.learning_contract import SAMPLE_PREPARATION_VERSION
from app.services.presence_absence_v2_service import save_model, compute_roi_hash
from app.services.tool_pipeline import run_tool_test, run_pipeline
from app.services.tool_registry import ToolRegistry
from app.ui.golden_wizard.tool_config_panel import ToolConfigPanel
from app.ui.golden_wizard.golden_wizard import GoldenWizard


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    widget = ToolConfigPanel()
    yield widget
    widget.close()


@pytest.mark.parametrize('invalid', [False, True])
def test_bottom_action_preserves_model_and_matches_run(panel, tmp_path, invalid):
    tool = ToolRegistry.make_default_tool('mold.protection_v1')
    tool.roi = ToolRoi(dict(x=0, y=0, w=16, h=16))
    golden = np.full((16, 16), 100, np.uint8)
    roi_hash = compute_roi_hash(tool.roi, tool.ignore_mask.value)
    signature = learning_signature(golden, RecipeV2().views[0], tool, [tool])
    save_model(tmp_path / 'model', golden, np.ones_like(golden), dict(
        learning_signature=signature, sample_preparation_version=SAMPLE_PREPARATION_VERSION,
        roi_hash=roi_hash))
    tool.params.values.update(reference_assets_dir=str(tmp_path), roi_hash=roi_hash,
                              reference_model_ready=True, reference_model_invalidated=invalid,
                              sample_count_ok=30, sample_count_nok=2)
    original = dict(tool.params.values)
    panel.set_tool(tool, ToolRegistry.get_tool_definition(tool.type), ToolRegistry.get_tool_schema(tool.type))
    assert not any(button.text() == 'Otestovať' for button in panel.findChildren(QPushButton))
    received = []
    panel.testRequested.connect(lambda p, t:received.append((p, t)))
    GoldenWizard._trigger_test_shortcut(SimpleNamespace(_tool_panel=panel))
    assert len(received) == 1
    params, thresholds = received[0]
    for key in ('reference_model_ready', 'reference_model_invalidated', 'reference_assets_dir',
                'sample_count_ok', 'sample_count_nok', 'roi_hash'):
        assert key not in params
    tested = tool.copy()
    tested.params = ToolParams({**original, **params})
    tested.thresholds = ToolThresholds({**tool.thresholds.values, **thresholds})
    diagnostic = run_tool_test(golden, golden, RecipeV2(tools=[tested], logging_enabled=False)).result
    production = run_pipeline(golden, golden, RecipeV2(tools=[tool], logging_enabled=False)).per_tool[-1]
    assert diagnostic.status == production.status == ('nok' if invalid else 'ok')
    assert diagnostic.metrics['inspection_fault'] is invalid
    assert diagnostic.metrics['model_ready'] is (not invalid)
    assert tool.params.values == original
    # The lower-bar action cannot re-enter a running test, and works again afterwards.
    assert not panel.is_test_enabled()
    panel.trigger_test()
    assert len(received) == 1
    panel.set_test_running(False)
    panel.trigger_test()
    assert len(received) == 2
    panel.clear()
    assert not panel.is_test_enabled()

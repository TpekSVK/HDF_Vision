import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from app.models.schema import ToolRoi, RecipeV2
from app.models.learning_contract import SAMPLE_PREPARATION_VERSION
from app.services.learning_context import learning_signature
from app.services.presence_absence_v2_service import save_model, compute_roi_hash
from app.services.tool_registry import ToolRegistry
from app.services.tool_pipeline import run_tool_test
from app.ui.golden_wizard.tool_config_panel import ToolConfigPanel
from app.ui.golden_wizard.style import metric_label


@pytest.mark.parametrize('has_blobs', [False, True])
def test_mold_metrics_and_smallest_filtered_blob(tmp_path, has_blobs):
    app = QApplication.instance() or QApplication([])
    tool = ToolRegistry.make_default_tool('mold.protection_v1')
    tool.roi = ToolRoi(dict(x=0,y=0,w=64,h=64))
    golden = np.full((64,64),100,np.uint8)
    frame = golden.copy()
    if has_blobs:
        frame[5:10,5:10] = 200
        frame[20:30,20:30] = 200
        frame[40,40] = 200  # Smaller than min_blob_area; must not determine minimum.
    model_hash = compute_roi_hash(tool.roi, tool.ignore_mask.value)
    save_model(tmp_path/'model', golden, np.ones_like(golden), dict(
        learning_signature=learning_signature(golden,RecipeV2().views[0],tool,[tool]),
        sample_preparation_version=SAMPLE_PREPARATION_VERSION,roi_hash=model_hash))
    tool.params.values.update(reference_model_ready=True,reference_assets_dir=str(tmp_path),roi_hash=model_hash)
    tool.thresholds.values.update(min_blob_area=10,total_area_threshold=1)
    result=run_tool_test(golden,frame,RecipeV2(tools=[tool],logging_enabled=False)).result
    assert result.metrics['smallest_blob_area'] == (25 if has_blobs else 0)
    assert result.metrics['largest_blob_area'] == (100 if has_blobs else 0)
    assert 'inspection_fault' in result.metrics  # Hidden only in UI, not removed from decisions.
    panel=ToolConfigPanel()
    panel.set_tool(tool,ToolRegistry.get_tool_definition(tool.type),ToolRegistry.get_tool_schema(tool.type))
    panel.show_test_result(result,10)
    labels={panel._metrics_table.item(row,0).text() for row in range(panel._metrics_table.rowCount())}
    assert labels == {'Nájdený zvyšok','Počet zvyškov','Celková zmenená plocha [px]',
                      'Najväčší zvyšok [px]','Najmenší zvyšok [px]'}
    assert panel._status_value_label.text() == ('NOK' if has_blobs else 'OK')
    assert panel._latency_label.text().startswith('Čas: ')
    assert metric_label('mold_empty','Long description') == 'Prázdna forma'
    panel.close()

import numpy as np
from PySide6.QtWidgets import QApplication, QMessageBox
from app.models.schema import ToolRoi
from app.models.learning_contract import SAMPLE_PREPARATION_VERSION
from app.services.recipe_service import RecipeService
from app.services.tool_registry import ToolRegistry
from app.services.learning_context import bind_samples
from app.services.presence_absence_v2_service import save_sample
from app.ui.golden_wizard.golden_wizard import GoldenWizard


def test_rebuild_updates_selected_panel_without_reselection(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    recipes = RecipeService(tmp_path)
    recipes.create('default')
    tool = ToolRegistry.make_default_tool('mold.protection_v1')
    tool.roi = ToolRoi(dict(x=0,y=0,w=16,h=16))
    tool.params.values['sample_preparation_version'] = SAMPLE_PREPARATION_VERSION
    recipes.add_tool('default',tool,view_id='view_1')
    w = GoldenWizard(None,recipes)
    golden = np.full((32,32),100,np.uint8)
    monkeypatch.setattr(w,'_current_golden_image',lambda:golden)
    monkeypatch.setattr(w,'_info',lambda message:None)
    monkeypatch.setattr(w,'_warn',lambda message: (_ for _ in ()).throw(AssertionError(message)))
    monkeypatch.setattr(QMessageBox,'exec',lambda self:QMessageBox.Yes)
    try:
        w.tools_table.selectRow(0)
        context = w._presence_v2_context()
        recipe,view,row,current,assets = context
        bind_samples(assets,w._presence_learning_signature(current,view))
        for _ in range(15):save_sample(golden[:16,:16],assets/'ok')
        w._refresh_tools_table()
        assert 'Nenaučený' in w._tool_panel._presence_model_state.text()
        w._apply_mold_learning('rebuild',w._presence_v2_context())
        assert w.tools_table.currentRow() == 0
        assert 'Pripravený' in w._tool_panel._presence_model_state.text()
        assert w._tool_panel._current_tool.params.values['reference_model_ready']
    finally:
        monkeypatch.setattr(w,'_has_unsaved_changes',lambda:False)
        w.close()

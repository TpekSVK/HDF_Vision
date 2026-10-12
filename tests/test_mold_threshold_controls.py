from types import SimpleNamespace
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFormLayout
from app.services.recipe_service import RecipeService
from app.services.tool_registry import ToolRegistry
from app.ui.golden_wizard.tool_config_panel import ToolConfigPanel
from app.ui.golden_wizard.golden_wizard import GoldenWizard


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('total,old_largest,new_largest,expected', [
    (50,0,2000,2000), (3000,0,2000,3000), (2000,2000,1000,2000),
    (2000,2000,0,2000), (2000,0,2000,2000), (0,0,2000,2000)])
def test_largest_residual_edit_updates_ui_and_published_recipe(app,tmp_path,total,old_largest,new_largest,expected):
    service=RecipeService(tmp_path);service.create('test')
    tool=ToolRegistry.make_default_tool('mold.protection_v1')
    tool.thresholds.values.update(total_area_threshold=total,max_largest_blob_area=old_largest)
    service.add_tool('test',tool,view_id='view_1')
    panel=ToolConfigPanel()
    panel.set_tool(tool,ToolRegistry.get_tool_definition(tool.type),ToolRegistry.get_tool_schema(tool.type))
    w=SimpleNamespace(_filtered_roi_timer=QTimer(),_selected_tool_row=0,_active_view_id='view_1',
        _current_recipe_name=lambda:'test',recipes=service,_tool_panel=panel,
        _update_dirty_state=lambda *a:None,_err=lambda msg:pytest.fail(msg))
    panel.thresholdChanged.connect(lambda name,value:GoldenWizard._on_tool_threshold_changed(w,name,value))
    try:
        panel._threshold_widgets['max_largest_blob_area'].setValue(new_largest)
        assert panel._threshold_widgets['total_area_threshold'].value()==expected
        saved=service.get_draft_tools('test','view_1')[0]
        assert saved.thresholds.values['total_area_threshold']==expected
        assert saved.thresholds.values['max_largest_blob_area']==new_largest
        assert saved.params.to_dict()==tool.params.to_dict()
        service.save_tools('test',[saved],view_id='view_1');service.publish_recipe('test')
        loaded=RecipeService(tmp_path).get_published_tools('test','view_1')[0]
        assert loaded.thresholds.values==saved.thresholds.values
        # A direct edit of the total is still allowed independently.
        panel._threshold_widgets['total_area_threshold'].setValue(4000)
        assert panel._threshold_widgets['max_largest_blob_area'].value()==new_largest
        assert service.get_draft_tools('test','view_1')[0].thresholds.values['total_area_threshold']==4000
    finally:panel.close()


def test_empty_mold_sensitivity_contains_only_six_requested_fields(app):
    panel=ToolConfigPanel()
    try:
        tool=ToolRegistry.make_default_tool('mold.protection_v1')
        panel.set_tool(tool,ToolRegistry.get_tool_definition(tool.type),ToolRegistry.get_tool_schema(tool.type))
        expected = [panel._threshold_wrappers['sensitivity'], panel._param_wrappers['polarity'],
                    panel._threshold_wrappers['max_largest_blob_area'], panel._threshold_wrappers['max_blob_count'],
                    panel._threshold_wrappers['total_area_threshold'], panel._threshold_wrappers['min_blob_area']]
        assert [panel._threshold_layout.itemAt(row, QFormLayout.ItemRole.SpanningRole).widget()
                for row in range(6)] == expected
        assert panel._threshold_layout.rowCount() == 7  # Six fields and the live result label.
        assert panel._advanced_layout.rowCount()>0
        assert not panel._advanced_section.isHidden()
        for name, wrapper in panel._param_wrappers.items():
            if name != 'polarity':
                assert panel._advanced_layout.indexOf(wrapper)>=0
        for kind in ('presence.absence_v2','locator.template_match'):
            tool=ToolRegistry.make_default_tool(kind)
            panel.set_tool(tool,ToolRegistry.get_tool_definition(kind),ToolRegistry.get_tool_schema(kind))
            assert panel._advanced_layout.rowCount()>0
    finally:panel.close()

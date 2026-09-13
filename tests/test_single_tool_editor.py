import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QTableWidget
from app.ui.golden_wizard.golden_wizard import GoldenWizard
from app.ui.golden_wizard.tool_config_panel import ToolConfigPanel
from app.services.tool_registry import ToolRegistry

@pytest.fixture(scope='module')
def qt_app():
    return QApplication.instance() or QApplication([])

def test_panel_name_changes_only_on_commit_and_rejects_empty(qt_app):
    panel = ToolConfigPanel()
    tool = ToolRegistry.make_default_tool('ssim')
    tool.name = 'Pôvodný názov'
    changes = []
    panel.nameChanged.connect(changes.append)
    panel.set_tool(tool, ToolRegistry.get_tool_definition('ssim'), ToolRegistry.get_tool_schema('ssim'))
    assert changes == []
    panel._name_input.setText('  Kontrola uzáveru  ')
    panel._name_input.editingFinished.emit()
    assert changes == ['Kontrola uzáveru']
    panel._name_input.setText(' ')
    panel._name_input.editingFinished.emit()
    assert panel._name_input.text() == tool.name
    assert len(changes) == 1
    panel.clear()
    assert not panel._name_input.isEnabled()
    panel.close()

@pytest.mark.parametrize('kind', ['ssim', 'locator.template_match', 'edge_profile_deviation', 'mold.protection_v1', 'presence.absence_v2'])
def test_edit_selects_shared_workspace(qt_app, kind):
    table = QTableWidget(2, 2)
    host = SimpleNamespace(
        _active_view_id='view2', _current_recipe_name=lambda: 'recipe',
        recipes=SimpleNamespace(get_draft_tools=lambda *_: [ToolRegistry.make_default_tool('ssim'), ToolRegistry.make_default_tool(kind)]),
        tools_table=table, _refresh_tool_panel_for_selection=Mock(),
        roi_editor=Mock(), _tool_panel=Mock())
    GoldenWizard._edit_tool(host, 1)
    assert table.currentRow() == 1
    host._refresh_tool_panel_for_selection.assert_called_once()
    if kind == 'edge_profile_deviation':
        host.roi_editor.set_edit_context.assert_called_once_with('edge')
    else:
        host.roi_editor.set_edit_context.assert_not_called()
    table.close()

def test_rename_persists_selected_view_without_mutating_original():
    tool = ToolRegistry.make_default_tool('ssim')
    original = tool.to_dict()
    update = Mock()
    host = SimpleNamespace(_selected_tool_row=0, _active_view_id='view2',
        _current_recipe_name=lambda: 'recipe',
        recipes=SimpleNamespace(get_draft_tools=lambda *_: [tool], update_tool=update),
        _refresh_tools_table=Mock(), _update_dirty_state=Mock(), _err=Mock(),
        _refresh_tool_panel_for_selection=Mock())
    GoldenWizard._on_tool_name_changed(host, 'Kontrola uzáveru')
    assert update.call_args.args[2].name == 'Kontrola uzáveru'
    assert update.call_args.kwargs == {'view_id': 'view2'}
    assert tool.to_dict() == original
    host._update_dirty_state.assert_called_once_with('recipe', 'view2')

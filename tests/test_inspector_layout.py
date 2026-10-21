import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from app.services.tool_registry import ToolRegistry
from app.services.tool_contracts import ToolRunResult
from app.ui.golden_wizard.tool_config_panel import ToolConfigPanel


@pytest.mark.parametrize('height', [600, 900])
def test_inspector_anchors_and_defaults(height):
    app = QApplication.instance() or QApplication([])
    panel = ToolConfigPanel()
    tool = ToolRegistry.make_default_tool('mold.protection_v1')
    tool.name = 'kavita 1'
    panel.set_tool(tool, ToolRegistry.get_tool_definition(tool.type), ToolRegistry.get_tool_schema(tool.type))
    panel.resize(340, height)
    panel.show()
    app.processEvents()
    assert panel._presence_learning_section._header.isChecked()
    assert panel._threshold_section._header.isChecked()
    for section in (panel._geometry_section, panel._presence_validation_section,
                    panel._advanced_section, panel._result_section):
        assert not section._header.isChecked()
        assert section._content.isHidden()
    assert panel._properties_header.y() == 0
    for expanded in (False, True):
        panel._result_section._header.setChecked(expanded)
        panel.show_test_result(ToolRunResult(status='ok', latency_ms=10, metrics={'inspection_fault':False}), 10)
        app.processEvents()
        assert panel._diagnostics_group.isVisible() == expanded
        header_pos = panel._properties_header.mapTo(panel, QPoint())
        result_pos = panel._result_section.mapTo(panel, QPoint())
        bar = panel._settings_scroll.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum())
        app.processEvents()
        assert panel._properties_header.mapTo(panel, QPoint()) == header_pos
        assert panel._result_section.mapTo(panel, QPoint()) == result_pos
        assert panel._result_section.geometry().bottom() <= panel.height()
        assert panel._settings_scroll.height() >= 60
        bar.setValue(0)
    panel.close()

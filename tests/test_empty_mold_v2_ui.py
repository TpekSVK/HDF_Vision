import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from app.models.schema import ToolRoi, RecipeV2
from app.services.tool_registry import ToolRegistry
from app.services.learning_context import learning_signature
from app.services.empty_mold_v2.workflow import bind_store
from app.ui.golden_wizard.empty_mold_v2_dialog import EmptyMoldV2Dialog, AnnotationDialog
from app.ui.golden_wizard.tool_config_panel import ToolConfigPanel
from app.ui.golden_wizard.tool_catalog_dialog import ToolCatalogDialog


@pytest.fixture(scope='module')
def qt_app():
    return QApplication.instance() or QApplication([])


def test_v2_separate_catalog_and_panel(qt_app):
    catalog = ToolCatalogDialog(SimpleNamespace(list_tool_types=ToolRegistry.list_tool_types,
                                                get_tool_meta=ToolRegistry.get_tool_definition))
    entries = {entry.type_id: entry for entry in catalog._entries}
    assert 'mold.protection_v1' in entries and 'mold.protection_v2' in entries
    panel = ToolConfigPanel()
    for kind, visible in [('mold.protection_v2', True), ('mold.protection_v1', False)]:
        tool = ToolRegistry.make_default_tool(kind)
        panel.set_tool(tool, ToolRegistry.get_tool_definition(kind), ToolRegistry.get_tool_schema(kind))
        assert panel._empty_mold_v2_button.isHidden() != visible
    panel.clear()
    assert panel._empty_mold_v2_button.isHidden()
    catalog.close(); panel.close()


def test_setup_cavity_annotation_and_advanced(qt_app, tmp_path):
    tool = ToolRegistry.make_default_tool('mold.protection_v2')
    tool.roi = ToolRoi({'x': 2, 'y': 2, 'w': 56, 'h': 44})
    recipe = RecipeV2(tools=[tool])
    tool = recipe.tools[0]
    golden = np.full((48,64), 100, 'uint8')
    signature = learning_signature(golden, recipe.views[0], tool, recipe.tools)
    store = bind_store(tool, tmp_path / 'HDF_Vision.db', tmp_path / 'recipes', 'test', recipe.views[0].id)
    dialog = EmptyMoldV2Dialog(tool, store, golden, signature, lambda: golden.copy(), lambda: True)
    polygon = {'shape': 'polygon', 'points': [[10,10],[25,10],[25,30],[10,30]]}
    dialog.region_editor.set_roi_data(polygon)
    dialog.action(dialog.add_cavity)
    assert tool.params.values['cavities'][0]['name'] == 'K1'
    assert dialog.workflow.zones['K1'].any()
    dialog.fields['sensitivity'].setValue(70)
    assert tool.params.values['sensitivity'] == 70
    annotation = AnnotationDialog(golden, tool)
    annotation.editor.set_roi_data(polygon)
    annotation.add()
    assert annotation.polygons == [polygon['points']]
    annotation.finish()
    assert annotation.result() == QDialog.Accepted
    assert 'v2_active_model' not in tool.params.values
    dialog.close()


def test_v2_capture_accept_stops_auto_timer(qt_app):
    from app.ui.golden_wizard.empty_mold_v2_dialog import EmptyMoldV2CaptureDialog
    dialog = EmptyMoldV2CaptureDialog(title='V2', capture_fn=lambda: np.zeros((16,20),'uint8'), crop_fn=lambda f:f)
    dialog._auto_timer.start(1000)
    assert dialog._auto_timer.isActive()
    dialog.accept()
    assert not dialog._auto_timer.isActive()


def test_ignore_history_buttons_follow_shared_canvas_history(qt_app):
    from app.ui.roi_mask_editor import LocatorROIEditor
    from app.ui.golden_wizard.empty_mold_v2_dialog import pixmap
    editor = LocatorROIEditor()
    editor.set_background(pixmap(np.zeros((48,64), 'uint8')))
    mask = np.zeros((48,64), 'uint8'); mask[10:20,10:20] = 255
    editor.configure_ignore_mask(True, mask)
    editor.set_edit_context('mask')
    assert not editor._btn_undo.isEnabled()
    editor.clear_ignore_mask()
    assert editor._btn_undo.isEnabled()
    editor.undo_ignore_mask()
    assert editor._btn_redo.isEnabled()
    np.testing.assert_array_equal(editor.ignore_mask(), mask)
    editor.redo_ignore_mask()
    assert not editor.ignore_mask().any()
    editor.set_edit_context('roi')
    editor.set_edit_context('mask')
    assert editor._btn_undo.isEnabled()
    editor.close()


def test_v2_boundaries_are_cosmetic_paths_not_image_pixels(qt_app):
    from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsPixmapItem
    from app.ui.roi_mask_editor import ROIEditor
    from app.ui.golden_wizard.empty_mold_v2_dialog import set_region_background
    tool = ToolRegistry.make_default_tool('mold.protection_v2')
    tool.roi = ToolRoi({'x':10,'y':10,'w':180,'h':120})
    tool.params.values['cavities'] = [{'name':'K1','roi':{'x':40,'y':30,'w':80,'h':70}}]
    frame = np.full((160,220), 100, 'uint8')
    editor = ROIEditor()
    set_region_background(editor, frame, tool)
    paths = [item for item in editor._view.scene().items() if isinstance(item,QGraphicsPathItem) and item.zValue()==5]
    assert len(paths)==2
    assert all(item.pen().isCosmetic() and item.pen().widthF()==2 for item in paths)
    for scale in (.31, .1, 1., 2.):
        editor._view.resetTransform(); editor._view.scale(scale,scale)
        assert all(item.pen().isCosmetic() for item in paths)
    image_item = next(item for item in editor._view.scene().items() if isinstance(item,QGraphicsPixmapItem))
    image = image_item.pixmap().toImage()
    assert image.pixelColor(10,10).red()==100
    set_region_background(editor, frame, tool)
    paths = [item for item in editor._view.scene().items() if isinstance(item,QGraphicsPathItem) and item.zValue()==5]
    assert len(paths)==2
    editor.close()

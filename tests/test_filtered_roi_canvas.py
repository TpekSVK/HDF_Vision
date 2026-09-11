import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QImage
from app.ui.roi_mask_editor import LocatorROIEditor


def test_preview_pixels_preserve_editor_geometry_and_zoom():
    app = QApplication.instance() or QApplication([])
    editor = LocatorROIEditor()
    a = QPixmap(80,60); a.fill()
    editor.set_background(a)
    editor.set_roi((10,12,30,20))
    editor._view.scale(2,2)
    transform = editor._view.transform()
    rect = editor.roi()
    items = list(editor._view.scene().items())
    b = QPixmap(80,60); b.fill(0)
    editor.update_display_pixmap(b)
    assert editor.roi() == rect
    assert editor._view.transform() == transform
    assert editor._view.scene().items() == items
    editor.close()


def test_golden_checkbox_displays_actual_tool_roi_and_restores_pixels():
    from types import SimpleNamespace
    import numpy as np
    from PySide6.QtWidgets import QCheckBox, QLabel
    from app.ui.golden_wizard.golden_wizard import GoldenWizard
    from app.models.schema import Tool,ToolRoi,ToolParams,ToolThresholds
    from app.ui.filtered_roi import golden_filtered_roi,compose_filtered_roi
    app = QApplication.instance() or QApplication([])
    image = np.random.default_rng(4).integers(0,255,(24,32),dtype=np.uint8)
    original = image.copy()
    t = Tool(type='mse',name='MSE',order=1,roi=ToolRoi({'x':3,'y':4,'w':16,'h':12}),params=ToolParams({'preblur_sigma':1.0}),thresholds=ToolThresholds({}))
    shown = []
    def show(pm):
        q = pm.toImage().convertToFormat(QImage.Format_Grayscale8)
        shown.append(np.frombuffer(q.constBits(),np.uint8).reshape(q.height(),q.bytesPerLine())[:,:q.width()].copy())
    w = SimpleNamespace(current_img=image,_active_view_id='view_1',_selected_tool_row=0,
        _current_golden_image=lambda:image,_current_recipe_name=lambda:'test',
        recipes=SimpleNamespace(get_draft_tools=lambda *args:[t]),
        chk_filtered_roi=QCheckBox(),lbl_filtered_roi=QLabel(),
        view=SimpleNamespace(update_display_pixmap=show),roi_editor=SimpleNamespace(update_display_pixmap=lambda pm:None))
    w.chk_filtered_roi.setChecked(True)
    GoldenWizard._refresh_filtered_roi(w)
    np.testing.assert_array_equal(shown[-1],compose_filtered_roi(image,golden_filtered_roi(image,t)))
    assert 'Gaussian blur' in w.lbl_filtered_roi.text()
    w.chk_filtered_roi.setChecked(False)
    GoldenWizard._refresh_filtered_roi(w)
    np.testing.assert_array_equal(shown[-1],original)
    np.testing.assert_array_equal(w.current_img,original)

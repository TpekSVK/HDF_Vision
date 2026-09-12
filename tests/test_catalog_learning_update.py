import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from app.services.tool_registry import ToolRegistry
from app.services.tool_service import run_pipeline
from app.models.schema import RecipeV2,ToolRoi,ToolMask
from app.ui.golden_wizard.golden_wizard import GoldenWizard
from app.ui.golden_wizard.tool_catalog_dialog import ToolCatalogDialog,make_catalog_tool
from app.ui.view_utils import apply_view_rotation


@pytest.mark.parametrize('angle',[0,90,180,270])
def test_learning_crop_uses_rotated_view_coordinates(angle):
    raw=np.arange(24*32,dtype=np.uint8).reshape(24,32)
    golden=apply_view_rotation(raw,angle)
    calls=[]
    def capture(**kw):
        calls.append(kw)
        return apply_view_rotation(raw,kw['image_rotation_override'])
    w=SimpleNamespace(_capture_frame_for_golden=capture,_view_by_id=lambda _:SimpleNamespace(image_rotation=angle),
        _current_golden_image=lambda:golden,_warn=lambda text:pytest.fail(text))
    frame=GoldenWizard._capture_presence_learning_frame(w,'view_1')
    crop=GoldenWizard._presence_v2_crop(frame,(3,5,8,6))
    np.testing.assert_array_equal(crop,golden[5:11,3:11])
    assert calls[0]['view_id']=='view_1'


def test_learning_rejects_different_resolution_and_outside_roi():
    warnings=[]
    w=SimpleNamespace(_capture_frame_for_golden=lambda **kw:np.zeros((20,30),np.uint8),
        _view_by_id=lambda _:SimpleNamespace(image_rotation=0),_current_golden_image=lambda:np.zeros((40,60),np.uint8),_warn=warnings.append)
    assert GoldenWizard._capture_presence_learning_frame(w,'view_1') is None
    assert warnings
    assert GoldenWizard._presence_v2_crop(np.zeros((20,30),np.uint8),(28,4,5,5)) is None


def test_catalog_groups_and_legacy_implementations():
    app=QApplication.instance() or QApplication([])
    service=SimpleNamespace(list_tool_types=ToolRegistry.list_tool_types,get_tool_meta=ToolRegistry.get_tool_definition)
    d=ToolCatalogDialog(service)
    entries={e.type_id:e for e in d._entries}
    assert not {'ssd','absdiff'} & entries.keys()
    assert entries['mse'].category_label == 'Pokročilé'
    assert entries['ncc'].category_label == 'Pokročilé'
    assert entries['mold.protection_v1'].category_label == 'Špecializované'
    assert entries['light_presence'].category_label == 'Prednastavenia'
    assert ToolRegistry.create_tool('ssd') is not None
    assert ToolRegistry.create_tool('absdiff') is not None
    d.close()


def test_hole_preset_uses_general_presence_without_rewriting_legacy():
    new=make_catalog_tool(ToolRegistry,'light_presence')
    assert new.type=='presence_absence'
    assert new.params.values['polarity']=='bright'
    assert new.params.values['binary_threshold']==200
    assert ToolRegistry.make_default_tool('light_presence').type=='light_presence'


def test_difference_area_largest_component_and_binary_preview():
    golden=np.zeros((30,40),np.uint8);frame=golden.copy()
    frame[3:6,4:8]=100;frame[15:17,20:22]=100
    tool=ToolRegistry.make_default_tool('edge_change')
    tool.roi=ToolRoi({'x':0,'y':0,'w':40,'h':30})
    tool.params.values.update(blur_sigma=0,diff_threshold=25)
    tool.thresholds.values.update(edge_ratio_max=1.0,largest_change_max_px=10)
    recipe=RecipeV2(tools=[tool],logging_enabled=False)
    result=run_pipeline(golden,frame,recipe,capture_filtered_roi=True)
    report=result.per_tool[0]
    assert report.status=='nok'
    assert report.metrics['largest_change_px']==12
    assert report.metrics['changed_area_pct']==pytest.approx(100*16/1200,abs=.001)
    np.testing.assert_array_equal(report.filtered_roi['image'],(frame>25).astype(np.uint8)*255)
    tool.thresholds.values['largest_change_max_px']=0
    assert run_pipeline(golden,frame,RecipeV2(tools=[tool],logging_enabled=False)).per_tool[0].status=='ok'
    mask=np.zeros_like(frame);mask[3:6,4:8]=255;tool.ignore_mask=ToolMask(mask)
    report=run_pipeline(golden,frame,RecipeV2(tools=[tool],logging_enabled=False)).per_tool[0]
    assert report.metrics['largest_change_px']==4


def test_difference_percent_control_preserves_fractional_recipe_threshold():
    from app.ui.golden_wizard.form_widgets import _create_form_widget,_get_form_widget_value
    app=QApplication.instance() or QApplication([])
    spec=ToolRegistry.get_tool_schema('edge_change')['thresholds']['edge_ratio_max']
    widget=_create_form_widget(spec,None)
    assert widget.value()==5.0
    widget.setValue(2.5)
    assert _get_form_widget_value(widget,spec)==pytest.approx(.025)
    widget.close()

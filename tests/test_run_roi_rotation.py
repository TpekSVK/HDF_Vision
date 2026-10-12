"""RUN overlay toggles and refreshes preserve inspection-space orientation."""
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import numpy as np
import pytest

from app.services.view_images import apply_view_image_transform
from app.utils import overlay as overlay_utils
from app.ui.filtered_roi import compose_filtered_roi


def window_for(view, frame):
    from types import MethodType
    from app.ui.main_window import MainWindow
    w = SimpleNamespace()
    for name in ('_render_run_overlay_frame', '_on_run_overlay_controls_changed',
                 '_update_live_view', '_set_last_view_frame', '_get_last_frame_for_view',
                 '_view_storage_key', '_clone_frame'):
        setattr(w, name, MethodType(getattr(MainWindow, name), w))
    w._active_view_id = view.id
    w._last_trigger_frames = {view.id: frame.copy()}
    w._last_trigger_frame = frame.copy()
    w.live_enabled = False
    w.chk_show_roi = SimpleNamespace(checked=False)
    w.chk_show_roi.isChecked = lambda: w.chk_show_roi.checked
    w.cmb_roi_tool = SimpleNamespace(currentData=lambda: 'tool', count=lambda: 1, setEnabled=lambda value: None)
    w.chk_heatmap = SimpleNamespace(isChecked=lambda: False)
    w.cam = SimpleNamespace(is_trigger_capture_in_progress=lambda: False)
    w.live_view = object()
    w.shown = []
    w._show_gray_or_bgr = lambda label, img: w.shown.append(img.copy())
    w._resolve_active_capture_view = lambda **kwargs: view
    roi = overlay_utils.OverlayItem.from_rect((2, 3, 7, 5), color=(0, 255, 0), thickness=1)
    w._run_overlay_cache = {view.id: dict(frame=frame.copy(), view=view, roi_items={'tool': [roi]}, error_items=[])}
    return w, roi


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_roi_toggle_and_repeated_refresh_preserve_orientation(angle):
    view = SimpleNamespace(id='view_1', image_rotation=angle)
    raw = np.arange(24 * 32 * 3, dtype=np.uint8).reshape(24, 32, 3)
    inspected = apply_view_image_transform(raw, view, stage='inspection')
    w, roi = window_for(view, inspected)
    for checked in (False, True, False, True, False):
        w.chk_show_roi.checked = checked
        w._on_run_overlay_controls_changed()
        expected = overlay_utils.draw_overlay_items(inspected, [roi]) if checked else inspected
        np.testing.assert_array_equal(w.shown[-1], expected)
        for _ in range(2):
            w._update_live_view()
            np.testing.assert_array_equal(w.shown[-1], expected)
        np.testing.assert_array_equal(w._run_overlay_cache[view.id]['frame'], inspected)


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_raw_live_preview_still_rotates_once(angle):
    view = SimpleNamespace(id='view_1', image_rotation=angle)
    raw = np.arange(24 * 32, dtype=np.uint8).reshape(24, 32)
    w, _ = window_for(view, raw)
    w.live_enabled = True
    w.cam.last_frame = lambda **kwargs: raw
    w._update_live_view()
    np.testing.assert_array_equal(w.shown[-1], apply_view_image_transform(raw, view))


@pytest.mark.parametrize('angle', [0,90,180,270])
def test_filtered_roi_toggle_preserves_orientation_and_restores_original(angle):
    view = SimpleNamespace(id='view_1', image_rotation=angle)
    raw = np.arange(24*32*3,dtype=np.uint8).reshape(24,32,3)
    inspected = apply_view_image_transform(raw,view)
    w, _ = window_for(view,inspected)
    preview = dict(rect=(2,3,7,5),image=np.full((5,7),200,np.uint8),mask=np.ones((5,7),bool),label='Gaussian blur')
    w._run_overlay_cache[view.id]['filtered'] = {'tool':preview}
    w.chk_filtered_roi = SimpleNamespace(checked=True)
    w.chk_filtered_roi.isChecked = lambda: w.chk_filtered_roi.checked
    w.lbl_filtered_roi = SimpleNamespace(setVisible=lambda v:None,setText=lambda v:None)
    w._on_run_overlay_controls_changed()
    np.testing.assert_array_equal(w.shown[-1],compose_filtered_roi(inspected,preview))
    w.chk_filtered_roi.checked = False
    w._on_run_overlay_controls_changed()
    np.testing.assert_array_equal(w.shown[-1],inspected)


@pytest.mark.parametrize('aligned', [True, False])
@pytest.mark.parametrize('shape', ['polygon', 'ellipse', 'rect'])
def test_locator_roi_toggle_shows_search_and_template_without_changing_recipe(aligned, shape):
    from app.models.schema import ToolRoi
    from app.services.tool_service import ToolService
    from app.ui.main_window import MainWindow
    tool = ToolService().make_default_tool('locator.template_match')
    search = dict(shape='polygon', points=[[20,20],[180,20],[180,120],[120,120],[120,180],[20,180]])
    template = (dict(shape='polygon', points=[[50,50],[90,50],[70,90]]) if shape == 'polygon'
                else dict(shape=shape, x=50, y=50, w=40, h=40))
    tool.roi = ToolRoi.from_obj(search)
    tool.template_roi = ToolRoi.from_obj(template)
    tool.params.values['template_roi'] = tool.template_roi.to_dict()
    original = tool.to_dict()
    frame = np.zeros((220,220,3), np.uint8)
    view = SimpleNamespace(id='view_1', image_rotation=0)
    w, _ = window_for(view, frame)
    matrix = np.float32([[1,0,7],[0,1,-4]])
    report = SimpleNamespace(tool=tool, tool_id='tool', status='ok', filtered_roi=None)
    result = SimpleNamespace(per_tool=[report], context=SimpleNamespace(frame_is_aligned=aligned, T_total=matrix))
    MainWindow._cache_run_overlays(w, view.id, frame, view, result)
    items = w._run_overlay_cache[view.id]['roi_items']['tool']
    assert len(items) == 2
    assert items[0].label.endswith('HĽADANIE')
    assert items[1].label.endswith('ŠABLÓNA')
    assert items[0].color != items[1].color
    np.testing.assert_array_equal(items[0].points, search['points'])
    if shape == 'polygon':
        expected = np.array(template['points']) + (np.array([7,-4]) if not aligned else 0)
        assert items[1].kind == 'polygon'
        np.testing.assert_array_equal(items[1].points, expected)
    if shape == 'ellipse':
        assert items[1].kind == 'polygon' and len(items[1].points) == 64
    if shape == 'rect':
        assert items[1].kind == ('rect' if aligned else 'polygon')
    for checked in (False, True, False, True):
        w.chk_show_roi.checked = checked
        rendered = MainWindow._render_run_overlay_frame(w, view.id)
        expected = overlay_utils.draw_overlay_items(frame, items) if checked else frame
        np.testing.assert_array_equal(rendered, expected)
    w.cmb_roi_tool.currentData = lambda: None  # All tools show both regions too.
    np.testing.assert_array_equal(MainWindow._render_run_overlay_frame(w, view.id), expected)
    assert tool.to_dict() == original
    np.testing.assert_array_equal(w._run_overlay_cache[view.id]['frame'], frame)

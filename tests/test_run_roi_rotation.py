"""RUN overlay toggles and refreshes preserve inspection-space orientation."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import numpy as np
import pytest

from app.ui.view_utils import apply_view_image_transform
from app.utils import overlay as overlay_utils
from app.ui.filtered_roi import compose_filtered_roi


def window_for(view, frame):
    tree = ast.parse(Path('app/ui/main_window.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    names = {'_render_run_overlay_frame', '_on_run_overlay_controls_changed',
             '_update_live_view', '_set_last_view_frame', '_get_last_frame_for_view',
             '_view_storage_key', '_clone_frame'}
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    cls.bases = []; cls.decorator_list = []
    ns = dict(np=np, Any=Any, Mapping=Mapping, overlay_utils=overlay_utils,
              apply_view_image_transform=apply_view_image_transform, compose_filtered_roi=compose_filtered_roi)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), 'main_window.py', 'exec'), ns)
    w = ns['MainWindow']()
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

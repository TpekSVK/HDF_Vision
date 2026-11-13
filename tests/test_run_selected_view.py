"""OK preserves selection; NOK focuses a failed view and its matching image."""
from types import SimpleNamespace
from unittest.mock import Mock
import logging

import numpy as np
import pytest

from app.ui.main_window import MainWindow


def host_for(selected):
    frames = {}
    host = SimpleNamespace(
        _active_view_id=selected, _last_trigger_frame=None, _last_trigger_view_id=None,
        _nok_comparison=Mock(), view_strip=SimpleNamespace(set_status=Mock(), set_active=Mock()),
        _views_by_id={f'view_{i}': object() for i in range(1, 5)},
        _set_last_view_frame=lambda vid, frame: frames.__setitem__(vid, frame.copy()),
        _get_last_frame_for_view=frames.get, _clone_frame=lambda frame: frame.copy(),
        _update_sidebar=Mock(), _bump_runtime_stats=Mock(), _apply_run_status_style=Mock(),
        _update_operator_status_message=Mock(), _cache_run_overlays=Mock(),
        _render_run_overlay_frame=lambda vid: frames.get(vid), _reload_results_strip=Mock(),
        live_enabled=False, _update_live_view=Mock(), cam=SimpleNamespace(gst_start_count=lambda: 0),
        _logger=logging.getLogger('test.selected-view'), _on_view_selected_view=Mock(),
        _load_view_golden_array=lambda recipe, view: np.zeros((8, 8), np.uint8))
    host._finalize_run_trigger = MainWindow._finalize_run_trigger.__get__(host)
    def select_view(view_id):
        host._active_view_id = view_id
        host.view_strip.set_active(view_id)
    host._on_view_selected_view.side_effect = select_view
    return host, frames


def state_for(statuses):
    records = [dict(view_id=f'view_{i}', status=status, frame=np.full((8,8),i,np.uint8),
                    reports=[{'name':f'Tool {i}','status':status}], cycle_time_ms=i,
                    capture_time_ms=1, processing_time_ms=1, total_cycle_time_ms=i)
               for i, status in enumerate(statuses, 1)]
    return dict(records=records, logging_enabled=False, last_view_id=records[-1]['view_id'],
                last_preview_frame=records[-1]['frame'], status='nok' if 'nok' in statuses else 'ok',
                recipe_name='r', pending_overlays={}, relevant_reports=[], gst_starts_before=0)


@pytest.mark.parametrize('selected', [f'view_{i}' for i in range(1,5)])
@pytest.mark.parametrize('statuses', [('ok',)*4, ('nok','ok','ok','nok'), ('ok','ok','nok','ok')])
def test_four_view_cycle_focuses_only_nok_and_keeps_matching_preview(selected, statuses):
    host, frames = host_for(selected)
    state = state_for(statuses)
    MainWindow._display_inspection_result(host, state)
    failed = [f'view_{i}' for i, status in enumerate(statuses, 1) if status == 'nok']
    expected = selected if not failed or selected in failed else failed[0]
    assert host._active_view_id == expected
    if expected == selected:
        host._on_view_selected_view.assert_not_called()
    else:
        host._on_view_selected_view.assert_called_once_with(expected)
        host.view_strip.set_active.assert_called_once_with(expected)
    assert host.view_strip.set_status.call_count == 4
    assert host._bump_runtime_stats.call_count == 4
    assert list(frames) == [f'view_{i}' for i in range(1,5)]
    np.testing.assert_array_equal(host._last_trigger_frame, frames[expected])
    assert host._last_trigger_view_id == expected
    assert host._update_sidebar.call_args.kwargs == {'view_id': expected}
    host._apply_run_status_style.assert_called_once_with(state['status'])
    if statuses[int(expected[-1])-1] == 'nok':
        np.testing.assert_array_equal(host._nok_comparison.start.call_args.args[1], frames[expected])
    else:
        host._nok_comparison.start.assert_not_called()


@pytest.mark.parametrize('status', ['ok', 'nok'])
def test_external_result_for_another_view_switches_only_on_nok(status):
    host, frames = host_for('view_3')
    frames['view_3'] = np.full((8,8), 33, np.uint8)
    state = state_for([status])
    MainWindow._display_inspection_result(host,state)
    expected = 'view_1' if status == 'nok' else 'view_3'
    assert host._active_view_id == expected
    np.testing.assert_array_equal(host._last_trigger_frame, frames[expected])
    if status == 'ok':
        host._nok_comparison.start.assert_not_called()


def test_following_ok_result_does_not_switch_back_from_nok_view():
    host, frames = host_for('view_3')
    MainWindow._display_inspection_result(host, state_for(['nok']))
    assert host._active_view_id == 'view_1'
    host._on_view_selected_view.reset_mock()
    MainWindow._display_inspection_result(host, state_for(['ok'] * 4))
    assert host._active_view_id == 'view_1'
    host._on_view_selected_view.assert_not_called()
    np.testing.assert_array_equal(host._last_trigger_frame, frames['view_1'])


def test_selected_view_without_frame_does_not_use_last_processed_view():
    host, frames = host_for('view_3')
    state = state_for(['ok'])
    MainWindow._display_inspection_result(host,state)
    assert host._active_view_id == 'view_3'
    assert host._last_trigger_frame is None
    assert host._last_trigger_view_id is None


def test_empty_selected_view_never_displays_another_views_fallback():
    host = SimpleNamespace(_nok_comparison=None, live_enabled=False, _active_view_id='view_2',
        _last_trigger_view_id='view_1', _last_trigger_frame=np.ones((8,8),np.uint8),
        _get_last_frame_for_view=lambda vid:None, cam=SimpleNamespace(is_trigger_capture_in_progress=lambda:False),
        live_view=Mock(), _show_gray_or_bgr=Mock())
    MainWindow._update_live_view(host)
    host.live_view.clear.assert_called_once()
    host._show_gray_or_bgr.assert_not_called()

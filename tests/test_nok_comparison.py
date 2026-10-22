from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from PySide6.QtWidgets import QApplication
from app.ui.nok_comparison import NokComparison
from app.ui.main_window import MainWindow


def test_alternation_frozen_images_and_stop():
    app = QApplication.instance() or QApplication([])
    frames=[]; captions=[]
    c=NokComparison(frames.append,captions.append,lambda:True)
    golden=np.zeros((10,10),np.uint8);nok=np.ones((10,10),np.uint8)
    c.start(golden,nok)
    assert c.timer.interval()==1000 and c.timer.isActive()
    nok[:]=9
    assert np.all(frames[-1]==1) and captions[-1].startswith('NOK')
    c.advance();assert np.all(frames[-1]==0) and captions[-1].startswith('GOLDEN')
    c.advance();assert np.all(frames[-1]==1)
    c.stop();n=len(frames);c.advance()
    assert len(frames)==n and not c.timer.isActive() and captions[-1]==''
    c.start(golden,np.zeros((5,5),np.uint8));assert not c.active


def test_hidden_page_never_starts_timer():
    app=QApplication.instance() or QApplication([])
    c=NokComparison(Mock(),Mock(),lambda:False)
    c.start(np.zeros((2,2)),np.ones((2,2)))
    assert not c.active and not c.timer.isActive()


def test_live_refresh_does_not_replace_comparison():
    comparison=SimpleNamespace(active=True,render=Mock())
    MainWindow._update_live_view(SimpleNamespace(_nok_comparison=comparison))
    comparison.render.assert_called_once()


def test_accepted_trigger_stops_comparison_but_rejected_does_not():
    c=Mock()
    request=object()
    host=SimpleNamespace(_nok_comparison=c,
        inspection=SimpleNamespace(admit=lambda source:(None,'busy')),
        _show_trigger_rejection=Mock())
    MainWindow.manual_trigger(host)
    c.stop.assert_not_called()
    host.inspection.admit=lambda source:(request,None)
    host._pause_live_preview_for_trigger=lambda:False
    host._set_runtime_controls=Mock()
    host.current_recipe_name=lambda:'r';host.capture_mode='trigger';host._active_view_id='v'
    host.chk_filtered_roi=SimpleNamespace(isChecked=lambda:False)
    host.runtime=object();host.runtime_worker=SimpleNamespace(submit=lambda *args:True)
    MainWindow.manual_trigger(host)
    c.stop.assert_called_once()
    assert host._active_inspection_request is request


def test_nok_uses_matching_golden_and_existing_bbox_frame():
    c=Mock();frame=np.ones((8,8),np.uint8);golden=np.zeros_like(frame);bbox=frame+2
    host=SimpleNamespace(_nok_comparison=c,_set_last_view_frame=Mock(),
        view_strip=SimpleNamespace(set_status=Mock(),set_active=Mock()),_update_sidebar=Mock(),
        _finalize_run_trigger=Mock(),_views_by_id={'bad':object()},
        _on_view_selected_view=Mock(),_render_run_overlay_frame=lambda v:bbox,
        _load_view_golden_array=lambda recipe,view:golden)
    record=dict(view_id='bad',status='nok',frame=frame,reports=[],cycle_time_ms=1,
                capture_time_ms=1,processing_time_ms=1,total_cycle_time_ms=1)
    state=dict(records=[record],logging_enabled=True,last_view_id='other',status='nok',
               recipe_name='r',pending_overlays={'bad':object()})
    MainWindow._display_inspection_result(host,state)
    host._on_view_selected_view.assert_called_once_with('bad')
    assert c.start.call_args.args[0] is golden
    assert c.start.call_args.args[1] is bbox
    c.reset_mock();state['status']='ok'
    MainWindow._display_inspection_result(host,state)
    c.stop.assert_called_once();c.start.assert_not_called()


def test_hold_nok_freezes_even_during_live_refresh_and_next_nok_restarts():
    app=QApplication.instance() or QApplication([])
    frames=[];enabled=[]
    c=NokComparison(frames.append,lambda text:None,lambda:True)
    c.switchingChanged.connect(enabled.append)
    golden=np.zeros((8,8),np.uint8);nok=np.ones_like(golden)
    c.start(golden,nok)
    c.advance()
    assert np.all(frames[-1]==0)
    c.hold_nok()
    assert c.active and not c.timer.isActive() and enabled[-1] is False
    assert np.all(frames[-1]==1)
    c.advance();c.render()
    assert np.all(frames[-1]==1)
    c.stop()
    assert not c.active
    c.start(golden,nok*2)
    assert c.timer.isActive() and enabled[-1] is True
    assert np.all(frames[-1]==2)
    c.advance();assert np.all(frames[-1]==0)
    c.stop()

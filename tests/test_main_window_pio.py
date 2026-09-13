import logging
import re
from numbers import Integral
from pathlib import Path
from types import SimpleNamespace
import pytest


def harness():
    from types import MethodType
    from app.ui.main_window import MainWindow
    from app.services.inspection_controller import InspectionController
    window = SimpleNamespace()
    for name in ('_handle_pico_trigger','_handle_external_trigger','get_capture_mode',
                 '_enter_run_trigger_session','_capture_frame_for_trigger'):
        setattr(window, name, MethodType(getattr(MainWindow, name), window))
    window.inspection = InspectionController()
    window.inspection.prepare(lambda: None, lambda: None)
    window.trigger_rejected = SimpleNamespace(emit=lambda *args: None)
    window.mode='RUN';window._logger=logging.getLogger(__name__)
    window._consume_software_pico_request=lambda source:None
    return window


@pytest.mark.parametrize('mode,armed', [('master',True),('trigger',False)])
def test_external_event_reserves_master_only_in_master(mode,armed):
    w=harness();w.capture_mode=mode
    events=[];requests=[]
    def arm():requests.append('frame');return 'frame'
    w.cam=SimpleNamespace(arm_master_frame=arm)
    w.external_triggered=SimpleNamespace(emit=lambda *args:events.append(args))
    w._handle_pico_trigger('IN3')
    assert w.inspection.owns(events[0][1].pop('inspection_request'))
    assert events==[('pico',{'input_index':3,'spec':None,'frame_request':'frame' if armed else None})]
    assert bool(requests)==armed


def test_trigger_capture_uses_pico_transaction():
    w=harness();w.capture_mode='trigger';w._active_view_id='view_1'
    w._resolve_active_capture_view=lambda **kwargs:None
    w.cam=object();calls=[]
    w.pico=SimpleNamespace(prepare_trigger=lambda cam:calls.append(('prepare',cam)),
           capture_trigger=lambda cam,**kwargs:calls.append(('capture',cam)) or 'production')
    assert w._capture_frame_for_trigger(trigger_mode_label='test')=='production'
    assert calls==[('prepare',w.cam),('capture',w.cam)]

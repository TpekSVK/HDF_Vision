from collections import deque
from types import SimpleNamespace
import pytest
from app.services.camera_pio import PioCapture
from app.services.pico_service import PicoService
from app.utils.cu55_pio import cu55_pio_profile


@pytest.mark.parametrize('size,fps,period', [((2592,1944),30,33340),((1920,1080),60,16670),((1280,720),60,16670),((640,480),112,8930)])
def test_live_service_uses_profile_waits_for_good_frame_and_drains(monkeypatch,size,fps,period):
    service=PicoService();ring=deque(maxlen=5)
    camera=SimpleNamespace(stream=SimpleNamespace(_ring=ring,_clear_ring=ring.clear))
    camera.pio=PioCapture(camera)
    camera.prepare_pio_trigger=lambda pico:cu55_pio_profile(*size,fps,'Y8',1000)
    calls=[];good=object()
    def command(cmd):
        calls.append(cmd)
        if cmd=='STATUS':return True,'CAPABILITIES PIO_PAIR_V1 SESSION_V1 LIVE_PIO_V1'
        if cmd.startswith('LIVE ON'):
            assert camera.pio._live_preview
            camera.pio._publish_pio_frame(object(),5)
            camera.pio._publish_pio_frame(good,6)
        return True,'OK '+cmd
    monkeypatch.setattr(service,'_send_command',command)
    monkeypatch.setattr(camera.pio,'_pio_quiet',lambda **kwargs:None)
    service.set_live_preview(True,camera)
    assert calls==['STATUS',f'LIVE ON {period} 100']
    assert ring[-1] is good and camera.pio._live_discarded==1
    service.set_live_preview(False,camera)
    assert calls[-1]=='LIVE OFF' and not camera.pio._live_preview
    assert camera.pio._pio_ready is None


def test_old_firmware_refused_before_camera_prepare(monkeypatch):
    service=PicoService();monkeypatch.setattr(service,'_send_command',lambda cmd:(True,'CAPABILITIES PIO_PAIR_V1 SESSION_V1'))
    camera=SimpleNamespace(prepare_pio_trigger=lambda pico:pytest.fail('must not arm old firmware'))
    with pytest.raises(RuntimeError,match='4.2.1'):service.set_live_preview(True,camera)


def test_live_on_timeout_confirms_off_and_drains(monkeypatch):
    service=PicoService();events=[]
    pio=SimpleNamespace(begin_live_preview=lambda:events.append('begin'),end_live_preview=lambda:events.append('drain'),_pio_ready=object())
    camera=SimpleNamespace(pio=pio,prepare_pio_trigger=lambda pico:SimpleNamespace(period_us=33340,pulse_us=100))
    def command(cmd):
        events.append(cmd)
        if cmd=='STATUS':return True,'CAPABILITIES LIVE_PIO_V1'
        if cmd.startswith('LIVE ON'):raise TimeoutError('serial timeout')
        return True,'OK LIVE OFF'
    monkeypatch.setattr(service,'_send_command',command)
    with pytest.raises(TimeoutError):service.set_live_preview(True,camera)
    assert events[-2:]==['LIVE OFF','drain'] and pio._pio_ready is None


def test_rearming_live_restores_existing_manual_light(monkeypatch):
    service=PicoService();events=[]
    camera=SimpleNamespace(pio=SimpleNamespace(begin_live_preview=lambda:None,wait_live_preview=lambda:None))
    camera.prepare_pio_trigger=lambda pico:events.append('prepare') or SimpleNamespace(period_us=33340,pulse_us=100)
    def command(cmd):
        events.append(cmd)
        if cmd=='STATUS':return True,'CAPABILITIES LIVE_PIO_V1\nMANUAL_LIGHT ON'
        return True,'OK '+cmd
    monkeypatch.setattr(service,'_send_command',command)
    service.set_live_preview(True,camera)
    assert events==['STATUS','prepare','LIGHT ON','LIVE ON 33340 100']


def test_light_transition_holds_last_good_live_frame(monkeypatch):
    service=PicoService();ring=deque(maxlen=5)
    camera=SimpleNamespace(stream=SimpleNamespace(_ring=ring,_clear_ring=ring.clear))
    camera.pio=PioCapture(camera);camera.pio.begin_live_preview()
    camera.pio._publish_pio_frame(object(),1)
    good=object();camera.pio._publish_pio_frame(good,2)
    service._live_camera=camera;service._manual_light=False
    monkeypatch.setattr(service,'_send_command',lambda cmd:(True,'OK '+cmd))
    assert service.set_manual_light(True)
    camera.pio._publish_pio_frame(object(),3);camera.pio._publish_pio_frame(object(),4)
    assert ring[-1] is good
    next_good=object();camera.pio._publish_pio_frame(next_good,5)
    assert ring[-1] is next_good


def test_pause_stops_and_drains_live_before_disabling_inputs(monkeypatch):
    service = PicoService()
    service._pio_capable = True
    service._live_camera = camera = object()
    events = []
    monkeypatch.setattr(service, 'is_available', lambda: True)
    def stop(enabled, actual):
        assert enabled is False and actual is camera
        events.append('live_off_and_drained')
        service._live_camera = None
    monkeypatch.setattr(service, 'set_live_preview', stop)
    monkeypatch.setattr(service, 'set_session_mode', lambda mode: events.append(mode))
    service.quiesce()
    assert events == ['live_off_and_drained', 'IDLE']
    assert service._manual_light is False

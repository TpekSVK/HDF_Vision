import logging
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.camera_pio import CameraPioMixin
from app.utils.cu55_pio import cu55_pio_profile


@pytest.mark.parametrize('width,height,fps,base', [(2592,1944,30,33340),(1920,1080,60,16670),(1280,720,60,16670),(640,480,112,8930)])
@pytest.mark.parametrize('exposure', [500,1000,2000,5000,10000,15000,16000])
def test_validated_matrix(width,height,fps,base,exposure):
    p = cu55_pio_profile(width,height,fps,'Y8',exposure)
    assert p.period_us == (exposure + 100 if exposure > base else base)
    assert p.pulse_us == 100
    # LED is already on before production exposure begins and remains on
    # through the following frame period, including a 2 ms margin.
    led_start = -p.pre_us
    assert led_start <= p.period_us - exposure - 1000
    assert led_start + p.light_duration_us == 2 * p.period_us + 2000


@pytest.mark.parametrize('args', [(640,480,60,'Y8',1000),(1920,1080,60,'Y12',1000),(1920,1080,60,'Y8',50),(1920,1080,60,'Y8',17000)])
def test_unsupported_profile_rejected(args):
    with pytest.raises(ValueError):
        cu55_pio_profile(*args)


class Camera(CameraPioMixin):
    def __init__(self):
        self.width, self.height = 4, 3
        self._logger = logging.getLogger(__name__)
        self._init_pio_capture()


def burst(cam, frames):
    def fire(profile, count):
        # Frames can arrive before the serial ACK is read.
        for seq, frame in frames:
            cam._publish_pio_frame(frame, seq)
        return {'id': 12}
    return cam._pio_burst(SimpleNamespace(fire_pio=fire),
                          cu55_pio_profile(1920,1080,60,'Y8',1000), timeout_s=0.001)


def test_frames_before_ack_return_second_only():
    cam = Camera()
    prime, production = np.zeros((3,4)), np.ones((3,4))
    assert burst(cam, [(10,prime),(11,production)]) is production
    assert cam._pio_request is None


@pytest.mark.parametrize('sequences', [[], [1], [2,1], [1,1], [1,3], [1,2,3]])
def test_partial_out_of_order_extra_frames_never_return(sequences):
    cam = Camera()
    with pytest.raises(RuntimeError):
        burst(cam, [(seq,np.zeros((3,4))) for seq in sequences])
    assert cam._pio_request is None


def test_stream_change_cancels_reserved_capture():
    cam = Camera()
    def fire(profile, count):
        cam._invalidate_pio()
        return {'id': 1}
    with pytest.raises(RuntimeError, match='Stream'):
        cam._pio_burst(SimpleNamespace(fire_pio=fire), cu55_pio_profile(640,480,112,'Y8',1000), timeout_s=.001)


def test_wrong_dimensions_rejected():
    with pytest.raises(RuntimeError, match='rozmery'):
        burst(Camera(), [(1,np.zeros((4,3)))])


def test_confirmed_mode_is_not_written_again():
    cam = Camera()
    writes = []
    hid = SimpleNamespace(timeout_s=.25, get_stream_mode=lambda: 1,
                          set_stream_mode=writes.append)
    cam._ensure_hid = lambda: hid
    cam._pio_set_mode(1)
    assert writes == []


def test_unknown_mode_never_causes_blind_trigger_set():
    cam = Camera()
    writes = []
    def fail():
        raise TimeoutError('HID')
    cam._ensure_hid = lambda: SimpleNamespace(get_stream_mode=fail, set_stream_mode=writes.append)
    with pytest.raises(TimeoutError):
        cam._pio_set_mode(1)
    assert writes == []


def test_cu55_exposure_units_and_readback(monkeypatch):
    from app.services.camera_service import CameraService
    cam = CameraService()
    cam._camera_model = 'See3CAM_CU55_MH'
    calls = []
    monkeypatch.setattr(cam, '_run_v4l2_ctl', lambda arg: calls.append(arg) or True)
    monkeypatch.setattr(cam, '_read_cu55_exposure', lambda: 10)
    cam.set_manual_exposure_us(1000)
    assert calls == ['exposure_time_absolute=10']
    assert cam.exposure_us == 1000
    cam._pio_ready = cam._pio_signature()
    cam.set_manual_exposure_us(1000)
    assert calls == ['exposure_time_absolute=10']
    assert cam._pio_ready is not None
    monkeypatch.setattr(cam, '_read_cu55_exposure', lambda: 9)
    with pytest.raises(RuntimeError, match='readback'):
        cam.set_manual_exposure_us(2000)
    assert cam._pio_ready is None


def test_profiles_match_recorded_lab_handoff():
    import json
    from pathlib import Path
    rows=json.loads(Path('docs/cu55_pio_validated_profiles.json').read_text())['profiles']
    assert len(rows)==28
    for row in rows:
        p=cu55_pio_profile(row['width'],row['height'],row['stream_fps'],row['pixel_format'],row['exposure_us'])
        assert (p.period_us,p.pulse_us,p.pre_us,p.light_duration_us)==(row['period_us'],row['pulse_us'],row['light_pre_relative_first_us'],row['light_duration_us'])


def test_unsolicited_frame_invalidates_ready_session():
    cam=Camera();cam._pio_ready=('ready',)
    cam._publish_pio_frame(np.zeros((3,4)),7)
    assert cam._pio_ready is None
    with pytest.raises(RuntimeError,match='mimo PIO'):
        burst(cam,[(8,np.zeros((3,4))),(9,np.zeros((3,4)))])


def test_settling_can_discard_startup_gap_but_never_returns_image():
    cam=Camera()
    def fire(profile,count):
        cam._publish_pio_frame(np.zeros((3,4)),0)
        cam._publish_pio_frame(np.ones((3,4)),2)
        return {'id':1}
    assert cam._pio_burst(SimpleNamespace(fire_pio=fire),cu55_pio_profile(1280,720,60,'Y8',1000),settling=True) is None
    with pytest.raises(RuntimeError,match='poradie'):
        burst(cam,[(3,np.zeros((3,4))),(5,np.zeros((3,4)))])


@pytest.mark.parametrize('size,fps,reopens', [((1920,1080),60,0),((1280,720),60,1),((640,480),112,1),((2592,1944),30,1)])
def test_preparation_uses_resolution_specific_stream_lifecycle(size,fps,reopens):
    cam=Camera()
    cam.width,cam.height=size;cam.fps=fps;cam.pixel_format='Y8'
    cam.exposure_us=1000;cam.gain_db=0;cam.device='/dev/video0'
    cam._paused_external=False;cam._pipeline=object();cam._mode='gst'
    events=[];mode=[0]
    def set_mode(value):mode[0]=value;events.append(('mode',value))
    hid=SimpleNamespace(get_stream_mode=lambda:mode[0],set_stream_mode=set_mode)
    cam._ensure_hid=lambda:hid
    cam.is_pipeline_open=lambda:True
    cam.start=lambda **kwargs:events.append(('start',kwargs['caller']))
    def stop(**kwargs):
        cam._invalidate_pio();cam._pipeline=object();events.append(('stop',kwargs['caller']))
    cam.stop=stop
    cam.set_manual_exposure_us=lambda value:None
    cam._pio_quiet=lambda:None
    cam._pio_wait_master_frames=lambda:events.append(('master_frames',20))
    cam._pio_burst=lambda *args,**kwargs:events.append(('burst',kwargs.get('count',2),kwargs.get('settling',False)))
    pico=SimpleNamespace(require_pio=lambda:None,set_session_mode=lambda value:events.append(('session',value)))
    cam.prepare_pio_trigger(pico)
    assert len([e for e in events if e[0]=='stop'])==reopens
    assert ('master_frames',20) in events
    assert events.index(('master_frames',20)) < events.index(('mode',1))
    assert ('burst',1,False) in events and ('burst',2,True) in events
    events.clear()
    cam.prepare_pio_trigger(pico)
    assert events==[('session','TRIGGER')]


def test_pipeline_error_after_two_frames_still_rejects_capture():
    cam=Camera()
    def fire(profile,count):
        cam._publish_pio_frame(np.zeros((3,4)),0)
        cam._publish_pio_frame(np.zeros((3,4)),1)
        cam._pio_pipeline_error='GStreamer failed'
        return {'id':1}
    with pytest.raises(RuntimeError,match='GStreamer'):
        cam._pio_burst(SimpleNamespace(fire_pio=fire),cu55_pio_profile(1280,720,60,'Y8',1000))

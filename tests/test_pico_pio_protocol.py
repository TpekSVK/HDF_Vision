import json
import threading
import pytest
from app.utils.cu55_pio import cu55_pio_profile
from app.services.pico_service import PicoService
from test_pico_service import connected_service, wait_for


def test_stale_ack_cannot_complete_new_pair(connected_service):
    p, dev = connected_service
    p._pio_capable = True
    output = []
    thread = threading.Thread(target=lambda: output.append(p.fire_pio(cu55_pio_profile(640,480,112,'Y8',1000))))
    thread.start()
    assert wait_for(lambda: bool(dev.writes))
    ident = int(dev.writes[0].decode().split()[2])
    dev.emit('PIO RESULT '+json.dumps({'id':ident-1,'count':2}), f'OK PIO {ident-1}')
    dev.emit('PIO RESULT '+json.dumps({'id':ident,'count':2}))
    assert not output
    dev.emit(f'OK PIO {ident}')
    thread.join(1)
    assert output == [{'id':ident,'count':2}]


@pytest.mark.parametrize('mode,line,expected', [('TRIGGER','REQUEST IN3',['IN3']),('IDLE','REQUEST IN3',[]),('MASTER','REQUEST IN3',[]),('TRIGGER','CAPTURE IN3',[]),('MASTER','CAPTURE IN3',['IN3'])])
def test_events_match_session(connected_service,mode,line,expected):
    p,dev = connected_service
    p._session_mode = mode
    values=[]
    p.register_trigger_callback(values.append)
    dev.emit(line)
    if expected:
        assert wait_for(lambda: values == expected)
    else:
        import time
        time.sleep(.05)
        assert values == []


def test_legacy_firmware_cannot_fire_trigger(monkeypatch):
    p=PicoService()
    monkeypatch.setattr(p,'_send_command',lambda command: (True,'FIRMWARE 3.3\nEND'))
    with pytest.raises(RuntimeError,match='4.0'):
        p.require_pio()


def test_cached_mode_is_cleared_on_close():
    p=PicoService();p._session_mode='TRIGGER';p._pio_capable=True
    p.close()
    assert p._session_mode is None and p._pio_capable is None


def test_firmware_reboot_invalidates_cached_session(connected_service):
    p,dev=connected_service
    p._pio_capable=True;p._session_mode='TRIGGER'
    dev.emit('READY pico_hdf_controller 4.0-pio-pair LED=GP17 TRIG=GP16')
    assert wait_for(lambda:p._session_mode is None and p._pio_capable is None)


def test_quiesce_disables_requests_without_reconnecting(monkeypatch):
    p=PicoService();modes=[]
    monkeypatch.setattr(p,'set_session_mode',modes.append)
    p.quiesce()
    assert not modes
    monkeypatch.setattr(p,'is_available',lambda:True)
    p._pio_capable=True
    p.quiesce()
    assert modes==['IDLE']

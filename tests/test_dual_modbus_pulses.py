import threading
import time
from app.services.modbus_service import ModbusService


def wait_for(predicate):
    end=time.monotonic()+2
    while not predicate() and time.monotonic()<end: time.sleep(.005)
    assert predicate()


def test_old_pulse_cannot_switch_off_a_new_pulse(tmp_path, monkeypatch):
    service=ModbusService(tmp_path/'modbus.json')
    writes=[]
    monkeypatch.setattr(service,'_write_coil',lambda address,value,cfg: writes.append((address,value)) or True)
    service.pulse_coil(0,pulse_ms=80)
    wait_for(lambda: len(writes)==1)
    service.pulse_coil(0,pulse_ms=300)
    wait_for(lambda: len(writes)==2)
    time.sleep(.12)
    assert writes==[(0,True),(0,True)]
    wait_for(lambda: len(writes)==3)
    assert writes[-1]==(0,False)
    service.close()


def test_result_cancels_delayed_opposite_pulse_and_optional_heartbeat(tmp_path, monkeypatch):
    service=ModbusService(tmp_path/'modbus.json')
    service._config.enabled=True
    service._config.output_groups={'group_1':dict(ok=0,nok=1,heartbeat=-1)}
    writes=[]
    monkeypatch.setattr(service,'_write_coil',lambda address,value,cfg: writes.append((address,value)) or True)
    service.pulse_coil(0,pulse_ms=10,delay_ms=80)
    service.signal_result('nok',group_id='group_1',pulse_ms=10)
    service.emit_heartbeat(group_id='group_1')
    wait_for(lambda: (1,False) in writes)
    time.sleep(.1)
    assert (0,True) not in writes and not service.last_error
    service.close()

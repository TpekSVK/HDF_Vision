"""Execute the firmware command handlers with simulated pins and PIO FIFO."""
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.fixture
def fw(monkeypatch):
    class Pin:
        OUT=1; IN=0; PULL_UP=2
        def __init__(self,pin,*args,**kwargs):
            self.pin=pin; self.state=0
        def value(self,value=None):
            if value is not None: self.state=value
            return self.state
        def init(self,*args,value=0): self.state=value
    class SM:
        instances=[]
        def __init__(self,*args,**kwargs):
            self.words=[];self.enabled=False;self.instances.append(self)
        def put(self,word):self.words.append(word)
        def active(self,value):self.enabled=bool(value)
    clock=SimpleNamespace(now=1000000)
    def advance(us):clock.now+=us
    modules={
        'machine':SimpleNamespace(Pin=Pin),
        'select':SimpleNamespace(POLLIN=1,poll=lambda:SimpleNamespace(register=lambda *args:None)),
        'time':SimpleNamespace(ticks_us=lambda:clock.now,ticks_ms=lambda:clock.now//1000,
               ticks_diff=lambda a,b:a-b,ticks_add=lambda a,b:a+b,
               sleep_us=advance,sleep_ms=lambda ms:advance(ms*1000)),
        'rp2':SimpleNamespace(PIO=SimpleNamespace(OUT_LOW=0,SHIFT_RIGHT=0,JOIN_TX=0),
              asm_pio=lambda **kwargs:lambda f:f,StateMachine=SM),
    }
    for name,module in modules.items():monkeypatch.setitem(sys.modules,name,module)
    source=Path('firmware/pico/main_v4.0.py').read_text()
    tree=ast.parse(source);tree.body.pop()  # do not enter the firmware main loop
    ns={};exec(compile(tree,'firmware/pico/main_v4.0.py','exec'),ns)
    ns['config']=ns['DEFAULT_CONFIG'].copy()
    ns['SM']=SM
    return ns


def test_boot_idle_and_external_request_never_fires_pulses(fw,capsys):
    assert fw['session_mode']=='IDLE'
    assert fw['fire_view']('V1','IN1')=='BUSY SESSION_NOT_READY'
    assert fw['handle_command']('SESSION TRIGGER')==['OK SESSION TRIGGER']
    assert fw['fire_view']('V1','IN1')=='OK REQUESTED IN1'
    assert capsys.readouterr().out=='REQUEST IN1\n'
    assert not fw['SM'].instances
    assert fw['cam_trig'].state==0 and fw['led'].state==0


def test_pio_words_encode_exact_edges_and_cleanup(fw):
    result=fw['handle_command']('PIO FIRE 7 2 16670 100 -14670 20670 1 50000')
    assert result[-1]=='OK PIO 7'
    data=json.loads(result[0][11:]);assert data['count']==2
    states=[];t=0
    for word in fw['SM'].instances[-1].words:
        states.append((t,word&3));t+=(word>>2)+4
    rises=[t for i,(t,s) in enumerate(states) if s&1 and (i==0 or not states[i-1][1]&1)]
    assert rises==[2000,18670]
    assert [(t,s) for t,s in states if s==3]==[(18670,3)]
    assert not fw['SM'].instances[-1].enabled
    assert fw['cam_trig'].state==fw['led'].state==0
    assert fw['busy'] is False


def test_master_session_disallows_pio_and_preserves_capture(fw,capsys):
    fw['handle_command']('SESSION MASTER')
    assert fw['handle_command']('PIO FIRE 1 2 16670 100 -14670 20670 1 50000')==['ERR PIO SESSION_MASTER']
    assert fw['fire_view']('V1','IN2').startswith('OK FIRED V1 MODE=MASTER')
    assert capsys.readouterr().out=='CAPTURE IN2\n'
    assert not fw['SM'].instances


def test_transition_turns_off_manual_light(fw):
    fw['handle_command']('SESSION MASTER');fw['handle_command']('LIGHT ON')
    assert fw['led'].state==1
    fw['handle_command']('SESSION TRIGGER')
    assert fw['led'].state==0
    assert fw['handle_command']('LIGHT ON')==['ERR LIGHT SESSION_NOT_MASTER']


def test_rejected_pio_command_does_not_change_master_light(fw):
    fw['handle_command']('SESSION MASTER');fw['handle_command']('LIGHT ON')
    assert fw['handle_command']('PIO FIRE 1 2 16670 100 -14670 20670 1 50000')==['ERR PIO SESSION_MASTER']
    assert fw['led'].state==1
    assert fw['busy'] is False

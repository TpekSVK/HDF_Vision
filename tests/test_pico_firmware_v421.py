"""Exercise real command handlers and cycle-count the continuous PIO program."""
from pathlib import Path
import pytest
from test_pico_firmware_v4 import fw

pytestmark = pytest.mark.parametrize('fw', ['4.2.1'], indirect=True)


@pytest.mark.parametrize('period', [33340, 16670, 8930, 16100])
def test_live_pio_edges_match_profile_and_stop_preserves_light(fw, period):
    cmd = fw['handle_command']
    assert cmd(f'LIVE ON {period} 100') == ['ERR LIVE SESSION']
    cmd('SESSION TRIGGER');cmd('LIGHT ON')
    assert cmd(f'LIVE ON {period} 100') == [f'OK LIVE ON {period} 100']
    sm = fw['SM'].instances[-1]
    assert sm.enabled and sm.words == [(100-4) | ((period-100-4)<<16)]
    assert cmd('PIO FIRE 1 2 33340 100 0 0 0 50000') == ['BUSY LIVE']
    assert cmd('LIVE OFF') == ['OK LIVE OFF']
    assert not sm.enabled and not fw['live_preview']
    assert fw['cam_trig'].state == 0 and fw['led'].state == 1

    # Interpret the actual PIO instruction stream, including conditional jump
    # cycle costs, rather than restating the encoded counter arithmetic.
    program=[]
    for op in ('pull','mov','out','label','set','jmp'):
        fw[op] = lambda *args, op=op: program.append((op,args))
    for symbol in ('block','isr','osr','pins','y','y_dec'):fw[symbol]=symbol
    fw['live_trigger_program']()
    labels={args[0]:i for i,(op,args) in enumerate(program) if op=='label'}
    regs={'osr':0,'isr':0,'y':0};pc=t=0;edges=[]
    while len(edges)<8:
        op,args=program[pc];pc+=1
        if op=='label':continue
        if op=='pull':regs['osr']=sm.words[0]
        elif op=='mov':regs[args[0]]=regs[args[1]]
        elif op=='out':
            regs[args[0]]=regs['osr'] & ((1<<args[1])-1);regs['osr'] >>= args[1]
        elif op=='set':edges.append((t,args[1]))
        elif op=='jmp':
            if len(args)==1:pc=labels[args[0]]
            else:
                before=regs['y'];regs['y']=(before-1)&0xffffffff
                if before:pc=labels[args[1]]
        t+=1
    rises=[t for t,v in edges if v==1];falls=[t for t,v in edges if v==0]
    assert all(b-a==period for a,b in zip(rises,rises[1:]))
    assert all(b-a==100 for a,b in zip(rises,falls))


def test_live_invalid_reconfigure_keeps_current_generator(fw):
    cmd=fw['handle_command'];cmd('SESSION TRIGGER');cmd('LIVE ON 33340 100')
    sm=fw['live_sm']
    assert cmd('LIVE ON 100 100') == ['ERR LIVE RANGE']
    assert cmd('LIVE ON nonsense 100')[0].startswith('ERR LIVE ')
    assert fw['live_sm'] is sm and sm.enabled
    cmd('LIVE ON 33340 100');assert fw['live_sm'] is sm
    cmd('SESSION IDLE');assert not sm.enabled and fw['led'].state==0
    assert fw['cam_trig'].state==0 and not fw['live_preview']


def test_legacy_live_defaults_and_deployment(fw):
    cmd=fw['handle_command'];cmd('SESSION TRIGGER')
    assert cmd('LIVE ON') == ['OK LIVE ON']
    assert fw['live_period_us']==33340
    assert 'LIVE_PIO_V1' in ' '.join(cmd('STATUS'))
    assert Path('firmware/pico/main.py').read_bytes()==Path('firmware/pico/main_v4.2.1.py').read_bytes()

"""Exercise concurrent firmware schedules with the real command parser."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def fw43(monkeypatch):
    class Pin:
        OUT, IN, PULL_UP = 1, 0, 2
        def __init__(self, number, *args, value=0):
            self.number, self.state = number, value
        def value(self, value=None):
            if value is not None: self.state = value
            return self.state
        def init(self, *args, value=0): self.state = value
    class SM:
        def __init__(self, number, *args, **kwargs):
            self.number, self.words, self.enabled = number, [], False
        def active(self, enabled): self.enabled = bool(enabled)
        def put(self, word): self.words.append(word)
        def tx_fifo(self): return 0
    now = [0]
    monkeypatch.setitem(sys.modules, 'machine', SimpleNamespace(Pin=Pin))
    monkeypatch.setitem(sys.modules, 'rp2', SimpleNamespace(
        PIO=SimpleNamespace(OUT_LOW=0, SHIFT_RIGHT=0, JOIN_TX=0),
        asm_pio=lambda **kwargs: lambda fn: fn, StateMachine=SM))
    monkeypatch.setitem(sys.modules, 'time', SimpleNamespace(
        ticks_us=lambda: now[0], ticks_ms=lambda: now[0] // 1000, ticks_diff=lambda a, b: a-b))
    tree = ast.parse(Path('firmware/pico/main_v4.3.1.py').read_text())
    tree.body.pop()
    ns = {}
    exec(compile(tree, 'firmware/pico/main_v4.3.1.py', 'exec'), ns)
    ns['channels'] = [ns['Channel'](i, {}) for i in (1, 2)]
    ns['inputs'], ns['now'] = {}, now
    return ns


def test_both_pio_trains_start_without_waiting_for_other_channel(fw43, capsys):
    handle = fw43['handle_command']
    for i in (1, 2):
        handle(f'CH {i} SESSION TRIGGER')
        handle(f'CH {i} PIO FIRE {i} 2 16670 100 -14670 20670 1 50000')
    one, two = fw43['channels']
    assert one.job and two.job and one.sm.enabled and two.sm.enabled
    assert one.sm.number == 0 and two.sm.number == 1
    assert one.trigger.number == 16 and two.trigger.number == 18
    assert 'PIO RESULT' not in capsys.readouterr().out
    fw43['now'][0] = 100000
    one.tick(); two.tick()
    lines = capsys.readouterr().out.splitlines()
    for i in (1, 2):
        result = next(line for line in lines if line.startswith(f'CH {i} PIO RESULT '))
        data = json.loads(result.split('PIO RESULT ', 1)[1])
        assert data['id'] == i and data['channel'] == i
        assert f'CH {i} OK PIO {i}' in lines
    assert one.trigger.state == two.trigger.state == one.light.state == two.light.state == 0


def test_master_light_window_does_not_block_peer_or_emit_before_capture(fw43, capsys):
    handle = fw43['handle_command']
    for i in (1, 2):
        handle(f'CH {i} SESSION MASTER')
        handle(f'CH {i} FIRE V1')
    one, two = fw43['channels']
    assert one.job and two.job
    one.tick(); two.tick()
    assert one.light.state == two.light.state == 1
    assert 'CAPTURE' not in capsys.readouterr().out
    fw43['now'][0] = 5000
    one.tick(); two.tick()
    assert capsys.readouterr().out.splitlines() == ['CH 1 CAPTURE V1', 'CH 2 CAPTURE V1']
    fw43['now'][0] = 200000
    one.tick(); two.tick()
    assert capsys.readouterr().out.splitlines() == ['CH 1 OK FIRED V1', 'CH 2 OK FIRED V1']
    assert one.job is None and two.job is None


def test_live_and_manual_light_are_channel_local(fw43, capsys):
    handle = fw43['handle_command']
    for i in (1, 2): handle(f'CH {i} SESSION TRIGGER')
    handle('CH 1 LIVE ON 16670 100')
    handle('CH 1 LIGHT ON')
    one, two = fw43['channels']
    handle('CH 2 SESSION IDLE')
    assert one.live and one.sm.enabled and one.light.state == 1
    assert two.light.state == two.trigger.state == 0
    handle('CH 1 LIVE OFF')
    assert not one.live and one.light.state == 1
    handle('CH 1 SESSION IDLE')
    assert one.light.state == one.trigger.state == 0


def test_busy_channel_does_not_accept_second_capture(fw43, capsys):
    handle = fw43['handle_command']
    handle('CH 1 SESSION MASTER'); handle('CH 1 FIRE V1'); handle('CH 1 FIRE V2')
    assert capsys.readouterr().out.splitlines()[-1] == 'CH 1 BUSY V2'
    assert fw43['channels'][0].job['source'] == 'V1'


def test_channel_master_input_waits_for_host_before_illumination(fw43, capsys):
    channel = fw43['channels'][0]
    channel.tagged, channel.mode = True, 'MASTER'
    assert channel.request_input('V1', 'IN1') == []
    assert capsys.readouterr().out == 'CH 1 REQUEST IN1\n'
    assert channel.job is None and channel.light.state == 0
    channel.fire('V1', 'IN1')
    channel.tick()
    assert channel.job is not None and channel.light.state == 1


def test_host_can_arm_in_idle_without_enabling_physical_inputs(fw43):
    channel = fw43['channels'][0]
    assert channel.request_input('V1', 'IN1') == ['ERR SESSION_IDLE']
    assert channel.fire_pio('PIO FIRE 1 1 16670 100 -14670 20670 1 50000'.split()) == []
    assert channel.job is not None and channel.mode == 'IDLE'


def test_config_migrates_old_channel_one_and_keeps_both_separate(fw43, tmp_path):
    fw43['CONFIG_FILE'] = str(tmp_path / 'pico.json')
    Path(fw43['CONFIG_FILE']).write_text(json.dumps({'V1_PULSE': 123, 'IN1': 'V1'}))
    loaded = fw43['load_config']()
    assert loaded['1']['V1_PULSE'] == 123 and '2' not in loaded
    fw43['channels'][0].config['V1_PULSE'] = 123
    fw43['channels'][1].config['V1_PULSE'] = 456
    assert fw43['save_config']() == 'OK SAVED'
    loaded = fw43['load_config']()
    assert loaded['1']['V1_PULSE'] == 123 and loaded['2']['V1_PULSE'] == 456


def test_deployed_firmware_is_current_dual_channel_release(fw43):
    assert fw43['FIRMWARE_VERSION'] == '4.3.1-dual-channel'
    assert Path('firmware/pico/main.py').read_bytes() == Path('firmware/pico/main_v4.3.1.py').read_bytes()

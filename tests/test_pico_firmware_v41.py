from pathlib import Path
import pytest
from test_pico_firmware_v4 import fw

pytestmark = pytest.mark.parametrize('fw', ['4.1'], indirect=True)


def test_trigger_manual_light_then_pio_clears_override(fw):
    command = fw['handle_command']
    command('SESSION TRIGGER')
    assert command('LIGHT ON') == ['OK LIGHT ON']
    assert fw['led'].state == 1
    assert command('PIO FIRE 7 2 16670 100 -14670 20670 1 50000')[-1] == 'OK PIO 7'
    assert command('LIGHT STATUS') == ['MANUAL_LIGHT OFF']
    fw['apply_light_idle_state']()
    assert fw['led'].state == fw['cam_trig'].state == 0
    assert not fw['busy']


@pytest.mark.parametrize('mode', ['MASTER', 'TRIGGER'])
def test_capture_request_cancels_manual_light(fw, mode):
    command=fw['handle_command']
    command('SESSION '+mode); command('LIGHT ON')
    assert fw['fire_view']('V1','IN1').startswith('OK ')
    assert command('LIGHT STATUS') == ['MANUAL_LIGHT OFF']
    assert fw['led'].state == 0


def test_idle_invalid_busy_and_session_transition(fw):
    command=fw['handle_command']
    assert command('LIGHT ON') == ['ERR LIGHT SESSION_NOT_READY']
    command('SESSION TRIGGER'); command('LIGHT ON')
    assert command('PIO FIRE bad') == ['ERR PIO ARGUMENTS']
    assert fw['manual_light_enabled']
    fw['busy']=True
    assert command('LIGHT ON') == ['BUSY LIGHT']
    assert command('PIO FIRE 1 2 16670 100 0 20670 1 50000') == ['BUSY PIO']
    fw['busy']=False
    command('SESSION MASTER')
    assert command('LIGHT STATUS') == ['MANUAL_LIGHT OFF']


def test_pio_failure_does_not_restore_override(fw):
    command=fw['handle_command']
    command('SESSION TRIGGER');command('LIGHT ON')
    def fail(*args, **kwargs):raise RuntimeError('PIO failed')
    fw['rp2'].StateMachine=fail
    assert command('PIO FIRE 1 2 16670 100 0 20670 1 50000') == ['ERR PIO PIO failed']
    assert command('LIGHT STATUS') == ['MANUAL_LIGHT OFF']
    assert fw['led'].state == fw['cam_trig'].state == 0
    assert not fw['busy']


def test_deployment_copy_matches_version(fw):
    assert fw['FIRMWARE_VERSION'] == '4.1-pio-pair'
    assert Path('firmware/pico/main.py').read_bytes() == Path('firmware/pico/main_v4.1.py').read_bytes()

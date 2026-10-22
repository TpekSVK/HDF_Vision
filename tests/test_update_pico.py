"""Updater decisions and failure recovery, without touching USB hardware."""
from types import SimpleNamespace
from pathlib import Path
import json

import pytest

from tools import update_pico as updater


def source(value):
    return f'FIRMWARE_NAME = "pico_hdf_controller"\nFIRMWARE_VERSION = "{value}"\n'.encode()


class FakePico:
    def __init__(self, current='3.3-master-production-capture', config=b'{"V1_PULSE": 150}'):
        self.current = current
        self.files = {'main.py': source(current)}
        if config is not None:
            self.files[updater.CONFIG] = config
        self.events = []
        self.fail_install = False
        self.fail_boot = False

    def status(self):
        self.events.append('status')
        return self.current, f'FIRMWARE pico_hdf_controller {self.current}\nSESSION_MODE IDLE\nEND'

    def enter_raw(self):
        self.events.append('raw')

    def read_file(self, name, optional=False):
        return self.files.get(name) if optional else self.files[name]

    def install(self, data):
        self.events.append('install')
        self.files['main.py'] = data
        if self.fail_install:
            self.fail_install = False
            raise RuntimeError('simulated interrupted upload')

    def reboot(self):
        self.events.append('reboot')
        if self.fail_boot:
            self.fail_boot = False
            raise TimeoutError('simulated boot failure')
        self.current = updater.firmware_info(self.files['main.py'])


def run(pico, tmp_path, check=False):
    updater.update(pico, source('4.1-pio-pair'), '4.1-pio-pair', tmp_path / 'backups',
                   SimpleNamespace(device='/dev/ttyACM0', serial_number='TEST'), check)


@pytest.mark.parametrize('current', ['4.1-pio-pair', '4.2-pio-pair', '4.10-pio-pair', '5.0'])
def test_equal_or_newer_never_interrupts_or_writes(current, tmp_path):
    pico = FakePico(current)
    run(pico, tmp_path)
    assert pico.events == ['status']
    assert not (tmp_path / 'backups').exists()


def test_check_only_does_not_interrupt_old_firmware(tmp_path):
    pico = FakePico()
    run(pico, tmp_path, check=True)
    assert pico.events == ['status']
    assert not (tmp_path / 'backups').exists()


@pytest.mark.parametrize('config', [None, b'{"V1_PULSE":150}'])
def test_upgrade_preserves_configuration_and_backups(tmp_path, config):
    pico = FakePico(config=config)
    original = pico.files['main.py']
    run(pico, tmp_path)
    assert pico.current == '4.1-pio-pair'
    assert pico.files.get(updater.CONFIG) == config
    backup, = (tmp_path / 'backups').iterdir()
    assert (backup / 'main.py').read_bytes() == original
    manifest = json.loads((backup / 'manifest.json').read_text())
    assert manifest['config_present'] == (config is not None)
    if config is not None:
        assert (backup / updater.CONFIG).read_bytes() == config
    assert pico.events == ['status', 'raw', 'install', 'reboot', 'status']


@pytest.mark.parametrize('failure', ['fail_install', 'fail_boot'])
def test_failed_update_rolls_back_and_reports_failure(tmp_path, failure):
    pico = FakePico()
    original = pico.files['main.py']
    setattr(pico, failure, True)
    with pytest.raises((RuntimeError, TimeoutError)):
        run(pico, tmp_path)
    assert pico.files['main.py'] == original
    assert pico.current == '3.3-master-production-capture'
    assert pico.events.count('install') == 2


def test_backup_failure_never_installs(tmp_path, monkeypatch):
    pico = FakePico()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(updater, 'backup_files', fail)
    with pytest.raises(OSError):
        run(pico, tmp_path)
    assert 'install' not in pico.events
    assert pico.events[-2:] == ['reboot', 'status']


def test_ram_version_mismatch_refused(tmp_path):
    pico = FakePico()
    pico.files['main.py'] = source('3.2')
    with pytest.raises(RuntimeError, match='flash'):
        run(pico, tmp_path)
    assert 'install' not in pico.events


def test_port_selection_requires_explicit_choice_for_multiple_devices():
    ports = [SimpleNamespace(device=f'/dev/ttyACM{i}', vid=0x2E8A, serial_number=str(i)) for i in range(2)]
    with pytest.raises(RuntimeError, match='Vyberte'):
        updater.select_port(ports)
    assert updater.select_port(ports, '/dev/ttyACM1') is ports[1]
    with pytest.raises(RuntimeError):
        updater.select_port([], '/dev/ttyACM0')


@pytest.mark.parametrize('value', ['unknown', '4', '4.1-dev extra', '4.x'])
def test_unknown_versions_refused(value):
    with pytest.raises(ValueError):
        updater.version(value)


def test_project_firmware_identity():
    assert updater.firmware_info((updater.ROOT / 'firmware/pico/main.py').read_bytes()) == '4.1-pio-pair'
    with pytest.raises(ValueError):
        updater.firmware_info(b'FIRMWARE_NAME="other"\nFIRMWARE_VERSION="1.0"')


class Wire:
    """Fragmented raw REPL response, including an optional remote exception."""
    def __init__(self, error=b''):
        self.response = bytearray(b'OKresult\r\n\x04' + error + b'\x04>')
        self.writes = []

    @property
    def in_waiting(self):
        return 1 if self.response else 0

    def read(self, count):
        out = bytes(self.response[:count])
        del self.response[:count]
        return out

    def write(self, data):
        self.writes.append(data)


def test_raw_protocol_handles_fragmented_output_and_remote_errors():
    pico = object.__new__(updater.Pico)
    pico.pending = b''
    pico.serial = Wire()
    assert pico.execute('print("result")') == 'result'
    assert pico.serial.writes[-1] == b'\x04'
    pico.serial = Wire(b'Traceback: disk full')
    with pytest.raises(RuntimeError, match='disk full'):
        pico.execute('write_file()')


def test_readback_mismatch_does_not_replace_main(monkeypatch):
    pico = object.__new__(updater.Pico)
    commands = []
    monkeypatch.setattr(pico, 'execute', commands.append)
    monkeypatch.setattr(pico, 'read_file', lambda name: b'corrupt')
    with pytest.raises(RuntimeError, match='nezmenený'):
        pico.install(source('4.1'))
    assert not any('os.rename' in command for command in commands)


def test_micropython_compile_failure_does_not_replace_main(monkeypatch):
    pico = object.__new__(updater.Pico)
    commands = []
    data = source('4.1')
    def execute(command):
        commands.append(command)
        if 'compile(' in command:
            raise RuntimeError('SyntaxError')
    monkeypatch.setattr(pico, 'execute', execute)
    monkeypatch.setattr(pico, 'read_file', lambda name: data)
    with pytest.raises(RuntimeError, match='SyntaxError'):
        pico.install(data)
    assert not any('os.rename' in command for command in commands)

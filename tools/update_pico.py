#!/usr/bin/env python3
"""Update the HDF application on an existing MicroPython Pico over USB serial."""
import argparse
import ast
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import termios
import time
import uuid

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[1]
STAGE = 'main.hdf-update.tmp'
CONFIG = 'hdf_pico_config.json'


def version(value):
    match = re.fullmatch(r'(\d+)\.(\d+)(?:\.(\d+))?(?:-[A-Za-z0-9.-]+)?', value)
    if not match:
        raise ValueError(f'Neznáma verzia: {value!r}')
    return tuple(int(part or 0) for part in match.groups())


def firmware_info(source):
    fields = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    fields[target.id] = node.value.value
    if fields.get('FIRMWARE_NAME') != 'pico_hdf_controller':
        raise ValueError('Zdroj nie je HDF Pico firmvér.')
    value = fields['FIRMWARE_VERSION']
    version(value)
    return value


def select_port(ports, requested=None):
    candidates = [p for p in ports if p.vid == 0x2E8A]
    if requested:
        matches = [p for p in candidates if os.path.realpath(p.device) == os.path.realpath(requested)]
    else:
        matches = candidates
    if len(matches) != 1:
        available = ', '.join(f'{p.device} (USB serial {p.serial_number})' for p in candidates) or 'žiadne'
        raise RuntimeError(f'Vyberte jedno Pico cez --port /dev/ttyACM0. Dostupné: {available}')
    return matches[0]


def assert_port_unused(port):
    """Host PID namespace is required by the Docker launcher."""
    device = os.stat(port).st_rdev
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            for fd in (process / 'fd').iterdir():
                try:
                    info = fd.stat()
                    if stat.S_ISCHR(info.st_mode) and info.st_rdev == device:
                        raise RuntimeError(f'Port {port} používa proces {process.name}. Zatvorte aplikáciu/sériový terminál.')
                except FileNotFoundError:
                    pass
        except (FileNotFoundError, ProcessLookupError):
            pass
        except PermissionError as exc:
            raise RuntimeError('Nemožno overiť obsadenosť portu; použite docker/update-pico.sh.') from exc


class Pico:
    def __init__(self, port):
        assert_port_unused(port)
        self.serial = serial.Serial(port, 115200, timeout=0.1, write_timeout=3, exclusive=True)
        try:
            # Also block clients that do not use pyserial's advisory lock.
            fcntl.ioctl(self.serial.fileno(), termios.TIOCEXCL)
            assert_port_unused(port)
        except BaseException:
            self.close()
            raise
        self.pending = b''

    def close(self):
        try:
            fcntl.ioctl(self.serial.fileno(), termios.TIOCNXCL)
        finally:
            self.serial.close()

    def until(self, marker, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if marker in self.pending:
                index = self.pending.index(marker) + len(marker)
                result, self.pending = self.pending[:index], self.pending[index:]
                return result
            self.pending += self.serial.read(max(1, self.serial.in_waiting))
            if len(self.pending) > 2_000_000:
                raise RuntimeError('Príliš veľká odpoveď Pico.')
        raise TimeoutError(f'Pico neodpovedalo; koniec odpovede: {self.pending[-200:]!r}')

    def status(self):
        self.pending = b''
        self.serial.reset_input_buffer()
        self.serial.write(b'\nSTATUS\n')
        lines = []
        end = time.monotonic() + 5
        while time.monotonic() < end:
            line = self.until(b'\n', timeout=max(0.1, end - time.monotonic())).decode(errors='replace').strip()
            lines.append(line)
            if line == 'END':
                break
        else:
            raise TimeoutError('Neúplný STATUS.')
        firmware = [line.split() for line in lines if line.startswith('FIRMWARE ')]
        if len(firmware) != 1 or len(firmware[0]) != 3 or firmware[0][1] != 'pico_hdf_controller':
            raise RuntimeError('Zariadenie nepotvrdilo HDF Pico firmvér; nič sa nenahráva.')
        version(firmware[0][2])
        return firmware[0][2], '\n'.join(lines)

    def enter_raw(self):
        self.pending = b''
        self.serial.write(b'\r\x03\x03')
        time.sleep(0.2)
        self.serial.reset_input_buffer()
        self.serial.write(b'\r\x01')
        self.until(b'raw REPL; CTRL-B to exit\r\n>')
        self.execute('from machine import Pin\nPin(17, Pin.OUT).value(0)\nPin(16, Pin.OUT).value(0)')

    def execute(self, source):
        data = source.encode()
        for start in range(0, len(data), 128):
            self.serial.write(data[start:start + 128])
            time.sleep(0.003)
        self.serial.write(b'\x04')
        if self.until(b'OK') != b'OK':
            raise RuntimeError('Neplatné potvrdenie raw REPL.')
        output = self.until(b'\x04')[:-1]
        error = self.until(b'\x04')[:-1]
        self.until(b'>')
        if error:
            raise RuntimeError(error.decode(errors='replace'))
        return output.decode().strip()

    def read_file(self, name, optional=False):
        files = json.loads(self.execute('import os, json\nprint(json.dumps(os.listdir()))'))
        if name not in files:
            if optional:
                return None
            raise RuntimeError(f'Na Pico chýba {name}.')
        return bytes.fromhex(self.execute(f"import ubinascii\nprint(ubinascii.hexlify(open({name!r}, 'rb').read()).decode())"))

    def install(self, data):
        # The active main.py remains intact until staging and verification finish.
        self.execute(f"f = open({STAGE!r}, 'wb')\nf.close()")
        for start in range(0, len(data), 512):
            chunk = data[start:start + 512].hex()
            self.execute(f"import ubinascii\nf = open({STAGE!r}, 'ab')\nf.write(ubinascii.unhexlify({chunk!r}))\nf.close()")
        self.execute('import os\nos.sync()')
        if self.read_file(STAGE) != data:
            raise RuntimeError('Kontrola nahraného súboru zlyhala; main.py zostal nezmenený.')
        # Compile without executing, using the Pico's actual MicroPython runtime.
        self.execute(f"import gc\ngc.collect()\ncompile(open({STAGE!r}).read(), 'main.py', 'exec')\ngc.collect()")
        self.execute(f"import os\nos.rename({STAGE!r}, 'main.py')\nos.sync()")
        if self.read_file('main.py') != data:
            raise RuntimeError('Overenie main.py po nahratí zlyhalo.')

    def reboot(self):
        self.pending = b''
        # Friendly REPL soft reset runs main.py; a raw-mode reset would skip it.
        self.serial.write(b'\x02')
        self.until(b'>>> ')
        self.serial.write(b'\x04')
        self.until(b'READY pico_hdf_controller ', timeout=12)
        self.until(b'\n')


def backup_files(directory, original, config, info):
    directory.mkdir(parents=True, exist_ok=False)
    files = {'main.py': original, 'status-before.txt': info.pop('status').encode()}
    if config is not None:
        files[CONFIG] = config
    info['config_present'] = config is not None
    info['sha256'] = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files['manifest.json'] = json.dumps(info, indent=2).encode()
    for name, data in files.items():
        with (directory / name).open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    # Persist directory entries before touching the device's active firmware.
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def update(pico, source, target, backups, device, check=False):
    current, status = pico.status()
    print(f'Pico: {current}; projekt: {target}', flush=True)
    if version(current) >= version(target):
        print('Pico má rovnakú alebo novšiu verziu. Bez zmeny.')
        return
    if check:
        print('Je dostupná novšia verzia. Kontrola skončila bez nahrávania.')
        return
    original = None
    installation_started = False
    interrupted = False
    try:
        interrupted = True
        pico.enter_raw()
        original = pico.read_file('main.py')
        if firmware_info(original) != current:
            raise RuntimeError('Bežiaca verzia sa nezhoduje s main.py vo flash; automatická aktualizácia odmietnutá.')
        config = pico.read_file(CONFIG, optional=True)
        directory = backups / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        backup_files(directory, original, config, {
            'port': device.device, 'usb_serial': device.serial_number,
            'before': current, 'target': target, 'status': status,
        })
        print(f'Záloha: {directory}', flush=True)
        installation_started = True
        pico.install(source)
        if pico.read_file(CONFIG, optional=True) != config:
            raise RuntimeError('Konfigurácia sa počas aktualizácie zmenila.')
        pico.reboot()
        actual, after = pico.status()
        if actual != target or 'SESSION_MODE IDLE' not in after:
            raise RuntimeError(f'Overenie po reštarte zlyhalo: {actual}.')
        print(f'Úspešne nahraté a overené: {actual}. Pico je v IDLE.', flush=True)
    except BaseException:
        if interrupted:
            try:
                pico.enter_raw()
                if installation_started and original is not None:
                    pico.install(original)
                pico.reboot()
                restored, _ = pico.status()
                if restored != current:
                    raise RuntimeError(f'Očakávaná pôvodná verzia {current}, zistená {restored}')
                print('Pôvodný firmvér bol znova spustený.', flush=True)
            except BaseException as recovery_error:
                print(f'AUTOMATICKÁ OBNOVA ZLYHALA: {recovery_error}. Použite uloženú zálohu; nezapínajte výrobu.', flush=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='Konkrétny Pico port, napr. /dev/ttyACM0')
    parser.add_argument('--check', action='store_true', help='Iba zistí a porovná verziu')
    args = parser.parse_args(argv)
    source = (ROOT / 'firmware/pico/main.py').read_bytes()
    target = firmware_info(source)
    device = select_port(list_ports.comports(), args.port)
    pico = Pico(device.device)
    try:
        update(pico, source, target, Path('/backups'), device, check=args.check)
    finally:
        pico.close()


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'CHYBA: {exc}', flush=True)
        raise SystemExit(1)

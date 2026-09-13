"""Persistent logical stations and serial-number based USB discovery."""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class UsbDevice:
    serial: str
    path: str
    product: str


def discover_devices(sys_root=Path('/sys'), dev_root=Path('/dev')):
    cameras, picos = [], []
    for kind, pattern, target, vendor in (
        ('video4linux', 'video*', cameras, '2560'),
        ('tty', 'ttyACM*', picos, '2e8a'),
    ):
        for entry in sorted((Path(sys_root) / 'class' / kind).glob(pattern)):
            if kind == 'video4linux':
                # CU55 exposes a capture node and a metadata node.
                try:
                    if (entry / 'index').read_text().strip() != '0':
                        continue
                except OSError:
                    continue
            device = (entry / 'device').resolve()
            for parent in (device, *device.parents):
                if not (parent / 'idVendor').exists():
                    continue
                try:
                    if (parent / 'idVendor').read_text().strip().lower() != vendor:
                        break
                    serial = (parent / 'serial').read_text().strip()
                    product = (parent / 'product').read_text().strip()
                    if serial:
                        target.append(UsbDevice(serial, str(Path(dev_root) / entry.name), product))
                except OSError:
                    pass
                break
    return cameras, picos


def resolve_serial(serial, kind, discover=discover_devices):
    cameras, picos = discover()
    matches = [item for item in (cameras if kind == 'camera' else picos) if item.serial == serial]
    if len(matches) != 1:
        raise RuntimeError(f'{"Kamera" if kind == "camera" else "Pico"} {serial}: '
                           'zariadenie chýba alebo nemá jednoznačnú identitu.')
    return matches[0].path


@dataclass(frozen=True)
class StationBinding:
    id: str
    name: str
    camera_serial: str
    pico_serial: str

    def data_root(self, root):
        # Preserve the first station's existing recipes and history in place.
        return Path(root) if self.id == 'camera_1' else Path(root) / 'stations' / self.id


class WorkstationDevices:
    def __init__(self, path=Path('/data/workstation_devices.json')):
        self.path = Path(path)

    @staticmethod
    def validate(bindings):
        if not bindings:
            raise ValueError('Nastavte aspoň jednu kamerovú stanicu.')
        for binding in bindings:
            if not all(isinstance(getattr(binding, key), str) for key in ('id', 'name', 'camera_serial', 'pico_serial')):
                raise ValueError('Priradenie zariadení musí obsahovať textové identity.')
            if not re.fullmatch(r'camera_[1-9][0-9]*', binding.id) or not binding.name.strip():
                raise ValueError('Neplatná identita kamerovej stanice.')
            if not binding.camera_serial.strip() or not binding.pico_serial.strip():
                raise ValueError('Každá stanica potrebuje kameru a Pico.')
        for field in ('id', 'camera_serial', 'pico_serial'):
            if len({getattr(b, field) for b in bindings}) != len(bindings):
                raise ValueError('Jedno zariadenie nemožno priradiť dvom nezávislým staniciam.')
        if bindings[0].id != 'camera_1':
            raise ValueError('Prvá stanica musí byť Kamera 1.')

    def load(self):
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
                raise ValueError('Nepodporovaný formát priradenia zariadení.')
            if not isinstance(data.get('stations'), list):
                raise ValueError('Chýba zoznam kamerových staníc.')
            bindings = [StationBinding(**item) for item in data['stations']]
            self.validate(bindings)
            return bindings
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('Poškodené priradenie kamier a Pico.') from exc

    def save(self, bindings):
        self.validate(bindings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'version': 1, 'stations': [asdict(b) for b in bindings]},
                                        ensure_ascii=False, indent=2) + '\n')
        temporary.replace(self.path)

from pathlib import Path
from types import SimpleNamespace
import pytest
from app.services.workstation_devices import (StationBinding, WorkstationDevices,
    UsbDevice, resolve_serial, discover_devices)
from app.services.camera_service import CameraService
from app.services.pico_service import PicoService


def bindings(n=3):
    return [StationBinding(f'camera_{i}', f'Kamera {i}', f'cam{i}', f'pico{i}') for i in range(1, n + 1)]


def test_three_station_pairings_roundtrip_and_data_roots(tmp_path):
    config = WorkstationDevices(tmp_path / 'devices.json')
    config.save(bindings())
    assert config.load() == bindings()
    assert bindings()[0].data_root(tmp_path) == tmp_path
    assert bindings()[2].data_root(tmp_path) == tmp_path / 'stations/camera_3'


@pytest.mark.parametrize('field', ['camera_serial', 'pico_serial', 'id'])
def test_duplicate_resources_rejected_without_overwriting_config(tmp_path, field):
    from dataclasses import replace
    config = WorkstationDevices(tmp_path / 'devices.json'); original = bindings()
    config.save(original)
    invalid = original.copy(); invalid[1] = replace(invalid[1], **{field: getattr(original[0], field)})
    with pytest.raises(ValueError): config.save(invalid)
    assert config.load() == original


def test_identity_follows_usb_renumbering_and_never_uses_first_device():
    attached = [UsbDevice('other', '/dev/video0', 'CU55'), UsbDevice('mine', '/dev/video2', 'CU55')]
    discovery = lambda: (attached, [])
    assert resolve_serial('mine', 'camera', discovery) == '/dev/video2'
    attached[:] = [UsbDevice('mine', '/dev/video4', 'CU55')]
    assert resolve_serial('mine', 'camera', discovery) == '/dev/video4'
    attached.clear()
    with pytest.raises(RuntimeError): resolve_serial('mine', 'camera', discovery)


def test_capture_and_metadata_nodes_are_not_two_cameras(tmp_path):
    usb = tmp_path / 'devices/usb-camera'; usb.mkdir(parents=True)
    for key, value in {'idVendor': '2560', 'serial': 'unique', 'product': 'CU55'}.items():
        (usb / key).write_text(value)
    for number in range(2):
        entry = tmp_path / 'class/video4linux' / f'video{number}'; entry.mkdir(parents=True)
        (entry / 'device').symlink_to(usb, target_is_directory=True)
        (entry / 'index').write_text(str(number))
    cameras, picos = discover_devices(tmp_path, Path('/dev'))
    assert cameras == [UsbDevice('unique', '/dev/video0', 'CU55')]
    assert not picos


def test_camera_reconnect_invalidates_only_its_own_stream():
    paths = ['/dev/video2']
    camera = CameraService('/dev/video0', device_resolver=lambda: paths[0])
    other = CameraService('/dev/video4')
    stopped = []
    camera.stream.stop = lambda **kw: stopped.append(kw['caller'])
    camera.resolve_device()
    assert camera.device == '/dev/video2' and camera.devices == ['/dev/video2']
    assert stopped == ['usb_identity_changed']
    assert other.device == '/dev/video4' and other.pio._pio_generation == 0


def test_missing_pico_does_not_fall_back_to_other_port(monkeypatch):
    def missing(): raise RuntimeError('Pico missing')
    pico = PicoService(port_resolver=missing)
    monkeypatch.setattr('app.services.pico_service.serial', SimpleNamespace(Serial=lambda *a, **k: pytest.fail('opened wrong Pico')))
    assert not pico.connect()
    assert pico.last_error == 'Pico missing'


def test_pair_verifier_always_releases_both_resources_after_failure():
    from app.services.pairing_verification import verify_pair
    events = []
    camera = SimpleNamespace(stop=lambda **kw: events.append('camera stopped'))
    def capture(*args, **kwargs): raise RuntimeError('wrong pair')
    pico = SimpleNamespace(capture_trigger=capture, quiesce=lambda: events.append('pico idle'),
                           close=lambda: events.append('pico closed'))
    with pytest.raises(RuntimeError, match='wrong pair'):
        verify_pair('camera', 'pico', camera_factory=lambda **kw: camera, pico_factory=lambda **kw: pico)
    assert events == ['pico idle', 'camera stopped', 'pico closed']


def test_station_settings_do_not_change_another_station(tmp_path):
    from app.services.settings_service import SessionSettingsStore
    a, b = SessionSettingsStore(tmp_path / 'a'), SessionSettingsStore(tmp_path / 'b')
    a.update_session_settings(logging_enabled=False, export_artifacts=False)
    assert b.get_session_settings().logging_enabled
    assert b.get_session_settings().export_artifacts
    assert b.get_session_settings().logging_path == tmp_path / 'b'

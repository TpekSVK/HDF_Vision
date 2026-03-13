from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - test environment shim
    sys.path.insert(0, str(ROOT))

from app.services import camera_hid_cu55 as hid_mod


class _FakeOS:
    O_RDWR = 0x02
    O_NONBLOCK = 0x800

    def __init__(self, reply_payload: bytes):
        self.reply_payload = reply_payload
        self.last_write: bytes | None = None

    def open(self, *_args, **_kwargs):
        return 11

    def close(self, *_args, **_kwargs):
        return None

    def write(self, _fd: int, data: bytes):
        self.last_write = bytes(data)
        return len(data)

    def read(self, _fd: int, _size: int):
        return self.reply_payload


def test_stream_mode_parses_verified_layout(monkeypatch):
    fake = _FakeOS(bytes.fromhex("9F 01 01 00 00 00 01") + bytes(57))
    monkeypatch.setattr(hid_mod, "os", fake)
    monkeypatch.setattr(hid_mod.select, "select", lambda *_args, **_kwargs: ([11], [], []))

    dev = hid_mod.CU55HID("/dev/hidraw1")
    dev.open()
    value = dev.get_stream_mode()

    assert value == hid_mod.MODE_TRIGGER
    assert fake.last_write is not None
    assert len(fake.last_write) == hid_mod.HID_TX_PACKET_SIZE
    assert fake.last_write[:4] == bytes([0x00, 0x9F, 0x01, 0x00])


def test_unique_id_and_firmware_are_parsed_from_verified_reply_layout(monkeypatch):
    unique_reply = bytes.fromhex("41 1D 3A 58 06 00 00 00") + bytes(56)
    firmware_reply = bytes.fromhex("40 01 05 00 83 06 70 00") + bytes(56)

    replies = [unique_reply, firmware_reply, firmware_reply]
    fake = _FakeOS(replies[0])
    monkeypatch.setattr(hid_mod, "os", fake)
    monkeypatch.setattr(hid_mod.select, "select", lambda *_args, **_kwargs: ([11], [], []))

    def _read(_fd: int, _size: int):
        return replies.pop(0)

    fake.read = _read

    dev = hid_mod.CU55HID("/dev/hidraw1")
    dev.open()

    assert dev.read_unique_id() == "1D3A5806"
    assert dev.read_firmware_version() == (1, 5, 131, 1648)
    assert dev.read_firmware_version_string() == "1.5.131.1648"




def test_set_stream_mode_accepts_status_in_next_index_for_65b_reply(monkeypatch):
    # Niektoré kernel/driver kombinácie vrátia 65 B frame a success status
    # na nasledujúcom indexe (pri zachovaní rovnakého command/value layoutu).
    reply_65 = bytes([
        0x00, 0x9F, 0x02, 0x01, 0x00, 0x00, 0x00, 0x00, 0x01
    ]) + bytes(56)
    fake = _FakeOS(reply_65)
    monkeypatch.setattr(hid_mod, "os", fake)
    monkeypatch.setattr(hid_mod.select, "select", lambda *_args, **_kwargs: ([11], [], []))

    dev = hid_mod.CU55HID("/dev/hidraw1")
    dev.open()
    dev.set_stream_mode(hid_mod.MODE_TRIGGER)

def test_map_video_to_hidraw_filters_for_cu55_vid_pid(monkeypatch):
    monkeypatch.setattr(hid_mod, "_usb_identity_pyudev", lambda dev: hid_mod.USBIdentity("1d6b", "0002", "usb-a"))
    monkeypatch.setattr(hid_mod, "_usb_identity_udevadm", lambda dev: hid_mod.USBIdentity(None, None, None))

    assert hid_mod.map_video_to_hidraw("/dev/video0") is None


def test_map_video_to_hidraw_matches_same_usb_parent(monkeypatch):
    identities = {
        "/dev/video0": hid_mod.USBIdentity("2560", "c155", "usb-0000:01:00.0-2"),
        "/dev/hidraw0": hid_mod.USBIdentity("2560", "c155", "usb-0000:01:00.0-1"),
        "/dev/hidraw1": hid_mod.USBIdentity("2560", "c155", "usb-0000:01:00.0-2"),
    }

    monkeypatch.setattr(hid_mod.glob, "glob", lambda _pattern: ["/dev/hidraw0", "/dev/hidraw1"])
    monkeypatch.setattr(hid_mod, "_usb_identity_pyudev", lambda dev: identities.get(dev, hid_mod.USBIdentity(None, None, None)))
    monkeypatch.setattr(hid_mod, "_usb_identity_udevadm", lambda dev: hid_mod.USBIdentity(None, None, None))

    assert hid_mod.map_video_to_hidraw("/dev/video0") == "/dev/hidraw1"


def test_send_software_trigger_uses_set_roll_high_then_low(monkeypatch):
    sent: list[tuple[int, int | None]] = []

    dev = hid_mod.CU55HID("/dev/hidraw1")

    def _fake_send(command: int, payload: int | None = None):
        sent.append((command, payload))
        return bytes([0] * hid_mod.HID_RX_MAX_SIZE)

    monkeypatch.setattr(dev, "_send_cmd", _fake_send)

    dev.send_software_trigger()

    assert sent == [
        (hid_mod.SET_ROLL, 0x01),
        (hid_mod.SET_ROLL, 0x00),
    ]

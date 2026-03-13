from __future__ import annotations

import glob
import logging
import os
import re
import select
import subprocess
from dataclasses import dataclass
from typing import Optional

try:
    import pyudev
except Exception:  # pragma: no cover - optional runtime dependency
    pyudev = None


LOGGER = logging.getLogger(__name__)


HID_TX_PACKET_SIZE = 65
HID_RX_PAYLOAD_SIZE = 64
HID_RX_MAX_SIZE = 65
REPORT_ID = 0x00
COMMAND_GROUP = 0x9F

GET_STREAM_MODE = 0x01
SET_STREAM_MODE = 0x02
GET_FLASH_MODE = 0x03
SET_FLASH_MODE = 0x04
SET_DEFAULT = 0x05
GET_ROLL = 0x06
SET_ROLL = 0x07
READ_FIRMWARE_VERSION = 0x40
READ_UNIQUE_ID = 0x41

MODE_MASTER = 0x00
MODE_TRIGGER = 0x01

FLASH_OFF = 0x00
FLASH_STROBE = 0x01
FLASH_TORCH = 0x02

STATUS_SUCCESS = 0x01
CU55_VENDOR_ID = "2560"
CU55_PRODUCT_ID = "c155"

IDX_REPORT_ID = 0
IDX_GROUP = 1
IDX_COMMAND = 2
IDX_VALUE = 3
IDX_STATUS = 7


@dataclass
class USBIdentity:
    vendor_id: Optional[str]
    product_id: Optional[str]
    devpath: Optional[str]


class CU55HID:
    def __init__(self, hidraw_path: str, timeout_s: float = 0.25):
        self.hidraw_path = hidraw_path
        self.timeout_s = timeout_s
        self.fd: int | None = None

    def open(self):
        if self.fd is not None:
            return
        try:
            self.fd = os.open(self.hidraw_path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            LOGGER.error("Failed to open HID device %s: %s", self.hidraw_path, exc)
            raise

    def close(self):
        if self.fd is None:
            return
        try:
            os.close(self.fd)
        except OSError as exc:
            LOGGER.error("Failed to close HID device %s: %s", self.hidraw_path, exc)
        finally:
            self.fd = None

    def _send_cmd(self, command: int, payload: int | None = None) -> bytes:
        if self.fd is None:
            raise RuntimeError("HID device not open")

        packet = bytearray(HID_TX_PACKET_SIZE)
        packet[0] = REPORT_ID
        packet[1] = COMMAND_GROUP
        packet[2] = command & 0xFF
        if payload is not None:
            packet[3] = payload & 0xFF

        LOGGER.debug("HID TX %s: %s", self.hidraw_path, packet.hex(" "))
        try:
            os.write(self.fd, packet)
        except OSError as exc:
            raise RuntimeError(f"HID write failed: {exc}") from exc

        ready, _, _ = select.select([self.fd], [], [], self.timeout_s)
        if not ready:
            raise TimeoutError(f"HID read timeout for command 0x{command:02X}")

        try:
            reply_raw = os.read(self.fd, HID_RX_MAX_SIZE)
        except OSError as exc:
            raise RuntimeError(f"HID read failed: {exc}") from exc

        # Na Linuxe môže hidraw vrátiť buď 64 B payload (bez report ID), alebo 65 B
        # (s report ID). Interný parser používa jednotný 65-bajtový rámec.
        if len(reply_raw) == HID_RX_PAYLOAD_SIZE:
            reply = bytes([REPORT_ID]) + bytes(reply_raw)
        elif len(reply_raw) == HID_RX_MAX_SIZE:
            reply = bytes(reply_raw)
        else:
            raise RuntimeError(f"HID reply has unexpected length: {len(reply_raw)}")

        LOGGER.debug("HID RX %s: %s", self.hidraw_path, reply.hex(" "))
        if len(reply) < 8:
            raise RuntimeError(f"HID reply too short: {len(reply)}")

        if command in {GET_STREAM_MODE, SET_STREAM_MODE, GET_FLASH_MODE, SET_FLASH_MODE, SET_DEFAULT, GET_ROLL, SET_ROLL}:
            if reply[IDX_GROUP] != COMMAND_GROUP:
                raise RuntimeError(f"Invalid HID group: 0x{reply[IDX_GROUP]:02X}")
            if reply[IDX_COMMAND] != (command & 0xFF):
                raise RuntimeError(f"Invalid HID command echo: 0x{reply[IDX_COMMAND]:02X}")

            status_candidates = [reply[IDX_STATUS]]
            if len(reply) > (IDX_STATUS + 1):
                status_candidates.append(reply[IDX_STATUS + 1])
            if STATUS_SUCCESS not in status_candidates:
                raise RuntimeError(f"HID command failed status=0x{reply[IDX_STATUS]:02X}")
        elif command in {READ_FIRMWARE_VERSION, READ_UNIQUE_ID}:
            if reply[1] != (command & 0xFF):
                raise RuntimeError(f"Invalid HID info reply command: 0x{reply[1]:02X}")
        else:
            raise RuntimeError(f"Unsupported HID command 0x{command:02X}")

        return reply

    def set_stream_mode(self, mode: int):
        mode = int(mode)
        if mode not in (MODE_MASTER, MODE_TRIGGER):
            raise ValueError("Stream mode must be 0 (Master) or 1 (Trigger)")
        self._send_cmd(SET_STREAM_MODE, mode)

    def get_stream_mode(self) -> int:
        return int(self._send_cmd(GET_STREAM_MODE)[IDX_VALUE])

    def set_flash_mode(self, mode: int):
        mode = int(mode)
        if mode not in (FLASH_OFF, FLASH_STROBE, FLASH_TORCH):
            raise ValueError("Flash mode must be 0 (Off), 1 (Strobe) or 2 (Torch)")
        self._send_cmd(SET_FLASH_MODE, mode)

    def get_flash_mode(self) -> int:
        return int(self._send_cmd(GET_FLASH_MODE)[IDX_VALUE])

    def read_firmware_version(self) -> tuple[int, int, int, int]:
        reply = self._send_cmd(READ_FIRMWARE_VERSION)
        return (
            int(reply[2]),
            int(reply[3]),
            int(reply[5]),
            int(reply[6] << 8 | reply[7]),
        )

    def read_firmware_version_string(self) -> str:
        return ".".join(str(part) for part in self.read_firmware_version())

    def read_unique_id(self) -> str:
        reply = self._send_cmd(READ_UNIQUE_ID)
        return "".join(f"{b:02X}" for b in reply[2:6])

    def send_software_trigger(self):
        """
        See3CAM CU55: software trigger pulse via SET_ROLL command group.
        Pulse is generated by writing HIGH then LOW.
        """
        self._send_cmd(SET_ROLL, 0x01)
        self._send_cmd(SET_ROLL, 0x00)


def _usb_identity_udevadm(devnode: str) -> USBIdentity:
    vendor = product = devpath = None
    try:
        output = subprocess.check_output(["udevadm", "info", "--query=property", "--name", devnode], text=True)
    except Exception:
        return USBIdentity(vendor, product, devpath)

    for line in output.splitlines():
        if line.startswith("ID_VENDOR_ID="):
            vendor = line.split("=", 1)[1].lower()
        elif line.startswith("ID_MODEL_ID="):
            product = line.split("=", 1)[1].lower()
        elif line.startswith("ID_PATH="):
            devpath = line.split("=", 1)[1]
    return USBIdentity(vendor, product, devpath)


def _usb_identity_pyudev(devnode: str) -> USBIdentity:
    if pyudev is None:
        return USBIdentity(None, None, None)
    try:
        context = pyudev.Context()
        dev = pyudev.Devices.from_device_file(context, devnode)
    except Exception:
        return USBIdentity(None, None, None)

    usb_parent = dev.find_parent("usb", "usb_device")
    if usb_parent is None:
        return USBIdentity(None, None, None)

    vendor = (usb_parent.attributes.get("idVendor") or b"").decode("utf-8", errors="ignore").lower() or None
    product = (usb_parent.attributes.get("idProduct") or b"").decode("utf-8", errors="ignore").lower() or None
    devpath = usb_parent.get("DEVPATH")
    return USBIdentity(vendor, product, devpath)


def _extract_parent_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    # Udev ID_PATH niekedy obsahuje suffix rozhrania (napr. ":1.0").
    # Pri párovaní video<->hidraw chceme porovnať fyzickú USB vetvu.
    return re.sub(r":\d+\.\d+$", "", path)


def map_video_to_hidraw(video_dev: str) -> Optional[str]:
    video_identity = _usb_identity_pyudev(video_dev)
    if video_identity.vendor_id is None and video_identity.product_id is None:
        video_identity = _usb_identity_udevadm(video_dev)

    if video_identity.vendor_id is None and video_identity.product_id is None:
        return None

    vendor_id = (video_identity.vendor_id or "").lower()
    product_id = (video_identity.product_id or "").lower()

    # CU55 kamera používa VID/PID 2560:c155.
    if vendor_id != CU55_VENDOR_ID or product_id != CU55_PRODUCT_ID:
        return None

    video_parent = _extract_parent_path(video_identity.devpath)

    for hidraw in sorted(glob.glob("/dev/hidraw*")):
        hid_identity = _usb_identity_pyudev(hidraw)
        if hid_identity.vendor_id is None and hid_identity.product_id is None:
            hid_identity = _usb_identity_udevadm(hidraw)

        if not hid_identity.vendor_id or not hid_identity.product_id:
            continue
        if hid_identity.vendor_id.lower() != vendor_id:
            continue
        if hid_identity.product_id.lower() != product_id:
            continue

        hid_parent = _extract_parent_path(hid_identity.devpath)
        if video_parent and hid_parent and hid_parent != video_parent:
            continue
        return hidraw
    return None

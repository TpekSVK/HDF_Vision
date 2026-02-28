from __future__ import annotations

import glob
import logging
import os
import select
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


class CU55MHHidError(RuntimeError):
    """HID command failed or returned invalid response."""


@dataclass(frozen=True)
class CU55MHProtocol:
    BUFFER_LENGTH: int = 65
    CAMERA_CONTROL_SEE3CAM_CU55_MH: int = 0x9F

    GET_STREAM_MODE: int = 0x01
    SET_STREAM_MODE: int = 0x02
    GET_FLASH_MODE: int = 0x03
    SET_FLASH_MODE: int = 0x04
    SET_DEFAULT: int = 0x05

    MODE_MASTER: int = 0x00
    MODE_TRIGGER: int = 0x01

    FLASH_OFF: int = 0x00
    FLASH_STROBE: int = 0x01
    FLASH_TORCH: int = 0x02


def _hex(data: bytes) -> str:
    return data.hex() if data else "<empty>"


def _hidraw_sysfs_vid_pid(hidraw_path: str) -> tuple[str | None, str | None]:
    node = Path(hidraw_path).name
    base = Path("/sys/class/hidraw") / node / "device"
    candidates = [
        base,
        base.parent,
        base.parent.parent,
    ]
    for cand in candidates:
        vid = cand / "idVendor"
        pid = cand / "idProduct"
        if vid.exists() and pid.exists():
            return vid.read_text(encoding="utf8").strip().lower(), pid.read_text(encoding="utf8").strip().lower()
    return None, None


def _build_packet(cmd: int, value: int | None = None) -> bytes:
    packet = bytearray(CU55MHProtocol.BUFFER_LENGTH)
    packet[1] = CU55MHProtocol.CAMERA_CONTROL_SEE3CAM_CU55_MH
    packet[2] = int(cmd) & 0xFF
    if value is not None:
        packet[3] = int(value) & 0xFF
    return bytes(packet)


def _probe_hidraw(path: str, timeout_s: float = 0.5) -> bytes | None:
    fd = None
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        os.write(fd, _build_packet(CU55MHProtocol.GET_STREAM_MODE))
        ready, _, _ = select.select([fd], [], [], timeout_s)
        if not ready:
            return None
        data = os.read(fd, CU55MHProtocol.BUFFER_LENGTH)
        if len(data) >= 5 and data[0] == 0x01:
            return data
        return None
    except Exception as exc:
        logger.debug("HID probe failed for %s: %s", path, exc)
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def discover_cu55mh_hidraw(video_dev: str = "/dev/video0") -> str | None:
    """
    Discover hidraw node for CU55M_MH.

    Priority:
    1) HDF_HIDRAW env override.
    2) Scan /dev/hidraw* and prefer VID:PID 8516:13bb.
    3) Probe candidates with GET_STREAM and accept first response (len>=5 and data[0]==0x01).
    """

    _ = video_dev
    forced = os.getenv("HDF_HIDRAW", "").strip()
    if forced:
        logger.info("Using HDF_HIDRAW override: %s", forced)
        return forced

    nodes = sorted(glob.glob("/dev/hidraw*"))
    if not nodes:
        return None

    preferred: list[str] = []
    others: list[str] = []
    for path in nodes:
        vid, pid = _hidraw_sysfs_vid_pid(path)
        if vid == "8516" and pid == "13bb":
            preferred.append(path)
        else:
            others.append(path)

    ordered = preferred + others
    for path in ordered:
        resp = _probe_hidraw(path)
        if resp is not None:
            logger.info("Selected HID node %s (probe=%s)", path, _hex(resp))
            return path

    logger.warning("No responsive hidraw node found among: %s", ", ".join(ordered))
    return None


def select_hidraw_for_device(video_dev: str = "/dev/video0") -> str | None:
    return discover_cu55mh_hidraw(video_dev)


class CU55MH_HID:
    def __init__(self, video_dev: str = "/dev/video0", hidraw_path: str | None = None):
        self.video_dev = video_dev
        self.protocol = CU55MHProtocol()
        self.hidraw_path = hidraw_path or select_hidraw_for_device(video_dev)
        if not self.hidraw_path:
            raise CU55MHHidError("No suitable /dev/hidraw* device available for CU55M_MH")
        self._fd = os.open(self.hidraw_path, os.O_RDWR | os.O_NONBLOCK)

    def close(self) -> None:
        if getattr(self, "_fd", None) is not None:
            os.close(self._fd)
            self._fd = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _exchange(self, cmd: int, value: int | None = None) -> bytes:
        packet = _build_packet(cmd, value)
        written = os.write(self._fd, packet)
        if written != self.protocol.BUFFER_LENGTH:
            raise CU55MHHidError(f"HID write failed: wrote {written}/{self.protocol.BUFFER_LENGTH} bytes")

        ready, _, _ = select.select([self._fd], [], [], 5.0)
        if not ready:
            raise CU55MHHidError(f"HID read timeout (cmd=0x{cmd:02X}, path={self.hidraw_path})")

        data = os.read(self._fd, self.protocol.BUFFER_LENGTH)
        if not data:
            raise CU55MHHidError(f"HID empty response for cmd=0x{cmd:02X}")
        return data

    def _parse_stream_mode(self, resp: bytes) -> int:
        if len(resp) >= 4 and resp[0] == 0x01:
            return 1 if resp[3] != 0 else 0
        raise CU55MHHidError(f"Unexpected GET_STREAM response: {self.hidraw_path} resp={_hex(resp)}")

    def set_stream_mode(self, mode: int) -> None:
        mode = int(mode)
        if mode not in (self.protocol.MODE_MASTER, self.protocol.MODE_TRIGGER):
            raise ValueError("Stream mode must be 0 (Master) or 1 (Trigger)")

        attempts = 3
        last_verify = None
        for attempt in range(attempts):
            resp = self._exchange(self.protocol.SET_STREAM_MODE, mode)
            logger.debug("SET_STREAM attempt=%d response=%s", attempt + 1, _hex(resp))

            try:
                verified = self.get_stream_mode()
            except Exception as exc:
                last_verify = exc
                verified = None

            if verified == mode:
                return
            last_verify = CU55MHHidError(
                f"SET_STREAM verify mismatch: expected={mode}, got={verified}, set_resp={_hex(resp)}"
            )
            if attempt < attempts - 1:
                time.sleep(0.1)

        raise CU55MHHidError(f"Failed to set stream mode after {attempts} attempts: {last_verify}")

    def get_stream_mode(self) -> int:
        resp = self._exchange(self.protocol.GET_STREAM_MODE)
        return self._parse_stream_mode(resp)

    def set_flash_mode(self, mode: int) -> None:
        mode = int(mode)
        if mode not in (self.protocol.FLASH_OFF, self.protocol.FLASH_STROBE, self.protocol.FLASH_TORCH):
            raise ValueError("Flash mode must be 0 (OFF), 1 (Strobe) or 2 (Torch)")
        self._exchange(self.protocol.SET_FLASH_MODE, mode)

    def restore_defaults(self) -> None:
        self._exchange(self.protocol.SET_DEFAULT)

    def _run_v4l2_ctl(self, arg: str) -> bool:
        cmd = ["v4l2-ctl", "-d", self.video_dev, "-c", arg]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def set_manual_exposure_us(self, exposure_us: int) -> None:
        val = int(exposure_us)
        if val <= 0:
            raise ValueError("Exposure must be positive (microseconds)")
        self._run_v4l2_ctl("exposure_auto=1")
        if not self._run_v4l2_ctl(f"exposure_time_absolute={val}"):
            hundred_us = max(1, val // 100)
            self._run_v4l2_ctl(f"exposure_absolute={hundred_us}")

    def set_gain_db(self, gain_db: int) -> None:
        val = int(gain_db)
        if val < 0:
            raise ValueError("Gain must be non-negative")
        self._run_v4l2_ctl(f"gain={val}")

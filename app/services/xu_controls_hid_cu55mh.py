from __future__ import annotations

import errno
import glob
import os
import select
import subprocess
import time
from dataclasses import dataclass


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

    STATUS_FAILURE: int = 0x00
    STATUS_SUCCESS: int = 0x01


def first_available_hidraw() -> str | None:
    hid_nodes = sorted(glob.glob("/dev/hidraw*"))
    return hid_nodes[0] if hid_nodes else None


def select_hidraw_for_device(video_dev: str = "/dev/video0") -> str | None:
    """
    Return hidraw path for a video device.

    TODO: in a future iteration, filter by udev metadata (ID_VENDOR_ID/ID_MODEL_ID)
    and/or map by USB topology instead of returning the first hidraw node.
    """

    _ = video_dev
    return first_available_hidraw()


class CU55MH_HID:
    def __init__(self, video_dev: str = "/dev/video0", hidraw_path: str | None = None):
        self.video_dev = video_dev
        self.protocol = CU55MHProtocol()
        self.hidraw_path = hidraw_path or select_hidraw_for_device(video_dev)
        if not self.hidraw_path:
            raise CU55MHHidError("No /dev/hidraw* device available")
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
        out = bytearray(self.protocol.BUFFER_LENGTH)
        out[1] = self.protocol.CAMERA_CONTROL_SEE3CAM_CU55_MH
        out[2] = int(cmd) & 0xFF
        if value is not None:
            out[3] = int(value) & 0xFF

        written = os.write(self._fd, out)
        if written != self.protocol.BUFFER_LENGTH:
            raise CU55MHHidError(f"HID write failed: wrote {written}/{self.protocol.BUFFER_LENGTH} bytes")

        ready, _, _ = select.select([self._fd], [], [], 5.0)
        if not ready:
            raise CU55MHHidError(f"HID read timeout (cmd=0x{cmd:02X}, path={self.hidraw_path})")

        data = self._read_exact(self.protocol.BUFFER_LENGTH, timeout_s=0.2)
        self._validate_response(data, cmd)
        return data

    def _read_exact(self, size: int, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        chunks = bytearray()
        while len(chunks) < size:
            try:
                chunk = os.read(self._fd, size - len(chunks))
                if chunk:
                    chunks.extend(chunk)
                    continue
            except BlockingIOError:
                pass
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    pass
                else:
                    raise

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CU55MHHidError(f"HID read returned {len(chunks)}/{size} bytes")
            ready, _, _ = select.select([self._fd], [], [], remaining)
            if not ready and time.monotonic() >= deadline:
                raise CU55MHHidError(f"HID read returned {len(chunks)}/{size} bytes")

        return bytes(chunks)

    def _validate_response(self, data: bytes, expected_cmd: int) -> None:
        if len(data) != self.protocol.BUFFER_LENGTH:
            raise CU55MHHidError(f"Invalid HID response length: {len(data)}")
        if data[0] != self.protocol.CAMERA_CONTROL_SEE3CAM_CU55_MH:
            raise CU55MHHidError(f"Invalid HID response header: 0x{data[0]:02X}")
        if data[1] != (expected_cmd & 0xFF):
            raise CU55MHHidError(
                f"Unexpected HID response cmd: got 0x{data[1]:02X}, expected 0x{expected_cmd:02X}"
            )
        if data[6] != self.protocol.STATUS_SUCCESS:
            raise CU55MHHidError(f"Camera reported failure status=0x{data[6]:02X} for cmd=0x{expected_cmd:02X}")

    def set_stream_mode(self, mode: int) -> None:
        mode = int(mode)
        if mode not in (self.protocol.MODE_MASTER, self.protocol.MODE_TRIGGER):
            raise ValueError("Stream mode must be 0 (Master) or 1 (Trigger)")
        self._exchange(self.protocol.SET_STREAM_MODE, mode)

    def get_stream_mode(self) -> int:
        data = self._exchange(self.protocol.GET_STREAM_MODE)
        return int(data[2])

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

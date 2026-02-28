# XU stub pre See3CAM_CU55M – Linux V4L2 XU (alebo vendor .so)
# Podľa manuálu:
#  - Stream Mode: 0x00 Master, 0x01 Trigger
#  - Flash: 0x00 OFF, 0x01 Strobe, 0x02 Torch
#  Implementácia v tomto súbore nekomunikuje so skutočným zariadením.
#  Slúži ako perzistentný stub, ktorý vystavuje rovnaké API ako reálna
#  integrácia a uchováva posledné nastavenia v lokálnom súbore.

from __future__ import annotations

import json
import os
import subprocess
import uuid
import logging
from pathlib import Path
from typing import Any, Dict

from app.services.xu_controls_hid_cu55mh import CU55MH_HID, CU55MHHidError, select_hidraw_for_device


logger = logging.getLogger(__name__)

# Reálne GUID a selektory sú odvodené z Windows SDK pre See3CAM_CU55M.
# GUID zodpovedá extension jednotke `e-con See3CAM_CU55M` a selektory
# jednotlivým príkazom (Stream/Flash/Restore). Aj keď linuxový build v
# rámci testov nekomunikuje so skutočným zariadením, je praktické mať
# tieto identifikátory k dispozícii – môžu byť použité pri integrácii
# s V4L2 ioctl, resp. vendor knižnicou.
XU_GUID = uuid.UUID("e7dc6f74-1b62-411c-9c16-7a4f29c1b5cf")

# Selektory podľa SDK (ekvivalenty SetStreamModeCU55_MH, SetFlashCU55_MH,
# RestoreDefaultCU55_MH).
SELECTOR_STREAM_MODE = 0x01
SELECTOR_FLASH_MODE = 0x02
SELECTOR_RESTORE_DEFAULTS = 0x03


def _state_directory() -> Path:
    """Return path where we persist stub state."""

    base = os.getenv("XU_STATE_DIR")
    if base:
        path = Path(base)
    else:
        cache_root = os.getenv("XDG_CACHE_HOME")
        if cache_root:
            path = Path(cache_root) / "hdf_vision"
        else:
            path = Path.home() / ".cache" / "hdf_vision"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sanitize_dev_name(dev: str) -> str:
    return dev.replace("/", "_").strip("_") or "video0"


class XUControls:
    """Vendor XU stub so the rest of the application can run without HW."""

    def __init__(self, video_dev: str = "/dev/video0"):
        self.video_dev = video_dev
        self.guid = XU_GUID
        self.selector_stream_mode = SELECTOR_STREAM_MODE
        self.selector_flash_mode = SELECTOR_FLASH_MODE
        self.selector_restore_defaults = SELECTOR_RESTORE_DEFAULTS
        self._state_path = _state_directory() / f"xu_{_sanitize_dev_name(video_dev)}.json"
        self._state: Dict[str, Any] = self._load_state()

    # ------------------------------------------------------------------
    # Persistence helpers (stub behaviour)
    # ------------------------------------------------------------------
    def _default_state(self) -> Dict[str, Any]:
        return {
            "stream_mode": 0,
            "flash_mode": 0,
            "pixel_format": "Y8",
            "exposure_us": 8000,
            "gain_db": 0,
        }

    def _load_state(self) -> Dict[str, Any]:
        if self._state_path.exists():
            try:
                with self._state_path.open("r", encoding="utf8") as fh:
                    data = json.load(fh)
            except Exception:
                data = self._default_state()
        else:
            data = self._default_state()

        defaults = self._default_state()
        for key, value in defaults.items():
            data.setdefault(key, value)
        return data

    def _save_state(self) -> None:
        try:
            with self._state_path.open("w", encoding="utf8") as fh:
                json.dump(self._state, fh, indent=2, sort_keys=True)
        except Exception:
            # Persistence failure should not prevent usage of the stub.
            pass

    # ------------------------------------------------------------------
    # Helper to run optional v4l2-ctl commands (best effort).
    # ------------------------------------------------------------------
    def _run_v4l2_ctl(self, arg: str) -> bool:
        cmd = ["v4l2-ctl", "-d", self.video_dev, "-c", arg]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except FileNotFoundError:
            # v4l2-ctl je voliteľný – v CI/testoch ho nemusíme mať.
            return False
        except subprocess.CalledProcessError:
            return False

    # ------------------------------------------------------------------
    # Public API mirrors vendor SDK calls.
    # ------------------------------------------------------------------
    def set_stream_mode(self, mode: int) -> None:
        # mode: 0=Master, 1=Trigger
        mode = int(mode)
        if mode not in (0, 1):
            raise ValueError("Stream mode must be 0 (Master) or 1 (Trigger)")
        self._state["stream_mode"] = mode
        self._save_state()

    def get_stream_mode(self) -> int:
        return int(self._state.get("stream_mode", 0))

    def set_flash_mode(self, val: int) -> None:
        # 0=OFF, 1=Strobe, 2=Torch
        val = int(val)
        if val not in (0, 1, 2):
            raise ValueError("Flash mode must be 0 (OFF), 1 (Strobe) or 2 (Torch)")
        self._state["flash_mode"] = val
        self._save_state()

    def restore_defaults(self) -> None:
        self._state = self._default_state()
        self._save_state()

    def set_manual_exposure_us(self, exposure_us: int) -> None:
        # Pozn.: v Trigger Mode musí byť expo >= trigger period; pre 1080p@60 je frame ~16.67 ms.
        val = int(exposure_us)
        if val <= 0:
            raise ValueError("Exposure must be positive (microseconds)")

        # UVC štandard – najprv manuálny režim, potom samotná hodnota.
        self._run_v4l2_ctl("exposure_auto=1")
        if not self._run_v4l2_ctl(f"exposure_time_absolute={val}"):
            hundred_us = max(1, val // 100)
            self._run_v4l2_ctl(f"exposure_absolute={hundred_us}")

        self._state["exposure_us"] = val
        self._save_state()

    def set_gain_db(self, gain_db: int) -> None:
        val = int(gain_db)
        if val < 0:
            raise ValueError("Gain must be non-negative")

        self._run_v4l2_ctl(f"gain={val}")
        self._state["gain_db"] = val
        self._save_state()


def create_xu_backend(video_dev: str = "/dev/video0", prefer_hid: bool = True):
    if prefer_hid:
        hidraw_path = select_hidraw_for_device(video_dev)
        if hidraw_path:
            try:
                backend = CU55MH_HID(video_dev=video_dev, hidraw_path=hidraw_path)
                logger.info("XU backend selected: HID (%s)", hidraw_path)
                return backend
            except (CU55MHHidError, OSError) as exc:
                logger.info("XU HID backend unavailable (%s), fallback to stub.", exc)
        else:
            logger.info("XU HID backend unavailable (no /dev/hidraw*), fallback to stub.")

    backend = XUControls(video_dev)
    logger.info("XU backend selected: STUB")
    return backend

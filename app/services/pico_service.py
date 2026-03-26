"""USB serial service for Raspberry Pi Pico flash control."""

from __future__ import annotations

import glob
import logging
import threading
from dataclasses import dataclass
from typing import Iterable

try:
    import serial
    from serial import SerialException
except Exception:  # pragma: no cover - optional dependency fallback
    serial = None  # type: ignore[assignment]

    class SerialException(Exception):
        pass


@dataclass(slots=True)
class PicoStatus:
    connected: bool
    port: str | None
    last_error: str


class PicoService:
    def __init__(
        self,
        *,
        port: str | None = None,
        baudrate: int = 115200,
        timeout_s: float = 0.35,
        write_timeout_s: float = 0.35,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._configured_port = (port or "").strip() or None
        self._baudrate = int(baudrate)
        self._timeout_s = max(0.05, float(timeout_s))
        self._write_timeout_s = max(0.05, float(write_timeout_s))
        self._lock = threading.Lock()
        self._serial = None
        self._active_port: str | None = None
        self._available = False
        self.last_error: str = ""

    def connect(self) -> bool:
        if serial is None:
            self.last_error = "pyserial is not installed"
            self._available = False
            self._logger.warning("[PICO] pyserial missing")
            return False

        with self._lock:
            if self._serial is not None and bool(getattr(self._serial, "is_open", False)):
                self._available = True
                return True

            for candidate in self._candidate_ports():
                try:
                    dev = serial.Serial(
                        candidate,
                        self._baudrate,
                        timeout=self._timeout_s,
                        write_timeout=self._write_timeout_s,
                    )
                except Exception as exc:
                    self.last_error = str(exc)
                    self._logger.debug("[PICO] connect failed port=%s err=%s", candidate, exc)
                    continue
                self._serial = dev
                self._active_port = candidate
                self._available = True
                self.last_error = ""
                self._logger.info("[PICO] connected port=%s baud=%s", candidate, self._baudrate)
                return True

            self._available = False
            if not self.last_error:
                self.last_error = "Pico serial port not found"
            self._logger.warning("[PICO] unavailable: %s", self.last_error)
            return False

    def is_available(self) -> bool:
        if self._serial is None or not bool(getattr(self._serial, "is_open", False)):
            return False
        return bool(self._available)

    def configure_view_flash(self, view_id_or_channel: str | int, delay_ms: int, pulse_ms: int) -> bool:
        target = self._normalize_target(view_id_or_channel)
        ok_delay, _ = self._send_command(f"SET {target} DELAY {int(delay_ms)}")
        ok_pulse, _ = self._send_command(f"SET {target} PULSE {int(pulse_ms)}")
        return ok_delay and ok_pulse

    def configure_recipe_views(self, mapping: dict[str, dict[str, int]]) -> bool:
        success = True
        for target, params in dict(mapping or {}).items():
            delay = int(params.get("delay_ms", 0))
            pulse = int(params.get("pulse_ms", 200))
            success = self.configure_view_flash(target, delay, pulse) and success
        return success

    def fire(self, channel_or_view_id: str | int) -> bool:
        target = self._normalize_target(channel_or_view_id)
        ok, _ = self._send_command(f"FIRE {target}")
        return ok

    def save(self) -> bool:
        ok, _ = self._send_command("SAVE")
        return ok

    def status(self) -> dict[str, object]:
        ok, response = self._send_command("STATUS")
        payload = response if ok else ""
        return {
            "connected": bool(ok and self.is_available()),
            "port": self._active_port,
            "last_error": self.last_error,
            "device_status": payload,
        }

    def close(self) -> None:
        with self._lock:
            dev = self._serial
            self._serial = None
            self._available = False
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass

    def _candidate_ports(self) -> list[str]:
        if self._configured_port:
            return [self._configured_port]
        return sorted(glob.glob("/dev/ttyACM*"))

    def _normalize_target(self, value: str | int) -> str:
        text = str(value).strip()
        if not text:
            return "V1"
        if text.upper().startswith("V"):
            return text.upper()
        if text.isdigit():
            return f"V{text}"
        return text.upper()

    def _send_command(self, command: str) -> tuple[bool, str]:
        if not self.connect():
            return False, ""

        cmd = f"{command.strip()}\n"
        with self._lock:
            dev = self._serial
            if dev is None:
                self._available = False
                self.last_error = "Pico serial is disconnected"
                return False, ""
            try:
                dev.reset_input_buffer()
            except Exception:
                pass
            try:
                dev.write(cmd.encode("utf-8"))
                dev.flush()
                line = dev.readline().decode("utf-8", errors="ignore").strip()
            except (SerialException, OSError) as exc:
                self.last_error = str(exc)
                self._available = False
                self._logger.warning("[PICO] command failed cmd=%s err=%s", command, exc)
                try:
                    dev.close()
                except Exception:
                    pass
                self._serial = None
                return False, ""
            except Exception as exc:
                self.last_error = str(exc)
                self._available = False
                self._logger.warning("[PICO] unexpected error cmd=%s err=%s", command, exc)
                return False, ""

        normalized = line.strip()
        ok = normalized.upper().startswith("OK") or normalized.upper().startswith("STATUS")
        if not ok and not normalized:
            self.last_error = f"No response for command: {command}"
            self._logger.warning("[PICO] empty response cmd=%s", command)
        elif not ok:
            self.last_error = normalized
            self._logger.warning("[PICO] error response cmd=%s resp=%s", command, normalized)
        else:
            self.last_error = ""
            self._available = True
            self._logger.debug("[PICO] cmd=%s resp=%s", command, normalized)
        return ok, normalized


__all__ = ["PicoService", "PicoStatus"]

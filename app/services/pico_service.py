"""USB serial service for Raspberry Pi Pico flash control."""

from __future__ import annotations

import glob
import logging
import queue
import re
import threading
from dataclasses import dataclass
from typing import Callable

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


_DISCONNECTED = object()
_CAPTURE_RE = re.compile(r"^CAPTURE\s+IN([1-8])$", re.IGNORECASE)


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
        self._state_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._serial = None
        self._active_port: str | None = None
        self._available = False
        self._rx_thread: threading.Thread | None = None
        self._rx_stop: threading.Event | None = None
        self._pending_response: queue.Queue[object] | None = None
        self._pending_command: str | None = None
        self._trigger_callbacks: list[Callable[[int], None]] = []
        self.last_error: str = ""

    def connect(self) -> bool:
        if serial is None:
            self.last_error = "pyserial is not installed"
            self._available = False
            self._logger.warning("[PICO] pyserial missing")
            return False

        with self._state_lock:
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
                stop = threading.Event()
                reader = threading.Thread(
                    target=self._rx_loop,
                    args=(dev, stop),
                    name="pico-serial-rx",
                    daemon=True,
                )
                self._serial = dev
                self._active_port = candidate
                self._rx_stop = stop
                self._rx_thread = reader
                self._available = True
                self.last_error = ""
                reader.start()
                self._logger.info("[PICO] connected port=%s baud=%s", candidate, self._baudrate)
                self._logger.info("[PICO] RX thread started")
                return True

            self._available = False
            if not self.last_error:
                self.last_error = "Pico serial port not found"
            self._logger.warning("[PICO] unavailable: %s", self.last_error)
            return False

    def is_available(self) -> bool:
        with self._state_lock:
            return bool(
                self._available
                and self._serial is not None
                and getattr(self._serial, "is_open", False)
                and self._rx_thread is not None
                and self._rx_thread.is_alive()
            )

    def register_trigger_callback(self, callback: Callable[[int], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._state_lock:
            if callback not in self._trigger_callbacks:
                self._trigger_callbacks.append(callback)

    def unregister_trigger_callback(self, callback: Callable[[int], None]) -> None:
        with self._state_lock:
            if callback in self._trigger_callbacks:
                self._trigger_callbacks.remove(callback)

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

    def set_view_mode(self, view_id_or_channel: str | int, mode: str) -> bool:
        """Set a supported view to the firmware's MASTER or TRIGGER mode."""
        target = self._normalize_target(view_id_or_channel)
        if target not in {"V1", "V2"}:
            self.last_error = f"Invalid Pico view: {view_id_or_channel!r}"
            return False

        normalized_mode = str(mode or "").strip().upper()
        if normalized_mode not in {"MASTER", "TRIGGER"}:
            self.last_error = f"Invalid Pico mode: {mode!r}"
            return False

        ok, _ = self._send_command(f"SET {target} MODE {normalized_mode}")
        return ok

    def save_config(self) -> bool:
        """Persist the current Pico firmware configuration."""
        ok, _ = self._send_command("SAVE")
        return ok

    def save(self) -> bool:
        """Backward-compatible alias for :meth:`save_config`."""
        return self.save_config()

    def status(self) -> dict[str, object]:
        ok, response = self._send_command("STATUS")
        return {
            "connected": bool(ok and self.is_available()),
            "port": self._active_port,
            "last_error": self.last_error,
            "device_status": response if ok else "",
        }

    def inputs(self) -> str:
        """Return the firmware's multiline INPUTS response, or an empty string."""
        ok, response = self._send_command("INPUTS")
        return response if ok else ""

    def close(self) -> None:
        with self._state_lock:
            dev = self._serial
            stop = self._rx_stop
            reader = self._rx_thread
            pending = self._pending_response
            self._serial = None
            self._active_port = None
            self._available = False
            self._rx_stop = None
            self._rx_thread = None
            self._pending_response = None
            self._pending_command = None
        if stop is not None:
            stop.set()
        if pending is not None:
            pending.put(_DISCONNECTED)
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=max(1.0, self._timeout_s * 2))

    def _candidate_ports(self) -> list[str]:
        if self._configured_port:
            return [self._configured_port]
        return sorted(glob.glob("/dev/ttyACM*"))

    @staticmethod
    def _parse_capture(line: str) -> int | None:
        match = _CAPTURE_RE.fullmatch(line.strip())
        return int(match.group(1)) if match else None

    def _rx_loop(self, dev: object, stop: threading.Event) -> None:
        disconnect_error = ""
        try:
            while not stop.is_set():
                try:
                    raw = dev.readline()  # type: ignore[attr-defined]
                except (SerialException, OSError) as exc:
                    disconnect_error = str(exc)
                    break
                except Exception as exc:
                    disconnect_error = str(exc)
                    break
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                self._logger.debug("[PICO] RX %s", line)
                input_index = self._parse_capture(line)
                if input_index is not None:
                    self._dispatch_trigger(input_index)
                    continue
                with self._state_lock:
                    pending = self._pending_response
                    pending_command = self._pending_command
                if pending is not None and self._line_matches_command(line, pending_command):
                    pending.put(line)
                else:
                    self._logger.debug("[PICO] ignored unsolicited line: %s", line)
        finally:
            with self._state_lock:
                owns_connection = self._serial is dev
                pending = self._pending_response if owns_connection else None
                if owns_connection:
                    self._serial = None
                    self._active_port = None
                    self._available = False
                    self._rx_stop = None
                    self._rx_thread = None
                    self._pending_response = None
                    self._pending_command = None
                    if disconnect_error:
                        self.last_error = disconnect_error
            if pending is not None:
                pending.put(_DISCONNECTED)
            try:
                dev.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            if owns_connection and not stop.is_set():
                self._logger.warning("[PICO] disconnected: %s", disconnect_error or "reader stopped")

    def _dispatch_trigger(self, input_index: int) -> None:
        with self._state_lock:
            callbacks = tuple(self._trigger_callbacks)
        self._logger.info("[PICO] event CAPTURE input=%s", input_index)
        for callback in callbacks:
            try:
                callback(input_index)
            except Exception:
                self._logger.exception("[PICO] trigger callback failed input=%s", input_index)

    @staticmethod
    def _line_matches_command(line: str, command: str | None) -> bool:
        """Keep recognizable unsolicited responses out of another command's reply."""
        if not command:
            return False
        verb = command.split(maxsplit=1)[0].upper()
        upper = line.upper()
        if upper.startswith("ERR"):
            return True
        if verb == "STATUS":
            return not upper.startswith(("OK FIRED", "BUSY ", "OK SAVED", "OK SET", "OK MAP"))
        if verb == "INPUTS":
            return upper.startswith("INPUTS ") or upper == "END"
        prefixes = {
            "SAVE": ("OK SAVED",),
            "SET": ("OK SET",),
            "MAP": ("OK MAP",),
            "FIRE": ("OK FIRED", "BUSY "),
        }
        return upper.startswith(prefixes.get(verb, ("OK",)))

    def _normalize_target(self, value: str | int) -> str | None:
        if isinstance(value, int):
            return f"V{value}" if value in {1, 2} else None
        text = str(value or "").strip().upper()
        if text in {"V1", "V2"}:
            return text
        normalized = text[4:] if text.startswith("VIEW") else text
        match = re.search(r"([12])", normalized.replace("_", ""))
        return f"V{match.group(1)}" if match else None

    def _send_command(self, command: str) -> tuple[bool, str]:
        command = command.strip()
        multiline = command.split(maxsplit=1)[0].upper() in {"STATUS", "INPUTS"}
        with self._command_lock:
            if not self.connect():
                return False, ""
            responses: queue.Queue[object] = queue.Queue()
            with self._state_lock:
                dev = self._serial
                if dev is None:
                    self.last_error = "Pico serial is disconnected"
                    return False, ""
                self._pending_response = responses
                self._pending_command = command
            try:
                dev.write(f"{command}\n".encode("utf-8"))
                dev.flush()
            except Exception as exc:
                self.last_error = str(exc)
                self._logger.warning("[PICO] command write failed cmd=%s err=%s", command, exc)
                self.close()
                return False, ""

            lines: list[str] = []
            try:
                while True:
                    try:
                        item = responses.get(timeout=self._timeout_s)
                    except queue.Empty:
                        self.last_error = f"No response for command: {command}"
                        self._logger.warning("[PICO] response timeout cmd=%s", command)
                        return False, "\n".join(lines)
                    if item is _DISCONNECTED:
                        self.last_error = self.last_error or "Pico serial is disconnected"
                        return False, "\n".join(lines)
                    line = str(item)
                    lines.append(line)
                    if not multiline or line.upper() == "END":
                        break
            finally:
                with self._state_lock:
                    if self._pending_response is responses:
                        self._pending_response = None
                        self._pending_command = None

            response = "\n".join(lines)
            ok = bool(lines) and not lines[0].upper().startswith(("ERR", "BUSY"))
            if ok:
                self.last_error = ""
                self._logger.debug("[PICO] cmd=%s resp=%s", command, response)
            else:
                self.last_error = response
                self._logger.warning("[PICO] error response cmd=%s resp=%s", command, response)
            return ok, response


__all__ = ["PicoService", "PicoStatus"]

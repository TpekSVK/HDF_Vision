"""Modbus TCP helper service used by the Modbus Wizard.

This module centralizes storage of Modbus connection and mapping settings and
offers a small API for testing connectivity, pulsing coils and reading discrete
inputs on the Ethernet relay module.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ExceptionResponse

__all__ = [
    "ModbusConfig",
    "ModbusService",
]


_CONFIG_PATH = Path("/data/modbus_config.json")


@dataclass
class ModbusConfig:
    host: str = "192.168.0.50"
    port: int = 502
    unit_id: int = 1
    timeout_ms: int = 300
    retry_count: int = 1
    enabled: bool = False

    ok_coil: int = 0
    nok_coil: int = 1
    heartbeat_coil: int = 2
    flash1_coil: int = -1
    flash2_coil: int = -1
    flash1_delay_ms: int = 0
    flash2_delay_ms: int = 0
    flash1_pulse_ms: int = 200
    flash2_pulse_ms: int = 200
    pulse_length_ms: int = 200
    heartbeat_period_ms: int = 1000

    trigger_di: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ModbusConfig":
        def _to_int(value: object, default: int) -> int:
            try:
                return int(value)
            except Exception:
                return default

        return cls(
            host=str(data.get("host", cls.host)).strip() or cls.host,
            port=_to_int(data.get("port", cls.port), cls.port),
            unit_id=_to_int(data.get("unit_id", cls.unit_id), cls.unit_id),
            timeout_ms=_to_int(data.get("timeout_ms", cls.timeout_ms), cls.timeout_ms),
            retry_count=_to_int(data.get("retry_count", cls.retry_count), cls.retry_count),
            enabled=bool(data.get("enabled", cls.enabled)),
            ok_coil=_to_int(data.get("ok_coil", cls.ok_coil), cls.ok_coil),
            nok_coil=_to_int(data.get("nok_coil", cls.nok_coil), cls.nok_coil),
            heartbeat_coil=_to_int(
                data.get("heartbeat_coil", cls.heartbeat_coil), cls.heartbeat_coil
            ),
            flash1_coil=_to_int(data.get("flash1_coil", cls.flash1_coil), cls.flash1_coil),
            flash2_coil=_to_int(data.get("flash2_coil", cls.flash2_coil), cls.flash2_coil),
            flash1_delay_ms=_to_int(
                data.get("flash1_delay_ms", cls.flash1_delay_ms), cls.flash1_delay_ms
            ),
            flash2_delay_ms=_to_int(
                data.get("flash2_delay_ms", cls.flash2_delay_ms), cls.flash2_delay_ms
            ),
            flash1_pulse_ms=_to_int(
                data.get("flash1_pulse_ms", cls.flash1_pulse_ms), cls.flash1_pulse_ms
            ),
            flash2_pulse_ms=_to_int(
                data.get("flash2_pulse_ms", cls.flash2_pulse_ms), cls.flash2_pulse_ms
            ),
            pulse_length_ms=_to_int(
                data.get("pulse_length_ms", cls.pulse_length_ms), cls.pulse_length_ms
            ),
            heartbeat_period_ms=_to_int(
                data.get("heartbeat_period_ms", cls.heartbeat_period_ms),
                cls.heartbeat_period_ms,
            ),
            trigger_di=_to_int(data.get("trigger_di", cls.trigger_di), cls.trigger_di),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ModbusService:
    def __init__(self, config_path: Path | str = _CONFIG_PATH) -> None:
        self._config_path = Path(config_path)
        self._lock = threading.Lock()
        self._client_io_lock = threading.Lock()
        self._config = self._load_config()
        self._client: Optional[ModbusTcpClient] = None
        self._client_config_key: Optional[Tuple[str, int, float, int]] = None
        self.last_error: str = ""
        self._trigger_callbacks: list[Callable[[], None]] = []
        self._trigger_monitor_stop = threading.Event()
        self._trigger_monitor_thread: threading.Thread | None = None
        self._last_trigger_level: Optional[bool] = None
        self._last_trigger_edge_ts: float = 0.0

        self._restart_trigger_monitor()

    # ------------------------------------------------------------------
    def _load_config(self) -> ModbusConfig:
        try:
            if self._config_path.exists():
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return ModbusConfig.from_dict(data)
        except Exception as exc:
            self.last_error = str(exc)
        return ModbusConfig()

    def _save_config(self, config: ModbusConfig) -> None:
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.last_error = str(exc)

    def get_config(self) -> ModbusConfig:
        with self._lock:
            return ModbusConfig.from_dict(self._config.to_dict())

    def set_config(self, config: ModbusConfig, *, persist: bool = True) -> None:
        with self._lock:
            self._config = config
            self._close_client()
            if persist:
                self._save_config(config)
        self._restart_trigger_monitor()

    # ------------------------------------------------------------------
    def _close_client(self) -> None:
        client = self._client
        self._client = None
        self._client_config_key = None
        if client:
            try:
                client.close()
            except Exception:
                pass

    def _is_connected(self, client: ModbusTcpClient) -> bool:
        if hasattr(client, "connected"):
            return bool(getattr(client, "connected"))
        return False

    def _client_key(self, config: ModbusConfig) -> Tuple[str, int, float, int]:
        return (
            config.host,
            int(config.port),
            float(config.timeout_ms) / 1000.0,
            int(config.retry_count),
        )

    def _ensure_client(self, config: ModbusConfig) -> Optional[ModbusTcpClient]:
        key = self._client_key(config)
        with self._lock:
            if self._client and self._client_config_key == key and self._is_connected(self._client):
                return self._client

            self._close_client()

            client = ModbusTcpClient(
                config.host,
                port=int(config.port),
                timeout=float(config.timeout_ms) / 1000.0,
                retries=int(config.retry_count),
            )
            try:
                connected = bool(client.connect())
            except Exception as exc:
                self.last_error = str(exc)
                return None

            if not connected:
                self.last_error = "Connection failed"
                try:
                    client.close()
                except Exception:
                    pass
                return None

            self._client = client
            self._client_config_key = key
            return client

    # ------------------------------------------------------------------
    def test_connection(self, config: Optional[ModbusConfig] = None) -> tuple[bool, str]:
        cfg = config or self.get_config()
        client = self._ensure_client(cfg)
        if client is None:
            return False, self.last_error or "Connection failed"
        return True, "Connected"

    # High-level helpers mirroring GPIO pipeline hooks -----------------
    def emit_heartbeat(self) -> None:
        cfg = self.get_config()
        if not cfg.enabled:
            return
        self.pulse_coil(cfg.heartbeat_coil, pulse_ms=cfg.pulse_length_ms, config=cfg)

    def signal_result(self, status: str, *, pulse_ms: Optional[int] = None) -> None:
        cfg = self.get_config()
        if not cfg.enabled:
            return
        normalized = (status or "").strip().lower()
        if normalized == "ok":
            target, other = cfg.ok_coil, cfg.nok_coil
        else:
            target, other = cfg.nok_coil, cfg.ok_coil

        duration = pulse_ms if pulse_ms is not None else cfg.pulse_length_ms
        if other is not None and int(other) >= 0:
            self._write_coil(other, False, cfg)
        self.pulse_coil(target, pulse_ms=duration, config=cfg)

    def set_flash(self, channel: int, enabled: bool) -> None:
        cfg = self.get_config()
        if not cfg.enabled:
            return
        coil = cfg.flash1_coil if int(channel) == 1 else cfg.flash2_coil
        if coil is None or int(coil) < 0:
            self.last_error = "Invalid flash coil"
            return
        self._write_coil(coil, bool(enabled), cfg)

    def pulse_flash(self, channel: int, *, pulse_ms: Optional[int] = None) -> None:
        cfg = self.get_config()
        if not cfg.enabled:
            return
        if int(channel) == 1:
            coil = cfg.flash1_coil
            delay_ms = cfg.flash1_delay_ms
            duration_ms = pulse_ms if pulse_ms is not None else cfg.flash1_pulse_ms
        else:
            coil = cfg.flash2_coil
            delay_ms = cfg.flash2_delay_ms
            duration_ms = pulse_ms if pulse_ms is not None else cfg.flash2_pulse_ms
        self.pulse_coil(
            coil,
            pulse_ms=duration_ms,
            delay_ms=delay_ms,
            config=cfg,
        )

    def pulse_configured_flashes(self) -> None:
        cfg = self.get_config()
        if not cfg.enabled:
            return
        if int(cfg.flash1_coil) >= 0:
            self.pulse_coil(
                cfg.flash1_coil,
                pulse_ms=cfg.flash1_pulse_ms,
                delay_ms=cfg.flash1_delay_ms,
                config=cfg,
            )
        if int(cfg.flash2_coil) >= 0:
            self.pulse_coil(
                cfg.flash2_coil,
                pulse_ms=cfg.flash2_pulse_ms,
                delay_ms=cfg.flash2_delay_ms,
                config=cfg,
            )

    def recommended_flash_capture_delay_ms(self, *, post_flash_guard_ms: int = 10) -> int:
        cfg = self.get_config()
        if not cfg.enabled:
            return 0

        delays: list[int] = []
        if int(cfg.flash1_coil) >= 0:
            delays.append(max(0, int(cfg.flash1_delay_ms)))
        if int(cfg.flash2_coil) >= 0:
            delays.append(max(0, int(cfg.flash2_delay_ms)))
        if not delays:
            return 0

        return max(0, min(delays) + max(0, int(post_flash_guard_ms)))

    def _write_coil(self, address: int, value: bool, config: ModbusConfig) -> bool:
        client = self._ensure_client(config)
        if client is None:
            return False
        with self._client_io_lock:
            try:
                response = client.write_coil(int(address), value, unit=int(config.unit_id))
            except (ModbusException, OSError, AttributeError) as exc:
                self.last_error = str(exc)
                with self._lock:
                    self._close_client()
                return False
            except Exception as exc:
                self.last_error = str(exc)
                with self._lock:
                    self._close_client()
                return False
        if isinstance(response, ExceptionResponse) or getattr(response, "isError", lambda: False)():
            self.last_error = str(response)
            return False
        return True

    def pulse_coil(
        self,
        address: int,
        *,
        pulse_ms: Optional[int] = None,
        delay_ms: int = 0,
        config: Optional[ModbusConfig] = None,
    ) -> bool:
        if address is None or int(address) < 0:
            self.last_error = "Invalid coil address"
            return False
        cfg = config or self.get_config()
        delay = max(0, int(delay_ms)) / 1000.0
        duration = max(1, int(pulse_ms if pulse_ms is not None else cfg.pulse_length_ms)) / 1000.0

        def _job() -> None:
            if delay > 0:
                time.sleep(delay)
            if not self._write_coil(address, True, cfg):
                return
            time.sleep(duration)
            self._write_coil(address, False, cfg)

        threading.Thread(target=_job, daemon=True).start()
        return True

    def heartbeat_pulse(self, address: int, *, count: int = 3, period_ms: int = 1000, config: Optional[ModbusConfig] = None) -> bool:
        if address is None or int(address) < 0:
            self.last_error = "Invalid heartbeat coil"
            return False
        cfg = config or self.get_config()
        period = max(10, int(period_ms)) / 1000.0

        def _job() -> None:
            for i in range(max(1, int(count))):
                if not self._write_coil(address, True, cfg):
                    return
                time.sleep(min(period, 0.5))
                self._write_coil(address, False, cfg)
                if i < count - 1:
                    time.sleep(period)

        threading.Thread(target=_job, daemon=True).start()
        return True

    def read_discrete_input(self, address: int, *, config: Optional[ModbusConfig] = None) -> Optional[bool]:
        cfg = config or self.get_config()
        if address is None or int(address) < 0:
            self.last_error = "Invalid input address"
            return None

        client = self._ensure_client(cfg)
        if client is None:
            return None

        with self._client_io_lock:
            try:
                response = client.read_discrete_inputs(int(address), 1, unit=int(cfg.unit_id))
            except (ModbusException, OSError, AttributeError) as exc:
                self.last_error = str(exc)
                with self._lock:
                    self._close_client()
                return None
            except Exception as exc:
                self.last_error = str(exc)
                with self._lock:
                    self._close_client()
                return None

        if isinstance(response, ExceptionResponse) or getattr(response, "isError", lambda: False)():
            self.last_error = str(response)
            return None

        if not hasattr(response, "bits"):
            self.last_error = "Unexpected response"
            return None
        try:
            return bool(response.bits[0])
        except Exception:
            return None

    # Trigger monitoring ------------------------------------------------
    def register_trigger_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._trigger_callbacks:
            self._trigger_callbacks.append(callback)

    def unregister_trigger_callback(self, callback: Callable[[], None]) -> None:
        try:
            self._trigger_callbacks.remove(callback)
        except ValueError:
            pass

    def _restart_trigger_monitor(self) -> None:
        self._stop_trigger_monitor()
        cfg = self.get_config()
        if not cfg.enabled or cfg.trigger_di is None or int(cfg.trigger_di) < 0:
            return
        self._trigger_monitor_stop = threading.Event()
        self._trigger_monitor_thread = threading.Thread(
            target=self._poll_trigger_input,
            daemon=True,
        )
        self._trigger_monitor_thread.start()

    def _stop_trigger_monitor(self) -> None:
        self._trigger_monitor_stop.set()
        thread = self._trigger_monitor_thread
        if thread and thread.is_alive():
            thread.join(timeout=0.5)
        self._trigger_monitor_thread = None
        self._last_trigger_level = None
        self._last_trigger_edge_ts = 0.0

    def _poll_trigger_input(self) -> None:
        debounce_seconds = 0.05
        while not self._trigger_monitor_stop.wait(0.01):
            cfg = self.get_config()
            if not cfg.enabled or cfg.trigger_di is None or int(cfg.trigger_di) < 0:
                continue
            level = self.read_discrete_input(cfg.trigger_di, config=cfg)
            if level is None:
                continue
            now = time.monotonic()
            last_level = self._last_trigger_level if self._last_trigger_level is not None else level
            if not last_level and level:
                if now - self._last_trigger_edge_ts >= debounce_seconds:
                    self._last_trigger_edge_ts = now
                    self._fire_trigger_callbacks()
            self._last_trigger_level = level

    def _fire_trigger_callbacks(self) -> None:
        callbacks = list(self._trigger_callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def close(self) -> None:
        self._stop_trigger_monitor()
        self._close_client()

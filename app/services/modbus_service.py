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
from typing import Optional, Tuple

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
    timeout_ms: int = 1500
    retry_count: int = 1
    enabled: bool = False

    ok_coil: int = 0
    nok_coil: int = 1
    heartbeat_coil: int = 2
    flash1_coil: int = -1
    flash2_coil: int = -1
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
        self._config = self._load_config()
        self._client: Optional[ModbusTcpClient] = None
        self._client_config_key: Optional[Tuple[str, int, float, int]] = None
        self.last_error: str = ""

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

    def _write_coil(self, address: int, value: bool, config: ModbusConfig) -> bool:
        client = self._ensure_client(config)
        if client is None:
            return False
        try:
            response = client.write_coil(int(address), value, unit=int(config.unit_id))
        except (ModbusException, OSError) as exc:
            self.last_error = str(exc)
            return False
        if isinstance(response, ExceptionResponse) or getattr(response, "isError", lambda: False)():
            self.last_error = str(response)
            return False
        return True

    def pulse_coil(self, address: int, *, pulse_ms: Optional[int] = None, config: Optional[ModbusConfig] = None) -> bool:
        if address is None or int(address) < 0:
            self.last_error = "Invalid coil address"
            return False
        cfg = config or self.get_config()
        duration = max(1, int(pulse_ms if pulse_ms is not None else cfg.pulse_length_ms)) / 1000.0

        def _job() -> None:
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

        try:
            response = client.read_discrete_inputs(int(address), 1, unit=int(cfg.unit_id))
        except (ModbusException, OSError) as exc:
            self.last_error = str(exc)
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

    def close(self) -> None:
        self._close_client()


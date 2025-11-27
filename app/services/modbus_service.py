"""Simple Modbus TCP helper focused on coil/DI access for UI triggers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import List, Optional

from pymodbus.client import ModbusTcpClient

__all__ = [
    "ModbusConnectionParams",
    "ModbusService",
]


@dataclass(slots=True)
class ModbusConnectionParams:
    host: str = ""
    port: int = 502
    unit_id: int = 1
    timeout_ms: int = 1500
    retries: int = 1


class ModbusService:
    def __init__(self) -> None:
        self._client: Optional[ModbusTcpClient] = None
        self._lock = threading.Lock()
        self._params = ModbusConnectionParams()
        self._last_error: str | None = None
        self._last_read_ts: float | None = None

    # ------------------------------------------------------------------
    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_read_ts(self) -> float | None:
        return self._last_read_ts

    def is_connected(self) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            return bool(client.connected) or bool(client.is_socket_open())
        except Exception:
            return False

    def connect(
        self,
        host: str,
        port: int = 502,
        unit_id: int = 1,
        timeout_ms: int = 1500,
        retries: int = 1,
    ) -> bool:
        """Establish a Modbus TCP connection (blocking)."""

        with self._lock:
            self.disconnect()
            self._last_error = None
            self._last_read_ts = None
            try:
                client = ModbusTcpClient(
                    host=host,
                    port=int(port),
                    timeout=float(timeout_ms) / 1000.0,
                    retries=int(retries),
                    retry_on_empty=True,
                )
                ok = bool(client.connect())
                if not ok:
                    self._last_error = "Connection failed"
                    return False
                self._client = client
                self._params = ModbusConnectionParams(
                    host=str(host or ""),
                    port=int(port),
                    unit_id=int(unit_id),
                    timeout_ms=int(timeout_ms),
                    retries=int(retries),
                )
                self._last_error = None
                return True
            except Exception as exc:
                self._last_error = str(exc)
                self._client = None
                return False

    def disconnect(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None

    # ------------------------------------------------------------------
    def _ensure_client(self) -> Optional[ModbusTcpClient]:
        client = self._client
        if client is None:
            self._last_error = "Not connected"
            return None
        return client

    def read_coils(self, address: int, count: int = 1) -> List[bool]:
        with self._lock:
            client = self._ensure_client()
            if client is None:
                return []
            try:
                result = client.read_coils(address, count, unit=self._params.unit_id)
                if result.isError():
                    self._last_error = str(result)
                    return []
                self._last_read_ts = time.time()
                self._last_error = None
                return [bool(x) for x in (result.bits or [])][:count]
            except Exception as exc:
                self._last_error = str(exc)
                return []

    def read_discrete_inputs(self, address: int, count: int = 1) -> List[bool]:
        with self._lock:
            client = self._ensure_client()
            if client is None:
                return []
            try:
                result = client.read_discrete_inputs(address, count, unit=self._params.unit_id)
                if result.isError():
                    self._last_error = str(result)
                    return []
                self._last_read_ts = time.time()
                self._last_error = None
                return [bool(x) for x in (result.bits or [])][:count]
            except Exception as exc:
                self._last_error = str(exc)
                return []

    def write_coil(self, address: int, value: bool) -> bool:
        with self._lock:
            client = self._ensure_client()
            if client is None:
                return False
            try:
                result = client.write_coil(address, value, unit=self._params.unit_id)
                if result.isError():
                    self._last_error = str(result)
                    return False
                self._last_error = None
                return True
            except Exception as exc:
                self._last_error = str(exc)
                return False


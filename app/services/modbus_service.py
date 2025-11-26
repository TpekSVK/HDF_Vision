"""Lightweight Modbus TCP helper used by the Modbus Wizard and runtime."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import List

from pymodbus.client import ModbusTcpClient

__all__ = [
    "ModbusService",
]


class ModbusService:
    """Thread-safe wrapper around ``ModbusTcpClient``.

    The methods are synchronous; callers are expected to execute them via
    background threads to avoid blocking the UI thread.
    """

    def __init__(self) -> None:
        self._client: ModbusTcpClient | None = None
        self._lock = threading.RLock()
        self._last_error: str | None = None
        self._unit_id: int = 1
        self._last_read_ts: float | None = None

    # ------------------------------------------------------------------
    # Connection management
    def connect(
        self,
        host: str,
        port: int = 502,
        unit_id: int = 1,
        timeout_ms: int = 1500,
        retries: int = 1,
    ) -> bool:
        """Open a TCP connection using the provided parameters."""

        with self._lock:
            self._last_error = None
            self._unit_id = int(unit_id)
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

            try:
                client = ModbusTcpClient(
                    host,
                    port=int(port),
                    timeout=max(0.1, float(timeout_ms) / 1000.0),
                    retries=max(0, int(retries)),
                    retry_on_empty=True,
                )
                ok = bool(client.connect())
                if not ok:
                    self._last_error = "Failed to connect"
                    return False
                self._client = client
                return True
            except Exception as exc:  # pragma: no cover - defensive
                self._last_error = str(exc)
                self._client = None
                return False

    def disconnect(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def is_connected(self) -> bool:
        with self._lock:
            return bool(self._client and self._client.connected)

    # ------------------------------------------------------------------
    # Accessors
    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_read_timestamp(self) -> datetime | None:
        if self._last_read_ts is None:
            return None
        return datetime.fromtimestamp(self._last_read_ts)

    # ------------------------------------------------------------------
    # Read/write helpers
    def read_coils(self, address: int, count: int = 1) -> List[bool]:
        with self._lock:
            if not self._client:
                self._last_error = "Not connected"
                return []
            try:
                resp = self._client.read_coils(int(address), int(count), unit=self._unit_id)
                if hasattr(resp, "isError") and resp.isError():
                    self._last_error = str(resp)
                    return []
                result = list(getattr(resp, "bits", []) or [])
                self._last_read_ts = time.time()
                return result
            except Exception as exc:
                self._last_error = str(exc)
                return []

    def read_discrete_inputs(self, address: int, count: int = 1) -> List[bool]:
        with self._lock:
            if not self._client:
                self._last_error = "Not connected"
                return []
            try:
                resp = self._client.read_discrete_inputs(int(address), int(count), unit=self._unit_id)
                if hasattr(resp, "isError") and resp.isError():
                    self._last_error = str(resp)
                    return []
                result = list(getattr(resp, "bits", []) or [])
                self._last_read_ts = time.time()
                return result
            except Exception as exc:
                self._last_error = str(exc)
                return []

    def write_coil(self, address: int, value: bool) -> bool:
        with self._lock:
            if not self._client:
                self._last_error = "Not connected"
                return False
            try:
                resp = self._client.write_coil(int(address), bool(value), unit=self._unit_id)
                if hasattr(resp, "isError") and resp.isError():
                    self._last_error = str(resp)
                    return False
                self._last_read_ts = time.time()
                return True
            except Exception as exc:
                self._last_error = str(exc)
                return False

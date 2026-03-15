"""Service for streaming and parsing Jetson tegrastats output."""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QObject, Signal


CPU_BLOCK_RE: Final[re.Pattern[str]] = re.compile(r"CPU\s*\[([^\]]+)\]")
CPU_PERCENT_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)%")
GPU_RE: Final[re.Pattern[str]] = re.compile(r"GR3D_FREQ\s+(\d+)%")
RAM_RE: Final[re.Pattern[str]] = re.compile(r"RAM\s+(\d+)/(\d+)MB")
TEMP_RE: Final[re.Pattern[str]] = re.compile(r"(?:CPU|GPU|cpu|gpu)@([0-9]+(?:\.[0-9]+)?)C")


@dataclass(frozen=True)
class JetsonStats:
    cpu_percent: int
    gpu_percent: int
    ram_used_gb: float
    ram_total_gb: float
    temp_c: float


class JetsonStatsService(QObject):
    """Read tegrastats in the background and expose latest parsed values."""

    stats_updated = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._latest_stats: JetsonStats | None = None

    @property
    def latest_stats(self) -> JetsonStats | None:
        with self._lock:
            return self._latest_stats

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        self._thread = None
        self._process = None

    def _run_loop(self) -> None:
        try:
            self._process = subprocess.Popen(
                ["tegrastats", "--interval", "1000"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception:
            return

        assert self._process.stdout is not None
        for line in self._process.stdout:
            if self._stop_event.is_set():
                break
            stats = self.parse_tegrastats_line(line)
            if stats is None:
                continue
            with self._lock:
                self._latest_stats = stats
            self.stats_updated.emit(
                {
                    "cpu_percent": stats.cpu_percent,
                    "gpu_percent": stats.gpu_percent,
                    "ram_used_gb": stats.ram_used_gb,
                    "ram_total_gb": stats.ram_total_gb,
                    "temp_c": stats.temp_c,
                }
            )

    @staticmethod
    def parse_tegrastats_line(line: str) -> JetsonStats | None:
        cpu_match = CPU_BLOCK_RE.search(line)
        gpu_match = GPU_RE.search(line)
        ram_match = RAM_RE.search(line)
        temp_match = TEMP_RE.search(line)
        if not (cpu_match and gpu_match and ram_match and temp_match):
            return None

        cpu_values = [int(match) for match in CPU_PERCENT_RE.findall(cpu_match.group(1))]
        if not cpu_values:
            return None
        cpu_avg = round(sum(cpu_values) / len(cpu_values))

        ram_used_mb = int(ram_match.group(1))
        ram_total_mb = int(ram_match.group(2))

        return JetsonStats(
            cpu_percent=cpu_avg,
            gpu_percent=int(gpu_match.group(1)),
            ram_used_gb=ram_used_mb / 1024.0,
            ram_total_gb=ram_total_mb / 1024.0,
            temp_c=float(temp_match.group(1)),
        )


__all__ = ["JetsonStats", "JetsonStatsService"]

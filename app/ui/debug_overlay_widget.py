"""Overlay widget for runtime Jetson performance telemetry."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget


class DebugOverlayWidget(QLabel):
    """Small non-interactive performance overlay rendered above main UI."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "background: rgba(0,0,0,120);"
            "color: #66ff66;"
            "font-size: 12px;"
            "padding: 6px;"
            "border-radius: 6px;"
        )
        self.setText("CPU --% | GPU --% | RAM --/-- GB | TEMP --°C")
        self.adjustSize()
        self.hide()

    def update_stats(self, stats: dict) -> None:
        text = (
            f"CPU {int(stats.get('cpu_percent', 0))}% | "
            f"GPU {int(stats.get('gpu_percent', 0))}% | "
            f"RAM {float(stats.get('ram_used_gb', 0.0)):.1f}/"
            f"{float(stats.get('ram_total_gb', 0.0)):.1f} GB | "
            f"TEMP {float(stats.get('temp_c', 0.0)):.0f}°C"
        )
        self.setText(text)
        self.adjustSize()

    def place_top_left(self, margin: int = 10) -> None:
        self.move(margin, margin)


__all__ = ["DebugOverlayWidget"]

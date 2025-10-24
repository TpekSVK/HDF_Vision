"""Lightweight widget showing per-step thumbnails for multi-view runs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
    QSpacerItem,
    QSizePolicy,
)

_STATUS_COLORS = {"ok": "#33dd66", "warn": "#e67e22", "nok": "#ff3366"}


def _frame_to_pixmap(frame: np.ndarray | None, size: QSize) -> QPixmap:
    if frame is None:
        return QPixmap()
    if frame.ndim == 2:
        height, width = frame.shape
        image = QImage(frame.data, width, height, width, QImage.Format_Grayscale8)
        pixmap = QPixmap.fromImage(image)
    else:
        data = frame
        if data.shape[2] == 4:
            format_ = QImage.Format_RGBA8888
            stride = data.shape[1] * 4
        else:
            format_ = QImage.Format_BGR888
            stride = data.shape[1] * 3
        image = QImage(data.data, data.shape[1], data.shape[0], stride, format_)
        pixmap = QPixmap.fromImage(image.convertToFormat(QImage.Format_RGB32))
    return pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class _StepPreview(QWidget):
    """Single item representing one multi-view step."""

    def __init__(
        self,
        index: int,
        name: str,
        status: str,
        frame: np.ndarray | None,
        metrics: Mapping[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        title = QLabel(f"{index}. {name}")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        preview_label = QLabel()
        preview_label.setFixedSize(120, 90)
        preview_label.setAlignment(Qt.AlignCenter)
        preview_label.setStyleSheet("border: 1px solid #444; border-radius: 4px; background: #181818;")
        layout.addWidget(preview_label)

        color = _STATUS_COLORS.get(status.lower(), "#888888")
        status_label = QLabel(status.upper())
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        layout.addWidget(status_label)

        if isinstance(frame, np.ndarray):
            if not frame.flags.get("C_CONTIGUOUS"):
                frame = np.ascontiguousarray(frame)
            pixmap = _frame_to_pixmap(frame, preview_label.size())
            if not pixmap.isNull():
                preview_label.setPixmap(pixmap)
        else:
            preview_label.setText("—")

        tooltip_rows: list[str] = []
        if metrics:
            for key, value in metrics.items():
                tooltip_rows.append(f"{key}: {value}")
        if tooltip_rows:
            self.setToolTip("\n".join(tooltip_rows))


class StepStrip(QWidget):
    """Horizontal strip containing previews for each multi-view step."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._placeholder = QLabel("Žiadne kroky")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._spacer = QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._layout.addWidget(self._placeholder)
        self._layout.addItem(self._spacer)

    def _clear_previews(self) -> None:
        for index in reversed(range(self._layout.count())):
            item = self._layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is None or widget is self._placeholder:
                continue
            self._layout.takeAt(index)
            widget.deleteLater()

    def clear(self) -> None:
        self._clear_previews()
        if self._layout.indexOf(self._placeholder) == -1:
            self._layout.insertWidget(0, self._placeholder)
        self._placeholder.show()

    def set_results(self, results: Sequence[Any]) -> None:
        self._clear_previews()
        if not results:
            if self._layout.indexOf(self._placeholder) == -1:
                self._layout.insertWidget(0, self._placeholder)
            self._placeholder.show()
            return

        if self._layout.indexOf(self._placeholder) != -1:
            self._layout.removeWidget(self._placeholder)
        self._placeholder.hide()

        for index, entry in enumerate(results, start=1):
            verdict = None
            frame = None
            metrics: Mapping[str, Any] | None = None
            status = "nok"
            name = "Step"

            if hasattr(entry, "verdict"):
                verdict = getattr(entry, "verdict")
                frame = getattr(entry, "frame", None)
            elif isinstance(entry, Mapping) and "verdict" in entry:
                verdict = entry["verdict"]
                frame = entry.get("frame")
            elif isinstance(entry, Mapping):
                verdict = entry
                frame = entry.get("frame")

            if verdict is not None:
                name = str(getattr(verdict, "name", verdict.get("name", name)))
                status = str(getattr(verdict, "status", verdict.get("status", status)))
                metrics = getattr(verdict, "metrics", verdict.get("metrics"))
            preview = _StepPreview(
                index,
                name,
                status,
                frame if isinstance(frame, np.ndarray) else None,
                metrics,
            )
            insert_at = max(0, self._layout.count() - 1)
            self._layout.insertWidget(insert_at, preview)


__all__ = ["StepStrip"]

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class PresenceV2SampleCaptureDialog(QDialog):
    def __init__(
        self,
        *,
        title: str,
        capture_fn: Callable[[], np.ndarray | None],
        crop_fn: Callable[[np.ndarray], np.ndarray | None],
        default_mode: str = "manual",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 640)
        self._capture_fn = capture_fn
        self._crop_fn = crop_fn
        self._samples: list[np.ndarray] = []
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._capture_once)

        root = QVBoxLayout(self)
        cfg = QFormLayout()
        self._mode = QComboBox(self)
        self._mode.addItem("Manuálne", "manual")
        self._mode.addItem("Automaticky", "auto")
        idx = self._mode.findData(default_mode)
        self._mode.setCurrentIndex(max(0, idx))
        cfg.addRow("Režim", self._mode)

        self._target = QSpinBox(self)
        self._target.setRange(1, 500)
        self._target.setValue(15)
        cfg.addRow("Cieľový počet", self._target)

        self._interval = QSpinBox(self)
        self._interval.setRange(50, 10000)
        self._interval.setValue(500)
        self._interval.setSuffix(" ms")
        cfg.addRow("Interval", self._interval)
        root.addLayout(cfg)

        controls = QHBoxLayout()
        self._btn_capture = QPushButton("Zachytiť snímku", self)
        self._btn_capture.clicked.connect(self._capture_once)
        self._btn_start = QPushButton("Spustiť automatický zber", self)
        self._btn_start.clicked.connect(self._start_auto)
        self._btn_stop = QPushButton("Zastaviť", self)
        self._btn_stop.clicked.connect(self._stop_auto)
        self._btn_delete = QPushButton("Zmazať vybraný thumbnail", self)
        self._btn_delete.clicked.connect(self._delete_selected)
        controls.addWidget(self._btn_capture)
        controls.addWidget(self._btn_start)
        controls.addWidget(self._btn_stop)
        controls.addWidget(self._btn_delete)
        controls.addStretch(1)
        root.addLayout(controls)

        body = QHBoxLayout()
        self._preview = QLabel("Náhľad vzorky", self)
        self._preview.setMinimumSize(580, 420)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet("background:#111;color:#999;border:1px solid #444;")
        body.addWidget(self._preview, 2)

        self._thumbs = QListWidget(self)
        self._thumbs.currentRowChanged.connect(self._on_select)
        body.addWidget(self._thumbs, 1)
        root.addLayout(body, 1)

        self._counter = QLabel("Počet snímok: 0", self)
        root.addWidget(self._counter)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

    def samples(self) -> list[np.ndarray]:
        return [np.asarray(s).copy() for s in self._samples]

    def _start_auto(self) -> None:
        self._auto_timer.start(int(self._interval.value()))

    def _stop_auto(self) -> None:
        self._auto_timer.stop()

    def _capture_once(self) -> None:
        frame = self._capture_fn()
        if frame is None:
            QMessageBox.warning(self, "Capture", "Capture zlyhal – frame nie je dostupný.")
            return
        cropped = self._crop_fn(frame)
        if cropped is None:
            QMessageBox.warning(self, "Capture", "ROI nie je definované alebo je neplatné.")
            return
        if self._samples and np.asarray(cropped).shape != np.asarray(self._samples[0]).shape:
            QMessageBox.warning(self, "Capture", "Veľkosť vzorky sa nezhoduje s existujúcimi vzorkami.")
            return
        self._samples.append(np.asarray(cropped).copy())
        item = QListWidgetItem(f"#{len(self._samples)}")
        self._thumbs.addItem(item)
        self._thumbs.setCurrentRow(self._thumbs.count() - 1)
        self._counter.setText(f"Počet snímok: {len(self._samples)}")
        if len(self._samples) >= int(self._target.value()):
            self._stop_auto()

    def _delete_selected(self) -> None:
        row = self._thumbs.currentRow()
        if row < 0:
            return
        self._thumbs.takeItem(row)
        self._samples.pop(row)
        self._counter.setText(f"Počet snímok: {len(self._samples)}")
        if self._thumbs.count() > 0:
            self._thumbs.setCurrentRow(max(0, min(row, self._thumbs.count() - 1)))
        else:
            self._preview.setText("Náhľad vzorky")

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self._samples):
            return
        img = np.asarray(self._samples[row])
        if img.ndim == 2:
            qimg = QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format_Grayscale8)
        else:
            qimg = QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg.copy())
        self._preview.setPixmap(pix.scaled(self._preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_auto()
        super().closeEvent(event)

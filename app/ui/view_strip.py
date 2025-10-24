from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

_STATUS_COLORS = {
    "ok": "#33dd66",
    "nok": "#ff3366",
    "warn": "#e67e22",
}


def _ndarray_to_qimage(image: np.ndarray) -> Optional[QImage]:
    if image is None:
        return None
    try:
        arr = np.asarray(image)
    except Exception:
        return None
    if arr.ndim == 3:
        if arr.shape[2] == 3:
            fmt = QImage.Format_RGB888
            qimg = QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], fmt)
            return qimg.rgbSwapped()
        arr = arr[:, :, 0]
    if arr.ndim != 2:
        return None
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8, copy=False)
    return QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], QImage.Format_Grayscale8)


@dataclass(slots=True)
class ViewItemState:
    view_id: str
    name: str
    status: str | None = None
    pixmap: QPixmap | None = None


class _ViewStripItem(QFrame):
    clicked = Signal(str)

    def __init__(self, state: ViewItemState, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setObjectName("viewStripItem")
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setStyleSheet("#viewStripItem{border:1px solid #333; border-radius:6px; background:#111}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._thumb = QLabel(self)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setFixedSize(120, 80)
        self._thumb.setObjectName("viewThumb")
        self._thumb.setStyleSheet(
            "#viewThumb{border:1px solid #444; border-radius:4px; background:#181818;}"
        )
        layout.addWidget(self._thumb)

        info = QHBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)

        self._name = QLabel(state.name, self)
        self._name.setStyleSheet("color:#ddd; font-weight:bold;")
        info.addWidget(self._name, 1)

        self._badge = QLabel("—", self)
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setFixedWidth(46)
        self._badge.setStyleSheet("border-radius:4px; padding:2px 4px; background:#444; color:#eee;")
        info.addWidget(self._badge)

        layout.addLayout(info)

        self.set_active(False)
        self.update_state(state)

    @property
    def view_id(self) -> str:
        return self._state.view_id

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self._state.view_id)
        super().mousePressEvent(event)

    def update_state(self, state: ViewItemState) -> None:
        self._state = state
        if state.pixmap is not None:
            scaled = state.pixmap.scaled(
                self._thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._thumb.setPixmap(scaled)
            self._thumb.setText("")
        else:
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("—")

        status = (state.status or "").lower()
        text = status.upper() if status else "—"
        color = _STATUS_COLORS.get(status, "#555555")
        self._badge.setText(text)
        self._badge.setStyleSheet(
            f"border-radius:4px; padding:2px 4px; background:{color}; color:#000; font-weight:bold;"
        )

    def set_active(self, active: bool) -> None:
        if active:
            self.setStyleSheet(
                "#viewStripItem{border:2px solid #33aaff; border-radius:6px; background:#151515;}"
            )
        else:
            self.setStyleSheet(
                "#viewStripItem{border:1px solid #333; border-radius:6px; background:#111;}"
            )


class ViewStrip(QWidget):
    view_selected = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._items: dict[str, _ViewStripItem] = {}
        self._states: dict[str, ViewItemState] = {}
        self._active_view: Optional[str] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._layout = layout

    def set_views(self, views: list[tuple[str, str]]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._items.clear()
        self._states.clear()

        for view_id, name in views:
            state = ViewItemState(view_id=view_id, name=name, status=None, pixmap=None)
            widget = _ViewStripItem(state, self)
            widget.clicked.connect(self._on_item_clicked)
            self._layout.addWidget(widget)
            self._items[view_id] = widget
            self._states[view_id] = state

        self._layout.addStretch(1)

        if views:
            self.set_active_view(views[0][0])
        else:
            self._active_view = None

    def _on_item_clicked(self, view_id: str) -> None:
        self.set_active_view(view_id)
        self.view_selected.emit(view_id)

    def set_active_view(self, view_id: str | None) -> None:
        if view_id is None or view_id not in self._items:
            self._active_view = None
            return
        self._active_view = view_id
        for vid, item in self._items.items():
            item.set_active(vid == view_id)

    def update_snapshot(self, view_id: str, image: Optional[np.ndarray]) -> None:
        if view_id not in self._items:
            return
        pixmap: Optional[QPixmap] = None
        qimg = _ndarray_to_qimage(image) if image is not None else None
        if qimg is not None:
            pixmap = QPixmap.fromImage(qimg)
        state = self._states.get(view_id)
        if state is None:
            state = ViewItemState(view_id=view_id, name=view_id, status=None, pixmap=pixmap)
        else:
            state.pixmap = pixmap
        self._states[view_id] = state
        self._items[view_id].update_state(state)

    def update_status(self, view_id: str, status: Optional[str]) -> None:
        if view_id not in self._items:
            return
        state = self._states.get(view_id)
        if state is None:
            state = ViewItemState(view_id=view_id, name=view_id, status=status, pixmap=None)
        else:
            state.status = status
        self._states[view_id] = state
        self._items[view_id].update_state(state)

    def current_view(self) -> Optional[str]:
        return self._active_view

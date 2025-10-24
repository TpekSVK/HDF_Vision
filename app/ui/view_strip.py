from __future__ import annotations

from typing import Callable, Iterable, Mapping, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


_STATUS_STYLES = {
    "ok": "background-color:#1f7a39; color:#ffffff;",
    "warn": "background-color:#c97b10; color:#ffffff;",
    "nok": "background-color:#b22222; color:#ffffff;",
}


class _ViewItem(QFrame):
    def __init__(
        self,
        view_id: str,
        name: str,
        *,
        on_click: Callable[[str], None],
    ) -> None:
        super().__init__()
        self.view_id = view_id
        self._on_click = on_click
        self.setObjectName("viewStripItem")
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Plain)
        self.setStyleSheet(
            "#viewStripItem{border:1px solid #333;border-radius:6px;background-color:#202020;}"
        )
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.thumbnail = QLabel(self)
        self.thumbnail.setFixedSize(96, 72)
        self.thumbnail.setStyleSheet("border:1px solid #2a2a2a; background:#111;")
        self.thumbnail.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.thumbnail)

        self.status = QLabel("—", self)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(
            "border-radius:10px;padding:2px 6px;background-color:#444;color:#ddd;"
        )
        layout.addWidget(self.status, alignment=Qt.AlignCenter)

        self.label = QLabel(name, self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        layout.addStretch(1)

    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton:
            self._on_click(self.view_id)
        return super().mousePressEvent(event)

    def set_active(self, active: bool) -> None:
        if active:
            self.setStyleSheet(
                "#viewStripItem{border:2px solid #4a90e2;border-radius:6px;background-color:#282828;}"
            )
        else:
            self.setStyleSheet(
                "#viewStripItem{border:1px solid #333;border-radius:6px;background-color:#202020;}"
            )

    def set_status(self, status: Optional[str]) -> None:
        if not status:
            self.status.setText("—")
            self.status.setStyleSheet(
                "border-radius:10px;padding:2px 6px;background-color:#444;color:#ddd;"
            )
            return
        normalized = str(status).strip().lower()
        self.status.setText(normalized.upper())
        style = _STATUS_STYLES.get(normalized, "background-color:#444;color:#ddd;")
        self.status.setStyleSheet(
            f"border-radius:10px;padding:2px 6px;{style}"
        )

    def set_thumbnail(self, pixmap: Optional[QPixmap]) -> None:
        if pixmap is None or pixmap.isNull():
            self.thumbnail.clear()
            self.thumbnail.setText("—")
        else:
            scaled = pixmap.scaled(
                self.thumbnail.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.thumbnail.setPixmap(scaled)
            self.thumbnail.setText("")


class ViewStrip(QWidget):
    def __init__(
        self,
        *,
        parent: QWidget | None = None,
        on_view_selected: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_view_selected = on_view_selected or (lambda _vid: None)
        self._items: dict[str, _ViewItem] = {}
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._stretch = QWidget(self)
        self._stretch.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._layout.addWidget(self._stretch)

    def set_views(
        self,
        views: Iterable[Mapping[str, object] | object],
        *,
        thumbnail_loader: Callable[[object], Optional[QPixmap]] | None = None,
    ) -> None:
        for item in list(self._items.values()):
            self._layout.removeWidget(item)
            item.deleteLater()
        self._items.clear()
        if self._stretch is not None:
            self._layout.removeWidget(self._stretch)
        self._stretch = QWidget(self)
        self._stretch.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        for entry in views:
            if isinstance(entry, Mapping):
                view_id = str(entry.get("id", ""))
                name = str(entry.get("name", view_id or "View"))
                source = entry
            else:
                view_id = str(getattr(entry, "id", ""))
                name = str(getattr(entry, "name", view_id or "View"))
                source = entry
            if not view_id:
                continue
            item = _ViewItem(view_id, name, on_click=self._handle_click)
            item.set_status(None)
            if thumbnail_loader is not None:
                try:
                    pixmap = thumbnail_loader(source)
                except Exception:
                    pixmap = None
                item.set_thumbnail(pixmap)
            self._layout.addWidget(item)
            self._items[view_id] = item

        self._layout.addWidget(self._stretch)

    def _handle_click(self, view_id: str) -> None:
        self._on_view_selected(view_id)

    def set_active(self, view_id: str | None) -> None:
        for vid, item in self._items.items():
            item.set_active(vid == view_id)

    def set_status(self, view_id: str, status: Optional[str]) -> None:
        item = self._items.get(view_id)
        if item is not None:
            item.set_status(status)

    def set_thumbnail(self, view_id: str, pixmap: Optional[QPixmap]) -> None:
        item = self._items.get(view_id)
        if item is not None:
            item.set_thumbnail(pixmap)

    def has_view(self, view_id: str) -> bool:
        return view_id in self._items

    def view_ids(self) -> list[str]:
        return list(self._items.keys())

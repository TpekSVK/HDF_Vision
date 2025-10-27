"""Tool catalog dialog for the Golden Wizard."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QWidget,
    QVBoxLayout,
)


class ToolCatalogDialog(QDialog):
    """Dialog allowing users to pick a tool from the registry."""

    def __init__(self, tool_service, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tool catalog")
        self._tool_service = tool_service
        self._selected_type: str | None = None

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter tools…")
        self._filter.textChanged.connect(self._apply_filter)

        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._filter)
        layout.addWidget(self._list)
        layout.addWidget(buttons)

        self._entries: list[tuple[str, str, str]] = []
        self._populate_entries()
        self._apply_filter("")

    def _populate_entries(self) -> None:
        self._entries.clear()
        for tool_type in self._tool_service.list_tool_types():
            try:
                meta = self._tool_service.get_tool_meta(tool_type)
                display = f"{getattr(meta, 'name', tool_type)} ({tool_type})"
                tooltip = getattr(meta, "description", tool_type)
            except KeyError:
                display = tool_type
                tooltip = tool_type
            self._entries.append((tool_type, display, tooltip))

    def _apply_filter(self, text: str) -> None:
        pattern = (text or "").strip().lower()
        self._list.clear()
        for tool_type, display, tooltip in self._entries:
            if pattern and pattern not in display.lower() and pattern not in tool_type.lower():
                continue
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, tool_type)
            item.setToolTip(tooltip)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        self._selected_type = item.data(Qt.UserRole)
        super().accept()

    def accept(self) -> None:  # noqa: N802 - Qt API
        current = self._list.currentItem()
        if current is None:
            return
        self._selected_type = current.data(Qt.UserRole)
        super().accept()

    def selected_type(self) -> str | None:
        return self._selected_type


__all__ = ["ToolCatalogDialog"]

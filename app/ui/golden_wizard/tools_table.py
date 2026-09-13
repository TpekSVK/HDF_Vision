"""Reorderable table of recipe tools."""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTableWidget, QAbstractItemView


class ToolsTableWidget(QTableWidget):
    """Table with internal drag & drop row reordering support."""

    rowsReordered = Signal(list)

    def dropEvent(self, event):  # type: ignore[override]
        if event.source() is not self or self.dragDropMode() != QAbstractItemView.InternalMove:
            super().dropEvent(event)
            return

        current_order = self._collect_row_ids()
        if not current_order:
            event.ignore()
            return

        selection = self.selectionModel()
        if selection is None:
            event.ignore()
            return

        selected_rows = sorted({index.row() for index in selection.selectedRows()})
        if not selected_rows:
            event.ignore()
            return

        drop_row = self.rowAt(int(event.position().y())) if hasattr(event, "position") else self.rowAt(event.pos().y())
        if drop_row < 0:
            drop_row = self.rowCount()

        # Removing rows shifts the target; account for rows dragged from above.
        insert_at = drop_row
        for row in selected_rows:
            if row < drop_row:
                insert_at -= 1
        insert_at = max(0, min(insert_at, len(current_order)))

        moving_ids = [current_order[row] for row in selected_rows]
        remaining_ids = [tool_id for idx, tool_id in enumerate(current_order) if idx not in selected_rows]
        for offset, tool_id in enumerate(moving_ids):
            remaining_ids.insert(insert_at + offset, tool_id)

        if remaining_ids != current_order:
            self.rowsReordered.emit(remaining_ids)
        event.acceptProposedAction()

    def _collect_row_ids(self) -> list[int]:
        ids: list[int] = []
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is None:
                continue
            data = item.data(Qt.UserRole)
            if data is None:
                continue
            try:
                ids.append(int(data))
            except (TypeError, ValueError):  # pragma: no cover - defensive fallback
                continue
        return ids

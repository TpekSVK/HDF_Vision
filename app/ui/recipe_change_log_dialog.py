"""Recipe audit browser for SETUP."""

from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from app.services.recipe_audit_service import RecipeAuditService


FIELD_LABELS = {
    "camera_profile.exposure_us": "Exposure [us]",
    "camera_profile.gain": "Gain",
    "external_request_input": "Externý vstup",
    "settle_ms": "Settle Time [ms]",
    "image_rotation": "Rotácia",
    "name": "Názov",
}
TYPE_LABELS = {name: name.title() for name in (
    "RECIPE", "VIEW", "CAMERA", "TRIGGER", "ROI", "LOCATOR", "IGNORE",
    "TOOL", "GOLDEN", "VALIDATION",
)}


def _display_json(raw: str | None, *, pretty: bool = False) -> str:
    if raw is None:
        return ""
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return str(raw)
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))


class RecipeChangeDetailDialog(QDialog):
    def __init__(self, event: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Detail zmeny")
        self.resize(720, 600)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        ts = datetime.fromtimestamp(event["ts_ms"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
        values = (("Čas", ts), ("Recept", event.get("recipe_name")),
                  ("View", event.get("view_name")), ("Typ", event.get("entity_type")),
                  ("Akcia", event.get("action")), ("Objekt", event.get("entity_name")),
                  ("Parameter", FIELD_LABELS.get(event.get("field_name"), event.get("field_name"))),
                  ("Zdroj", event.get("source")))
        for label, value in values:
            form.addRow(label + ":", QLabel(str(value or "—")))
        layout.addLayout(form)
        for label, key in (("Pôvodná hodnota", "old_value_json"), ("Nová hodnota", "new_value_json")):
            layout.addWidget(QLabel(label))
            edit = QTextEdit(_display_json(event.get(key), pretty=True))
            edit.setReadOnly(True)
            layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class RecipeChangeLogDialog(QDialog):
    COLUMNS = ("Čas", "Recept", "View", "Typ", "Objekt", "Parameter", "Z", "Na")

    def __init__(self, audit: RecipeAuditService, parent=None):
        super().__init__(parent)
        self.audit = audit
        self._events: list[dict] = []
        self.setWindowTitle("Záznam zmien")
        self.resize(1180, 700)
        root = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.recipe_filter = QComboBox(); self.view_filter = QComboBox(); self.type_filter = QComboBox()
        self.search = QLineEdit(); self.search.setPlaceholderText("Hľadať…")
        for label, widget in (("Recept:", self.recipe_filter), ("View:", self.view_filter),
                              ("Typ zmeny:", self.type_filter), ("Hľadať:", self.search)):
            filters.addWidget(QLabel(label)); filters.addWidget(widget)
        self.reset_button = QPushButton("Zrušiť filtre")
        self.refresh_button = QPushButton("Obnoviť")
        filters.addWidget(self.reset_button); filters.addWidget(self.refresh_button)
        root.addLayout(filters)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table)
        self.detail_button = QPushButton("Detail")
        root.addWidget(self.detail_button, 0, Qt.AlignRight)
        self._populate_filters()
        self.recipe_filter.currentIndexChanged.connect(self._recipe_changed)
        self.view_filter.currentIndexChanged.connect(self.refresh)
        self.type_filter.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.reset_button.clicked.connect(self.reset_filters)
        self.refresh_button.clicked.connect(self.refresh)
        self.detail_button.clicked.connect(self.show_detail)
        self.table.cellDoubleClicked.connect(lambda *_: self.show_detail())
        self.refresh()

    def _populate_filters(self):
        self.recipe_filter.addItem("Všetky", None)
        for name in self.audit.distinct_recipe_names(): self.recipe_filter.addItem(name, name)
        self.type_filter.addItem("Všetky", None)
        for value, label in TYPE_LABELS.items(): self.type_filter.addItem(label, value)
        self._populate_views()

    def _populate_views(self):
        selected = self.view_filter.currentData()
        self.view_filter.blockSignals(True); self.view_filter.clear(); self.view_filter.addItem("Všetky", None)
        for view_id, view_name in self.audit.distinct_views(self.recipe_filter.currentData()):
            self.view_filter.addItem(view_name, (view_id, view_name))
        index = self.view_filter.findData(selected)
        if index >= 0: self.view_filter.setCurrentIndex(index)
        self.view_filter.blockSignals(False)

    def _recipe_changed(self):
        self._populate_views(); self.refresh()

    def refresh(self):
        view_data = self.view_filter.currentData()
        self._events = self.audit.list_changes(
            recipe_name=self.recipe_filter.currentData(),
            view_id=view_data[0] if view_data and view_data[0] else None,
            view_name=view_data[1] if view_data and not view_data[0] else None,
            entity_type=self.type_filter.currentData(), search=self.search.text())
        self.table.setRowCount(len(self._events))
        for row, event in enumerate(self._events):
            stamp = datetime.fromtimestamp(event["ts_ms"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            old, new = _display_json(event.get("old_value_json")), _display_json(event.get("new_value_json"))
            values = (stamp, event.get("recipe_name"), event.get("view_name"),
                      TYPE_LABELS.get(event.get("entity_type"), event.get("entity_type")),
                      event.get("entity_name"), FIELD_LABELS.get(event.get("field_name"), event.get("field_name")),
                      self._short(old), self._short(new))
            for column, value in enumerate(values): self.table.setItem(row, column, QTableWidgetItem(str(value or "")))
        self.table.scrollToBottom()

    @staticmethod
    def _short(value: str, limit: int = 80) -> str:
        return value if len(value) <= limit else value[:limit - 3] + "..."

    def reset_filters(self):
        self.recipe_filter.setCurrentIndex(0); self.view_filter.setCurrentIndex(0)
        self.type_filter.setCurrentIndex(0); self.search.clear(); self.refresh()

    def show_detail(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._events): RecipeChangeDetailDialog(self._events[row], self).exec()

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.gpio_service import GPIOService, PinDefinition


@dataclass
class _PinRow:
    definition: PinDefinition
    combo: QComboBox | None


class GPIOWizard(QDialog):
    """Modal dialog used to configure Jetson GPIO input/output roles."""

    def __init__(self, gpio: GPIOService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GPIO Wizard")
        self._gpio = gpio
        self._rows: list[_PinRow] = []
        self._role_labels = gpio.available_roles()

        self._init_ui()
        self._load_assignments()

    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        status = QLabel()
        status.setWordWrap(True)
        recipe = self._gpio.active_recipe()
        if self._gpio.is_hardware_ready():
            status.setText(
                f"Jetson.GPIO driver je dostupný. Konfigurácia pre recept '{recipe}' sa uloží do /data/gpio_config.json."
            )
        else:
            status.setText(
                "Jetson.GPIO knižnica nebola nájdená – používam simulovaný režim."
                f" Mapovanie pre recept '{recipe}' je možné pripraviť a po prenesení na Jetson sa použije."
            )
        layout.addWidget(status)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        container = QWidget(scroll)
        scroll.setWidget(container)

        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        header_font = self.font()
        header_font.setBold(True)

        headers = ["Pin", "Signál", "Popis", "Priradenie"]
        for col, text in enumerate(headers):
            label = QLabel(text)
            label.setFont(header_font)
            grid.addWidget(label, 0, col)

        pins = self._gpio.list_pins()
        for row, definition in enumerate(pins, start=1):
            lbl_pin = QLabel(str(definition.physical))
            lbl_pin.setAlignment(Qt.AlignCenter)
            grid.addWidget(lbl_pin, row, 0)

            lbl_name = QLabel(definition.label)
            grid.addWidget(lbl_name, row, 1)

            lbl_desc = QLabel(definition.description)
            lbl_desc.setWordWrap(True)
            grid.addWidget(lbl_desc, row, 2)

            if definition.is_gpio:
                combo = QComboBox()
                self._populate_combo(combo)
                grid.addWidget(combo, row, 3)
                self._rows.append(_PinRow(definition, combo))
            else:
                label = QLabel("Vyhradené")
                label.setAlignment(Qt.AlignCenter)
                grid.addWidget(label, row, 3)
                self._rows.append(_PinRow(definition, None))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate_combo(self, combo: QComboBox) -> None:
        combo.clear()
        for role, label in self._role_labels.items():
            combo.addItem(label, role)

    def _load_assignments(self) -> None:
        assignments = self._gpio.get_assignments()
        for row in self._rows:
            if row.combo is None:
                continue
            current_role = assignments.get(row.definition.physical, "none")
            index = row.combo.findData(current_role)
            if index < 0:
                index = row.combo.findData("none")
            if index >= 0:
                row.combo.setCurrentIndex(index)

    # ------------------------------------------------------------------
    def accept(self) -> None:
        assignments: Dict[int, str] = {}
        for row in self._rows:
            if row.combo is None:
                continue
            role = row.combo.currentData()
            if isinstance(role, str):
                assignments[row.definition.physical] = role
        self._gpio.update_assignments(assignments)
        super().accept()

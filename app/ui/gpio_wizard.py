from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.gpio_service import GPIOService, PinDefinition


@dataclass
class _PinRow:
    definition: PinDefinition
    combo: QComboBox | None


@dataclass
class _TestPinRow:
    definition: PinDefinition
    checkbox: QCheckBox
    status_label: QLabel


class GPIOWizard(QDialog):
    """Modal dialog used to configure Jetson GPIO input/output roles."""

    def __init__(self, gpio: GPIOService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GPIO Wizard")
        self._gpio = gpio
        self._rows: list[_PinRow] = []
        self._test_rows: list[_TestPinRow] = []
        self._role_labels = gpio.available_roles()
        self._pins: List[PinDefinition] = gpio.list_pins()
        self._pin_lookup: dict[int, PinDefinition] = {pin.physical: pin for pin in self._pins}

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._update_pin_statuses)

        self._init_ui()
        self._load_assignments()
        self._update_pin_statuses()
        if self._test_rows:
            self._status_timer.start()

    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        tabs = QTabWidget(self)
        tabs.addTab(self._create_config_tab(tabs), "Konfigurácia")
        tabs.addTab(self._create_test_tab(tabs), "Test výstupov")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_config_tab(self, parent: QWidget) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
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

        scroll = QScrollArea(widget)
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

        pins = self._pins
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

        return widget

    def _create_test_tab(self, parent: QWidget) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        info = QLabel(
            "Vyberte piny nakonfigurované ako výstupné signály a odošlite krátky impulz."
            " Stav pinov sa obnovuje automaticky."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        outputs = self._gpio.configured_output_pins()
        if not outputs:
            empty = QLabel("Žiadne piny nie sú nakonfigurované ako výstup.")
            empty.setAlignment(Qt.AlignCenter)
            layout.addWidget(empty, 1)
        else:
            scroll = QScrollArea(widget)
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

            headers = ["", "Pin", "Signál", "Popis", "Rola", "Stav"]
            for col, text in enumerate(headers):
                label = QLabel(text)
                label.setFont(header_font)
                if col == 0:
                    label.setAlignment(Qt.AlignCenter)
                grid.addWidget(label, 0, col)

            for row_index, pin in enumerate(sorted(outputs), start=1):
                definition = self._pin_lookup.get(pin)
                if definition is None:
                    continue
                checkbox = QCheckBox()
                checkbox.setToolTip("Odošle impulz na vybraný pin")
                grid.addWidget(checkbox, row_index, 0, alignment=Qt.AlignCenter)

                lbl_pin = QLabel(str(definition.physical))
                lbl_pin.setAlignment(Qt.AlignCenter)
                grid.addWidget(lbl_pin, row_index, 1)

                lbl_name = QLabel(definition.label)
                grid.addWidget(lbl_name, row_index, 2)

                lbl_desc = QLabel(definition.description)
                lbl_desc.setWordWrap(True)
                grid.addWidget(lbl_desc, row_index, 3)

                lbl_role = QLabel(self._role_labels.get(outputs[pin], outputs[pin]))
                grid.addWidget(lbl_role, row_index, 4)

                status_label = QLabel("?")
                status_label.setAlignment(Qt.AlignCenter)
                grid.addWidget(status_label, row_index, 5)

                self._test_rows.append(
                    _TestPinRow(definition=definition, checkbox=checkbox, status_label=status_label)
                )

        controls = QHBoxLayout()
        controls.addStretch(1)
        lbl_duration = QLabel("Dĺžka impulzu (ms):", widget)
        controls.addWidget(lbl_duration)

        self._duration_spin = QSpinBox(widget)
        self._duration_spin.setRange(10, 5000)
        self._duration_spin.setSingleStep(10)
        self._duration_spin.setValue(200)
        self._duration_spin.setSuffix(" ms")
        controls.addWidget(self._duration_spin)

        self._btn_send_signal = QPushButton("Odoslať impulz", widget)
        self._btn_send_signal.clicked.connect(self._handle_send_signal)
        self._btn_send_signal.setEnabled(bool(self._test_rows))
        controls.addWidget(self._btn_send_signal)
        layout.addLayout(controls)

        return widget

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

    def _handle_send_signal(self) -> None:
        pins = [row.definition.physical for row in self._test_rows if row.checkbox.isChecked()]
        if not pins:
            return
        pulse_seconds = max(self._duration_spin.value(), 10) / 1000.0
        self._gpio.pulse_outputs(pins, pulse_seconds=pulse_seconds)
        delay_ms = max(300, int(self._duration_spin.value() * 1.5))
        QTimer.singleShot(delay_ms, self._update_pin_statuses)

    def _update_pin_statuses(self) -> None:
        if not self._test_rows:
            return
        pins = [row.definition.physical for row in self._test_rows]
        states = self._gpio.read_pin_states(pins)
        for row in self._test_rows:
            state = states.get(row.definition.physical, False)
            row.status_label.setText("HIGH" if state else "LOW")

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

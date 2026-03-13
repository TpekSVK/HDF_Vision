from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
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
    combo: QComboBox


@dataclass
class _TestPinRow:
    definition: PinDefinition
    checkbox: QCheckBox
    status_label: QLabel


@dataclass
class _MonitorRow:
    definition: PinDefinition
    checkbox: QCheckBox


class GPIOWizard(QDialog):
    """Modal dialog used to configure Jetson GPIO input/output roles."""

    def __init__(self, gpio: GPIOService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sprievodca GPIO")
        self._gpio = gpio
        self._rows: list[_PinRow] = []
        self._test_rows: list[_TestPinRow] = []
        self._monitor_rows: list[_MonitorRow] = []
        self._role_labels = gpio.available_roles()
        self._pins: List[PinDefinition] = gpio.list_pins()
        self._pin_lookup: dict[int, PinDefinition] = {pin.physical: pin for pin in self._pins}
        self._capabilities = gpio.pin_capabilities()
        self._output_definitions = self._resolve_definitions(
            pin for pin, caps in self._capabilities.items() if "output" in caps and "input" not in caps
        )
        self._input_definitions = self._resolve_definitions(
            pin for pin, caps in self._capabilities.items() if "input" in caps and "output" not in caps
        )
        self._bidirectional_definitions = self._resolve_definitions(
            pin for pin, caps in self._capabilities.items() if "input" in caps and "output" in caps
        )
        self._output_role_options = self._build_role_options(gpio.output_roles())
        self._input_role_options = self._build_role_options(gpio.input_roles())
        combined_roles = list(dict.fromkeys(gpio.output_roles() + gpio.input_roles()))
        self._bidirectional_role_options = self._build_role_options(tuple(combined_roles))

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._update_pin_statuses)

        self._init_ui()
        self._load_assignments()
        self._update_pin_statuses()
        if self._test_rows or self._monitor_rows:
            self._status_timer.start()

    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        tabs = QTabWidget(self)
        tabs.addTab(self._create_config_tab(tabs), "Konfigurácia")
        tabs.addTab(self._create_test_tab(tabs), "Test výstupov")
        tabs.addTab(self._create_monitor_tab(tabs), "I/O monitor")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _resolve_definitions(self, pins: Iterable[int]) -> list[PinDefinition]:
        result: list[PinDefinition] = []
        for pin in sorted({int(p) for p in pins}):
            definition = self._pin_lookup.get(pin)
            if definition and definition.is_gpio:
                result.append(definition)
        return result

    def _build_role_options(self, roles: Iterable[str]) -> tuple[str, ...]:
        options: list[str] = ["none"]
        for role in roles:
            if role not in options:
                options.append(role)
        return tuple(options)

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

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(12)

        output_column = self._build_pin_column(
            widget,
            "Výstupné piny",
            [("Výstupné GPIO", self._output_definitions, self._output_role_options)],
        )
        columns.addWidget(output_column, 1)

        input_groups: list[tuple[str, list[PinDefinition], tuple[str, ...]]] = []
        if self._input_definitions:
            input_groups.append(("Vstupné GPIO", self._input_definitions, self._input_role_options))
        if self._bidirectional_definitions:
            input_groups.append(("Obojsmerné GPIO", self._bidirectional_definitions, self._bidirectional_role_options))
        input_column = self._build_pin_column(widget, "Vstupné piny", input_groups)
        columns.addWidget(input_column, 1)

        layout.addLayout(columns, 1)

        return widget

    def _build_pin_column(
        self,
        parent: QWidget,
        title: str,
        groups: list[tuple[str, list[PinDefinition], tuple[str, ...]]],
    ) -> QScrollArea:
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)

        container = QWidget(scroll)
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QLabel(title, container)
        header_font = header.font()
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        if groups:
            for group_title, definitions, role_options in groups:
                if not definitions:
                    continue
                layout.addWidget(
                    self._create_pin_group_box(container, group_title, definitions, role_options)
                )
        else:
            placeholder = QLabel("Žiadne piny nie sú dostupné pre túto kategóriu.", container)
            placeholder.setAlignment(Qt.AlignCenter)
            layout.addWidget(placeholder)

        layout.addStretch(1)
        return scroll

    def _create_pin_group_box(
        self,
        parent: QWidget,
        title: str,
        definitions: list[PinDefinition],
        role_options: tuple[str, ...],
    ) -> QGroupBox:
        box = QGroupBox(title, parent)
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        header_font = box.font()
        header_font.setBold(True)

        headers = ("Pin", "Signál", "Popis", "Priradenie")
        for col, text in enumerate(headers):
            label = QLabel(text, box)
            label.setFont(header_font)
            if col == 0:
                label.setAlignment(Qt.AlignCenter)
            grid.addWidget(label, 0, col)

        for row_index, definition in enumerate(definitions, start=1):
            lbl_pin = QLabel(str(definition.physical), box)
            lbl_pin.setAlignment(Qt.AlignCenter)
            grid.addWidget(lbl_pin, row_index, 0)

            grid.addWidget(QLabel(definition.label, box), row_index, 1)

            lbl_desc = QLabel(definition.description, box)
            lbl_desc.setWordWrap(True)
            grid.addWidget(lbl_desc, row_index, 2)

            combo = QComboBox(box)
            self._populate_combo(combo, role_options)
            grid.addWidget(combo, row_index, 3)
            self._rows.append(_PinRow(definition, combo))

        return box

    def _create_test_tab(self, parent: QWidget) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        info = QLabel(
            "Vyberte piny nakonfigurované ako výstupné signály, odošlite krátky impulz alebo ich"
            " nastavte na logickú úroveň. Stav pinov sa obnovuje automaticky."
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

        self._btn_set_high = QPushButton("Nastaviť HIGH", widget)
        self._btn_set_high.clicked.connect(self._handle_set_high)
        self._btn_set_high.setEnabled(bool(self._test_rows))
        controls.addWidget(self._btn_set_high)

        self._btn_set_low = QPushButton("Nastaviť LOW", widget)
        self._btn_set_low.clicked.connect(self._handle_set_low)
        self._btn_set_low.setEnabled(bool(self._test_rows))
        controls.addWidget(self._btn_set_low)
        layout.addLayout(controls)

        return widget

    def _create_monitor_tab(self, parent: QWidget) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        info = QLabel(
            "Sledujte aktuálne logické úrovne na všetkých GPIO pinoch."
            " Stav je zobrazovaný ako zaškrtávacie políčko (HIGH = zaškrtnuté)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        gpio_pins = [definition for definition in self._pins if definition.is_gpio]
        if not gpio_pins:
            empty = QLabel("Nie sú dostupné žiadne GPIO piny na monitorovanie.")
            empty.setAlignment(Qt.AlignCenter)
            layout.addWidget(empty, 1)
            return widget

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

        headers = ("Pin", "Signál", "Popis", "Stav")
        for col, text in enumerate(headers):
            label = QLabel(text, container)
            label.setFont(header_font)
            if col in (0, 3):
                label.setAlignment(Qt.AlignCenter)
            grid.addWidget(label, 0, col)

        for row_index, definition in enumerate(gpio_pins, start=1):
            lbl_pin = QLabel(str(definition.physical), container)
            lbl_pin.setAlignment(Qt.AlignCenter)
            grid.addWidget(lbl_pin, row_index, 0)

            grid.addWidget(QLabel(definition.label, container), row_index, 1)

            lbl_desc = QLabel(definition.description, container)
            lbl_desc.setWordWrap(True)
            grid.addWidget(lbl_desc, row_index, 2)

            checkbox = QCheckBox(container)
            checkbox.setFocusPolicy(Qt.NoFocus)
            checkbox.setAttribute(Qt.WA_TransparentForMouseEvents)
            checkbox.setToolTip("Aktuálny stav pinu (iba na čítanie)")
            grid.addWidget(checkbox, row_index, 3, alignment=Qt.AlignCenter)

            self._monitor_rows.append(_MonitorRow(definition=definition, checkbox=checkbox))

        grid.setColumnStretch(2, 1)

        return widget

    def _populate_combo(self, combo: QComboBox, roles: Iterable[str]) -> None:
        combo.clear()
        for role in roles:
            label = self._role_labels.get(role, role)
            combo.addItem(label, role)

    def _load_assignments(self) -> None:
        assignments = self._gpio.get_assignments()
        for row in self._rows:
            current_role = assignments.get(row.definition.physical, "none")
            index = row.combo.findData(current_role)
            if index < 0:
                index = row.combo.findData("none")
            if index >= 0:
                row.combo.setCurrentIndex(index)

    def _handle_send_signal(self) -> None:
        pins = self._selected_test_pins()
        if not pins:
            return
        pulse_seconds = max(self._duration_spin.value(), 10) / 1000.0
        self._gpio.pulse_outputs(pins, pulse_seconds=pulse_seconds)
        delay_ms = max(300, int(self._duration_spin.value() * 1.5))
        QTimer.singleShot(delay_ms, self._update_pin_statuses)

    def _handle_set_high(self) -> None:
        self._handle_set_level(True)

    def _handle_set_low(self) -> None:
        self._handle_set_level(False)

    def _handle_set_level(self, high: bool) -> None:
        pins = self._selected_test_pins()
        if not pins:
            return
        self._gpio.set_outputs_level(pins, level=high)
        QTimer.singleShot(200, self._update_pin_statuses)

    def _update_pin_statuses(self) -> None:
        if not (self._test_rows or self._monitor_rows):
            return
        pins = {row.definition.physical for row in self._test_rows}
        pins.update(row.definition.physical for row in self._monitor_rows)
        if not pins:
            return
        states = self._gpio.read_pin_states(pins)
        for row in self._test_rows:
            state = states.get(row.definition.physical, False)
            row.status_label.setText("HIGH" if state else "LOW")
        for row in self._monitor_rows:
            state = states.get(row.definition.physical, False)
            block = row.checkbox.blockSignals(True)
            row.checkbox.setChecked(state)
            row.checkbox.blockSignals(block)

    def _selected_test_pins(self) -> list[int]:
        return [row.definition.physical for row in self._test_rows if row.checkbox.isChecked()]

    # ------------------------------------------------------------------
    def accept(self) -> None:
        assignments: Dict[int, str] = {}
        for row in self._rows:
            role = row.combo.currentData()
            if isinstance(role, str):
                assignments[row.definition.physical] = role
        self._gpio.update_assignments(assignments)
        super().accept()

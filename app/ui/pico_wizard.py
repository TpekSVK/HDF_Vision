"""Diagnostic and local input-enablement dialog for Raspberry Pi Pico."""

from __future__ import annotations

import re

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.pico_config_service import PicoConfigService
from app.services.pico_service import PicoService


class PicoWizard(QDialog):
    """Use the application's existing Pico connection for read-only diagnostics."""

    capture_received = Signal(int)

    def __init__(
        self,
        pico: PicoService,
        pico_config: PicoConfigService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sprievodca Raspberry Pi Pico")
        self._pico = pico
        self._pico_config = pico_config
        self._callback_registered = False
        self._input_states: dict[int, QLabel] = {}
        self._enabled_checks: dict[int, QCheckBox] = {}
        self._build_ui()
        self._load_config()
        self.capture_received.connect(self._show_capture)
        self._pico.register_trigger_callback(self._on_capture)
        self._callback_registered = True
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh_inputs)
        self._timer.start()
        self.refresh_device()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        device = QGroupBox("Stav zariadenia", self)
        device_grid = QGridLayout(device)
        self.lbl_connection = QLabel("Odpojené", device)
        self.lbl_port = QLabel("—", device)
        self.lbl_firmware = QLabel("—", device)
        for row, (title, value) in enumerate((
            ("Stav:", self.lbl_connection),
            ("Port:", self.lbl_port),
            ("Firmware:", self.lbl_firmware),
        )):
            device_grid.addWidget(QLabel(title, device), row, 0)
            device_grid.addWidget(value, row, 1)
        layout.addWidget(device)

        inputs = QGroupBox("Stav vstupov / Povolené externé vstupy", self)
        grid = QGridLayout(inputs)
        grid.addWidget(QLabel("Vstup", inputs), 0, 0)
        grid.addWidget(QLabel("Stav", inputs), 0, 1)
        grid.addWidget(QLabel("Povolený", inputs), 0, 2)
        for index in range(1, 9):
            state = QLabel("—", inputs)
            enabled = QCheckBox(inputs)
            self._input_states[index] = state
            self._enabled_checks[index] = enabled
            grid.addWidget(QLabel(f"IN{index}", inputs), index, 0)
            grid.addWidget(state, index, 1)
            grid.addWidget(enabled, index, 2)
        layout.addWidget(inputs)

        self.lbl_last_event = QLabel("Posledný event: —", self)
        layout.addWidget(self.lbl_last_event)
        self.txt_status = QPlainTextEdit(self)
        self.txt_status.setReadOnly(True)
        self.txt_status.setPlaceholderText("STATUS nie je dostupný")
        status_box = QGroupBox("STATUS (iba na čítanie)", self)
        status_layout = QVBoxLayout(status_box)
        status_layout.addWidget(self.txt_status)
        layout.addWidget(status_box, 1)

        actions = QHBoxLayout()
        refresh = QPushButton("Obnoviť", self)
        refresh.clicked.connect(self.refresh_device)
        actions.addWidget(refresh)
        actions.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Save).setText("Uložiť")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

    @staticmethod
    def parse_firmware(response: str) -> str | None:
        for line in str(response or "").splitlines():
            match = re.match(r"^\s*FIRMWARE\s+(.+?)\s*$", line, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def parse_inputs(response: str) -> dict[int, str]:
        states: dict[int, str] = {}
        for index, state in re.findall(r"\bIN([1-8])\s*=\s*([A-Za-z0-9_-]+)", str(response or ""), re.IGNORECASE):
            states[int(index)] = state.upper()
        return states

    def _load_config(self) -> None:
        enabled = self._pico_config.get_enabled_inputs()
        for index, checkbox in self._enabled_checks.items():
            checkbox.setChecked(index in enabled)

    def refresh_device(self) -> None:
        try:
            connected = bool(self._pico.connect())
            status = self._pico.status() if connected else {}
        except Exception:
            connected, status = False, {}
        connected = bool(connected and status.get("connected", False))
        response = str(status.get("device_status", "") or "")
        self.lbl_connection.setText("Pripojené" if connected else "Odpojené")
        self.lbl_port.setText(str(status.get("port") or "—") if connected else "—")
        self.lbl_firmware.setText(self.parse_firmware(response) or "—")
        self.txt_status.setPlainText(response)
        self.refresh_inputs()

    def refresh_inputs(self) -> None:
        try:
            response = self._pico.inputs() if self._pico.is_available() else ""
        except Exception:
            response = ""
        states = self.parse_inputs(response)
        for index, label in self._input_states.items():
            label.setText(states.get(index, "—"))

    def _on_capture(self, input_index: int) -> None:
        if isinstance(input_index, int) and not isinstance(input_index, bool) and 1 <= input_index <= 8:
            self.capture_received.emit(input_index)

    def _show_capture(self, input_index: int) -> None:
        self.lbl_last_event.setText(f"Posledný event: IN{input_index}")

    def _save(self) -> None:
        enabled = {index for index, checkbox in self._enabled_checks.items() if checkbox.isChecked()}
        try:
            self._pico_config.set_enabled_inputs(enabled)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "Pico konfigurácia", f"Konfiguráciu sa nepodarilo uložiť:\n{exc}")
            return
        self.accept()

    def done(self, result: int) -> None:
        self._timer.stop()
        if self._callback_registered:
            self._pico.unregister_trigger_callback(self._on_capture)
            self._callback_registered = False
        super().done(result)


__all__ = ["PicoWizard"]

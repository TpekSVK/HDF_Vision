"""Diagnostic and local input-enablement dialog for Raspberry Pi Pico."""

from __future__ import annotations

import re

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

        modes = QGroupBox("Režim Pico", self)
        modes_grid = QGridLayout(modes)
        self.cmb_v1_mode = QComboBox(modes)
        self.cmb_v2_mode = QComboBox(modes)
        mode_tooltip = (
            "MASTER (odporúčané pre HDF_Vision): kamera streamuje kontinuálne a Pico pri externom "
            "vstupe odošle CAPTURE INx.\nTRIGGER: legacy/test režim; Pico generuje hardvérové "
            "trigger pulzy pre kameru."
        )
        for combo in (self.cmb_v1_mode, self.cmb_v2_mode):
            combo.addItems(["MASTER", "TRIGGER"])
            combo.setToolTip(mode_tooltip)
            combo.setEnabled(False)
        modes_grid.addWidget(QLabel("V1 režim:", modes), 0, 0)
        modes_grid.addWidget(self.cmb_v1_mode, 0, 1)
        modes_grid.addWidget(QLabel("V2 režim:", modes), 1, 0)
        modes_grid.addWidget(self.cmb_v2_mode, 1, 1)
        explanation = QLabel(
            "MASTER je odporúčaný produkčný režim. TRIGGER je legacy/test režim pre hardvérové pulzy.",
            modes,
        )
        explanation.setWordWrap(True)
        explanation.setToolTip(mode_tooltip)
        modes_grid.addWidget(explanation, 2, 0, 1, 2)
        layout.addWidget(modes)

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

    @staticmethod
    def parse_view_modes(response: str) -> dict[str, str]:
        modes: dict[str, str] = {}
        for line in str(response or "").splitlines():
            match = re.match(r"^\s*(V[12])_MODE\s+([^\s]+)\s*$", line, re.IGNORECASE)
            if not match:
                continue
            mode = match.group(2).upper()
            if mode in {"MASTER", "TRIGGER"}:
                modes[match.group(1).upper()] = mode
        return modes

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
        view_modes = self.parse_view_modes(response) if connected else {}
        for view, combo in (("V1", self.cmb_v1_mode), ("V2", self.cmb_v2_mode)):
            mode = view_modes.get(view)
            if mode is not None:
                combo.setCurrentText(mode)
            combo.setEnabled(mode is not None)
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
        if not self.cmb_v1_mode.isEnabled() or not self.cmb_v2_mode.isEnabled():
            QMessageBox.critical(
                self,
                "Pico konfigurácia",
                "Režimy V1/V2 nie sú dostupné zo STATUS. Obnovte pripojenie a skúste znova.",
            )
            return
        try:
            commands = (
                ("V1", self.cmb_v1_mode.currentText()),
                ("V2", self.cmb_v2_mode.currentText()),
            )
            for view, mode in commands:
                if not self._pico.set_view_mode(view, mode):
                    detail = str(getattr(self._pico, "last_error", "") or "Neznáma chyba")
                    QMessageBox.critical(
                        self, "Pico konfigurácia", f"Nepodarilo sa nastaviť {view} režim {mode}:\n{detail}"
                    )
                    return
            if not self._pico.save_config():
                detail = str(getattr(self._pico, "last_error", "") or "Neznáma chyba")
                QMessageBox.critical(
                    self,
                    "Pico konfigurácia",
                    f"Konfiguráciu sa nepodarilo uložiť do Pico:\n{detail}",
                )
                return
            self._pico_config.set_enabled_inputs(enabled)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
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

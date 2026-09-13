from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.modbus_service import ModbusConfig, ModbusService


class ModbusWizard(QDialog):
    """Modal dialog used to configure Modbus TCP mapping for the relay module."""

    def __init__(self, modbus: ModbusService, parent: QWidget | None = None, *, peer_configs=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sprievodca Modbus")
        self._peer_configs = peer_configs or (lambda: [])
        self._modbus = modbus
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._refresh_inputs_status)
        self._init_ui()
        initial = modbus.get_config()
        self._invalid_saved_outputs = any(
            not -1 <= int(getattr(initial, field)) <= 7
            for field in ("ok_coil", "nok_coil", "heartbeat_coil"))
        self._load_from_config(initial)
        for spin in (self.spin_ok, self.spin_nok, self.spin_heartbeat, self.spin_port, self.spin_unit):
            spin.valueChanged.connect(self._refresh_output_warning)
        self.txt_host.textChanged.connect(self._refresh_output_warning)
        self._refresh_output_warning()

    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addWidget(self._build_connection_group())
        content_layout.addWidget(self._build_outputs_group())
        content_layout.addWidget(self._build_inputs_group())
        content_layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.Save).setText("Uložiť & Aplikovať")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_connection_group(self) -> QGroupBox:
        box = QGroupBox("Modbus TCP Connection", self)
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.txt_host = QLineEdit(box)
        self.txt_host.setPlaceholderText("192.168.0.50")
        self.spin_port = QSpinBox(box)
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(502)
        self.spin_unit = QSpinBox(box)
        self.spin_unit.setRange(0, 255)
        self.spin_unit.setValue(1)
        self.spin_timeout = QSpinBox(box)
        self.spin_timeout.setRange(100, 10000)
        self.spin_timeout.setValue(1500)
        self.spin_retry = QSpinBox(box)
        self.spin_retry.setRange(0, 10)
        self.spin_retry.setValue(1)
        self.chk_enable = QCheckBox("Zapnúť Modbus", box)
        self.lbl_conn_status = QLabel("–", box)

        labels = [
            ("Host/IP:", self.txt_host),
            ("Port:", self.spin_port),
            ("Unit ID:", self.spin_unit),
            ("Timeout (ms):", self.spin_timeout),
            ("Retry count:", self.spin_retry),
            ("", self.chk_enable),
        ]
        for row, (text, widget) in enumerate(labels):
            if text:
                grid.addWidget(QLabel(text, box), row, 0)
            grid.addWidget(widget, row, 1)

        btn_test = QPushButton("Otestovať pripojenie", box)
        btn_test.clicked.connect(self._on_test_connection)
        row = len(labels)
        grid.addWidget(btn_test, row, 0)
        grid.addWidget(self.lbl_conn_status, row, 1)
        return box

    def _build_outputs_group(self) -> QGroupBox:
        box = QGroupBox("Mapovanie výstupov (Coils)", self)
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.spin_ok = self._coil_spin(box, default=0)
        self.spin_nok = self._coil_spin(box, default=1)
        self.spin_heartbeat = self._coil_spin(box, default=2)
        for spin in (self.spin_ok, self.spin_nok, self.spin_heartbeat):
            spin.setMaximum(7)
        self.spin_pulse_len = QSpinBox(box)
        self.spin_pulse_len.setRange(10, 10000)
        self.spin_pulse_len.setValue(200)
        self.spin_heartbeat_period = QSpinBox(box)
        self.spin_heartbeat_period.setRange(100, 10000)
        self.spin_heartbeat_period.setValue(1000)

        rows = [
            ("Adresa výstupu OK:", self.spin_ok),
            ("Adresa výstupu NOK:", self.spin_nok),
            ("Adresa výstupu heartbeat:", self.spin_heartbeat),
            ("Pulse length OK/NOK (ms):", self.spin_pulse_len),
            ("Heartbeat period (ms):", self.spin_heartbeat_period),
        ]
        for row, (label, widget) in enumerate(rows):
            grid.addWidget(QLabel(label, box), row, 0)
            grid.addWidget(widget, row, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_ok = QPushButton("Otestovať impulz OK", box)
        btn_nok = QPushButton("Otestovať impulz NOK", box)
        btn_hb = QPushButton("Otestovať heartbeat 3×", box)
        btn_ok.clicked.connect(self._on_test_ok)
        btn_nok.clicked.connect(self._on_test_nok)
        btn_hb.clicked.connect(self._on_test_heartbeat)
        for btn in (btn_ok, btn_nok, btn_hb):
            btn_row.addWidget(btn)
        grid.addLayout(btn_row, len(rows), 0, 1, 2)
        hint = QLabel("8 výstupov: adresy 0–7 zodpovedajú relé 1–8. −1 = vypnuté.", box)
        hint.setWordWrap(True)
        grid.addWidget(hint, len(rows) + 1, 0, 1, 2)
        self.lbl_output_warning = QLabel(box)
        self.lbl_output_warning.setWordWrap(True)
        self.lbl_output_warning.setStyleSheet("color: #e6a23c;")
        grid.addWidget(self.lbl_output_warning, len(rows) + 2, 0, 1, 2)
        return box

    def _build_inputs_group(self) -> QGroupBox:
        box = QGroupBox("Mapovanie vstupov (Discrete Inputs)", self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.spin_request_inputs: list[QSpinBox] = []
        self.lbl_request_input_statuses: list[QLabel] = []
        for idx in range(8):
            spin = self._coil_spin(box, default=0 if idx == 0 else -1)
            lbl_status = QLabel("–", box)
            lbl_status.setStyleSheet("color: #bbb;")
            self.spin_request_inputs.append(spin)
            self.lbl_request_input_statuses.append(lbl_status)
            grid.addWidget(QLabel(f"Vstup {idx + 1} DI adresa:", box), idx, 0)
            grid.addWidget(spin, idx, 1)
            grid.addWidget(lbl_status, idx, 2)
        layout.addLayout(grid)

        btn_now = QPushButton("Načítať vstupy teraz", box)
        btn_now.clicked.connect(self._refresh_inputs_status)
        layout.addWidget(btn_now, 0, Qt.AlignLeft)

        hint = QLabel("Stavy vstupov sa obnovujú každých ~200 ms počas otvoreného dialógu.", box)
        hint.setStyleSheet("color: #777;")
        layout.addWidget(hint)
        return box

    def _coil_spin(self, parent: QWidget, *, default: int) -> QSpinBox:
        spin = QSpinBox(parent)
        spin.setRange(-1, 65535)
        spin.setValue(default)
        spin.setSpecialValueText("Vypnuté (-1)")
        return spin

    # ------------------------------------------------------------------
    def _load_from_config(self, config: ModbusConfig) -> None:
        self.txt_host.setText(config.host)
        self.spin_port.setValue(int(config.port))
        self.spin_unit.setValue(int(config.unit_id))
        self.spin_timeout.setValue(int(config.timeout_ms))
        self.spin_retry.setValue(int(config.retry_count))
        self.chk_enable.setChecked(bool(config.enabled))

        self.spin_ok.setValue(int(config.ok_coil) if -1 <= int(config.ok_coil) <= 7 else -1)
        self.spin_nok.setValue(int(config.nok_coil) if -1 <= int(config.nok_coil) <= 7 else -1)
        self.spin_heartbeat.setValue(int(config.heartbeat_coil) if -1 <= int(config.heartbeat_coil) <= 7 else -1)
        self.spin_pulse_len.setValue(int(config.pulse_length_ms))
        self.spin_heartbeat_period.setValue(int(config.heartbeat_period_ms))
        addresses = list(config.request_di_addresses or [])
        if len(addresses) < len(self.spin_request_inputs):
            addresses.extend([-1] * (len(self.spin_request_inputs) - len(addresses)))
        for idx, spin in enumerate(self.spin_request_inputs):
            spin.setValue(int(addresses[idx]))

    def _collect_config(self) -> ModbusConfig:
        cfg = ModbusConfig(
            host=self.txt_host.text().strip() or "192.168.0.50",
            port=int(self.spin_port.value()),
            unit_id=int(self.spin_unit.value()),
            timeout_ms=int(self.spin_timeout.value()),
            retry_count=int(self.spin_retry.value()),
            enabled=self.chk_enable.isChecked(),
            ok_coil=int(self.spin_ok.value()),
            nok_coil=int(self.spin_nok.value()),
            heartbeat_coil=int(self.spin_heartbeat.value()),
            pulse_length_ms=int(self.spin_pulse_len.value()),
            heartbeat_period_ms=int(self.spin_heartbeat_period.value()),
            request_di_addresses=[int(spin.value()) for spin in self.spin_request_inputs],
        )
        return cfg

    # ------------------------------------------------------------------
    def _set_status(self, label: QLabel, text: str, *, ok: bool = True) -> None:
        color = "#4caf50" if ok else "#f44336"
        label.setText(text)
        label.setStyleSheet(f"color: {color};")

    def _on_test_connection(self) -> None:
        cfg = self._collect_config()
        ok, msg = self._modbus.test_connection(cfg)
        self._set_status(self.lbl_conn_status, msg, ok=ok)

    def _on_test_ok(self) -> None:
        cfg = self._collect_config()
        success = self._modbus.pulse_coil(cfg.ok_coil, pulse_ms=cfg.pulse_length_ms, config=cfg)
        self._set_status(self.lbl_conn_status, "OK pulse sent" if success else (self._modbus.last_error or "Error"), ok=success)

    def _on_test_nok(self) -> None:
        cfg = self._collect_config()
        success = self._modbus.pulse_coil(cfg.nok_coil, pulse_ms=cfg.pulse_length_ms, config=cfg)
        self._set_status(self.lbl_conn_status, "NOK pulse sent" if success else (self._modbus.last_error or "Error"), ok=success)

    def _on_test_heartbeat(self) -> None:
        cfg = self._collect_config()
        success = self._modbus.heartbeat_pulse(cfg.heartbeat_coil, count=3, period_ms=cfg.heartbeat_period_ms, config=cfg)
        self._set_status(self.lbl_conn_status, "Heartbeat test running" if success else (self._modbus.last_error or "Error"), ok=success)

    def _refresh_inputs_status(self) -> None:
        cfg = self._collect_config()
        results = self._modbus.read_configured_discrete_inputs(config=cfg)
        for idx, lbl_status in enumerate(self.lbl_request_input_statuses):
            item = results[idx] if idx < len(results) else None
            if not item:
                self._set_status(lbl_status, "Error", ok=False)
                continue
            if bool(item.get("disabled")):
                lbl_status.setText("Disabled")
                lbl_status.setStyleSheet("color: #bbb;")
                continue
            value = item.get("value")
            if value is None:
                self._set_status(lbl_status, "Error", ok=False)
            elif bool(value):
                self._set_status(lbl_status, "ON", ok=True)
            else:
                self._set_status(lbl_status, "OFF", ok=True)

    def _output_conflicts(self):
        cfg = self._collect_config()
        key = lambda c: (c.host.strip().lower(), c.port, c.unit_id)
        fields = (("ok_coil", "OK"), ("nok_coil", "NOK"), ("heartbeat_coil", "heartbeat"))
        used = {}
        for field, label in fields:
            address = getattr(cfg, field)
            if address >= 0:
                used.setdefault(address, []).append(f"táto kamera: {label}")
        for name, peer in self._peer_configs():
            if key(peer) != key(cfg):
                continue
            for field, label in fields:
                address = getattr(peer, field)
                if address in used:
                    suffix = " (Modbus vypnutý)" if not peer.enabled else ""
                    used[address].append(f"{name}: {label}{suffix}")
        return [f"Relé {address + 1} (adresa {address}): {', '.join(labels)}"
                for address, labels in sorted(used.items()) if len(labels) > 1]

    def _refresh_output_warning(self, *_args):
        conflicts = self._output_conflicts()
        self.lbl_output_warning.setText(
            "Výstup je už priradený:\n" + "\n".join(conflicts) +
            "\nSpoločné použitie môže ovplyvniť impulzy. Uloženie je povolené."
            if conflicts else "")
        if self._invalid_saved_outputs:
            self.lbl_output_warning.setText(
                "Pôvodná adresa mimo rozsahu 0–7 bola v tomto formulári vypnutá. "
                "Pred uložením skontrolujte výstupy.\n" + self.lbl_output_warning.text())

    def _on_accept(self) -> None:
        cfg = self._collect_config()
        conflicts = self._output_conflicts()
        if conflicts:
            QMessageBox.warning(self, "Spoločné použitie výstupu",
                "Výstup je už priradený:\n" + "\n".join(conflicts) +
                "\n\nSpoločné použitie môže ovplyvniť impulzy. Nastavenie sa uloží.")
        self._modbus.set_config(cfg, persist=True)
        self.accept()

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # type: ignore[override]
        self._poll_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._poll_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._poll_timer.stop()
        super().closeEvent(event)

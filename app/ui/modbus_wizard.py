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
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.modbus_service import ModbusConfig, ModbusService


class ModbusWizard(QDialog):
    """Modal dialog used to configure Modbus TCP mapping for the relay module."""

    def __init__(self, modbus: ModbusService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modbus Wizard")
        self._modbus = modbus
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._refresh_trigger_status)
        self._init_ui()
        self._load_from_config(modbus.get_config())

    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        layout.addWidget(self._build_connection_group())
        layout.addWidget(self._build_outputs_group())
        layout.addWidget(self._build_inputs_group())

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
        self.chk_enable = QCheckBox("Enable Modbus", box)
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

        btn_test = QPushButton("Test Connect", box)
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
        self.spin_flash1 = self._coil_spin(box, default=-1)
        self.spin_flash1_delay = QSpinBox(box)
        self.spin_flash1_delay.setRange(0, 10000)
        self.spin_flash1_delay.setValue(0)
        self.spin_flash1_pulse = QSpinBox(box)
        self.spin_flash1_pulse.setRange(1, 10000)
        self.spin_flash1_pulse.setValue(200)
        self.spin_flash2 = self._coil_spin(box, default=-1)
        self.spin_flash2_delay = QSpinBox(box)
        self.spin_flash2_delay.setRange(0, 10000)
        self.spin_flash2_delay.setValue(0)
        self.spin_flash2_pulse = QSpinBox(box)
        self.spin_flash2_pulse.setRange(1, 10000)
        self.spin_flash2_pulse.setValue(200)
        self.spin_pulse_len = QSpinBox(box)
        self.spin_pulse_len.setRange(10, 10000)
        self.spin_pulse_len.setValue(200)
        self.spin_heartbeat_period = QSpinBox(box)
        self.spin_heartbeat_period.setRange(100, 10000)
        self.spin_heartbeat_period.setValue(1000)

        rows = [
            ("OK coil address:", self.spin_ok),
            ("NOK coil address:", self.spin_nok),
            ("Heartbeat coil address:", self.spin_heartbeat),
            ("Flash1 coil address:", self.spin_flash1),
            ("Flash1 delay (ms):", self.spin_flash1_delay),
            ("Flash1 pulse length (ms):", self.spin_flash1_pulse),
            ("Flash2 coil address:", self.spin_flash2),
            ("Flash2 delay (ms):", self.spin_flash2_delay),
            ("Flash2 pulse length (ms):", self.spin_flash2_pulse),
            ("Pulse length OK/NOK (ms):", self.spin_pulse_len),
            ("Heartbeat period (ms):", self.spin_heartbeat_period),
        ]
        for row, (label, widget) in enumerate(rows):
            grid.addWidget(QLabel(label, box), row, 0)
            grid.addWidget(widget, row, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_ok = QPushButton("Test OK Pulse", box)
        btn_nok = QPushButton("Test NOK Pulse", box)
        btn_hb = QPushButton("Test Heartbeat 3×", box)
        btn_ok.clicked.connect(self._on_test_ok)
        btn_nok.clicked.connect(self._on_test_nok)
        btn_hb.clicked.connect(self._on_test_heartbeat)
        for btn in (btn_ok, btn_nok, btn_hb):
            btn_row.addWidget(btn)
        grid.addLayout(btn_row, len(rows), 0, 1, 2)
        return box

    def _build_inputs_group(self) -> QGroupBox:
        box = QGroupBox("Mapovanie vstupov (Discrete Inputs)", self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.spin_trigger = self._coil_spin(box, default=0)
        grid.addWidget(QLabel("Trigger DI address:", box), 0, 0)
        grid.addWidget(self.spin_trigger, 0, 1)
        layout.addLayout(grid)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(QLabel("Live Trigger status:", box))
        self.lbl_trigger_status = QLabel("–", box)
        self.lbl_trigger_status.setStyleSheet("color: #bbb;")
        status_row.addWidget(self.lbl_trigger_status)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        btn_now = QPushButton("Read Trigger Now", box)
        btn_now.clicked.connect(self._refresh_trigger_status)
        layout.addWidget(btn_now, 0, Qt.AlignLeft)

        hint = QLabel("Obnovuje sa každých ~200 ms počas otvoreného dialógu.", box)
        hint.setStyleSheet("color: #777;")
        layout.addWidget(hint)
        return box

    def _coil_spin(self, parent: QWidget, *, default: int) -> QSpinBox:
        spin = QSpinBox(parent)
        spin.setRange(-1, 65535)
        spin.setValue(default)
        spin.setSpecialValueText("Disabled (-1)")
        return spin

    # ------------------------------------------------------------------
    def _load_from_config(self, config: ModbusConfig) -> None:
        self.txt_host.setText(config.host)
        self.spin_port.setValue(int(config.port))
        self.spin_unit.setValue(int(config.unit_id))
        self.spin_timeout.setValue(int(config.timeout_ms))
        self.spin_retry.setValue(int(config.retry_count))
        self.chk_enable.setChecked(bool(config.enabled))

        self.spin_ok.setValue(int(config.ok_coil))
        self.spin_nok.setValue(int(config.nok_coil))
        self.spin_heartbeat.setValue(int(config.heartbeat_coil))
        self.spin_flash1.setValue(int(config.flash1_coil))
        self.spin_flash1_delay.setValue(int(config.flash1_delay_ms))
        self.spin_flash1_pulse.setValue(int(config.flash1_pulse_ms))
        self.spin_flash2.setValue(int(config.flash2_coil))
        self.spin_flash2_delay.setValue(int(config.flash2_delay_ms))
        self.spin_flash2_pulse.setValue(int(config.flash2_pulse_ms))
        self.spin_pulse_len.setValue(int(config.pulse_length_ms))
        self.spin_heartbeat_period.setValue(int(config.heartbeat_period_ms))
        self.spin_trigger.setValue(int(config.trigger_di))

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
            flash1_coil=int(self.spin_flash1.value()),
            flash1_delay_ms=int(self.spin_flash1_delay.value()),
            flash1_pulse_ms=int(self.spin_flash1_pulse.value()),
            flash2_coil=int(self.spin_flash2.value()),
            flash2_delay_ms=int(self.spin_flash2_delay.value()),
            flash2_pulse_ms=int(self.spin_flash2_pulse.value()),
            pulse_length_ms=int(self.spin_pulse_len.value()),
            heartbeat_period_ms=int(self.spin_heartbeat_period.value()),
            trigger_di=int(self.spin_trigger.value()),
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

    def _refresh_trigger_status(self) -> None:
        cfg = self._collect_config()
        value = self._modbus.read_discrete_input(cfg.trigger_di, config=cfg)
        if value is None:
            self._set_status(self.lbl_trigger_status, self._modbus.last_error or "–", ok=False)
        elif value:
            self._set_status(self.lbl_trigger_status, "HIGH", ok=True)
        else:
            self._set_status(self.lbl_trigger_status, "LOW", ok=True)

    def _on_accept(self) -> None:
        cfg = self._collect_config()
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


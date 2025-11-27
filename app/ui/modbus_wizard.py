from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from app.utils.qt_concurrent import run as qt_run

from app.services.modbus_service import ModbusService


class ModbusWizard(QDialog):
    """Modal dialog used to configure Modbus TCP mapping and connectivity."""

    def __init__(
        self,
        modbus: ModbusService,
        settings: dict[str, Any],
        apply_callback: Callable[[dict[str, Any]], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modbus Wizard")
        self.setModal(True)
        self._modbus = modbus
        self._apply_callback = apply_callback
        self._futures: list[Any] = []
        self._reading_trigger = False
        self._heartbeat_state = False

        self._init_ui(settings)

        self._trigger_timer = QTimer(self)
        self._trigger_timer.setInterval(200)
        self._trigger_timer.timeout.connect(self._refresh_trigger)
        self._trigger_timer.start()

    # ------------------------------------------------------------------
    def _init_ui(self, settings: dict[str, Any]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        layout.addWidget(self._build_connection_group(settings))
        layout.addWidget(self._build_coils_group(settings))
        layout.addWidget(self._build_inputs_group(settings))

        self._status_bar = QHBoxLayout()
        self._lbl_status = QLabel("Disconnected", self)
        self._lbl_last_error = QLabel("", self)
        self._lbl_last_error.setStyleSheet("color: #ff8888;")
        self._lbl_last_read = QLabel("Last read: –", self)
        self._status_bar.addWidget(self._lbl_status, 0)
        self._status_bar.addWidget(self._lbl_last_error, 1)
        self._status_bar.addWidget(self._lbl_last_read, 1)
        layout.addLayout(self._status_bar)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        btn_save = buttons.button(QDialogButtonBox.Save)
        if btn_save:
            btn_save.setText("Uložiť & Aplikovať")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_status_bar()

    # ----------------------- UI builders -----------------------
    def _build_connection_group(self, settings: dict[str, Any]) -> QGroupBox:
        box = QGroupBox("A) Modbus TCP Connection", self)
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.txt_host = QLineEdit(settings.get("host", ""), box)
        self.sp_port = QSpinBox(box)
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(int(settings.get("port", 502)))

        self.sp_unit_id = QSpinBox(box)
        self.sp_unit_id.setRange(0, 255)
        self.sp_unit_id.setValue(int(settings.get("unit_id", 1)))

        self.sp_timeout = QSpinBox(box)
        self.sp_timeout.setRange(100, 10000)
        self.sp_timeout.setSingleStep(100)
        self.sp_timeout.setValue(int(settings.get("timeout_ms", 1500)))

        self.sp_retry = QSpinBox(box)
        self.sp_retry.setRange(0, 10)
        self.sp_retry.setValue(int(settings.get("retry", 1)))

        self.chk_enable = QCheckBox("Enable Modbus", box)
        self.chk_enable.setChecked(bool(settings.get("enabled", False)))

        labels = [
            "Host/IP:",
            "Port:",
            "Unit ID:",
            "Timeout (ms):",
            "Retry count:",
            "",
        ]
        widgets = [
            self.txt_host,
            self.sp_port,
            self.sp_unit_id,
            self.sp_timeout,
            self.sp_retry,
            self.chk_enable,
        ]
        for row, (lbl, widget) in enumerate(zip(labels, widgets)):
            if lbl:
                grid.addWidget(QLabel(lbl, box), row, 0)
            grid.addWidget(widget, row, 1)

        self.btn_test_connect = QPushButton("Test Connect", box)
        self.btn_test_connect.clicked.connect(self._on_test_connect)
        self._lbl_connect_status = QLabel("–", box)
        grid.addWidget(self.btn_test_connect, len(labels), 0)
        grid.addWidget(self._lbl_connect_status, len(labels), 1)

        return box

    def _build_coils_group(self, settings: dict[str, Any]) -> QGroupBox:
        box = QGroupBox("B) Mapovanie výstupov (Coils)", self)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.sp_ok = self._make_coil_spin(settings.get("coil_ok", 0))
        self.sp_nok = self._make_coil_spin(settings.get("coil_nok", 1))
        self.sp_hb = self._make_coil_spin(settings.get("coil_heartbeat", 2))
        self.sp_flash1 = self._make_coil_spin(settings.get("coil_flash1", -1))
        self.sp_flash2 = self._make_coil_spin(settings.get("coil_flash2", -1))
        self.sp_pulse = QSpinBox(box)
        self.sp_pulse.setRange(10, 10000)
        self.sp_pulse.setValue(int(settings.get("pulse_ms", 200)))
        self.sp_heartbeat_period = QSpinBox(box)
        self.sp_heartbeat_period.setRange(100, 10000)
        self.sp_heartbeat_period.setValue(int(settings.get("heartbeat_period_ms", 1000)))

        form.addRow("OK coil address:", self.sp_ok)
        form.addRow("NOK coil address:", self.sp_nok)
        form.addRow("Heartbeat coil address:", self.sp_hb)
        form.addRow("Flash1 coil address:", self.sp_flash1)
        form.addRow("Flash2 coil address:", self.sp_flash2)
        form.addRow("Pulse length OK/NOK (ms):", self.sp_pulse)
        form.addRow("Heartbeat period (ms):", self.sp_heartbeat_period)

        btn_row = QHBoxLayout()
        self.btn_test_ok = QPushButton("Test OK Pulse", box)
        self.btn_test_ok.clicked.connect(lambda: self._on_test_pulse("ok"))
        self.btn_test_nok = QPushButton("Test NOK Pulse", box)
        self.btn_test_nok.clicked.connect(lambda: self._on_test_pulse("nok"))
        self.btn_test_hb = QPushButton("Test Heartbeat 3×", box)
        self.btn_test_hb.clicked.connect(self._on_test_heartbeat)
        for btn in (self.btn_test_ok, self.btn_test_nok, self.btn_test_hb):
            btn_row.addWidget(btn)
        btn_row.addStretch(1)
        form.addRow(btn_row)

        return box

    def _build_inputs_group(self, settings: dict[str, Any]) -> QGroupBox:
        box = QGroupBox("C) Mapovanie vstupov (Discrete Inputs)", self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.sp_trigger = QSpinBox(box)
        self.sp_trigger.setRange(0, 65535)
        self.sp_trigger.setValue(int(settings.get("di_trigger", 0)))
        form.addRow("Trigger DI address:", self.sp_trigger)
        layout.addLayout(form)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Live Trigger status:", box))
        self.lbl_trigger_status = QLabel("–", box)
        self._set_trigger_indicator(False)
        status_row.addWidget(self.lbl_trigger_status)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        btn_read = QPushButton("Read Trigger Now", box)
        btn_read.clicked.connect(self._read_trigger_now)
        layout.addWidget(btn_read, alignment=Qt.AlignLeft)

        return box

    def _make_coil_spin(self, value: Any) -> QSpinBox:
        spin = QSpinBox(self)
        spin.setRange(-1, 65535)
        spin.setValue(int(value) if value is not None else -1)
        return spin

    # ----------------------- interactions -----------------------
    def _run_async(self, func: Callable[[], Any], callback: Callable[[Any], None] | None = None) -> None:
        future = qt_run(func)
        self._futures.append(future)
        if callback is not None:
            future.finished.connect(lambda f=future: callback(f.result()))
        future.finished.connect(lambda f=future: self._futures.remove(f) if f in self._futures else None)

    def _on_test_connect(self) -> None:
        self.btn_test_connect.setEnabled(False)
        params = self._collect_connection_params()
        self._lbl_connect_status.setText("Connecting…")
        self._run_async(
            lambda: self._modbus.connect(
                params["host"],
                params["port"],
                params["unit_id"],
                params["timeout_ms"],
                params["retry"],
            ),
            self._on_connect_result,
        )

    def _on_connect_result(self, ok: bool) -> None:
        self.btn_test_connect.setEnabled(True)
        if ok:
            self._lbl_connect_status.setText("Connected")
        else:
            self._lbl_connect_status.setText("Connection failed")
        self._update_status_bar()

    def _on_test_pulse(self, status: str) -> None:
        settings = self._collect_settings()
        addr = settings.get("coil_ok") if status == "ok" else settings.get("coil_nok")
        if addr is None or int(addr) < 0:
            self._lbl_connect_status.setText("Coil disabled")
            return
        pulse_ms = int(settings.get("pulse_ms", 200))
        self._run_async(lambda: self._pulse_coil(int(addr), pulse_ms))

    def _on_test_heartbeat(self) -> None:
        settings = self._collect_settings()
        addr = settings.get("coil_heartbeat")
        if addr is None or int(addr) < 0:
            self._lbl_connect_status.setText("Heartbeat disabled")
            return
        period = max(50, int(settings.get("heartbeat_period_ms", 1000)))
        self._heartbeat_state = False

        def _run():
            for _ in range(3):
                self._heartbeat_state = not self._heartbeat_state
                self._modbus.write_coil(int(addr), self._heartbeat_state)
                time.sleep(period / 1000.0)

        self._run_async(_run)

    def _refresh_trigger(self) -> None:
        if self._reading_trigger or not self.isVisible():
            return
        settings = self._collect_settings()
        addr = settings.get("di_trigger")
        if not self.chk_enable.isChecked() or addr is None:
            return
        self._reading_trigger = True
        self._run_async(lambda: self._modbus.read_discrete_inputs(int(addr), 1), self._on_trigger_read)

    def _on_trigger_read(self, values: list[bool]) -> None:
        self._reading_trigger = False
        level = bool(values[0]) if values else False
        self._set_trigger_indicator(level)
        if self._modbus.last_read_ts:
            ts = datetime.fromtimestamp(self._modbus.last_read_ts)
            self._lbl_last_read.setText(f"Last read: {ts:%H:%M:%S}")
        self._update_status_bar()

    def _read_trigger_now(self) -> None:
        self._refresh_trigger()

    def _pulse_coil(self, address: int, pulse_ms: int) -> None:
        if not self._modbus.is_connected():
            return
        self._modbus.write_coil(address, True)
        time.sleep(max(10, int(pulse_ms)) / 1000.0)
        self._modbus.write_coil(address, False)
        self._update_status_bar()

    # ----------------------- helpers -----------------------
    def _collect_connection_params(self) -> dict[str, Any]:
        return {
            "host": self.txt_host.text().strip(),
            "port": int(self.sp_port.value()),
            "unit_id": int(self.sp_unit_id.value()),
            "timeout_ms": int(self.sp_timeout.value()),
            "retry": int(self.sp_retry.value()),
            "enabled": self.chk_enable.isChecked(),
        }

    def _collect_settings(self) -> dict[str, Any]:
        data = self._collect_connection_params()
        data.update(
            {
                "coil_ok": int(self.sp_ok.value()),
                "coil_nok": int(self.sp_nok.value()),
                "coil_heartbeat": int(self.sp_hb.value()),
                "coil_flash1": int(self.sp_flash1.value()),
                "coil_flash2": int(self.sp_flash2.value()),
                "pulse_ms": int(self.sp_pulse.value()),
                "heartbeat_period_ms": int(self.sp_heartbeat_period.value()),
                "di_trigger": int(self.sp_trigger.value()),
            }
        )
        return data

    def _set_trigger_indicator(self, active: bool) -> None:
        palette = self.lbl_trigger_status.palette()
        palette.setColor(QPalette.WindowText, QColor("#33dd66" if active else "#dddddd"))
        self.lbl_trigger_status.setPalette(palette)
        self.lbl_trigger_status.setText("ON" if active else "OFF")

    def _update_status_bar(self) -> None:
        self._lbl_status.setText("Connected" if self._modbus.is_connected() else "Disconnected")
        err = self._modbus.last_error or ""
        self._lbl_last_error.setText(f"Last error: {err}" if err else "")
        if self._modbus.last_read_ts:
            ts = datetime.fromtimestamp(self._modbus.last_read_ts)
            self._lbl_last_read.setText(f"Last read: {ts:%H:%M:%S}")

    def _on_save(self) -> None:
        settings = self._collect_settings()
        self._apply_callback(settings)
        self.accept()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._trigger_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)


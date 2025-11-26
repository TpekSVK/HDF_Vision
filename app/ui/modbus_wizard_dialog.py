from __future__ import annotations

import time
from datetime import datetime
from typing import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import Qt, QTimer, Signal
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

from app.services.db_service import DbService
from app.services.modbus_service import ModbusService


class ModbusWizardDialog(QDialog):
    """Modal wizard for configuring Modbus TCP connectivity and mapping."""

    settings_applied = Signal(dict)

    def __init__(self, modbus: ModbusService, db: DbService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modbus Wizard")
        self._modbus = modbus
        self._db = db
        self._settings = db.get_modbus_settings()
        self._futures: list[Future] = []
        self._trigger_refresh_running = False
        self._executor = ThreadPoolExecutor(max_workers=4)

        self._init_ui()
        self._load_settings()

        self._trigger_timer = QTimer(self)
        self._trigger_timer.setInterval(200)
        self._trigger_timer.timeout.connect(self._refresh_trigger_status)
        self._trigger_timer.start()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._update_status_bar)
        self._status_timer.start()

    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        layout.addWidget(self._build_connection_group())
        layout.addWidget(self._build_coil_group())
        layout.addWidget(self._build_input_group())

        self._status_connected = QLabel("Disconnected")
        self._status_error = QLabel("–")
        self._status_last_read = QLabel("Last read: –")
        status_bar = QHBoxLayout()
        status_bar.setSpacing(12)
        status_bar.addWidget(self._status_connected)
        status_bar.addWidget(self._status_error, 1)
        status_bar.addWidget(self._status_last_read)
        layout.addLayout(status_bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save, parent=self)
        buttons.button(QDialogButtonBox.Save).setText("Uložiť & Aplikovať")
        buttons.accepted.connect(self._save_and_apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def _build_connection_group(self) -> QGroupBox:
        group = QGroupBox("A) Modbus TCP Connection", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)

        self.txt_host = QLineEdit(group)
        self.txt_host.setPlaceholderText("192.168.0.50")
        form.addRow("Host/IP:", self.txt_host)

        self.spn_port = QSpinBox(group)
        self.spn_port.setRange(1, 65535)
        form.addRow("Port:", self.spn_port)

        self.spn_unit = QSpinBox(group)
        self.spn_unit.setRange(1, 255)
        form.addRow("Unit ID:", self.spn_unit)

        self.spn_timeout = QSpinBox(group)
        self.spn_timeout.setRange(100, 10000)
        form.addRow("Timeout (ms):", self.spn_timeout)

        self.spn_retry = QSpinBox(group)
        self.spn_retry.setRange(0, 10)
        form.addRow("Retry count:", self.spn_retry)

        self.chk_enable = QCheckBox("Enable Modbus", group)
        form.addRow(self.chk_enable)

        test_row = QHBoxLayout()
        self.btn_test_connect = QPushButton("Test Connect", group)
        self.btn_test_connect.clicked.connect(self._test_connect)
        self.lbl_connect_status = QLabel("–", group)
        self.lbl_connect_status.setWordWrap(True)
        test_row.addWidget(self.btn_test_connect)
        test_row.addWidget(self.lbl_connect_status, 1)
        form.addRow(test_row)

        return group

    def _build_coil_group(self) -> QGroupBox:
        group = QGroupBox("B) Mapovanie výstupov (Coils)", self)
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.spn_coil_ok = self._make_spin(minimum=0)
        self.spn_coil_nok = self._make_spin(minimum=0)
        self.spn_coil_heartbeat = self._make_spin(minimum=0)
        self.spn_coil_flash1 = self._make_spin(minimum=-1, special="Vypnuté (-1)")
        self.spn_coil_flash2 = self._make_spin(minimum=-1, special="Vypnuté (-1)")
        self.spn_pulse_ms = self._make_spin(minimum=10, maximum=10000)
        self.spn_heartbeat_period = self._make_spin(minimum=50, maximum=10000)

        labels = [
            ("OK coil address:", self.spn_coil_ok),
            ("NOK coil address:", self.spn_coil_nok),
            ("Heartbeat coil address:", self.spn_coil_heartbeat),
            ("Flash1 coil address:", self.spn_coil_flash1),
            ("Flash2 coil address:", self.spn_coil_flash2),
            ("Pulse length OK/NOK (ms):", self.spn_pulse_ms),
            ("Heartbeat period (ms):", self.spn_heartbeat_period),
        ]
        for row, (text, widget) in enumerate(labels):
            grid.addWidget(QLabel(text, group), row, 0)
            grid.addWidget(widget, row, 1)

        btn_row = QHBoxLayout()
        self.btn_test_ok = QPushButton("Test OK Pulse", group)
        self.btn_test_ok.clicked.connect(lambda: self._pulse_role("coil_ok"))
        self.btn_test_nok = QPushButton("Test NOK Pulse", group)
        self.btn_test_nok.clicked.connect(lambda: self._pulse_role("coil_nok"))
        self.btn_test_heartbeat = QPushButton("Test Heartbeat 3×", group)
        self.btn_test_heartbeat.clicked.connect(self._test_heartbeat)
        btn_row.addWidget(self.btn_test_ok)
        btn_row.addWidget(self.btn_test_nok)
        btn_row.addWidget(self.btn_test_heartbeat)
        grid.addLayout(btn_row, len(labels), 0, 1, 2)

        return group

    def _build_input_group(self) -> QGroupBox:
        group = QGroupBox("C) Mapovanie vstupov (Discrete Inputs)", self)
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.spn_di_trigger = self._make_spin(minimum=0)
        form.addRow("Trigger DI address:", self.spn_di_trigger)
        layout.addLayout(form)

        status_row = QHBoxLayout()
        self.lbl_trigger_status = QLabel("–", group)
        self.lbl_trigger_status.setAlignment(Qt.AlignCenter)
        status_row.addWidget(QLabel("Live Trigger status:"))
        status_row.addWidget(self.lbl_trigger_status, 1)
        self.btn_read_trigger = QPushButton("Read Trigger Now", group)
        self.btn_read_trigger.clicked.connect(self._refresh_trigger_status)
        status_row.addWidget(self.btn_read_trigger)
        layout.addLayout(status_row)

        return group

    # ------------------------------------------------------------------
    def _make_spin(self, *, minimum: int = -1, maximum: int = 65535, special: str | None = None) -> QSpinBox:
        spin = QSpinBox(self)
        spin.setRange(minimum, maximum)
        if special is not None:
            spin.setSpecialValueText(special)
        return spin

    def _load_settings(self) -> None:
        self.chk_enable.setChecked(bool(self._settings.get("enabled")))
        self.txt_host.setText(str(self._settings.get("host") or ""))
        self.spn_port.setValue(int(self._settings.get("port") or 502))
        self.spn_unit.setValue(int(self._settings.get("unit_id") or 1))
        self.spn_timeout.setValue(int(self._settings.get("timeout_ms") or 1500))
        self.spn_retry.setValue(int(self._settings.get("retry") or 1))

        self.spn_coil_ok.setValue(int(self._settings.get("coil_ok") or 0))
        self.spn_coil_nok.setValue(int(self._settings.get("coil_nok") or 1))
        self.spn_coil_heartbeat.setValue(int(self._settings.get("coil_heartbeat") or 2))
        self._set_optional_value(self.spn_coil_flash1, self._settings.get("coil_flash1"))
        self._set_optional_value(self.spn_coil_flash2, self._settings.get("coil_flash2"))
        self.spn_pulse_ms.setValue(int(self._settings.get("pulse_ms") or 200))
        self.spn_heartbeat_period.setValue(int(self._settings.get("heartbeat_period_ms") or 1000))
        self.spn_di_trigger.setValue(int(self._settings.get("di_trigger") or 0))
        self._update_status_bar()

    def _set_optional_value(self, spin: QSpinBox, value: object) -> None:
        try:
            number = int(value)
        except Exception:
            number = -1
        spin.setValue(number if number >= 0 else spin.minimum())

    def _collect_settings(self) -> dict[str, object]:
        return {
            "enabled": self.chk_enable.isChecked(),
            "host": self.txt_host.text().strip(),
            "port": self.spn_port.value(),
            "unit_id": self.spn_unit.value(),
            "timeout_ms": self.spn_timeout.value(),
            "retry": self.spn_retry.value(),
            "coil_ok": self.spn_coil_ok.value(),
            "coil_nok": self.spn_coil_nok.value(),
            "coil_heartbeat": self.spn_coil_heartbeat.value(),
            "coil_flash1": self.spn_coil_flash1.value(),
            "coil_flash2": self.spn_coil_flash2.value(),
            "pulse_ms": self.spn_pulse_ms.value(),
            "heartbeat_period_ms": self.spn_heartbeat_period.value(),
            "di_trigger": self.spn_di_trigger.value(),
        }

    # ------------------------------------------------------------------
    def _run_async(self, func: Callable, *args, callback: Callable[[Future], None] | None = None) -> None:
        future = self._executor.submit(func, *args)
        self._futures.append(future)

        def handle_future(fut: Future) -> None:
            def deliver() -> None:
                try:
                    if callback:
                        callback(fut)
                finally:
                    self._cleanup_future(fut)

            QTimer.singleShot(0, deliver)

        future.add_done_callback(handle_future)

    def _cleanup_future(self, future: Future) -> None:
        try:
            self._futures.remove(future)
        except ValueError:
            pass
        self._update_status_bar()

    # ------------------------------------------------------------------
    def _test_connect(self) -> None:
        params = self._collect_settings()
        self.lbl_connect_status.setText("Connecting…")
        self._run_async(
            self._modbus.connect,
            params.get("host", ""),
            int(params.get("port", 502)),
            int(params.get("unit_id", 1)),
            int(params.get("timeout_ms", 1500)),
            int(params.get("retry", 1)),
            callback=self._on_test_connect_finished,
        )

    def _on_test_connect_finished(self, future: Future) -> None:
        try:
            ok = bool(future.result())
        except Exception as exc:  # pragma: no cover - defensive
            ok = False
            self.lbl_connect_status.setText(f"Error: {exc}")
        else:
            self.lbl_connect_status.setText("Connected" if ok else "Error: " + (self._modbus.last_error or ""))
        self._update_status_bar()

    def _pulse_role(self, role: str) -> None:
        mapping = {
            "coil_ok": self.spn_coil_ok.value(),
            "coil_nok": self.spn_coil_nok.value(),
        }
        address = mapping.get(role, -1)
        pulse_ms = self.spn_pulse_ms.value()
        self._run_async(self._pulse_task, address, pulse_ms)

    def _pulse_task(self, address: int, pulse_ms: int) -> bool:
        if address < 0:
            return False
        if not self._modbus.write_coil(address, True):
            return False
        time.sleep(max(0, pulse_ms) / 1000.0)
        return self._modbus.write_coil(address, False)

    def _test_heartbeat(self) -> None:
        address = self.spn_coil_heartbeat.value()
        period_ms = self.spn_heartbeat_period.value()
        self._run_async(self._heartbeat_task, address, period_ms)

    def _heartbeat_task(self, address: int, period_ms: int) -> bool:
        if address < 0:
            return False
        delay = max(50, int(period_ms)) / 1000.0
        state = False
        for _ in range(3):
            state = not state
            if not self._modbus.write_coil(address, state):
                return False
            time.sleep(delay)
            state = not state
            self._modbus.write_coil(address, state)
            time.sleep(delay)
        return True

    def _refresh_trigger_status(self) -> None:
        if self._trigger_refresh_running:
            return
        if not self.chk_enable.isChecked():
            self._set_trigger_label(None)
            return
        self._trigger_refresh_running = True
        address = self.spn_di_trigger.value()
        if address < 0:
            self._set_trigger_label(None)
            self._trigger_refresh_running = False
            return
        self._run_async(self._read_trigger_task, address, callback=self._on_trigger_value)

    def _read_trigger_task(self, address: int):
        return self._modbus.read_discrete_inputs(address, 1)

    def _on_trigger_value(self, future: Future) -> None:
        self._trigger_refresh_running = False
        try:
            result = future.result()
        except Exception:
            result = []
        level = bool(result[0]) if result else False
        self._set_trigger_label(level)
        self._update_status_bar()

    def _set_trigger_label(self, level: bool | None) -> None:
        if level is None:
            color = "#555"
            text = "Disabled"
        elif level:
            color = "#33dd66"
            text = "TRIGGER HIGH"
        else:
            color = "#777"
            text = "low"
        self.lbl_trigger_status.setStyleSheet(
            f"background:{color}; color:#fff; padding:4px; border-radius:4px;"
        )
        self.lbl_trigger_status.setText(text)

    # ------------------------------------------------------------------
    def _update_status_bar(self) -> None:
        connected = self._modbus.is_connected()
        self._status_connected.setText("Connected" if connected else "Disconnected")
        last_error = self._modbus.last_error or "–"
        self._status_error.setText(f"Last error: {last_error}")
        ts = self._modbus.last_read_timestamp
        if isinstance(ts, datetime):
            self._status_last_read.setText(f"Last read: {ts.strftime('%H:%M:%S')}")
        else:
            self._status_last_read.setText("Last read: –")

    def _save_and_apply(self) -> None:
        stored = self._db.save_modbus_settings(self._collect_settings())
        self._settings = stored
        self.settings_applied.emit(stored)
        self.accept()

    def closeEvent(self, event) -> None:
        try:
            self._trigger_timer.stop()
            self._status_timer.stop()
            self._executor.shutdown(wait=False, cancel_futures=True)
        finally:
            super().closeEvent(event)


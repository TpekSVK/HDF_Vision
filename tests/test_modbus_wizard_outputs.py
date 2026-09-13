from unittest.mock import Mock
from PySide6.QtWidgets import QApplication, QMessageBox
from app.services.modbus_service import ModbusConfig
from app.ui.modbus_wizard import ModbusWizard


def test_range_conflicts_and_nonblocking_save(monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = Mock()
    service.get_config.return_value = ModbusConfig(ok_coil=0, nok_coil=1, heartbeat_coil=-1)
    peer = ModbusConfig(ok_coil=0, nok_coil=7, heartbeat_coil=-1, enabled=True)
    dialog = ModbusWizard(service, peer_configs=lambda: [('Kamera 2', peer)])
    assert 'Kamera 2: OK' in dialog.lbl_output_warning.text()
    for spin in (dialog.spin_ok, dialog.spin_nok, dialog.spin_heartbeat):
        assert spin.minimum() == -1 and spin.maximum() == 7
    dialog.spin_nok.setValue(0)
    assert 'táto kamera: NOK' in dialog.lbl_output_warning.text()
    warning = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    dialog._on_accept()
    warning.assert_called_once()
    service.set_config.assert_called_once()
    peer.host = 'different-device'
    dialog.spin_nok.setValue(1)
    assert dialog._output_conflicts() == []
    dialog.close()
    app.processEvents()


def test_invalid_saved_output_is_disabled_not_redirected():
    app = QApplication.instance() or QApplication([])
    service = Mock()
    service.get_config.return_value = ModbusConfig(nok_coil=8)
    dialog = ModbusWizard(service)
    assert dialog.spin_nok.value() == -1
    assert 'mimo rozsahu' in dialog.lbl_output_warning.text()
    service.set_config.assert_not_called()
    dialog.close()
    app.processEvents()

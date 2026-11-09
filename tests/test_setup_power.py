import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QScrollArea

from app.services.security_service import SecurityService
from app.services.workstation_devices import StationBinding, WorkstationDevices
from app.ui.main_window import MainWindow
from app.ui.power_dialog import PowerDialog
from app.ui.theme import DARK_STYLE
from app.ui.workstation_window import WorkstationWindow
from test_workstation_window import FakeWorkspace


def test_power_menu_uses_recipe_password_and_native_text_field(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(DARK_STYLE)
    monkeypatch.delenv('HDF_ADMIN_MODE', raising=False)
    security = SecurityService(tmp_path/'security.json')
    security.set_password('A1!á')
    dialog = PowerDialog(security)
    dialog.show(); app.processEvents()
    try:
        assert dialog.locked.isVisible() and not dialog.actions.isVisible()
        dialog._choose('exit')
        assert dialog.action is None
        dialog.password.setText('wrong'); dialog.unlock_button.click()
        assert dialog.error.text() and not dialog.actions.isVisible()
        dialog.password.setText('A1!á')
        assert dialog.password.inputMethodHints() & Qt.ImhHiddenText
        assert dialog.password.height() >= 44
        dialog.unlock_button.click(); app.processEvents()
        assert dialog.actions.isVisible() and not dialog.locked.isVisible()
        assert not dialog.password.text()
        dialog.action_buttons['exit'].click()
        assert dialog.result() == QDialog.Accepted and dialog.action == 'exit'
        assert not dialog._authenticated
        again = PowerDialog(security)
        assert not again.actions.isVisible() and not again.locked.isHidden()
        again.reject()
    finally:
        dialog.reject(); app.processEvents()


def test_unprotected_and_admin_power_menu(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.delenv('HDF_ADMIN_MODE', raising=False)
    security = SecurityService(tmp_path/'security.json')
    dialog = PowerDialog(security)
    dialog.show(); app.processEvents()
    assert dialog.actions.isVisible() and dialog.locked.isHidden()
    # Enabling the shared password from the terminal also protects an open menu.
    security.set_password('secret')
    dialog.action_buttons['shutdown'].click(); app.processEvents()
    assert dialog.action is None and dialog.locked.isVisible()
    dialog.reject()
    monkeypatch.setenv('HDF_ADMIN_MODE', '1')
    dialog = PowerDialog(security)
    dialog.show(); app.processEvents()
    assert dialog.actions.isVisible()
    dialog.action_buttons['exit'].click()
    assert dialog.action == 'exit'


@pytest.mark.parametrize('action, code', [('exit', 0), ('reboot', 11), ('shutdown', 10)])
def test_power_actions_wait_for_all_stations(tmp_path, monkeypatch, action, code):
    app = QApplication.instance() or QApplication([])
    class DeferredWorkspace(FakeWorkspace):
        def closeEvent(self, event):
            if self._close_ready:
                event.accept()
            else:
                event.ignore()
    WorkstationDevices(tmp_path/'workstation_devices.json').save([
        StationBinding(f'camera_{i}', f'Kamera {i}', f'cam{i}', f'pico{i}') for i in range(1,4)])
    window = WorkstationWindow(tmp_path, workspace_factory=DeferredWorkspace)
    exits = []
    monkeypatch.setattr('app.ui.workstation_window.QApplication.exit', exits.append)
    window.show(); window.request_power_action(action); app.processEvents()
    assert not exits
    for workspace in window.workspaces[:-1]:
        workspace._close_ready = True
        workspace.station_closed.emit(workspace.camera_id)
        app.processEvents()
    assert not exits
    workspace = window.workspaces[-1]
    workspace._close_ready = True
    workspace.station_closed.emit(workspace.camera_id)
    app.processEvents(); app.processEvents()
    assert exits and set(exits) == {code}
    assert window._power_exit_code == code
    window.close(); app.processEvents()


@pytest.mark.parametrize('height', [768, 704])
def test_setup_fits_without_scrolling(tmp_path, monkeypatch, height):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(DARK_STYLE)
    monkeypatch.setattr(MainWindow, '_start_production', lambda self:False)
    monkeypatch.setattr(MainWindow, '_refresh_manual_light', lambda self:None)
    WorkstationDevices(tmp_path/'workstation_devices.json').save([
        StationBinding('camera_1','Kamera 1','test-camera','test-pico')])
    window = WorkstationWindow(tmp_path)
    window.resize(1366,height); window.show()
    workspace = window.workspaces[0]
    workspace.stack.setCurrentWidget(workspace.panel_setup)
    workspace._sync_mode_chrome()
    app.processEvents()
    try:
        assert window.size().height() == height
        assert window.minimumSizeHint().height() <= height
        assert not workspace.panel_setup.findChildren(QScrollArea)
        assert isinstance(workspace.chk_debug_overlay, QPushButton)
        assert workspace.chk_debug_overlay.isCheckable()
        for button in (workspace.btn_power, workspace.btn_wizard, workspace.btn_new, workspace.btn_ren,
                       workspace.btn_del, workspace.btn_modbus_wizard, workspace.btn_pico_wizard,
                       workspace.btn_export, workspace.btn_change_log, workspace.chk_debug_overlay):
            assert button.isVisible()
            assert button.height() >= 44
            assert window.rect().contains(QRect(button.mapTo(window, QPoint()), button.size()))
        assert workspace.cmb_capture_mode.height() >= 44
    finally:
        workspace._close_ready = True
        window.close(); app.processEvents()


def test_close_application_requires_confirmation(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PySide6.QtWidgets import QMessageBox
    class AcceptedMenu:
        action = 'exit'
        def __init__(self, *args): pass
        def exec(self): return QDialog.DialogCode.Accepted
    monkeypatch.setattr('app.ui.main_window.PowerDialog', AcceptedMenu)
    host = SimpleNamespace(security=object(), _request_host_power_action=Mock())
    monkeypatch.setattr('app.ui.main_window.QMessageBox.question', lambda *args: QMessageBox.No)
    MainWindow._show_power_menu(host)
    host._request_host_power_action.assert_not_called()
    monkeypatch.setattr('app.ui.main_window.QMessageBox.question', lambda *args: QMessageBox.Yes)
    MainWindow._show_power_menu(host)
    host._request_host_power_action.assert_called_once_with('exit')


@pytest.mark.parametrize('action', ['exit', 'reboot', 'shutdown', None])
def test_real_power_dialog_dispatches_action_after_exec(tmp_path, monkeypatch, action):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QMessageBox
    from app.services.camera_service import CameraService
    from app.services.pico_service import PicoService
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, '_start_production', lambda self: False)
    monkeypatch.setattr(MainWindow, '_refresh_manual_light', lambda self: None)
    window = MainWindow(camera=CameraService('/dev/test-only'),
        pico=PicoService(port='/dev/test-only'), data_root=tmp_path/'station')
    window.security = SecurityService(tmp_path/'security.json')
    window._request_host_power_action = Mock()
    monkeypatch.setattr('app.ui.main_window.QMessageBox.question', lambda *args: QMessageBox.Yes)
    window.show(); app.processEvents()
    dialogs = []

    def choose():
        dialog = app.activeModalWidget()
        dialogs.append(dialog)
        if isinstance(dialog, PowerDialog):
            if action is None:
                dialog.reject()
            else:
                dialog.action_buttons[action].click()

    QTimer.singleShot(0, choose)
    try:
        window._show_power_menu()
        assert dialogs and isinstance(dialogs[0], PowerDialog)
        if action is None:
            window._request_host_power_action.assert_not_called()
        else:
            window._request_host_power_action.assert_called_once_with(action)
    finally:
        window._close_ready = True
        window.close(); app.processEvents()


def test_terminal_password_is_shared_with_power_menu(tmp_path, monkeypatch, capsys):
    from app.tools import security_cli
    app = QApplication.instance() or QApplication([])
    monkeypatch.delenv('HDF_ADMIN_MODE', raising=False)
    security = SecurityService(tmp_path/'security.json')
    monkeypatch.setattr(security_cli, 'SecurityService', lambda: security)
    monkeypatch.setattr(security_cli.getpass, 'getpass', lambda prompt: 'Shared-pass')
    assert security_cli.main(['set-password']) == 0
    assert security_cli.main(['status']) == 0
    assert 'Ochrana receptov a napájania: ZAPNUTÁ' in capsys.readouterr().out
    dialog = PowerDialog(security)
    dialog.show(); app.processEvents()
    assert dialog.locked.isVisible()
    dialog.password.setText('Shared-pass'); dialog.unlock_button.click()
    assert dialog.actions.isVisible()
    dialog.reject()
    monkeypatch.setattr('builtins.input', lambda prompt: 'y')
    assert security_cli.main(['remove-password']) == 0
    assert not security.has_password()
    dialog = PowerDialog(security)
    dialog.show(); app.processEvents()
    assert dialog.actions.isVisible()
    dialog.reject()

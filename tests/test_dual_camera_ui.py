from types import SimpleNamespace
from unittest.mock import Mock
import time
import json

import pytest
from PySide6.QtCore import Qt, QEvent, QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from app.services.modbus_service import ModbusConfig
from app.services.workstation_devices import StationBinding, WorkstationDevices
from app.ui.golden_wizard.view_config_dialog import ViewConfigDialog
from app.ui.modbus_wizard import ModbusWizard
from app.ui.main_window import MainWindow
from app.ui.workstation_window import WorkstationWindow
from app.ui.theme import DARK_STYLE


@pytest.fixture
def app(monkeypatch):
    application = QApplication.instance() or QApplication([])
    previous = application.styleSheet()
    application.setStyleSheet(DARK_STYLE)
    def unexpected_message(*args, **kwargs):
        raise AssertionError(f'Unexpected dialog: {args[1:]}')
    monkeypatch.setattr(QMessageBox, 'critical', unexpected_message)
    yield application
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.setStyleSheet(previous)


def test_shared_pico_opens_one_recipe_workspace_and_two_camera_history_sources(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, '_start_production', lambda self: False)
    monkeypatch.setattr(MainWindow, '_refresh_manual_light', lambda self: None)
    monkeypatch.setattr(MainWindow, '_stop_live_and_light', lambda self: None)
    WorkstationDevices(tmp_path / 'workstation_devices.json').save([
        StationBinding('camera_1', 'Predná kamera', 'USB-A', 'PICO', 1),
        StationBinding('camera_2', 'Zadná kamera', 'USB-B', 'PICO', 2)])
    (tmp_path / 'config.json').write_text(json.dumps({'capture_mode': 'trigger'}))
    shell = WorkstationWindow(tmp_path)
    shell.resize(1366, 704); shell.show(); app.processEvents()
    assert len(shell.workspaces) == 1
    assert shell.overview_button.text() == 'Všetky kamery (2)'
    workspace = shell.workspaces[0]
    assert workspace.capture_mode == 'trigger' and not workspace._initial_capture_mode
    assert workspace.workcell is not None
    assert set(workspace.runtime.runtimes) == {'camera_1', 'camera_2'}
    assert len({source['db_path'] for source in workspace.results_sources()}) == 1
    assert [source['camera_id'] for source in workspace.results_sources()] == ['camera_1', 'camera_2']
    workspace.cam.registry.select('camera_2')
    assert workspace.pico.channel == 2
    assert workspace.cam.width == 1280
    recipe = workspace.current_recipe_name()
    workspace.recipes.update_view(recipe, 'view_1', view_name='celust',
        frame_source_view_id=None, camera_profile=None, settle_ms=None, output_group='group_1',
        trigger_mode='timed', trigger_interval_ms=1000)
    workspace.recipes.add_view(recipe, view_id='view_2', view_name='diel',
        camera_id='camera_2', output_group='group_2')
    workspace._refresh_views()
    assert workspace.station_title.text() == 'celust'
    workspace._on_view_selected_view('view_2')
    assert workspace.station_title.text() == 'diel'
    assert workspace.btn_trigger.text() == 'Trigger'
    workspace._workcell_outcome(SimpleNamespace(group_id='group_2', status='ok', reason=''))
    assert workspace.lbl_status.full_text == 'OK'
    assert 'group_2' not in workspace._run_status_message.text()
    workspace.stack.setCurrentWidget(workspace.panel_setup)
    workspace._sync_mode_chrome()
    assert workspace.station_title.isHidden() and workspace.station_title.text() == ''
    workspace.stack.setCurrentWidget(workspace.panel_run)
    workspace._sync_mode_chrome()
    assert workspace.station_title.text() == 'diel'
    workspace._close_ready = True
    shell._closing = True
    workspace.close(); app.processEvents(); shell.close(); app.processEvents()
    shell.deleteLater()


@pytest.mark.parametrize('height', [768, 704])
def test_view_dialog_keeps_camera_and_group_settings_with_touch_footer(app, height):
    current = dict(width=1280, height=720, fps=60, pixel_format='Y8', exposure_us=1000)
    dialog = ViewConfigDialog(view_id='view_2', name='Zadná forma', available_resolutions=[],
        current_camera=current, camera_profile=current,
        trigger_interval_ms=1000,
        available_cameras=[('camera_1', 'Predná kamera'), ('camera_2', 'Zadná kamera')],
        camera_id='camera_2', output_group='group_1', output_mode='AND', and_timeout_ms=1250)
    dialog.setWindowState(Qt.WindowNoState); dialog.resize(760, height - 80); dialog.show()
    app.processEvents()
    assert dialog._camera_combo.currentData() == 'camera_2'
    assert not dialog._and_timeout.isHidden()
    assert not dialog._branch_enabled_checkbox.isEnabled()
    assert dialog._accept_button.geometry().height() >= 32
    assert dialog._accept_button.mapTo(dialog, dialog._accept_button.rect().bottomRight()).y() < dialog.height()
    dialog.grab().save(f'/testdeps/dual-view-{height}.png')
    dialog.accept()
    values = dialog.values()
    assert values['camera_id'] == 'camera_2' and values['output_group'] == 'group_1'
    assert values['output_mode'] == 'AND' and values['and_timeout_ms'] == 1250
    dialog.deleteLater()


def test_modbus_groups_have_three_relays_each_and_conflicting_roles_cannot_save(app, monkeypatch):
    modbus = Mock()
    modbus.get_config.return_value = ModbusConfig()
    dialog = ModbusWizard(modbus, grouped=True)
    dialog.resize(760, 640); dialog.show(); app.processEvents()
    second = dialog.group_outputs['group_2']
    assert [second['ok'].itemData(i) for i in range(second['ok'].count())] == [-1, 3, 4, 5]
    second['heartbeat'].setCurrentIndex(second['heartbeat'].findData(5))
    first = dialog.group_outputs['group_1']
    first['ok'].setCurrentIndex(first['ok'].findData(1))
    warning = Mock(); monkeypatch.setattr(QMessageBox, 'warning', warning)
    dialog._on_accept()
    assert warning.called and not modbus.set_config.called
    first['nok'].setCurrentIndex(first['nok'].findData(0))
    dialog.grab().save('/testdeps/dual-modbus-groups.png')
    dialog._on_accept()
    cfg = modbus.set_config.call_args[0][0]
    assert cfg.group_config('group_1').ok_coil == 1
    assert cfg.group_config('group_2').heartbeat_coil == 5
    dialog.close()
    dialog.deleteLater()


def test_pico_channel_switch_preserves_drafts_and_restores_selected_view_camera(app, tmp_path):
    from app.services.camera_registry import CameraRegistry, PicoRouter
    from app.services.pico_config_service import PicoConfigService
    from app.ui.pico_wizard import PicoWizard
    from test_pico_wizard import FakePico
    one,two=FakePico(),FakePico()
    one.channel,two.channel=1,2
    registry=CameraRegistry({'camera_1':object(),'camera_2':object()}, {'camera_1':one,'camera_2':two})
    dialog=PicoWizard(PicoRouter(registry),PicoConfigService(tmp_path/'pico.json'))
    dialog._profile_widgets['V1']['pulse_ms'].setValue(321)
    dialog.camera_channel.setCurrentIndex(dialog.camera_channel.findData('camera_2'))
    dialog._profile_widgets['V1']['pulse_ms'].setValue(456)
    dialog.camera_channel.setCurrentIndex(dialog.camera_channel.findData('camera_1'))
    assert dialog._profile_widgets['V1']['pulse_ms'].value()==321
    dialog.camera_channel.setCurrentIndex(dialog.camera_channel.findData('camera_2'))
    dialog._save()
    assert ('V1','PULSE',321) in one.commands and ('V1','PULSE',456) in two.commands
    assert registry.selected=='camera_1'
    dialog.deleteLater()

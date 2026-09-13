from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QMainWindow
from app.services.inspection_controller import InspectionController, InspectionState
from app.services.workstation_devices import WorkstationDevices, StationBinding
from app.ui.workstation_window import WorkstationWindow
from app.ui.workstation_devices_dialog import WorkstationDevicesDialog


class FakeWorkspace(QMainWindow):
    station_closed = Signal(str)
    def __init__(self, **kwargs):
        super().__init__()
        self.station_name = kwargs['station_name']; self.camera_id = kwargs['camera_id']
        self.data_root = kwargs['data_root']
        self.camera, self.pico = kwargs['camera'], kwargs['pico']
        self.inspection = InspectionController(self.camera_id)
        self.inspection.prepare(lambda: None, lambda: None)
        self.runtime_worker = SimpleNamespace(busy=False)
        self._close_ready = False
    def closeEvent(self, event):
        self.inspection.pause(lambda: None, close=True)
        self._close_ready = True
        self.station_closed.emit(self.camera_id)
        event.accept()


def test_three_tabs_do_not_pause_background_station(tmp_path):
    app = QApplication.instance() or QApplication([])
    WorkstationDevices(tmp_path / 'workstation_devices.json').save([
        StationBinding(f'camera_{i}', f'Kamera {i}', f'usb{i}', f'pico{i}') for i in range(1, 4)])
    window = WorkstationWindow(tmp_path, workspace_factory=FakeWorkspace)
    window.show()
    for index in (1, 2, 0):
        window.tabs.setCurrentIndex(index); app.processEvents()
        assert all(w.inspection.state == InspectionState.READY for w in window.workspaces)
    assert window.workspaces[2].data_root == tmp_path / 'stations/camera_3'
    window.workspaces[0].inspection.pause(lambda: None)
    assert window.workspaces[2].inspection.admit('pico')[0] is not None
    request = window.workspaces[2].inspection._request
    window.workspaces[2].inspection.finish(request)
    window.close(); app.processEvents(); app.processEvents()
    assert all(w._close_ready for w in window.workspaces)


def test_empty_config_opens_no_camera_and_pairing_can_add_third(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = WorkstationWindow(tmp_path, workspace_factory=lambda **kw: (_ for _ in ()).throw(AssertionError('guessed pair')))
    assert not window.workspaces
    monkeypatch.setattr('app.ui.workstation_devices_dialog.discover_devices', lambda: ([], []))
    dialog = WorkstationDevicesDialog([])
    dialog.add_station(); dialog.add_station()
    assert [b.id for b in dialog.bindings()] == ['camera_1', 'camera_2', 'camera_3']
    dialog.reject(); window.close()


def test_real_workspace_uses_its_own_recipes_and_database(tmp_path, monkeypatch):
    from app.ui.main_window import MainWindow
    from app.services.camera_service import CameraService
    from app.services.pico_service import PicoService
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, '_start_production', lambda self: False)
    monkeypatch.setattr(MainWindow, '_refresh_manual_light', lambda self: None)
    # No hardware operation and no production data; render the real widget tree.
    workspace = MainWindow(camera=CameraService('/dev/test-only'), pico=PicoService(port='/dev/test-only'),
        data_root=tmp_path / 'stations/camera_2', camera_id='camera_2', pico_id='pico_2', station_name='Kamera 2')
    workspace.show(); app.processEvents()
    assert workspace.recipes.base == tmp_path / 'stations/camera_2'
    assert workspace.db.db_path == tmp_path / 'stations/camera_2/HDF_Vision.db'
    assert workspace.runtime.camera_id == 'camera_2'
    assert workspace.inspection.camera_id == 'camera_2'
    assert not (tmp_path / 'recipes').exists()
    workspace._close_ready = True
    workspace.close(); app.processEvents()


def test_host_power_exit_waits_for_all_stations(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    WorkstationDevices(tmp_path / 'workstation_devices.json').save([
        StationBinding(f'camera_{i}', f'Kamera {i}', f'usb{i}', f'pico{i}') for i in range(1, 4)])
    window = WorkstationWindow(tmp_path, workspace_factory=FakeWorkspace)
    exits = []
    monkeypatch.setattr('app.ui.workstation_window.QApplication.exit', lambda code: exits.append(code))
    window.show()
    window.request_power_action('reboot')
    assert all(w._close_ready for w in window.workspaces)
    app.processEvents(); app.processEvents()
    assert exits and all(code == 11 for code in exits)


def test_real_golden_wizard_uses_bound_camera_and_station_recipe_root(tmp_path):
    import numpy as np
    from app.services.recipe_service import RecipeService
    from app.services.storage_service import save_golden
    from app.ui.golden_wizard.golden_wizard import GoldenWizard
    app = QApplication.instance() or QApplication([])
    recipes = RecipeService(tmp_path / 'stations/camera_3')
    recipes.create('default')
    image = np.full((30, 40), 120, np.uint8)
    save_golden(image, 'default', base_dir=recipes.base)
    recipes.load('default')
    calls = []
    camera = SimpleNamespace(width=40, height=30, fps=60, pixel_format='Y8', exposure_us=1000,
        start=lambda **kw: calls.append('start3'), last_frame=lambda **kw: image.copy())
    wizard = GoldenWizard(camera, recipes, get_capture_mode=lambda: 'master')
    wizard.show(); app.processEvents()
    np.testing.assert_array_equal(wizard._current_golden_image(), image)
    wizard._start_preview_session(); wizard._live_tick()
    assert calls == ['start3']
    wizard._stop_preview_session()
    wizard.close(); app.processEvents()
    recipes.db.close()

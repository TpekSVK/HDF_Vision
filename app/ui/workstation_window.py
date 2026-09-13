"""Application shell for independently running camera/Pico workspaces."""
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTabWidget, QMessageBox, QDialog, QApplication)
from app.services.workstation_devices import WorkstationDevices, resolve_serial
from app.services.camera_service import CameraService
from app.services.pico_service import PicoService
from app.services.inspection_controller import InspectionState
from app.ui.workstation_devices_dialog import WorkstationDevicesDialog
from app.ui.main_window import MainWindow
from app.services.security_service import SecurityService
from app.ui.password_dialog import authorize_recipe_write


class WorkstationWindow(QMainWindow):
    def __init__(self, data_root=Path('/data'), *, workspace_factory=MainWindow):
        super().__init__()
        self.data_root = Path(data_root)
        self.devices = WorkstationDevices(self.data_root / 'workstation_devices.json')
        self.security = SecurityService(self.data_root / "security.json")
        self.workspace_factory = workspace_factory
        self.workspaces = []
        self._closing = False
        self._power_exit_code = None
        self._reconfigure = None
        self.setWindowTitle('HDF Vision — kamerové stanice')
        root = QWidget(); layout = QVBoxLayout(root)
        top = QHBoxLayout()
        self.summary = QLabel('Priraďte kamery a Pico. Žiadna kontrola nie je spustená.')
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        self.configure = QPushButton('Kamery a Pico')
        self.configure.clicked.connect(self.configure_devices)
        top.addWidget(self.configure); layout.addLayout(top)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        try:
            bindings = self.devices.load()
        except (OSError, ValueError) as exc:
            bindings = []
            self.summary.setText(f'Priradenie zariadení treba opraviť: {exc}')
        self._open_workspaces(bindings)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(500)

    def _open_workspaces(self, bindings):
        for binding in bindings:
            # Only explicit pairing creates a station. Missing hardware cannot
            # cause the serial services to choose another station's device.
            camera = CameraService(device=f'/dev/unavailable-{binding.id}',
                device_resolver=lambda serial=binding.camera_serial: resolve_serial(serial, 'camera'))
            pico = PicoService(port_resolver=lambda serial=binding.pico_serial: resolve_serial(serial, 'pico'))
            workspace = self.workspace_factory(camera=camera, pico=pico,
                data_root=binding.data_root(self.data_root), camera_id=binding.id,
                pico_id=binding.pico_serial, station_name=binding.name, host_power_action=self.request_power_action)
            workspace.modbus_peer_configs = lambda current=workspace: [
                (peer.station_name, peer.modbus.get_config())
                for peer in self.workspaces if peer is not current]
            workspace.setWindowFlags(Qt.Widget)
            workspace.station_closed.connect(self._station_closed)
            self.workspaces.append(workspace)
            self.tabs.addTab(workspace, binding.name)

    def refresh_status(self):
        names = {'paused': 'SETUP', 'preparing': 'príprava / zastavovanie',
                 'ready': 'RUN pripravený', 'busy': 'kontroluje',
                 'error': 'CHYBA', 'closed': 'zastavená'}
        statuses = []
        for index, workspace in enumerate(self.workspaces):
            snapshot = workspace.inspection.snapshot()
            label = names[snapshot['state']]
            counts = snapshot['counts']
            text = f'{workspace.station_name}: {label}'
            self.tabs.setTabText(index, text)
            statuses.append(f'{text} · dokončené {counts.get("completed", 0)} · '
                f'chyby {counts.get("failed", 0) + counts.get("preparation_failed", 0)} · '
                f'neprijaté {sum(v for k, v in counts.items() if k.startswith("rejected_"))}')
        if statuses:
            self.summary.setText('   |   '.join(statuses))
        can_configure = all(w.inspection.state in {InspectionState.PAUSED, InspectionState.ERROR}
                            and not w.runtime_worker.busy for w in self.workspaces)
        self.configure.setEnabled(can_configure and not self._closing and self._reconfigure is None)
        self.configure.setToolTip('Pred zmenou priradenia prepnite všetky stanice do SETUP.')

    def configure_devices(self):
        if not authorize_recipe_write(self, self.security):
            return
        # Recheck at action time; the status timer is only presentation.
        if any(w.inspection.state not in {InspectionState.PAUSED, InspectionState.ERROR}
               or w.runtime_worker.busy for w in self.workspaces):
            return
        try:
            old = self.devices.load()
        except (OSError, ValueError):
            old = []
        # All lanes are paused; release exclusive USB handles for operator verification.
        for workspace in self.workspaces:
            workspace.cam.stop(caller="device_pairing")
            workspace.pico.close()
        dialog = WorkstationDevicesDialog(old, self)
        if dialog.exec() != QDialog.Accepted:
            return
        bindings = dialog.bindings()
        try:
            self.devices.save(bindings)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Priradenie sa neuložilo', str(exc)); return
        if not self.workspaces:
            self._open_workspaces(bindings)
            return
        self._reconfigure = bindings
        self.configure.setEnabled(False)
        for workspace in self.workspaces:
            workspace.close()

    def _station_closed(self, _camera_id):
        # A child emits before its close event returns; teardown on next UI turn.
        QTimer.singleShot(0, self._finish_closing)

    def _finish_closing(self):
        if not all(w._close_ready for w in self.workspaces):
            return
        if self._closing:
            self.close()
        elif self._reconfigure is not None:
            for workspace in self.workspaces:
                self.tabs.removeTab(self.tabs.indexOf(workspace))
                workspace.deleteLater()
            self.workspaces = []
            bindings, self._reconfigure = self._reconfigure, None
            self._open_workspaces(bindings)

    def request_power_action(self, action):
        if action not in {"shutdown", "reboot"}:
            raise ValueError("Neznáma akcia napájania.")
        self._power_exit_code = 10 if action == "shutdown" else 11
        self.close()

    def closeEvent(self, event):
        if all(w._close_ready for w in self.workspaces):
            self.timer.stop()
            if self._power_exit_code is not None:
                QApplication.exit(self._power_exit_code)
            event.accept()
            return
        event.ignore()
        self._closing = True
        self.configure.setEnabled(False)
        for workspace in self.workspaces:
            if not workspace._close_ready:
                workspace.close()

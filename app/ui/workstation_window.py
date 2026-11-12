"""Application shell for independently running camera/Pico workspaces."""
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMessageBox, QDialog, QApplication,
    QStackedWidget, QScrollArea, QGridLayout, QFrame, QScroller)
from app.services.workstation_devices import WorkstationDevices, resolve_serial
from app.services.camera_service import CameraService
from app.services.pico_service import PicoService
from app.services.inspection_controller import InspectionState
from app.ui.workstation_devices_dialog import WorkstationDevicesDialog
from app.ui.main_window import MainWindow
from app.services.security_service import SecurityService
from app.ui.password_dialog import authorize_recipe_write
from app.ui.theme import refresh_style


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
        root = QWidget(); root.setObjectName('stationShell')
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        header = QFrame(); header.setProperty('role', 'panel')
        top = QHBoxLayout(header)
        top.setContentsMargins(10, 6, 10, 6)
        brand = QLabel('HDF VISION'); brand.setProperty('role', 'appTitle')
        top.addWidget(brand); top.addStretch(1)
        self.navigation = {}
        for key, label in [('RUN', 'RUN'), ('RESULTS', 'VÝSLEDKY'),
                           ('SETUP', 'NASTAVENIA'), ('TROUBLESHOOTING', 'DIAGNOSTIKA')]:
            button = self._station_button(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, mode=key: self._navigate(mode))
            self.navigation[key] = button
            top.addWidget(button)
        self.summary = QLabel('Priraďte kamery a Pico. Žiadna kontrola nie je spustená.')
        self.summary.setWordWrap(True)
        self.configure = self._station_button('Kamery a Pico')
        self.configure.clicked.connect(self.configure_devices)
        top.addWidget(self.configure); layout.addWidget(header)
        self.station_row = QHBoxLayout()
        self.station_buttons = []
        self.overview_button = self._station_button('Všetky kamery')
        self.overview_button.setCheckable(True)
        self.overview_button.clicked.connect(self.show_overview)
        self.station_row.addWidget(self.overview_button)
        layout.addLayout(self.station_row)
        # Keep a persistent stack of independent runtimes. Only the station
        # ribbon changes visibility; it never recreates or pauses a workspace.
        self.tabs = QStackedWidget()
        self.tabs.currentChanged.connect(self._selected_station_changed)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.tabs)
        self.overview = QScrollArea()
        self.overview.setWidgetResizable(True)
        self.overview.setFrameShape(QFrame.NoFrame)
        overview_content = QWidget()
        self.overview_grid = QGridLayout(overview_content)
        self.overview_grid.setAlignment(Qt.AlignTop)
        self.overview_grid.setSpacing(12)
        self.overview.setWidget(overview_content)
        QScroller.grabGesture(self.overview.viewport(), QScroller.TouchGesture)
        self.pages.addWidget(self.overview)
        self.overview_cards = []
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.summary)
        self.summary.setVisible(False)
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
        self.refresh_status()

    @staticmethod
    def _station_button(text):
        button = QPushButton(text)
        button.setProperty('stationControl', True)
        return button

    def _navigate(self, mode):
        workspace = self.tabs.currentWidget()
        if workspace is not None and hasattr(workspace, '_request_mode'):
            self.pages.setCurrentWidget(self.tabs)
            workspace._request_mode(mode)
        self.refresh_status()

    def _selected_station_changed(self, _index):
        if hasattr(self, 'pages'):
            self.pages.setCurrentWidget(self.tabs)
        for workspace in self.workspaces:
            if hasattr(workspace, 'run_options'):
                workspace.run_options.hide()
        if hasattr(self, 'overview_cards'):
            self.refresh_status()

    def select_station(self, index):
        self.tabs.setCurrentIndex(index)
        self.pages.setCurrentWidget(self.tabs)
        self.refresh_status()

    def show_overview(self):
        self.pages.setCurrentWidget(self.overview)
        for workspace in self.workspaces:
            if hasattr(workspace, 'run_options'):
                workspace.run_options.hide()
        self.refresh_status()

    def _arrange_overview(self):
        columns = max(1, min(4, self.width() // 300))
        for index, card in enumerate(self.overview_cards):
            self.overview_grid.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'overview_cards'):
            self._arrange_overview()
            self.refresh_status()

    def _open_workspaces(self, bindings):
        shared_registry = None
        if bindings and all(binding.pico_channel is not None for binding in bindings):
            from app.services.camera_registry import CameraRegistry
            from app.services.dual_pico_service import DualPicoTransport
            transport = DualPicoTransport(port_resolver=lambda serial=bindings[0].pico_serial:
                                          resolve_serial(serial, 'pico'))
            transport.usb_serial = bindings[0].pico_serial
            shared_registry = CameraRegistry(
                {binding.id: CameraService(device=f'/dev/unavailable-{binding.id}',
                    width=1280, height=720, fps=60,
                    device_resolver=lambda serial=binding.camera_serial: resolve_serial(serial, 'camera'))
                 for binding in bindings},
                {binding.id: transport.channels[binding.pico_channel] for binding in bindings},
                names={binding.id: binding.name for binding in bindings},
                serials={binding.id: binding.camera_serial for binding in bindings}, transport=transport)
            camera_bindings = bindings
            bindings = bindings[:1]
        for binding in bindings:
            # Only explicit pairing creates a station. Missing hardware cannot
            # cause the serial services to choose another station's device.
            camera = CameraService(device=f'/dev/unavailable-{binding.id}',
                device_resolver=lambda serial=binding.camera_serial: resolve_serial(serial, 'camera'))
            pico = PicoService(port_resolver=lambda serial=binding.pico_serial: resolve_serial(serial, 'pico'))
            if shared_registry is not None:
                from app.services.camera_registry import CameraRouter, PicoRouter
                camera, pico = CameraRouter(shared_registry), PicoRouter(shared_registry)
            workspace = self.workspace_factory(camera=camera, pico=pico,
                data_root=self.data_root if shared_registry is not None else binding.data_root(self.data_root), camera_id=binding.id,
                pico_id=binding.pico_serial, station_name=binding.name, host_power_action=self.request_power_action)
            workspace.modbus_peer_configs = lambda current=workspace: [
                (peer.station_name, peer.modbus.get_config())
                for peer in self.workspaces if peer is not current]
            workspace.results_sources = lambda: [
                dict(id=peer.camera_id, name=peer.station_name, db_path=peer.db.db_path,
                     active=peer.mode == 'RUN') for peer in self.workspaces]
            if shared_registry is not None:
                workspace.station_name = 'Kamerová zostava'
                workspace.results_sources = lambda current=workspace, registry=shared_registry: [
                    dict(id=ident, name=registry.names[ident], db_path=current.db.db_path,
                         camera_id=ident, active=current.mode == 'RUN') for ident in registry.cameras]
            workspace.setWindowFlags(Qt.Widget)
            if hasattr(workspace, 'use_shared_navigation'):
                workspace.use_shared_navigation()
            workspace.station_closed.connect(self._station_closed)
            self.workspaces.append(workspace)
            self.tabs.addWidget(workspace)
            card = self._station_button(binding.name)
            card.setMinimumHeight(160)
            card.clicked.connect(lambda checked=False, i=len(self.workspaces)-1: self.select_station(i))
            self.overview_cards.append(card)
        self._arrange_overview()
        self.refresh_status()

    def refresh_status(self):
        names = {'paused': 'SETUP', 'preparing': 'príprava / zastavovanie',
                 'ready': 'RUN pripravený', 'busy': 'kontroluje',
                 'error': 'CHYBA', 'closed': 'zastavená'}
        statuses = []
        overview_visible = self.pages.currentWidget() is self.overview
        visible_count = max(1, min(4, (self.width() - 230) // 230))
        visible_indices = list(range(min(visible_count, len(self.workspaces))))
        selected = self.tabs.currentIndex()
        if selected >= len(visible_indices) and visible_indices:
            visible_indices[-1] = selected
        while len(self.station_buttons) < len(visible_indices):
            button = self._station_button('')
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, b=button: self.select_station(b.property('stationIndex')))
            self.station_row.insertWidget(len(self.station_buttons), button, 1)
            self.station_buttons.append(button)
        for slot, button in enumerate(self.station_buttons):
            button.setVisible(slot < len(visible_indices))
        for index, workspace in enumerate(self.workspaces):
            snapshot = workspace.inspection.snapshot()
            label = names[snapshot['state']]
            counts = snapshot['counts']
            text = f'{workspace.station_name}: {label}'
            result_card = getattr(workspace, 'run_status_card', None)
            result = result_card.property('status') if result_card is not None else None
            if result not in {'ok', 'nok', 'warn'}:
                result = None
            status = 'nok' if snapshot['state'] == 'error' else (
                str(result).lower() if snapshot['state'] in {'ready', 'busy'} and result else 'idle')
            suffix = f' · {str(result).upper()}' if result and snapshot['state'] in {'ready', 'busy'} else ''
            if index < len(self.overview_cards):
                card = self.overview_cards[index]
                recipe = workspace.current_recipe_name() if hasattr(workspace, 'current_recipe_name') else '—'
                card.setText(f'{workspace.station_name}\n{label}{suffix}\nRecept: {recipe}\nOtvoriť detail →')
                card.setProperty('status', status)
                card.setToolTip(snapshot['error'] or text)
                refresh_style(card)
            if index in visible_indices:
                button = self.station_buttons[visible_indices.index(index)]
                button.setProperty('stationIndex', index)
                short = {'ready': 'RUN', 'busy': 'Kontroluje', 'error': 'CHYBA',
                         'paused': 'SETUP', 'preparing': 'Príprava', 'closed': 'Zastavená'}[snapshot['state']]
                button.setText(f'{workspace.station_name} · {short}{suffix}')
                button.setProperty('status', status)
                button.setChecked(index == selected and not overview_visible)
                button.setToolTip(snapshot['error'] or text)
                refresh_style(button)
            statuses.append(f'{text} · dokončené {counts.get("completed", 0)} · '
                f'chyby {counts.get("failed", 0) + counts.get("preparation_failed", 0)} · '
                f'neprijaté {sum(v for k, v in counts.items() if k.startswith("rejected_"))}')
        if statuses:
            self.summary.setText('   |   '.join(statuses))
        self.summary.setVisible(not self.workspaces)
        camera_count = sum(len(w.cam.registry.cameras) if hasattr(getattr(w, 'cam', None), 'registry') else 1
                           for w in self.workspaces)
        self.overview_button.setText(f'Všetky kamery ({camera_count})')
        self.overview_button.setChecked(overview_visible)
        for key, button in self.navigation.items():
            workspace = self.tabs.currentWidget()
            source = {'RUN': 'btn_mode_run', 'SETUP': 'mode_btn', 'RESULTS': 'btn_results',
                      'TROUBLESHOOTING': 'btn_troubleshooting'}[key]
            local = getattr(workspace, source, None)
            button.setChecked(not overview_visible and local is not None and local.isChecked())
            button.setEnabled(workspace is not None)
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
            if getattr(workspace, 'workcell', None) is not None:
                for camera in workspace.cam.registry.cameras.values():
                    camera.stop(caller='device_pairing')
            else:
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
            for button in self.station_buttons:
                self.station_row.removeWidget(button)
                button.deleteLater()
            self.station_buttons = []
            for card in self.overview_cards:
                self.overview_grid.removeWidget(card)
                card.deleteLater()
            self.overview_cards = []
            self.tabs.blockSignals(True)
            for workspace in self.workspaces:
                self.tabs.removeWidget(workspace)
                workspace.deleteLater()
            self.workspaces = []
            self.tabs.blockSignals(False)
            bindings, self._reconfigure = self._reconfigure, None
            self._open_workspaces(bindings)

    def request_power_action(self, action):
        if action not in {"shutdown", "reboot", "exit"}:
            raise ValueError("Neznáma akcia napájania.")
        self._power_exit_code = {"shutdown": 10, "reboot": 11, "exit": 0}[action]
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

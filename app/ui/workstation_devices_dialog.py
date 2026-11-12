"""Explicit pairing; opening this dialog never starts cameras or pulses."""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox,
    QLabel, QDialogButtonBox, QMessageBox, QPushButton, QLineEdit, QGroupBox,
    QScrollArea, QWidget, QHBoxLayout)
from app.services.workstation_devices import StationBinding, WorkstationDevices, discover_devices


class WorkstationDevicesDialog(QDialog):
    def __init__(self, bindings, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Priradenie kamier a Pico')
        self.resize(760, 580)
        layout = QVBoxLayout(self)
        explanation = QLabel('Jedno spoločné Pico ovláda dve kamery. Vyberte kameru a jej kanál dosky.\n'
            'Kanál 1: trigger GP16 / svetlo GP17. Kanál 2: trigger GP18 / svetlo GP19.\n'
            'Kamera sa rozpoznáva podľa USB identity aj po reštarte.')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.pico = QComboBox()
        self.pico.setMinimumHeight(44)
        layout.addWidget(QLabel('Spoločné Pico:'))
        layout.addWidget(self.pico)
        self._initial_pico = bindings[0].pico_serial if bindings else ''
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); self.form_layout = QVBoxLayout(content)
        scroll.setWidget(content); layout.addWidget(scroll)
        self.rows = []
        for binding in bindings or [None]:
            self.add_station(binding)
        actions = QHBoxLayout()
        add = QPushButton('Pridať kameru'); add.clicked.connect(lambda: self.add_station())
        remove = QPushButton('Odobrať poslednú kameru'); remove.clicked.connect(self.remove_station)
        refresh = QPushButton('Obnoviť zoznam USB'); refresh.clicked.connect(self.refresh)
        for button in (add, remove, refresh): actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_station(self, binding=None):
        index = len(self.rows)
        box = QGroupBox(f'Priradenie kamery {index + 1}'); form = QFormLayout(box)
        name = QLineEdit(binding.name if binding else f'Kamera {index + 1}')
        if index >= 2:
            box.deleteLater()
            return
        camera, pico = QComboBox(), QComboBox()
        camera.setMinimumHeight(44); pico.setMinimumHeight(44)
        pico.addItem('Kamera 1 — GP16 / GP17', 1)
        pico.addItem('Kamera 2 — GP18 / GP19', 2)
        pico.setCurrentIndex(index)
        if binding and binding.pico_channel:
            pico.setCurrentIndex(pico.findData(binding.pico_channel))
        def update_default_name():
            if name.text() in ('Kamera 1', 'Kamera 2'):
                name.setText(f'Kamera {pico.currentData()}')
        pico.currentIndexChanged.connect(update_default_name)
        form.addRow('Názov kamery', name); form.addRow('Kamera', camera); form.addRow('Kanál dosky', pico)
        verify = QPushButton("Overiť dvojicu a zobraziť snímku")
        verify.setMinimumHeight(44)
        verify.clicked.connect(lambda: self.verify_pair(camera.currentData(), self.pico.currentData(), pico.currentData()))
        form.addRow(verify)
        self.form_layout.addWidget(box)
        self.rows.append((box, name, camera, pico, binding.id if binding else f'camera_{index + 1}'))
        self.refresh()
        if binding:
            if camera.findData(binding.camera_serial) < 0:
                camera.addItem(f'{binding.camera_serial} — teraz nepripojené', binding.camera_serial)
            camera.setCurrentIndex(camera.findData(binding.camera_serial))

    def remove_station(self):
        if len(self.rows) > 1:
            row = self.rows.pop(); row[0].deleteLater()

    def refresh(self):
        cameras, picos = discover_devices()
        selected_pico = self.pico.currentData() or self._initial_pico
        self.pico.clear(); self.pico.addItem('Vyberte Pico…', '')
        for device in picos:
            self.pico.addItem(f'{device.product} — {device.serial}', device.serial)
        if selected_pico and self.pico.findData(selected_pico) < 0:
            self.pico.addItem(f'{selected_pico} — teraz nepripojené', selected_pico)
        self.pico.setCurrentIndex(max(0, self.pico.findData(selected_pico)))
        for _, _, camera, _, _ in self.rows:
            selected = camera.currentData()
            camera.clear(); camera.addItem('Vyberte kameru…', '')
            for device in cameras:
                camera.addItem(f'{device.product} — {device.serial}', device.serial)
            if selected and camera.findData(selected) < 0:
                camera.addItem(f'{selected} — teraz nepripojené', selected)
            camera.setCurrentIndex(max(0, camera.findData(selected)))

    def bindings(self):
        return sorted([StationBinding(f'camera_{row[3].currentData()}', row[1].text().strip(),
                    row[2].currentData() or '', self.pico.currentData() or '', row[3].currentData())
                for row in self.rows], key=lambda binding: binding.id)

    def verify_pair(self, camera_serial, pico_serial, channel):
        if not camera_serial or not pico_serial:
            QMessageBox.warning(self, 'Vyberte zariadenia', 'Najprv vyberte kameru a spoločné Pico.')
            return
        from app.ui.pairing_verification_dialog import PairingVerificationDialog
        PairingVerificationDialog(camera_serial, pico_serial, self, channel=channel).exec()

    def validate_and_accept(self):
        try:
            WorkstationDevices.validate(self.bindings())
        except ValueError as exc:
            QMessageBox.warning(self, 'Priradenie nie je platné', str(exc)); return
        self.accept()

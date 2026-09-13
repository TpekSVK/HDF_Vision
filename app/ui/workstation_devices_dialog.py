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
        explanation = QLabel('Každá stanica vykonáva vlastnú kontrolu zo svojho Pico.\n'
            'Vyberte fyzicky zapojenú dvojicu. Čísla USB zostávajú platné aj po reštarte.\n'
            'Kamera 1 zachová existujúce dáta; ďalšie stanice majú vlastné recepty a výsledky.')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
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
        box = QGroupBox(f'Kamera {index + 1}'); form = QFormLayout(box)
        name = QLineEdit(binding.name if binding else f'Kamera {index + 1}')
        camera, pico = QComboBox(), QComboBox()
        form.addRow('Názov stanice', name); form.addRow('Kamera', camera); form.addRow('Pico', pico)
        verify = QPushButton("Overiť dvojicu a zobraziť snímku")
        verify.clicked.connect(lambda: self.verify_pair(camera.currentData(), pico.currentData()))
        form.addRow(verify)
        self.form_layout.addWidget(box)
        self.rows.append((box, name, camera, pico, binding.id if binding else f'camera_{index + 1}'))
        self.refresh()
        if binding:
            for combo, serial in ((camera, binding.camera_serial), (pico, binding.pico_serial)):
                if combo.findData(serial) < 0: combo.addItem(f'{serial} — teraz nepripojené', serial)
                combo.setCurrentIndex(combo.findData(serial))

    def remove_station(self):
        if len(self.rows) > 1:
            row = self.rows.pop(); row[0].deleteLater()

    def refresh(self):
        cameras, picos = discover_devices()
        for _, _, camera, pico, _ in self.rows:
            for combo, devices in ((camera, cameras), (pico, picos)):
                selected = combo.currentData()
                combo.clear(); combo.addItem('Vyberte zariadenie…', '')
                for device in devices:
                    combo.addItem(f'{device.product} — {device.serial} ({device.path})', device.serial)
                if selected and combo.findData(selected) < 0:
                    combo.addItem(f'{selected} — teraz nepripojené', selected)
                combo.setCurrentIndex(max(0, combo.findData(selected)))

    def bindings(self):
        return [StationBinding(row[4], row[1].text().strip(),
                    row[2].currentData() or '', row[3].currentData() or '') for row in self.rows]

    def verify_pair(self, camera_serial, pico_serial):
        if not camera_serial or not pico_serial:
            QMessageBox.warning(self, 'Vyberte dvojicu', 'Najprv vyberte kameru a Pico.')
            return
        from app.ui.pairing_verification_dialog import PairingVerificationDialog
        PairingVerificationDialog(camera_serial, pico_serial, self).exec()

    def validate_and_accept(self):
        try:
            WorkstationDevices.validate(self.bindings())
        except ValueError as exc:
            QMessageBox.warning(self, 'Priradenie nie je platné', str(exc)); return
        self.accept()

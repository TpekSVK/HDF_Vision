"""Diagnostic task selection; opening the menu never acquires the camera."""
import json
from pathlib import Path
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QDialog


class TroubleshootingHome(QWidget):
    camera_position_requested = Signal()

    def __init__(self, parent=None, *, contact_path=Path('/data/contact.json')):
        super().__init__(parent)
        self.contact_path = Path(contact_path)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel('Diagnostické činnosti')
        title.setStyleSheet('font-size: 22px; font-weight: bold;')
        layout.addWidget(title)
        layout.addWidget(QLabel('Vyberte činnosť pre nápravu chyby.'))
        self.position_button = QPushButton('Overenie polohy kamery')
        self.position_button.setMinimumHeight(64)
        self.position_button.setMaximumWidth(380)
        self.position_button.clicked.connect(self.camera_position_requested)
        layout.addWidget(self.position_button)
        self.contact_button = QPushButton('Kontakt')
        self.contact_button.setMinimumHeight(64)
        self.contact_button.setMaximumWidth(380)
        self.contact_button.clicked.connect(self._show_contact)
        layout.addWidget(self.contact_button)
        layout.addStretch()

    def _show_contact(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Kontakt')
        dialog.setMinimumWidth(320)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        try:
            contact = json.loads(self.contact_path.read_text(encoding='utf-8'))
            texts = [contact[key] for key in ('name', 'phone', 'email')]
            if not all(isinstance(text, str) and text.strip() for text in texts):
                raise ValueError('Incomplete contact')
        except FileNotFoundError:
            texts = ['Kontakt zatiaľ nie je nastavený.']
        except (OSError, ValueError, KeyError, TypeError):
            texts = ['Kontaktné údaje sa nepodarilo načítať.']
        for text in texts:
            label = QLabel(text)
            label.setTextFormat(Qt.PlainText)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(label)
        dialog.exec()

"""Diagnostic task selection; opening the menu never acquires the camera."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton


class TroubleshootingHome(QWidget):
    camera_position_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
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
        layout.addStretch()

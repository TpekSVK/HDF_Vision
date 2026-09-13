from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
import numpy as np
from app.services.pairing_verification import verify_pair
from app.ui.inspection_worker import InspectionWorker


class PairingVerificationDialog(QDialog):
    def __init__(self, camera_serial, pico_serial, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Overenie dvojice kamera / Pico')
        self.resize(850, 600)
        layout = QVBoxLayout(self)
        self.message = QLabel('Pripravujem vybranú kameru a snímam cez vybrané Pico…')
        self.message.setWordWrap(True); layout.addWidget(self.message)
        self.image = QLabel(); self.image.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.image, 1)
        self.button = QPushButton('Zavrieť'); self.button.setEnabled(False)
        self.button.clicked.connect(self.accept); layout.addWidget(self.button)
        self.worker = InspectionWorker(self)
        self.worker.completed.connect(self.completed)
        self._finished = False
        QTimer.singleShot(0, lambda: self.worker.submit('pair', lambda: verify_pair(camera_serial, pico_serial)))

    def completed(self, kind, frame, error):
        self._finished = True
        self.button.setEnabled(True)
        if error is not None:
            self.message.setText(f'Dvojica sa neoverila: {error}\n'
                                 'Skontrolujte priradenie, zapojenie a firmware Pico.')
            return
        if frame is None:
            self.message.setText('Kamera nevrátila snímku. Dvojica sa neoverila.')
            return
        array = np.ascontiguousarray(frame, dtype=np.uint8)
        qimage = QImage(array.data, array.shape[1], array.shape[0], array.strides[0], QImage.Format_Grayscale8).copy()
        self.image.setPixmap(QPixmap.fromImage(qimage).scaled(800, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.message.setText('Kamera prijala PIO impulzy a vrátila produkčnú snímku.\n'
                             'Skontrolujte, či je na nej očakávaný objekt. Priradenie uložíte v predchádzajúcom okne.')

    def done(self, result):
        if not self._finished:
            self.message.setText('Počkajte na dokončenie overenia a vypnutie svetla.')
            return
        self.worker.shutdown()
        super().done(result)

    def closeEvent(self, event):
        if not self._finished:
            event.ignore()
        else:
            self.worker.shutdown()
            event.accept()

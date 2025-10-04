from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox, QCheckBox, QSpinBox, QFileDialog
)
from PySide6.QtCore import Qt, QTimer
from app.services.camera_service import CameraService
from app.services.storage_service import save_golden, save_validation_image, save_production_result
from app.services.retention_service import run_retention_cycle
from app.ui.golden_wizard import GoldenWizard
from PySide6.QtWidgets import QPushButton

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HDF_Vision")
        self.mode = "RUN"  # RUN alebo SETUP
        self.cam = CameraService()
        self.cam.start()

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # Top bar: režim
        self.mode_btn = QPushButton("Prepnúť do SETUP")
        self.mode_btn.clicked.connect(self.toggle_mode)
        layout.addWidget(self.mode_btn)

        # Panel RUN
        self.lbl_status = QLabel("OK/NOK: —")
        self.btn_trigger = QPushButton("Manuálny TRIGGER (one-shot)")
        self.btn_trigger.clicked.connect(self.manual_trigger)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.btn_trigger)

        # Panel SETUP – „Nastavenia kamery“ (zatím len placeholdery)
        self.panel_setup = QWidget(); v = QVBoxLayout(self.panel_setup)
        v.addWidget(QLabel("Nastavenia kamery"))
        self.btn_wizard = QPushButton("Golden WIZARD", self)
        self.btn_wizard.clicked.connect(self.open_wizard)
        self.topBarLayout.addWidget(self.btn_wizard)
        # rozlíšenie
        res_line = QHBoxLayout()
        v.addLayout(res_line)
        res_line.addWidget(QLabel("Rozlíšenie:"))
        self.cmb_res = QComboBox()
        self.cmb_res.addItems([
            "1920x1080@60 Y8",
            "1280x720@60 Y8",
            "2592x1944@30 Y8 (len setup/pomalé)"
        ])
        res_line.addWidget(self.cmb_res)

        # expo/gain (hookne sa na XU/UV C neskôr)
        v.addWidget(QLabel("Expo [us] (XU stub):"))
        self.spin_expo = QSpinBox(); self.spin_expo.setRange(1, 1000000); self.spin_expo.setValue(8000)
        v.addWidget(self.spin_expo)

        v.addWidget(QLabel("Gain [dB] (XU stub):"))
        self.spin_gain = QSpinBox(); self.spin_gain.setRange(0, 48); self.spin_gain.setValue(0)
        v.addWidget(self.spin_gain)

        # golden save
        self.btn_save_golden = QPushButton("Uložiť GOLDEN (current one-shot)")
        self.btn_save_golden.clicked.connect(self.save_golden_clicked)
        v.addWidget(self.btn_save_golden)

        layout.addWidget(self.panel_setup)
        self.panel_setup.hide()

        # Retencia periodicky
        from app.services.retention_service import RetentionThread
        self.retention = RetentionThread(interval_sec=300)  # 5 min
        self.retention.start()

    def toggle_mode(self):
        if self.mode == "RUN":
            self.mode = "SETUP"; self.mode_btn.setText("Prepnúť do RUN"); self.panel_setup.show()
        else:
            self.mode = "RUN"; self.mode_btn.setText("Prepnúť do SETUP"); self.panel_setup.hide()

    def manual_trigger(self):
        frame = self.cam.one_shot()
        # TODO: tu zatiaľ len pseudo-hodnotenie -> OK
        meta = {"ts": self._ts(), "result": "OK", "recipe": "default", "metrics": {}}
        save_production_result(frame, meta, "default", store_full_nok=False, nok=False)
        self.lbl_status.setText("OK/NOK: OK")

    def save_golden_clicked(self):
        frame = self.cam.one_shot()
        path = save_golden(frame, "default")
        self.lbl_status.setText(f"GOLDEN uložený: {path}")

    def closeEvent(self, e):
        try:
            self.cam.stop()
            try:
                self.retention.stop()
            except Exception:
                pass
        finally:
            e.accept()
            
    def open_wizard(self):
        dlg = GoldenWizard(self.cam, self)
        dlg.resize(1200, 800)
    dlg.exec()

    @staticmethod
    def _ts():
        import datetime as dt
        return dt.datetime.utcnow().isoformat()

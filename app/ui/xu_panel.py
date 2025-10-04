# app/ui/xu_panel.py
from PySide6.QtWidgets import QWidget, QFormLayout, QDoubleSpinBox, QSpinBox, QPushButton, QHBoxLayout, QComboBox, QLabel
from PySide6.QtCore import Qt
import subprocess, shlex

class XUPanel(QWidget):
    """
    Ovládanie kamery:
      - V4L2: exposure/gain (UVC štandard)
      - XU stub: Stream Mode (Master/Trigger), Flash (OFF/STROBE/TORCH), Pixel Format (Y8/Y12)
    Očakáva self.mw.cam s atribútom .devices[0] (napr. '/dev/video0')
    """
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.dev = getattr(self.mw.cam, "devices", ["/dev/video0"])[0]

        f = QFormLayout(self)

        # --------- V4L2 ---------
        self.exp_us = QSpinBox(); self.exp_us.setRange(10, 1_000_000); self.exp_us.setValue(8000)
        self.gain_db = QSpinBox(); self.gain_db.setRange(0, 48); self.gain_db.setValue(0)

        row_v4l2 = QHBoxLayout()
        btn_set_exp = QPushButton("Nastaviť")
        btn_set_gain = QPushButton("Nastaviť")
        row_v4l2.addWidget(btn_set_exp); row_v4l2.addWidget(btn_set_gain)

        f.addRow(QLabel("<b>V4L2 (UVC štandard)</b>"))
        f.addRow("Exposure [µs]", self.exp_us)
        f.addRow("", row_v4l2)
        f.addRow("Gain [dB]", self.gain_db)

        btn_set_exp.clicked.connect(self._apply_exposure)
        btn_set_gain.clicked.connect(self._apply_gain)

        # --------- XU (vendor) ---------
        self.stream_mode = QComboBox(); self.stream_mode.addItems(["Master (0)", "Trigger (1)"])
        self.flash_mode  = QComboBox(); self.flash_mode.addItems(["OFF (0)", "STROBE (1)", "TORCH (2)"])
        self.pixfmt      = QComboBox(); self.pixfmt.addItems(["Y8 (GRAY8)", "Y12 (12-bit packed)"])

        row_xu = QHBoxLayout()
        btn_xu_apply = QPushButton("Apply XU")
        row_xu.addWidget(btn_xu_apply)

        f.addRow(QLabel("<b>Vendor XU</b> (Stream/Flash/Pixel format)"))
        f.addRow("Stream Mode", self.stream_mode)
        f.addRow("Flash Mode", self.flash_mode)
        f.addRow("Pixel Format", self.pixfmt)
        f.addRow("", row_xu)

        btn_xu_apply.clicked.connect(self._apply_xu)

        note = QLabel("Pozn.: StreamMode 0=Master, 1=Trigger; Flash 0=OFF,1=Strobe,2=Torch (podľa XU API).")
        note.setWordWrap(True)
        f.addRow(note)

    # ---- Helpers ----
    def _sh(self, cmd):
        print("[v4l2ctl]", cmd)
        return subprocess.run(shlex.split(cmd), capture_output=True, text=True)

    def _apply_exposure(self):
        # UVC: exposure_auto=1 (Manual Mode), exposure_absolute je v jednotkách 100 µs (bežne)
        # Kušať dve cesty: "exposure_absolute" alebo "exposure_time_absolute" (záleží od ovládača)
        us = int(self.exp_us.value())
        dev = self.dev
        # prepnúť do manuálu
        self._sh(f"v4l2-ctl -d {dev} -c exposure_auto=1 || true")
        # skúsiť exposure_time_absolute (µs), ak zlyhá, prepočítať na 100µs pre exposure_absolute
        r = self._sh(f"v4l2-ctl -d {dev} -c exposure_time_absolute={us}")
        if r.returncode != 0:
            # 100 µs jednotky
            val = max(1, us // 100)
            self._sh(f"v4l2-ctl -d {dev} -c exposure_absolute={val}")
        print(r.stdout, r.stderr)

    def _apply_gain(self):
        dev = self.dev
        val = int(self.gain_db.value())
        self._sh(f"v4l2-ctl -d {dev} -c gain={val}")

    def _apply_xu(self):
        # STUB: tu prídu linux XU volania (UVC XU GUID+selector) alebo vendor .so
        # StreamMode: 0 Master, 1 Trigger (SetStreamModeCU55_MH) – podľa manuálu
        sm = 0 if self.stream_mode.currentIndex() == 0 else 1
        fm = self.flash_mode.currentIndex()  # 0/1/2
        pf = self.pixfmt.currentIndex()      # 0=Y8, 1=Y12
        print(f"[XU] (stub) SetStreamMode={sm}, SetFlash={fm}, PixelFormat={'Y12' if pf==1 else 'Y8'}")
        # TODO: doplniť po dodaní XU selector/ID pre Linux. Zatiaľ iba zalogujeme.

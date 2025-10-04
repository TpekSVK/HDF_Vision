# app/ui/golden_wizard.py
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QAction, QImage
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit, QMessageBox, QWidget
)

import json
from pathlib import Path

from app.ui.draw_view import DrawView
from app.services.storage_service import save_golden, save_validation_image
from app.models.regions import Region, validate_cardinality
from app.services.live_preview_service import LivePreviewService


class GoldenWizard(QDialog):
    """
    Jediné miesto na nastavenie nástroja:
      1) Získať/načítať GOLDEN (1 ks)
      2) Nakresliť oblasti (Blue pose×1, Green ROI×1, Magenta ignore≤5)
      3) Zbierať validáciu (OK/NOK)
      4) Uložiť recept (golden.png + regions.json)
      5) Live feed (ON/OFF) – živý náhľad priamo vo wizarde
    """
    def __init__(self, camera, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Golden WIZARD")
        self.setModal(True)
        self.cam = camera
        self.current_img = None

        # --- Live feed infra ---
        dev = getattr(self.cam, "devices", ["/dev/video0"])[0]
        self._lp = LivePreviewService(dev, 1280, 720, 60)
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(50)  # ~20 FPS
        self._live_timer.timeout.connect(self._live_tick)
        self._live_on = False

        # --- Horný panel ---
        self.recipe_name = QLineEdit("default", self)
        self.shape_sel   = QComboBox(self); self.shape_sel.addItems(["rect","circle","poly"])
        self.type_sel    = QComboBox(self); self.type_sel.addItems(["pose","roi","ignore"])

        # Live prepínač
        self.live_sel    = QComboBox(self); self.live_sel.addItems(["Live OFF","Live ON"])
        self.live_sel.currentIndexChanged.connect(self._toggle_live)

        top = QHBoxLayout()
        top.addWidget(QLabel("Recept:")); top.addWidget(self.recipe_name)
        top.addWidget(QLabel("Tvar:"));   top.addWidget(self.shape_sel)
        top.addWidget(QLabel("Typ:"));    top.addWidget(self.type_sel)
        top.addWidget(QLabel("Náhľad:")); top.addWidget(self.live_sel)

        # --- Plátno s kreslením (pozadie meníme live) ---
        self.view = DrawView(self)
        self.view.set_shape_type(self.shape_sel.currentText())
        self.view.set_region_type(self.type_sel.currentText())

        # --- Ovládacie tlačidlá ---
        btn_cap_golden   = QPushButton("Získať GOLDEN z kamery")
        btn_load_golden  = QPushButton("Načítať GOLDEN z disku")
        btn_save_recipe  = QPushButton("Uložiť RECEPT")
        btn_val_ok       = QPushButton("Validačný zber: uložiť Ⓞ OK")
        btn_val_nok      = QPushButton("Validačný zber: uložiť ✕ NOK")

        buttons = QHBoxLayout()
        buttons.addWidget(btn_cap_golden)
        buttons.addWidget(btn_load_golden)
        buttons.addStretch(1)
        buttons.addWidget(btn_val_ok)
        buttons.addWidget(btn_val_nok)
        buttons.addWidget(btn_save_recipe)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.view)
        layout.addLayout(buttons)

        # signály
        self.shape_sel.currentTextChanged.connect(self.view.set_shape_type)
        self.type_sel.currentTextChanged.connect(self.view.set_region_type)
        btn_cap_golden.clicked.connect(self._capture_golden)
        btn_load_golden.clicked.connect(self._load_golden)
        btn_save_recipe.clicked.connect(self._save_recipe)
        btn_val_ok.clicked.connect(lambda: self._save_validation(True))
        btn_val_nok.clicked.connect(lambda: self._save_validation(False))

    # ---------- Live feed ----------
    def _toggle_live(self, idx: int):
        if idx == 1 and not self._live_on:  # ON
            try:
                self._lp.start()
                self._live_timer.start()
                self._live_on = True
            except Exception as e:
                self._err(f"Live feed sa nepodarilo spustiť: {e}")
                self.live_sel.setCurrentIndex(0)
                self._live_on = False
        elif idx == 0 and self._live_on:  # OFF
            self._live_timer.stop()
            try:
                self._lp.stop()
            except Exception:
                pass
            self._live_on = False

    def _live_tick(self):
        img = self._lp.last_frame_u8()
        if img is None:
            return
        # Zobraz live ako pozadie do DrawView (prekresľuje len bitmapu; overlay/regiony zostanú)
        self._set_pixmap(img)

    # ---------- UI util ----------
    def _set_pixmap(self, img_u8):
        # img_u8: numpy uint8 (H, W)
        h, w = img_u8.shape[:2]
        qimg = QImage(img_u8.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy())
        self.view.set_background(pm)

    # ---------- Akcie ----------
    def _capture_golden(self):
        try:
            # Ak beží LIVE, vezmi aktuálny frame; inak sprav one-shot
            frame = self._lp.last_frame_u8() if self._live_on else None
            if frame is None:
                frame = self.cam.one_shot()
            self.current_img = frame
            self._set_pixmap(frame)
            self._info("Golden zachytený z kamery.")
        except Exception as e:
            self._err(f"Zachytenie zlyhalo: {e}")

    def _load_golden(self):
        from PySide6.QtWidgets import QFileDialog
        fp, _ = QFileDialog.getOpenFileName(self, "Načítaj obrázok", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not fp:
            return
        import imageio.v3 as iio, numpy as np
        img = iio.imread(fp)
        if img.ndim == 3:
            import cv2
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.shape[2]==3 else img[:,:,0]
        if img.dtype != np.uint8:
            # pre istotu – konvertuj na 8-bit
            import cv2
            img = cv2.convertScaleAbs(img)
        self.current_img = img
        self._set_pixmap(img)
        self._info("Golden načítaný z disku.")

    def _save_recipe(self):
        if self.current_img is None:
            self._err("Najprv zachyť alebo načítaj GOLDEN.")
            return
        regs = self.view.export_regions()
        ok, msg = validate_cardinality([Region(**r) for r in regs])
        if not ok:
            self._err(msg); return

        name = self.recipe_name.text().strip() or "default"
        # ulož golden
        golden_path = save_golden(self.current_img, name)
        # ulož regions.json
        recipe_dir = Path("/data") / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        with open(recipe_dir / "regions.json", "w", encoding="utf-8") as f:
            json.dump(regs, f, ensure_ascii=False, indent=2)

        self._info(f"Recept uložený:\n{golden_path}\n{recipe_dir/'regions.json'}")

    def _save_validation(self, is_ok: bool):
        if self.current_img is None:
            try:
                self.current_img = (self._lp.last_frame_u8() if self._live_on else None) or self.cam.one_shot()
            except Exception as e:
                self._err(f"Zachytenie zlyhalo: {e}")
                return
        name = self.recipe_name.text().strip() or "default"
        out = save_validation_image(self.current_img, ok=is_ok, recipe_name=name)
        self._info(f"Validačný snímok uložený:\n{out['thumb']}\n{out['full']}")

    # ---------- Info/Err ----------
    def _info(self, msg):
        QMessageBox.information(self, "Info", msg)

    def _err(self, msg):
        QMessageBox.critical(self, "Chyba", msg)

    # ---------- Shutdown ----------
    def closeEvent(self, e):
        try:
            self._live_timer.stop()
            self._lp.stop()
        except Exception:
            pass
        e.accept()

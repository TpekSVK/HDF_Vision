# app/ui/golden_wizard.py
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit, QMessageBox, QWidget
)

import json
from pathlib import Path

from app.ui.draw_view import DrawView
from app.services.storage_service import save_golden, save_validation_image
from app.models.regions import Region, validate_cardinality

class GoldenWizard(QDialog):
    """
    Jediné miesto na nastavenie nástroja:
      1) Získať/načítať GOLDEN (1 ks)
      2) Nakresliť oblasti (Blue pose×1, Green ROI×1, Magenta ignore≤5)
      3) Zbierať validáciu (OK/NOK)
      4) Uložiť recept (golden.png + regions.json)
    """
    def __init__(self, camera, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Golden WIZARD")
        self.setModal(True)
        self.cam = camera
        self.current_img = None

        self.recipe_name = QLineEdit("default", self)
        self.shape_sel   = QComboBox(self)
        self.shape_sel.addItems(["rect","circle","poly"])
        self.type_sel    = QComboBox(self)
        self.type_sel.addItems(["pose","roi","ignore"])

        self.view = DrawView(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Recept:")); top.addWidget(self.recipe_name)
        top.addWidget(QLabel("Tvar:"));   top.addWidget(self.shape_sel)
        top.addWidget(QLabel("Typ:"));    top.addWidget(self.type_sel)

        btn_cap_golden = QPushButton("Získať GOLDEN z kamery")
        btn_load_golden = QPushButton("Načítať GOLDEN z disku")
        btn_save_recipe = QPushButton("Uložiť RECEPT")
        btn_val_ok      = QPushButton("Validačný zber: uložiť Ⓞ OK")
        btn_val_nok     = QPushButton("Validačný zber: uložiť ✕ NOK")

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

    def _set_pixmap(self, img_u8):
        # img_u8: numpy uint8 (H,W)
        h, w = img_u8.shape[:2]
        qimg = QPixmap.fromImage(
            QPixmap.fromImage(
                # workaround: PySide6 si rozumie s QImage priamo,
                # ale tu využijeme jednoduchú cestu: QPixmap -> QImage implicitne
                ).toImage()
        )
        # rýchly spôsob – cez QImage z raw dát:
        from PySide6.QtGui import QImage
        qimg = QImage(img_u8.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy())  # copy -> vlastníme buffer
        self.view.set_background(pm)

    def _capture_golden(self):
        try:
            frame = self.cam.one_shot()
            self.current_img = frame
            # Golden zobrazujeme v 8-bit – ak je 16-bit, konverziu už robí CameraService
            self._set_pixmap(frame)
            self._info("Golden zachytený z kamery.")
        except Exception as e:
            self._err(f"Zachytenie zlyhalo: {e}")

    def _load_golden(self):
        from PySide6.QtWidgets import QFileDialog
        fp, _ = QFileDialog.getOpenFileName(self, "Načítaj obrázok", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not fp:
            return
        import imageio.v3 as iio
        img = iio.imread(fp)
        if img.ndim == 3:
            import cv2, numpy as np
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.shape[2]==3 else img[:,:,0]
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
                self.current_img = self.cam.one_shot()
            except Exception as e:
                self._err(f"Zachytenie zlyhalo: {e}")
                return
        name = self.recipe_name.text().strip() or "default"
        out = save_validation_image(self.current_img, ok=is_ok, recipe_name=name)
        self._info(f"Validačný snímok uložený:\n{out['thumb']}\n{out['full']}")

    def _info(self, msg):
        QMessageBox.information(self, "Info", msg)

    def _err(self, msg):
        QMessageBox.critical(self, "Chyba", msg)

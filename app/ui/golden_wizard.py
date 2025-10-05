# app/ui/golden_wizard.py
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit, QMessageBox
)

import json, os
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
      5) Live feed (ON/OFF) – samostatný náhľad (bez kreslenia)
    """
    def __init__(self, camera, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle("Golden WIZARD")
        self.setModal(True)
        self.cam = camera
        self.current_img = None

        # --- Live infra (len video label, bez kreslenia) ---

        dev = os.environ.get("CAM_DEV") or getattr(self.cam, "devices", ["/dev/video0"])[0]
        print(f"[GoldenWizard] Live device: {dev}")
        self._lp = LivePreviewService(dev, 1280, 720, 60)

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(50)  # ~20 FPS
        self._live_timer.timeout.connect(self._live_tick)
        self._live_on = False

        # ---- Horná lišta ----
        self.recipe_name = QLineEdit("default", self)
        self.shape_sel   = QComboBox(self); self.shape_sel.addItems(["rect","circle","poly"])
        self.type_sel    = QComboBox(self); self.type_sel.addItems(["pose","roi","ignore"])

        # Toggle Live
        self.btn_live = QPushButton("Live OFF")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._toggle_live)

        top = QHBoxLayout()
        top.addWidget(QLabel("Recept:")); top.addWidget(self.recipe_name)
        top.addWidget(QLabel("Tvar:"));   top.addWidget(self.shape_sel)
        top.addWidget(QLabel("Typ:"));    top.addWidget(self.type_sel)
        top.addStretch(1)
        top.addWidget(self.btn_live)

        # ---- Dva režimy zobrazenia ----
        # 1) Live LABEL (video) – používa sa len pri Live ON
        self.live_lbl = QLabel("—")
        self.live_lbl.setAlignment(Qt.AlignCenter)
        self.live_lbl.setMinimumHeight(360)
        self.live_lbl.hide()  # default skryté

        # 2) DrawView (kreslenie) – používa sa pri Live OFF
        self.view = DrawView(self)
        self.view.set_shape_type(self.shape_sel.currentText())
        self.view.set_region_type(self.type_sel.currentText())

        # ---- Ovládacie tlačidlá ----
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

        # ---- Layout ----
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.live_lbl)
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

    # ---------- Live ----------
    def _toggle_live(self, checked: bool):
        if checked:
            try:
                self.cam.pause_for_external()
            except Exception as e:
                print("[GoldenWizard] pause_for_external:", e)


            # Zapnúť live: zobraz label, skryť DrawView (žiadne kreslenie počas live)
            self.view.hide()
            self.live_lbl.show()
            try:
                self._lp.start()
                self._live_timer.start()
                self._live_on = True
                self.btn_live.setText("Live ON")
                # Deaktivuj meniče tvar/typ počas live (čisto vizuálne)
                self.shape_sel.setEnabled(False)
                self.type_sel.setEnabled(False)
            except Exception as e:
                self._err(f"Live feed sa nepodarilo spustiť: {e}")
                self.btn_live.setChecked(False)
                self.live_lbl.setText("—")
                self._live_on = False
        else:
            # Vypnúť live: skryť label, ukázať DrawView
            self._live_timer.stop()
            try:
                self._lp.stop()
            except Exception:
                pass
            self._live_on = False
            self.btn_live.setText("Live OFF")
            self.live_lbl.hide()
            self.view.show()
            self.shape_sel.setEnabled(True)
            self.type_sel.setEnabled(True)

    def _live_tick(self):
        img = self._lp.last_frame_u8()
        if img is None:
            return
        h, w = img.shape[:2]
        qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy()).scaled(self.live_lbl.width(), self.live_lbl.height(),
                                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.live_lbl.setPixmap(pm)

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
            # ak je live ON, zober aktuálny frame a hneď live vypni (freeze)
            frame = (self._lp.last_frame_u8() if self._live_on else None)
            if frame is None:
                frame = self.cam.one_shot()
            self.current_img = frame
            self._set_pixmap(frame)
            if self._live_on:
                self.btn_live.setChecked(False)
                self._toggle_live(False)  # vypnúť live, prepnúť späť na DrawView

            # po vypnutí live obnov kameru
            try:
                self.cam.resume_after_external()
            except Exception as e:
                print("[GoldenWizard] resume_after_external:", e)

            self._info("Golden zachytený z kamery.")
        except Exception as e:
            self._err(f"Zachytenie zlyhalo: {e}")

    def _load_golden(self):
        from PySide6.QtWidgets import QFileDialog
        fp, _ = QFileDialog.getOpenFileName(self, "Načítaj obrázok", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not fp:
            return
        import imageio.v3 as iio, numpy as np, cv2
        img = iio.imread(fp)
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.shape[2]==3 else img[:,:,0]
        if img.dtype != np.uint8:
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

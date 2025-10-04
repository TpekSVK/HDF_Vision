# app/ui/golden_wizard.py
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit, QMessageBox, QWidget
)

import json
from pathlib import Path
from app.services.live_preview_service import LivePreviewService

from app.ui.draw_view import DrawView
from app.services.storage_service import save_golden, save_validation_image
from app.models.regions import Region, validate_cardinality

class GoldenWizard(QDialog):
    """
    JedinĂ© miesto na nastavenie nĂˇstroja:
      1) ZĂ­skaĹĄ/naÄŤĂ­taĹĄ GOLDEN (1 ks)
      2) NakresliĹĄ oblasti (Blue poseĂ—1, Green ROIĂ—1, Magenta ignoreâ‰¤5)
      3) ZbieraĹĄ validĂˇciu (OK/NOK)
      4) UloĹľiĹĄ recept (golden.png + regions.json)
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
        self.view.set_shape_type(self.shape_sel.currentText())
        self.view.set_region_type(self.type_sel.currentText())
        top = QHBoxLayout()
        top.addWidget(QLabel("Recept:")); top.addWidget(self.recipe_name)
        top.addWidget(QLabel("Tvar:"));   top.addWidget(self.shape_sel)
        top.addWidget(QLabel("Typ:"));    top.addWidget(self.type_sel)

        btn_cap_golden = QPushButton("ZĂ­skaĹĄ GOLDEN z kamery")
        btn_load_golden = QPushButton("NaÄŤĂ­taĹĄ GOLDEN z disku")
        btn_save_recipe = QPushButton("UloĹľiĹĄ RECEPT")
        btn_val_ok      = QPushButton("ValidaÄŤnĂ˝ zber: uloĹľiĹĄ â“„ OK")
        btn_val_nok     = QPushButton("ValidaÄŤnĂ˝ zber: uloĹľiĹĄ âś• NOK")

        buttons = QHBoxLayout()
        buttons.addWidget(btn_cap_golden)
        buttons.addWidget(btn_load_golden)
        buttons.addStretch(1)
        buttons.addWidget(btn_val_ok)
        buttons.addWidget(btn_val_nok)
        buttons.addWidget(btn_save_recipe)

                # --- Live feed v WIZARDe ---
        self.live_combo = QComboBox()
        self.live_combo.addItems(["Live OFF", "Live ON"])
        self.live_combo.currentIndexChanged.connect(self._on_live_toggle)
        self.topBarLayout.addWidget(self.live_combo)  # kam pridĂˇvaĹˇ tvoje ovlĂˇdacie prvky

        # helpery
        dev = getattr(self.cam, "devices", ["/dev/video0"])[0]
        self._lp = LivePreviewService(dev, 1280, 720, 60)  # mĂ´ĹľeĹˇ doladiĹĄ rozlĂ­Ĺˇenie podÄľa potreby SETUP
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(50)  # ~20 FPS, postaÄŤuje
        self._live_timer.timeout.connect(self._on_live_tick)

        # pamĂ¤taj si pĂ´vodnĂ© pozadie (napr. naÄŤĂ­tanĂ˝ golden), aby OFF vedel vrĂˇtiĹĄ spĂ¤ĹĄ
        self._static_bg = None
        if hasattr(self.view, "_bg") and self.view._bg and self.view._bg.pixmap():
            self._static_bg = self.view._bg.pixmap()


        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.view)
        layout.addLayout(buttons)

        # signĂˇly
        self.shape_sel.currentTextChanged.connect(self.view.set_shape_type)
        self.type_sel.currentTextChanged.connect(self.view.set_region_type)
        btn_cap_golden.clicked.connect(self._capture_golden)
        btn_load_golden.clicked.connect(self._load_golden)
        btn_save_recipe.clicked.connect(self._save_recipe)
        btn_val_ok.clicked.connect(lambda: self._save_validation(True))
        btn_val_nok.clicked.connect(lambda: self._save_validation(False))

    def _set_pixmap(self, img_u8):
        # img_u8: numpy uint8 (H, W)
        from PySide6.QtGui import QImage, QPixmap
        h, w = img_u8.shape[:2]
        # bytesPerLine = w (1 byte na pixel pri GRAY8)
        qimg = QImage(img_u8.data, w, h, w, QImage.Format_Grayscale8)
        # .copy() aby QImage vlastnil buffer (numpy mĂ´Ĺľe zaniknĂşĹĄ)
        pm = QPixmap.fromImage(qimg.copy())
        self.view.set_background(pm)


    def _capture_golden(self):
        try:
            frame = self.cam.one_shot()
            self.current_img = frame
            # Golden zobrazujeme v 8-bit â€“ ak je 16-bit, konverziu uĹľ robĂ­ CameraService
            self._set_pixmap(frame)
            self._info("Golden zachytenĂ˝ z kamery.")
        except Exception as e:
            self._err(f"Zachytenie zlyhalo: {e}")

    def _load_golden(self):
        from PySide6.QtWidgets import QFileDialog
        fp, _ = QFileDialog.getOpenFileName(self, "NaÄŤĂ­taj obrĂˇzok", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not fp:
            return
        import imageio.v3 as iio
        img = iio.imread(fp)
        if img.ndim == 3:
            import cv2, numpy as np
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.shape[2]==3 else img[:,:,0]
        self.current_img = img
        self._set_pixmap(img)
        self._info("Golden naÄŤĂ­tanĂ˝ z disku.")

    def _save_recipe(self):
        if self.current_img is None:
            self._err("Najprv zachyĹĄ alebo naÄŤĂ­taj GOLDEN.")
            return
        regs = self.view.export_regions()
        ok, msg = validate_cardinality([Region(**r) for r in regs])
        if not ok:
            self._err(msg); return

        name = self.recipe_name.text().strip() or "default"
        # uloĹľ golden
        golden_path = save_golden(self.current_img, name)
        # uloĹľ regions.json
        recipe_dir = Path("/data") / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        with open(recipe_dir / "regions.json", "w", encoding="utf-8") as f:
            json.dump(regs, f, ensure_ascii=False, indent=2)

        self._info(f"Recept uloĹľenĂ˝:\n{golden_path}\n{recipe_dir/'regions.json'}")

    def _save_validation(self, is_ok: bool):
        if self.current_img is None:
            try:
                self.current_img = self.cam.one_shot()
            except Exception as e:
                self._err(f"Zachytenie zlyhalo: {e}")
                return
        name = self.recipe_name.text().strip() or "default"
        out = save_validation_image(self.current_img, ok=is_ok, recipe_name=name)
        self._info(f"ValidaÄŤnĂ˝ snĂ­mok uloĹľenĂ˝:\n{out['thumb']}\n{out['full']}")

    def _info(self, msg):
        QMessageBox.information(self, "Info", msg)

    def _err(self, msg):
        QMessageBox.critical(self, "Chyba", msg)

    def _on_live_toggle(self, idx: int):
        if idx == 1:  # ON
            # uloĹľ aktuĂˇlne pozadie na neskorĹˇĂ­ nĂˇvrat
            if hasattr(self.view, "_bg") and self.view._bg and self.view._bg.pixmap():
                self._static_bg = self.view._bg.pixmap()
            try:
                self._lp.start()
                self._live_timer.start()
            except Exception as e:
                # fallback na OFF
                self.live_combo.setCurrentIndex(0)
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Live feed", f"Nepodarilo sa spustiĹĄ live: {e}")
        else:
            try:
                self._live_timer.stop()
                self._lp.stop()
            except Exception:
                pass
            # vrĂˇĹĄ statickĂ˝ obrĂˇzok (golden / poslednĂ˝)
            if self._static_bg is not None and hasattr(self.view, "_bg") and self.view._bg:
                self.view._bg.setPixmap(self._static_bg)
                self.view.update()

    def _on_live_tick(self):
        img = self._lp.last_frame_u8()
        if img is None:
            return
        # aktualizuj pozadie kresliaceho view
        self.view.set_background_image(img)
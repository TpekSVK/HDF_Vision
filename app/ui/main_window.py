from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox, QSpinBox,
    QStackedWidget, QFrame, QScrollArea, QCheckBox, QToolButton
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap

from threading import Thread
from app.services.retention_service import RetentionService

from app.ui.xu_panel import XUPanel

from app.services.camera_service import CameraService
from app.services.storage_service import save_golden, save_production_result
from app.ui.golden_wizard import GoldenWizard
from app.services.db_service import DbService
from app.services.recipe_service import RecipeService
from app.services.stats_service import StatsService
from app.ui.thresholds_panel import ThresholdsPanel
from app.ui.results_strip import ResultsStrip


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HDF_Vision")
        self.mode = "RUN"  # RUN alebo SETUP

        # Kamera
        self.cam = CameraService()
        self.cam.start()

        # DB + služby
        self.db = DbService()
        self.recipes = RecipeService(db=self.db)
        self.stats = StatsService(db=self.db)

        # Tool/Recipe
        try:
            if "default" not in self.recipes.list():
                self.recipes.create("default")
            self.recipes.load("default")
            self.tool = self.recipes.tool  # ToolService z RecipeService
            print("[Tool] Loaded recipe: default")
        except Exception as e:
            print("[Tool] Recipe not loaded:", e)
            self.tool = self.recipes.tool

        # ========== Root & Top bar ==========
        root = QWidget(); self.setCentralWidget(root)
        root_layout = QVBoxLayout(root); root_layout.setContentsMargins(10, 10, 10, 10); root_layout.setSpacing(8)

        top = QHBoxLayout(); top.setSpacing(8)
        title = QLabel("HDF_Vision")
        tf = QFont(); tf.setPointSize(14); tf.setBold(True)
        title.setFont(tf)
        top.addWidget(title)
        top.addStretch(1)

        # prepínač režimu (ikonový text)
        self.mode_btn = QPushButton("⚙ SETUP")
        self.mode_btn.clicked.connect(self.toggle_mode)
        top.addWidget(self.mode_btn)

        root_layout.addLayout(top)

        # ========== Recipe bar (pod titulkom) ==========
        bar = QHBoxLayout(); bar.setSpacing(8)
        bar.addWidget(QLabel("Recept:"))
        self.cmb_recipe = QComboBox(); self._refresh_recipe_list()
        self.cmb_recipe.currentTextChanged.connect(self.on_recipe_changed)
        bar.addWidget(self.cmb_recipe)

        self.btn_new = QPushButton("Nový")
        self.btn_ren = QPushButton("Premenovať")
        self.btn_del = QPushButton("Zmazať")
        bar.addWidget(self.btn_new); bar.addWidget(self.btn_ren); bar.addWidget(self.btn_del)

        self.btn_new.clicked.connect(self.on_recipe_new)
        self.btn_ren.clicked.connect(self.on_recipe_rename)
        self.btn_del.clicked.connect(self.on_recipe_delete)

        root_layout.addLayout(bar)

        # deliaca čiara
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken)
        root_layout.addWidget(line)

        # ========== Stacked RUN/SETUP ==========
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

        # ---------- RUN panel ----------
        self.panel_run = QWidget(); self.stack.addWidget(self.panel_run)
        run = QVBoxLayout(self.panel_run); run.setSpacing(8)

        # Status + metriky + štatistiky v jednom riadku
        status_row = QHBoxLayout(); status_row.setSpacing(16)
        self.lbl_status = QLabel("–")
        sf = QFont(); sf.setPointSize(28); sf.setBold(True)
        self.lbl_status.setFont(sf)
        self.lbl_status.setAlignment(Qt.AlignLeft)
        status_row.addWidget(self.lbl_status, 0)

        self.lbl_metrics = QLabel("")
        self.lbl_metrics.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        status_row.addWidget(self.lbl_metrics, 1)

        self.lbl_stats_day = QLabel("Štatistiky dnes: –")
        status_row.addWidget(self.lbl_stats_day, 0)

        run.addLayout(status_row)

        # Akcie (TRIGGER, Export, Wizard) + minimalizácia stripu
        actions = QHBoxLayout(); actions.setSpacing(8)
        self.btn_trigger = QPushButton("⏻ TRIGGER")  # berie posledný kontinuálny frame
        self.btn_trigger.clicked.connect(self.manual_trigger)
        actions.addWidget(self.btn_trigger)

        self.btn_export = QPushButton("📊 Export CSV (dnes)")
        self.btn_export.clicked.connect(self.export_csv_today)
        actions.addWidget(self.btn_export)

        self.btn_wizard_quick = QPushButton("📷 Golden WIZARD")
        self.btn_wizard_quick.clicked.connect(self.open_wizard)
        actions.addWidget(self.btn_wizard_quick)

        # Live náhľad + Heatmap toggle
        actions.addStretch(1)
        self.chk_heatmap = QCheckBox("Heatmap")
        self.chk_heatmap.setToolTip("Zobraziť farebnú mapu rozdielov voči golden")
        actions.addWidget(self.chk_heatmap)
        run.addLayout(actions)

        # Live view panel (aktuálny záber)
        self.live_view = QLabel("— aktuálny záber —")
        self.live_view.setAlignment(Qt.AlignCenter)
        self.live_view.setMinimumHeight(320)
        self.live_view.setStyleSheet("border: 1px solid #444; border-radius: 6px; background:#181818;")
        run.addWidget(self.live_view)

        # deliaca čiara
        line2 = QFrame(); line2.setFrameShape(QFrame.HLine); line2.setFrameShadow(QFrame.Sunken)
        run.addWidget(line2)

        # Strip v scroll area (minimalistické okraje)
        strip_header = QHBoxLayout()
        self.btn_strip_toggle = QToolButton()
        self.btn_strip_toggle.setText("▾ Posledné snímky")
        self.btn_strip_toggle.clicked.connect(self._toggle_strip)
        strip_header.addWidget(self.btn_strip_toggle)
        strip_header.addStretch(1)
        run.addLayout(strip_header)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{background-color:#181818; border: none;} ")
        strip_wrap = QWidget(); strip_layout = QVBoxLayout(strip_wrap); strip_layout.setContentsMargins(6,6,6,6)
        self.strip = ResultsStrip(self, limit=12)
        self.strip.setStyleSheet("QLabel{border:1px solid #333; border-radius:4px;} ")
        strip_layout.addWidget(self.strip)
        self.scroll.setWidget(strip_wrap)
        run.addWidget(self.scroll, 1)

        # timer pre RUN live view refresh
        self._run_timer = QTimer(self)
        self._run_timer.setInterval(100)  # ~10 FPS
        self._run_timer.timeout.connect(self._update_live_view)
        self._run_timer.start()

        # ---------- SETUP panel ----------
        self.panel_setup = QWidget(); self.stack.addWidget(self.panel_setup)
        s = QVBoxLayout(self.panel_setup); s.setSpacing(8)

        row1 = QHBoxLayout();
        self.btn_wizard = QPushButton("🔧 Golden Wizard", self)
        self.btn_wizard.clicked.connect(self.open_wizard)
        self.btn_save_golden = QPushButton("💾 Uložiť GOLDEN (one-shot)")
        self.btn_save_golden.clicked.connect(self.save_golden_clicked)
        row1.addWidget(self.btn_wizard); row1.addWidget(self.btn_save_golden); row1.addStretch(1)
        s.addLayout(row1)

        cam_title = QLabel("Nastavenia kamery"); tf2 = QFont(); tf2.setPointSize(12); tf2.setBold(True); cam_title.setFont(tf2)
        s.addWidget(cam_title)

        # Rozlíšenie
        res_line = QHBoxLayout()
        res_line.addWidget(QLabel("Rozlíšenie:"))
        self.cmb_res = QComboBox()
        self.cmb_res.addItems([
            "1920x1080@60 Y8",
            "1280x720@60 Y8",
            "2592x1944@30 Y8 (len setup/pomalé)"
        ])
        res_line.addWidget(self.cmb_res)
        s.addLayout(res_line)

        # Expo/Gain
        eg_line = QHBoxLayout()
        eg_line.addWidget(QLabel("Expo [µs] (XU stub):"))
        self.spin_expo = QSpinBox(); self.spin_expo.setRange(1, 1_000_000); self.spin_expo.setValue(8000)
        eg_line.addWidget(self.spin_expo)
        eg_line.addSpacing(12)
        eg_line.addWidget(QLabel("Gain [dB] (XU stub):"))
        self.spin_gain = QSpinBox(); self.spin_gain.setRange(0, 48); self.spin_gain.setValue(0)
        eg_line.addWidget(self.spin_gain)
        eg_line.addStretch(1)
        s.addLayout(eg_line)

        # XU panel
        line3 = QFrame(); line3.setFrameShape(QFrame.HLine); line3.setFrameShadow(QFrame.Sunken)
        s.addWidget(line3)
        self.xu = XUPanel(self)
        s.addWidget(self.xu)

        # default RUN zobrazenie
        self.stack.setCurrentWidget(self.panel_run)

        # Spusť retenciu na pozadí (jednorazovo pri štarte)
        Thread(target=lambda: RetentionService().run_once(verbose=False), daemon=True).start()

    # ---------- Helpers ----------
    def current_recipe_name(self) -> str:
        return getattr(self.tool, "recipe", "default") or "default"

    # ---------- UI akcie ----------
    def toggle_mode(self):
        if self.stack.currentWidget() is self.panel_run:
            self.stack.setCurrentWidget(self.panel_setup)
            self.mode = "SETUP"
            self.mode_btn.setText("▶ RUN")
        else:
            self.stack.setCurrentWidget(self.panel_run)
            self.mode = "RUN"
            self.mode_btn.setText("⚙ SETUP")

    def manual_trigger(self):
        try:
            frame = self.cam.last_frame()  # posledný kontinuálny frame
            meta = {"mode": "manual"}

            # D3: vyhodnotenie podľa receptu
            nok = False
            metrics = {}
            try:
                res = self.tool.evaluate(frame)
                nok = (not res["ok"])   # True ak je nezhoda
                metrics = res["metrics"]
            except Exception as e:
                print("[Tool] evaluate failed:", e)

            # UI update
            if nok:
                self.lbl_status.setText("NOK")
                self.lbl_status.setStyleSheet("color: #ff3366;")
            else:
                self.lbl_status.setText("OK")
                self.lbl_status.setStyleSheet("color: #33dd66;")

            st = self.stats.daily_for_recipe(self.current_recipe_name())
            self.lbl_stats_day.setText(f"Štatistiky dnes: total={st['total']}  OK={st['ok']}  NOK={st['nok']}  yield={st['yield']}%")
            self.lbl_metrics.setText(
                f'SSIM={metrics.get("ssim","-")}  blobs={metrics.get("blob_count","-")}  area={metrics.get("total_area","-")}'
            )

            # Uloženie (NOK flag pre retenciu)
            save_production_result(
                frame,
                meta | {"metrics": metrics},
                self.current_recipe_name(),
                store_full_nok=True,
                nok=nok
            )

            # refresh stripu po uložení výsledku
            self.strip.reload()

        except Exception:
            import traceback; traceback.print_exc()

    def save_golden_clicked(self):
        frame = self.cam.one_shot()
        path = save_golden(frame, self.current_recipe_name())
        self.lbl_status.setText(f"GOLDEN uložený: {path}")

    def open_wizard(self):
        dlg = GoldenWizard(self.cam, self)
        dlg.resize(1200, 800)
        dlg.exec()

    def _toggle_strip(self):
        # minimalizácia / rozbalenie výsledkového stripu
        is_visible = self.scroll.isVisible()
        self.scroll.setVisible(not is_visible)
        self.btn_strip_toggle.setText("▾ Posledné snímky" if not is_visible else "▸ Posledné snímky")

    def _update_live_view(self):
        try:
            frame = self.cam.last_frame()
            if frame is None:
                return
            img = frame
            if self.chk_heatmap.isChecked():
                try:
                    img = self._make_heatmap_overlay(frame)
                except Exception as e:
                    # ak zlyhá heatmap, zobraz surový obraz
                    img = frame
            self._show_gray_or_bgr(self.live_view, img)
        except Exception:
            pass

    def _show_gray_or_bgr(self, label: QLabel, img):
        import numpy as np
        if img.ndim == 2:  # GRAY8
            h, w = img.shape
            q = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
            pm = QPixmap.fromImage(q.copy()).scaled(label.width(), label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(pm)
        else:  # BGR
            import cv2
            bgr = img
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb.shape
            q = QImage(rgb.data, w, h, w*3, QImage.Format_RGB888)
            pm = QPixmap.fromImage(q.copy()).scaled(label.width(), label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(pm)

    def _make_heatmap_overlay(self, frame_u8):
        """Vytvorí farebnú heatmapu rozdielov voči golden a preloží ju cez aktuálny obraz."""
        import os
        import imageio.v3 as iio
        import cv2
        import numpy as np
        name = self.current_recipe_name()
        golden_fp = f"/data/recipes/{name}/golden.png"
        if not os.path.exists(golden_fp):
            return frame_u8
        g = iio.imread(golden_fp)
        if g.ndim == 3:
            g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY) if g.shape[2]==3 else g[:,:,0]
        # veľkosť na aktuálny frame, pre istotu
        if g.shape != frame_u8.shape:
            g = cv2.resize(g, (frame_u8.shape[1], frame_u8.shape[0]), interpolation=cv2.INTER_AREA)
        # absolútna diferencía, normalizácia 0..255
        diff = cv2.absdiff(frame_u8, g)
        if diff.max() > 0:
            diff_norm = cv2.convertScaleAbs(diff, alpha=255.0/max(1, diff.max()))
        else:
            diff_norm = diff
        heat = cv2.applyColorMap(diff_norm, cv2.COLORMAP_JET)  # BGR
        # prehľadná zmes (alpha 0.45)
        base = cv2.cvtColor(frame_u8, cv2.COLOR_GRAY2BGR)
        out = cv2.addWeighted(base, 0.55, heat, 0.45, 0.0)
        return out

    def closeEvent(self, e):
        try:
            self.cam.stop()
        finally:
            e.accept()

    def _refresh_recipe_list(self):
        self.cmb_recipe.blockSignals(True)
        items = self.recipes.list()
        self.cmb_recipe.clear()
        self.cmb_recipe.addItems(items)
        # vyber aktuálny tool.recipe
        cur = getattr(self.tool, "recipe", "default")
        ix = self.cmb_recipe.findText(cur)
        if ix >= 0:
            self.cmb_recipe.setCurrentIndex(ix)
        self.cmb_recipe.blockSignals(False)

    def on_recipe_changed(self, name: str):
        try:
            self.recipes.load(name)
            self.tool = self.recipes.tool
            self.lbl_status.setText("Recipe loaded.")
            # refresh štatistík + strip
            st = self.stats.daily_for_recipe(name)
            self.lbl_stats_day.setText(f"Štatistiky dnes: total={st['total']}  OK={st['ok']}  NOK={st['nok']}  yield={st['yield']}%")
            self.strip.reload()
        except Exception as e:
            self.lbl_status.setText(f"Load failed: {e}")

    def on_recipe_new(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Nový recept", "Názov receptu:")
        if not ok or not name.strip():
            return
        name = name.strip()
        self.recipes.create(name)
        self._refresh_recipe_list()
        self.recipes.load(name)
        self.tool = self.recipes.tool

    def on_recipe_rename(self):
        from PySide6.QtWidgets import QInputDialog
        old = self.current_recipe_name()
        new, ok = QInputDialog.getText(self, "Premenovať recept", f"Nový názov pre '{old}':")
        if not ok or not new.strip():
            return
        new = new.strip()
        self.recipes.rename(old, new)
        self._refresh_recipe_list()
        self.recipes.load(new)
        self.tool = self.recipes.tool

    def on_recipe_delete(self):
        from PySide6.QtWidgets import QMessageBox
        name = self.current_recipe_name()
        if name == "default":
            QMessageBox.warning(self, "Upozornenie", "Recept 'default' nie je možné zmazať.")
            return
        r = QMessageBox.question(self, "Zmazať recept", f"Naozaj zmazať '{name}'?")
        if r != QMessageBox.Yes:
            return
        self.recipes.delete(name)
        self._refresh_recipe_list()
        self.recipes.load("default")
        self.tool = self.recipes.tool

    def export_csv_today(self):
        rid = self.db.recipe_id(self.current_recipe_name())
        if rid is None:
            self.lbl_status.setText("Nie je vybraný recept.")
            return
        out = f"/data/runs/{self.current_recipe_name()}_today.csv"
        try:
            path = self.db.export_csv_today(rid, out)
            self.lbl_status.setText(f"CSV export: {path}")
        except Exception as e:
            self.lbl_status.setText(f"CSV error: {e}")

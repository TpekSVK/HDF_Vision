from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox, QSpinBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap

from app.services.live_preview_service import LivePreviewService
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
            # zaruč, že existuje "default"
            if "default" not in self.recipes.list():
                self.recipes.create("default")
            self.recipes.load("default")
            self.tool = self.recipes.tool  # ToolService z RecipeService
            print("[Tool] Loaded recipe: default")
        except Exception as e:
            print("[Tool] Recipe not loaded:", e)
            self.tool = self.recipes.tool

        # Root layout
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # Prepínač RUN/SETUP
        self.mode_btn = QPushButton("Prepnúť do SETUP")
        self.mode_btn.clicked.connect(self.toggle_mode)
        layout.addWidget(self.mode_btn)

        # ---------- Recipe bar ----------
        bar = QHBoxLayout()
        layout.addLayout(bar)
        bar.addWidget(QLabel("Recept:"))
        self.cmb_recipe = QComboBox()
        self._refresh_recipe_list()
        self.cmb_recipe.currentTextChanged.connect(self.on_recipe_changed)
        bar.addWidget(self.cmb_recipe)

        self.btn_new = QPushButton("Nový")
        self.btn_ren = QPushButton("Premenovať")
        self.btn_del = QPushButton("Zmazať")
        bar.addWidget(self.btn_new); bar.addWidget(self.btn_ren); bar.addWidget(self.btn_del)

        self.btn_new.clicked.connect(self.on_recipe_new)
        self.btn_ren.clicked.connect(self.on_recipe_rename)
        self.btn_del.clicked.connect(self.on_recipe_delete)

        # ---------- RUN panel ----------
        self.panel_run = QWidget()
        self.runLayout = QVBoxLayout(self.panel_run)

        # Veľký OK/NOK + metriky
        self.lbl_status = QLabel("–")
        f = QFont(); f.setPointSize(28); f.setBold(True)
        self.lbl_status.setFont(f)
        self.lbl_status.setAlignment(Qt.AlignLeft)

        self.lbl_metrics = QLabel("")
        self.lbl_metrics.setAlignment(Qt.AlignLeft)

        self.lbl_stats_day = QLabel("Štatistiky dnes: –")

        self.btn_trigger = QPushButton("Manuálny TRIGGER")  # berieme z ring bufferu
        self.btn_trigger.clicked.connect(self.manual_trigger)

        # Strip posledných snímok + Export CSV
        self.strip = ResultsStrip(self, limit=12)
        self.btn_export = QPushButton("Export CSV (dnes)")
        self.btn_export.clicked.connect(self.export_csv_today)

        # Poradie: status → metriky → stats → trigger → strip → export
        self.runLayout.addWidget(self.lbl_status)
        self.runLayout.addWidget(self.lbl_metrics)
        self.runLayout.addWidget(self.lbl_stats_day)
        self.runLayout.addWidget(self.btn_trigger)
        self.runLayout.addWidget(self.strip)
        self.runLayout.addWidget(self.btn_export)

        layout.addWidget(self.panel_run)

        # ---------- SETUP panel ----------
        self.panel_setup = QWidget()
        v = QVBoxLayout(self.panel_setup)

        v.addWidget(QLabel("Nastavenia kamery"))
        self.btn_wizard = QPushButton("Golden WIZARD", self)
        self.btn_wizard.clicked.connect(self.open_wizard)
        v.addWidget(self.btn_wizard)

        v.addWidget(QLabel("Limity / Prahy"))
        self.th_panel = ThresholdsPanel(self)
        v.addWidget(self.th_panel)

        # Rozlíšenie (placeholder)
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

        # Expo/gain (XU stub – doplníme neskôr)
        v.addWidget(QLabel("Expo [us] (XU stub):"))
        self.spin_expo = QSpinBox(); self.spin_expo.setRange(1, 1_000_000); self.spin_expo.setValue(8000)
        v.addWidget(self.spin_expo)

        v.addWidget(QLabel("Gain [dB] (XU stub):"))
        self.spin_gain = QSpinBox(); self.spin_gain.setRange(0, 48); self.spin_gain.setValue(0)
        v.addWidget(self.spin_gain)

        # Uložiť GOLDEN z aktuálneho one-shotu
        self.btn_save_golden = QPushButton("Uložiť GOLDEN (current one-shot)")
        self.btn_save_golden.clicked.connect(self.save_golden_clicked)
        v.addWidget(self.btn_save_golden)

        # --- Live náhľad (SETUP) ---
        self.live_on = QComboBox(); self.live_on.addItems(["Live OFF","Live ON"])
        self.live_on.currentIndexChanged.connect(self._live_toggle)
        v.addWidget(self.live_on)

        self.live_lbl = QLabel("—")
        self.live_lbl.setFixedHeight(320)
        self.live_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(self.live_lbl)

        # XU panel
        self.xu = XUPanel(self)
        v.addWidget(self.xu)

        # live helpery
        self._lp = LivePreviewService(getattr(self.cam, "devices", ["/dev/video0"])[0], 1280, 720, 60)
        self._live_timer = QTimer(self); self._live_timer.setInterval(50)  # ~20 FPS
        self._live_timer.timeout.connect(self._live_tick)

        layout.addWidget(self.panel_setup)
        self.panel_setup.hide()  # default RUN

    # ---------- Helpers ----------
    def current_recipe_name(self) -> str:
        # Zatiaľ používame to, čo je v ToolService (alebo "default")
        return getattr(self.tool, "recipe", "default") or "default"

    # ---------- UI akcie ----------
    def toggle_mode(self):
        if self.mode == "RUN":
            self.mode = "SETUP"
            self.mode_btn.setText("Prepnúť do RUN")
            self.panel_setup.show()
            self.panel_run.hide()
        else:
            self.mode = "RUN"
            self.mode_btn.setText("Prepnúť do SETUP")
            self.panel_setup.hide()
            self.panel_run.show()

    def manual_trigger(self):
        try:
            frame = self.cam.last_frame()  # berieme posledný kontinuálny frame
            meta = {"mode": "manual"}

            # D3: vyhodnotenie podľa receptu
            nok = False
            metrics = {}
            try:
                res = self.tool.evaluate(frame)
                nok = (not res["ok"])
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

        except Exception as e:
            import traceback; traceback.print_exc()

    def save_golden_clicked(self):
        frame = self.cam.one_shot()
        path = save_golden(frame, self.current_recipe_name())
        self.lbl_status.setText(f"GOLDEN uložený: {path}")

    def open_wizard(self):
        dlg = GoldenWizard(self.cam, self)
        dlg.resize(1200, 800)
        dlg.exec()

    def closeEvent(self, e):
        # najprv vypni live, potom kameru, potom zavri okno
        try:
            self._live_timer.stop()
            self._lp.stop()
        except Exception:
            pass
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

    def _live_toggle(self, idx):
        if idx == 1:  # ON
            try:
                self._lp.start()
                self._live_timer.start()
            except Exception as e:
                self.live_lbl.setText(f"Live error: {e}")
                self.live_on.setCurrentIndex(0)
        else:
            try:
                self._live_timer.stop()
                self._lp.stop()
                self.live_lbl.setText("—")
            except Exception:
                pass

    def _live_tick(self):
        img = self._lp.last_frame_u8()
        if img is None:
            return
        h, w = img.shape[:2]
        q = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(q.copy()).scaled(self.live_lbl.width(), self.live_lbl.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.live_lbl.setPixmap(pm)

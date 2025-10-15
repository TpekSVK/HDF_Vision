from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox, QSpinBox,
    QStackedWidget, QFrame, QScrollArea, QCheckBox, QToolButton, QSizePolicy, QGridLayout
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

import numpy as np

from threading import Thread
from app.services.retention_service import RetentionService

from app.ui.xu_panel import XUPanel

from app.services.camera_service import CameraService
from app.services.storage_service import save_golden, save_production_result, load_recipe_config
from app.ui.golden_wizard import GoldenWizard
from app.services.db_service import DbService
from app.services.recipe_service import RecipeService
from app.services.stats_service import StatsService
from app.ui.thresholds_panel import ThresholdsPanel
from app.ui.results_strip import ResultsStrip
from app.services.tool_service import run_pipeline
from app.services.tool_registry import ToolRegistry


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HDF_Vision")
        self.mode = "RUN"  # RUN alebo SETUP

        # Live režim (RUN):
        self.live_enabled = False
        self._last_trigger_frame = None

        # Kamera
        self.cam = CameraService()
        self.cam.start()

        # DB + služby
        self.db = DbService()
        self.recipes = RecipeService(db=self.db)
        self.stats = StatsService(db=self.db)

        self._last_tool_reports: list[dict[str, Any]] = []
        self._last_cycle_time_ms: float | None = None
        self._last_pipeline_status: str | None = None
        self._tool_selector_items: list[dict[str, Any]] = []

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
        status_container = QWidget()
        status_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        status_row = QHBoxLayout(status_container); status_row.setSpacing(16)
        self.lbl_status = QLabel("–")
        sf = QFont(); sf.setPointSize(28); sf.setBold(True)
        self.lbl_status.setFont(sf)
        self.lbl_status.setAlignment(Qt.AlignLeft)
        status_row.addWidget(self.lbl_status, 0)

        status_container.setMaximumHeight(status_container.sizeHint().height())
        run.addWidget(status_container)

        # Akcie (TRIGGER, Export, Wizard) + Live + Heatmap + minimalizácia stripu
        actions_container = QWidget()
        actions_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        actions = QHBoxLayout(actions_container); actions.setSpacing(8)
        self.btn_trigger = QPushButton("⏻ TRIGGER")  # berie posledný kontinuálny frame
        self.btn_trigger.clicked.connect(self.manual_trigger)
        actions.addWidget(self.btn_trigger)

        self.btn_export = QPushButton("📊 Export CSV (dnes)")
        self.btn_export.clicked.connect(self.export_csv_today)
        actions.addWidget(self.btn_export)

        self.btn_wizard_quick = QPushButton("📷 Golden WIZARD")
        self.btn_wizard_quick.clicked.connect(self.open_wizard)
        actions.addWidget(self.btn_wizard_quick)

        actions.addStretch(1)
        # Live toggle
        self.btn_live = QPushButton("Live OFF")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._toggle_live)
        actions.addWidget(self.btn_live)

        # Heatmap toggle
        self.chk_heatmap = QCheckBox("Heatmap")
        self.chk_heatmap.setToolTip("Zobraziť farebnú mapu rozdielov voči golden")
        actions.addWidget(self.chk_heatmap)

        self.lbl_tool_selector = QLabel("Tool:")
        actions.addWidget(self.lbl_tool_selector)
        self.cmb_tool = QComboBox()
        self.cmb_tool.setEnabled(False)
        self.cmb_tool.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb_tool.currentIndexChanged.connect(self._on_tool_selection_changed)
        actions.addWidget(self.cmb_tool)

        actions_container.setMaximumHeight(actions_container.sizeHint().height())
        run.addWidget(actions_container)

        # Live view + pravý sidebar so štatistikami
        preview_container = QWidget()
        preview_container.setMaximumHeight(540)
        preview_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        preview_row = QHBoxLayout(preview_container); preview_row.setSpacing(12)

        # Live view panel (aktuálny záber)
        self.live_view = QLabel("— aktuálny záber —")
        self.live_view.setAlignment(Qt.AlignCenter)
        self.live_view.setMinimumSize(640, 360)
        self.live_view.setMaximumHeight(540)
        self.live_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.live_view.setStyleSheet("border: 1px solid #444; border-radius: 6px; background:#181818;")
        self.live_view.setContentsMargins(0,0,0,0)
        preview_row.addWidget(self.live_view, 1)

        # Pravý panel (štatistiky + posledné metriky)
        self.side_panel = QWidget(); self.side_panel.setObjectName("sidePanel")
        self.side_panel.setMaximumHeight(540)
        self.side_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        side = QVBoxLayout(self.side_panel); side.setSpacing(8); side.setContentsMargins(10,10,10,10)
        self.side_panel.setStyleSheet("#sidePanel{border:1px solid #333; border-radius:6px; background:#111;} QLabel{color:#ddd}")

        # Nadpis a recept
        t = QLabel("Štatistiky & Metriky"); tf = QFont(); tf.setPointSize(12); tf.setBold(True); t.setFont(tf)
        side.addWidget(t)
        self.sb_recipe = QLabel("Recept: –")
        side.addWidget(self.sb_recipe)
        self.sb_pose = QLabel("Pose alignment: –")
        side.addWidget(self.sb_pose)

        # Denné štatistiky
        side.addWidget(QLabel("— Dnes —"))
        self.sb_total = QLabel("Celkom: –")
        self.sb_ok    = QLabel("OK: –")
        self.sb_nok   = QLabel("NOK: –")
        self.sb_yield = QLabel("Yield: –")
        for w in (self.sb_total, self.sb_ok, self.sb_nok, self.sb_yield):
            side.addWidget(w)

        # Posledné meranie (TRIGGER)
        side.addWidget(QLabel("— Posledné meranie —"))
        self.metrics_container = QWidget()
        self.metrics_layout = QGridLayout(self.metrics_container)
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setSpacing(4)
        self.metrics_layout.setColumnStretch(1, 1)
        self._metrics_widgets: list[QLabel] = []
        self._metrics_placeholder = QLabel("Žiadne dáta")
        self._metrics_placeholder.setStyleSheet("color:#777;")
        self.metrics_layout.addWidget(self._metrics_placeholder, 0, 0, 1, 2)
        side.addWidget(self.metrics_container)

        side.addStretch(1)
        preview_row.addWidget(self.side_panel, 1)

        run.addWidget(preview_container)

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
        # spúšťa sa až pri Live ON v _toggle_live()

        # inicializuj pravý panel hodnotami
        self._update_sidebar()
        self._refresh_tool_selector()

        # maximalizovať a uzamknúť veľkosť okna po zobrazení
        QTimer.singleShot(0, self._maximize_and_lock)

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

        # Limity / Prahy (Thresholds)
        line4 = QFrame(); line4.setFrameShape(QFrame.HLine); line4.setFrameShadow(QFrame.Sunken)
        s.addWidget(line4)
        th_title = QLabel("Limity / Prahy"); tf3 = QFont(); tf3.setPointSize(12); tf3.setBold(True); th_title.setFont(tf3)
        s.addWidget(th_title)
        self.th_panel = ThresholdsPanel(self)
        s.addWidget(self.th_panel)

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
            frame = self.cam.last_frame()
            if frame is None:
                self.lbl_status.setText("Žiadny snímok z kamery.")
                return

            frame_u8 = frame.copy()
            recipe_name = self.current_recipe_name()
            golden = getattr(self.tool, "golden", None)

            try:
                recipe_cfg = load_recipe_config(recipe_name)
            except Exception as exc:
                print(f"[Tool] load_recipe_config failed for {recipe_name}: {exc}")
                recipe_cfg = None

            if golden is None or recipe_cfg is None or not getattr(recipe_cfg, "tools", []):
                self._run_legacy_trigger(frame_u8, recipe_name)
                return

            if not getattr(recipe_cfg, "regions", None):
                recipe_cfg.regions = list(getattr(self.tool, "regions", []) or [])
            recipe_cfg.pose_enabled = bool(getattr(self.tool, "pose_enabled", True))

            result = run_pipeline(
                golden,
                frame_u8,
                recipe_cfg,
                recipe_name=recipe_name,
                notes="manual_trigger",
            )

            status = (result.status or "ok").lower()
            status_text = status.upper()
            color_map = {"ok": "#33dd66", "warn": "#e67e22", "nok": "#ff3366"}
            self.lbl_status.setText(status_text)
            self.lbl_status.setStyleSheet(f"color: {color_map.get(status, '#33dd66')};")

            context_frame = getattr(result.context, "frame_aligned", None)
            if context_frame is None:
                context_frame = getattr(result.context, "frame", None)
            if isinstance(context_frame, np.ndarray):
                self._last_trigger_frame = context_frame.copy()
            else:
                self._last_trigger_frame = frame_u8.copy()

            reports = [self._serialize_tool_report(report) for report in result.per_tool]
            st = self.stats.daily_for_recipe(recipe_name)
            self._update_sidebar(st, reports, status=status, cycle_time_ms=float(result.cycle_time_ms))

            diagnostics_payload: list[Any] = []
            for diag in getattr(result, "diagnostics", []) or []:
                diagnostics_payload.append(self._simplify_value(diag))

            combined_metrics = self._merge_pipeline_metrics(reports)

            meta_payload = {
                "mode": "manual",
                "status": status,
                "cycle_time_ms": float(result.cycle_time_ms),
                "per_tool": reports,
                "diagnostics": diagnostics_payload,
                "metrics": combined_metrics,
            }
            if getattr(result, "policy_applied", None):
                meta_payload["policy_applied"] = result.policy_applied

            save_production_result(
                frame_u8,
                meta_payload,
                recipe_name,
                store_full_nok=True,
                nok=status != "ok",
            )

            self.strip.reload()

            if not self.live_enabled and self._last_trigger_frame is not None:
                img = self._last_trigger_frame
                if self.chk_heatmap.isChecked():
                    try:
                        img = self._make_heatmap_overlay(img)
                    except Exception:
                        pass
                self._show_gray_or_bgr(self.live_view, img)

        except Exception:
            import traceback; traceback.print_exc()

    def _run_legacy_trigger(self, frame_u8, recipe_name: str):
        try:
            res = self.tool.evaluate(frame_u8)
            ok = bool(res.get("ok", False))
            metrics = dict(res.get("metrics", {}) or {})
            status = "ok" if ok else "nok"
        except Exception as exc:
            print("[Tool] evaluate failed:", exc)
            metrics = {}
            status = "nok"

        self._last_trigger_frame = frame_u8.copy() if isinstance(frame_u8, np.ndarray) else frame_u8

        color = "#33dd66" if status == "ok" else "#ff3366"
        self.lbl_status.setText(status.upper())
        self.lbl_status.setStyleSheet(f"color: {color};")

        legacy_report = [{
            "id": "legacy",
            "name": "Inspection",
            "type": "legacy",
            "status": status,
            "latency_ms": None,
            "metrics": metrics,
            "diagnostics": {},
        }]

        self._last_cycle_time_ms = None
        st = self.stats.daily_for_recipe(recipe_name)
        self._update_sidebar(st, legacy_report, status=status)

        meta_payload = {
            "mode": "manual",
            "status": status,
            "metrics": metrics,
            "per_tool": legacy_report,
        }

        save_production_result(
            frame_u8,
            meta_payload,
            recipe_name,
            store_full_nok=True,
            nok=status != "ok",
        )

        self.strip.reload()

        if not self.live_enabled and self._last_trigger_frame is not None:
            img = self._last_trigger_frame
            if self.chk_heatmap.isChecked():
                try:
                    img = self._make_heatmap_overlay(img)
                except Exception:
                    pass
            self._show_gray_or_bgr(self.live_view, img)

    def save_golden_clicked(self):
        frame = self.cam.one_shot()
        path = save_golden(frame, self.current_recipe_name())
        self.lbl_status.setText(f"GOLDEN uložený: {path}")

    def open_wizard(self):
        dlg = GoldenWizard(self.cam, self.recipes, self)
        dlg.resize(1200, 800)
        dlg.exec()
        self._update_sidebar()
        self._refresh_tool_selector()

    def _toggle_strip(self):
        # minimalizácia / rozbalenie výsledkového stripu
        is_visible = self.scroll.isVisible()
        self.scroll.setVisible(not is_visible)
        self.btn_strip_toggle.setText("▾ Posledné snímky" if not is_visible else "▸ Posledné snímky")

    def _toggle_live(self):
        self.live_enabled = self.btn_live.isChecked()
        self.btn_live.setText("Live ON" if self.live_enabled else "Live OFF")
        if self.live_enabled:
            self._run_timer.start()
        else:
            self._run_timer.stop()
            # po vypnutí live zobraz posledný manuálny trigger, ak existuje
            if self._last_trigger_frame is not None:
                img = self._last_trigger_frame
                if self.chk_heatmap.isChecked():
                    try:
                        img = self._make_heatmap_overlay(img)
                    except Exception:
                        pass
                self._show_gray_or_bgr(self.live_view, img)

    def _update_live_view(self):
        try:
            # Zdroj podľa live stavu
            if self.live_enabled:
                src = self.cam.last_frame()
            else:
                src = self._last_trigger_frame
            if src is None:
                return
            img = src
            if self.chk_heatmap.isChecked():
                try:
                    img = self._make_heatmap_overlay(src)
                except Exception:
                    img = src
            self._show_gray_or_bgr(self.live_view, img)
        except Exception:
            pass

    def _show_gray_or_bgr(self, label: QLabel, img):
        import numpy as np
        from PySide6.QtGui import QImage, QPixmap
        if img is None:
            label.clear(); return
        if not img.flags['C_CONTIGUOUS']:
            img = np.ascontiguousarray(img)
        # cieľový rozmer ber z obsahového rectu labelu (stabilné)
        target = label.contentsRect().size()
        tw, th = max(1, target.width()), max(1, target.height())
        if img.ndim == 2:
            h, w = img.shape
            q = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
            pm = QPixmap.fromImage(q.copy()).scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(pm)
        else:
            import cv2
            bgr = img
            if bgr.shape[2] == 4:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGRA2RGB)
            else:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            if not rgb.flags['C_CONTIGUOUS']:
                rgb = np.ascontiguousarray(rgb)
            h, w, _ = rgb.shape
            q = QImage(rgb.data, w, h, w*3, QImage.Format_RGB888)
            pm = QPixmap.fromImage(q.copy()).scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
        base = cv2.cvtColor(frame_u8, cv2.COLOR_GRAY2BGR)
        out = cv2.addWeighted(base, 0.55, heat, 0.45, 0.0)
        return out

    def _update_sidebar(
        self,
        st: dict | None = None,
        per_tool: Sequence[dict[str, Any]] | None = None,
        *,
        status: str | None = None,
        cycle_time_ms: float | None = None,
    ):
        """Naplní pravý panel dennými štatistikami a poslednými metrikami."""
        try:
            name = self.current_recipe_name()
            self.sb_recipe.setText(f"Recept: {name}")
            if st is None:
                st = self.stats.daily_for_recipe(name)
            pose_enabled = getattr(self.tool, "pose_enabled", True)
            self.sb_pose.setText(f"Pose alignment: {'ON' if pose_enabled else 'OFF'}")
            self.sb_total.setText(f"Celkom: {st.get('total','–')}")
            self.sb_ok.setText(f"OK: {st.get('ok','–')}")
            self.sb_nok.setText(f"NOK: {st.get('nok','–')}")
            self.sb_yield.setText(f"Yield: {st.get('yield','–')}%")

            if per_tool is not None:
                self._last_tool_reports = [dict(entry) for entry in per_tool]
            if status is not None:
                self._last_pipeline_status = status
            if cycle_time_ms is not None:
                self._last_cycle_time_ms = cycle_time_ms

            self._update_metrics_panel()
        except Exception:
            pass

    def _set_metrics_rows(self, rows: Sequence[tuple[str, str]]):
        try:
            while self.metrics_layout.count():
                item = self.metrics_layout.takeAt(0)
                widget = item.widget()
                if widget is None:
                    continue
                if widget is self._metrics_placeholder:
                    widget.setParent(None)
                else:
                    widget.deleteLater()
            self._metrics_widgets.clear()

            if not rows:
                self._metrics_placeholder.setParent(self.metrics_container)
                self._metrics_placeholder.setText("Žiadne dáta")
                self.metrics_layout.addWidget(self._metrics_placeholder, 0, 0, 1, 2)
                self._metrics_placeholder.show()
                return

            self._metrics_placeholder.hide()
            for row_index, (label_text, value_text) in enumerate(rows):
                name_label = QLabel(label_text)
                value_label = QLabel(value_text)
                value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.metrics_layout.addWidget(name_label, row_index, 0)
                self.metrics_layout.addWidget(value_label, row_index, 1)
                self._metrics_widgets.extend([name_label, value_label])
        except Exception:
            pass

    def _update_metrics_panel(self):
        try:
            selection = self.cmb_tool.currentData()
            if not self._last_tool_reports:
                self._set_metrics_rows([])
                return

            if selection is None:
                rows = self._build_summary_rows()
            else:
                rows = self._build_tool_metric_rows(selection)
            self._set_metrics_rows(rows)
        except Exception:
            self._set_metrics_rows([])

    def _build_summary_rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if self._last_pipeline_status:
            rows.append(("Celkový status", self._last_pipeline_status.upper()))
        if self._last_cycle_time_ms is not None:
            rows.append(("Cyklus [ms]", self._format_metric_value(self._last_cycle_time_ms)))
        for report in self._last_tool_reports:
            name = str(report.get("name") or report.get("id") or "Tool")
            status = str(report.get("status") or "").upper() or "—"
            rows.append((name, status))
        return rows or [("Info", "Žiadne dáta")]

    def _build_tool_metric_rows(self, selection: dict[str, Any]) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        tool_id = str(selection.get("id")) if isinstance(selection, Mapping) else str(selection or "")
        report = next((r for r in self._last_tool_reports if str(r.get("id")) == tool_id), None)
        if report is None:
            return [("Info", "Žiadne dáta")]

        name = str(report.get("name") or tool_id or "Tool")
        status = str(report.get("status") or "").upper() or "—"
        rows.append((f"{name} status", status))

        latency_value = report.get("latency_ms")
        if latency_value is None:
            metrics_map = report.get("metrics")
            if isinstance(metrics_map, Mapping):
                latency_value = metrics_map.get("latency_ms")
        if latency_value is not None:
            rows.append(("Čas [ms]", self._format_metric_value(latency_value)))

        metrics = {}
        if isinstance(report.get("metrics"), Mapping):
            metrics = dict(report["metrics"])
        metrics.pop("latency_ms", None)

        tool_type = str(report.get("type") or "")
        definition = ToolRegistry.get_tool_definition(tool_type) if tool_type else None
        if definition is not None:
            spec_entries = sorted(
                getattr(definition, "metrics_spec", ()) or (),
                key=lambda entry: (
                    -int(getattr(entry, "priority", 0) or 0),
                    str(getattr(entry, "key", "")),
                ),
            )
            for spec in spec_entries:
                key = getattr(spec, "key", "")
                if not key or key not in metrics:
                    continue
                label = (getattr(spec, "description", "") or key or "Metric").strip()
                unit = getattr(spec, "unit", None)
                if unit:
                    label = f"{label} [{unit}]"
                rows.append((label, self._format_metric_value(metrics.pop(key))))

        for key in sorted(metrics.keys()):
            rows.append((str(key), self._format_metric_value(metrics[key])))

        return rows or [("Info", "Žiadne dáta")]

    def _format_metric_value(self, value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "áno" if value else "nie"
        if isinstance(value, Real) and not isinstance(value, bool):
            val = float(value)
            if not math.isfinite(val):
                return "—"
            if abs(val) >= 1000 or (0 < abs(val) < 0.001):
                return f"{val:.3g}"
            text = f"{val:.4f}".rstrip("0").rstrip(".")
            return text or "0"
        return str(value)

    def _simplify_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return float(value) if math.isfinite(value) else None
        if hasattr(value, "item"):
            try:
                return self._simplify_value(value.item())
            except Exception:
                return None
        if isinstance(value, Mapping):
            return {str(k): self._simplify_value(v) for k, v in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self._simplify_value(v) for v in value]
        try:
            return float(value)
        except Exception:
            return str(value)

    def _serialize_tool_report(self, report) -> dict[str, Any]:
        metrics = {}
        raw_metrics = getattr(report, "metrics", None)
        if isinstance(raw_metrics, Mapping):
            metrics = {str(k): self._simplify_value(v) for k, v in raw_metrics.items()}
        diagnostics = {}
        raw_diag = getattr(report, "diagnostics", None)
        if isinstance(raw_diag, Mapping):
            diagnostics = {str(k): self._simplify_value(v) for k, v in raw_diag.items()}

        latency_value = self._simplify_value(getattr(report, "latency_ms", None))
        if latency_value is not None:
            metrics.setdefault("latency_ms", latency_value)

        tool = getattr(report, "tool", None)
        tool_name = getattr(tool, "name", None) if tool is not None else None
        tool_type = getattr(tool, "type", None) if tool is not None else None
        tool_order = getattr(tool, "order", None) if tool is not None else None
        tool_id = getattr(report, "tool_id", None) or tool_name or (f"tool_{tool_order}" if tool_order is not None else None)

        return {
            "id": tool_id,
            "name": tool_name or tool_id or "Tool",
            "type": tool_type or diagnostics.get("type"),
            "status": getattr(report, "status", None),
            "latency_ms": latency_value,
            "metrics": metrics,
            "diagnostics": diagnostics,
        }

    def _merge_pipeline_metrics(self, reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
        combined: dict[str, Any] = {}
        for entry in reports:
            metrics = entry.get("metrics")
            if not isinstance(metrics, Mapping):
                continue
            for key, value in metrics.items():
                if key not in combined and value is not None:
                    combined[key] = value
        return combined

    def _refresh_tool_selector(self):
        try:
            recipe_name = self.current_recipe_name()
        except Exception:
            recipe_name = "default"

        try:
            tools = self.recipes.get_published_tools(recipe_name)
        except Exception:
            tools = []

        entries: list[dict[str, Any]] = []
        for tool in tools:
            tool_id = tool.name or f"tool_{tool.order}"
            display_name = tool.name or tool.type or tool_id
            tool_type = tool.type or ""
            entries.append(
                {
                    "id": tool_id,
                    "name": display_name,
                    "type": tool_type,
                    "order": getattr(tool, "order", 0),
                }
            )

        entries.sort(key=lambda item: int(item.get("order", 0)))
        self._tool_selector_items = entries

        self.cmb_tool.blockSignals(True)
        self.cmb_tool.clear()

        if entries:
            self.cmb_tool.addItem("Celý pipeline", None)
            for entry in entries:
                label = entry["name"]
                tool_type = entry.get("type")
                if tool_type and tool_type != label:
                    label = f"{label} ({tool_type})"
                self.cmb_tool.addItem(label, entry)
            self.cmb_tool.setEnabled(True)
            self.lbl_tool_selector.setEnabled(True)
            default_index = 1 if self.cmb_tool.count() > 1 else 0
            self.cmb_tool.setCurrentIndex(default_index)
        else:
            self.cmb_tool.addItem("—", None)
            self.cmb_tool.setCurrentIndex(0)
            self.cmb_tool.setEnabled(False)
            self.lbl_tool_selector.setEnabled(False)

        self.cmb_tool.blockSignals(False)
        self._update_metrics_panel()

    def _on_tool_selection_changed(self):
        self._update_metrics_panel()
        try:
            self.strip.reload()
        except Exception:
            pass

    def _maximize_and_lock(self):
        try:
            # maximalizuj a uzamkni veľkosť (žiadne manuálne resize)
            self.showMaximized()
            self.setFixedSize(self.size())
        except Exception:
            pass

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
            self.strip.reload()
            # update sidebar (nový recept, reset posledných metrík)
            self._update_sidebar(st, [])
            self._refresh_tool_selector()
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
        self._refresh_tool_selector()

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
        self._refresh_tool_selector()

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
        self._refresh_tool_selector()

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

from __future__ import annotations

import json
import math
from collections import Counter
from numbers import Real
from pathlib import Path
from typing import Any, Dict

from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox, QSpinBox,
    QStackedWidget, QFrame, QScrollArea, QCheckBox, QToolButton, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap

from threading import Thread
from app.services.retention_service import RetentionService

from app.ui.xu_panel import XUPanel

from app.services.camera_service import CameraService
from app.services.storage_service import save_golden, save_production_result, load_recipe_config
from app.ui.golden_wizard import GoldenWizard
from app.services.db_service import DbService
from app.services.recipe_service import RecipeService
from app.services.stats_service import StatsService
from app.services.tool_registry import ToolRegistry
from app.services.tool_service import run_pipeline
from app.ui.thresholds_panel import ThresholdsPanel
from app.ui.results_strip import ResultsStrip
from app.models.schema import RecipeV2, Tool


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

        self._runtime_recipe: RecipeV2 | None = None
        self._tool_selector_meta: Dict[str, Tool] = {}
        self._last_tool_data: Dict[str, Dict[str, Any]] = {"__pipeline__": {}}
        self._last_tool_payloads: list[dict[str, Any]] = []

        try:
            self._load_runtime_recipe_config(self.current_recipe_name())
        except Exception as e:
            print("[Run] Failed to load runtime recipe:", e)

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

        run.addLayout(status_row)

        # Akcie (TRIGGER, Export, Wizard) + Live + Heatmap + minimalizácia stripu
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

        actions.addStretch(1)

        self.cmb_tool_filter = QComboBox()
        self.cmb_tool_filter.setToolTip("Vyberte nástroj pre zobrazenie metrík")
        self.cmb_tool_filter.currentIndexChanged.connect(self._on_tool_filter_changed)
        actions.addWidget(self.cmb_tool_filter)

        # Live toggle
        self.btn_live = QPushButton("Live OFF")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._toggle_live)
        actions.addWidget(self.btn_live)

        # Heatmap toggle
        self.chk_heatmap = QCheckBox("Heatmap")
        self.chk_heatmap.setToolTip("Zobraziť farebnú mapu rozdielov voči golden")
        actions.addWidget(self.chk_heatmap)
        run.addLayout(actions)

        self._refresh_tool_selector()

        # Live view + pravý sidebar so štatistikami
        preview_row = QHBoxLayout(); preview_row.setSpacing(12)

        # Live view panel (aktuálny záber)
        self.live_view = QLabel("— aktuálny záber —")
        self.live_view.setAlignment(Qt.AlignCenter)
        self.live_view.setMinimumSize(960, 540)
        self.live_view.setFixedSize(960, 540)
        self.live_view.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.live_view.setStyleSheet("border: 1px solid #444; border-radius: 6px; background:#181818;")
        self.live_view.setContentsMargins(0,0,0,0)
        preview_row.addWidget(self.live_view, 0)

        # Pravý panel (štatistiky + posledné metriky)
        self.side_panel = QWidget(); self.side_panel.setObjectName("sidePanel")
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
        self.sb_tool_label = QLabel("Nástroj: –")
        side.addWidget(self.sb_tool_label)
        self.sb_status_label = QLabel("Status: –")
        self.sb_status_label.setStyleSheet("font-weight: 600; color: #bbb;")
        side.addWidget(self.sb_status_label)
        self.sb_latency_label = QLabel("Latencia: –")
        side.addWidget(self.sb_latency_label)

        self.sb_metrics_container = QWidget(self.side_panel)
        self.sb_metrics_layout = QVBoxLayout(self.sb_metrics_container)
        self.sb_metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.sb_metrics_layout.setSpacing(2)
        placeholder = QLabel("Žiadne metriky.", self.sb_metrics_container)
        placeholder.setStyleSheet("color: #777;")
        self.sb_metrics_layout.addWidget(placeholder)
        self.sb_metrics_layout.addStretch(1)
        self.sb_metrics_placeholder = placeholder
        side.addWidget(self.sb_metrics_container)

        side.addStretch(1)
        preview_row.addWidget(self.side_panel, 1)

        run.addLayout(preview_row)

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
            frame = self.cam.last_frame()  # posledný kontinuálny frame
            self._last_trigger_frame = frame.copy() if frame is not None else None
            meta = {"mode": "manual"}

            nok = False
            metrics: Dict[str, Any] = {}
            pipeline_status: str | None = None
            runtime_recipe = self._runtime_recipe.copy() if isinstance(self._runtime_recipe, RecipeV2) else None
            per_tool_payloads: list[dict[str, Any]] = []
            pipeline_executed = False

            golden = getattr(self.tool, "golden", None)
            if frame is not None and golden is not None and runtime_recipe is not None and runtime_recipe.tools:
                try:
                    runtime_recipe.pose_enabled = getattr(self.tool, "pose_enabled", runtime_recipe.pose_enabled)
                    result = run_pipeline(
                        golden,
                        frame,
                        runtime_recipe,
                        recipe_name=self.current_recipe_name(),
                    )
                    metrics = self._handle_pipeline_result(result)
                    per_tool_payloads = list(self._last_tool_payloads)
                    pipeline_status = getattr(result, "status", None)
                    nok = pipeline_status != "ok"
                    if isinstance(result.cycle_time_ms, (int, float)):
                        meta["cycle_time_ms"] = float(result.cycle_time_ms)
                    if result.policy_applied:
                        meta["policy_applied"] = result.policy_applied
                    pipeline_executed = True
                except Exception as exc:
                    print("[Run] pipeline evaluation failed:", exc)
                    runtime_recipe = None

            if not pipeline_executed:
                try:
                    res = self.tool.evaluate(frame)
                    ok = bool(res.get("ok", False))
                    metrics = self._handle_fallback_metrics(res.get("metrics", {}), ok)
                    nok = not ok
                    pipeline_status = "ok" if ok else "nok"
                except Exception as exc:
                    print("[Tool] evaluate failed:", exc)
                    metrics = self._handle_fallback_metrics({}, False)
                    nok = True
                    pipeline_status = "nok"

            self._apply_main_status(pipeline_status)

            st = self.stats.daily_for_recipe(self.current_recipe_name())
            self._update_sidebar(st)

            meta_payload: Dict[str, Any] = {"metrics": metrics}
            if pipeline_status:
                meta_payload["status"] = pipeline_status
            if per_tool_payloads:
                meta_payload["per_tool"] = per_tool_payloads

            save_production_result(
                frame,
                meta | meta_payload,
                self.current_recipe_name(),
                store_full_nok=True,
                nok=nok,
            )

            # refresh stripu po uložení výsledku
            self.strip.reload()

            # ak nie je live režim, zobraz práve triggernutý frame
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

    def save_golden_clicked(self):
        frame = self.cam.one_shot()
        path = save_golden(frame, self.current_recipe_name())
        self.lbl_status.setText(f"GOLDEN uložený: {path}")

    def open_wizard(self):
        dlg = GoldenWizard(self.cam, self.recipes, self)
        dlg.resize(1200, 800)
        dlg.exec()
        try:
            self._load_runtime_recipe_config(self.current_recipe_name())
        except Exception as exc:
            print("[Run] failed to refresh runtime recipe after wizard:", exc)
        self._update_sidebar()

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

    def _load_runtime_recipe_config(self, name: str) -> None:
        base_dir = getattr(self.recipes, "base", Path("/data"))
        recipe_dir = Path(base_dir) / "recipes" / name

        recipe: RecipeV2
        try:
            published_path = recipe_dir / "recipe.published.json"
            if published_path.exists():
                with open(published_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                recipe = RecipeV2.from_dict(data)
            else:
                recipe = load_recipe_config(name, base_dir=base_dir)
        except Exception as exc:
            print(f"[Run] runtime recipe fallback for {name}: {exc}")
            recipe = RecipeV2()

        tools = sorted(getattr(recipe, "tools", []) or [], key=lambda t: t.order)
        self._runtime_recipe = recipe
        self._tool_selector_meta = {self._tool_key(tool): tool for tool in tools}
        self._last_tool_data = {"__pipeline__": {}}
        self._last_tool_payloads = []
        self._refresh_tool_selector()

    @staticmethod
    def _tool_key(tool: Tool) -> str:
        name = str(getattr(tool, "name", "") or "").strip()
        if name:
            return name
        return f"tool_{int(getattr(tool, "order", 0))}"

    @staticmethod
    def _format_tool_label(name: str | None, tool_type: str | None, fallback: str) -> str:
        name = (name or "").strip()
        tool_type = (tool_type or "").strip()
        if name and tool_type:
            return f"{name} ({tool_type})"
        if name:
            return name
        if tool_type:
            return tool_type
        return fallback

    def _refresh_tool_selector(self) -> None:
        combo = getattr(self, "cmb_tool_filter", None)
        if combo is None:
            return

        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Celý recept", "__pipeline__")

        items = sorted(self._tool_selector_meta.items(), key=lambda item: item[1].order)
        for key, tool in items:
            label = self._format_tool_label(tool.name, tool.type, key)
            combo.addItem(label, key)

        if current is not None:
            idx = combo.findData(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self._render_selected_metrics()

    def _current_tool_selection(self) -> str:
        combo = getattr(self, "cmb_tool_filter", None)
        if combo is None:
            return "__pipeline__"
        data = combo.currentData()
        return data if isinstance(data, str) and data else "__pipeline__"

    def _on_tool_filter_changed(self) -> None:
        self._render_selected_metrics()

    def _render_selected_metrics(self) -> None:
        if not hasattr(self, "sb_tool_label"):
            return
        key = self._current_tool_selection()
        data = self._last_tool_data.get(key) or {}

        if key == "__pipeline__":
            label = "Celý recept"
        else:
            tool = self._tool_selector_meta.get(key)
            if tool is not None:
                label = self._format_tool_label(tool.name, tool.type, key)
            else:
                label = self._format_tool_label(data.get("tool_name"), data.get("tool_type"), key)

        self.sb_tool_label.setText(f"Nástroj: {label}")

        status_key = str(data.get("status") or "").lower()
        status_map = {
            "ok": ("OK", "#33dd66"),
            "warn": ("WARN", "#e6a23c"),
            "nok": ("NOK", "#ff3366"),
        }
        status_text, color = status_map.get(status_key, ("—", "#bbbbbb"))
        self.sb_status_label.setText(f"Status: {status_text}")
        self.sb_status_label.setStyleSheet(f"font-weight: 600; color: {color};")

        if key == "__pipeline__":
            cycle = data.get("cycle_time_ms")
            if isinstance(cycle, (int, float)):
                self.sb_latency_label.setText(f"Cyklus: {cycle:.1f} ms")
            else:
                self.sb_latency_label.setText("Cyklus: –")
        else:
            latency = data.get("latency_ms")
            if isinstance(latency, (int, float)):
                self.sb_latency_label.setText(f"Latencia: {latency:.1f} ms")
            else:
                self.sb_latency_label.setText("Latencia: –")

        rows = data.get("metrics_rows") if data else None
        if rows:
            self._populate_metrics_layout(rows)
        else:
            message = "Žiadne metriky pre zvolený nástroj." if key != "__pipeline__" else "Žiadne metriky."
            self._populate_metrics_layout([], message)

    def _populate_metrics_layout(self, rows: list[tuple[str, str]], empty_message: str | None = None) -> None:
        if not hasattr(self, "sb_metrics_layout"):
            return
        while self.sb_metrics_layout.count():
            item = self.sb_metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not rows:
            message = empty_message or "Žiadne metriky."
            placeholder = QLabel(message, self.sb_metrics_container)
            placeholder.setStyleSheet("color: #777;")
            self.sb_metrics_layout.addWidget(placeholder)
            self.sb_metrics_layout.addStretch(1)
            self.sb_metrics_placeholder = placeholder
            return
        for name, value in rows:
            label = QLabel(f"{name}: {value}", self.sb_metrics_container)
            self.sb_metrics_layout.addWidget(label)
        self.sb_metrics_layout.addStretch(1)
        self.sb_metrics_placeholder = None

    @staticmethod
    def _format_metric_value(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, Real) and not isinstance(value, bool):
            val = float(value)
            if not math.isfinite(val):
                return str(val)
            if abs(val) >= 1000 or (0 < abs(val) < 0.001):
                return f"{val:.3g}"
            text = f"{val:.4f}".rstrip("0").rstrip(".")
            return text or "0"
        return str(value)

    def _format_metric_rows(
        self,
        tool_type: str | None,
        metrics: Dict[str, Any],
        diagnostics: Dict[str, Any] | None,
    ) -> list[tuple[str, str]]:
        values = dict(metrics or {})
        diag = dict(diagnostics or {})
        for key in ("corr", "dx", "dy", "blob_count", "total_area", "ssim", "found", "threshold_corr"):
            if key in diag and key not in values:
                values[key] = diag[key]

        rows: list[tuple[str, str]] = []
        definition = ToolRegistry.get_tool_definition(tool_type) if tool_type else None
        if definition is not None:
            spec = getattr(definition, "metrics_spec", ()) or ()
            sorted_spec = sorted(
                spec,
                key=lambda entry: (
                    -int(getattr(entry, "priority", 0) or 0),
                    str(getattr(entry, "key", "")),
                ),
            )
            for entry in sorted_spec:
                key = getattr(entry, "key", "")
                if not key or key not in values:
                    continue
                rows.append((key, self._format_metric_value(values.pop(key))))

        for key in sorted(values.keys()):
            rows.append((key, self._format_metric_value(values[key])))
        return rows

    def _coerce_scalar(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return int(value)
        if isinstance(value, float):
            return float(value)
        if isinstance(value, Real):
            return float(value)
        try:
            import numpy as np

            if isinstance(value, np.generic):
                return value.item()
        except Exception:
            pass
        return None

    def _sanitize_metrics(self, metrics: Dict[str, Any] | None) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        if not isinstance(metrics, dict):
            return sanitized
        for key, value in metrics.items():
            scalar = self._coerce_scalar(value)
            if scalar is not None:
                sanitized[str(key)] = scalar
        return sanitized

    def _serialize_tool_report(self, report) -> dict[str, Any]:
        tool_name = getattr(report.tool, "name", None)
        tool_type = getattr(report.tool, "type", None)
        payload: dict[str, Any] = {
            "tool_id": report.tool_id,
            "name": tool_name or report.tool_id,
            "type": tool_type,
            "status": report.status,
            "latency_ms": float(report.latency_ms) if report.latency_ms is not None else None,
        }
        metrics = self._sanitize_metrics(getattr(report, "metrics", {}))
        if metrics:
            payload["metrics"] = metrics
        diagnostics = self._sanitize_metrics(getattr(report, "diagnostics", {}))
        if diagnostics:
            payload["diagnostics"] = diagnostics
        return payload

    def _build_pipeline_overview_entry(self, result) -> Dict[str, Any]:
        per_tool = list(getattr(result, "per_tool", []) or [])
        counts = Counter(str(getattr(report, "status", "")).lower() for report in per_tool)
        rows: list[tuple[str, str]] = [("Nástrojov", str(len(per_tool)))]
        if counts.get("ok"):
            rows.append(("OK nástrojov", str(counts["ok"])))
        if counts.get("warn"):
            rows.append(("WARN nástrojov", str(counts["warn"])))
        if counts.get("nok"):
            rows.append(("NOK nástrojov", str(counts["nok"])))
        policy = getattr(result, "policy_applied", None)
        if policy:
            rows.append(("Politika", str(policy)))
        cycle_time = getattr(result, "cycle_time_ms", None)
        raw_metrics: Dict[str, Any] = {}
        if isinstance(cycle_time, (int, float)):
            raw_metrics["cycle_time_ms"] = float(cycle_time)
        return {
            "title": "Celý recept",
            "status": getattr(result, "status", None),
            "cycle_time_ms": float(cycle_time) if isinstance(cycle_time, (int, float)) else None,
            "latency_ms": None,
            "metrics_rows": rows,
            "tool_type": None,
            "tool_name": "Celý recept",
            "raw_metrics": raw_metrics,
        }

    def _collect_primary_metrics(self, per_tool_payloads: list[dict[str, Any]]) -> Dict[str, Any]:
        collected: Dict[str, Any] = {}
        for payload in per_tool_payloads:
            metrics = payload.get("metrics") or {}
            for key in ("ssim", "blob_count", "total_area"):
                value = metrics.get(key)
                if key not in collected and value is not None:
                    collected[key] = value
        return collected

    def _handle_pipeline_result(self, result) -> Dict[str, Any]:
        per_tool_payloads: list[dict[str, Any]] = []
        tool_entries: Dict[str, Dict[str, Any]] = {}

        for report in getattr(result, "per_tool", []) or []:
            payload = self._serialize_tool_report(report)
            per_tool_payloads.append(payload)
            metrics = payload.get("metrics", {})
            diagnostics = payload.get("diagnostics")
            rows = self._format_metric_rows(payload.get("type"), metrics, diagnostics)
            tool_entries[report.tool_id] = {
                "title": self._format_tool_label(payload.get("name"), payload.get("type"), report.tool_id),
                "status": payload.get("status"),
                "latency_ms": payload.get("latency_ms"),
                "cycle_time_ms": None,
                "metrics_rows": rows,
                "tool_type": payload.get("type"),
                "tool_name": payload.get("name"),
                "raw_metrics": metrics,
            }

        pipeline_entry = self._build_pipeline_overview_entry(result)
        tool_entries["__pipeline__"] = pipeline_entry
        self._last_tool_data = tool_entries
        self._last_tool_payloads = per_tool_payloads
        aggregated = self._collect_primary_metrics(per_tool_payloads)
        self._render_selected_metrics()
        return aggregated

    def _handle_fallback_metrics(self, metrics: dict[str, Any], ok: bool) -> Dict[str, Any]:
        sanitized = self._sanitize_metrics(metrics)
        status = "ok" if ok else "nok"
        rows = [(key, self._format_metric_value(value)) for key, value in sorted(sanitized.items())]
        self._last_tool_data = {
            "__pipeline__": {
                "title": "Celý recept",
                "status": status,
                "cycle_time_ms": None,
                "latency_ms": None,
                "metrics_rows": rows,
                "tool_type": None,
                "tool_name": "Celý recept",
                "raw_metrics": sanitized,
            }
        }
        self._last_tool_payloads = []
        self._render_selected_metrics()
        return sanitized

    def _apply_main_status(self, status: str | None) -> None:
        status_key = str(status or "").lower()
        mapping = {
            "ok": ("OK", "#33dd66"),
            "warn": ("WARN", "#e6a23c"),
            "nok": ("NOK", "#ff3366"),
        }
        text, color = mapping.get(status_key, ("–", "#bbbbbb"))
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"color: {color};")

    def _update_sidebar(self, st: dict | None = None):
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
        except Exception:
            pass
        finally:
            self._render_selected_metrics()

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
            self._load_runtime_recipe_config(name)
            self.lbl_status.setText("Recipe loaded.")
            # refresh štatistík + strip
            st = self.stats.daily_for_recipe(name)
            self.strip.reload()
            # update sidebar (nový recept, reset posledných metrík)
            self._update_sidebar(st)
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

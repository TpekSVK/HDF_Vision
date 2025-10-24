from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox,
    QStackedWidget, QFrame, QScrollArea, QCheckBox, QToolButton, QSizePolicy, QGridLayout
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap

import math
import os
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from numbers import Integral, Real
from pathlib import Path
from typing import Any

import numpy as np

from threading import Thread
from app.services.retention_service import RetentionService

from app.ui.xu_panel import XUPanel

from app.services.camera_service import CameraService
from app.services.storage_service import save_production_result, load_recipe_config
from app.ui.golden_wizard import GoldenWizard
from app.ui.gpio_wizard import GPIOWizard
from app.services.db_service import DbService
from app.services.recipe_service import RecipeService
from app.services.stats_service import StatsService
from app.ui.results_strip import ResultsStrip
from app.ui.view_strip import ViewStrip
from app.models.schema import DEFAULT_VIEW_ID, RecipeAggregation, RecipeView, RecipeV2
from app.services.tool_service import PipelineOrchestrator, run_pipeline
from app.services.tool_registry import ToolRegistry
from app.services.gpio_service import GPIOService


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

        self.gpio = GPIOService()
        self.gpio.register_trigger_callback(self._handle_gpio_trigger)

        self._last_tool_reports: list[dict[str, Any]] = []
        self._last_cycle_time_ms: float | None = None
        self._last_pipeline_status: str | None = None
        self._tool_selector_items: list[dict[str, Any]] = []
        self._view_reports: dict[str, list[dict[str, Any]]] = {}
        self._view_statuses: dict[str, str] = {}
        self._view_cycle_times: dict[str, float | None] = {}
        self._view_frames: dict[str, np.ndarray] = {}
        self._view_metrics: dict[str, dict[str, Any]] = {}
        self._active_view_id: str | None = None
        self._last_run_serial: str | None = None
        env_fail_fast = os.getenv("HDF_FAIL_FAST", "0").strip().lower()
        self.fail_fast_enabled = env_fail_fast in {"1", "true", "yes", "on"}

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
        self.gpio.set_active_recipe(self.current_recipe_name())

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
        run_root = QVBoxLayout(self.panel_run); run_root.setSpacing(8)

        run_container = QWidget()
        run_container.setObjectName("runContainer")
        run_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        run_container.setMaximumHeight(780)

        run = QVBoxLayout(run_container); run.setSpacing(8)
        run_root.addWidget(run_container, 0, Qt.AlignTop)
        run_root.addStretch(1)

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

        # View strip (multi-view selector)
        self.view_strip = ViewStrip(self)
        self.view_strip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.view_strip.view_selected.connect(self._on_view_selected)
        run.addWidget(self.view_strip)

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
        self._sync_views_with_recipe()

        # maximalizovať a uzamknúť veľkosť okna po zobrazení
        QTimer.singleShot(0, self._maximize_and_lock)

        # ---------- SETUP panel ----------
        self.panel_setup = QWidget(); self.stack.addWidget(self.panel_setup)
        s = QVBoxLayout(self.panel_setup); s.setSpacing(8)

        row1 = QHBoxLayout();
        self.btn_wizard = QPushButton("🔧 Golden Wizard", self)
        self.btn_wizard.clicked.connect(self.open_wizard)
        row1.addWidget(self.btn_wizard)

        self.btn_gpio_wizard = QPushButton("🧰 GPIO Wizard", self)
        self.btn_gpio_wizard.clicked.connect(self.open_gpio_wizard)
        row1.addWidget(self.btn_gpio_wizard)

        row1.addStretch(1)
        s.addLayout(row1)

        cam_title = QLabel("Nastavenia kamery"); tf2 = QFont(); tf2.setPointSize(12); tf2.setBold(True); cam_title.setFont(tf2)
        s.addWidget(cam_title)

        # Rozlíšenie
        res_line = QHBoxLayout()
        res_line.addWidget(QLabel("Rozlíšenie:"))
        self._resolution_presets: list[tuple[str, dict[str, Any]]] = [
            ("1920x1080@60 Y8", {"width": 1920, "height": 1080, "fps": 60, "pixel_format": "Y8"}),
            ("1280x720@60 Y8",  {"width": 1280, "height": 720,  "fps": 60, "pixel_format": "Y8"}),
            ("2592x1944@30 Y8 (len setup/pomalé)", {"width": 2592, "height": 1944, "fps": 30, "pixel_format": "Y8"}),
        ]
        self.cmb_res = QComboBox()
        for label, data in self._resolution_presets:
            self.cmb_res.addItem(label, data)
        self._sync_resolution_combo()
        self.cmb_res.currentIndexChanged.connect(self._on_resolution_changed)
        res_line.addWidget(self.cmb_res)
        s.addLayout(res_line)

        # XU panel
        self.xu = XUPanel(self)
        s.addWidget(self.xu)

        # default RUN zobrazenie
        self.stack.setCurrentWidget(self.panel_run)

        # Spusť retenciu na pozadí (jednorazovo pri štarte)
        Thread(target=lambda: RetentionService().run_once(verbose=False), daemon=True).start()

    # ---------- Helpers ----------
    def current_recipe_name(self) -> str:
        return getattr(self.tool, "recipe", "default") or "default"

    def _load_recipe_configuration(self, name: str | None = None) -> RecipeV2 | None:
        recipe_name = name or self.current_recipe_name()
        try:
            recipe = load_recipe_config(recipe_name)
        except Exception:
            return None
        if isinstance(recipe, RecipeV2):
            return recipe
        return None

    def _sync_views_with_recipe(self, recipe: RecipeV2 | None = None) -> None:
        if recipe is None:
            recipe = self._load_recipe_configuration()
        views: list[RecipeView] = []
        if recipe is not None and getattr(recipe, "views", None):
            views = [view for view in recipe.views if isinstance(view, RecipeView)]
        if not views:
            views = [RecipeView(id=DEFAULT_VIEW_ID, name="View", golden_path="golden.png")]

        view_entries = [(view.id, view.name or view.id) for view in views]

        self._filter_view_state({vid for vid, _ in view_entries})
        self.view_strip.set_views(view_entries)

        if self._active_view_id and any(vid == self._active_view_id for vid, _ in view_entries):
            self.view_strip.set_active_view(self._active_view_id)
        else:
            self._active_view_id = view_entries[0][0] if view_entries else None
            if self._active_view_id:
                self.view_strip.set_active_view(self._active_view_id)

        self._refresh_tool_selector()
        if self._active_view_id:
            self._apply_view_selection(self._active_view_id)

    def _filter_view_state(self, valid_ids: set[str]) -> None:
        self._view_reports = {k: v for k, v in self._view_reports.items() if k in valid_ids}
        self._view_statuses = {k: v for k, v in self._view_statuses.items() if k in valid_ids}
        self._view_cycle_times = {
            k: v for k, v in self._view_cycle_times.items() if k in valid_ids
        }
        self._view_frames = {k: v for k, v in self._view_frames.items() if k in valid_ids}
        self._view_metrics = {k: v for k, v in self._view_metrics.items() if k in valid_ids}

    def _on_view_selected(self, view_id: str) -> None:
        if not view_id:
            return
        self._active_view_id = view_id
        self.view_strip.set_active_view(view_id)
        self._refresh_tool_selector()
        self._apply_view_selection(view_id)

    def _apply_view_selection(self, view_id: str | None) -> None:
        if view_id is None:
            return
        reports = self._view_reports.get(view_id, [])
        self._last_tool_reports = [dict(entry) for entry in reports]
        self._last_pipeline_status = self._view_statuses.get(view_id)
        self._last_cycle_time_ms = self._view_cycle_times.get(view_id)
        self._update_metrics_panel()
        if not self.live_enabled:
            frame = self._view_frames.get(view_id)
            if isinstance(frame, np.ndarray):
                img = frame
                if self.chk_heatmap.isChecked():
                    try:
                        img = self._make_heatmap_overlay(img)
                    except Exception:
                        pass
                self._show_gray_or_bgr(self.live_view, img)
        try:
            self.strip.set_active_view(view_id)
            self.strip.reload()
        except Exception:
            pass

    def _primary_view_id(self, recipe: RecipeV2 | None = None) -> str:
        if recipe is None:
            recipe = self._load_recipe_configuration()
        if recipe and getattr(recipe, "views", None):
            for view in recipe.views:
                if isinstance(view, RecipeView):
                    return view.id
        return DEFAULT_VIEW_ID

    def _generate_run_serial(self, recipe_name: str) -> str:
        safe = str(recipe_name or "run").strip().replace("/", "_").replace(" ", "_")
        timestamp = datetime.now().strftime("%H%M%S")
        unique = uuid.uuid4().hex[:8]
        return f"{safe}_{timestamp}_{unique}"

    def _load_view_golden(self, recipe_name: str, view: RecipeView) -> np.ndarray | None:
        golden_name = (view.golden_path or "golden.png").strip() or "golden.png"
        path = Path("/data") / "recipes" / recipe_name / golden_name
        if not path.exists():
            return None
        try:
            import imageio.v3 as iio

            golden = iio.imread(path)
        except Exception:
            return None
        arr = np.asarray(golden)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8, copy=False)
        return arr

    def _prepare_frame(self, frame: np.ndarray | None) -> np.ndarray | None:
        if frame is None:
            return None
        arr = np.asarray(frame)
        if arr.ndim == 3 and arr.shape[2] > 1:
            arr = arr[:, :, 0]
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8, copy=False)
        return arr.copy()

    def _capture_frame_for_view(self, *, reuse_last: bool = False) -> np.ndarray | None:
        frame = None
        try:
            if reuse_last:
                frame = self.cam.last_frame()
            else:
                frame = self.cam.one_shot()
        except Exception:
            frame = None
        return self._prepare_frame(frame)

    def _apply_camera_profile(self, view: RecipeView) -> None:
        profile = str(getattr(view, "camera_profile", "") or "").strip()
        if not profile:
            return
        if hasattr(self.cam, "apply_profile"):
            try:
                self.cam.apply_profile(profile)
            except Exception:
                pass

    def _make_view_recipe(self, recipe: RecipeV2, view: RecipeView) -> RecipeV2:
        primary_view_id = self._primary_view_id(recipe)
        view_copy = RecipeView(
            id=view.id,
            name=view.name,
            golden_path=view.golden_path,
            camera_profile=view.camera_profile,
            settle_ms=view.settle_ms,
        )
        view_tools = [
            tool.copy()
            for tool in getattr(recipe, "tools", [])
            if (getattr(tool, "view_id", "") or primary_view_id) == view.id
        ]
        return RecipeV2(
            pose_enabled=recipe.pose_enabled,
            regions=[dict(r) for r in getattr(recipe, "regions", [])],
            tools=view_tools,
            on_locator_failure=recipe.on_locator_failure,
            export_artifacts=recipe.export_artifacts,
            views=[view_copy],
            aggregation=RecipeAggregation(mode="AND"),
        )

    def _update_overall_status(self, status: str | None) -> None:
        normalized = (status or "ok").lower()
        text = normalized.upper()
        color_map = {"ok": "#33dd66", "warn": "#e67e22", "nok": "#ff3366"}
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"color: {color_map.get(normalized, '#33dd66')};")

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

    def _match_resolution_index(self, width: int, height: int, fps: int, pixel_format: str | None) -> int | None:
        pix_fmt = (pixel_format or "Y8").upper()
        for idx, (_, data) in enumerate(self._resolution_presets):
            if (
                int(data.get("width", 0)) == int(width)
                and int(data.get("height", 0)) == int(height)
                and int(data.get("fps", 0)) == int(fps)
                and (data.get("pixel_format", "Y8") or "Y8").upper() == pix_fmt
            ):
                return idx
        return None

    def _sync_resolution_combo(self):
        pix_fmt = getattr(self.cam, "pixel_format", "Y8")
        idx = self._match_resolution_index(self.cam.width, self.cam.height, self.cam.fps, pix_fmt)
        if idx is None:
            idx = 0
        self.cmb_res.blockSignals(True)
        self.cmb_res.setCurrentIndex(idx)
        self.cmb_res.blockSignals(False)

    def _on_resolution_changed(self, index: int):
        data = self.cmb_res.itemData(index)
        if not isinstance(data, dict):
            return
        target = (
            int(data.get("width", 0)),
            int(data.get("height", 0)),
            int(data.get("fps", 0)),
            (data.get("pixel_format", "Y8") or "Y8").upper(),
        )
        current = (
            int(getattr(self.cam, "width", 0)),
            int(getattr(self.cam, "height", 0)),
            int(getattr(self.cam, "fps", 0)),
            (getattr(self.cam, "pixel_format", "Y8") or "Y8").upper(),
        )
        if target == current:
            return
        was_live = self.live_enabled and self._run_timer.isActive()
        if was_live:
            self._run_timer.stop()
        success = False
        try:
            self.cam.apply_resolution(**data)
        except Exception as exc:
            self.lbl_status.setText(f"Zmena rozlíšenia zlyhala: {exc}")
            self._sync_resolution_combo()
        else:
            success = True
            label = self.cmb_res.itemText(index)
            self.lbl_status.setText(f"Rozlíšenie nastavené: {label}")
        finally:
            if was_live:
                self._run_timer.start()
        if success:
            self._update_live_view()

    def manual_trigger(self):
        try:
            try:
                recipe_name = self.current_recipe_name()
            except Exception:
                recipe_name = "default"

            try:
                recipe_cfg = self._load_recipe_configuration(recipe_name)
            except Exception:
                recipe_cfg = None

            try:
                self.gpio.emit_heartbeat()
            except Exception:
                pass

            if recipe_cfg is None or not getattr(recipe_cfg, "tools", []):
                frame = self._prepare_frame(self.cam.last_frame())
                if frame is None:
                    self.lbl_status.setText("Žiadny snímok z kamery.")
                    return
                self._run_legacy_trigger(frame, recipe_name)
                return

            views = [view for view in getattr(recipe_cfg, "views", []) if isinstance(view, RecipeView)]
            if not views:
                views = [RecipeView(id=DEFAULT_VIEW_ID, name="View", golden_path="golden.png")]

            self._sync_views_with_recipe(recipe_cfg)

            run_serial = self._generate_run_serial(recipe_name)
            self._last_run_serial = run_serial

            if len(views) == 1:
                frame = self._prepare_frame(self.cam.last_frame())
                if frame is None:
                    self.lbl_status.setText("Žiadny snímok z kamery.")
                    return
                self._run_single_view_trigger(
                    recipe_cfg,
                    views[0],
                    frame,
                    recipe_name,
                    run_serial,
                )
                return

            self._run_multi_view_trigger(recipe_cfg, views, recipe_name, run_serial)

            if self._active_view_id:
                self._apply_view_selection(self._active_view_id)

            if not self.live_enabled and self._active_view_id:
                frame = self._view_frames.get(self._active_view_id)
                if isinstance(frame, np.ndarray):
                    img = frame
                    if self.chk_heatmap.isChecked():
                        try:
                            img = self._make_heatmap_overlay(img)
                        except Exception:
                            pass
                    self._show_gray_or_bgr(self.live_view, img)
        except Exception:
            self.gpio.signal_result("nok")
            import traceback

            traceback.print_exc()

    def _run_single_view_trigger(
        self,
        recipe_cfg: RecipeV2,
        view: RecipeView,
        frame_u8: np.ndarray,
        recipe_name: str,
        run_serial: str,
    ) -> None:
        golden = self._load_view_golden(recipe_name, view)
        if golden is None:
            golden = getattr(self.tool, "golden", None)
        if golden is None:
            self._run_legacy_trigger(frame_u8, recipe_name)
            return

        sub_recipe = self._make_view_recipe(recipe_cfg, view)
        if not getattr(sub_recipe, "regions", None):
            sub_recipe.regions = list(getattr(self.tool, "regions", []) or [])
        sub_recipe.pose_enabled = bool(getattr(self.tool, "pose_enabled", True))

        result = run_pipeline(
            golden,
            frame_u8,
            sub_recipe,
            recipe_name=recipe_name,
            notes=f"manual_trigger:view={view.id}",
        )

        status = (result.status or "ok").lower()
        self._update_overall_status(status)
        self.gpio.signal_result(status)

        context_frame = getattr(result.context, "frame_aligned", None)
        if context_frame is None:
            context_frame = getattr(result.context, "frame", None)
        if isinstance(context_frame, np.ndarray):
            self._last_trigger_frame = context_frame.copy()
        else:
            self._last_trigger_frame = frame_u8.copy()

        reports = [self._serialize_tool_report(report) for report in result.per_tool]
        st = self.stats.daily_for_recipe(recipe_name)
        self._update_sidebar(
            st,
            reports,
            status=status,
            cycle_time_ms=float(result.cycle_time_ms),
            view_id=view.id,
        )

        diagnostics_payload: list[Any] = []
        for diag in getattr(result, "diagnostics", []) or []:
            diagnostics_payload.append(self._simplify_value(diag))

        combined_metrics = self._merge_pipeline_metrics(reports)
        self._view_reports[view.id] = [dict(entry) for entry in reports]
        self._view_statuses[view.id] = status
        self._view_cycle_times[view.id] = float(result.cycle_time_ms)
        self._view_frames[view.id] = frame_u8.copy()
        self._view_metrics[view.id] = combined_metrics

        meta_payload = {
            "mode": "manual",
            "status": status,
            "cycle_time_ms": float(result.cycle_time_ms),
            "per_tool": reports,
            "diagnostics": diagnostics_payload,
            "metrics": combined_metrics,
            "view_id": view.id,
            "view_name": view.name,
            "run_serial": run_serial,
        }
        if getattr(result, "policy_applied", None):
            meta_payload["policy_applied"] = result.policy_applied

        save_production_result(
            frame_u8,
            meta_payload,
            recipe_name,
            store_full_nok=True,
            nok=status != "ok",
            run_id=run_serial,
            view_id=view.id,
        )

        self.view_strip.update_snapshot(view.id, frame_u8)
        self.view_strip.update_status(view.id, status)

        self._active_view_id = view.id
        self.strip.set_active_view(view.id)
        try:
            self.strip.reload()
        except Exception:
            pass

    def _run_multi_view_trigger(
        self,
        recipe_cfg: RecipeV2,
        views: Sequence[RecipeView],
        recipe_name: str,
        run_serial: str,
    ) -> None:
        orchestrator = PipelineOrchestrator()
        per_view_status: dict[str, str] = {}

        for index, view in enumerate(views):
            self._apply_camera_profile(view)
            frame = self._capture_frame_for_view(reuse_last=index == 0)
            if frame is None:
                status = "nok"
                per_view_status[view.id] = status
                self.view_strip.update_status(view.id, status)
                self._view_reports[view.id] = []
                self._view_cycle_times[view.id] = None
                self._view_metrics[view.id] = {}
                continue

            golden = self._load_view_golden(recipe_name, view)
            if golden is None:
                status = "nok"
                per_view_status[view.id] = status
                self._view_frames[view.id] = frame.copy()
                self.view_strip.update_snapshot(view.id, frame)
                self.view_strip.update_status(view.id, status)
                self._view_reports[view.id] = []
                self._view_cycle_times[view.id] = None
                self._view_metrics[view.id] = {}
                continue

            sub_recipe = self._make_view_recipe(recipe_cfg, view)
            if not getattr(sub_recipe, "regions", None):
                sub_recipe.regions = list(getattr(self.tool, "regions", []) or [])
            sub_recipe.pose_enabled = bool(getattr(self.tool, "pose_enabled", True))

            result = run_pipeline(
                golden,
                frame,
                sub_recipe,
                recipe_name=recipe_name,
                notes=f"manual_trigger:view={view.id}",
            )

            status = (result.status or "ok").lower()
            per_view_status[view.id] = status

            reports = [self._serialize_tool_report(report) for report in result.per_tool]
            diagnostics_payload: list[Any] = []
            for diag in getattr(result, "diagnostics", []) or []:
                diagnostics_payload.append(self._simplify_value(diag))
            combined_metrics = self._merge_pipeline_metrics(reports)

            self._view_reports[view.id] = [dict(entry) for entry in reports]
            self._view_statuses[view.id] = status
            self._view_cycle_times[view.id] = float(result.cycle_time_ms)
            self._view_frames[view.id] = frame.copy()
            self._view_metrics[view.id] = combined_metrics
            self._last_trigger_frame = frame.copy()

            st = self.stats.daily_for_recipe(recipe_name)
            self._update_sidebar(
                st,
                reports,
                status=status,
                cycle_time_ms=float(result.cycle_time_ms),
                view_id=view.id,
            )

            meta_payload = {
                "mode": "manual",
                "status": status,
                "cycle_time_ms": float(result.cycle_time_ms),
                "per_tool": reports,
                "diagnostics": diagnostics_payload,
                "metrics": combined_metrics,
                "view_id": view.id,
                "view_name": view.name,
                "run_serial": run_serial,
            }
            if getattr(result, "policy_applied", None):
                meta_payload["policy_applied"] = result.policy_applied

            save_production_result(
                frame,
                meta_payload,
                recipe_name,
                store_full_nok=True,
                nok=status != "ok",
                run_id=run_serial,
                view_id=view.id,
            )

            self.view_strip.update_snapshot(view.id, frame)
            self.view_strip.update_status(view.id, status)

            if self.fail_fast_enabled and status == "nok":
                break

        normalized_statuses: dict[str, str] = {}
        for view in views:
            normalized_statuses[view.id] = per_view_status.get(view.id, "nok")

        overall_status = orchestrator._combine_view_statuses(recipe_cfg, normalized_statuses)
        self._update_overall_status(overall_status)
        self.gpio.signal_result(overall_status)
        self._view_statuses.update(normalized_statuses)
        for view in views:
            view_status = normalized_statuses.get(view.id, "nok")
            self.view_strip.update_status(view.id, view_status)
            self._view_reports.setdefault(view.id, [])
            self._view_cycle_times.setdefault(view.id, None)
            self._view_metrics.setdefault(view.id, {})
        self._active_view_id = self._active_view_id or views[0].id
        try:
            self.strip.reload()
        except Exception:
            pass

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

        self._update_overall_status(status)
        self.gpio.signal_result(status)

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
        view_id = self._active_view_id or DEFAULT_VIEW_ID
        self._update_sidebar(st, legacy_report, status=status, view_id=view_id)
        self._view_reports[view_id] = [dict(entry) for entry in legacy_report]
        self._view_statuses[view_id] = status
        self._view_cycle_times[view_id] = None
        self._view_frames[view_id] = frame_u8.copy() if isinstance(frame_u8, np.ndarray) else frame_u8
        self._view_metrics[view_id] = metrics

        run_serial = self._generate_run_serial(recipe_name)
        self._last_run_serial = run_serial
        meta_payload = {
            "mode": "manual",
            "status": status,
            "metrics": metrics,
            "per_tool": legacy_report,
            "view_id": view_id,
            "run_serial": run_serial,
        }

        save_production_result(
            frame_u8,
            meta_payload,
            recipe_name,
            store_full_nok=True,
            nok=status != "ok",
            run_id=run_serial,
            view_id=view_id,
        )

        self.view_strip.update_snapshot(view_id, frame_u8)
        self.view_strip.update_status(view_id, status)
        self._active_view_id = view_id
        self.strip.set_active_view(view_id)
        try:
            self.strip.reload()
        except Exception:
            pass

        if not self.live_enabled and self._last_trigger_frame is not None:
            img = self._last_trigger_frame
            if self.chk_heatmap.isChecked():
                try:
                    img = self._make_heatmap_overlay(img)
                except Exception:
                    pass
            self._show_gray_or_bgr(self.live_view, img)

        self._apply_view_selection(view_id)

    def open_wizard(self):
        dlg = GoldenWizard(self.cam, self.recipes, self)
        dlg.resize(1200, 800)
        dlg.exec()
        self._update_sidebar()
        self._refresh_tool_selector()

    def open_gpio_wizard(self):
        self.gpio.set_active_recipe(self.current_recipe_name())
        dlg = GPIOWizard(self.gpio, self)
        dlg.resize(720, 520)
        dlg.exec()

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

    def _handle_gpio_trigger(self):
        QTimer.singleShot(0, self.manual_trigger)

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
        view_id: str | None = None,
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

            if view_id is not None and per_tool is not None:
                self._view_reports[view_id] = [dict(entry) for entry in per_tool]
            if view_id is not None and status is not None:
                self._view_statuses[view_id] = status
            if view_id is not None and cycle_time_ms is not None:
                self._view_cycle_times[view_id] = cycle_time_ms

            should_update_active = (
                view_id is None
                or self._active_view_id is None
                or view_id == self._active_view_id
            )

            if per_tool is not None and should_update_active:
                self._last_tool_reports = [dict(entry) for entry in per_tool]
            if status is not None and should_update_active:
                self._last_pipeline_status = status
            if cycle_time_ms is not None and should_update_active:
                self._last_cycle_time_ms = cycle_time_ms

            if should_update_active:
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
        if isinstance(value, Integral):
            return str(int(value))
        if isinstance(value, Real):
            val = float(value)
            if not math.isfinite(val):
                return "—"
            if abs(val - round(val)) < 1e-6:
                return str(int(round(val)))
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

        recipe_cfg = self._load_recipe_configuration(recipe_name)

        try:
            tools = self.recipes.get_published_tools(recipe_name)
        except Exception:
            tools = []

        primary_view_id = self._primary_view_id(recipe_cfg)
        active_view_id = self._active_view_id or primary_view_id

        entries: list[dict[str, Any]] = []
        for tool in tools:
            tool_view_id = getattr(tool, "view_id", "") or primary_view_id
            if active_view_id and tool_view_id != active_view_id:
                continue
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
            self.gpio.close()
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
            self.gpio.set_active_recipe(name)
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
        self.gpio.set_active_recipe(name)

    def on_recipe_rename(self):
        from PySide6.QtWidgets import QInputDialog
        old = self.current_recipe_name()
        new, ok = QInputDialog.getText(self, "Premenovať recept", f"Nový názov pre '{old}':")
        if not ok or not new.strip():
            return
        new = new.strip()
        self.recipes.rename(old, new)
        self.gpio.rename_profile(old, new)
        self._refresh_recipe_list()
        self.recipes.load(new)
        self.tool = self.recipes.tool
        self._refresh_tool_selector()
        self.gpio.set_active_recipe(new)

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
        self.gpio.delete_profile(name)
        self._refresh_recipe_list()
        self.recipes.load("default")
        self.tool = self.recipes.tool
        self._refresh_tool_selector()
        self.gpio.set_active_recipe("default")

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

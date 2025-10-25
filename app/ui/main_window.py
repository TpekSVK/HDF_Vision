from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox,
    QStackedWidget, QFrame, QCheckBox, QSizePolicy, QGridLayout
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap, QImageReader

import json
import math
from pathlib import Path
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from numbers import Integral, Real
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
from app.ui.view_strip import ViewStrip
from app.services.tool_service import run_pipeline
from app.services.tool_registry import ToolRegistry
from app.services.gpio_service import GPIOService
from app.models.schema import RecipeV2
from app.ui.camera_profile_utils import (
    apply_camera_state,
    apply_view_camera_profile,
    snapshot_camera_state,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HDF_Vision")
        self.mode = "RUN"  # RUN alebo SETUP

        # Live režim (RUN):
        self.live_enabled = False
        self._last_trigger_frame = None
        self._last_trigger_view_id: str | None = None
        self._last_trigger_frames: dict[str, Any] = {}

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
        self._last_total_cycle_time_ms: float | None = None
        self._last_pipeline_status: str | None = None
        self._tool_selector_items: list[dict[str, Any]] = []
        self._view_states: dict[str, dict[str, Any]] = {}
        self._active_view_id: str | None = None
        self._manual_trigger_positions: dict[str, int] = {}
        self._manual_trigger_statuses: dict[str, dict[str, str]] = {}

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
        self.btn_trigger = QPushButton("TRIGGER")  # berie posledný kontinuálny frame
        self.btn_trigger.clicked.connect(self.manual_trigger)
        actions.addWidget(self.btn_trigger)

        self.btn_export = QPushButton("Export CSV (dnes)")
        self.btn_export.clicked.connect(self.export_csv_today)
        actions.addWidget(self.btn_export)

        self.btn_wizard_quick = QPushButton("Golden WIZARD")
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

        view_strip_container = QWidget()
        view_strip_layout = QVBoxLayout(view_strip_container)
        view_strip_layout.setContentsMargins(0, 0, 0, 0)
        view_strip_layout.setSpacing(4)
        view_strip_label = QLabel("Pohľady")
        vf = QFont(); vf.setBold(True)
        view_strip_label.setFont(vf)
        view_strip_layout.addWidget(view_strip_label)
        self.view_strip = ViewStrip(on_view_selected=self._on_view_selected_view)
        view_strip_layout.addWidget(self.view_strip)
        run.addWidget(view_strip_container)
        self.strip = None

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
        self.sb_total_test_time = QLabel("Čas testov (dnes): –")
        for w in (
            self.sb_total,
            self.sb_ok,
            self.sb_nok,
            self.sb_yield,
            self.sb_total_test_time,
        ):
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

        # timer pre RUN live view refresh
        self._run_timer = QTimer(self)
        self._run_timer.setInterval(100)  # ~10 FPS
        self._run_timer.timeout.connect(self._update_live_view)
        # spúšťa sa až pri Live ON v _toggle_live()

        # inicializuj pohľady a pravý panel
        self._refresh_views()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

        # maximalizovať a uzamknúť veľkosť okna po zobrazení
        QTimer.singleShot(0, self._maximize_and_lock)

        # ---------- SETUP panel ----------
        self.panel_setup = QWidget(); self.stack.addWidget(self.panel_setup)
        s = QVBoxLayout(self.panel_setup); s.setSpacing(8)

        row1 = QHBoxLayout();
        self.btn_wizard = QPushButton("Golden Wizard", self)
        self.btn_wizard.clicked.connect(self.open_wizard)
        row1.addWidget(self.btn_wizard)

        self.btn_gpio_wizard = QPushButton("GPIO Wizard", self)
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

    @property
    def active_view_id(self) -> str | None:
        return self._active_view_id

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

    def _reset_manual_trigger_progress(self, recipe_name: str | None = None) -> None:
        if recipe_name is None:
            self._manual_trigger_positions.clear()
            self._manual_trigger_statuses.clear()
            return
        self._manual_trigger_positions.pop(recipe_name, None)
        self._manual_trigger_statuses.pop(recipe_name, None)

    def _reset_view_sequence_state(self) -> None:
        for vid in self.view_strip.view_ids():
            self.view_strip.set_status(vid, None)
            state = self._view_states.get(vid)
            if isinstance(state, dict):
                state.pop("reports", None)
                state.pop("status", None)
                state.pop("cycle_time_ms", None)
                state.pop("total_cycle_time_ms", None)
                state.pop("combined_metrics", None)
        self._update_metrics_panel()

    def manual_trigger(self):
        try:
            frame = self.cam.last_frame()
            if frame is None:
                self.lbl_status.setText("Žiadny snímok z kamery.")
                return

            base_frame = frame.copy()
            self.gpio.emit_heartbeat()
            recipe_name = self.current_recipe_name()

            try:
                recipe_cfg = load_recipe_config(recipe_name)
            except Exception as exc:
                print(f"[Tool] load_recipe_config failed for {recipe_name}: {exc}")
                recipe_cfg = None

            if recipe_cfg is None or not getattr(recipe_cfg, "views", None):
                self._reset_manual_trigger_progress(recipe_name)
                self._run_legacy_trigger(base_frame, recipe_name)
                return

            if not getattr(recipe_cfg, "regions", None):
                recipe_cfg.regions = list(getattr(self.tool, "regions", []) or [])
            recipe_cfg.pose_enabled = bool(getattr(self.tool, "pose_enabled", True))

            fail_fast = bool(getattr(recipe_cfg.aggregation, "fail_fast", False))
            run_id = f"{recipe_name}_{uuid.uuid4().hex[:8]}"
            view_specs: list[dict[str, Any]] = []
            for index, view in enumerate(recipe_cfg.views):
                settle_ms = getattr(view, "settle_ms", None)
                settle_ms = int(settle_ms) if isinstance(settle_ms, Integral) else None
                if settle_ms is not None and settle_ms < 0:
                    settle_ms = 0
                trigger_mode = str(getattr(view, "trigger_mode", "timed") or "timed").lower()
                if trigger_mode not in {"timed", "external", "manual"}:
                    trigger_mode = "timed"
                interval_ms = getattr(view, "trigger_interval_ms", None)
                interval_ms = (
                    int(interval_ms)
                    if isinstance(interval_ms, Integral) and interval_ms is not None
                    else None
                )
                if interval_ms is not None and interval_ms < 0:
                    interval_ms = 0
                view_specs.append(
                    {
                        "index": index,
                        "view": view,
                        "settle_ms": settle_ms,
                        "trigger_mode": trigger_mode,
                        "interval_ms": interval_ms,
                    }
                )

            base_camera_state = snapshot_camera_state(self.cam)

            manual_specs = [spec for spec in view_specs if spec["trigger_mode"] == "manual"]
            all_manual = bool(manual_specs) and len(manual_specs) == len(view_specs)

            if all_manual:
                cycle_position = self._manual_trigger_positions.get(recipe_name, 0)
                index_in_cycle = cycle_position % len(manual_specs)
                current_spec = manual_specs[index_in_cycle]
                self._manual_trigger_positions[recipe_name] = (
                    index_in_cycle + 1
                ) % len(manual_specs)
                if index_in_cycle == 0:
                    self._manual_trigger_statuses[recipe_name] = {}
                    self._reset_view_sequence_state()
                    per_view_statuses: dict[str, str] = {}
                else:
                    per_view_statuses = dict(self._manual_trigger_statuses.get(recipe_name, {}))
                views_to_process = [current_spec]
            else:
                self._reset_view_sequence_state()
                per_view_statuses = {}
                views_to_process = view_specs
                self._reset_manual_trigger_progress(recipe_name)

            last_preview_frame = base_frame
            trigger_start_ts = time.monotonic()

            last_view_id: str | None = None

            try:
                for spec in views_to_process:
                    view = spec["view"]
                    index = spec["index"]
                    view_id = getattr(view, "id", None) or f"view_{index+1}"
                    view_name = getattr(view, "name", view_id)
                    golden = self._load_view_golden_array(recipe_name, view)

                    profile = getattr(view, "camera_profile", None)
                    try:
                        apply_view_camera_profile(
                            self.cam,
                            base_camera_state,
                            profile,
                        )
                    except Exception as exc:
                        self.lbl_status.setText(
                            f"{view_name}: {exc}"
                        )
                        self._reset_manual_trigger_progress(recipe_name)
                        return

                    settle_ms = spec["settle_ms"]
                    trigger_mode = spec["trigger_mode"]
                    interval_ms = spec["interval_ms"]

                    if trigger_mode == "timed" and interval_ms is not None and interval_ms > 0:
                        target_time = trigger_start_ts + (interval_ms / 1000.0)
                        now = time.monotonic()
                        if now < target_time:
                            time.sleep(target_time - now)

                    if settle_ms is not None and settle_ms > 0:
                        time.sleep(settle_ms / 1000.0)

                    latest_frame = self.cam.last_frame()
                    if latest_frame is not None:
                        view_frame = latest_frame
                        base_frame = view_frame.copy()
                    else:
                        view_frame = base_frame

                    view_frame_u8 = view_frame.copy()

                    if golden is None:
                        status = "nok"
                        reports = []
                        diagnostics_payload = ["missing_golden"]
                        combined_metrics = {}
                        policy_applied = None
                        result = None
                        last_preview_frame = view_frame_u8.copy()
                    else:
                        view_recipe = RecipeV2(
                            pose_enabled=recipe_cfg.pose_enabled,
                            regions=[dict(r) for r in recipe_cfg.regions],
                            tools=[tool.copy() for tool in getattr(view, "tools", [])],
                            views=[view.copy()],
                            aggregation=recipe_cfg.aggregation.copy(),
                            on_locator_failure=recipe_cfg.on_locator_failure,
                            export_artifacts=recipe_cfg.export_artifacts,
                        )

                        result = run_pipeline(
                            golden,
                            view_frame_u8,
                            view_recipe,
                            recipe_name=recipe_name,
                            notes=f"manual_trigger::{view_id}",
                        )

                        status = (result.status or "ok").lower()
                        diagnostics_payload = [
                            self._simplify_value(diag)
                            for diag in getattr(result, "diagnostics", []) or []
                        ]
                        reports = [
                            self._serialize_tool_report(report)
                            for report in result.per_tool
                        ]
                        combined_metrics = self._merge_pipeline_metrics(reports)
                        policy_applied = getattr(result, "policy_applied", None)

                        context_frame = getattr(result.context, "frame_aligned", None)
                        if context_frame is None:
                            context_frame = getattr(result.context, "frame", None)
                        if isinstance(context_frame, np.ndarray):
                            last_preview_frame = context_frame.copy()
                        else:
                            last_preview_frame = view_frame_u8.copy()

                    per_view_statuses[view_id] = status
                    if all_manual:
                        self._manual_trigger_statuses[recipe_name] = dict(per_view_statuses)

                    cycle_time_value = float(result.cycle_time_ms) if result is not None else None
                    total_cycle_time_value = (time.monotonic() - trigger_start_ts) * 1000.0

                    meta_payload = {
                        "mode": "manual",
                        "status": status,
                        "view_id": view_id,
                        "view_name": view_name,
                        "cycle_time_ms": cycle_time_value,
                        "total_cycle_time_ms": total_cycle_time_value,
                        "per_tool": reports,
                        "diagnostics": diagnostics_payload,
                        "metrics": combined_metrics,
                        "sequence_statuses": dict(per_view_statuses),
                    }
                    if policy_applied:
                        meta_payload["policy_applied"] = policy_applied

                    artifacts = save_production_result(
                        view_frame_u8,
                        meta_payload,
                        recipe_name,
                        store_full_nok=True,
                        nok=status != "ok",
                        run_id=run_id,
                        view_id=view_id,
                    )

                    self._record_run_result(
                        recipe_name,
                        status=status,
                        metrics=combined_metrics,
                        artifacts=artifacts,
                    )

                    self._set_last_view_frame(view_id, last_preview_frame)
                    last_view_id = view_id

                    self.view_strip.set_status(view_id, status)
                    self._update_sidebar(
                        per_tool=reports,
                        status=status,
                        cycle_time_ms=cycle_time_value,
                        total_cycle_time_ms=total_cycle_time_value,
                        view_id=view_id,
                    )

                    if fail_fast and status == "nok":
                        break
            finally:
                try:
                    apply_camera_state(self.cam, base_camera_state)
                except Exception as exc:
                    print(f"[Trigger] Obnovenie nastavenia kamery zlyhalo: {exc}")
                self._sync_resolution_combo()

            if self._active_view_id and self._active_view_id not in per_view_statuses:
                self._update_sidebar(view_id=self._active_view_id)

            aggregated_status = recipe_cfg.aggregation.aggregate_statuses(per_view_statuses)
            color_map = {"ok": "#33dd66", "warn": "#e67e22", "nok": "#ff3366"}
            self.lbl_status.setText(aggregated_status.upper())
            self.lbl_status.setStyleSheet(
                f"color: {color_map.get(aggregated_status, '#33dd66')};"
            )
            self.gpio.signal_result(aggregated_status)

            active_frame = self._get_last_frame_for_view(self._active_view_id)
            if active_frame is not None:
                self._last_trigger_frame = self._clone_frame(active_frame)
                self._last_trigger_view_id = self._active_view_id
            else:
                self._last_trigger_frame = self._clone_frame(last_preview_frame)
                self._last_trigger_view_id = last_view_id

            self._reload_results_strip()

            if not self.live_enabled:
                self._update_live_view()

        except Exception:
            self.gpio.signal_result("nok")
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

        self._set_last_view_frame(None, frame_u8)
        active_frame = self._get_last_frame_for_view(self._active_view_id)
        if active_frame is not None:
            self._last_trigger_frame = self._clone_frame(active_frame)
            self._last_trigger_view_id = self._active_view_id
        else:
            self._last_trigger_frame = self._clone_frame(frame_u8)
            self._last_trigger_view_id = None

        color = "#33dd66" if status == "ok" else "#ff3366"
        self.lbl_status.setText(status.upper())
        self.lbl_status.setStyleSheet(f"color: {color};")
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
        self._last_total_cycle_time_ms = None
        st = self.stats.daily_for_recipe(recipe_name)
        self._update_sidebar(st, legacy_report, status=status)

        meta_payload = {
            "mode": "manual",
            "status": status,
            "metrics": metrics,
            "per_tool": legacy_report,
        }

        artifacts = save_production_result(
            frame_u8,
            meta_payload,
            recipe_name,
            store_full_nok=True,
            nok=status != "ok",
        )

        self._record_run_result(
            recipe_name,
            status=status,
            metrics=metrics,
            artifacts=artifacts,
        )

        self._reload_results_strip()

        if not self.live_enabled:
            self._update_live_view()

    def open_wizard(self):
        dlg = GoldenWizard(self.cam, self.recipes, self)
        dlg.resize(1200, 800)
        dlg.exec()
        self._reset_manual_trigger_progress(self.current_recipe_name())
        self._refresh_views()
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

    def open_gpio_wizard(self):
        self.gpio.set_active_recipe(self.current_recipe_name())
        dlg = GPIOWizard(self.gpio, self)
        dlg.resize(720, 520)
        dlg.exec()

    def _reload_results_strip(self) -> None:
        strip = getattr(self, "strip", None)
        if strip is None:
            return
        try:
            strip.reload()
        except Exception:
            pass

    def _toggle_live(self):
        self.live_enabled = self.btn_live.isChecked()
        self.btn_live.setText("Live ON" if self.live_enabled else "Live OFF")
        if self.live_enabled:
            self._run_timer.start()
        else:
            self._run_timer.stop()
            self._update_live_view()

    def _handle_gpio_trigger(self):
        QTimer.singleShot(0, self.manual_trigger)

    def _update_live_view(self):
        try:
            # Zdroj podľa live stavu
            if self.live_enabled:
                src = self.cam.last_frame()
            else:
                src = self._get_last_frame_for_view(self._active_view_id)
                if (
                    src is None
                    and self._last_trigger_frame is not None
                    and self._view_storage_key(self._last_trigger_view_id)
                    == self._view_storage_key(self._active_view_id)
                ):
                    src = self._last_trigger_frame
            if src is None:
                self.live_view.clear()
                self.live_view.setText("— aktuálny záber —")
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

    def _view_storage_key(self, view_id: str | None) -> str:
        return view_id or ""

    def _set_last_view_frame(self, view_id: str | None, frame: Any) -> None:
        if frame is None:
            return
        stored = frame.copy() if isinstance(frame, np.ndarray) else frame
        key = self._view_storage_key(view_id)
        self._last_trigger_frames[key] = stored
        if view_id == self._active_view_id:
            self._last_trigger_frame = stored
            self._last_trigger_view_id = view_id

    def _get_last_frame_for_view(self, view_id: str | None):
        key = self._view_storage_key(view_id)
        frame = self._last_trigger_frames.get(key)
        if frame is None and key and "" in self._last_trigger_frames:
            frame = self._last_trigger_frames.get("")
        return frame

    def _clone_frame(self, frame: Any):
        if frame is None:
            return None
        return frame.copy() if isinstance(frame, np.ndarray) else frame

    def _update_sidebar(
        self,
        st: dict | None = None,
        per_tool: Sequence[dict[str, Any]] | None = None,
        *,
        status: str | None = None,
        cycle_time_ms: float | None = None,
        total_cycle_time_ms: float | None = None,
        view_id: str | None = None,
    ):
        """Naplní pravý panel dennými štatistikami a poslednými metrikami."""
        try:
            active_view = view_id or self._active_view_id
            name = self.current_recipe_name()
            self.sb_recipe.setText(f"Recept: {name}")
            if st is None:
                st = self.stats.daily_for_recipe(name, view_id=active_view)
            pose_enabled = getattr(self.tool, "pose_enabled", True)
            self.sb_pose.setText(f"Pose alignment: {'ON' if pose_enabled else 'OFF'}")
            self.sb_total.setText(f"Celkom: {st.get('total','–')}")
            self.sb_ok.setText(f"OK: {st.get('ok','–')}")
            self.sb_nok.setText(f"NOK: {st.get('nok','–')}")
            self.sb_yield.setText(f"Yield: {st.get('yield','–')}%")
            total_cycle_time = st.get("total_cycle_time_ms")
            self.sb_total_test_time.setText(
                f"Čas testov (dnes): {self._format_total_test_duration(total_cycle_time)}"
            )

            if active_view:
                state = self._view_states.setdefault(active_view, {})
            else:
                state = self._view_states.setdefault("", {})

            if per_tool is not None:
                reports = [dict(entry) for entry in per_tool]
                state["reports"] = reports
                if active_view == self._active_view_id:
                    self._last_tool_reports = reports
            if status is not None:
                state["status"] = status
                if active_view == self._active_view_id:
                    self._last_pipeline_status = status
            if cycle_time_ms is not None:
                state["cycle_time_ms"] = cycle_time_ms
                if active_view == self._active_view_id:
                    self._last_cycle_time_ms = cycle_time_ms
            if total_cycle_time_ms is not None:
                state["total_cycle_time_ms"] = total_cycle_time_ms
                if active_view == self._active_view_id:
                    self._last_total_cycle_time_ms = total_cycle_time_ms

            if per_tool is not None:
                state["combined_metrics"] = self._merge_pipeline_metrics(per_tool)

            if active_view == self._active_view_id:
                self._update_metrics_panel()
        except Exception:
            pass

    def _set_metrics_rows(self, rows: Sequence[tuple[str, str]]):
        with suppress(Exception):
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

    def _record_run_result(
        self,
        recipe_name: str,
        *,
        status: str,
        metrics: Mapping[str, Any] | None,
        artifacts: Mapping[str, Any] | None,
    ) -> None:
        if not isinstance(artifacts, Mapping):
            return

        try:
            rid = self.db.recipe_id(recipe_name)
            if rid is None:
                rid = self.db.ensure_recipe(recipe_name)
        except Exception as exc:
            print(f"[Run][DB] Failed to resolve recipe '{recipe_name}': {exc}")
            return

        try:
            view_id = artifacts.get("view_id")
            run_id = artifacts.get("run_id")
            ts_ms = artifacts.get("ts_ms")
            meta_payload = artifacts.get("meta_payload")
            if not isinstance(meta_payload, Mapping):
                meta_payload = {}

            meta_dict = {str(k): v for k, v in dict(meta_payload).items()}
            if ts_ms is None:
                ts_ms = int(time.time() * 1000)
            meta_dict.setdefault("ts_ms", ts_ms)
            meta_dict.setdefault("status", status)
            meta_dict.setdefault("recipe", recipe_name)
            meta_dict.setdefault("nok", status != "ok")
            if view_id is not None:
                meta_dict.setdefault("view_id", view_id)
            if run_id is not None:
                meta_dict.setdefault("run_id", run_id)

            thumb_path = artifacts.get("thumb") or ""
            full_path = artifacts.get("full")

            self.db.insert_result(
                ts_ms=int(ts_ms),
                recipe_id=int(rid),
                ok=str(status).lower() == "ok",
                metrics=dict(metrics or {}),
                thumb_path=str(thumb_path),
                full_path=str(full_path) if full_path else None,
                meta_json=json.dumps(meta_dict, ensure_ascii=False),
                view_id=view_id,
                run_id=run_id,
            )
        except Exception as exc:
            print(f"[Run][DB] Failed to record run result: {exc}")

    def _update_metrics_panel(self):
        try:
            active_view = self._active_view_id or ""
            state = self._view_states.get(active_view, {})
            reports = list(state.get("reports", []) or [])
            status = state.get("status")
            cycle_time = state.get("cycle_time_ms")
            total_cycle_time = state.get("total_cycle_time_ms")

            if active_view == self._active_view_id:
                self._last_tool_reports = reports
                self._last_pipeline_status = status
                if cycle_time is not None:
                    self._last_cycle_time_ms = cycle_time
                if total_cycle_time is not None:
                    self._last_total_cycle_time_ms = total_cycle_time

            if not reports:
                reports = list(self._last_tool_reports or [])
            if status is None:
                status = self._last_pipeline_status
            if cycle_time is None:
                cycle_time = self._last_cycle_time_ms
            if total_cycle_time is None:
                total_cycle_time = self._last_total_cycle_time_ms

            selection = self.cmb_tool.currentData()
            if not reports and status is None and cycle_time is None and total_cycle_time is None:
                self._set_metrics_rows([])
                return

            if selection is None:
                rows = self._build_summary_rows(reports, status, cycle_time, total_cycle_time)
            else:
                rows = self._build_tool_metric_rows(selection, reports)
            self._set_metrics_rows(rows)
        except Exception:
            self._set_metrics_rows([])

    def _build_summary_rows(
        self,
        reports: Sequence[Mapping[str, Any]],
        status: str | None,
        cycle_time_ms: float | None,
        total_cycle_time_ms: float | None,
    ) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if status:
            rows.append(("Celkový status", str(status).upper()))
        if cycle_time_ms is not None:
            rows.append(("Cyklus [ms]", self._format_metric_value(cycle_time_ms)))
        if total_cycle_time_ms is not None:
            rows.append(("Celkový čas testu [ms]", self._format_metric_value(total_cycle_time_ms)))
        for report in reports:
            name = str(report.get("name") or report.get("id") or "Tool")
            status = str(report.get("status") or "").upper() or "—"
            rows.append((name, status))
        return rows or [("Info", "Žiadne dáta")]

    def _build_tool_metric_rows(
        self,
        selection: dict[str, Any],
        reports: Sequence[Mapping[str, Any]],
    ) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        tool_id = str(selection.get("id")) if isinstance(selection, Mapping) else str(selection or "")
        report = next((r for r in reports if str(r.get("id")) == tool_id), None)
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
            return text or str(val)
        return str(value)

    def _format_total_test_duration(self, value: Any) -> str:
        if value is None:
            return "–"
        try:
            total_ms = float(value)
        except Exception:
            return "–"

        if not math.isfinite(total_ms) or total_ms < 0:
            return "–"
        if total_ms < 1.0:
            return "0 ms"
        if total_ms < 1000.0:
            return f"{int(round(total_ms))} ms"

        total_seconds = int(total_ms // 1000)
        remainder_ms = int(round(total_ms - (total_seconds * 1000)))
        if remainder_ms >= 1000:
            total_seconds += 1
            remainder_ms = 0

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
        if minutes > 0:
            return f"{minutes:d}m {seconds:02d}s"

        if remainder_ms:
            fraction = f"{seconds}.{remainder_ms:03d}".rstrip("0").rstrip(".")
            return f"{fraction}s"

        return f"{seconds:d}s"

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

    def _refresh_views(self):
        try:
            recipe_name = self.current_recipe_name()
        except Exception:
            recipe_name = "default"

        try:
            views = self.recipes.list_views(recipe_name)
        except Exception:
            views = []

        entries = [view for view in views if getattr(view, "id", "")]
        if not entries and views:
            entries = views

        self._view_states = {getattr(view, "id", ""): {} for view in entries if getattr(view, "id", "")}
        default_view_id = entries[0].id if entries else None
        self._active_view_id = default_view_id

        self.view_strip.set_views(entries, thumbnail_loader=self._load_view_thumbnail)
        self.view_strip.set_active(self._active_view_id)

    def _load_view_thumbnail(self, view: object) -> QPixmap | None:
        try:
            recipe_name = self.current_recipe_name()
        except Exception:
            recipe_name = "default"
        golden_name = getattr(view, "golden_path", "golden.png") or "golden.png"
        path = Path("/data") / "recipes" / recipe_name / golden_name
        if not path.exists():
            return None
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        img = reader.read()
        if img.isNull():
            return None
        return QPixmap.fromImage(img)

    def _load_view_golden_array(self, recipe_name: str, view: object):
        import imageio.v3 as iio
        import numpy as np

        golden_name = getattr(view, "golden_path", "golden.png") or "golden.png"
        path = Path("/data") / "recipes" / recipe_name / golden_name
        if not path.exists():
            return None
        try:
            arr = iio.imread(path)
        except Exception as exc:
            print(f"[Run] Golden read failed for {golden_name}: {exc}")
            return None
        if arr is None:
            return None
        arr = np.asarray(arr)
        if arr.ndim == 3:
            try:
                import cv2

                if arr.shape[2] >= 3:
                    arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
                else:
                    arr = arr[:, :, 0]
            except Exception:
                arr = arr[:, :, 0]
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8)
        return arr

    def _on_view_selected_view(self, view_id: str):
        if not view_id or view_id == self._active_view_id:
            return
        if not self.view_strip.has_view(view_id):
            return
        self._active_view_id = view_id
        self.view_strip.set_active(view_id)
        self._refresh_tool_selector()
        self._update_sidebar(view_id=view_id)
        self._reload_results_strip()
        frame = self._get_last_frame_for_view(view_id)
        if frame is not None:
            self._last_trigger_frame = self._clone_frame(frame)
            self._last_trigger_view_id = view_id
        else:
            self._last_trigger_view_id = None
        if not self.live_enabled:
            self._update_live_view()

    def _refresh_tool_selector(self):
        try:
            recipe_name = self.current_recipe_name()
        except Exception:
            recipe_name = "default"

        try:
            tools = self.recipes.get_published_tools(recipe_name, self._active_view_id)
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
        self._reload_results_strip()

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
            self._refresh_views()
            self._reset_manual_trigger_progress(name)
            self.lbl_status.setText("Recipe loaded.")
            # refresh štatistík + strip
            st = self.stats.daily_for_recipe(name, view_id=self._active_view_id)
            self._reload_results_strip()
            # update sidebar (nový recept, reset posledných metrík)
            self._update_sidebar(st, [], view_id=self._active_view_id)
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
        self._refresh_views()
        self._reset_manual_trigger_progress(name)
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)
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
        self._reset_manual_trigger_progress(old)
        self._refresh_recipe_list()
        self.recipes.load(new)
        self.tool = self.recipes.tool
        self._refresh_views()
        self._reset_manual_trigger_progress(new)
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)
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
        self._reset_manual_trigger_progress(name)
        self._refresh_recipe_list()
        self.recipes.load("default")
        self.tool = self.recipes.tool
        self._refresh_views()
        self._reset_manual_trigger_progress("default")
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)
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

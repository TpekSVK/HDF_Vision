from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox,
    QStackedWidget, QFrame, QCheckBox, QSizePolicy, QGridLayout, QMessageBox, QApplication
)
from PySide6.QtCore import Qt, QTimer, Signal, QSettings
from PySide6.QtGui import QFont, QImage, QPixmap, QImageReader

import json
import logging
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

from app.services.camera_service import CameraService
from app.services.storage_service import save_production_result, load_recipe_config
from app.ui.golden_wizard import GoldenWizard
from app.ui.gpio_wizard import GPIOWizard
from app.ui.modbus_wizard import ModbusWizard
from app.services.db_service import DbService
from app.services.recipe_service import RecipeService
from app.services.stats_service import StatsService
from app.ui.view_strip import ViewStrip
from app.services.tool_service import run_pipeline
from app.services.tool_registry import ToolRegistry
from app.services.gpio_service import GPIOService
from app.services.modbus_service import ModbusService
from app.models.schema import RecipeV2
from app.ui.branching_utils import aggregate_branching_statuses
from app.utils.tool_identity import compute_tool_identity
from app.ui.camera_profile_utils import (
    apply_view_camera_profile,
    snapshot_camera_state,
)
from app.utils.trigger_timing import get_default_trigger_gap_ms
from app.ui.view_utils import apply_view_image_transform, apply_view_rotation


class MainWindow(QMainWindow):
    external_triggered = Signal()
    _UI_STATE_PATH = Path("/data/config.json")
    _LAST_RECIPE_STATE_KEY = "last_recipe"

    def __init__(self):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.setWindowTitle("HDF Vision")
        self.mode = "RUN"  # RUN alebo SETUP

        # Live režim (RUN):
        self.live_enabled = False
        self._last_trigger_frame = None
        self._last_trigger_view_id: str | None = None
        self._last_trigger_frames: dict[str, Any] = {}
        self._golden_cache: dict[tuple[str, str], tuple[int, np.ndarray]] = {}

        # Kamera
        self.cam = CameraService()
        self.cam.start(caller="main_window_init")
        try:
            self.capture_mode = "trigger" if int(self.cam.get_stream_mode()) == 1 else "master"
        except Exception:
            self.capture_mode = "master"

        # DB + služby
        self.db = DbService()
        self.recipes = RecipeService(db=self.db)
        self.stats = StatsService(db=self.db)

        self.gpio = GPIOService()
        self.gpio.register_trigger_callback(self._handle_gpio_trigger)
        self.modbus = ModbusService()
        self.modbus.register_trigger_callback(self._handle_modbus_trigger)
        self.external_triggered.connect(self.manual_trigger)

        self._last_tool_reports: list[dict[str, Any]] = []
        self._last_cycle_time_ms: float | None = None
        self._last_total_cycle_time_ms: float | None = None
        self._last_pipeline_status: str | None = None
        self._tool_selector_items: list[dict[str, Any]] = []
        self._view_states: dict[str, dict[str, Any]] = {}
        self._runtime_stats: dict[tuple[str, str], dict[str, Any]] = {}
        self._views_by_id: dict[str, Any] = {}
        self._active_view_id: str | None = None
        self._manual_trigger_positions: dict[str, int] = {}
        self._manual_trigger_statuses: dict[str, dict[str, str]] = {}
        self._run_trigger_session_active = False
        # Tool/Recipe
        try:
            if "default" not in self.recipes.list():
                self.recipes.create("default")
            startup_recipe = self._resolve_startup_recipe()
            self.recipes.load(startup_recipe)
            self.tool = self.recipes.tool  # ToolService z RecipeService
            self._persist_last_recipe(startup_recipe)
            print(f"[Tool] Loaded recipe: {startup_recipe}")
        except Exception as e:
            print("[Tool] Recipe not loaded:", e)
            self.tool = self.recipes.tool
        self.gpio.set_active_recipe(self.current_recipe_name())

        # ========== Root & Top bar ==========
        root = QWidget(); self.setCentralWidget(root)
        root_layout = QVBoxLayout(root); root_layout.setContentsMargins(10, 10, 10, 10); root_layout.setSpacing(8)

        top = QHBoxLayout(); top.setSpacing(8)
        title = QLabel("HDF Vision")
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
        run_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        run = QVBoxLayout(run_container); run.setSpacing(8)
        run_root.addWidget(run_container, 1)

        # Status + metriky + štatistiky v jednom riadku
        status_container = QWidget()
        status_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        status_row = QHBoxLayout(status_container); status_row.setSpacing(16)
        self.lbl_status = QLabel("–")
        sf = QFont(); sf.setPointSize(34); sf.setBold(True)
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

        self.btn_wizard_quick = QPushButton("Sprievodca Golden")
        self.btn_wizard_quick.clicked.connect(self.open_wizard)
        actions.addWidget(self.btn_wizard_quick)

        self.btn_flash1_toggle = QPushButton("Flash 1 ZAPNUTÉ")
        self.btn_flash1_toggle.setCheckable(True)
        self.btn_flash1_toggle.toggled.connect(lambda checked: self._toggle_modbus_flash(1, checked))
        actions.addWidget(self.btn_flash1_toggle)

        self.btn_flash2_toggle = QPushButton("Flash 2 ZAPNUTÉ")
        self.btn_flash2_toggle.setCheckable(True)
        self.btn_flash2_toggle.toggled.connect(lambda checked: self._toggle_modbus_flash(2, checked))
        actions.addWidget(self.btn_flash2_toggle)

        actions.addStretch(1)
        # Live toggle
        self.btn_live = QPushButton("Live vypnuté")
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
        preview_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_row = QHBoxLayout(preview_container); preview_row.setSpacing(12)

        # Live view panel (aktuálny záber)
        self.live_view = QLabel("— aktuálny záber —")
        self.live_view.setAlignment(Qt.AlignCenter)
        self.live_view.setMinimumSize(640, 360)
        self.live_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._live_view_base_style = "border-radius: 6px; background:#181818;"
        self._set_live_view_border()
        self.live_view.setContentsMargins(0,0,0,0)
        preview_row.addWidget(self.live_view, 4)

        # Pravý panel (štatistiky + posledné metriky)
        self.side_panel = QWidget(); self.side_panel.setObjectName("sidePanel")
        self.side_panel.setMaximumWidth(420)
        self.side_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        side = QVBoxLayout(self.side_panel); side.setSpacing(8); side.setContentsMargins(10,10,10,10)
        self.side_panel.setStyleSheet("#sidePanel{border:1px solid #333; border-radius:6px; background:#111;} QLabel{color:#ddd}")

        # Nadpis a recept
        t = QLabel("Štatistiky & Metriky"); tf = QFont(); tf.setPointSize(12); tf.setBold(True); t.setFont(tf)
        side.addWidget(t)
        self.sb_recipe = QLabel("Recept: –")
        side.addWidget(self.sb_recipe)
        self.sb_pose = QLabel("Pose alignment: –")
        side.addWidget(self.sb_pose)
        self.sb_recipe_duration = QLabel("Čas receptu: –")
        side.addWidget(self.sb_recipe_duration)

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
        preview_row.setStretch(0, 4)
        preview_row.setStretch(1, 1)

        run.addWidget(preview_container, 1)

        # timer pre RUN live view refresh
        self._run_timer = QTimer(self)
        self._run_timer.setInterval(100)  # ~10 FPS
        self._run_timer.timeout.connect(self._update_live_view)

        # Systémové akcie (spodná ľavá časť RUN)
        power_actions_container = QWidget()
        power_actions_row = QHBoxLayout(power_actions_container)
        power_actions_row.setContentsMargins(0, 0, 0, 0)
        power_actions_row.setSpacing(10)

        self.btn_shutdown_pc = QPushButton("⏻ Vypnúť PC")
        self.btn_shutdown_pc.setToolTip("Bezpečne vypnúť aplikáciu aj počítač")
        self.btn_shutdown_pc.setStyleSheet(
            """
            QPushButton {
                background: #c62828;
                color: #ffffff;
                border: 2px solid #8e0000;
                border-radius: 22px;
                padding: 8px 16px;
                font-weight: 700;
            }
            QPushButton:hover { background: #d32f2f; }
            QPushButton:pressed { background: #8e0000; }
            """
        )
        self.btn_shutdown_pc.clicked.connect(self._confirm_shutdown_pc)
        power_actions_row.addWidget(self.btn_shutdown_pc)

        self.btn_reboot_pc = QPushButton("↻ Reštart PC")
        self.btn_reboot_pc.setToolTip("Bezpečne reštartovať aplikáciu aj počítač")
        self.btn_reboot_pc.setStyleSheet(
            """
            QPushButton {
                background: #d35400;
                color: #ffffff;
                border: 2px solid #9c3e00;
                border-radius: 22px;
                padding: 8px 16px;
                font-weight: 700;
            }
            QPushButton:hover { background: #e67e22; }
            QPushButton:pressed { background: #9c3e00; }
            """
        )
        self.btn_reboot_pc.clicked.connect(self._confirm_reboot_pc)
        power_actions_row.addWidget(self.btn_reboot_pc)

        power_actions_row.addStretch(1)
        run_root.addWidget(power_actions_container, 0, Qt.AlignLeft | Qt.AlignBottom)
        # spúšťa sa až pri Live zapnuté v _toggle_live()

        # inicializuj pohľady a pravý panel
        self._refresh_views()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

        # ---------- SETUP panel ----------
        self.panel_setup = QWidget(); self.stack.addWidget(self.panel_setup)
        s = QVBoxLayout(self.panel_setup); s.setSpacing(8)

        row1 = QHBoxLayout();
        self.btn_wizard = QPushButton("Sprievodca Golden", self)
        self.btn_wizard.clicked.connect(self.open_wizard)
        row1.addWidget(self.btn_wizard)

        row1.addSpacing(12)
        row1.addWidget(QLabel("Capture Mode (global):", self))
        self.cmb_capture_mode = QComboBox(self)
        self.cmb_capture_mode.addItem("MASTER", "master")
        self.cmb_capture_mode.addItem("TRIGGER", "trigger")
        self.cmb_capture_mode.currentIndexChanged.connect(self._on_capture_mode_ui_changed)
        row1.addWidget(self.cmb_capture_mode)

        self.btn_gpio_wizard = QPushButton("Sprievodca GPIO", self)
        self.btn_gpio_wizard.clicked.connect(self.open_gpio_wizard)
        row1.addWidget(self.btn_gpio_wizard)

        self.btn_modbus_wizard = QPushButton("Sprievodca Modbus", self)
        self.btn_modbus_wizard.clicked.connect(self.open_modbus_wizard)
        row1.addWidget(self.btn_modbus_wizard)

        row1.addStretch(1)
        s.addLayout(row1)


        # default RUN zobrazenie
        self.stack.setCurrentWidget(self.panel_run)

        self._sync_capture_mode_ui()
        self._apply_capture_mode(ensure_runtime_ready=True)

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
            self._logger.info("[PAGE_SWITCH] from=run to=setup capture_mode=%s", self.capture_mode)
            if self.capture_mode == "trigger":
                self._logger.info("[PAGE_SWITCH] cleanup run trigger state without restore_master")
                self._exit_run_trigger_session(restore_master=False)
            self._logger.info("[PAGE_SWITCH] no camera mode change on page switch")
            self.stack.setCurrentWidget(self.panel_setup)
            self.mode = "SETUP"
            self.mode_btn.setText("▶ RUN")
        else:
            self._logger.info("[PAGE_SWITCH] from=setup to=run capture_mode=%s", self.capture_mode)
            self._logger.info("[PAGE_SWITCH] no camera mode change on page switch")
            self.stack.setCurrentWidget(self.panel_run)
            self.mode = "RUN"
            self.mode_btn.setText("⚙ SETUP")
            if self.capture_mode == "trigger":
                self._enter_run_trigger_session()
                self.live_enabled = False
                self.btn_live.setChecked(False)
                self.btn_live.setEnabled(False)
                self.btn_live.setText("Live vypnuté")
            else:
                self.btn_live.setEnabled(True)
                if not self.cam.is_pipeline_open():
                    self.cam.start(caller="page_switch_run_master")
            if not self.live_enabled:
                self._apply_run_camera_profile()

    def _set_live_view_border(self, color: str | None = None) -> None:
        border_color = color or "#444"
        self.live_view.setStyleSheet(
            f"border: 2px solid {border_color}; {self._live_view_base_style}"
        )

    def _apply_run_status_style(self, status: str | None) -> None:
        color_map = {"ok": "#33dd66", "warn": "#e67e22", "nok": "#ff3366"}
        status_key = str(status or "").lower()
        color = color_map.get(status_key, "#33dd66")

        font = self.lbl_status.font()
        font.setPointSize(34)
        font.setBold(True)
        self.lbl_status.setFont(font)

        self.lbl_status.setText(str(status or "–").upper())
        self.lbl_status.setStyleSheet(f"color: {color};")
        border_color = color if status_key in color_map else None
        self._set_live_view_border(border_color)

    def _manual_trigger_capture_result(self) -> str:
        status = str(getattr(self.cam, "get_last_trigger_capture_status", lambda: "normal")() or "normal").lower()
        if status == "recovered":
            return "recovered"
        if status == "fail":
            return "fail"
        return "normal"

    def _update_manual_trigger_feedback(self, *, force_fail: bool = False) -> None:
        result = "fail" if force_fail else self._manual_trigger_capture_result()
        if result == "recovered":
            self.lbl_status.setText("TRIGGER: RECOVERED SUCCESS")
        elif result == "fail":
            self.lbl_status.setText("TRIGGER: FAIL")
        else:
            self.lbl_status.setText("TRIGGER: NORMAL SUCCESS")

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
                state.pop("capture_time_ms", None)
                state.pop("processing_time_ms", None)
                state.pop("combined_metrics", None)
        self._update_metrics_panel()

    def _signal_outputs(self, status: str) -> None:
        self.gpio.emit_heartbeat()
        self.modbus.emit_heartbeat()
        self.gpio.signal_result(status)
        self.modbus.signal_result(status)

    def _toggle_modbus_flash(self, channel: int, enabled: bool) -> None:
        button = self.btn_flash1_toggle if int(channel) == 1 else self.btn_flash2_toggle
        button.setText(f"Flash {int(channel)} {'VYPNUTÉ' if enabled else 'ZAPNUTÉ'}")
        self.modbus.set_flash(channel, enabled)
        if enabled:
            self.lbl_status.setText(f"Flash {int(channel)} zapnutý")
        else:
            self.lbl_status.setText(f"Flash {int(channel)} vypnutý")

    def _run_trigger_context(self, *, requested_stream_mode: int | None = None) -> dict[str, Any]:
        current_stream_mode: int | None = None
        stream_mode_error: str | None = None
        try:
            current_stream_mode = int(self.cam.get_stream_mode())
        except Exception as exc:
            stream_mode_error = str(exc)

        return {
            "requested_stream_mode": requested_stream_mode,
            "current_stream_mode": current_stream_mode,
            "stream_mode_error": stream_mode_error,
            "pipeline_open": bool(getattr(self.cam, "is_pipeline_open", lambda: False)()),
            "live_active": bool(self.live_enabled),
            "active_view_id": self._active_view_id,
            "video_device": getattr(self.cam, "device", None),
            "hid_device": getattr(self.cam, "get_hid_device", lambda: None)(),
        }

    def _log_run_trigger_context(
        self,
        message: str,
        *,
        requested_stream_mode: int | None = None,
        hid_set: str = "not_applicable",
    ) -> None:
        ctx = self._run_trigger_context(requested_stream_mode=requested_stream_mode)
        ctx["hid_set"] = hid_set
        self._logger.debug(
            "%s | requested=%s current=%s pipeline_open=%s live_active=%s active_view_id=%s video_device=%s hid_device=%s hid_set=%s stream_mode_error=%s",
            message,
            ctx.get("requested_stream_mode"),
            ctx.get("current_stream_mode"),
            ctx.get("pipeline_open"),
            ctx.get("live_active"),
            ctx.get("active_view_id"),
            ctx.get("video_device"),
            ctx.get("hid_device"),
            ctx.get("hid_set"),
            ctx.get("stream_mode_error"),
        )

    def _log_trigger_cycle(
        self,
        event: str,
        *,
        active_view: str | None = None,
        trigger_mode: str | None = None,
        preview_state: str | None = None,
        trigger_primed: bool | None = None,
        frame_received: bool = False,
        note: str | None = None,
    ) -> None:
        stream_mode: int | None = None
        stream_mode_error: str | None = None
        try:
            stream_mode = int(self.cam.get_stream_mode())
        except Exception as exc:
            stream_mode_error = str(exc)
        self._logger.debug(
            "trigger_cycle event=%s active_recipe=%s active_view=%s stream_mode=%s pipeline_open=%s "
            "trigger_mode=%s preview=%s trigger_primed=%s frame_received=%s note=%s stream_mode_error=%s",
            event,
            self.current_recipe_name(),
            active_view or self._active_view_id,
            stream_mode,
            bool(getattr(self.cam, "is_pipeline_open", lambda: False)()),
            trigger_mode,
            preview_state,
            trigger_primed,
            frame_received,
            note,
            stream_mode_error,
        )

    def _send_run_trigger_gpio_pulse(self) -> None:
        sent = bool(getattr(self.gpio, "pulse_physical_pin", lambda *_args, **_kwargs: False)(7, pulse_seconds=0.01))
        if sent:
            self._logger.info("production trigger sent via GPIO pin=7 pulse_ms=10")
        else:
            self._logger.warning("production trigger GPIO pulse failed pin=7")

    def _build_runtime_view_spec(self, view: Any, index: int) -> dict[str, Any]:
        settle_ms = getattr(view, "settle_ms", None)
        settle_ms = int(settle_ms) if isinstance(settle_ms, Integral) else None
        if settle_ms is not None and settle_ms < 0:
            settle_ms = 0

        # manual = view sa spracuje iba po kliknutí TRIGGER (bez auto-sleep medzi viewmi)
        # timed = po spracovaní sa čaká trigger_interval_ms
        # external = view čaká na externý trigger (GPIO/Modbus), interval sa nepoužíva
        trigger_mode = str(getattr(view, "trigger_mode", "timed") or "timed").strip().lower()
        if trigger_mode not in {"timed", "external", "manual"}:
            trigger_mode = "timed"

        interval_ms = getattr(view, "trigger_interval_ms", None)
        interval_ms = int(interval_ms) if isinstance(interval_ms, Integral) else None
        if interval_ms is not None and interval_ms < 0:
            interval_ms = 0
        if trigger_mode != "timed":
            interval_ms = None

        trigger_gap_ms = getattr(view, "trigger_gap_ms", None)
        trigger_gap_ms = float(trigger_gap_ms) if isinstance(trigger_gap_ms, (int, float)) else None
        if trigger_gap_ms is not None and trigger_gap_ms <= 0:
            trigger_gap_ms = None

        profile = getattr(view, "camera_profile", None)
        width = getattr(profile, "width", None) or getattr(self.cam, "width", None)
        height = getattr(profile, "height", None) or getattr(self.cam, "height", None)
        fps = getattr(profile, "fps", None) or getattr(self.cam, "fps", None)
        if trigger_gap_ms is None:
            trigger_gap_ms = get_default_trigger_gap_ms(width, height, fps)

        frame_source_view_id = str(getattr(view, "frame_source_view_id", "") or "").strip() or None
        return {
            "index": index,
            "view": view,
            "image_rotation": int(getattr(view, "image_rotation", 0) or 0),
            "settle_ms": settle_ms,
            "trigger_mode": trigger_mode,
            "interval_ms": interval_ms,
            "trigger_gap_ms": trigger_gap_ms,
            "frame_source_view_id": frame_source_view_id,
            "branch_enabled": bool(getattr(view, "branch_enabled", False)),
            "branch_targets": dict(getattr(view, "branch_targets", {}) or {}),
            "branch_default_view_id": str(getattr(view, "branch_default_view_id", "") or "").strip() or None,
        }

    def _resolve_active_capture_view(self, *, requested_view_id: str | None = None) -> Any | None:
        view_id = requested_view_id or self._active_view_id
        if view_id:
            with suppress(Exception):
                view = self.recipes.get_view(self.current_recipe_name(), view_id)
                self._views_by_id[view_id] = view
                return view
            cached = self._views_by_id.get(view_id)
            if cached is not None:
                return cached

        with suppress(Exception):
            views = self.recipes.list_views(self.current_recipe_name())
            if views:
                view = views[0]
                resolved_id = getattr(view, "id", None)
                if resolved_id:
                    self._active_view_id = resolved_id
                    self._views_by_id[resolved_id] = view
                return view
        return None

    def _capture_frame_for_view(
        self,
        *,
        trigger_mode_label: str,
        master_caller: str,
        view: Any | None = None,
        view_id: str | None = None,
        base_camera_state: Mapping[str, Any] | None = None,
        settle_ms: int | None = None,
        transform_stage: str = "inspection",
        image_rotation_override: int | None = None,
    ):
        active_view = view if view is not None else self._resolve_active_capture_view(requested_view_id=view_id)
        active_view_id = getattr(active_view, "id", None) if active_view is not None else (view_id or self._active_view_id)
        self._logger.info("[VIEW_CAPTURE] active_view=%s", active_view_id)

        profile = getattr(active_view, "camera_profile", None) if active_view is not None else None
        self._logger.info("[VIEW_CAPTURE] applying camera profile")
        resolved_state = apply_view_camera_profile(
            self.cam,
            dict(base_camera_state) if isinstance(base_camera_state, Mapping) else snapshot_camera_state(self.cam),
            profile,
        )
        self._logger.info(
            "[VIEW_CAPTURE] resolved state width=%s height=%s fps=%s pixel_format=%s exposure=%s",
            resolved_state.get("width"),
            resolved_state.get("height"),
            resolved_state.get("fps"),
            resolved_state.get("pixel_format"),
            resolved_state.get("exposure_us"),
        )

        width = resolved_state.get("width") or getattr(self.cam, "width", None)
        height = resolved_state.get("height") or getattr(self.cam, "height", None)
        fps = resolved_state.get("fps") or getattr(self.cam, "fps", None)
        trigger_gap_ms = getattr(active_view, "trigger_gap_ms", None) if active_view is not None else None
        if not isinstance(trigger_gap_ms, (Integral, Real)) or float(trigger_gap_ms) <= 0:
            trigger_gap_ms = get_default_trigger_gap_ms(width, height, fps)
        trigger_gap_ms = float(trigger_gap_ms)
        self._logger.info("[VIEW_CAPTURE] resolved trigger_gap_ms=%.2f", trigger_gap_ms)

        mode = self.get_capture_mode()
        self._logger.info("[VIEW_CAPTURE] capture_mode=%s", mode)
        if settle_ms is not None and int(settle_ms) > 0:
            time.sleep(float(settle_ms) / 1000.0)

        if mode == "trigger":
            self._enter_run_trigger_session(trigger_gap_ms=trigger_gap_ms)
            frame = self.cam.capture_trigger_frame(
                timeout_s=0.8,
                trigger_fn=self._send_run_trigger_gpio_pulse,
                trigger_gap_ms=trigger_gap_ms,
                pulse_ms=10.0,
                trigger_mode_label=trigger_mode_label,
            )
        else:
            frame = self.cam.last_frame(caller=master_caller)
        if image_rotation_override is not None:
            frame = apply_view_rotation(
                frame,
                int(image_rotation_override),
                context=str(active_view_id or "n/a"),
            )
            if transform_stage:
                self._logger.info("[VIEW_ROTATION] applied before %s", transform_stage)
        else:
            frame = apply_view_image_transform(frame, active_view, stage=transform_stage)
        return frame

    def _enter_run_trigger_session(self, *, trigger_gap_ms: float | None = None) -> None:
        gap_ms = float(trigger_gap_ms) if trigger_gap_ms is not None else float(
            get_default_trigger_gap_ms(self.cam.width, self.cam.height, self.cam.fps)
        )
        self._logger.info("[TRIGGER_SESSION] enter trigger_gap_ms=%.2f", gap_ms)
        self.cam.enter_trigger_session(
            trigger_fn=self._send_run_trigger_gpio_pulse,
            trigger_gap_ms=gap_ms,
            pulse_ms=10.0,
        )
        self._run_trigger_session_active = True

    def _exit_run_trigger_session(self, *, restore_master: bool = False) -> None:
        if not self._run_trigger_session_active and not getattr(self.cam, "is_trigger_session_active", lambda: False)():
            if restore_master:
                self._logger.info("[TRIGGER_SESSION] exit requested restore_master=True")
                self._logger.info("[TRIGGER_SESSION] restore to master delegated to CameraService")
                self.cam.exit_trigger_session(restore_master=True)
            return
        self._logger.info("[TRIGGER_SESSION] exit requested restore_master=%s", bool(restore_master))
        self._logger.info("[TRIGGER_SESSION] restore to master delegated to CameraService")
        self.cam.exit_trigger_session(restore_master=bool(restore_master))
        self._run_trigger_session_active = False

    def _apply_capture_mode(self, *, ensure_runtime_ready: bool = False) -> None:
        # Architecture rule: global runtime owns capture mode transitions.
        # Low-level camera helpers must never switch capture mode implicitly.
        if self.capture_mode not in {"master", "trigger"}:
            self.capture_mode = "master"
        self._logger.info("[CAPTURE_MODE] %s", self.capture_mode)
        self._sync_capture_mode_ui()

        try:
            if self.capture_mode == "trigger":
                self._enter_run_trigger_session()
                self.live_enabled = False
                self.btn_live.setChecked(False)
                self.btn_live.setEnabled(False)
                self.btn_live.setText("Live vypnuté")
            else:
                self._exit_run_trigger_session(restore_master=True)
                self.btn_live.setEnabled(True)
                if ensure_runtime_ready and not self.cam.is_pipeline_open():
                    self.cam.start(caller="capture_mode_master")
        except Exception as exc:
            self._logger.error("apply capture mode failed: %s", exc)
            self.lbl_status.setText(f"Prepnutie capture mode zlyhalo: {exc}")

    def _sync_capture_mode_ui(self) -> None:
        cmb = getattr(self, "cmb_capture_mode", None)
        if cmb is None:
            return
        expected = "trigger" if self.capture_mode == "trigger" else "master"
        index = cmb.findData(expected)
        if index < 0:
            return
        if cmb.currentIndex() == index:
            return
        cmb.blockSignals(True)
        try:
            cmb.setCurrentIndex(index)
        finally:
            cmb.blockSignals(False)

    def _on_capture_mode_ui_changed(self, index: int) -> None:
        requested = str(self.cmb_capture_mode.itemData(index) or "master").strip().lower()
        if requested not in {"master", "trigger"}:
            requested = "master"
        self._logger.info("[CAPTURE_MODE_UI] requested=%s", requested)
        if requested == self.capture_mode:
            self._logger.info("[CAPTURE_MODE_UI] applied=%s", self.capture_mode)
            return
        self.capture_mode = requested
        self._apply_capture_mode(ensure_runtime_ready=True)
        self._logger.info("[CAPTURE_MODE_UI] applied=%s", self.capture_mode)

    def get_capture_mode(self) -> str:
        mode = str(getattr(self, "capture_mode", "master") or "master").strip().lower()
        return "trigger" if mode == "trigger" else "master"

    def capture_frame_for_golden(
        self,
        *,
        view_id: str | None = None,
        trigger_mode_label: str = "golden_wizard",
        image_rotation_override: int | None = None,
    ):
        self._logger.info("[GOLDEN_CAPTURE] using shared view capture path")
        frame = self._capture_frame_for_view(
            trigger_mode_label=trigger_mode_label,
            master_caller="golden_wizard_capture_master",
            view_id=view_id,
            transform_stage="golden capture",
            image_rotation_override=image_rotation_override,
        )
        self._logger.info("[GOLDEN_CAPTURE] frame captured")
        return frame

    def _capture_frame_for_trigger(self, *, trigger_mode_label: str, trigger_gap_ms: float | None = None):
        if self.capture_mode == "trigger":
            self._enter_run_trigger_session(trigger_gap_ms=trigger_gap_ms)
            return self.cam.capture_trigger_frame(
                timeout_s=0.8,
                trigger_fn=self._send_run_trigger_gpio_pulse,
                trigger_gap_ms=float(trigger_gap_ms) if trigger_gap_ms is not None else float(get_default_trigger_gap_ms(self.cam.width, self.cam.height, self.cam.fps)),
                pulse_ms=10.0,
                trigger_mode_label=trigger_mode_label,
            )
        return self.cam.last_frame(caller="run_manual_trigger_master")

    def manual_trigger(self):
        if self.mode != "RUN":
            self.lbl_status.setText("TRIGGER je dostupný len v RUN režime.")
            return
        if self.capture_mode == "trigger":
            self.modbus.pulse_configured_flashes()
        self._log_run_trigger_context("RUN trigger start")
        try:
            trigger_state = self._prepare_run_trigger()
        except Exception as exc:
            self.lbl_status.setText(f"Spustenie trigger session zlyhalo: {exc}")
            self._resume_live_preview_after_trigger(False)
            return
        if trigger_state is None:
            return
        try:
            self._logger.info("trigger_click(caller=run_manual_trigger)")
            self._log_trigger_cycle("cycle_start", preview_state="paused")
            if trigger_state["recipe_cfg"] is None:
                base_frame = self._capture_frame_for_trigger(trigger_mode_label="manual_gpio")
                active_view = self._resolve_active_capture_view(requested_view_id=self._active_view_id)
                base_frame = apply_view_image_transform(base_frame, active_view, stage="inspection")
                self._update_manual_trigger_feedback()
                self._log_trigger_cycle(
                    "legacy_capture_done",
                    trigger_mode="legacy",
                    frame_received=base_frame is not None,
                )
                self._run_legacy_trigger(
                    base_frame,
                    trigger_state["recipe_name"],
                    logging_enabled=trigger_state["logging_enabled"],
                )
                return

            queue = list(trigger_state["views_to_process"])
            while queue:
                spec = queue.pop(0)
                execution = self._execute_view_trigger(spec, trigger_state)
                if execution.get("replace_queue") is not None:
                    queue = execution["replace_queue"]
                if execution.get("should_break"):
                    break

            self._finalize_run_trigger(trigger_state)

        except Exception:
            self._update_manual_trigger_feedback(force_fail=True)
            self._signal_outputs("nok")
            import traceback; traceback.print_exc()
        finally:
            self._resume_live_preview_after_trigger(bool(trigger_state.get("was_live_enabled", False)) if trigger_state else False)

    def _prepare_run_trigger(self) -> dict[str, Any] | None:
        was_live_enabled = self._pause_live_preview_for_trigger()
        gst_starts_before = int(getattr(self.cam, "gst_start_count", lambda: 0)())
        recipe_name = self.current_recipe_name()
        base_camera_state = snapshot_camera_state(self.cam)
        self._logger.info("snapshot camera state taken")
        self._logger.info("[CAPTURE_MODE] %s", self.capture_mode)

        try:
            recipe_cfg = load_recipe_config(recipe_name)
        except Exception as exc:
            print(f"[Tool] load_recipe_config failed for {recipe_name}: {exc}")
            recipe_cfg = None

        if recipe_cfg is None or not getattr(recipe_cfg, "views", None):
            self._reset_manual_trigger_progress(recipe_name)
            return {
                "was_live_enabled": was_live_enabled,
                "gst_starts_before": gst_starts_before,
                "recipe_name": recipe_name,
                "recipe_cfg": None,
                "logging_enabled": bool(
                    getattr(recipe_cfg, "logging_enabled", True) if recipe_cfg else True
                ),
                "base_camera_state": base_camera_state,
            }

        if not getattr(recipe_cfg, "regions", None):
            recipe_cfg.regions = list(getattr(self.tool, "regions", []) or [])
        recipe_cfg.pose_enabled = bool(getattr(self.tool, "pose_enabled", True))

        view_specs: list[dict[str, Any]] = [
            self._build_runtime_view_spec(view, index)
            for index, view in enumerate(recipe_cfg.views)
        ]

        manual_specs = [spec for spec in view_specs if spec["trigger_mode"] == "manual"]
        all_manual = bool(manual_specs) and len(manual_specs) == len(view_specs)
        if all_manual:
            cycle_position = self._manual_trigger_positions.get(recipe_name, 0)
            index_in_cycle = cycle_position % len(manual_specs)
            current_spec = manual_specs[index_in_cycle]
            self._manual_trigger_positions[recipe_name] = (index_in_cycle + 1) % len(manual_specs)
            if index_in_cycle == 0:
                self._manual_trigger_statuses[recipe_name] = {}
                self._reset_view_sequence_state()
                per_view_statuses = {}
            else:
                per_view_statuses = dict(self._manual_trigger_statuses.get(recipe_name, {}))
            views_to_process = [current_spec]
        else:
            self._reset_view_sequence_state()
            per_view_statuses = {}
            views_to_process = view_specs
            self._reset_manual_trigger_progress(recipe_name)

        self._last_total_cycle_time_ms = None
        self.sb_recipe_duration.setText("Čas receptu: –")
        return {
            "gst_starts_before": gst_starts_before,
            "recipe_name": recipe_name,
            "recipe_cfg": recipe_cfg,
            "logging_enabled": bool(getattr(recipe_cfg, "logging_enabled", True)),
            "run_id": f"{recipe_name}_{uuid.uuid4().hex[:8]}",
            "view_specs": view_specs,
            "views_to_process": views_to_process,
            "all_manual": all_manual,
            "per_view_statuses": per_view_statuses,
            "ignored_for_aggregation": set(),
            "last_preview_frame": None,
            "last_view_id": None,
            "captured_frames": {},
            "trigger_start_ts": time.monotonic(),
            "spec_lookup": {
                getattr(spec["view"], "id", None) or f"view_{spec['index']+1}": spec
                for spec in view_specs
            },
            "base_camera_state": base_camera_state,
            "fail_fast": bool(getattr(recipe_cfg.aggregation, "fail_fast", False)),
        }

    def _execute_view_trigger(self, spec: dict[str, Any], trigger_state: dict[str, Any]) -> dict[str, Any]:
        recipe_name = trigger_state["recipe_name"]
        recipe_cfg = trigger_state["recipe_cfg"]
        base_camera_state = trigger_state["base_camera_state"]
        captured_frames = trigger_state["captured_frames"]
        per_view_statuses = trigger_state["per_view_statuses"]
        all_manual = trigger_state["all_manual"]

        view = spec["view"]
        index = spec["index"]
        view_id = getattr(view, "id", None) or f"view_{index+1}"
        view_name = getattr(view, "name", view_id)
        trigger_mode = spec["trigger_mode"]
        settle_ms = spec["settle_ms"]
        interval_ms = spec["interval_ms"]
        self._logger.info("active view id: %s", view_id)
        self._logger.info("trigger mode for current view: %s", trigger_mode)
        self._log_trigger_cycle(
            "view_start",
            active_view=view_id,
            trigger_mode=trigger_mode,
            preview_state="paused",
            trigger_primed=bool(getattr(self.cam, "_trigger_primed", False)),
        )

        golden = self._load_view_golden_array(recipe_name, view)
        source_view_id = spec.get("frame_source_view_id")
        view_frame_u8 = None
        injected_frame = spec.get("injected_frame")
        if injected_frame is not None:
            view_frame_u8 = self._clone_frame(injected_frame)
            view_frame_u8 = apply_view_image_transform(view_frame_u8, view, stage="inspection")
        elif source_view_id:
            view_frame_u8 = captured_frames.get(source_view_id)
            if view_frame_u8 is None:
                view_frame_u8 = self._clone_frame(self._get_last_frame_for_view(source_view_id))
            view_frame_u8 = apply_view_image_transform(view_frame_u8, view, stage="inspection")

        trigger_requested_ts = time.monotonic()
        frame_received_ts = trigger_requested_ts

        if view_frame_u8 is None:
            self._log_run_trigger_context(
                f"RUN trigger capture flow for view={view_id}",
                requested_stream_mode=None,
                hid_set="skipped",
            )
            view_frame = self._capture_frame_for_view(
                trigger_mode_label=trigger_mode,
                master_caller="run_manual_trigger_master",
                view=view,
                base_camera_state=base_camera_state,
                settle_ms=settle_ms,
                transform_stage="inspection",
            )
            self._update_manual_trigger_feedback()
            if view_frame is None:
                self._update_manual_trigger_feedback(force_fail=True)
                self.lbl_status.setText("Žiadny snímok z kamery.")
                self._reset_manual_trigger_progress(recipe_name)
                return {"should_break": True}
            view_frame_u8 = view_frame.copy()
            frame_received_ts = time.monotonic()
        else:
            frame_received_ts = time.monotonic()

        self._log_trigger_cycle(
            "view_capture_done",
            active_view=view_id,
            trigger_mode=trigger_mode,
            preview_state="paused",
            trigger_primed=bool(getattr(self.cam, "_trigger_primed", False)),
            frame_received=view_frame_u8 is not None,
        )

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
                logging_enabled=recipe_cfg.logging_enabled,
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
                self._simplify_value(diag) for diag in getattr(result, "diagnostics", []) or []
            ]
            reports = [self._serialize_tool_report(report) for report in result.per_tool]
            combined_metrics = self._merge_pipeline_metrics(reports)
            policy_applied = getattr(result, "policy_applied", None)
            context_frame = getattr(result.context, "frame_aligned", None)
            if context_frame is None:
                context_frame = getattr(result.context, "frame", None)
            if isinstance(context_frame, np.ndarray):
                context_frame = apply_view_image_transform(context_frame, view, stage="preview")
            last_preview_frame = context_frame.copy() if isinstance(context_frame, np.ndarray) else view_frame_u8.copy()

        per_view_statuses[view_id] = status
        if all_manual:
            self._manual_trigger_statuses[recipe_name] = dict(per_view_statuses)

        result_time_ts = time.monotonic()
        cycle_time_value = float(result.cycle_time_ms) if result is not None else None
        total_cycle_time_value = (result_time_ts - trigger_state["trigger_start_ts"]) * 1000.0
        capture_time_value = (frame_received_ts - trigger_requested_ts) * 1000.0
        processing_time_value = (result_time_ts - frame_received_ts) * 1000.0
        meta_payload = {
            "mode": "manual",
            "status": status,
            "view_id": view_id,
            "view_name": view_name,
            "cycle_time_ms": cycle_time_value,
            "capture_time_ms": capture_time_value,
            "processing_time_ms": processing_time_value,
            "total_cycle_time_ms": total_cycle_time_value,
            "per_tool": reports,
            "diagnostics": diagnostics_payload,
            "metrics": combined_metrics,
            "sequence_statuses": dict(per_view_statuses),
        }
        if policy_applied:
            meta_payload["policy_applied"] = policy_applied

        if trigger_state["logging_enabled"]:
            artifacts = save_production_result(
                view_frame_u8,
                meta_payload,
                recipe_name,
                store_full_nok=True,
                nok=status != "ok",
                run_id=trigger_state["run_id"],
                view_id=view_id,
            )
            self._record_run_result(recipe_name, status=status, metrics=combined_metrics, artifacts=artifacts)
        else:
            self._bump_runtime_stats(
                recipe_name,
                status=status,
                view_id=view_id,
                cycle_time_ms=cycle_time_value,
            )

        self._set_last_view_frame(view_id, last_preview_frame)
        captured_frames[view_id] = self._clone_frame(view_frame_u8)
        trigger_state["last_preview_frame"] = last_preview_frame
        trigger_state["last_view_id"] = view_id
        self.view_strip.set_status(view_id, status)
        self._update_sidebar(
            per_tool=reports,
            status=status,
            cycle_time_ms=cycle_time_value,
            capture_time_ms=capture_time_value,
            processing_time_ms=processing_time_value,
            total_cycle_time_ms=total_cycle_time_value,
            view_id=view_id,
        )

        branch_target_id = None
        replace_queue = None
        if bool(spec["branch_enabled"]):
            if index == 0:
                trigger_state["ignored_for_aggregation"].add(view_id)
            branch_map = dict(spec["branch_targets"])
            branch_target_id = branch_map.get(status) or spec.get("branch_default_view_id")
            if branch_target_id and branch_target_id != view_id:
                forwarded_frame = self._clone_frame(view_frame_u8)
                target_spec = trigger_state["spec_lookup"].get(branch_target_id)
                if target_spec:
                    queued_spec = dict(target_spec)
                    queued_spec["injected_frame"] = forwarded_frame
                    replace_queue = [queued_spec]
                else:
                    replace_queue = []

        should_break = bool(trigger_state["fail_fast"] and status == "nok" and not branch_target_id)
        if trigger_mode == "timed" and interval_ms is not None and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)
        return {"replace_queue": replace_queue, "should_break": should_break}

    def _finalize_run_trigger(self, trigger_state: dict[str, Any]) -> None:
        recipe_cfg = trigger_state["recipe_cfg"]
        per_view_statuses = trigger_state["per_view_statuses"]
        if self._active_view_id and self._active_view_id not in per_view_statuses:
            self._update_sidebar(view_id=self._active_view_id)

        aggregated_status = aggregate_branching_statuses(
            recipe_cfg.aggregation,
            per_view_statuses,
            trigger_state["ignored_for_aggregation"],
        )
        self._apply_run_status_style(aggregated_status)
        self._signal_outputs(aggregated_status)

        if trigger_state["last_preview_frame"] is not None:
            self._last_trigger_frame = self._clone_frame(trigger_state["last_preview_frame"])
            self._last_trigger_view_id = trigger_state["last_view_id"]
        else:
            active_frame = self._get_last_frame_for_view(self._active_view_id)
            if active_frame is not None:
                self._last_trigger_frame = self._clone_frame(active_frame)
                self._last_trigger_view_id = self._active_view_id

        self._reload_results_strip()
        if not self.live_enabled:
            self._update_live_view()

        gst_starts_after = int(getattr(self.cam, "gst_start_count", lambda: 0)())
        gst_restarts = max(0, gst_starts_after - trigger_state["gst_starts_before"])
        self._logger.info(
            "trigger_click_gst_starts(caller=run_manual_trigger, count=%s)",
            gst_restarts,
        )
        if gst_restarts > 0:
            self._logger.warning("any unexpected GST restart (count=%s)", gst_restarts)

    def _run_legacy_trigger(
        self,
        frame_u8,
        recipe_name: str,
        *,
        logging_enabled: bool = True,
    ):
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

        self._apply_run_status_style(status)
        self._signal_outputs(status)

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

        if logging_enabled:
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
        else:
            self._bump_runtime_stats(
                recipe_name,
                status=status,
                cycle_time_ms=None,
            )

        self._reload_results_strip()

        if not self.live_enabled:
            self._update_live_view()

    def open_wizard(self):
        if self.capture_mode == "master":
            self._exit_run_trigger_session(restore_master=False)
        else:
            self._apply_capture_mode(ensure_runtime_ready=True)
        dlg = GoldenWizard(
            self.cam,
            self.recipes,
            self,
            modbus=self.modbus,
            trigger_fn=self._send_run_trigger_gpio_pulse,
            get_capture_mode=self.get_capture_mode,
            capture_frame_for_golden=self.capture_frame_for_golden,
        )
        dlg.exec()
        if self.mode == "RUN":
            self._apply_capture_mode(ensure_runtime_ready=True)
        self._reset_manual_trigger_progress(self.current_recipe_name())
        self._refresh_views()
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

    def open_gpio_wizard(self):
        self._exit_run_trigger_session(restore_master=False)
        self.gpio.set_active_recipe(self.current_recipe_name())
        dlg = GPIOWizard(self.gpio, self)
        dlg.resize(720, 520)
        dlg.exec()
        if self.mode == "RUN":
            self._apply_capture_mode(ensure_runtime_ready=True)

    def open_modbus_wizard(self):
        self._exit_run_trigger_session(restore_master=False)
        dlg = ModbusWizard(self.modbus, self)
        dlg.resize(760, 640)
        dlg.exec()
        if self.mode == "RUN":
            self._apply_capture_mode(ensure_runtime_ready=True)

    def _reload_results_strip(self) -> None:
        strip = getattr(self, "strip", None)
        if strip is None:
            return
        try:
            strip.reload()
        except Exception:
            pass

    def _pause_live_preview_for_trigger(self) -> bool:
        was_live_enabled = bool(self.live_enabled)
        self._logger.info("preview paused")
        self._log_trigger_cycle(
            "preview_pause",
            preview_state="paused",
            note="manual trigger cycle start",
        )
        if was_live_enabled:
            self._run_timer.stop()
        if self.capture_mode == "trigger":
            self.cam.begin_trigger_capture()
        return was_live_enabled

    def _resume_live_preview_after_trigger(self, was_live_enabled: bool = False) -> None:
        if self.capture_mode == "trigger":
            self.cam.end_trigger_capture()
        if was_live_enabled:
            self._run_timer.start()
        elif not self.live_enabled:
            # Po ukončení trigger capture obnov statický preview frame.
            self._update_live_view()
        self._logger.info("preview resumed")
        self._log_trigger_cycle(
            "preview_resume",
            preview_state="resumed",
            note="manual trigger cycle end",
        )

    def _toggle_live(self):
        if self.capture_mode != "master":
            self.live_enabled = False
            self.btn_live.setChecked(False)
            self.btn_live.setText("Live vypnuté")
            return
        self.live_enabled = self.btn_live.isChecked()
        self.btn_live.setText("Live zapnuté" if self.live_enabled else "Live vypnuté")
        if self.live_enabled:
            self._apply_run_camera_profile()
            self._run_timer.start()
        else:
            self._run_timer.stop()
            self._apply_run_camera_profile()
            self._update_live_view()

    def _handle_gpio_trigger(self):
        self._handle_external_trigger("GPIO")

    def _handle_modbus_trigger(self):
        self._handle_external_trigger("Modbus")

    def _handle_external_trigger(self, source: str) -> None:
        print(f"[RUN] {source} trigger received, mode={self.mode}")
        if self.mode != "RUN":
            return
        if self.capture_mode == "trigger":
            self.modbus.pulse_configured_flashes()
        self.external_triggered.emit()

    def _update_live_view(self):
        try:
            if self.cam.is_trigger_capture_in_progress():
                return
            # Zdroj podľa live stavu
            if self.live_enabled:
                src = self.cam.last_frame(caller="run_live_view")
            else:
                src = self._get_last_frame_for_view(self._active_view_id)
                if src is None:
                    src = self._last_trigger_frame
            if src is None:
                self.live_view.clear()
                self.live_view.setText("— aktuálny záber —")
                return
            active_view = self._resolve_active_capture_view(requested_view_id=self._active_view_id)
            img = apply_view_image_transform(src, active_view, stage="preview")
            if self.chk_heatmap.isChecked():
                try:
                    img = self._make_heatmap_overlay(img)
                except Exception:
                    pass
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

    def _runtime_stats_key(self, recipe: str, view_id: str | None) -> tuple[str, str]:
        return (recipe, view_id or "")

    def _bump_runtime_stats(
        self,
        recipe: str,
        *,
        status: str,
        view_id: str | None = None,
        cycle_time_ms: float | None = None,
    ) -> None:
        key = self._runtime_stats_key(recipe, view_id)
        entry = self._runtime_stats.setdefault(
            key,
            {"total": 0, "ok": 0, "nok": 0, "total_cycle_time_ms": 0.0},
        )
        entry["total"] = int(entry.get("total", 0)) + 1
        if str(status).lower() == "ok":
            entry["ok"] = int(entry.get("ok", 0)) + 1
        else:
            entry["nok"] = int(entry.get("nok", 0)) + 1
        if cycle_time_ms is not None:
            entry["total_cycle_time_ms"] = float(entry.get("total_cycle_time_ms", 0.0)) + float(
                cycle_time_ms
            )

    def _get_runtime_stats(self, recipe: str, view_id: str | None) -> dict[str, Any] | None:
        key = self._runtime_stats_key(recipe, view_id)
        entry = self._runtime_stats.get(key)
        if not entry:
            return None
        total = int(entry.get("total", 0))
        ok = int(entry.get("ok", 0))
        nok = int(entry.get("nok", 0))
        total_cycle_time_ms = float(entry.get("total_cycle_time_ms", 0.0))
        yield_value = float(ok) / float(total) if total > 0 else 0.0
        return {
            "total": total,
            "ok": ok,
            "nok": nok,
            "yield": round(yield_value, 4),
            "total_cycle_time_ms": max(0.0, total_cycle_time_ms),
        }

    @staticmethod
    def _merge_stats(base: Mapping[str, Any], delta: Mapping[str, Any]) -> dict[str, Any]:
        total = int(base.get("total", 0)) + int(delta.get("total", 0))
        ok = int(base.get("ok", 0)) + int(delta.get("ok", 0))
        nok = int(base.get("nok", 0)) + int(delta.get("nok", 0))
        total_cycle_time_ms = float(base.get("total_cycle_time_ms", 0.0)) + float(
            delta.get("total_cycle_time_ms", 0.0)
        )
        yield_value = float(ok) / float(total) if total > 0 else 0.0
        return {
            "total": total,
            "ok": ok,
            "nok": nok,
            "yield": round(yield_value, 4),
            "total_cycle_time_ms": max(0.0, total_cycle_time_ms),
        }

    def _is_logging_enabled_for_recipe(self, recipe: str) -> bool:
        try:
            recipe_cfg = load_recipe_config(recipe)
        except Exception:
            return True
        return bool(getattr(recipe_cfg, "logging_enabled", True))

    def _update_sidebar(
        self,
        st: dict | None = None,
        per_tool: Sequence[dict[str, Any]] | None = None,
        *,
        status: str | None = None,
        cycle_time_ms: float | None = None,
        capture_time_ms: float | None = None,
        processing_time_ms: float | None = None,
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
            if not self._is_logging_enabled_for_recipe(name):
                runtime_stats = self._get_runtime_stats(name, active_view)
                if runtime_stats:
                    st = self._merge_stats(st, runtime_stats)
            pose_enabled = getattr(self.tool, "pose_enabled", True)
            self.sb_pose.setText(f"Zarovnanie pozície: {'ZAP' if pose_enabled else 'VYP'}")
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

            if total_cycle_time_ms is not None:
                state["total_cycle_time_ms"] = total_cycle_time_ms
                if active_view == self._active_view_id:
                    self._last_total_cycle_time_ms = total_cycle_time_ms

            recipe_cycle_ms = state.get("total_cycle_time_ms")
            if recipe_cycle_ms is None and active_view != self._active_view_id:
                current_state = self._view_states.get(self._active_view_id or "", {})
                if isinstance(current_state, Mapping):
                    recipe_cycle_ms = current_state.get("total_cycle_time_ms")
            if recipe_cycle_ms is None:
                recipe_cycle_ms = self._last_total_cycle_time_ms

            self.sb_recipe_duration.setText(
                f"Čas receptu: {self._format_total_test_duration(recipe_cycle_ms)}"
            )

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
            if capture_time_ms is not None:
                state["capture_time_ms"] = capture_time_ms
            if processing_time_ms is not None:
                state["processing_time_ms"] = processing_time_ms

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
            capture_time = state.get("capture_time_ms")
            processing_time = state.get("processing_time_ms")

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
            if not reports and status is None and cycle_time is None and total_cycle_time is None and capture_time is None and processing_time is None:
                self._set_metrics_rows([])
                return

            if selection is None:
                rows = self._build_summary_rows(reports, status, cycle_time, total_cycle_time, capture_time, processing_time)
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
        capture_time_ms: float | None,
        processing_time_ms: float | None,
    ) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if status:
            rows.append(("Celkový status", str(status).upper()))
        if cycle_time_ms is not None:
            rows.append(("Cycle Time [ms]", self._format_metric_value(cycle_time_ms)))
        if capture_time_ms is not None:
            rows.append(("Capture Time [ms]", self._format_metric_value(capture_time_ms)))
        if processing_time_ms is not None:
            rows.append(("Processing Time [ms]", self._format_metric_value(processing_time_ms)))
        if total_cycle_time_ms is not None:
            rows.append(("Total Cycle Time [ms]", self._format_metric_value(total_cycle_time_ms)))
        for report in reports:
            name = str(report.get("name") or report.get("id") or "Tool")
            status = str(report.get("status") or "").upper() or "—"
            rows.append((name, status))
        return rows or [("Informácia", "Žiadne dáta")]

    def _build_tool_metric_rows(
        self,
        selection: dict[str, Any],
        reports: Sequence[Mapping[str, Any]],
    ) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if isinstance(selection, Mapping):
            selection_map: Mapping[str, Any] = selection
        else:
            selection_map = {}

        tool_id = str(selection_map.get("id") or "")

        def _as_int(value: Any) -> int | None:
            try:
                return int(value)
            except Exception:
                return None

        report = next((r for r in reports if str(r.get("id")) == tool_id), None)
        if report is None:
            selection_order = _as_int(selection_map.get("order"))
            if selection_order is not None:
                report = next(
                    (
                        r
                        for r in reports
                        if _as_int(r.get("order")) == selection_order
                    ),
                    None,
                )
        if report is None:
            selection_index = _as_int(selection_map.get("index"))
            if selection_index is not None and 0 <= selection_index < len(reports):
                report = reports[selection_index]
        if report is None:
            return [("Informácia", "Žiadne dáta")]

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

        return rows or [("Informácia", "Žiadne dáta")]

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
            if abs(val) >= 1e6 or (0 < abs(val) < 0.001):
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
            "order": getattr(report, "order", tool_order),
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

    def _apply_run_camera_profile(self, view_id: str | None = None) -> None:
        """Apply the camera profile for the active recipe view when in RUN mode."""

        if self.mode != "RUN" or (self.capture_mode == "master" and self.live_enabled):
            return

        try:
            recipe_name = self.current_recipe_name()
        except Exception:
            return

        target_view_id = view_id or self._active_view_id
        view_obj: Any | None = None

        if target_view_id:
            view_obj = self._views_by_id.get(target_view_id)
            if view_obj is None:
                with suppress(Exception):
                    view_obj = self.recipes.get_view(recipe_name, target_view_id)

        if view_obj is None:
            views: list[Any] = []
            with suppress(Exception):
                views = self.recipes.list_views(recipe_name)
            if views:
                view_obj = views[0]
                resolved_id = getattr(view_obj, "id", None)
                if resolved_id:
                    target_view_id = resolved_id
                    self._active_view_id = resolved_id
                    self.view_strip.set_active(resolved_id)

        if view_obj is None:
            return

        profile = getattr(view_obj, "camera_profile", None)
        try:
            apply_view_camera_profile(self.cam, {}, profile)
        except Exception as exc:
            self.lbl_status.setText(f"Načítanie profilu kamery zlyhalo: {exc}")
            return


    def _refresh_views(self):
        self._golden_cache.clear()
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
        self._views_by_id = {getattr(view, "id", ""): view for view in entries if getattr(view, "id", "")}
        default_view = entries[0] if entries else None
        default_view_id = getattr(default_view, "id", None) if default_view is not None else None
        self._active_view_id = default_view_id

        self.view_strip.set_views(entries, thumbnail_loader=self._load_view_thumbnail)
        self.view_strip.set_active(self._active_view_id)
        if self.mode == "RUN" and not self.live_enabled:
            self._apply_run_camera_profile(self._active_view_id)

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

        golden_name = getattr(view, "golden_path", "golden.png") or "golden.png"
        path = Path("/data") / "recipes" / recipe_name / golden_name
        cache_key = (recipe_name, golden_name)

        try:
            stat = path.stat()
        except OSError:
            self._golden_cache.pop(cache_key, None)
            return None

        mtime_ns = getattr(stat, "st_mtime_ns", None)
        if mtime_ns is None:
            mtime_ns = int(stat.st_mtime * 1_000_000_000)

        cached = self._golden_cache.get(cache_key)
        if cached and cached[0] == mtime_ns:
            return cached[1]

        try:
            arr = iio.imread(path)
        except Exception as exc:
            print(f"[Run] Golden read failed for {golden_name}: {exc}")
            self._golden_cache.pop(cache_key, None)
            return None

        if arr is None:
            self._golden_cache.pop(cache_key, None)
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
        if not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr)

        arr.setflags(write=False)
        self._golden_cache[cache_key] = (mtime_ns, arr)
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
        if self.mode == "RUN" and not self.live_enabled:
            self._apply_run_camera_profile(view_id)
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

        ordered_tools = sorted(
            [(tool, idx) for idx, tool in enumerate(tools)],
            key=lambda item: int(getattr(item[0], "order", item[1])),
        )

        entries: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        for position, (tool, _) in enumerate(ordered_tools):
            tool_id, display_name, tool_order = compute_tool_identity(
                tool,
                fallback_index=position,
                used_ids=used_ids,
            )
            tool_type = tool.type or ""
            entries.append(
                {
                    "id": tool_id,
                    "name": display_name,
                    "type": tool_type,
                    "order": tool_order,
                    "index": position,
                }
            )
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

    def _confirm_shutdown_pc(self) -> None:
        answer = QMessageBox.question(
            self,
            "Vypnutie PC",
            "Naozaj chcete vypnúť tento počítač?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._request_host_power_action("shutdown")

    def _confirm_reboot_pc(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reštart PC",
            "Naozaj chcete reštartovať tento počítač?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._request_host_power_action("reboot")

    def _request_host_power_action(self, action: str) -> None:
        try:
            self.cam.stop(caller="main_window_power_action")
        except Exception:
            pass
        try:
            self.gpio.close()
        except Exception:
            pass
        try:
            self.modbus.close()
        except Exception:
            pass

        if action == "shutdown":
            QApplication.exit(10)
        elif action == "reboot":
            QApplication.exit(11)
        else:
            QMessageBox.critical(self, "Chyba", f"Neznáma akcia napájania: {action}")

    def closeEvent(self, e):
        try:
            self.cam.stop(caller="main_window_close")
            self.gpio.close()
            self.modbus.close()
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

    def _resolve_startup_recipe(self) -> str:
        recipes = self.recipes.list()
        if not recipes:
            return "default"

        saved_recipe = ""
        try:
            if self._UI_STATE_PATH.exists():
                with open(self._UI_STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved_recipe = str(data.get(self._LAST_RECIPE_STATE_KEY, "") or "").strip()
        except Exception:
            saved_recipe = ""

        if saved_recipe and saved_recipe in recipes:
            return saved_recipe
        return "default" if "default" in recipes else recipes[0]

    def _persist_last_recipe(self, recipe_name: str) -> None:
        name = str(recipe_name or "").strip()
        if not name:
            return
        data: dict[str, Any] = {}
        try:
            if self._UI_STATE_PATH.exists():
                with open(self._UI_STATE_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = dict(loaded)
        except Exception:
            data = {}

        data[self._LAST_RECIPE_STATE_KEY] = name
        try:
            self._UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self._UI_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def on_recipe_changed(self, name: str):
        try:
            self.recipes.load(name)
            self.tool = self.recipes.tool
            self._persist_last_recipe(name)
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
        self._persist_last_recipe(name)
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
        self._persist_last_recipe(new)
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
        self._persist_last_recipe("default")
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

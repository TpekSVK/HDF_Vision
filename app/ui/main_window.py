from PySide6.QtWidgets import (
    QWidget, QMainWindow, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QComboBox,
    QStackedWidget, QFrame, QCheckBox, QSizePolicy, QGridLayout, QMessageBox, QApplication,
    QScrollArea,
)
from PySide6.QtCore import Qt, QTimer, Signal, QSettings
from PySide6.QtGui import QImage, QPixmap, QImageReader

import json
import logging
import math
import re
from pathlib import Path
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from numbers import Integral, Real
from typing import Any

import numpy as np

from threading import Thread
from app.services.retention_service import RetentionService

from app.services.camera_service import CameraService
from app.services.storage_service import save_production_result, load_recipe_config
from app.ui.golden_wizard import GoldenWizard
from app.ui.modbus_wizard import ModbusWizard
from app.ui.pico_wizard import PicoWizard
from app.services.db_service import DbService
from app.services.recipe_service import RecipeService
from app.services.stats_service import StatsService
from app.ui.view_strip import ViewStrip
from app.services.tool_service import run_pipeline
from app.services.tool_registry import ToolRegistry
from app.services.modbus_service import ModbusService
from app.services.pico_service import PicoService
from app.services.pico_config_service import PicoConfigService
from app.services.jetson_stats_service import JetsonStatsService
from app.services import settings_service
from app.models.schema import RecipeV2
from app.ui.branching_utils import aggregate_branching_statuses
from app.utils.tool_identity import compute_tool_identity
from app.ui.camera_profile_utils import (
    apply_view_camera_profile,
    snapshot_camera_state,
)
from app.utils.trigger_timing import get_default_trigger_gap_ms
from app.ui.view_utils import apply_view_image_transform, apply_view_rotation
from app.ui.debug_overlay_widget import DebugOverlayWidget
from app.ui.recipe_change_log_dialog import RecipeChangeLogDialog
from app.services.security_service import SecurityService
from app.ui.password_dialog import authorize_recipe_write
from app.ui.theme import refresh_style
from app.ui.results_page import ResultsPage
from app.utils import overlay as overlay_utils
from app.utils.nok_label import nok_label


class MainWindow(QMainWindow):
    external_triggered = Signal(str, object)
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
        self._run_overlay_cache: dict[str, dict[str, Any]] = {}
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
        self.security = SecurityService()
        self.stats = StatsService(db=self.db)

        self.modbus = ModbusService()
        self.pico = PicoService()
        self.pico_config = PicoConfigService()
        self.pico.connect()
        self.pico.register_trigger_callback(self._handle_pico_trigger)
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
        self._pending_pico_software_request: dict[str, Any] | None = None
        self._external_sequence_index: dict[str, int] = {"pico": 0, "modbus": 0}
        self._external_sequence_statuses: dict[str, dict[str, str]] = {}
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
        # ========== Root & Top bar ==========
        root = QWidget(); root.setObjectName("mainWindowRoot"); self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        self._jetson_stats_service = JetsonStatsService(self)
        self._debug_overlay = DebugOverlayWidget(root)
        self._debug_overlay.raise_()
        self._jetson_stats_service.stats_updated.connect(self._debug_overlay.update_stats)
        self._overlay_enabled = False

        self.top_bar = QFrame(root)
        self.top_bar.setObjectName("appTopBar")
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(14, 8, 14, 8)
        top.setSpacing(8)
        title = QLabel("HDF Vision")
        title.setProperty("role", "appTitle")
        top.addWidget(title)
        top.addStretch(1)

        self.btn_mode_run = QPushButton("RUN")
        self.btn_mode_run.setCheckable(True)
        self.btn_mode_run.setProperty("role", "mode")
        self.btn_mode_run.clicked.connect(lambda: self._request_mode("RUN"))
        top.addWidget(self.btn_mode_run)

        self.mode_btn = QPushButton("SETUP")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setProperty("role", "mode")
        self.mode_btn.clicked.connect(lambda: self._request_mode("SETUP"))
        top.addWidget(self.mode_btn)
        self.btn_results = QPushButton("VÝSLEDKY")
        self.btn_results.setCheckable(True)
        self.btn_results.setProperty("role", "mode")
        self.btn_results.clicked.connect(lambda: self._request_mode("RESULTS"))
        top.addWidget(self.btn_results)
        top.addStretch(1)

        recipe_label = QLabel("Recept:")
        recipe_label.setProperty("role", "secondary")
        top.addWidget(recipe_label)
        self.cmb_recipe = QComboBox(); self._refresh_recipe_list()
        self.cmb_recipe.setMinimumWidth(190)
        self.cmb_recipe.currentTextChanged.connect(self.on_recipe_changed)
        top.addWidget(self.cmb_recipe)

        self.security_status = QLabel(
            "ADMIN MODE" if self.security.is_admin_mode()
            else ("Ochrana receptov: ZAPNUTÁ" if self.security.has_password()
                  else "Ochrana receptov: VYPNUTÁ")
        )
        self.security_status.setProperty("role", "secondary")
        if self.security.is_admin_mode():
            self.security_status.setStyleSheet("color: #e6a23c; font-weight: bold;")
        top.addWidget(self.security_status)

        root_layout.addWidget(self.top_bar)

        # Recipe management actions are placed in the SETUP dashboard below.
        self.btn_new = QPushButton("Nový")
        self.btn_ren = QPushButton("Premenovať")
        self.btn_del = QPushButton("Zmazať")
        self.btn_new.clicked.connect(self.on_recipe_new)
        self.btn_ren.clicked.connect(self.on_recipe_rename)
        self.btn_del.clicked.connect(self.on_recipe_delete)
        self.btn_del.setProperty("role", "destructive")

        # ========== Stacked RUN/SETUP ==========
        self.stack = QStackedWidget()
        self.panel_results = None
        root_layout.addWidget(self.stack, 1)

        # ---------- RUN panel ----------
        self.panel_run = QWidget(); self.stack.addWidget(self.panel_run)
        run_root = QVBoxLayout(self.panel_run); run_root.setSpacing(8)

        run_container = QWidget()
        run_container.setObjectName("runContainer")
        run_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        run = QVBoxLayout(run_container); run.setSpacing(8)
        run_root.addWidget(run_container, 1)

        # Prominent operator result card (placed in the right RUN column).
        status_container = QFrame()
        status_container.setObjectName("runStatusCard")
        status_container.setProperty("role", "card")
        status_container.setProperty("status", "idle")
        status_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        status_row = QVBoxLayout(status_container)
        status_row.setContentsMargins(14, 12, 14, 12)
        status_row.setSpacing(3)
        status_caption = QLabel("VÝSLEDOK KONTROLY")
        status_caption.setProperty("role", "secondary")
        status_row.addWidget(status_caption)
        self.lbl_status = QLabel("–")
        self.lbl_status.setProperty("role", "statusHero")
        self.lbl_status.setProperty("status", "idle")
        self.lbl_status.setAlignment(Qt.AlignLeft)
        status_row.addWidget(self.lbl_status)
        self._run_status_message = QLabel("Čaká na prvú kontrolu")
        self._run_status_message.setProperty("role", "secondary")
        self._run_status_message.setWordWrap(True)
        status_row.addWidget(self._run_status_message)
        self.run_status_card = status_container

        # Akcie (TRIGGER, Export, Wizard) + Live + Heatmap + minimalizácia stripu
        actions_container = QFrame()
        actions_container.setProperty("role", "panel")
        actions_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        actions = QHBoxLayout(actions_container)
        actions.setContentsMargins(10, 6, 10, 6)
        actions.setSpacing(8)
        self.btn_trigger = QPushButton("TRIGGER")  # berie posledný kontinuálny frame
        self.btn_trigger.setProperty("role", "primary")
        self.btn_trigger.setMinimumWidth(132)
        self.btn_trigger.clicked.connect(self._request_software_trigger)
        actions.addWidget(self.btn_trigger)

        self.btn_export = QPushButton("Export CSV", actions_container)
        self.btn_export.clicked.connect(self.export_csv_today)

        self.chk_show_roi = QCheckBox("Zobraziť ROI", actions_container)
        self.chk_show_roi.setToolTip("Zobraziť kontrolovanú oblasť vybraného nástroja")
        self.chk_show_roi.toggled.connect(self._on_run_overlay_controls_changed)
        actions.addWidget(self.chk_show_roi)

        self.cmb_roi_tool = QComboBox(actions_container)
        self.cmb_roi_tool.setMinimumWidth(190)
        self.cmb_roi_tool.setToolTip("Vybrať nástroj, ktorého ROI sa zobrazí")
        self.cmb_roi_tool.currentIndexChanged.connect(self._on_run_overlay_controls_changed)
        actions.addWidget(self.cmb_roi_tool)

        actions.addStretch(1)
        # Live toggle
        self.btn_live = QPushButton("Live vypnuté")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._toggle_live)
        actions.addWidget(self.btn_live)

        self.btn_manual_light = QPushButton("Svetlo: neznámy stav")
        self.btn_manual_light.setCheckable(True)
        self.btn_manual_light.clicked.connect(self._toggle_manual_light)
        actions.addWidget(self.btn_manual_light)
        QTimer.singleShot(0, self._refresh_manual_light)

        # Heatmap toggle
        self.chk_heatmap = QCheckBox("Mapa rozdielov", actions_container)
        self.chk_heatmap.setToolTip("Zobraziť farebnú mapu rozdielov voči golden")
        self.chk_heatmap.hide()

        self.lbl_tool_selector = QLabel("Detail nástroja:", actions_container)
        self.lbl_tool_selector.setProperty("role", "secondary")
        self.lbl_tool_selector.hide()
        self.cmb_tool = QComboBox(actions_container)
        self.cmb_tool.setEnabled(False)
        self.cmb_tool.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb_tool.currentIndexChanged.connect(self._on_tool_selection_changed)
        self.cmb_tool.hide()

        actions_container.setMaximumHeight(actions_container.sizeHint().height())
        run.addWidget(actions_container)

        view_strip_container = QFrame()
        view_strip_container.setProperty("role", "panel")
        view_strip_container.setMinimumWidth(126)
        view_strip_container.setMaximumWidth(146)
        view_strip_layout = QVBoxLayout(view_strip_container)
        view_strip_layout.setContentsMargins(10, 7, 10, 7)
        view_strip_layout.setSpacing(6)
        view_strip_label = QLabel("POHĽADY")
        view_strip_label.setProperty("role", "secondary")
        view_strip_layout.addWidget(view_strip_label)
        self.view_strip = ViewStrip(
            on_view_selected=self._on_view_selected_view,
            orientation=Qt.Vertical,
        )
        self.view_strip_scroll = QScrollArea()
        self.view_strip_scroll.setWidgetResizable(True)
        self.view_strip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view_strip_scroll.setFrameShape(QFrame.NoFrame)
        self.view_strip_scroll.setWidget(self.view_strip)
        view_strip_layout.addWidget(self.view_strip_scroll, 1)
        self.view_strip_container = view_strip_container
        self.strip = None

        # Live view + pravý sidebar so štatistikami
        preview_container = QWidget()
        preview_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_row = QHBoxLayout(preview_container)
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(10)

        preview_row.addWidget(self.view_strip_container, 0)

        # Live view panel (aktuálny záber)
        self.live_view = QLabel("— aktuálny záber —")
        self.live_view.setObjectName("liveInspectionView")
        self.live_view.setProperty("status", "idle")
        self.live_view.setAlignment(Qt.AlignCenter)
        self.live_view.setMinimumSize(640, 360)
        self.live_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.live_view.setContentsMargins(0,0,0,0)
        preview_row.addWidget(self.live_view, 4)

        # Pravý panel (štatistiky + posledné metriky)
        self.side_panel = QFrame(); self.side_panel.setObjectName("sidePanel")
        self.side_panel.setProperty("role", "panel")
        self.side_panel.setMinimumWidth(300)
        self.side_panel.setMaximumWidth(380)
        self.side_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        side = QVBoxLayout(self.side_panel)
        side.setSpacing(8)
        side.setContentsMargins(10,10,10,10)

        side.addWidget(self.run_status_card)

        # Nadpis a recept
        t = QLabel("PREVÁDZKOVÝ PREHĽAD")
        t.setProperty("role", "secondary")
        side.addWidget(t)
        self.sb_recipe = QLabel("Recept: –")
        self.sb_recipe.hide()
        self.sb_pose = QLabel("Pose alignment: –")
        self.sb_pose.hide()
        cycle_card = QFrame()
        cycle_card.setProperty("role", "card")
        cycle_layout = QVBoxLayout(cycle_card)
        cycle_layout.setContentsMargins(10, 8, 10, 8)
        cycle_layout.setSpacing(2)
        cycle_caption = QLabel("ČAS CYKLU")
        cycle_caption.setProperty("role", "secondary")
        cycle_layout.addWidget(cycle_caption)
        self.sb_recipe_duration = QLabel("–")
        self.sb_recipe_duration.setProperty("role", "metricValue")
        cycle_layout.addWidget(self.sb_recipe_duration)
        side.addWidget(cycle_card)

        # Denné štatistiky
        today_label = QLabel("DNES")
        today_label.setProperty("role", "secondary")
        side.addWidget(today_label)
        self.sb_total = QLabel("Celkom: –")
        self.sb_ok    = QLabel("OK: –")
        self.sb_nok   = QLabel("NOK: –")
        self.sb_yield = QLabel("Úspešnosť: –")
        self.sb_total_test_time = QLabel("Čas testov (dnes): –")
        self.sb_total_test_time.hide()
        self.sb_ok.setProperty("status", "ok")
        self.sb_nok.setProperty("status", "nok")
        daily_card = QFrame()
        daily_card.setProperty("role", "card")
        daily_layout = QGridLayout(daily_card)
        daily_layout.setContentsMargins(10, 8, 10, 8)
        daily_layout.setHorizontalSpacing(12)
        daily_layout.setVerticalSpacing(5)
        daily_layout.addWidget(self.sb_total, 0, 0)
        daily_layout.addWidget(self.sb_yield, 0, 1)
        daily_layout.addWidget(self.sb_ok, 1, 0)
        daily_layout.addWidget(self.sb_nok, 1, 1)
        side.addWidget(daily_card)

        # Posledné meranie (TRIGGER)
        last_measurement = QLabel("POSLEDNÁ KONTROLA")
        last_measurement.setProperty("role", "secondary")
        side.addWidget(last_measurement)
        self.metrics_scroll = QScrollArea()
        self.metrics_scroll.setWidgetResizable(True)
        self.metrics_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.metrics_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.metrics_scroll.setFrameShape(QFrame.NoFrame)
        self.metrics_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.metrics_container = QWidget()
        self.metrics_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.metrics_layout = QGridLayout(self.metrics_container)
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setSpacing(4)
        self.metrics_layout.setColumnStretch(0, 2)
        self.metrics_layout.setColumnStretch(1, 1)
        self.metrics_layout.setColumnMinimumWidth(1, 110)
        self._metrics_widgets: list[QLabel] = []
        self._metric_name_labels: list[tuple[QLabel, str]] = []
        self._metrics_placeholder = QLabel("Žiadne dáta")
        self._metrics_placeholder.setProperty("role", "secondary")
        self.metrics_layout.addWidget(self._metrics_placeholder, 0, 0, 1, 2)
        self.metrics_scroll.setWidget(self.metrics_container)
        side.addWidget(self.metrics_scroll, 1)

        preview_row.addWidget(self.side_panel, 1)
        preview_row.setStretch(0, 0)
        preview_row.setStretch(1, 4)
        preview_row.setStretch(2, 1)

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

        self.btn_shutdown_pc = QPushButton("Vypnúť počítač")
        self.btn_shutdown_pc.setToolTip("Bezpečne vypnúť aplikáciu aj počítač")
        self.btn_shutdown_pc.setProperty("role", "destructive")
        self.btn_shutdown_pc.clicked.connect(self._confirm_shutdown_pc)
        power_actions_row.addWidget(self.btn_shutdown_pc)

        self.btn_reboot_pc = QPushButton("Reštartovať počítač")
        self.btn_reboot_pc.setToolTip("Bezpečne reštartovať aplikáciu aj počítač")
        self.btn_reboot_pc.setProperty("role", "warning")
        self.btn_reboot_pc.clicked.connect(self._confirm_reboot_pc)
        power_actions_row.addWidget(self.btn_reboot_pc)

        power_actions_row.addStretch(1)
        self.power_actions_container = power_actions_container
        # spúšťa sa až pri Live zapnuté v _toggle_live()

        # inicializuj pohľady a pravý panel
        self._refresh_views()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

        # ---------- SETUP panel ----------
        self.panel_setup = QWidget(); self.panel_setup.setObjectName("setupWorkspace")
        self.stack.addWidget(self.panel_setup)
        setup_root = QVBoxLayout(self.panel_setup)
        setup_root.setContentsMargins(0, 0, 0, 0)

        setup_scroll = QScrollArea(self.panel_setup)
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setFrameShape(QFrame.NoFrame)
        setup_root.addWidget(setup_scroll)

        setup_content = QWidget()
        setup_content.setObjectName("setupContent")
        setup_layout = QVBoxLayout(setup_content)
        setup_layout.setContentsMargins(10, 10, 10, 10)
        setup_layout.setSpacing(12)
        setup_scroll.setWidget(setup_content)

        setup_hero = QFrame(setup_content)
        setup_hero.setObjectName("setupHero")
        hero_layout = QHBoxLayout(setup_hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        hero_layout.setSpacing(18)
        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(4)
        hero_kicker = QLabel("NASTAVENIE KONTROLY")
        hero_kicker.setProperty("role", "setupKicker")
        hero_copy.addWidget(hero_kicker)
        hero_title = QLabel("Golden, ROI a nástroje")
        hero_title.setProperty("role", "setupHeroTitle")
        hero_copy.addWidget(hero_title)
        hero_description = QLabel(
            "Vytvorte referenčný obraz, nastavte kontrolované oblasti a nakonfigurujte pipeline."
        )
        hero_description.setProperty("role", "setupDescription")
        hero_description.setWordWrap(True)
        hero_copy.addWidget(hero_description)
        hero_layout.addLayout(hero_copy, 1)

        self.btn_wizard = QPushButton("Otvoriť Golden Setup", setup_hero)
        self.btn_wizard.setObjectName("setupPrimaryAction")
        self.btn_wizard.setProperty("role", "primary")
        self.btn_wizard.setMinimumSize(240, 44)
        self.btn_wizard.clicked.connect(self.open_wizard)
        hero_layout.addWidget(self.btn_wizard, 0, Qt.AlignVCenter)
        setup_layout.addWidget(setup_hero)

        def create_setup_card(kicker: str, title: str, description: str):
            card = QFrame(setup_content)
            card.setProperty("role", "card")
            card.setProperty("setupCard", True)
            card.setMinimumHeight(220)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 16, 18, 16)
            card_layout.setSpacing(8)
            kicker_label = QLabel(kicker)
            kicker_label.setProperty("role", "setupKicker")
            card_layout.addWidget(kicker_label)
            title_label = QLabel(title)
            title_label.setProperty("role", "setupCardTitle")
            card_layout.addWidget(title_label)
            description_label = QLabel(description)
            description_label.setProperty("role", "setupDescription")
            description_label.setWordWrap(True)
            card_layout.addWidget(description_label)
            return card, card_layout

        setup_grid = QGridLayout()
        setup_grid.setHorizontalSpacing(12)
        setup_grid.setVerticalSpacing(12)
        setup_grid.setColumnStretch(0, 1)
        setup_grid.setColumnStretch(1, 1)

        recipe_card, recipe_layout = create_setup_card(
            "AKTÍVNY RECEPT",
            "Správa receptu",
            "Recept združuje Golden snímky, pohľady, nástroje a rozhodovacie pravidlá.",
        )
        self.setup_recipe_name = QLabel(self.current_recipe_name())
        self.setup_recipe_name.setProperty("role", "setupRecipeName")
        recipe_layout.addWidget(self.setup_recipe_name)
        recipe_actions = QHBoxLayout()
        recipe_actions.setSpacing(8)
        recipe_actions.addWidget(self.btn_new)
        recipe_actions.addWidget(self.btn_ren)
        recipe_actions.addWidget(self.btn_del)
        recipe_actions.addStretch(1)
        recipe_layout.addLayout(recipe_actions)
        recipe_layout.addStretch(1)
        setup_grid.addWidget(recipe_card, 0, 0)

        camera_card, camera_layout = create_setup_card(
            "KAMERA",
            "Snímanie a spúšťanie",
            "Vyberte spôsob získania obrazu. Nastavenie konkrétnej kamery je súčasťou Golden Setupu.",
        )
        capture_row = QHBoxLayout()
        capture_label = QLabel("Režim snímania")
        capture_label.setProperty("role", "setupFieldLabel")
        capture_row.addWidget(capture_label)
        capture_row.addStretch(1)
        self.cmb_capture_mode = QComboBox(camera_card)
        self.cmb_capture_mode.setMinimumWidth(180)
        self.cmb_capture_mode.addItem("MASTER", "master")
        self.cmb_capture_mode.addItem("TRIGGER", "trigger")
        self.cmb_capture_mode.currentIndexChanged.connect(self._on_capture_mode_ui_changed)
        capture_row.addWidget(self.cmb_capture_mode)
        camera_layout.addLayout(capture_row)
        camera_hint = QLabel("MASTER pre živý obraz · TRIGGER pre externé spustenie linkou")
        camera_hint.setProperty("role", "setupHint")
        camera_hint.setWordWrap(True)
        camera_layout.addWidget(camera_hint)
        camera_layout.addStretch(1)
        setup_grid.addWidget(camera_card, 0, 1)

        communication_card, communication_layout = create_setup_card(
            "KOMUNIKÁCIA",
            "Prepojenie s linkou",
            "Nastavte vstupy pre snímanie a výstupy výsledkov OK/NOK.",
        )
        self.btn_modbus_wizard = QPushButton("Modbus TCP", communication_card)
        self.btn_modbus_wizard.setProperty("role", "setupAction")
        self.btn_modbus_wizard.clicked.connect(self.open_modbus_wizard)
        communication_layout.addWidget(self.btn_modbus_wizard)
        self.btn_pico_wizard = QPushButton("Raspberry Pi Pico", communication_card)
        self.btn_pico_wizard.setProperty("role", "setupAction")
        self.btn_pico_wizard.clicked.connect(self.open_pico_wizard)
        communication_layout.addWidget(self.btn_pico_wizard)
        communication_layout.addStretch(1)
        setup_grid.addWidget(communication_card, 1, 0)

        system_card, system_layout = create_setup_card(
            "SYSTÉM",
            "Dáta a diagnostika",
            "Exportujte výsledky, skontrolujte zmeny receptu alebo zapnite servisné informácie.",
        )
        system_actions = QHBoxLayout()
        system_actions.setSpacing(8)
        self.btn_export.setText("Exportovať CSV")
        self.btn_export.setProperty("role", "setupAction")
        system_actions.addWidget(self.btn_export)
        self.btn_change_log = QPushButton("Záznam zmien", system_card)
        self.btn_change_log.setProperty("role", "setupAction")
        self.btn_change_log.clicked.connect(self.open_change_log)
        system_actions.addWidget(self.btn_change_log)
        system_actions.addStretch(1)
        system_layout.addLayout(system_actions)
        self.chk_debug_overlay = QCheckBox("Zobraziť servisný overlay výkonu", system_card)
        self.chk_debug_overlay.setToolTip("Zobraziť diagnostické informácie o výkone")
        self.chk_debug_overlay.toggled.connect(self._on_debug_overlay_toggled)
        system_layout.addWidget(self.chk_debug_overlay)
        system_layout.addStretch(1)
        system_layout.addWidget(self.power_actions_container)
        setup_grid.addWidget(system_card, 1, 1)

        setup_layout.addLayout(setup_grid)
        setup_layout.addStretch(1)


        # default RUN zobrazenie
        self.stack.setCurrentWidget(self.panel_run)
        self._sync_mode_chrome()

        self._sync_capture_mode_ui()
        self._apply_capture_mode(ensure_runtime_ready=True)

        # Spusť retenciu na pozadí (jednorazovo pri štarte)
        Thread(target=lambda: RetentionService().run_once(verbose=False), daemon=True).start()
        self._apply_debug_overlay_setting()

    # ---------- Helpers ----------
    def current_recipe_name(self) -> str:
        return getattr(self.tool, "recipe", "default") or "default"

    @property
    def active_view_id(self) -> str | None:
        return self._active_view_id

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_metric_name_elision()

    # ---------- UI akcie ----------
    def _request_mode(self, target: str) -> None:
        target_mode = str(target or "").upper()
        if target_mode == "RESULTS":
            if self.stack.currentWidget() is self.panel_run:
                self.toggle_mode()
            if self.panel_results is None:
                self.panel_results = ResultsPage(self.db.db_path, self)
                self.stack.addWidget(self.panel_results)
            self.stack.setCurrentWidget(self.panel_results)
            self.mode = "RESULTS"
            self.panel_results.activate()
            self._sync_mode_chrome()
            return
        if self.mode == "RESULTS" and target_mode == "SETUP":
            self.stack.setCurrentWidget(self.panel_setup)
            self.mode = "SETUP"
            self._sync_mode_chrome()
            return
        current_mode = "RUN" if self.stack.currentWidget() is self.panel_run else "SETUP"
        if target_mode in {"RUN", "SETUP"} and target_mode != current_mode:
            self.toggle_mode()
        else:
            self._sync_mode_chrome()

    def _sync_mode_chrome(self) -> None:
        is_run = self.stack.currentWidget() is self.panel_run
        self.btn_mode_run.setChecked(is_run)
        self.mode_btn.setChecked(self.stack.currentWidget() is self.panel_setup)
        self.btn_results.setChecked(self.panel_results is not None and self.stack.currentWidget() is self.panel_results)

    def toggle_mode(self):
        if self.stack.currentWidget() is self.panel_run:
            self._logger.info("[PAGE_SWITCH] from=run to=setup capture_mode=%s", self.capture_mode)
            if self.capture_mode == "trigger":
                self._logger.info("[PAGE_SWITCH] cleanup run trigger state without restore_master")
                self._exit_run_trigger_session(restore_master=False)
            self._logger.info("[PAGE_SWITCH] no camera mode change on page switch")
            self.stack.setCurrentWidget(self.panel_setup)
            self.mode = "SETUP"
        else:
            self._logger.info("[PAGE_SWITCH] from=setup to=run capture_mode=%s", self.capture_mode)
            self._logger.info("[PAGE_SWITCH] no camera mode change on page switch")
            self.stack.setCurrentWidget(self.panel_run)
            self.mode = "RUN"
            self._reset_external_sequence_state()
            self._refresh_manual_light()
            if self.capture_mode == "trigger":
                self._enter_run_trigger_session()
                self.live_enabled = False
                self.btn_live.setChecked(False)
                self.btn_live.setEnabled(False)
                self.btn_live.setText("Live vypnuté")
            else:
                self.btn_live.setEnabled(True)
                self.pico.prepare_master(self.cam)
            if not self.live_enabled:
                self._apply_run_camera_profile()
        self._sync_mode_chrome()

    def _set_live_view_border(self, status: str | None = None) -> None:
        status_key = str(status or "idle").lower()
        if status_key not in {"ok", "warn", "nok"}:
            status_key = "idle"
        self.live_view.setProperty("status", status_key)
        refresh_style(self.live_view)

    def _apply_run_status_style(self, status: str | None) -> None:
        status_key = str(status or "").lower()
        if status_key not in {"ok", "warn", "nok"}:
            status_key = "idle"

        self.lbl_status.setText(str(status or "–").upper())
        self.lbl_status.setProperty("status", status_key)
        self.run_status_card.setProperty("status", status_key)
        messages = {
            "ok": "Kontrola úspešná",
            "warn": "Kontrola vyžaduje overenie",
            "nok": "Vyžaduje pozornosť operátora",
            "idle": "Čaká na prvú kontrolu",
        }
        self._run_status_message.setText(messages[status_key])
        refresh_style(self.lbl_status)
        refresh_style(self.run_status_card)
        self._set_live_view_border(status_key)

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

    def _reset_external_sequence_state(self) -> None:
        """Start every source-specific external sequence at its first view."""
        self._external_sequence_index = {"pico": 0, "modbus": 0}
        self._external_sequence_statuses.clear()

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
        self.modbus.emit_heartbeat()
        self.modbus.signal_result(status)

    def _publish_recipe_flash_to_pico(self, recipe_name: str) -> tuple[bool, str]:
        mapping = self.recipes.build_pico_flash_mapping(recipe_name, published=True)
        if not self.pico.is_available() and not self.pico.connect():
            return False, self.pico.last_error or "Pico nie je dostupné"
        ok_cfg = self.pico.configure_recipe_views(mapping)
        ok_save = self.pico.save() if ok_cfg else False
        if ok_cfg and ok_save:
            return True, "Flash config uložený"
        return False, self.pico.last_error or "Synchronizácia Pico zlyhala"

    def _handle_master_flash_capture_flow(
        self, *, view: Any | None, capture_request_source: str,
    ):
        source = str(capture_request_source or "manual").lower()
        if source in {"pico", "picosoftware"}:
            raise RuntimeError("Pico snímka nemá rezervovaný frame.")
        view_id = getattr(view, "id", None) or self._active_view_id or "view_1"
        explicit = (
            str(getattr(view, "external_trigger_mode", "")).lower() == "explicit"
            and str(getattr(view, "external_source", "")).lower() == "pico"
        )
        if explicit:
            index = getattr(view, "external_request_input", None)
            if isinstance(index, bool) or not isinstance(index, Integral) or not 1 <= index <= 8:
                raise RuntimeError("Pohľad nemá platný Pico vstup.")
            target = f"IN{index}"
            if not self.pico_config.is_input_enabled(int(index)):
                raise RuntimeError("Pico vstup je zakázaný.")
        else:
            target = str(getattr(view, "pico_profile", None) or "").upper()
            if target not in {"V1", "V2"}:
                target = self.pico._normalize_target(view_id)
            if target not in {"V1", "V2"}:
                raise RuntimeError("Pohľad nemá platný Pico profil V1/V2.")
        self.cam.prepare_master_capture()
        return self.pico.capture_master(target, self.cam)

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
        # Kept only to fail closed for stale integrations; never pulse Jetson GPIO.
        raise RuntimeError("Použite spoločnú Pico PIO capture transakciu.")

    def _build_runtime_view_spec(self, view: Any, index: int) -> dict[str, Any]:
        settle_ms = getattr(view, "settle_ms", None)
        settle_ms = int(settle_ms) if isinstance(settle_ms, Integral) else None
        if settle_ms is not None and settle_ms < 0:
            settle_ms = 0

        # manual = view sa spracuje iba po kliknutí TRIGGER (bez auto-sleep medzi viewmi)
        # timed = po spracovaní sa čaká trigger_interval_ms
        # external = view čaká na externý trigger (Pico/Modbus), interval sa nepoužíva
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
        external_trigger_mode = str(
            getattr(view, "external_trigger_mode", "sequential") or "sequential"
        ).strip().lower()
        if external_trigger_mode not in {"sequential", "explicit"}:
            external_trigger_mode = "sequential"
        external_request_input_raw = getattr(view, "external_request_input", None)
        external_source = str(getattr(view, "external_source", "modbus") or "modbus").lower()
        external_request_input = (
            int(external_request_input_raw)
            if isinstance(external_request_input_raw, Integral)
            else None
        )
        if external_request_input is not None and not (1 <= external_request_input <= 8):
            external_request_input = None
        return {
            "index": index,
            "view": view,
            "image_rotation": int(getattr(view, "image_rotation", 0) or 0),
            "settle_ms": settle_ms,
            "trigger_mode": trigger_mode,
            "interval_ms": interval_ms,
            "trigger_gap_ms": trigger_gap_ms,
            "frame_source_view_id": frame_source_view_id,
            "external_trigger_mode": external_trigger_mode,
            "external_source": external_source if external_source in {"pico", "modbus"} else "modbus",
            "external_request_input": external_request_input,
            "branch_enabled": bool(getattr(view, "branch_enabled", False)),
            "branch_targets": dict(getattr(view, "branch_targets", {}) or {}),
            "branch_default_view_id": str(getattr(view, "branch_default_view_id", "") or "").strip() or None,
        }

    def _resolve_external_trigger_view(
        self,
        *,
        view_specs: list[dict[str, Any]],
        source: str,
        input_index: int,
    ) -> dict[str, Any] | None:
        """Resolve an external event without falling back to unrelated views."""
        source = str(source or "").strip().lower()
        if self.mode != "RUN" or source not in self._external_sequence_index:
            return None
        if isinstance(input_index, bool) or not isinstance(input_index, Integral):
            return None
        input_index = int(input_index)
        if not 1 <= input_index <= 8:
            return None

        if source == "pico" and not self.pico_config.is_input_enabled(input_index):
            self._logger.info(
                "[RUN] external trigger ignored source=pico input=%s reason=disabled",
                input_index,
            )
            return None

        external_specs = [
            spec for spec in view_specs
            if spec.get("trigger_mode") == "external"
            and spec.get("external_source") == source
        ]
        explicit = [
            spec for spec in external_specs
            if spec.get("external_trigger_mode") == "explicit"
            and spec.get("external_request_input") == input_index
        ]
        if len(explicit) > 1:
            self._logger.error(
                "[RUN] external trigger ignored source=%s input=%s reason=duplicate_explicit_match",
                source,
                input_index,
            )
            return None
        if explicit:
            view = explicit[0]["view"]
            self._logger.info(
                "[RUN] resolved external trigger mode=explicit source=%s input=%s view=%s",
                source,
                input_index,
                getattr(view, "name", None) or getattr(view, "id", None),
            )
            return explicit[0]

        sequential = [
            spec for spec in external_specs
            if spec.get("external_trigger_mode") == "sequential"
        ]
        if sequential:
            position = self._external_sequence_index[source] % len(sequential)
            selected = dict(sequential[position])
            self._external_sequence_index[source] = (position + 1) % len(sequential)
            selected["sequence_key"] = source
            selected["sequence_position"] = position
            selected["sequence_length"] = len(sequential)
            view = selected["view"]
            self._logger.info(
                "[RUN] resolved external trigger mode=sequential source=%s index=%s view=%s",
                source,
                position,
                getattr(view, "name", None) or getattr(view, "id", None),
            )
            return selected

        self._logger.info(
            "[RUN] external trigger ignored source=%s input=%s reason=no_matching_view",
            source,
            input_index,
        )
        return None

    def _resolve_manual_sequence_view(
        self,
        *,
        recipe_name: str,
        view_specs: list[dict[str, Any]],
        external_source: str | None = None,
    ) -> dict[str, Any] | None:
        """Let the RUN button simulate the next signal of an external sequence."""
        sequential = [
            spec for spec in view_specs
            if spec.get("trigger_mode") == "external"
            and spec.get("external_trigger_mode") == "sequential"
            and (
                external_source is None
                or spec.get("external_source") == str(external_source).lower()
            )
        ]
        if not sequential:
            return None
        position = self._manual_trigger_positions.get(recipe_name, 0) % len(sequential)
        self._manual_trigger_positions[recipe_name] = (position + 1) % len(sequential)
        selected = dict(sequential[position])
        selected["sequence_key"] = f"manual:{recipe_name}"
        selected["sequence_position"] = position
        selected["sequence_length"] = len(sequential)
        return selected

    def _request_software_trigger(self) -> None:
        """Start RUN through the same Pico MASTER timing as a line signal."""
        if self.mode != "RUN":
            self.lbl_status.setText("TRIGGER je dostupný len v RUN režime.")
            return
        if self.get_capture_mode() != "master" or not self.pico.is_available():
            self.manual_trigger("manual", None)
            return

        recipe_name = self.current_recipe_name()
        try:
            recipe_cfg = load_recipe_config(recipe_name)
            view_specs = [
                self._build_runtime_view_spec(view, index)
                for index, view in enumerate(getattr(recipe_cfg, "views", []) or [])
            ]
        except Exception as exc:
            self._logger.warning("[PICO] software trigger preparation failed: %s", exc)
            self.manual_trigger("manual", None)
            return

        active_view_id = str(self._active_view_id or "")
        explicit_spec = next(
            (
                spec for spec in view_specs
                if str(getattr(spec["view"], "id", "")) == active_view_id
                and spec.get("trigger_mode") == "external"
                and spec.get("external_source") == "pico"
                and spec.get("external_trigger_mode") == "explicit"
                and isinstance(spec.get("external_request_input"), Integral)
            ),
            None,
        )
        if explicit_spec is not None:
            selected = dict(explicit_spec)
        else:
            selected = self._resolve_manual_sequence_view(
                recipe_name=recipe_name,
                view_specs=view_specs,
                external_source="pico",
            )
            profile = getattr(selected["view"], "pico_profile", None) if selected else None
            profile = str(profile or "").upper()
            if selected is None or profile not in {"V1", "V2"}:
                self.manual_trigger("manual", None)
                return

        # Prepare the view/camera first; its shared capture path requests Pico
        # and receives a reserved frame during the pulse.
        self.manual_trigger("manual", {"spec": selected})

    def _consume_software_pico_request(self, capture_source: str) -> dict[str, Any] | None:
        pending = getattr(self, "_pending_pico_software_request", None)
        if not isinstance(pending, Mapping):
            return None
        expected = str(pending.get("source") or "").upper()
        if str(capture_source or "").upper() != expected:
            return None
        self._pending_pico_software_request = None
        spec = pending.get("spec")
        return dict(spec) if isinstance(spec, Mapping) else None

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
        capture_request_source: str = "manual",
        frame_request=None,
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
        if mode == "master":
            frame = self.cam.wait_master_frame(frame_request) if frame_request is not None else self._handle_master_flash_capture_flow(
                view=active_view,
                capture_request_source=capture_request_source,
            )
        self._logger.info(
            "[VIEW_CAPTURE] frame_capture_start source=%s capture_mode=%s active_view_id=%s settle_ms=%s",
            capture_request_source,
            mode,
            active_view_id,
            settle_ms,
        )
        if mode == "trigger" and settle_ms is not None and int(settle_ms) > 0:
            time.sleep(float(settle_ms) / 1000.0)

        if mode == "trigger":
            self._enter_run_trigger_session(trigger_gap_ms=trigger_gap_ms)
            frame = self.pico.capture_trigger(self.cam, timeout_s=1.0)

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
        # Legacy GAP is not a rising-edge period. Validated PIO profile owns timing.
        self.pico.prepare_trigger(self.cam)
        self._run_trigger_session_active = True

    def _exit_run_trigger_session(self, *, restore_master: bool = False) -> None:
        if restore_master:
            self.pico.prepare_master(self.cam)
        else:
            if self.pico.supports_pio():
                self.pico.set_session_mode("IDLE")
            self.cam.exit_trigger_session(restore_master=False)
        self._run_trigger_session_active = False

    def _apply_capture_mode(self, *, ensure_runtime_ready: bool = False) -> bool:
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
            self._capture_mode_ready = True
            return True
        except Exception as exc:
            self._capture_mode_ready = False
            self._logger.error("apply capture mode failed: %s", exc)
            self.lbl_status.setText(f"Prepnutie capture mode zlyhalo: {exc}")
            return False

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
        if requested == self.capture_mode and getattr(self, "_capture_mode_ready", False):
            self._logger.info("[CAPTURE_MODE_UI] applied=%s", self.capture_mode)
            return
        previous = self.capture_mode
        self.capture_mode = requested
        if self._apply_capture_mode(ensure_runtime_ready=True) is False:
            self.capture_mode = previous
            self._apply_capture_mode(ensure_runtime_ready=True)
            self._sync_capture_mode_ui()
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
        capture_request_source: str = "golden_wizard",
    ):
        self._logger.info("[GOLDEN_CAPTURE] using shared view capture path")
        active_view = self._resolve_active_capture_view(requested_view_id=view_id)
        settle_ms = getattr(active_view, "settle_ms", None) if active_view is not None else None
        settle_ms = int(settle_ms) if isinstance(settle_ms, Integral) else None
        if settle_ms is not None and settle_ms < 0:
            settle_ms = 0
        frame = self._capture_frame_for_view(
            trigger_mode_label=trigger_mode_label,
            master_caller="golden_wizard_capture_master",
            view=active_view,
            view_id=view_id,
            settle_ms=settle_ms,
            transform_stage="golden capture",
            image_rotation_override=image_rotation_override,
            capture_request_source=capture_request_source,
        )
        self._logger.info("[GOLDEN_CAPTURE] frame captured")
        return frame

    def _capture_frame_for_trigger(
        self,
        *,
        trigger_mode_label: str,
        trigger_gap_ms: float | None = None,
        capture_request_source: str = "manual",
    ):
        mode = self.get_capture_mode()
        active_view = self._resolve_active_capture_view(requested_view_id=self._active_view_id)
        if mode == "master":
            return self._handle_master_flash_capture_flow(
                view=active_view,
                capture_request_source=capture_request_source,
            )
        if self.capture_mode == "trigger":
            self._enter_run_trigger_session(trigger_gap_ms=trigger_gap_ms)
            return self.pico.capture_trigger(self.cam, timeout_s=1.0)
        return self.cam.last_frame(caller="run_manual_trigger_master")

    def manual_trigger(
        self,
        trigger_source: str | None = None,
        trigger_context: object | None = None,
    ):
        if self.mode != "RUN":
            self.lbl_status.setText("TRIGGER je dostupný len v RUN režime.")
            return
        context = dict(trigger_context) if isinstance(trigger_context, Mapping) else {}
        resolved_source = str(trigger_source or "manual").strip().lower()
        trigger_input_index = context.get("input_index")
        requested_spec = context.get("spec")
        self._logger.info(
            "[CAPTURE_REQUEST] trigger_source=%s trigger_input_index=%s capture_mode=%s",
            resolved_source,
            trigger_input_index,
            self.get_capture_mode(),
        )
        self._log_run_trigger_context("RUN trigger start")
        try:
            trigger_state = self._prepare_run_trigger(
                trigger_source=resolved_source,
                trigger_input_index=trigger_input_index,
                requested_spec=requested_spec,
            )
        except Exception as exc:
            self.lbl_status.setText(f"Spustenie trigger session zlyhalo: {exc}")
            self._resume_live_preview_after_trigger(False)
            return
        if trigger_state is None:
            self._resume_live_preview_after_trigger(False)
            return
        trigger_state["frame_request"] = context.get("frame_request")
        try:
            self._logger.info("trigger_click(caller=run_manual_trigger)")
            self._log_trigger_cycle("cycle_start", preview_state="paused")
            if trigger_state["recipe_cfg"] is None:
                base_frame = self._capture_frame_for_trigger(
                    trigger_mode_label="manual",
                    capture_request_source=resolved_source,
                )
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

        except Exception as exc:
            self._update_manual_trigger_feedback(force_fail=True)
            self.lbl_status.setText(f"CHYBA SNÍMANIA / KONTROLY: {exc}")
            self._signal_outputs("nok")
            import traceback; traceback.print_exc()
        finally:
            self._resume_live_preview_after_trigger(bool(trigger_state.get("was_live_enabled", False)) if trigger_state else False)

    def _prepare_run_trigger(
        self,
        *,
        trigger_source: str,
        trigger_input_index: int | None = None,
        requested_spec: object | None = None,
    ) -> dict[str, Any] | None:
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
                "trigger_source": trigger_source,
                "trigger_input_index": trigger_input_index,
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
        external_spec = None
        requested_view_id = ""
        if isinstance(requested_spec, Mapping):
            requested_view_id = str(
                getattr(requested_spec.get("view"), "id", "")
                or requested_spec.get("view_id")
                or ""
            )
            for spec in view_specs:
                if str(getattr(spec["view"], "id", "")) == requested_view_id:
                    external_spec = dict(spec)
                    for key in ("sequence_key", "sequence_position", "sequence_length"):
                        if key in requested_spec:
                            external_spec[key] = requested_spec[key]
                    all_manual = True
                    break
        is_routed_external = trigger_source in {"pico", "modbus"}
        if external_spec is None and is_routed_external and isinstance(trigger_input_index, Integral):
            external_spec = self._resolve_external_trigger_view(
                view_specs=view_specs,
                source=trigger_source,
                input_index=int(trigger_input_index),
            )
        elif external_spec is None and trigger_source == "manual":
            external_spec = self._resolve_manual_sequence_view(
                recipe_name=recipe_name,
                view_specs=view_specs,
            )
            if external_spec is not None:
                all_manual = True

        if isinstance(requested_spec, Mapping) and external_spec is None:
            self._logger.warning(
                "[RUN] software Pico trigger ignored reason=requested_view_not_found view=%s",
                requested_view_id or "unknown",
            )
            return None

        if is_routed_external and external_spec is None:
            return None
        if external_spec is not None:
            sequence_key = str(external_spec.get("sequence_key") or "")
            sequence_position = int(external_spec.get("sequence_position", 0))
            if sequence_position == 0:
                self._reset_view_sequence_state()
                per_view_statuses = {}
                if sequence_key.startswith("manual:"):
                    self._manual_trigger_statuses[recipe_name] = {}
                else:
                    self._external_sequence_statuses[sequence_key] = {}
            elif sequence_key.startswith("manual:"):
                per_view_statuses = dict(self._manual_trigger_statuses.get(recipe_name, {}))
            else:
                per_view_statuses = dict(self._external_sequence_statuses.get(sequence_key, {}))
            views_to_process = [external_spec]
            if not sequence_key.startswith("manual:"):
                self._reset_manual_trigger_progress(recipe_name)
        elif all_manual:
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

        if len(views_to_process) == 1:
            selected_view = views_to_process[0]["view"]
            selected_view_id = (
                getattr(selected_view, "id", None)
                or f"view_{views_to_process[0]['index'] + 1}"
            )
            self._active_view_id = selected_view_id
            self.view_strip.set_active(selected_view_id)

        self._last_total_cycle_time_ms = None
        self.sb_recipe_duration.setText("–")
        return {
            "gst_starts_before": gst_starts_before,
            "recipe_name": recipe_name,
            "recipe_cfg": recipe_cfg,
            "logging_enabled": bool(getattr(recipe_cfg, "logging_enabled", True)),
            "run_id": f"{recipe_name}_{uuid.uuid4().hex[:8]}",
            "view_specs": view_specs,
            "views_to_process": views_to_process,
            "all_manual": all_manual,
            "sequence_key": str(views_to_process[0].get("sequence_key") or "")
            if len(views_to_process) == 1 else "",
            "per_view_statuses": per_view_statuses,
            "ignored_for_aggregation": set(),
            "last_preview_frame": None,
            "last_view_id": None,
            "captured_frames": {},
            "pending_overlays": {},
            "trigger_start_ts": time.monotonic(),
            "spec_lookup": {
                getattr(spec["view"], "id", None) or f"view_{spec['index']+1}": spec
                for spec in view_specs
            },
            "base_camera_state": base_camera_state,
            "trigger_source": trigger_source,
            "trigger_input_index": (
                int(trigger_input_index) if trigger_input_index is not None else None
            ),
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
        inspection_finished_ts: float | None = None
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
                capture_request_source=trigger_state.get("trigger_source", "manual"),
                frame_request=trigger_state.pop("frame_request", None),
            )
            self._update_manual_trigger_feedback()
            if view_frame is None:
                self._update_manual_trigger_feedback(force_fail=True)
                self._apply_run_status_style("nok")
                self.lbl_status.setText("CHYBA SNÍMANIA")
                self._run_status_message.setText("Žiadny snímok z kamery")
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
            self._run_overlay_cache.pop(self._view_storage_key(view_id), None)
            last_preview_frame = view_frame_u8.copy()
            inspection_finished_ts = time.monotonic()
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
            inspection_finished_ts = time.monotonic()
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
                trigger_state["pending_overlays"][view_id] = (
                    context_frame,
                    view,
                    result,
                )
                context_frame = apply_view_image_transform(
                    context_frame,
                    view,
                    stage="preview",
                )
            last_preview_frame = context_frame.copy() if isinstance(context_frame, np.ndarray) else view_frame_u8.copy()

        per_view_statuses[view_id] = status
        if all_manual:
            self._manual_trigger_statuses[recipe_name] = dict(per_view_statuses)
        sequence_key = trigger_state.get("sequence_key")
        if sequence_key and not str(sequence_key).startswith("manual:"):
            self._external_sequence_statuses[str(sequence_key)] = dict(per_view_statuses)

        result_time_ts = inspection_finished_ts or time.monotonic()
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
        relevant_reports: list[Mapping[str, Any]] = []
        for candidate_view_id, candidate_status in per_view_statuses.items():
            if candidate_status != aggregated_status:
                continue
            state = self._view_states.get(candidate_view_id, {})
            reports = state.get("reports", []) if isinstance(state, Mapping) else []
            if isinstance(reports, Sequence):
                relevant_reports.extend(
                    entry for entry in reports if isinstance(entry, Mapping)
                )
        self._update_operator_status_message(aggregated_status, relevant_reports)
        self._signal_outputs(aggregated_status)

        for overlay_view_id, overlay_source in trigger_state.get("pending_overlays", {}).items():
            frame, view, result = overlay_source
            self._cache_run_overlays(overlay_view_id, frame, view, result)

        overlay_view_id = trigger_state.get("last_view_id")
        rendered_overlay_frame = self._render_run_overlay_frame(overlay_view_id)
        if rendered_overlay_frame is not None:
            trigger_state["last_preview_frame"] = rendered_overlay_frame
            self._set_last_view_frame(overlay_view_id, rendered_overlay_frame)

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
            pico=self.pico,
            trigger_fn=None,
            get_capture_mode=self.get_capture_mode,
            capture_frame_for_golden=self.capture_frame_for_golden,
            authorize_write=lambda: authorize_recipe_write(self, self.security),
        )
        dlg.exec()
        self._refresh_manual_light()
        self._apply_capture_mode(ensure_runtime_ready=True)
        self._reset_manual_trigger_progress(self.current_recipe_name())
        self._reset_external_sequence_state()
        self._refresh_views()
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

    def open_modbus_wizard(self):
        self._exit_run_trigger_session(restore_master=False)
        dlg = ModbusWizard(self.modbus, self)
        dlg.resize(760, 640)
        dlg.exec()
        self._apply_capture_mode(ensure_runtime_ready=True)

    def open_pico_wizard(self):
        self._exit_run_trigger_session(restore_master=False)
        dlg = PicoWizard(self.pico, self.pico_config, self)
        dlg.resize(680, 700)
        dlg.exec()
        self._apply_capture_mode(ensure_runtime_ready=True)

    def open_change_log(self):
        dialog = RecipeChangeLogDialog(self.recipes.audit, self)
        dialog.exec()

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

    def _set_manual_light_ui(self, enabled: bool | None) -> None:
        self.btn_manual_light.blockSignals(True)
        if enabled is None:
            self.btn_manual_light.setText("Svetlo: neznámy stav")
        else:
            self.btn_manual_light.setChecked(enabled)
            self.btn_manual_light.setText("Svetlo zapnuté" if enabled else "Svetlo vypnuté")
        self.btn_manual_light.blockSignals(False)

    def _refresh_manual_light(self) -> None:
        self._set_manual_light_ui(self.pico.manual_light_status())

    def _toggle_manual_light(self, checked: bool) -> None:
        previous = not checked
        if self.pico.set_manual_light(checked):
            self._set_manual_light_ui(checked)
            return
        self._set_manual_light_ui(previous)
        QMessageBox.critical(
            self, "Chyba", self.pico.last_error or "Pico nie je dostupné"
        )

    def _handle_modbus_trigger(self, input_index: int | None = None):
        self._handle_external_trigger("Modbus", input_index=input_index)

    def _handle_pico_trigger(self, capture_source: str) -> None:
        if self.mode != "RUN":
            return
        source = str(capture_source or "").upper().strip()
        if source in {"V1", "V2"}:
            requested_spec = self._consume_software_pico_request(source)
            if requested_spec is None:
                self._logger.warning("[PICO] ignored unexpected capture event source=%s", source)
                return
            self._logger.info("[PICO] software capture event profile=%s", source)
            self._handle_external_trigger("PicoSoftware", requested_spec=requested_spec)
            return

        match = re.fullmatch(r"IN([1-8])", source)
        if match is None:
            self._logger.warning("[PICO] ignored invalid capture event source=%r", capture_source)
            return
        input_index = int(match.group(1))
        requested_spec = self._consume_software_pico_request(source)
        if requested_spec is not None:
            self._logger.info("[PICO] software capture event input=%s", input_index)
            self._handle_external_trigger(
                "PicoSoftware",
                input_index=input_index,
                requested_spec=requested_spec,
            )
            return
        self._logger.info("[PICO] physical capture event input=%s", input_index)
        self._handle_external_trigger("Pico", input_index=input_index)

    def _handle_external_trigger(
        self,
        source: str,
        *,
        input_index: int | None = None,
        requested_spec: dict[str, Any] | None = None,
    ) -> None:
        self._logger.info("[RUN] %s trigger received input=%s mode=%s", source, input_index, self.mode)
        if self.mode != "RUN":
            return
        resolved_source = str(source or "external").strip().lower()
        if resolved_source in {"modbus", "pico"} and isinstance(input_index, Integral):
            parsed_input = int(input_index)
            parsed_input = parsed_input if 1 <= parsed_input <= 8 else None
        else:
            parsed_input = None
        self.external_triggered.emit(
            resolved_source,
            {"input_index": parsed_input, "spec": requested_spec,
             "frame_request": self.cam.arm_master_frame()
             if resolved_source == "pico" and self.get_capture_mode() == "master" else None},
        )

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

    def _cache_run_overlays(
        self,
        view_id: str,
        frame: np.ndarray,
        view: Any,
        result: Any,
    ) -> None:
        palette = overlay_utils.default_palette()
        roi_items_by_tool: dict[str, list[overlay_utils.OverlayItem]] = {}
        error_items: list[overlay_utils.OverlayItem] = []

        for index, report in enumerate(getattr(result, "per_tool", []) or []):
            tool = getattr(report, "tool", None)
            tool_id = str(getattr(report, "tool_id", "") or "")
            if tool is None or not tool_id:
                continue
            color = palette[index % len(palette)]
            tool_name = str(getattr(tool, "name", "") or getattr(tool, "type", "Nástroj"))
            tool_roi_items = overlay_utils.tool_overlay_items(
                tool,
                color=color,
                label=tool_name,
                include_ignore_mask=False,
            )
            roi_items = [item for item in tool_roi_items if item.z_index == 20]
            for item in roi_items:
                item.fill_alpha = None
            if roi_items:
                roi_items_by_tool[tool_id] = roi_items

            if str(getattr(report, "status", "") or "").lower() != "nok":
                continue
            tool_error_added = False
            failure_label = nok_label(report)
            for display_item in getattr(report, "overlay_items", []) or []:
                if getattr(display_item, "z_index", 0) >= 30:
                    error_items.append(replace(display_item, label=failure_label))
                    tool_error_added = True
            metrics = getattr(report, "metrics", {})
            metric_values = metrics if isinstance(metrics, Mapping) else {}
            blobs = metric_values.get("blobs", [])
            if not tool_error_added and isinstance(blobs, Sequence):
                for blob_index, blob in enumerate(blobs, start=1):
                    if not isinstance(blob, Mapping):
                        continue
                    try:
                        rect = (
                            int(blob.get("image_x", blob.get("x", 0))),
                            int(blob.get("image_y", blob.get("y", 0))),
                            int(blob.get("width", 0)),
                            int(blob.get("height", 0)),
                        )
                    except (TypeError, ValueError):
                        continue
                    if rect[2] <= 0 or rect[3] <= 0:
                        continue
                    error_items.append(
                        overlay_utils.OverlayItem.from_rect(
                            rect,
                            color=(68, 68, 239),
                            thickness=4,
                            alpha=255,
                            z_index=50,
                            label=failure_label,
                        )
                    )
                    tool_error_added = True

            if not tool_error_added:
                failed_roi_items = overlay_utils.tool_overlay_items(
                    tool,
                    color=(68, 68, 239),
                    label=failure_label,
                    include_ignore_mask=False,
                )
                for item in failed_roi_items:
                    if item.z_index == 20:
                        item.fill_alpha = None
                        item.z_index = 45
                        error_items.append(item)

        self._run_overlay_cache[self._view_storage_key(view_id)] = {
            "frame": frame.copy(),
            "view": view,
            "roi_items": roi_items_by_tool,
            "error_items": error_items,
        }

    def _render_run_overlay_frame(self, view_id: str | None) -> np.ndarray | None:
        entry = self._run_overlay_cache.get(self._view_storage_key(view_id))
        if not isinstance(entry, Mapping):
            return None
        frame = entry.get("frame")
        if not isinstance(frame, np.ndarray):
            return None

        items = list(entry.get("error_items", []) or [])
        if self.chk_show_roi.isChecked():
            roi_items = entry.get("roi_items", {})
            if isinstance(roi_items, Mapping):
                selected_tool_id = self.cmb_roi_tool.currentData()
                if selected_tool_id:
                    items.extend(roi_items.get(str(selected_tool_id), []) or [])
                else:
                    for tool_items in roi_items.values():
                        items.extend(tool_items or [])

        rendered = (
            overlay_utils.draw_overlay_items(frame, items)
            if items else frame.copy()
        )
        return apply_view_image_transform(
            rendered,
            entry.get("view"),
            stage="preview",
        )

    def _on_run_overlay_controls_changed(self, *_args) -> None:
        self.cmb_roi_tool.setEnabled(
            self.chk_show_roi.isChecked() and self.cmb_roi_tool.count() > 0
        )
        view_id = self._active_view_id
        rendered = self._render_run_overlay_frame(view_id)
        if rendered is None:
            return
        self._set_last_view_frame(view_id, rendered)
        self._last_trigger_frame = self._clone_frame(rendered)
        self._last_trigger_view_id = view_id
        if not self.live_enabled:
            self._show_gray_or_bgr(self.live_view, rendered)

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
            yield_raw = st.get("yield")
            try:
                yield_percent = float(yield_raw)
                if yield_percent <= 1.0:
                    yield_percent *= 100.0
                yield_text = f"{yield_percent:.1f} %"
            except (TypeError, ValueError):
                yield_text = "–"
            self.sb_yield.setText(f"Úspešnosť: {yield_text}")
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
                self._format_total_test_duration(recipe_cycle_ms)
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

            if status is not None:
                self._update_operator_status_message(status, per_tool or state.get("reports", []))

            if active_view == self._active_view_id:
                self._update_metrics_panel()
        except Exception:
            pass

    def _update_operator_status_message(
        self,
        status: str,
        reports: Sequence[Mapping[str, Any]],
    ) -> None:
        normalized = str(status or "").lower()
        if normalized == "ok":
            self._run_status_message.setText("Kontrola úspešná")
            return
        failed = next(
            (entry for entry in reports if str(entry.get("status") or "").lower() == "nok"),
            None,
        )
        if failed is None:
            self._run_status_message.setText(
                "Kontrola vyžaduje overenie"
                if normalized == "warn" else "Vyžaduje pozornosť operátora"
            )
            return
        name = str(failed.get("name") or "Nástroj")
        metrics = failed.get("metrics")
        metric_values = metrics if isinstance(metrics, Mapping) else {}
        if bool(metric_values.get("residual_detected")):
            message = f"Nájdený zvyšok · {name}"
        elif bool(metric_values.get("inspection_fault")):
            message = f"Kontrola nie je pripravená · {name}"
        else:
            message = f"NOK · {name}"
        self._run_status_message.setText(message)

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
            self._metric_name_labels.clear()
            self.metrics_container.adjustSize()

            if not rows:
                self._metrics_placeholder.setParent(self.metrics_container)
                self._metrics_placeholder.setText("Žiadne dáta")
                self.metrics_layout.addWidget(self._metrics_placeholder, 0, 0, 1, 2)
                self._metrics_placeholder.show()
                self.metrics_container.adjustSize()
                return

            self._metrics_placeholder.hide()
            for row_index, (label_text, value_text) in enumerate(rows):
                full_label = str(label_text)
                name_label = QLabel(full_label)
                name_label.setProperty("role", "resultName")
                name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                name_label.setWordWrap(False)
                name_label.setToolTip(full_label)
                name_label.setMaximumWidth(240)
                name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                value_label = QLabel(value_text if value_text else "-")
                value_label.setProperty("role", "resultValue")
                normalized_value = str(value_text or "").strip().lower()
                if normalized_value in {"ok", "warn", "nok"}:
                    value_label.setProperty("status", normalized_value)
                value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                value_label.setWordWrap(False)
                value_label.setMinimumWidth(110)
                value_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                self.metrics_layout.addWidget(name_label, row_index, 0)
                self.metrics_layout.addWidget(value_label, row_index, 1)
                self._metrics_widgets.extend([name_label, value_label])
                self._metric_name_labels.append((name_label, full_label))

            self._refresh_metric_name_elision()
            self.metrics_container.adjustSize()

    def _refresh_metric_name_elision(self) -> None:
        if not self._metric_name_labels:
            return
        viewport_width = self.metrics_scroll.viewport().width()
        spacing = self.metrics_layout.horizontalSpacing()
        margins = self.metrics_layout.contentsMargins()
        reserved = self.metrics_layout.columnMinimumWidth(1)
        available = viewport_width - reserved - spacing - margins.left() - margins.right()
        target_width = max(80, available)
        for label, full_text in self._metric_name_labels:
            label.setMaximumWidth(target_width)
            metrics = label.fontMetrics()
            elided = metrics.elidedText(full_text, Qt.ElideRight, target_width)
            label.setText(elided)

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
        for report in reports:
            name = str(report.get("name") or report.get("id") or "Tool")
            tool_status = str(report.get("status") or "").upper() or "—"
            rows.append((name, tool_status))
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
            selection_name = str(selection_map.get("name") or "").strip().lower()
            if selection_name:
                report = next(
                    (r for r in reports if str(r.get("name") or "").strip().lower() == selection_name),
                    None,
                )
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

        metrics: dict[str, Any] = {}
        if isinstance(report.get("metrics"), Mapping):
            metrics = dict(report["metrics"])
        print("METRICS:", metrics)
        metrics.pop("latency_ms", None)

        tool_type = str(report.get("type") or selection_map.get("type") or "")
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
                key = str(getattr(spec, "key", "") or "").strip()
                if not key:
                    continue
                value = metrics.get(key)
                if key not in metrics:
                    continue
                label = (getattr(spec, "description", "") or key or "Metric").strip()
                unit = getattr(spec, "unit", None)
                if unit:
                    label = f"{label} [{unit}]"
                rows.append((label, "-" if value is None else self._format_metric_value(value)))
                metrics.pop(key, None)

        for key in sorted(metrics.keys()):
            value = metrics.get(key)
            rows.append((str(key), "-" if value is None else self._format_metric_value(value)))

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

        # Persist geometry and thresholds from the executed tool, never rebuild
        # historical overlays from a subsequently edited recipe.
        history_overlays = []
        if tool is not None:
            geometry = overlay_utils.tool_overlay_items(
                tool, color=(255, 170, 73), include_ignore_mask=False,
            )
            for item in [item for item in geometry if item.z_index == 20] + list(getattr(report, "overlay_items", []) or []):
                if item.kind not in {"rect", "polygon", "polyline"}:
                    continue
                history_overlays.append({
                    "rect": self._simplify_value(item.rect),
                    "points": item.points.tolist() if item.points is not None else None,
                    "closed": item.closed,
                    "error": item.z_index >= 30 and str(getattr(report, "status", "")).lower() == "nok",
                })
            if str(getattr(report, "status", "")).lower() == "nok":
                for blob in (raw_metrics or {}).get("blobs", []):
                    if isinstance(blob, Mapping) and "image_x" in blob and "image_y" in blob:
                        history_overlays.append({
                            "rect": self._simplify_value([blob["image_x"], blob["image_y"], blob.get("width", 0), blob.get("height", 0)]),
                            "error": True,
                        })

        return {
            "id": tool_id,
            "name": tool_name or tool_id or "Tool",
            "type": tool_type or diagnostics.get("type"),
            "order": getattr(report, "order", tool_order),
            "status": getattr(report, "status", None),
            "latency_ms": latency_value,
            "metrics": metrics,
            "diagnostics": diagnostics,
            "thresholds": self._simplify_value(getattr(getattr(tool, "thresholds", None), "values", {})),
            "roi": tool.roi.to_dict() if tool is not None else None,
            "history_overlays": history_overlays,
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
            if self.get_capture_mode() == "master":
                self.cam.prepare_master_capture()
        except Exception as exc:
            self.lbl_status.setText(f"Načítanie profilu kamery zlyhalo: {exc}")
            return


    def _refresh_views(self):
        self._golden_cache.clear()
        self._run_overlay_cache.clear()
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
        roi_entries: list[dict[str, Any]] = []
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
            if tool.roi.rect() is not None:
                roi_entries.append(entries[-1])
        self._tool_selector_items = entries

        self.cmb_roi_tool.blockSignals(True)
        self.cmb_roi_tool.clear()
        if roi_entries:
            self.cmb_roi_tool.addItem("Všetky nástroje", None)
            for entry in roi_entries:
                self.cmb_roi_tool.addItem(entry["name"], entry["id"])
        else:
            self.cmb_roi_tool.addItem("Žiadne ROI", None)
            self.chk_show_roi.setChecked(False)
        self.cmb_roi_tool.setEnabled(bool(roi_entries) and self.chk_show_roi.isChecked())
        self.chk_show_roi.setEnabled(bool(roi_entries))
        self.cmb_roi_tool.blockSignals(False)
        self._on_run_overlay_controls_changed()

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
            self.cmb_tool.setCurrentIndex(0)
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
            self.pico.quiesce()
        except Exception:
            self._logger.exception("Pico sa nepodarilo zastaviť pred vypnutím")
        try:
            self.cam.stop(caller="main_window_power_action")
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

    def _apply_debug_overlay_setting(self) -> None:
        settings = settings_service.get_session_settings()
        enabled = bool(getattr(settings, "show_performance_debug_overlay", False))
        self.chk_debug_overlay.blockSignals(True)
        self.chk_debug_overlay.setChecked(enabled)
        self.chk_debug_overlay.blockSignals(False)
        self._set_debug_overlay_enabled(enabled)

    def _on_debug_overlay_toggled(self, enabled: bool) -> None:
        settings_service.update_session_settings(show_performance_debug_overlay=enabled)
        self._set_debug_overlay_enabled(bool(enabled))

    def _set_debug_overlay_enabled(self, enabled: bool) -> None:
        if self._overlay_enabled == bool(enabled):
            return
        self._overlay_enabled = bool(enabled)
        if self._overlay_enabled:
            self._debug_overlay.place_top_left(margin=10)
            self._debug_overlay.show()
            self._debug_overlay.raise_()
            self._jetson_stats_service.start()
        else:
            self._jetson_stats_service.stop()
            self._debug_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_debug_overlay"):
            self._debug_overlay.place_top_left(margin=10)
            self._debug_overlay.raise_()
        self._refresh_metric_name_elision()

    def closeEvent(self, e):
        try:
            self.pico.quiesce()
        except Exception:
            self._logger.exception("Pico sa nepodarilo zastaviť pri zatvorení")
        try:
            self._jetson_stats_service.stop()
            self.cam.stop(caller="main_window_close")
            self.modbus.close()
            self.pico.close()
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
        if hasattr(self, "setup_recipe_name"):
            self.setup_recipe_name.setText(str(cur or "–"))

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
            self._reset_external_sequence_state()
            self.recipes.load(name)
            self.tool = self.recipes.tool
            if hasattr(self, "setup_recipe_name"):
                self.setup_recipe_name.setText(name)
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
        except Exception as e:
            self.lbl_status.setText(f"Load failed: {e}")

    def on_recipe_new(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Nový recept", "Názov receptu:")
        if not ok or not name.strip():
            return
        if not authorize_recipe_write(self, self.security):
            return
        name = name.strip()
        self._reset_external_sequence_state()
        self.recipes.create(name)
        self._refresh_recipe_list()
        self.recipes.load(name)
        self.tool = self.recipes.tool
        self.setup_recipe_name.setText(name)
        self._persist_last_recipe(name)
        self._refresh_views()
        self._reset_manual_trigger_progress(name)
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

    def on_recipe_rename(self):
        from PySide6.QtWidgets import QInputDialog
        old = self.current_recipe_name()
        new, ok = QInputDialog.getText(self, "Premenovať recept", f"Nový názov pre '{old}':")
        if not ok or not new.strip():
            return
        if not authorize_recipe_write(self, self.security):
            return
        new = new.strip()
        self._reset_external_sequence_state()
        self.recipes.rename(old, new)
        self._reset_manual_trigger_progress(old)
        self._refresh_recipe_list()
        self.recipes.load(new)
        self.tool = self.recipes.tool
        self.setup_recipe_name.setText(new)
        self._persist_last_recipe(new)
        self._refresh_views()
        self._reset_manual_trigger_progress(new)
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

    def on_recipe_delete(self):
        from PySide6.QtWidgets import QMessageBox
        name = self.current_recipe_name()
        if name == "default":
            QMessageBox.warning(self, "Upozornenie", "Recept 'default' nie je možné zmazať.")
            return
        r = QMessageBox.question(self, "Zmazať recept", f"Naozaj zmazať '{name}'?")
        if r != QMessageBox.Yes:
            return
        if not authorize_recipe_write(self, self.security):
            return
        self._reset_external_sequence_state()
        self.recipes.delete(name)
        self._reset_manual_trigger_progress(name)
        self._refresh_recipe_list()
        self.recipes.load("default")
        self.tool = self.recipes.tool
        self.setup_recipe_name.setText("default")
        self._persist_last_recipe("default")
        self._refresh_views()
        self._reset_manual_trigger_progress("default")
        self._reload_results_strip()
        self._refresh_tool_selector()
        self._update_sidebar(view_id=self._active_view_id)

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

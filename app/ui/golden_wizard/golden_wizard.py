from app.ui.golden_wizard.preview_presenter import GoldenPreview
from app.ui.golden_wizard.view_controller import GoldenViews
from app.services.learning_dataset import LearningDataset
from app.ui.golden_wizard.tool_config_panel import ToolConfigPanel
from app.ui.golden_wizard.tools_table import ToolsTableWidget
from app.utils.tool_labels import tool_display_name
# app/ui/golden_wizard.py
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QImage, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit, QMessageBox, QCheckBox, QTableWidgetItem, QWidget, QHeaderView, QAbstractItemView, QSizePolicy, QFileDialog, QToolButton, QScrollArea, QSplitter, QFrame

import os
from app.ui.responsive import WrapLayout
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence
from functools import partial

import numpy as np
import cv2

from app.utils import overlay as overlay_utils

from app.ui.draw_view import DrawView
from app.ui.roi_mask_editor import MASK_WARN_PIXELS, MAX_MASK_PIXELS, MAX_ROI_PIXELS, ROI_WARN_PIXELS, LocatorROIEditor
from app.services.storage_service import save_golden
from app.models.regions import Region, validate_cardinality
from app.models.learning_contract import SAMPLE_PREPARATION_VERSION
from app.services.learning_context import learning_signature, samples_match, bind_samples
from app.models.schema import RecipeData, RecipeV2, Tool, RecipeView, ToolMask, ToolParams, ToolRoi, ToolThresholds
from app.services.recipe_service import RecipeService
from app.services.roi_geometry import roi_local_exclusion_mask
from app.services.tool_pipeline import run_tool_test
from app.services.tools.edge_profile_deviation import detect_guided_reference_edge
from app.services.presence_absence_v2_service import compute_roi_hash, ensure_assets_dirs, evaluate_dataset, load_samples, optimize_sensitivity, reset_learning_assets, resolve_assets_dir, save_sample, sensitivity_to_score_threshold
from app.utils.tool_identity import compute_tool_identity
from app.services.view_images import apply_view_image_transform, view_image_rotation
from app.services.camera_profiles import apply_camera_state, apply_view_camera_profile, snapshot_camera_state
from app.ui.golden_wizard.session_settings_dialog import SessionSettingsDialog
from app.ui.golden_wizard.style import GOLDEN_WIZARD_STYLE
from app.ui.golden_wizard.tool_catalog_dialog import ToolCatalogDialog, make_catalog_tool
from app.ui.golden_wizard.presence_v2_sample_capture_dialog import PresenceV2SampleCaptureDialog


_STATISTICAL_PRESENCE_TYPES = frozenset({
    "presence.absence_v2",
    "mold.protection_v1",
})
from app.ui.golden_wizard.view_config_dialog import _DEFAULT_CAMERA_RESOLUTIONS

if TYPE_CHECKING:
    from app.services.modbus_service import ModbusService
    from app.services.pico_service import PicoService


class GoldenWizard(QDialog):
    """
    Jediné miesto na nastavenie nástroja:
      1) Získať/načítať GOLDEN (1 ks)
      2) Zbierať validáciu (OK/NOK)
      3) Uložiť recept (golden.png + regions.json)
      4) Live feed (ON/OFF) – samostatný náhľad (bez kreslenia)
    """
    def __init__(
        self,
        camera,
        recipes: RecipeService,
        parent=None,
        *,
        modbus: "ModbusService | None" = None,
        pico: "PicoService | None" = None,
        trigger_fn: Optional[Callable[[], None]] = None,
        get_capture_mode: Optional[Callable[[], str]] = None,
        capture_frame_for_golden: Optional[Callable[..., Any]] = None,
        publish_flash_to_pico: Optional[Callable[[str], tuple[bool, str]]] = None,
        authorize_write: Optional[Callable[[], bool]] = None,
        session_settings=None,
    ):
        super().__init__(parent)
        from app.services.settings_service import SessionSettingsStore
        self.session_settings = session_settings or SessionSettingsStore(recipes.base / "logs")
        self._preview_presenter = GoldenPreview(self)
        self._view_controller = GoldenViews(self)
        self._logger = logging.getLogger(__name__)

        self._base_title = "Golden WIZARD"
        self.setWindowTitle(self._base_title)
        self.setModal(True)
        self.cam = camera
        self.recipes = recipes
        self.modbus = modbus
        self.pico = pico
        self._trigger_fn = trigger_fn
        self._get_capture_mode = get_capture_mode
        self._capture_frame_for_golden = capture_frame_for_golden
        self._publish_flash_to_pico = publish_flash_to_pico
        self._authorize_write = authorize_write or (lambda: True)
        self.current_img = None

        self._saved_snapshots: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._dirty_views: dict[str, dict[str, bool]] = {}
        self._view_states: dict[str, dict[str, Any]] = {}
        self._last_tool_results: dict[tuple[str, int, str], tuple[Any, ...]] = {}
        self._presence_tuning_results: dict[tuple[str, int, str], dict[str, Any]] = {}
        self._views: list[RecipeView] = []
        self._active_view_id: Optional[str] = None
        self._updating_view_selector = False

        # --- Live infra (len video label, bez kreslenia) ---

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(50)  # ~20 FPS
        self._live_timer.timeout.connect(self._live_tick)
        self._live_on = False

        # ---- Horná lišta ----
        current_recipe = getattr(self.recipes.tool, "recipe", "default")
        self.recipe_name = QLineEdit(current_recipe, self)
        self.chk_pose    = QCheckBox("Zapnúť zarovnanie pozície")
        self.chk_pose.setChecked(getattr(self.recipes.tool, "pose_enabled", False))
        self._updating_logging_checkbox = False
        self.chk_logging = QCheckBox("Ukladať históriu behov", self)
        self.chk_logging.setToolTip(
            "Ak je vypnuté, neukladajú sa logy, thumbnaily ani meta dáta na disk."
        )
        try:
            self.chk_logging.setChecked(
                bool(self.recipes.get_logging_enabled(current_recipe))
            )
        except Exception as exc:
            self._logger.warning("get_logging_enabled failed for %s: %s", current_recipe, exc)
            self.chk_logging.setChecked(True)

        self._view_selector = QComboBox(self)
        self._view_selector.currentIndexChanged.connect(self._on_view_changed)
        self.btn_add_view = QPushButton("Pridať pohľad", self)
        self.btn_add_view.clicked.connect(self._on_add_view)
        self.btn_edit_view = QPushButton("Upraviť pohľad", self)
        self.btn_edit_view.clicked.connect(self._on_edit_view)
        self.btn_edit_view.setEnabled(False)
        self.btn_remove_view = QPushButton("Odstrániť pohľad", self)
        self.btn_remove_view.clicked.connect(self._on_remove_view)

        self._updating_policy_combo = False
        self._current_locator_failure_policy = "continue_without_alignment"
        self.failure_policy_combo = QComboBox(self)
        self.failure_policy_combo.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.failure_policy_combo.addItem(
            "Pokračovať bez zarovnania", "continue_without_alignment"
        )
        self.failure_policy_combo.addItem("Zlyhať pipeline", "fail")
        self.failure_policy_combo.setToolTip(
            "Ako má pipeline reagovať, keď locator nezarovná frame."
        )

        self.btn_add_tool = QPushButton("Pridať nástroj")
        self.btn_add_tool.clicked.connect(self._open_tool_catalog)

        # Toggle Live
        self.btn_live = QPushButton("Live vypnuté")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._toggle_live)

        self.btn_manual_light = QPushButton("Svetlo: neznámy stav")
        self.btn_manual_light.setCheckable(True)
        self.btn_manual_light.clicked.connect(self._toggle_manual_light)

        self._session_settings_button = QToolButton(self)
        self._session_settings_button.setText("⚙")
        self._session_settings_button.setToolTip("Nastavenia relácie")
        self._session_settings_button.setAutoRaise(True)
        self._session_settings_button.clicked.connect(self._open_session_settings)

        top_primary = WrapLayout()
        top_primary.setContentsMargins(0, 0, 0, 0)
        top_primary.setSpacing(8)
        top_primary.addWidget(QLabel("Recept:"))
        top_primary.addWidget(self.recipe_name)
        top_primary.addWidget(QLabel("Pohľad:", self))
        top_primary.addWidget(self._view_selector)
        top_primary.addWidget(self.btn_add_view)
        top_primary.addWidget(self.btn_edit_view)
        top_primary.addWidget(self.btn_remove_view)
        top_primary.addWidget(self.btn_live)
        top_primary.addWidget(self.btn_manual_light)

        top_secondary = WrapLayout()
        top_secondary.setContentsMargins(0, 0, 0, 0)
        top_secondary.setSpacing(8)
        top_secondary.addWidget(self.chk_pose)
        top_secondary.addWidget(self.chk_logging)
        top_secondary.addWidget(QLabel("Zlyhanie locatora:", self))
        top_secondary.addWidget(self.failure_policy_combo)
        top_secondary.addWidget(self._session_settings_button)

        self.chk_filtered_roi = QCheckBox("Zobraziť filtrované ROI", self)
        self.lbl_filtered_roi = QLabel("", self)
        self.lbl_filtered_roi.setWordWrap(True)
        top_secondary.addWidget(self.chk_filtered_roi)
        top_secondary.addWidget(self.lbl_filtered_roi)
        self._filtered_roi_timer = QTimer(self)
        self._filtered_roi_timer.setSingleShot(True)
        self._filtered_roi_timer.setInterval(180)
        self._filtered_roi_timer.timeout.connect(self._refresh_filtered_roi)
        self.chk_filtered_roi.toggled.connect(lambda: self._filtered_roi_timer.start())

        # ---- Dva režimy zobrazenia ----
        # 1) Live LABEL (video) – používa sa len pri Live zapnuté
        self.live_lbl = QLabel("—")
        self.live_lbl.setAlignment(Qt.AlignCenter)
        self.live_lbl.setMinimumHeight(180)
        self.live_lbl.hide()  # default skryté

        # 2) DrawView (kreslenie) – používa sa pri Live vypnuté
        self.view = DrawView(self)
        self.roi_editor = LocatorROIEditor(self)
        self._syncing_workspace_roi = False
        self.live_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.roi_editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.view.hide()

        # ---- Ovládacie tlačidlá ----
        btn_cap_golden   = QPushButton("Získať GOLDEN z kamery")
        btn_load_golden  = QPushButton("Načítať GOLDEN z disku")
        self.btn_save_tool = QPushButton("Uložiť nástroj")
        self.btn_test_tool = QPushButton("Otestovať")
        self.btn_test_tool.setEnabled(False)
        self.btn_publish_recipe = QPushButton("Publikovať / aktualizovať recept")
        self.btn_publish_recipe.setProperty("role", "primary")
        self.btn_close_wizard = QPushButton("Zavrieť Golden Wizard")
        self.btn_close_wizard.setProperty("role", "destructive")

        buttons = WrapLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addWidget(btn_cap_golden)
        buttons.addWidget(btn_load_golden)
        buttons.addWidget(self.btn_save_tool)
        buttons.addWidget(self.btn_test_tool)
        self._publish_state_label = QLabel("", self)
        self._publish_state_label.setStyleSheet("color: #999; font-style: italic;")
        self._publish_state_label.setMinimumWidth(100)
        self._publish_state_label.setMaximumWidth(160)
        self._publish_state_label.setAlignment(Qt.AlignCenter)
        buttons.addWidget(self._publish_state_label)
        buttons.addWidget(self.btn_publish_recipe)
        buttons.addWidget(self.btn_close_wizard)

        # ---- Layout ----
        self._tool_panel = ToolConfigPanel(self)
        self._tool_panel.setMinimumWidth(280)
        self._tool_panel.setMaximumWidth(320)
        self._tool_panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self.tools_table = ToolsTableWidget(0, 5, self)
        self.tools_table.setHorizontalHeaderLabels(
            ["Poradie", "Názov", "Typ", "Povolený", "Akcie"]
        )
        header = self.tools_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tools_table.verticalHeader().setVisible(False)
        self.tools_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tools_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tools_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tools_table.setDragEnabled(True)
        self.tools_table.setAcceptDrops(True)
        self.tools_table.setDropIndicatorShown(True)
        self.tools_table.setDragDropMode(QAbstractItemView.InternalMove)
        self.tools_table.setDragDropOverwriteMode(False)
        self.tools_table.setDefaultDropAction(Qt.MoveAction)
        self.tools_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tools_table.setColumnHidden(0, True)
        self.tools_table.setColumnHidden(4, True)
        tools_label = QLabel("Nástroje v recepte:", self)
        header_item = self.tools_table.horizontalHeaderItem(2)
        if header_item:
            header_item.setToolTip("Locator nástroje musia bežať pred analyzátormi.")

        self.locator_hint_label = QLabel(
            "Locator nástroje (zvýraznené) sú automaticky spúšťané ako prvé v pipeline.",
            self,
        )
        self.locator_hint_label.setWordWrap(True)
        self.locator_hint_label.setStyleSheet("color: #555; font-style: italic;")

        self.locator_policy_banner = QLabel("", self)
        self.locator_policy_banner.setWordWrap(True)
        self.locator_policy_banner.setStyleSheet(
            "background-color: #2b2518; border: 1px solid #6f581d; "
            "color: #d29922; padding: 8px; border-radius: 4px; font-weight: 500;"
        )
        self.locator_policy_banner.setVisible(False)

        left_panel = QFrame(self)
        left_panel.setObjectName("workspacePanel")
        left_panel.setMinimumWidth(220)
        left_panel.setMaximumWidth(420)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)
        tools_label.setText("NÁSTROJE")
        tools_label.setProperty("role", "panelHeader")
        self.btn_add_tool.setText("+ Pridať nástroj")
        left_layout.addWidget(tools_label)
        left_layout.addWidget(self.btn_add_tool)
        left_layout.addWidget(self.tools_table, 1)
        selected_actions = QHBoxLayout()
        self._btn_delete_selected = QToolButton(left_panel)
        self._btn_delete_selected.setText("×")
        self._btn_delete_selected.setToolTip("Odstrániť vybraný nástroj")
        self._btn_delete_selected.clicked.connect(
            lambda: self._delete_tool(self.tools_table.currentRow())
        )
        selected_actions.addStretch(1)
        selected_actions.addWidget(self._btn_delete_selected)
        left_layout.addLayout(selected_actions)
        left_layout.addWidget(self.locator_hint_label)
        left_layout.addWidget(self.locator_policy_banner)

        center_panel = QFrame(self)
        center_panel.setObjectName("workspacePanel")
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(10, 10, 10, 10)
        center_layout.setSpacing(8)
        self._canvas_title = QLabel("HLAVNÝ OBRAZ", center_panel)
        self._canvas_title.setProperty("role", "panelHeader")
        self._canvas_empty = QLabel(
            "Nie je dostupný GOLDEN obraz\nNajprv ho získajte alebo načítajte", center_panel
        )
        self._canvas_empty.setAlignment(Qt.AlignCenter)
        self._canvas_empty.setStyleSheet(
            "color: #8d96a0; background: #111417; border: 1px dashed #414851; padding: 20px;"
        )
        self._canvas_empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        center_layout.addWidget(self._canvas_title)
        center_layout.addWidget(self._canvas_empty)
        center_layout.addWidget(self.live_lbl, 1)
        center_layout.addWidget(self.roi_editor, 1)

        properties_scroll = QScrollArea(self)
        properties_scroll.setObjectName("propertiesScroll")
        properties_scroll.setWidgetResizable(True)
        properties_scroll.setFrameShape(QScrollArea.NoFrame)
        properties_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        properties_scroll.setMinimumWidth(300)
        properties_scroll.setMaximumWidth(480)
        properties_scroll.setWidget(self._tool_panel)

        self._workspace_splitter = QSplitter(Qt.Horizontal, self)
        self._workspace_splitter.setChildrenCollapsible(False)
        self._workspace_splitter.setHandleWidth(5)
        self._workspace_splitter.addWidget(left_panel)
        self._workspace_splitter.addWidget(center_panel)
        self._workspace_splitter.addWidget(properties_scroll)
        self._workspace_splitter.setStretchFactor(0, 0)
        self._workspace_splitter.setStretchFactor(1, 1)
        self._workspace_splitter.setStretchFactor(2, 0)
        self._workspace_splitter.setSizes([250, 800, 340])

        top_controls = QVBoxLayout()
        top_controls.setContentsMargins(10, 8, 10, 8)
        top_controls.setSpacing(6)
        top_controls.addLayout(top_primary)
        top_controls.addLayout(top_secondary)
        top_bar = QFrame(self)
        top_bar.setObjectName("goldenTopBar")
        top_bar.setLayout(top_controls)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        # Keep all controls reachable even when the editor's own toolbar has
        # a larger minimum width than the available logical screen width.
        workspace = QWidget(self)
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(top_bar)
        workspace_layout.addWidget(self._workspace_splitter, 1)
        workspace_scroll = QScrollArea(self)
        workspace_scroll.setWidgetResizable(True)
        workspace_scroll.setWidget(workspace)
        layout.addWidget(workspace_scroll, 1)
        layout.addLayout(buttons)
        self._status_bar = QLabel("Pripravené", self)
        self._status_bar.setWordWrap(True)
        self._status_bar.setStyleSheet(
            "color: #aab2bc; background: #20242a; border-top: 1px solid #3b4149; padding: 5px 8px;"
        )
        layout.addWidget(self._status_bar)

        self.setStyleSheet(GOLDEN_WIZARD_STYLE)

        self._tool_panel.clear()

        # signály
        btn_cap_golden.clicked.connect(self._capture_golden)
        btn_load_golden.clicked.connect(self._load_golden)
        self.btn_save_tool.clicked.connect(self._save_tool_draft)
        self.btn_test_tool.clicked.connect(self._trigger_test_shortcut)
        self.btn_publish_recipe.clicked.connect(self._publish_recipe)
        self.btn_close_wizard.clicked.connect(self.close)
        self.recipe_name.editingFinished.connect(self._on_recipe_changed)
        self.tools_table.itemSelectionChanged.connect(self._on_tool_selection_changed)
        self.tools_table.rowsReordered.connect(self._on_tools_reordered)
        self.roi_editor.roiChanged.connect(self._on_workspace_roi_changed)
        self.roi_editor.locatorRoiChanged.connect(self._on_locator_roi_changed)
        self._tool_panel.nameChanged.connect(self._on_tool_name_changed)
        self._tool_panel.paramChanged.connect(self._on_tool_param_changed)
        self._tool_panel.thresholdChanged.connect(self._on_tool_threshold_changed)
        self._tool_panel.testRequested.connect(self._on_tool_test_requested)
        self._tool_panel.testButtonEnabledChanged.connect(self.btn_test_tool.setEnabled)
        self._tool_panel.locatorPolicyWarningChanged.connect(
            self._update_locator_policy_banner
        )
        self._tool_panel.locatorAreaRequested.connect(self.roi_editor.select_locator_roi)
        self._tool_panel.locatorFitSearchRequested.connect(self.roi_editor.fit_search_to_template)
        self._tool_panel.presenceLearningRequested.connect(self._on_presence_v2_learning)
        self._tool_panel.emptyMoldV2Requested.connect(self._open_empty_mold_v2)
        self.roi_editor.ignoreMaskChanged.connect(self._on_workspace_mask_changed)
        self.roi_editor.edgeAnchorsChanged.connect(
            self._on_workspace_edge_anchors_changed
        )
        self.roi_editor.edgeRefineRequested.connect(
            self._on_workspace_edge_refine_requested
        )
        self.failure_policy_combo.currentIndexChanged.connect(
            self._on_failure_policy_changed
        )
        self.chk_logging.toggled.connect(self._on_logging_changed)

        self.btn_test_tool.setEnabled(self._tool_panel.is_test_enabled())

        self._selected_tool_row = -1

        self._shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
        self._shortcut_save.activated.connect(self._save_tool_draft)
        self._shortcut_publish = QShortcut(QKeySequence("Ctrl+P"), self)
        self._shortcut_publish.activated.connect(self._publish_recipe)
        self._shortcut_test_return = QShortcut(QKeySequence(Qt.CTRL | Qt.Key_Return), self)
        self._shortcut_test_return.activated.connect(self._trigger_test_shortcut)
        self._shortcut_test_enter = QShortcut(QKeySequence(Qt.CTRL | Qt.Key_Enter), self)
        self._shortcut_test_enter.activated.connect(self._trigger_test_shortcut)

        self._last_recipe = self._current_recipe_name()
        self._refresh_view_list(recipe=self._last_recipe, reset_states=True)
        self._sync_locator_policy_ui(self._last_recipe)
        self._sync_logging_ui(self._last_recipe)
        self._sync_live_policy_ui()
        self._refresh_manual_light()
        self._refresh_publish_state()

        self.setSizeGripEnabled(True)
        available = self.screen().availableGeometry()
        self.resize(min(1400, available.width()-40), min(900, available.height()-80))
        self.setWindowState(self.windowState() | Qt.WindowMaximized)

    # ---------- Live ----------
    def _set_manual_light_ui(self, enabled: bool | None) -> None:
        self.btn_manual_light.blockSignals(True)
        if enabled is None:
            self.btn_manual_light.setText("Svetlo: neznámy stav")
        else:
            self.btn_manual_light.setChecked(enabled)
            self.btn_manual_light.setText("Svetlo zapnuté" if enabled else "Svetlo vypnuté")
        self.btn_manual_light.blockSignals(False)

    def _refresh_manual_light(self) -> None:
        state = self.pico.manual_light_status() if self.pico is not None else None
        self._set_manual_light_ui(state)

    def _toggle_manual_light(self, checked: bool) -> None:
        previous = not checked
        if self.pico is not None and self.pico.set_manual_light(checked):
            self._set_manual_light_ui(checked)
            return
        self._set_manual_light_ui(previous)
        self._err(
            getattr(self.pico, "last_error", "") or "Pico nie je dostupné"
        )

    def _toggle_live(self, checked: bool):
        self.chk_filtered_roi.setEnabled(not checked)
        if checked and not self._is_live_allowed_by_capture_mode():
            self._logger.info("[GOLDEN_CAPTURE] live disabled in trigger mode")
            self.btn_live.blockSignals(True)
            self.btn_live.setChecked(False)
            self.btn_live.blockSignals(False)
            self._sync_live_policy_ui()
            return
        if checked:
            # Camera lifecycle: oddelené helpery pre štart/stop preview session.
            try:
                self._start_preview_session()
            except Exception as e:
                self._stop_preview_session(clear_label=True)
                self._err(f"Live feed sa nepodarilo spustiť: {e}")
                self.btn_live.setChecked(False)
        else:
            self._stop_preview_session()

    def _start_preview_session(self) -> None:
        # Zapnúť live: zobraz label, skryť DrawView (žiadne kreslenie počas live)
        self.view.hide()
        self.roi_editor.hide()
        self.live_lbl.show()
        self.cam.start(caller="golden_preview")
        self._live_timer.start()
        self._live_on = True
        self.btn_live.setText("Live zapnuté")
        self._logger.info("wizard_preview state=started")

    def _stop_preview_session(
        self,
        *,
        clear_label: bool = False,
    ) -> None:
        # Vypnúť live: skryť label, ukázať DrawView
        self._live_timer.stop()
        if self._live_on:
            self._logger.info("wizard_preview state=stopped")
        self._live_on = False
        self.btn_live.setText("Live vypnuté")
        self.live_lbl.hide()
        if self.current_img is not None:
            self._canvas_empty.hide()
            self.roi_editor.show()
        else:
            self.roi_editor.hide()
            self._canvas_empty.show()
        if clear_label:
            self.live_lbl.setText("—")
    def _live_tick(self):
        img = self.cam.last_frame(caller="golden_preview")
        view = self._view_by_id(self._active_view_id)
        img = apply_view_image_transform(img, view, stage="preview")
        if img is None:
            return
        h, w = img.shape[:2]
        qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy()).scaled(self.live_lbl.width(), self.live_lbl.height(),
                                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.live_lbl.setPixmap(pm)

    def _runtime_capture_mode(self) -> str:
        mode = "master"
        if callable(self._get_capture_mode):
            try:
                mode = str(self._get_capture_mode() or "master").strip().lower()
            except Exception:
                mode = "master"
        return mode if mode in {"master", "trigger"} else "master"

    def _is_live_allowed_by_capture_mode(self) -> bool:
        return self._runtime_capture_mode() == "master"

    def _sync_live_policy_ui(self) -> None:
        allowed = self._is_live_allowed_by_capture_mode()
        if not allowed and self._live_on:
            self.btn_live.blockSignals(True)
            self.btn_live.setChecked(False)
            self.btn_live.blockSignals(False)
            self._stop_preview_session()
        self.btn_live.setEnabled(allowed)
        if allowed:
            self.btn_live.setText("Live zapnuté" if self._live_on else "Live vypnuté")
            self.btn_live.setToolTip("")
        else:
            self.btn_live.setText("Live nedostupné v TRIGGER režime")
            self.btn_live.setToolTip("Live preview je v TRIGGER režime blokovaný.")

    # ---------- UI util ----------
    def _set_pixmap(self, img_u8):
        return self._preview_presenter._set_pixmap(img_u8)

    def _view_by_id(self, view_id: Optional[str]):
        return self._view_controller._view_by_id(view_id)

    def _store_view_state(self, view_id: Optional[str]=None):
        return self._view_controller._store_view_state(view_id)

    def _refresh_view_list(self, *, recipe: Optional[str]=None, select_view_id: Optional[str]=None, reset_states: bool=False):
        return self._view_controller._refresh_view_list(recipe=recipe, select_view_id=select_view_id, reset_states=reset_states)

    def _on_view_changed(self):
        return self._view_controller._on_view_changed()

    def _switch_active_view(self, view_id: Optional[str], *, refresh_selector: bool=True):
        return self._view_controller._switch_active_view(view_id, refresh_selector=refresh_selector)

    def _load_saved_golden_image(self, recipe: Optional[str]=None, view_id: Optional[str]=None):
        return self._preview_presenter._load_saved_golden_image(recipe, view_id)

    def _refresh_golden_background(self, recipe: Optional[str]=None, view_id: Optional[str]=None):
        return self._preview_presenter._refresh_golden_background(recipe, view_id)

    def _set_selected_tool_overlay(self, tools: Optional[Sequence[Tool]]=None):
        return self._preview_presenter._set_selected_tool_overlay(tools)

    def _refresh_filtered_roi(self):
        return self._preview_presenter._refresh_filtered_roi()

    def _current_golden_image(self):
        return self._preview_presenter._current_golden_image()

    def _configure_workspace_editor(self, tool: Tool) -> None:
        self.roi_editor.set_result_overlay(None)
        is_locator = tool.type == "locator.template_match"
        params = dict(getattr(tool.params, "values", {}) or {})
        if is_locator:
            template_roi = ToolRoi.from_obj(params.get("template_roi"))
            if template_roi.rect() is None:
                template_roi = tool.template_roi.copy()
            self.roi_editor.set_locator_mode(
                True,
                search=tool.roi.to_dict(),
                template=template_roi.to_dict(),
                use_golden_crop=bool(params.get("use_golden_crop", False)),
            )
        else:
            self.roi_editor.set_locator_mode(False)
            self.roi_editor.set_roi_data(tool.roi.to_dict())
        try:
            definition = self.recipes.tool.get_tool_meta(tool.type)
            supports_mask = bool(definition.meta.supports_ignore_mask)
        except KeyError:
            supports_mask = False
        mask_value = getattr(getattr(tool, "ignore_mask", None), "value", None)
        self.roi_editor.configure_ignore_mask(supports_mask, mask_value)
        is_edge_profile = tool.type == "edge_profile_deviation"
        is_guided_locator = is_locator and str(
            params.get("alignment_mode", "translation")
        ) == "guided_edge"
        anchor_a_key = "reference_point_a" if is_guided_locator else "point_a"
        anchor_b_key = "reference_point_b" if is_guided_locator else "point_b"
        window_key = "reference_search_half_window" if is_guided_locator else "search_half_window"
        self.roi_editor.configure_edge_anchors(
            is_edge_profile or is_guided_locator,
            self._parse_edge_point(params.get(anchor_a_key)),
            self._parse_edge_point(params.get(anchor_b_key)),
            int(params.get(window_key, 20) or 20),
            label="Referenčná hrana A-B" if is_guided_locator else "Hrana A-B",
            refine_label=(
                "Spresniť referenčnú hranu" if is_guided_locator else "Spresniť hranu"
            ),
            activate=is_edge_profile,
        )

    @staticmethod
    def _parse_edge_point(value: object) -> Optional[tuple[float, float]]:
        try:
            if isinstance(value, dict):
                return float(value["x"]), float(value["y"])
            if isinstance(value, (tuple, list)) and len(value) >= 2:
                return float(value[0]), float(value[1])
        except (KeyError, TypeError, ValueError):
            return None
        return None

    # ---------- Akcie ----------
    def _capture_golden(self):
        try:
            runtime_capture_mode = self._runtime_capture_mode()
            self._logger.info("[GOLDEN_CAPTURE] capture_mode=%s", runtime_capture_mode)
            view_id = self._active_view_id
            frame = None
            active_view = self._view_by_id(view_id)
            if callable(self._capture_frame_for_golden):
                self._logger.info("[GOLDEN_CAPTURE] using shared view capture path")
                frame = self._capture_frame_for_golden(
                    view_id=view_id,
                    trigger_mode_label="golden_wizard",
                    image_rotation_override=0,
                    capture_request_source="golden_wizard",
                )
            if frame is None:
                raise RuntimeError("Frame z kamery nie je dostupný.")
            self._logger.info(
                "[GOLDEN_CAPTURE] raw frame received shape=%s",
                None if frame is None else getattr(frame, "shape", None),
            )
            self._logger.info(
                "[GOLDEN_CAPTURE] active view rotation=%s",
                view_image_rotation(active_view),
            )
            self._logger.info("[GOLDEN_CAPTURE] applying final shared view transform before store/display")
            frame = apply_view_image_transform(frame, active_view, stage="golden capture")
            self._logger.info(
                "[GOLDEN_CAPTURE] final frame shape=%s",
                None if frame is None else getattr(frame, "shape", None),
            )
            self.current_img = frame
            self._logger.info("[GOLDEN_CAPTURE] stored current_img")
            self._set_pixmap(frame)
            self._logger.info("[GOLDEN_CAPTURE] pixmap updated")
            self._set_selected_tool_overlay()
            if self._active_view_id:
                self._view_states.setdefault(self._active_view_id, {})[
                    "golden_image"
                ] = np.asarray(frame).copy()
                self._logger.info("[GOLDEN_CAPTURE] stored state golden_image")
            if self._live_on:
                self.btn_live.setChecked(False)
                self._toggle_live(False)  # vypnúť live, prepnúť späť na DrawView

            self._sync_live_policy_ui()

            self._info("Golden zachytený z kamery.")
        except Exception as e:
            self._err(f"Zachytenie zlyhalo: {e}")

    def _load_golden(self):
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
        self._set_selected_tool_overlay()
        if self._active_view_id:
            self._view_states.setdefault(self._active_view_id, {})[
                "golden_image"
            ] = np.asarray(img).copy()
        self._info("Golden načítaný z disku.")

    def _persist_recipe_assets(self) -> tuple[bool, str]:
        if self.current_img is None:
            self._err("Najprv zachyť alebo načítaj GOLDEN.")
            return False, ""

        regs = self.view.export_regions()
        region_models = [Region(**r) for r in regs]
        pose_requested = self.chk_pose.isChecked()
        pose_enabled = pose_requested and any(r.reg_type == "pose" for r in region_models)
        ok, msg = validate_cardinality(region_models, pose_required=pose_enabled)
        if not ok:
            self._err(msg)
            return False, ""

        if pose_requested and not pose_enabled:
            self._info("Pose alignment was disabled because no pose region is defined.")
            self.chk_pose.setChecked(False)
        else:
            self.chk_pose.setChecked(pose_enabled)

        pose_enabled = self.chk_pose.isChecked()

        name = self.recipe_name.text().strip() or "default"
        view = self._view_by_id(self._active_view_id)
        golden_filename = view.golden_path if view else "golden.png"
        import hashlib
        existing_golden = self.recipes.base / "recipes" / name / golden_filename
        old_sha256 = hashlib.sha256(existing_golden.read_bytes()).hexdigest() if existing_golden.exists() else None
        golden_path = save_golden(
            self.current_img,
            name,
            golden_path=golden_filename,
            base_dir=self.recipes.base,
        )
        new_sha256 = hashlib.sha256(Path(golden_path).read_bytes()).hexdigest()
        self.recipes.record_golden_replaced(
            name, self._active_view_id, golden_filename, old_sha256, new_sha256
        )
        if self._active_view_id:
            self._view_states.setdefault(self._active_view_id, {})["golden_image"] = (
                np.asarray(self.current_img).copy()
            )
        recipe_dir = self.recipes.base / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        recipe_data = RecipeData(pose_enabled=pose_enabled, regions=regs)
        self.recipes.save_regions(name, recipe_data)

        message = f"Recipe assets saved:\n{golden_path}\n{recipe_dir / 'regions.json'}"
        return True, message

    def _open_session_settings(self) -> None:
        dialog = SessionSettingsDialog(self, settings_store=self.session_settings)
        dialog.exec()

    def _save_tool_draft(self):
        if not self._authorize_write():
            return
        assets_ok, assets_message = self._persist_recipe_assets()
        if not assets_ok:
            return
        recipe = self._current_recipe_name()
        ok, autosorted = self._persist_tools(recipe)
        if not ok:
            return
        self._record_saved_snapshot(recipe, self._active_view_id)
        self._refresh_tools_table()
        message = "Nástroje uložené do draftu."
        if autosorted:
            message += "\nPoradie nástrojov bolo automaticky upravené: Locator nástroje boli presunuté na začiatok."
        if assets_message:
            message += f"\n{assets_message}"
        self._info(message)
        self._refresh_publish_state()

    def _publish_recipe(self):
        if not self._authorize_write():
            return
        assets_ok, assets_message = self._persist_recipe_assets()
        if not assets_ok:
            return
        recipe = self._current_recipe_name()
        ok, autosorted_draft = self._persist_tools(recipe)
        if not ok:
            return
        self._record_saved_snapshot(recipe, self._active_view_id)
        try:
            _, autosorted_publish = self.recipes.publish_recipe(
                recipe, view_id=self._active_view_id
            )
        except Exception as exc:
            self._err(f"Publikovanie receptu zlyhalo: {exc}")
            return
        self._record_saved_snapshot(recipe, self._active_view_id)
        self._refresh_tools_table()
        message = "Recept publikovaný."
        if autosorted_draft or autosorted_publish:
            message += "\nPoradie nástrojov bolo automaticky upravené: Locator nástroje boli presunuté na začiatok."
        if assets_message:
            message += f"\n{assets_message}"
        self._info(message)
        try:
            self.recipes.load(recipe)
        except Exception as exc:
            print(f"[GoldenWizard] reload after publish failed for {recipe}: {exc}")
        self._refresh_publish_state()

    def _refresh_publish_state(self) -> None:
        state = self._load_publish_state()
        self._apply_publish_state(state)

    def _load_publish_state(self) -> dict[str, Any]:
        recipe = self._current_recipe_name()
        try:
            state = self.recipes.publish_state(recipe)
        except Exception:
            state = {"draft_updated_at": None, "published_at": None, "has_unpublished_changes": False}
        return state

    def _apply_publish_state(self, state: dict[str, Any]) -> None:
        draft_at = state.get("draft_updated_at")
        published_at = state.get("published_at")
        dirty = bool(state.get("has_unpublished_changes"))

        if dirty:
            text = "Unpublished changes"
            style = "color: #d9534f; font-weight: bold;"
        elif published_at:
            published_str = str(published_at)
            text = f"Published {published_str.split('.', 1)[0]}"
            style = "color: #28a745; font-weight: bold;"
        else:
            text = "Not published"
            style = "color: #999; font-style: italic;"

        self._publish_state_label.setText(text)
        self._publish_state_label.setStyleSheet(style)

        tooltip_parts: list[str] = []
        if draft_at:
            tooltip_parts.append(f"Koncept aktualizovaný: {draft_at}")
        if published_at:
            tooltip_parts.append(f"Publikované: {published_at}")
        self._publish_state_label.setToolTip("\n".join(tooltip_parts) if tooltip_parts else "")

    # ---------- Draft state management ----------
    def _snapshot_tools(self, recipe: str, view_id: str):
        return self._view_controller._snapshot_tools(recipe, view_id)

    def _record_saved_snapshot(self, recipe: str, view_id: Optional[str]=None):
        return self._view_controller._record_saved_snapshot(recipe, view_id)

    def _update_dirty_state(self, recipe: Optional[str]=None, view_id: Optional[str]=None):
        return self._view_controller._update_dirty_state(recipe, view_id)

    def _update_window_title_dirty(self) -> None:
        if not hasattr(self, "_base_title"):
            return
        current_recipe = self._current_recipe_name()
        dirty_map = self._dirty_views.get(current_recipe, {})
        dirty = any(dirty_map.values())
        title = self._base_title + (" *" if dirty else "")
        self.setWindowTitle(title)

    def _has_unsaved_changes(self):
        return self._view_controller._has_unsaved_changes()

    def _trigger_test_shortcut(self) -> None:
        panel = getattr(self, "_tool_panel", None)
        if panel is None:
            return
        trigger = getattr(panel, "trigger_test", None)
        if callable(trigger):
            trigger()

    # ---------- Info/Err ----------
    def _info(self, msg):
        QMessageBox.information(self, "Informácia", msg)

    def _warn(self, msg):
        QMessageBox.warning(self, "Upozornenie", msg)

    def _err(self, msg):
        QMessageBox.critical(self, "Chyba", msg)

    def _sync_locator_policy_ui(self, recipe: Optional[str] = None) -> None:
        if not hasattr(self, "failure_policy_combo"):
            return

        recipe = recipe or self._current_recipe_name()
        try:
            policy = self.recipes.get_locator_failure_policy(recipe)
        except Exception as exc:
            print(f"[GoldenWizard] get_locator_failure_policy failed for {recipe}: {exc}")
            policy = "continue_without_alignment"

        self._current_locator_failure_policy = policy
        self._updating_policy_combo = True
        try:
            index = self.failure_policy_combo.findData(policy)
            if index < 0:
                index = self.failure_policy_combo.findData("continue_without_alignment")
            if index < 0:
                index = 0
            self.failure_policy_combo.setCurrentIndex(max(0, index))
        finally:
            self._updating_policy_combo = False

        self._tool_panel.set_locator_failure_policy(policy)
        self._update_locator_policy_banner("")

    def _sync_logging_ui(self, recipe: Optional[str] = None) -> None:
        if not hasattr(self, "chk_logging"):
            return

        recipe = recipe or self._current_recipe_name()
        try:
            enabled = self.recipes.get_logging_enabled(recipe)
        except Exception as exc:
            print(f"[GoldenWizard] get_logging_enabled failed for {recipe}: {exc}")
            enabled = True

        self._updating_logging_checkbox = True
        try:
            self.chk_logging.setChecked(bool(enabled))
        finally:
            self._updating_logging_checkbox = False

    def _on_failure_policy_changed(self) -> None:
        if getattr(self, "_updating_policy_combo", False):
            return

        if not self._authorize_write():
            self._sync_locator_policy_ui()
            return

        policy = self.failure_policy_combo.currentData() or "continue_without_alignment"
        recipe = self._current_recipe_name()
        try:
            normalized = self.recipes.set_locator_failure_policy(recipe, policy)
        except Exception as exc:
            self._err(f"Zmena politiky locatora zlyhala: {exc}")
            self._sync_locator_policy_ui(recipe)
            return

        self._current_locator_failure_policy = normalized
        if normalized != policy:
            self._updating_policy_combo = True
            try:
                index = self.failure_policy_combo.findData(normalized)
                if index < 0:
                    index = self.failure_policy_combo.findData("continue_without_alignment")
                if index >= 0:
                    self.failure_policy_combo.setCurrentIndex(index)
            finally:
                self._updating_policy_combo = False

        self._tool_panel.set_locator_failure_policy(normalized)
        self._update_locator_policy_banner("")
        self._refresh_publish_state()

    def _on_logging_changed(self, checked: bool) -> None:
        if getattr(self, "_updating_logging_checkbox", False):
            return

        if not self._authorize_write():
            self._sync_logging_ui()
            return

        recipe = self._current_recipe_name()
        try:
            normalized = self.recipes.set_logging_enabled(recipe, bool(checked))
        except Exception as exc:
            self._err(f"Zmena logovania pre recept zlyhala: {exc}")
            self._sync_logging_ui(recipe)
            return

        if bool(normalized) != bool(checked):
            self._updating_logging_checkbox = True
            try:
                self.chk_logging.setChecked(bool(normalized))
            finally:
                self._updating_logging_checkbox = False

        self._refresh_publish_state()

    def _update_locator_policy_banner(self, message: str) -> None:
        if not hasattr(self, "locator_policy_banner"):
            return

        text = (message or "").strip()
        if not text:
            self.locator_policy_banner.clear()
            self.locator_policy_banner.setVisible(False)
        else:
            self.locator_policy_banner.setText(text)
            self.locator_policy_banner.setVisible(True)

    # ---------- Tools management ----------
    def _current_recipe_name(self) -> str:
        return self.recipe_name.text().strip() or "default"

    def _available_camera_resolutions(self) -> list[tuple[str, dict[str, Any]]]:
        return [(label, dict(data)) for label, data in _DEFAULT_CAMERA_RESOLUTIONS]

    def _current_camera_config(self) -> Optional[dict[str, Any]]:
        width = getattr(self.cam, "width", None)
        height = getattr(self.cam, "height", None)
        fps = getattr(self.cam, "fps", None)
        pixel_format = getattr(self.cam, "pixel_format", None)
        if width and height and fps:
            return {
                "exposure_us": getattr(self.cam, "exposure_us", None),
                "width": int(width),
                "height": int(height),
                "fps": int(fps),
                "pixel_format": (pixel_format or "Y8").upper(),
            }
        return None

    def _camera_model(self) -> str | None:
        getter = getattr(self.cam, "get_camera_model", None)
        if callable(getter):
            try:
                model = getter()
                return str(model).strip() or None if model is not None else None
            except Exception:
                return None
        return None

    def _camera_v4l2_controls(self) -> set[str]:
        getter = getattr(self.cam, "get_supported_v4l2_controls", None)
        if callable(getter):
            try:
                return {str(item).strip() for item in getter() if str(item).strip()}
            except Exception:
                return set()
        return set()

    def _snapshot_camera_state(self) -> dict[str, Any]:
        return snapshot_camera_state(self.cam)

    def _apply_camera_state(self, state: dict[str, Any], *, show_warnings: bool = True) -> None:
        warn = self._warn if show_warnings else None
        apply_camera_state(self.cam, state, warn=warn)

    def _apply_view_camera_profile(self, view: Optional[RecipeView]) -> None:
        profile = getattr(view, "camera_profile", None) if view else None
        apply_view_camera_profile(
            self.cam,
            {},
            profile,
            warn=self._warn,
        )

    @staticmethod
    def _suggest_view_id(existing: Sequence[RecipeView]):
        return GoldenViews._suggest_view_id(existing)

    @staticmethod
    def _suggest_view_name(existing: Sequence[RecipeView]):
        return GoldenViews._suggest_view_name(existing)

    def _on_recipe_changed(self):
        recipe = self._current_recipe_name()
        if recipe == getattr(self, "_last_recipe", None):
            return
        self._store_view_state()
        self._last_recipe = recipe
        self._refresh_view_list(recipe=recipe, reset_states=True)
        self._sync_locator_policy_ui(recipe)
        self._sync_logging_ui(recipe)
        self._refresh_publish_state()

    def _read_pico_config_snapshot(self) -> dict[str, object] | None:
        """Read STATUS through the already-owned PicoService connection."""
        if self.pico is None:
            return None
        try:
            status = self.pico.status()
            if not status.get("connected"):
                return None
            return self.pico.parse_status_config(str(status.get("device_status", "") or ""))
        except Exception:
            self._logger.exception("Unable to read Pico configuration snapshot")
            return None

    def _on_add_view(self):
        return self._view_controller._on_add_view()

    def _on_edit_view(self):
        return self._view_controller._on_edit_view()

    def _on_remove_view(self):
        return self._view_controller._on_remove_view()

    def _open_tool_catalog(self):
        dialog = ToolCatalogDialog(self.recipes.tool, self)
        if dialog.exec() != QDialog.Accepted:
            return
        tool_type = dialog.selected_type()
        if not tool_type:
            return
        try:
            tool = make_catalog_tool(self.recipes.tool, tool_type)
            recipe = self._current_recipe_name()
            view_id = self._active_view_id
            if not view_id:
                self._err("Nie je vybraný žiadny view.")
                return
            self.recipes.add_tool(recipe, tool, view_id=view_id)
            self._selected_tool_row = len(self.recipes.get_draft_tools(recipe, view_id)) - 1
            self._refresh_tools_table()
            self._update_dirty_state(recipe, view_id)
        except Exception as exc:
            self._err(f"Pridanie nástroja zlyhalo: {exc}")

    def _refresh_tools_table(self):
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        if not view_id:
            self.tools_table.blockSignals(True)
            self.tools_table.setRowCount(0)
            self.tools_table.clearSelection()
            self.tools_table.blockSignals(False)
            self._tool_panel.clear()
            self._selected_tool_row = -1
            self.view.set_tool_overlay(None)
            return

        tools = self.recipes.get_draft_tools(recipe, view_id)
        previous_row = self._selected_tool_row if hasattr(self, "_selected_tool_row") else -1
        self.tools_table.blockSignals(True)
        self.tools_table.setRowCount(len(tools))
        for row, tool in enumerate(tools):
            order_item = QTableWidgetItem(str(tool.order + 1))
            order_item.setData(Qt.UserRole, row)
            order_flags = order_item.flags()
            order_flags |= Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
            order_item.setFlags(order_flags)
            name_item = QTableWidgetItem(tool_display_name(tool))
            try:
                tool_meta = self.recipes.tool.get_tool_meta(tool.type)
                secondary = getattr(tool_meta, "category", "") or getattr(tool_meta, "name", "")
            except KeyError:
                secondary = "Tool"
            type_item = QTableWidgetItem(str(secondary))
            type_item.setToolTip(tool.type)
            order_item.setTextAlignment(Qt.AlignCenter)
            is_locator = tool.type.startswith("locator.")
            if is_locator:
                highlight = QColor("#20262d")
                foreground = QColor("#e6e8eb")
                type_item.setBackground(highlight)
                type_item.setForeground(foreground)
                order_item.setBackground(highlight)
                order_item.setForeground(foreground)
                name_item.setBackground(highlight)
                name_item.setForeground(foreground)
                type_item.setText(f"{secondary} · Locator")
                type_item.setToolTip("Locator nástroje vždy bežia pred analyzátormi.")
            else:
                type_item.setToolTip("Analyzátory bežia po locator nástrojoch.")
            self.tools_table.setItem(row, 0, order_item)
            self.tools_table.setItem(row, 1, name_item)
            self.tools_table.setItem(row, 2, type_item)

            enabled_checkbox = QCheckBox(self.tools_table)
            enabled_checkbox.setTristate(False)
            enabled_checkbox.setToolTip("Rýchle zapnutie alebo vypnutie nástroja v pipeline.")
            enabled_checkbox.blockSignals(True)
            enabled_checkbox.setChecked(bool(tool.enabled))
            enabled_checkbox.blockSignals(False)
            enabled_checkbox.toggled.connect(partial(self._on_tool_enabled_toggled, row))
            enabled_container = QWidget(self.tools_table)
            enabled_layout = QHBoxLayout(enabled_container)
            enabled_layout.setContentsMargins(0, 0, 0, 0)
            enabled_layout.setSpacing(0)
            enabled_layout.addStretch(1)
            enabled_layout.addWidget(enabled_checkbox)
            enabled_layout.addStretch(1)
            self.tools_table.setCellWidget(row, 3, enabled_container)

            if not tool.enabled:
                disabled_color = QColor("#999999")
                for col in range(0, 3):
                    item = self.tools_table.item(row, col)
                    if item is not None:
                        item.setForeground(disabled_color)

            actions_widget = QWidget(self.tools_table)
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setSpacing(4)

            btn_edit = QToolButton(actions_widget)
            btn_edit.setText("⋯")
            btn_edit.setToolTip("Upraviť v bočnom paneli")
            btn_edit.clicked.connect(lambda _, idx=row: self._edit_tool(idx))
            btn_del = QToolButton(actions_widget)
            btn_del.setText("×")
            btn_del.setToolTip("Odstrániť nástroj")
            btn_del.clicked.connect(lambda _, idx=row: self._delete_tool(idx))

            actions_layout.addWidget(btn_edit)
            actions_layout.addWidget(btn_del)
            actions_layout.addStretch(1)

            self.tools_table.setCellWidget(row, 4, actions_widget)

        self.tools_table.resizeRowsToContents()
        self.tools_table.blockSignals(False)

        if tools:
            if previous_row < 0:
                target_row = 0
            elif previous_row >= len(tools):
                target_row = len(tools) - 1
            else:
                target_row = previous_row
            self.tools_table.selectRow(target_row)
            self._selected_tool_row = target_row
        else:
            self.tools_table.clearSelection()
            self._selected_tool_row = -1
            self._on_tool_selection_changed()

        self._set_selected_tool_overlay(tools)

        self._update_dirty_state(recipe, view_id)

    def _delete_tool(self, index: int):
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        if not view_id:
            return
        self.recipes.remove_tool(recipe, index, view_id=view_id)
        self._refresh_tools_table()
        self._update_dirty_state(recipe, view_id)

    def _on_tool_enabled_toggled(self, index: int, enabled: bool) -> None:
        self._toggle_tool_enabled(index, enabled)

    def _toggle_tool_enabled(self, index: int, enabled: bool) -> None:
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        if not view_id:
            return
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not (0 <= index < len(tools)):
            return

        tool = tools[index]
        if bool(tool.enabled) == bool(enabled):
            return

        tool.enabled = bool(enabled)
        try:
            self.recipes.update_tool(recipe, index, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Prepnutie nástroja zlyhalo: {exc}")
            self._refresh_tools_table()
            return

        self._selected_tool_row = index
        self._update_dirty_state(recipe, view_id)
        self._refresh_tools_table()

    def _on_tools_reordered(self, new_order: list[int]) -> None:
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        if not view_id:
            return
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if len(new_order) != len(tools):
            self._refresh_tools_table()
            return

        if new_order == list(range(len(tools))):
            self._refresh_tools_table()
            return

        reordered = [tools[idx] for idx in new_order]
        if not self._is_locator_order_valid(reordered):
            self._warn("Locator nástroje musia zostať pred analyzátormi. Zmena nebola aplikovaná.")
            self._refresh_tools_table()
            return

        previous_selection = getattr(self, "_selected_tool_row", -1)
        try:
            self.recipes.reorder_tools(recipe, new_order, view_id=view_id)
        except Exception as exc:
            self._err(f"Zmena poradia zlyhala: {exc}")
            self._refresh_tools_table()
            return

        if 0 <= previous_selection < len(new_order):
            try:
                self._selected_tool_row = new_order.index(previous_selection)
            except ValueError:
                self._selected_tool_row = -1

        self._refresh_tools_table()
        self._update_dirty_state(recipe, view_id)

    def _is_locator_order_valid(self, tools: Sequence[Tool]) -> bool:
        analyzer_seen = False
        for tool in tools:
            if tool.type.startswith("locator."):
                if analyzer_seen:
                    return False
            else:
                analyzer_seen = True
        return True

    def _on_tool_selection_changed(self) -> None:
        # Tool editing lifecycle: panel/overlay refresh podľa aktuálneho výberu nástroja.
        self._refresh_tool_panel_for_selection()

    def _refresh_tool_panel_for_selection(self) -> None:
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        tools = []
        if view_id:
            tools = self.recipes.get_draft_tools(recipe, view_id)
        row = self.tools_table.currentRow()
        if 0 <= row < len(tools):
            self._btn_delete_selected.setEnabled(True)
            tool = tools[row]
            try:
                meta = self.recipes.tool.get_tool_meta(tool.type)
                schema = self.recipes.tool.get_tool_schema(tool.type)
            except KeyError as exc:
                print(f"[GoldenWizard] Missing tool metadata for {tool.type}: {exc}")
                self._tool_panel.clear()
                self._selected_tool_row = -1
                self.view.set_tool_overlay(None)
                return
            self._tool_panel.set_tool(tool, meta, schema)
            supports_roi = bool(getattr(getattr(meta, "meta", meta), "supports_roi", False))
            supports_mask = bool(
                getattr(getattr(meta, "meta", meta), "supports_ignore_mask", False)
            )
            self.roi_editor.setEnabled(supports_roi or supports_mask)
            self._tool_panel.set_locator_failure_policy(
                self._current_locator_failure_policy
            )
            self._selected_tool_row = row
            self.view.set_tool_overlay(tool)
            self._syncing_workspace_roi = True
            try:
                self._configure_workspace_editor(tool)
            finally:
                self._syncing_workspace_roi = False
            self._restore_tool_result(tool)
            if tool.type in _STATISTICAL_PRESENCE_TYPES:
                self._refresh_presence_v2_learning(tool, row)
            shape = (
                "Otočený obdĺžnik" if tool.roi.is_rotated_rect()
                else {"rect": "Obdĺžnik", "ellipse": "Kruh", "polygon": "Polygón"}.get(
                    tool.roi.shape(), "ROI"
                )
            ) if tool.roi.rect() is not None else "Bez ROI"
            state = "Povolený" if tool.enabled else "Zakázaný"
            self._status_bar.setText(
                f"Nástroj: {tool.name}  |  ROI: {shape}  |  {state}  |  Pripravené"
            )
        else:
            self._btn_delete_selected.setEnabled(False)
            self._tool_panel.clear()
            self.roi_editor.setEnabled(False)
            self._selected_tool_row = -1
            self.view.set_tool_overlay(None)
            self.roi_editor.set_result_overlay(None)
            self._syncing_workspace_roi = True
            try:
                self.roi_editor.set_locator_mode(False)
                self.roi_editor.set_roi_data({})
                self.roi_editor.configure_ignore_mask(False)
                self.roi_editor.configure_edge_anchors(False)
            finally:
                self._syncing_workspace_roi = False
            self._status_bar.setText(
                "Nie je vybraný nástroj  |  Pridajte nástroj alebo ho vyberte zo zoznamu."
            )

    def _on_workspace_roi_changed(self, _rect: object) -> None:
        self._filtered_roi_timer.start()
        if self._syncing_workspace_roi:
            return
        row = getattr(self, "_selected_tool_row", -1)
        view_id = self._active_view_id
        if row < 0 or not view_id:
            return
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not (0 <= row < len(tools)):
            return
        tool = tools[row]
        if tool.type == "locator.template_match":
            return
        roi_data = self.roi_editor.roi_data()
        tool.roi = ToolRoi.from_obj(roi_data)
        self._invalidate_presence_v2_model(tool)
        try:
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Uloženie ROI zlyhalo: {exc}")
            return
        self.view.set_tool_overlay(tool)
        self._tool_panel.refresh_geometry(tool)
        self._update_dirty_state(recipe, view_id)
        if tool.type in _STATISTICAL_PRESENCE_TYPES:
            self._refresh_presence_v2_learning(tool, row)
        shape = (
            "Otočený obdĺžnik" if tool.roi.is_rotated_rect()
            else {"rect": "Obdĺžnik", "ellipse": "Kruh", "polygon": "Polygón"}.get(
                tool.roi.shape(), "ROI"
            )
        ) if tool.roi.rect() is not None else "Bez ROI"
        self._status_bar.setText(
            f"Nástroj: {tool.name}  |  ROI: {shape}  |  Koncept aktualizovaný"
        )

    def _on_workspace_mask_changed(self, mask: object) -> None:
        if self._syncing_workspace_roi:
            return
        row = getattr(self, "_selected_tool_row", -1)
        view_id = self._active_view_id
        if row < 0 or not view_id:
            return
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not (0 <= row < len(tools)):
            return
        tool = tools[row]
        try:
            definition = self.recipes.tool.get_tool_meta(tool.type)
        except KeyError:
            return
        if not bool(definition.meta.supports_ignore_mask):
            return
        value = None if mask is None else np.asarray(mask, dtype=np.uint8).copy()
        tool.ignore_mask = ToolMask(value)
        self._invalidate_presence_v2_model(tool)
        try:
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Uloženie Ignore Mask zlyhalo: {exc}")
            return
        self.view.set_tool_overlay(tool)
        self._update_dirty_state(recipe, view_id)
        if tool.type in _STATISTICAL_PRESENCE_TYPES:
            self._refresh_presence_v2_learning(tool, row)
        self._status_bar.setText(
            f"Nástroj: {tool.name}  |  Ignorovaná oblasť aktualizovaná  |  Koncept aktualizovaný"
        )

    def _on_workspace_edge_anchors_changed(
        self, point_a: object, point_b: object
    ) -> None:
        if self._syncing_workspace_roi:
            return
        row = getattr(self, "_selected_tool_row", -1)
        view_id = self._active_view_id
        if row < 0 or not view_id:
            return
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not (0 <= row < len(tools)):
            return
        tool = tools[row]
        is_guided_locator = tool.type == "locator.template_match" and str(
            getattr(tool.params, "values", {}).get("alignment_mode", "translation")
        ) == "guided_edge"
        if tool.type != "edge_profile_deviation" and not is_guided_locator:
            return

        parsed_a = self._parse_edge_point(point_a)
        parsed_b = self._parse_edge_point(point_b)
        params = dict(getattr(tool.params, "values", {}) or {})
        point_a_key = "reference_point_a" if is_guided_locator else "point_a"
        point_b_key = "reference_point_b" if is_guided_locator else "point_b"
        params[point_a_key] = (
            {"x": parsed_a[0], "y": parsed_a[1]} if parsed_a is not None else None
        )
        params[point_b_key] = (
            {"x": parsed_b[0], "y": parsed_b[1]} if parsed_b is not None else None
        )
        tool.params = ToolParams(params)
        try:
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Uloženie bodov A-B zlyhalo: {exc}")
            return
        self._update_dirty_state(recipe, view_id)
        label = "Referenčná hrana A-B" if is_guided_locator else "Body A-B"
        self._status_bar.setText(f"Nástroj: {tool.name}  |  {label} aktualizovaná  |  Koncept uložený")

    def _on_workspace_edge_refine_requested(self) -> None:
        row = getattr(self, "_selected_tool_row", -1)
        view_id = self._active_view_id
        if row < 0 or not view_id:
            return
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not (0 <= row < len(tools)):
            return
        tool = tools[row]
        is_guided_locator = tool.type == "locator.template_match" and str(
            getattr(tool.params, "values", {}).get("alignment_mode", "translation")
        ) == "guided_edge"
        if tool.type != "edge_profile_deviation" and not is_guided_locator:
            return
        image = self._current_golden_image()
        roi_rect = tool.roi.rect()
        point_a, point_b = self.roi_editor.edge_points()
        if image is None or roi_rect is None or point_a is None or point_b is None:
            self.roi_editor.set_edge_status(
                "Najprv nastav ROI a približné body A aj B.", error=True
            )
            return

        params = dict(getattr(tool.params, "values", {}) or {})
        thresholds = dict(getattr(tool.thresholds, "values", {}) or {})
        excluded_mask = roi_local_exclusion_mask(tool.roi, tool.ignore_mask.value)
        valid_mask = None if excluded_mask is None else np.asarray(excluded_mask) == 0
        try:
            detection = detect_guided_reference_edge(
                image,
                roi_rect,
                point_a,
                point_b,
                blur_sigma=float(params.get(
                    "reference_blur_sigma" if is_guided_locator else "blur_sigma", 1.0
                )),
                scan_step=int(params.get(
                    "reference_scan_step" if is_guided_locator else "scan_step", 2
                )),
                edge_polarity=str(params.get(
                    "reference_edge_polarity" if is_guided_locator else "edge_polarity", "any"
                )),
                grad_threshold=float(params.get(
                    "reference_grad_threshold" if is_guided_locator else "grad_threshold", 15.0
                )),
                search_half_window=int(params.get(
                    "reference_search_half_window" if is_guided_locator else "search_half_window", 20
                )),
                outlier_trim_pct=float(params.get("outlier_trim_pct", 0.1)),
                use_subpixel=bool(params.get(
                    "reference_use_subpixel" if is_guided_locator else "use_subpixel", False
                )),
                valid_mask=valid_mask,
            )
        except (TypeError, ValueError) as exc:
            self.roi_editor.set_edge_status(str(exc), error=True)
            return

        coverage = float(detection["coverage"])
        required_coverage = min(1.0, max(0.0, float(
            params.get("reference_min_coverage", 0.6)
            if is_guided_locator else thresholds.get("coverage_min", 0.6)
        )))
        if coverage < required_coverage:
            self.roi_editor.set_edge_status(
                f"Hrana nie je dostatočne súvislá: {coverage * 100.0:.0f} %, "
                f"požadovaných aspoň {required_coverage * 100.0:.0f} %.",
                error=True,
            )
            return

        refined_a = detection["point_a"]
        refined_b = detection["point_b"]
        self.roi_editor.set_edge_detection_result(
            refined_a,
            refined_b,
            detection["edge_points"],
        )
        recommended = dict(detection.get("recommended_params", {}) or {})
        if is_guided_locator:
            if "grad_threshold" in recommended:
                params["reference_grad_threshold"] = recommended["grad_threshold"]
            params["reference_point_a"] = {"x": float(refined_a[0]), "y": float(refined_a[1])}
            params["reference_point_b"] = {"x": float(refined_b[0]), "y": float(refined_b[1])}
        else:
            params.update(recommended)
            params["point_a"] = {"x": float(refined_a[0]), "y": float(refined_a[1])}
            params["point_b"] = {"x": float(refined_b[0]), "y": float(refined_b[1])}
        tool.params = ToolParams(params)
        try:
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Uloženie odporúčaných nastavení hrany zlyhalo: {exc}")
            return
        self._tool_panel.refresh_values(tool)
        self.roi_editor.set_edge_search_half_window(
            int(params.get(
                "reference_search_half_window" if is_guided_locator else "search_half_window", 20
            ))
        )
        self.roi_editor.set_edge_status(
            f"Hrana spresnená: {coverage * 100.0:.0f} % bodov "
            f"({detection['found_points']}/{detection['scan_lines']}). "
            "Použitá odporúčaná sila hrany."
        )

    def _invalidate_presence_v2_model(self, tool: Tool) -> None:
        if tool.type not in _STATISTICAL_PRESENCE_TYPES:
            return
        params = dict(tool.params.values or {})
        current_hash = compute_roi_hash(tool.roi, tool.ignore_mask.value)
        previous_hash = str(params.get("roi_hash", "") or "")
        params["roi_hash"] = current_hash
        if previous_hash and previous_hash != current_hash:
            params["reference_model_ready"] = False
            params["reference_model_invalidated"] = True
        tool.params = ToolParams(params)

    def _open_empty_mold_v2(self):
        if not self._authorize_write():
            return
        recipe, view_id = self._current_recipe_name(), self._active_view_id
        row = self._selected_tool_row
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not 0 <= row < len(tools) or tools[row].type != "mold.protection_v2":
            return
        from app.services.empty_mold_v2.workflow import bind_store
        from app.services.empty_mold_v2.alignment import capture_aligned
        from app.ui.golden_wizard.empty_mold_v2_dialog import EmptyMoldV2Dialog
        tool = tools[row].copy()
        try:
            golden = self._current_golden_image()
            if golden is None:
                raise ValueError("Najskôr vytvorte Golden a hlavnú ROI.")
            signature = self._presence_learning_signature(tool, view_id)
            store = bind_store(tool, self.recipes.db.db_path,
                               self.recipes.base / "recipes", recipe, view_id)
            def capture():
                frame = self._capture_presence_learning_frame(view_id)
                if frame is None:
                    return None
                return capture_aligned(golden, frame, tools)
            dialog = EmptyMoldV2Dialog(tool, store, golden, signature, capture,
                                       self._authorize_write, self)
            dialog.exec()
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
            self._tool_panel.refresh_values(tool)
            self._update_dirty_state(recipe, view_id)
        except Exception as exc:
            self._warn(str(exc))

    def _presence_v2_context(self):
        row = getattr(self, "_selected_tool_row", -1)
        view_id = self._active_view_id
        if row < 0 or not view_id:
            return None
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if (not (0 <= row < len(tools))
                or tools[row].type not in _STATISTICAL_PRESENCE_TYPES):
            return None
        tool = tools[row]
        identity, _, _ = compute_tool_identity(tool)
        assets = resolve_assets_dir(self.recipes.base, recipe, view_id, identity)
        # Keep the dataset location when only the display name/order changes.
        stored = str(tool.params.values.get("reference_assets_dir", "") or "")
        if stored:
            candidate = Path(stored).resolve()
            if candidate.is_relative_to(assets.parents[1].resolve()):
                assets = candidate
        return recipe, view_id, row, tool, assets

    def _presence_learning_signature(self, tool, view_id):
        return learning_signature(
            self._current_golden_image(), self._view_by_id(view_id), tool,
            self.recipes.get_draft_tools(self._current_recipe_name(), view_id),
        )

    def _presence_v2_expected_shape(self, tool: Tool) -> Optional[tuple[int, int]]:
        rect = tool.roi.rect()
        if rect is None:
            return None
        return int(rect[3]), int(rect[2])

    @staticmethod
    def _presence_v2_ignore_mask(tool: Tool) -> Optional[np.ndarray]:
        return roi_local_exclusion_mask(tool.roi, tool.ignore_mask.value)

    def _refresh_presence_v2_learning(self, tool, row):
        context = self._presence_v2_context()
        if context is None:
            return
        recipe, view_id, _, current, assets = context
        try:
            signature = self._presence_learning_signature(current, view_id)
        except ValueError:
            signature = None
        status = LearningDataset().refresh(current, assets, signature)
        self.recipes.update_tool(recipe, row, current, view_id=view_id)
        self._tool_panel.refresh_presence_learning(current, **status)

    def _rebuild_presence_v2_model(self, tool, dirs, view_id):
        try:
            return LearningDataset().rebuild(
                tool, dirs, view_id, self._presence_learning_signature(tool, view_id)
            )
        except ValueError as exc:
            self._warn(str(exc))
            return None

    def _on_presence_v2_learning(self, action: str) -> None:
        context = self._presence_v2_context()
        if context is None:
            return
        if action != "validate" and not self._authorize_write():
            return
        recipe, view_id, row, tool, assets = context
        dirs = ensure_assets_dirs(assets)
        if action != "reset":
            try:
                self._presence_learning_signature(tool, view_id)
            except ValueError as exc:
                self._warn(str(exc))
                return
        if action in {"capture_ok", "capture_nok"}:
            mode = "ok" if action == "capture_ok" else "nok"
            signature = self._presence_learning_signature(tool, view_id)
            params = dict(tool.params.values or {})
            if (load_samples(dirs["ok"]) or load_samples(dirs["nok"])) and (params.get("sample_preparation_version") != SAMPLE_PREPARATION_VERSION or not samples_match(assets, signature)):
                params["reference_model_invalidated"] = True
            if bool(params.get("reference_model_invalidated", False)) and (
                    load_samples(dirs["ok"]) or load_samples(dirs["nok"])):
                reply = QMessageBox.question(
                    self, "Staré vzorky už nesedia",
                    "Zmenilo sa nastavenie alebo spôsob prípravy snímok. Staré vzorky nemožno miešať s novými. Vymazať ich a začať nové učenie?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
                )
                if reply != QMessageBox.Yes:
                    return
                ok, error = reset_learning_assets(assets)
                if not ok:
                    self._warn(f"Reset učenia zlyhal: {error or 'neznáma chyba'}")
                    return
                params.update(reference_model_ready=False, reference_model_invalidated=False,
                              sample_count_ok=0, sample_count_nok=0)
                tool.params = ToolParams(params)
                self.recipes.update_tool(recipe, row, tool, view_id=view_id)
            dialog = PresenceV2SampleCaptureDialog(
                title=(
                    "Zber prázdnej formy" if mode == "ok"
                    else "Zber vzoriek so zvyškom"
                ) if tool.type == "mold.protection_v1" else (
                    "Zber OK snímok" if mode == "ok" else "Zber NOK snímok"
                ),
                capture_fn=lambda: self._capture_presence_learning_frame(view_id),
                crop_fn=lambda frame: self._prepare_presence_learning_sample(frame, tool, view_id),
                default_mode=str((tool.params.values or {}).get("capture_mode_default", "manual")),
                parent=self,
            )
            if dialog.exec() == QDialog.Accepted:
                samples = dialog.samples()
                if samples:
                    if self._presence_learning_signature(tool, view_id) != signature:
                        self._warn("Počas zberu sa zmenilo nastavenie. Snímky neboli pridané.")
                        return
                    bind_samples(assets, signature)
                    self._presence_tuning_results.pop((view_id, int(tool.order), tool.type), None)
                    for sample in samples:
                        save_sample(sample, dirs[mode])
                    params = dict(tool.params.values or {})
                    params["sample_preparation_version"] = SAMPLE_PREPARATION_VERSION
                    params["reference_model_ready"] = False
                    params["reference_model_needs_rebuild"] = True
                    params["reference_model_invalidated"] = False
                    tool.params = ToolParams(params)
        elif action == "reset":
            reply = QMessageBox.question(
                self, "Resetovať učenie?",
                "Vymažú sa všetky OK/NOK vzorky a prepočítaný model.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            ok, error = reset_learning_assets(assets)
            if not ok:
                self._warn(f"Reset učenia zlyhal: {error or 'neznáma chyba'}")
                return
            params = dict(tool.params.values or {})
            params.update(reference_model_ready=False, reference_model_invalidated=False,
                          sample_count_ok=0, sample_count_nok=0)
            self._presence_tuning_results.pop((view_id, int(tool.order), tool.type), None)
            params.pop("model_stats", None)
            params.pop("model_warnings", None)
            tool.params = ToolParams(params)
        elif action == "rebuild":
            if self._rebuild_presence_v2_model(tool, dirs, view_id) is None:
                return
        elif action == "validate":
            self._validate_presence_v2_samples(tool, dirs)
            return
        elif action == "auto_settings":
            rebuilt = self._rebuild_presence_v2_model(tool, dirs, view_id)
            if rebuilt is None:
                return
            median, mad, model_recommended = rebuilt
            ok_samples = load_samples(dirs["ok"])
            nok_samples = load_samples(dirs["nok"])
            thresholds = dict(tool.thresholds.values or {})
            current = int(thresholds.get("sensitivity", 60) or 60)
            current_summary = evaluate_dataset(
                ok_samples,
                nok_samples,
                median,
                mad,
                **self._presence_v2_evaluation_kwargs(tool),
            )
            recommended_area = float(model_recommended["total_area_threshold"])
            recommended_min_blob = float(model_recommended["min_blob_area"])
            recommended_kwargs = self._presence_v2_evaluation_kwargs(
                tool, include_score=False
            )
            recommended_kwargs.update(
                total_area_threshold=recommended_area,
                min_blob_area=recommended_min_blob,
            )
            tuning = optimize_sensitivity(
                ok_samples,
                nok_samples,
                median,
                mad,
                current_sensitivity=current,
                **recommended_kwargs,
            )
            tuning["learning_signature"] = self._presence_learning_signature(tool, view_id)
            tuning["current_validation_summary"] = current_summary
            tuned_sensitivity = int(tuning["recommended_sensitivity"])
            tuning["recommended_thresholds"] = {
                "sensitivity": tuned_sensitivity,
                "score_threshold": sensitivity_to_score_threshold(tuned_sensitivity),
                "total_area_threshold": recommended_area,
                "min_blob_area": recommended_min_blob,
            }
            key = (view_id, int(tool.order), tool.type)
            self._presence_tuning_results[key] = tuning
            params = dict(tool.params.values or {})
            weak = (
                int(tuning["recommended_validation_summary"]["ok_total"])
                < int(params.get("recommended_ok_samples", 30) or 30)
                or (0 < int(tuning["recommended_validation_summary"]["nok_total"]) < 5)
            )
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
            self._tool_panel.refresh_values(tool)
            self._refresh_presence_v2_learning(tool, row)
            self._update_dirty_state(recipe, view_id)
            self._tool_panel.show_presence_validation(
                tuning["current_validation_summary"], weak_dataset=weak
            )
            self._tool_panel.show_presence_tuning(tuning, weak_dataset=weak)
            return
        elif action == "apply_auto_settings":
            key = (view_id, int(tool.order), tool.type)
            tuning = self._presence_tuning_results.get(key)
            if tuning is None or tuning.get("learning_signature") != self._presence_learning_signature(tool, view_id):
                self._warn("Odporúčané nastavenia nie sú dostupné pre aktuálne učenie.")
                return
            thresholds = dict(tool.thresholds.values or {})
            for name, value in tuning["recommended_thresholds"].items():
                thresholds[name] = value
            tool.thresholds = ToolThresholds(thresholds)
        self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        self._tool_panel.refresh_values(tool)
        self._refresh_presence_v2_learning(tool, row)
        self._update_dirty_state(recipe, view_id)

    def _capture_presence_learning_frame(self, view_id):
        if not callable(self._capture_frame_for_golden):
            return None
        view = self._view_by_id(view_id)
        frame = self._capture_frame_for_golden(
            view_id=view_id, trigger_mode_label="presence_v2_learning",
            image_rotation_override=int(getattr(view, "image_rotation", 0) or 0),
            capture_request_source="presence_v2_learning",
        )
        golden = self._current_golden_image()
        if frame is not None and golden is not None and frame.shape[:2] != golden.shape[:2]:
            self._warn("Rozmery snímky nezodpovedajú golden. Skontrolujte rozlíšenie pohľadu pred zberom vzoriek.")
            return None
        return frame

    def _prepare_presence_learning_sample(self, frame, tool, view_id):
        from app.services.statistical_learning import LearningSamplePreparer
        tools = self.recipes.get_draft_tools(self._current_recipe_name(), view_id)
        return LearningSamplePreparer().sample(
            self._current_golden_image(), frame, tool, tools
        )

    @staticmethod
    def _presence_v2_crop(frame: np.ndarray, rect) -> Optional[np.ndarray]:
        if rect is None:
            return None
        x, y, width, height = map(int, rect)
        image = np.asarray(frame)
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > image.shape[1] or y + height > image.shape[0]:
            return None
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return image[max(0, y):max(0, y + height), max(0, x):max(0, x + width)].copy()

    def _validate_presence_v2_samples(self, tool, dirs):
        try:
            summary, weak = LearningDataset().validate(
                tool, dirs, self._presence_learning_signature(tool, self._active_view_id)
            )
        except ValueError as exc:
            self._warn(str(exc))
            return
        self._tool_panel.show_presence_validation(summary, weak_dataset=weak)

    def _presence_v2_evaluation_kwargs(self, tool, *, include_score=True):
        return LearningDataset().evaluation_kwargs(tool, include_score=include_score)

    def _on_locator_roi_changed(self, target: str, area: object) -> None:
        if self._syncing_workspace_roi:
            return
        row = getattr(self, "_selected_tool_row", -1)
        view_id = self._active_view_id
        if row < 0 or not view_id:
            return
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not (0 <= row < len(tools)) or tools[row].type != "locator.template_match":
            return
        tool = tools[row]
        roi = ToolRoi.from_obj(area)
        params = dict(getattr(tool.params, "values", {}) or {})
        if target == "search":
            tool.roi = roi
        elif target == "template":
            tool.template_roi = roi
            params["template_roi"] = roi.to_dict() or None
        else:
            return
        tool.params = ToolParams(params)
        try:
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Uloženie Locator ROI zlyhalo: {exc}")
            return
        self.view.set_tool_overlay(tool)
        self._tool_panel.refresh_geometry(tool)
        self._update_dirty_state(recipe, view_id)
        label = {"search": "Hľadanie", "template": "Šablóna"}[target]
        self._status_bar.setText(f"Locator | {label}: aktualizované | Koncept uložený")

    def _refresh_view_metadata(self):
        return self._view_controller._refresh_view_metadata()

    def _on_tool_name_changed(self, name: str) -> None:
        row = self._selected_tool_row
        view_id = self._active_view_id
        if not view_id or row < 0 or not name.strip():
            return
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if row >= len(tools):
            return
        tool = Tool.from_dict(tools[row].to_dict())
        tool.name = name.strip()
        try:
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Uloženie názvu nástroja zlyhalo: {exc}")
            self._refresh_tool_panel_for_selection()
            return
        self._refresh_tools_table()
        self._update_dirty_state(recipe, view_id)

    def _on_tool_param_changed(self, name: str, value: Any) -> None:
        self._filtered_roi_timer.start()
        row = getattr(self, "_selected_tool_row", -1)
        if row < 0:
            return
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        if not view_id:
            return
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not (0 <= row < len(tools)):
            return
        tool = tools[row]
        params = dict(getattr(tool.params, "values", {}) or {})
        params[name] = value
        tool.params = ToolParams(params)
        try:
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Uloženie parametra zlyhalo: {exc}")
            self._refresh_tools_table()
            return
        self._tool_panel.refresh_values(tool)
        if tool.type == "edge_profile_deviation" and name == "search_half_window":
            self.roi_editor.set_edge_search_half_window(int(value))
        if tool.type == "locator.template_match" and name in ("use_golden_crop", "alignment_mode"):
            self._syncing_workspace_roi = True
            try:
                self._configure_workspace_editor(tool)
            finally:
                self._syncing_workspace_roi = False
        if tool.type == "locator.template_match" and name == "reference_search_half_window":
            self.roi_editor.set_edge_search_half_window(int(value))
        self._update_dirty_state(recipe, view_id)

    def _on_tool_threshold_changed(self, name: str, value: Any) -> None:
        self._filtered_roi_timer.start()
        row = getattr(self, "_selected_tool_row", -1)
        if row < 0:
            return
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        if not view_id:
            return
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if not (0 <= row < len(tools)):
            return
        tool = tools[row]
        thresholds = dict(getattr(tool.thresholds, "values", {}) or {})
        thresholds[name] = value
        tool.thresholds = ToolThresholds(thresholds)
        try:
            self.recipes.update_tool(recipe, row, tool, view_id=view_id)
        except Exception as exc:
            self._err(f"Uloženie thresholdu zlyhalo: {exc}")
            self._refresh_tools_table()
            return
        self._tool_panel.refresh_values(tool)
        self._update_dirty_state(recipe, view_id)

    def _on_tool_test_requested(self, params: dict[str, Any], thresholds: dict[str, Any]) -> None:
        self.roi_editor.set_result_overlay(None)
        try:
            row = getattr(self, "_selected_tool_row", -1)
            if row < 0:
                self._tool_panel.show_test_error("Najprv vyber nástroj v tabuľke.")
                return

            recipe = self._current_recipe_name()
            view_id = self._active_view_id
            if not view_id:
                self._tool_panel.show_test_error("Nie je vybraný žiadny view.")
                return
            tools = self.recipes.get_draft_tools(recipe, view_id)
            if not (0 <= row < len(tools)):
                self._tool_panel.show_test_error("Vybraný nástroj nie je dostupný.")
                return

            target_tool = tools[row]
            golden = self._current_golden_image()
            if golden is None:
                self._tool_panel.show_test_error("Nie je dostupný GOLDEN obrázok.")
                return

            def _format_px(value: int) -> str:
                return f"{int(value):,}".replace(",", " ")

            status_messages: list[str] = []

            roi_rect = target_tool.roi.rect()
            if roi_rect is not None:
                _, _, roi_w, roi_h = roi_rect
                roi_area = max(0, int(roi_w) * int(roi_h))
                if roi_area > MAX_ROI_PIXELS:
                    limit_text = _format_px(MAX_ROI_PIXELS)
                    area_text = _format_px(roi_area)
                    self._tool_panel.show_test_error(
                        f"ROI je príliš veľká pre test ({area_text} px > {limit_text} px). Zmenši výber."
                    )
                    return
                if roi_area > ROI_WARN_PIXELS:
                    status_messages.append(
                        f"ROI {_format_px(roi_area)} px je veľká – test môže chvíľu trvať."
                    )

            mask_value = getattr(target_tool.ignore_mask, "value", None)
            if mask_value is not None:
                mask_array = np.asarray(mask_value)
                mask_pixels = int(np.count_nonzero(mask_array))
                if mask_pixels > MAX_MASK_PIXELS:
                    limit_text = _format_px(MAX_MASK_PIXELS)
                    count_text = _format_px(mask_pixels)
                    self._tool_panel.show_test_error(
                        f"Maska je príliš veľká pre test ({count_text} px > {limit_text} px). Zmenši masku."
                    )
                    return
                if mask_pixels > MASK_WARN_PIXELS:
                    status_messages.append(
                        f"Maska má {_format_px(mask_pixels)} px – výkon bude nižší."
                    )

            frame: Optional[np.ndarray] = None
            capture_errors: list[str] = []
            if callable(self._capture_frame_for_golden):
                try:
                    frame = self._capture_frame_for_golden(
                        view_id=view_id,
                        trigger_mode_label="golden_tool_test",
                        image_rotation_override=0,
                        capture_request_source="tool_test",
                    )
                except Exception as exc:
                    capture_errors.append(f"Zachytenie zlyhalo: {exc}")
            if frame is None:
                message = capture_errors[-1] if capture_errors else "Frame nie je dostupný."
                self._tool_panel.show_test_error(message)
                return

            active_view = self._view_by_id(self._active_view_id)
            frame = apply_view_image_transform(frame, active_view, stage="inspection")
            frame_array = np.asarray(frame)

            preceding_tools = [tool.copy() for tool in tools if tool.order < target_tool.order
                               or (tool.type.startswith("locator.") and not target_tool.type.startswith("locator."))]
            preceding_tools.sort(key=lambda tool: tool.order)

            # The properties panel only submits fields it renders.  Preserve
            # tool-specific stored values (for example Edge Profile A/B
            # anchors) which are intentionally edited on the canvas instead.
            params_payload = dict(getattr(target_tool.params, "values", {}) or {})
            params_payload.update(params or {})
            thresholds_payload = dict(getattr(target_tool.thresholds, "values", {}) or {})
            thresholds_payload.update(thresholds or {})

            target_copy = target_tool.copy()
            target_copy.params = ToolParams(params_payload)
            target_copy.thresholds = ToolThresholds(thresholds_payload)
            target_copy.enabled = True

            pipeline_tools = preceding_tools + [target_copy]

            test_view = active_view.copy()
            test_view.set_tools(pipeline_tools)
            test_recipe = RecipeV2(
                views=[test_view],
                pose_enabled=self.chk_pose.isChecked(),
                regions=[],
                tools=pipeline_tools,
                on_locator_failure=(
                    self._current_locator_failure_policy or "continue_without_alignment"
                ),
                export_artifacts=False,
            )

            test_run = run_tool_test(golden, frame_array, test_recipe)
            result = test_run.result

            perf_breakdown: list[dict[str, Any]] = []
            for report in test_run.reports:
                diagnostics = report.diagnostics if isinstance(report.diagnostics, dict) else {}
                timings = diagnostics.get("timings_ms") if isinstance(diagnostics, dict) else None
                perf_breakdown.append(
                    {
                        "tool": report.tool.name or report.tool.type,
                        "tool_id": report.tool_id,
                        "type": report.tool.type,
                        "latency_ms": float(report.latency_ms),
                        "timings": timings if isinstance(timings, dict) else None,
                    }
                )

            overlay_preview_img: Optional[np.ndarray] = None
            overlay_items_preview = list(test_run.overlay_items or [])
            frame_for_overlay = (
                test_run.context.frame_aligned
                if getattr(test_run.context, "frame_aligned", None) is not None
                else frame_array
            )
            if overlay_items_preview and isinstance(frame_for_overlay, np.ndarray):
                overlay_image = overlay_utils.render_overlay(
                    frame_for_overlay.shape[:2], overlay_items_preview
                )
                if overlay_image is not None:
                    overlay_preview_img = overlay_utils.apply_overlay(
                        frame_for_overlay, overlay_image
                    )

            if overlay_preview_img is not None:
                artifacts = result.debug_artifacts
                if not isinstance(artifacts, dict):
                    artifacts = {}
                    result.debug_artifacts = artifacts
                preview_payload: dict[str, Any] = {}
                existing_preview = artifacts.get("preview") if artifacts else None
                if isinstance(existing_preview, dict):
                    preview_payload.update(existing_preview)
                preview_payload.setdefault("before", frame_array)
                preview_payload.setdefault("aligned", frame_for_overlay)
                preview_payload["overlay"] = overlay_preview_img
                artifacts["preview"] = preview_payload

            failure_entry = next(
                (entry for entry in test_run.diagnostics if entry.get("locator_failure")),
                None,
            )
            if failure_entry:
                reason_map = {
                    "not_found": "nenašiel pozíciu",
                    "low_corr": "nízka korelácia",
                    "status_nok": "stav NOK",
                }
                reason_raw = failure_entry.get("locator_failure_reason")
                reason_text = reason_map.get(str(reason_raw), str(reason_raw))
                policy = failure_entry.get("policy_applied") or test_run.policy_applied
                policy_text = (
                    "pokračovanie bez zarovnania"
                    if policy == "continue_without_alignment"
                    else str(policy)
                )
                tool_label = failure_entry.get("tool_id") or failure_entry.get("type") or "locator"
                status_messages.append(
                    f"Locator '{tool_label}' zlyhal ({reason_text}). Politika: {policy_text}."
                )

            status_message = "\n".join(status_messages) if status_messages else None
            self._tool_panel.show_test_result(
                result,
                test_run.elapsed_ms,
                perf_breakdown=perf_breakdown,
                status_message=status_message,
            )
            result_rect = target_tool.roi.rect()
            is_locator = target_tool.type == "locator.template_match"
            if is_locator:
                template_rect = ToolRoi.from_obj(params_payload.get("template_roi")).rect()
                if template_rect is None:
                    template_rect = target_tool.template_roi.rect()
                if template_rect is not None:
                    dx = int(round(float((result.metrics or {}).get("dx", 0.0))))
                    dy = int(round(float((result.metrics or {}).get("dy", 0.0))))
                    x, y, width, height = template_rect
                    result_rect = (x + dx, y + dy, width, height)
            self.roi_editor.set_result_overlay(
                result.status,
                dict(result.metrics or {}),
                result_rect,
                locator=is_locator,
            )
            cache_key = (view_id, int(target_tool.order), target_tool.type)
            self._last_tool_results[cache_key] = (
                result, test_run.elapsed_ms, perf_breakdown, status_message, result_rect
            )
        except Exception as exc:
            self._tool_panel.show_test_error(f"Test zlyhal: {exc}")
        finally:
            self._tool_panel.set_test_running(False)

    def _restore_tool_result(self, tool: Tool) -> None:
        view_id = self._active_view_id
        if not view_id:
            return
        cached = self._last_tool_results.get((view_id, int(tool.order), tool.type))
        if cached is None:
            return
        result, elapsed_ms, breakdown, message, rect = cached
        self._tool_panel.show_test_result(
            result, elapsed_ms, perf_breakdown=breakdown, status_message=message
        )
        self.roi_editor.set_result_overlay(
            result.status,
            dict(result.metrics or {}),
            rect,
            locator=tool.type == "locator.template_match",
        )

    def _edit_tool(self, index: int):
        if not self._active_view_id:
            return
        tools = self.recipes.get_draft_tools(
            self._current_recipe_name(), self._active_view_id
        )
        if not 0 <= index < len(tools):
            return
        self.tools_table.setCurrentCell(index, 1)
        self._refresh_tool_panel_for_selection()
        if tools[index].type == "edge_profile_deviation":
            self.roi_editor.set_edit_context("edge")
        self._tool_panel.setFocus(Qt.OtherFocusReason)

    def _persist_tools(self, recipe: str) -> tuple[bool, bool]:
        view_id = self._active_view_id
        if not view_id:
            self._err("Nie je vybraný žiadny view.")
            return False, False
        tools = self.recipes.get_draft_tools(recipe, view_id)
        try:
            _, autosorted = self.recipes.save_tools(recipe, tools, view_id=view_id)
        except Exception as exc:
            self._err(f"Ukladanie nástrojov zlyhalo: {exc}")
            return False, False
        return True, autosorted

    # ---------- Shutdown ----------
    def closeEvent(self, e):
        if self._has_unsaved_changes():
            result = QMessageBox.question(
                self,
                "Neuložené zmeny",
                "Máte neuložené zmeny. Naozaj chcete zavrieť bez uloženia?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if result != QMessageBox.Yes:
                e.ignore()
                return
        self._stop_preview_session()
        e.accept()

"""Tool parameter and diagnostics panel."""
from __future__ import annotations
from app.utils.tool_labels import tool_display_name
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit, QCheckBox, QTableWidget, QTableWidgetItem, QWidget, QAbstractItemView, QFormLayout, QLayout, QSpinBox, QDoubleSpinBox, QGroupBox, QSizePolicy, QButtonGroup, QToolButton
import math
from typing import Any, Dict, Optional
import numpy as np
from app.models.schema import Tool, ToolDefinition, ToolMetricSpec, ToolParams, ToolRoi, ToolThresholds
from app.services.tool_contracts import ToolRunResult
from app.services.golden_wizard_logic import _SUPPORTED_FORM_FIELD_TYPES, _validate_params_and_thresholds
from app.ui.golden_wizard.form_widgets import _create_form_widget, _format_spec_tooltip, _get_form_widget_value, _set_form_widget_value
from app.ui.golden_wizard.style import field_label, metric_label
from app.services.learning_context import STATISTICAL_TYPES


class CollapsibleSection(QWidget):
    """Compact reusable section for the properties inspector."""

    def __init__(self, title: str, content: QWidget, *, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("goldenWizard")
        self._header = QToolButton(self)
        self._header.setObjectName("sectionHeader")
        self._header.setText(title)
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._content = content
        self._content.setVisible(expanded)
        self._header.toggled.connect(self._toggle)
        section_layout = QVBoxLayout(self)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(3)
        section_layout.addWidget(self._header)
        section_layout.addWidget(self._content)

    def _toggle(self, expanded: bool) -> None:
        self._header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._content.setVisible(expanded)

    def set_title(self, title: str) -> None:
        self._header.setText(title)


class ToolConfigPanel(QWidget):
    """Side panel for editing tool parameters and thresholds."""

    nameChanged = Signal(str)
    paramChanged = Signal(str, object)
    thresholdChanged = Signal(str, object)
    testRequested = Signal(dict, dict)
    locatorPolicyWarningChanged = Signal(str)
    testButtonEnabledChanged = Signal(bool)
    locatorAreaRequested = Signal(str)
    locatorFitSearchRequested = Signal()
    presenceLearningRequested = Signal(str)
    emptyMoldV2Requested = Signal()

    _STATUS_COLORS = {"ok": "#237804", "warn": "#b36b00", "nok": "#b03030"}

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._current_tool: Optional[Tool] = None
        self._presence_is_mold = False
        self._param_specs: dict[str, dict[str, Any]] = {}
        self._threshold_specs: dict[str, dict[str, Any]] = {}
        self._current_metrics_spec: list[ToolMetricSpec] = []
        self._param_widgets: dict[str, QWidget] = {}
        self._threshold_widgets: dict[str, QWidget] = {}
        self._updating = False
        self._param_wrappers: dict[str, QWidget] = {}
        self._threshold_wrappers: dict[str, QWidget] = {}
        self._param_labels: dict[str, QLabel] = {}
        self._param_error_labels: dict[str, QLabel] = {}
        self._threshold_error_labels: dict[str, QLabel] = {}
        self._validation_ok: bool = True
        self._current_form_values: dict[str, dict[str, Any]] = {"params": {}, "thresholds": {}}
        self._last_normalized: dict[str, dict[str, Any]] = {"params": {}, "thresholds": {}}
        self._preview_cache: dict[str, QPixmap] = {}
        self._preview_before_key: Optional[str] = None
        self._preview_aligned_key: Optional[str] = None
        self._preview_binarized_key: Optional[str] = None
        self._preview_overlay_key: Optional[str] = None
        self._active_preview_key: Optional[str] = None
        self._locator_failure_policy: str = "continue_without_alignment"

        layout = QVBoxLayout(self)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("VLASTNOSTI", self)
        title.setProperty("role", "panelHeader")
        layout.addWidget(title)

        self._tool_label = QLabel("", self)
        self._tool_label.setWordWrap(True)
        self._tool_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._tool_label)

        layout.addWidget(QLabel("Názov nástroja", self))
        self._name_input = QLineEdit(self)
        self._name_input.setEnabled(False)
        self._name_input.editingFinished.connect(self._on_name_edited)
        layout.addWidget(self._name_input)

        self._description_label = QLabel("", self)
        self._description_label.setStyleSheet("color: #666;")
        self._description_label.setWordWrap(True)
        layout.addWidget(self._description_label)
        self._empty_mold_v2_button = QPushButton("V2: Kavity, vzorky a model", self)
        self._empty_mold_v2_button.clicked.connect(self.emptyMoldV2Requested.emit)
        self._empty_mold_v2_button.hide()
        layout.addWidget(self._empty_mold_v2_button)

        self._form_container = QWidget(self)
        self._form_layout = QFormLayout(self._form_container)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setSpacing(6)
        self._form_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self._threshold_container = QWidget(self)
        self._threshold_layout = QFormLayout(self._threshold_container)
        self._threshold_layout.setContentsMargins(0, 0, 0, 0)
        self._threshold_layout.setSpacing(6)
        self._threshold_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self._presence_live_values: Optional[QLabel] = None
        geometry_content = QWidget(self)
        geometry_layout = QVBoxLayout(geometry_content)
        geometry_layout.setContentsMargins(0, 0, 0, 0)
        geometry_layout.setSpacing(6)
        self._geometry_summary = QLabel(
            "Nie je vybraný nástroj\nPridajte nástroj alebo ho vyberte zo zoznamu.", self
        )
        self._geometry_summary.setWordWrap(True)
        self._geometry_summary.setStyleSheet("color: #aab2bc; padding: 8px;")
        geometry_layout.addWidget(self._geometry_summary)
        self._locator_geometry_actions = QWidget(self)
        locator_actions = QVBoxLayout(self._locator_geometry_actions)
        locator_actions.setContentsMargins(0, 0, 0, 0)
        for text, target in (("Vybrať oblasť hľadania", "search"),
                             ("Vybrať oblasť šablóny", "template")):
            button = QPushButton(text, self._locator_geometry_actions)
            button.clicked.connect(lambda _checked=False, value=target: self.locatorAreaRequested.emit(value))
            locator_actions.addWidget(button)
            setattr(self, f"_locator_{target}_button", button)
        fit_search = QPushButton("Prispôsobiť hľadanie šablóne", self._locator_geometry_actions)
        fit_search.clicked.connect(self.locatorFitSearchRequested.emit)
        locator_actions.addWidget(fit_search)
        self._locator_fit_button = fit_search
        geometry_layout.addWidget(self._locator_geometry_actions)
        self._locator_geometry_actions.hide()
        self._geometry_section = CollapsibleSection("Geometria", geometry_content, parent=self)
        self._detection_section = CollapsibleSection("Detekcia", self._form_container, parent=self)
        self._threshold_section = CollapsibleSection("Prahy", self._threshold_container, parent=self)
        layout.addWidget(self._geometry_section)

        learning_content = QWidget(self)
        learning_layout = QVBoxLayout(learning_content)
        learning_layout.setContentsMargins(0, 0, 0, 0)
        learning_layout.setSpacing(6)
        self._presence_model_state = QLabel("● Nenaučený", learning_content)
        self._presence_counts = QLabel("OK vzorky: 0 / 30\nNOK vzorky: 0", learning_content)
        self._presence_warning = QLabel("", learning_content)
        self._presence_warning.setWordWrap(True)
        self._presence_warning.setStyleSheet("color: #d29922;")
        learning_layout.addWidget(self._presence_model_state)
        learning_layout.addWidget(self._presence_counts)
        learning_layout.addWidget(self._presence_warning)
        capture_row = QHBoxLayout()
        self._presence_capture_buttons: dict[str, QPushButton] = {}
        self._presence_capture_labels: dict[str, QLabel] = {}
        for label, action in (("Zbierať OK", "capture_ok"), ("Zbierať NOK", "capture_nok")):
            column = QVBoxLayout()
            caption = QLabel("", learning_content)
            caption.setWordWrap(True)
            column.addWidget(caption)
            button = QPushButton(label, learning_content)
            button.clicked.connect(
                lambda _checked=False, value=action: self.presenceLearningRequested.emit(value)
            )
            column.addWidget(button)
            capture_row.addLayout(column, 1)
            self._presence_capture_labels[action] = caption
            self._presence_capture_buttons[action] = button
        learning_layout.addLayout(capture_row)
        self._presence_rebuild = QPushButton("Prepočítať model", learning_content)
        self._presence_rebuild.clicked.connect(
            lambda: self.presenceLearningRequested.emit("rebuild")
        )
        learning_layout.addWidget(self._presence_rebuild)
        self._presence_recommended = QLabel("Základný odhad modelu nie je dostupný.", learning_content)
        self._presence_recommended.setWordWrap(True)
        learning_layout.addWidget(self._presence_recommended)
        reset_learning = QPushButton("Resetovať učenie", learning_content)
        reset_learning.setProperty("role", "destructive")
        reset_learning.clicked.connect(lambda: self.presenceLearningRequested.emit("reset"))
        learning_layout.addWidget(reset_learning)
        self._presence_learning_section = CollapsibleSection("Učenie", learning_content, parent=self)
        self._presence_learning_section.hide()
        layout.addWidget(self._presence_learning_section)

        layout.addWidget(self._detection_section)
        layout.addWidget(self._threshold_section)

        validation_content = QWidget(self)
        validation_layout = QVBoxLayout(validation_content)
        validation_layout.setContentsMargins(0, 0, 0, 0)
        validation_layout.setSpacing(6)
        validate_samples = QPushButton("Otestovať vzorky", validation_content)
        validate_samples.clicked.connect(lambda: self.presenceLearningRequested.emit("validate"))
        auto_settings = QPushButton("Automaticky navrhnúť nastavenia", validation_content)
        auto_settings.clicked.connect(
            lambda: self.presenceLearningRequested.emit("auto_settings")
        )
        self._presence_validation_result = QLabel("Zatiaľ bez výsledku", validation_content)
        self._presence_validation_result.setWordWrap(True)
        self._presence_tuning_result = QLabel("", validation_content)
        self._presence_tuning_result.setWordWrap(True)
        self._presence_tuning_result.hide()
        self._presence_apply_tuned = QPushButton("Použiť odporúčané nastavenie", validation_content)
        self._presence_apply_tuned.clicked.connect(
            lambda: self.presenceLearningRequested.emit("apply_auto_settings")
        )
        self._presence_apply_tuned.hide()
        validation_layout.addWidget(validate_samples)
        validation_layout.addWidget(auto_settings)
        validation_layout.addWidget(self._presence_validation_result)
        validation_layout.addWidget(self._presence_tuning_result)
        validation_layout.addWidget(self._presence_apply_tuned)
        self._presence_validation_section = CollapsibleSection(
            "Validácia", validation_content, parent=self
        )
        self._presence_validation_section.hide()
        layout.addWidget(self._presence_validation_section)
        self._advanced_container = QWidget(self)
        self._advanced_layout = QFormLayout(self._advanced_container)
        for form in (self._form_layout, self._threshold_layout, self._advanced_layout):
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            form.setVerticalSpacing(10)
            form.setHorizontalSpacing(12)
        self._advanced_layout.setContentsMargins(0, 0, 0, 0)
        self._advanced_layout.setSpacing(6)
        self._advanced_section = CollapsibleSection(
            "Pokročilé", self._advanced_container, expanded=False, parent=self
        )
        layout.addWidget(self._advanced_section)

        self._form_error_label = QLabel("", self)
        self._form_error_label.setStyleSheet("color: #b03030; padding-top: 4px;")
        self._form_error_label.setWordWrap(True)
        self._form_error_label.setVisible(False)
        layout.addWidget(self._form_error_label)

        self._placeholder_label = QLabel(
            "Vyber v tabuľke nástroj pre úpravu parametrov a prahov.",
            self,
        )
        self._placeholder_label.setStyleSheet("color: #666; font-style: italic;")
        self._placeholder_label.setWordWrap(True)
        self._placeholder_label.setAlignment(Qt.AlignTop)
        layout.addWidget(self._placeholder_label, 1)

        layout.addStretch(1)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)

        self._btn_test = QPushButton("Otestovať", self)
        self._btn_test.clicked.connect(self._on_test_clicked)

        self._btn_defaults = QPushButton("Obnoviť predvolené", self)
        self._btn_defaults.clicked.connect(self._on_restore_defaults)

        controls_layout.addWidget(self._btn_test)
        controls_layout.addWidget(self._btn_defaults)
        controls_layout.addStretch(1)
        layout.addLayout(controls_layout)

        self._diagnostics_group = QGroupBox("Diagnostika", self)
        diag_layout = QVBoxLayout(self._diagnostics_group)
        diag_layout.setContentsMargins(8, 8, 8, 8)
        diag_layout.setSpacing(6)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)

        self._status_indicator = QLabel(self._diagnostics_group)
        self._status_indicator.setFixedSize(12, 12)
        status_row.addWidget(self._status_indicator)

        self._status_value_label = QLabel("—", self._diagnostics_group)
        self._status_value_label.setStyleSheet("font-weight: 600;")
        status_row.addWidget(self._status_value_label)
        status_row.addStretch(1)
        diag_layout.addLayout(status_row)

        self._status_message_label = QLabel("", self._diagnostics_group)
        self._status_message_label.setStyleSheet("color: #666; font-size: 11px;")
        self._status_message_label.setWordWrap(True)
        self._status_message_label.setVisible(False)
        diag_layout.addWidget(self._status_message_label)

        self._latency_label = QLabel("Čas: —", self._diagnostics_group)
        self._latency_label.setStyleSheet("color: #888;")
        diag_layout.addWidget(self._latency_label)

        self._perf_overlay_label = QLabel("", self._diagnostics_group)
        self._perf_overlay_label.setStyleSheet(
            "color: #999; font-family: 'JetBrains Mono', 'Courier New', monospace; font-size: 11px;"
        )
        self._perf_overlay_label.setWordWrap(True)
        self._perf_overlay_label.setVisible(False)
        diag_layout.addWidget(self._perf_overlay_label)

        self._metrics_table = QTableWidget(0, 2, self._diagnostics_group)
        self._metrics_table.setHorizontalHeaderLabels(["Metrika", "Hodnota"])
        self._metrics_table.horizontalHeader().setStretchLastSection(True)
        self._metrics_table.verticalHeader().setVisible(False)
        self._metrics_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._metrics_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._metrics_table.setFocusPolicy(Qt.NoFocus)
        self._metrics_table.setVisible(False)
        diag_layout.addWidget(self._metrics_table)

        self._preview_toggle_container = QWidget(self._diagnostics_group)
        toggle_layout = QHBoxLayout(self._preview_toggle_container)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(8)

        self._preview_toggle_aligned = QCheckBox("Náhľad zarovnania", self._preview_toggle_container)
        self._preview_toggle_aligned.toggled.connect(
            lambda checked: self._on_preview_toggle_changed("aligned", checked)
        )
        toggle_layout.addWidget(self._preview_toggle_aligned)

        self._preview_toggle_binarized = QCheckBox("Náhľad binarizácie", self._preview_toggle_container)
        self._preview_toggle_binarized.toggled.connect(
            lambda checked: self._on_preview_toggle_changed("binarization", checked)
        )
        toggle_layout.addWidget(self._preview_toggle_binarized)
        self._preview_toggle_overlay = QCheckBox("Náhľad prekrytia", self._preview_toggle_container)
        self._preview_toggle_overlay.toggled.connect(
            lambda checked: self._on_preview_toggle_changed("overlay", checked)
        )
        toggle_layout.addWidget(self._preview_toggle_overlay)
        toggle_layout.addStretch(1)

        self._preview_button_group = QButtonGroup(self)
        self._preview_button_group.setExclusive(True)
        self._preview_button_group.addButton(self._preview_toggle_aligned)
        self._preview_button_group.addButton(self._preview_toggle_binarized)
        self._preview_button_group.addButton(self._preview_toggle_overlay)

        diag_layout.addWidget(self._preview_toggle_container)

        self._preview_widget = QWidget(self._diagnostics_group)
        preview_layout = QHBoxLayout(self._preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(8)

        self._preview_before_label = QLabel("Náhľad nie je dostupný", self._preview_widget)
        self._preview_before_label.setAlignment(Qt.AlignCenter)
        self._preview_before_label.setMinimumSize(160, 160)
        self._preview_before_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._preview_before_label.setStyleSheet(
            "background-color: #111; color: #777; border: 1px solid #333;"
        )
        self._preview_before_label.setScaledContents(True)
        preview_layout.addWidget(self._preview_before_label, 1)

        self._preview_after_label = QLabel("Náhľad nie je dostupný", self._preview_widget)
        self._preview_after_label.setAlignment(Qt.AlignCenter)
        self._preview_after_label.setMinimumSize(160, 160)
        self._preview_after_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._preview_after_label.setStyleSheet(
            "background-color: #111; color: #777; border: 1px solid #333;"
        )
        self._preview_after_label.setScaledContents(True)
        preview_layout.addWidget(self._preview_after_label, 1)

        diag_layout.addWidget(self._preview_widget)

        layout.addWidget(CollapsibleSection("Výsledok", self._diagnostics_group, parent=self))

        self._update_visibility()
        self._reset_diagnostics()
        self.testButtonEnabledChanged.emit(self._btn_test.isEnabled())

    def is_test_enabled(self) -> bool:
        return self._btn_test.isEnabled()

    def _set_test_button_enabled(self, enabled: bool) -> None:
        if self._btn_test.isEnabled() == enabled:
            return
        self._btn_test.setEnabled(enabled)
        self.testButtonEnabledChanged.emit(enabled)

    def _on_name_edited(self) -> None:
        if self._current_tool is None:
            return
        name = self._name_input.text().strip()
        if not name:
            self._name_input.setText(tool_display_name(self._current_tool))
            return
        if name != tool_display_name(self._current_tool):
            self.nameChanged.emit(name)

    def clear(self) -> None:
        self._current_tool = None
        self._empty_mold_v2_button.hide()
        self._name_input.clear()
        self._name_input.setEnabled(False)
        self._param_specs.clear()
        self._threshold_specs.clear()
        self._current_metrics_spec = []
        self._clear_form()
        self._tool_label.setText("Nie je vybraný nástroj")
        self._description_label.clear()
        self._geometry_summary.setText(
            "Nie je vybraný nástroj\nPridajte nástroj alebo ho vyberte zo zoznamu."
        )
        self._locator_geometry_actions.hide()
        self._presence_learning_section.hide()
        self._presence_validation_section.hide()
        self._geometry_section.set_title("Geometria")
        self._detection_section.set_title("Detekcia")
        self._threshold_section.set_title("Prahy")
        self._clear_test_result()
        self._update_visibility()
        self.locatorPolicyWarningChanged.emit("")

    def set_tool(
        self,
        tool: Tool,
        meta: ToolDefinition,
        schema: dict[str, dict[str, dict[str, Any]]],
    ) -> None:
        self._current_tool = tool
        self._empty_mold_v2_button.setVisible(tool.type == "mold.protection_v2")
        self._name_input.setText(tool_display_name(tool))
        self._name_input.setEnabled(True)
        self._param_specs = {k: dict(v) for k, v in (schema.get("params") or {}).items()}
        self._threshold_specs = {
            k: dict(v) for k, v in (schema.get("thresholds") or {}).items()
        }
        self._current_metrics_spec = list(getattr(meta, "metrics_spec", []) or [])

        tool_kind = getattr(meta, "name", "") or getattr(meta, "category", "") or "Nástroj"
        self._tool_label.setText(tool_kind if tool_display_name(tool) == tool_kind else f"{tool_display_name(tool)} — {tool_kind}")
        description = getattr(meta, "description", "") or ""
        self._description_label.setText(description)
        self._description_label.setVisible(bool(description))
        self.refresh_geometry(tool)
        is_locator = tool.type == "locator.template_match"
        is_presence_v2 = tool.type in STATISTICAL_TYPES
        self._presence_is_mold = tool.type == "mold.protection_v1"
        self._presence_capture_labels["capture_ok"].setText(
            "Prázdna forma (OK)" if self._presence_is_mold else "Vzorky OK"
        )
        self._presence_capture_labels["capture_nok"].setText(
            "Zvyšky vo forme (NOK)" if self._presence_is_mold else "Vzorky NOK"
        )
        self._geometry_section.set_title(
            "Oblasť hľadania" if is_locator else
            "Oblasť kontroly" if is_presence_v2 else "Geometria"
        )
        self._detection_section.set_title(
            "Zarovnanie" if is_locator else "Detekcia"
        )
        self._threshold_section.set_title(
            "Prijatie výsledku" if is_locator else "Citlivosť" if is_presence_v2 else "Prahy"
        )
        self._locator_geometry_actions.setVisible(is_locator)
        capabilities = getattr(meta, "meta", meta)
        self._presence_learning_section.setVisible(is_presence_v2)
        self._presence_validation_section.setVisible(is_presence_v2)
        if is_locator:
            params = dict(getattr(tool.params, "values", {}) or {})
            use_crop = bool(params.get("use_golden_crop", False))
            self._locator_template_button.setEnabled(not use_crop)
            has_template = (ToolRoi.from_obj(params.get("template_roi")).rect() is not None
                            or tool.template_roi.rect() is not None)
            self._locator_fit_button.setEnabled(has_template and not use_crop)

        self._rebuild_form()
        self._geometry_section.setVisible(
            bool(getattr(capabilities, "supports_roi", False)) or is_locator
        )
        self._detection_section.setVisible(not is_presence_v2 and self._form_layout.rowCount() > 1)
        self._threshold_section.setVisible(self._threshold_layout.rowCount() > 0)
        self._advanced_section.setVisible(self._advanced_layout.rowCount() > 0)
        self._clear_test_result()
        self._update_visibility()
        self.locatorPolicyWarningChanged.emit("")

    def refresh_presence_learning(
        self,
        tool: Tool,
        *,
        compatible_ok: Optional[int] = None,
        nok_count: Optional[int] = None,
        recommended: Optional[dict[str, float]] = None,
        validation_text: Optional[str] = None,
    ) -> None:
        if tool.type not in STATISTICAL_TYPES:
            return
        params = dict(tool.params.values or {})
        ok_count = int(params.get("sample_count_ok", 0) if compatible_ok is None else compatible_ok)
        nok_value = int(params.get("sample_count_nok", 0) if nok_count is None else nok_count)
        target = int(params.get("recommended_ok_samples", 30) or 30)
        minimum = int(params.get("min_ok_samples", 15) or 15)
        invalid = bool(params.get("reference_model_invalidated", False))
        ready = bool(params.get("reference_model_ready", False)) and not invalid
        if invalid:
            state, color = "● Neplatný", "#d29922"
            warning = "⚠ Model je neplatný\nROI alebo Ignore Mask boli zmenené."
        elif ready:
            state, color, warning = "● Pripravený", "#22c55e", ""
        else:
            state, color = "● Nenaučený", "#8d96a0"
            warning = f"⚠ Minimum pre model: {minimum}" if ok_count < minimum else ""
        self._presence_model_state.setText(state)
        self._presence_model_state.setStyleSheet(f"color: {color}; font-weight: 600;")
        if self._presence_is_mold:
            self._presence_counts.setText(
                f"Prázdna forma: {ok_count} / {target}\nVzorky so zvyškom: {nok_value}"
            )
        else:
            self._presence_counts.setText(
                f"OK vzorky: {ok_count} / {target}\nNOK vzorky: {nok_value}"
            )
        self._presence_warning.setText(warning)
        self._presence_rebuild.setEnabled(tool.roi.rect() is not None and ok_count >= minimum)
        values = dict(recommended or {})
        if values:
            self._presence_recommended.setText(
                "Základný odhad modelu:\n"
                f"Prah odchýlky: {float(values.get('score_threshold', 0)):.2f}\n"
                f"Max. anomálna plocha: {float(values.get('total_area_threshold', 0)):.0f} px\n"
                f"Min. veľkosť objektu: {float(values.get('min_blob_area', 0)):.0f} px"
            )
        else:
            self._presence_recommended.setText("Základný odhad modelu nie je dostupný.")
        self._presence_tuning_result.hide()
        self._presence_apply_tuned.hide()
        if validation_text is not None:
            self._presence_validation_result.setText(validation_text)

    def show_presence_validation(
        self, summary: dict[str, Any], *, weak_dataset: bool = False
    ) -> None:
        ok_total, nok_total = int(summary["ok_total"]), int(summary["nok_total"])
        false_rejects = int(summary["false_reject_count"])
        false_accepts = int(summary["false_accept_count"])
        ok_label = "Prázdna forma" if self._presence_is_mold else "OK vzorky"
        nok_label = "Vzorky so zvyškom" if self._presence_is_mold else "NOK vzorky"
        nok_line = (
            f"{nok_label}\n{int(summary['nok_correct'])} / {nok_total} správne"
            if nok_total else f"{nok_label}: bez vzoriek"
        )
        false_accept_line = (
            f"False Accept\n{false_accepts} / {nok_total} = "
            f"{float(summary['false_accept_rate']) * 100:.1f} %"
            if nok_total else "False Accept\nbez NOK vzoriek"
        )
        warning = (
            "\n\n⚠ Validačný dataset je malý. Výsledok môže byť nespoľahlivý."
            if weak_dataset else ""
        )
        text = (
            f"{ok_label}\n{int(summary['ok_correct'])} / {ok_total} správne\n\n"
            f"{nok_line}\n\n"
            f"False Reject\n{false_rejects} / {ok_total} = "
            f"{float(summary['false_reject_rate']) * 100:.1f} %\n\n"
            f"{false_accept_line}\n\n"
            f"Celková úspešnosť\n{float(summary['accuracy']) * 100:.1f} %"
            f"{warning}"
        )
        good = (
            nok_total > 0
            and false_accepts == 0
            and float(summary["false_reject_rate"]) <= 0.1
        )
        quality = "● Dobré rozlíšenie" if good else "● Slabé rozlíšenie"
        color = "#22c55e" if good else "#d29922"
        self._presence_validation_result.setText(f"{quality}\n\n{text}")
        self._presence_validation_result.setStyleSheet(f"color: {color};")

    def show_presence_tuning(self, tuning: dict[str, Any], *, weak_dataset: bool) -> None:
        current = tuning["current_validation_summary"]
        recommended = tuning["recommended_validation_summary"]
        warning = ""
        if not tuning["has_nok_samples"]:
            warning = ("⚠ Nie sú dostupné NOK vzorky. Nastavenie je optimalizované iba "
                       "tak, aby neodmietalo OK kusy.\n\n")
        elif weak_dataset:
            warning = "⚠ Validačný dataset je malý. Výsledok môže byť nespoľahlivý.\n\n"
        overlap = (
            tuning["has_nok_samples"]
            and (
                recommended["false_accept_count"] > 0
                or float(recommended["false_reject_rate"]) > 0.1
            )
        )
        if overlap:
            warning += (
                "⚠ OK a NOK vzorky sa pri aktuálnom ROI výrazne prekrývajú.\n"
                "Skús upraviť ROI, Ignore Mask, osvetlenie alebo nazbierať viac vzoriek.\n\n"
            )
        text = (
            f"{warning}Odporúčané nastavenia:\n"
            f"Citlivosť: {int(tuning['recommended_sensitivity'])} %\n"
            f"Max. anomálna plocha: "
            f"{float(tuning['recommended_thresholds']['total_area_threshold']):.0f} px\n"
            f"Min. veľkosť objektu: "
            f"{float(tuning['recommended_thresholds']['min_blob_area']):.0f} px\n\n"
            "Aktuálna:\n"
            f"False Accept: {float(current['false_accept_rate']) * 100:.1f} %\n"
            f"False Reject: {float(current['false_reject_rate']) * 100:.1f} %\n\n"
            "Odporúčaná:\n"
            f"False Accept: {float(recommended['false_accept_rate']) * 100:.1f} %\n"
            f"False Reject: {float(recommended['false_reject_rate']) * 100:.1f} %"
        )
        self._presence_tuning_result.setText(text)
        self._presence_tuning_result.show()
        self._presence_apply_tuned.show()

    def refresh_geometry(self, tool: Tool) -> None:
        rect = tool.roi.rect()
        if tool.type == "locator.template_match":
            params = dict(getattr(tool.params, "values", {}) or {})
            template = ToolRoi.from_obj(params.get("template_roi"))
            if template.rect() is None:
                template = tool.template_roi.copy()
            template_rect = template.rect()
            if rect is None:
                geometry_text = "Oblasť hľadania nie je nastavená."
            else:
                x, y, width, height = rect
                geometry_text = f"Hľadanie: {width}×{height} px @ ({x}, {y})"
                if template_rect is None:
                    geometry_text += "\nŠablóna nie je nastavená."
                else:
                    tx, ty, tw, th = template_rect
                    search_area = max(1, width * height)
                    ratio = (tw * th) / search_area
                    geometry_text += f"\nŠablóna: {tw}×{th} px @ ({tx}, {ty})"
                    if ratio >= 0.85:
                        geometry_text += "\n⚠ Šablóna je príliš veľká; nezostáva priestor na posun."
                    elif ratio <= 0.01:
                        geometry_text += "\n⚠ Šablóna je veľmi malá; skontroluj jej jednoznačnosť."
                    else:
                        geometry_text += "\n✓ Veľkosť šablóny ponecháva priestor na hľadanie."
            self._geometry_summary.setText(geometry_text)
            has_template = template_rect is not None
            self._locator_fit_button.setEnabled(
                has_template and not bool(params.get("use_golden_crop", False))
            )
            return
        if rect is None:
            geometry_text = "Bez ROI\nVyberte kresliaci nástroj a vytvorte ROI."
        else:
            x, y, width, height = rect
            shape = (
                "Otočený obdĺžnik" if tool.roi.is_rotated_rect()
                else {"rect": "Obdĺžnik", "ellipse": "Kruh", "polygon": "Polygón"}.get(
                    tool.roi.shape(), "Obdĺžnik"
                )
            )
            geometry_text = f"Shape: {shape}\nX: {x}   Y: {y}\nW: {width}   H: {height}"
        self._geometry_summary.setText(geometry_text)

    def set_locator_failure_policy(self, policy: str) -> None:
        normalized = "fail" if str(policy or "").strip().lower() == "fail" else "continue_without_alignment"
        self._locator_failure_policy = normalized
        if normalized != "continue_without_alignment":
            self.locatorPolicyWarningChanged.emit("")

    def refresh_values(self, tool: Tool) -> None:
        if tool is None:
            return
        self._current_tool = tool
        self._tool_label.setText(tool_display_name(tool))
        self._updating = True
        try:
            params = getattr(tool.params, "values", {}) or {}
            thresholds = getattr(tool.thresholds, "values", {}) or {}
            for name, widget in self._param_widgets.items():
                spec = self._param_specs.get(name, {})
                self._set_widget_value(widget, spec, params.get(name))
            for name, widget in self._threshold_widgets.items():
                spec = self._threshold_specs.get(name, {})
                self._set_widget_value(widget, spec, thresholds.get(name))
        finally:
            self._updating = False

        self._update_locator_mode_visibility()
        self._validate_current_values()

    def _rebuild_form(self) -> None:
        params = getattr(self._current_tool, "params", ToolParams()).values
        thresholds = getattr(self._current_tool, "thresholds", ToolThresholds()).values
        params = dict(params or {})
        thresholds = dict(thresholds or {})

        self._clear_form()

        if self._current_tool is None:
            return

        added_fields = False

        if any(self._is_supported_spec(spec) for spec in self._param_specs.values()):
            header = QLabel("Parametre", self)
            header.setStyleSheet("font-weight: 600; padding-top: 2px;")
            self._form_layout.addRow(header)
            for name, spec in self._param_specs.items():
                if not self._is_supported_spec(spec):
                    continue
                if (self._current_tool.type in STATISTICAL_TYPES and name in {
                        "reference_model_ready", "reference_model_invalidated",
                        "sample_count_ok", "sample_count_nok"}):
                    continue
                widget = self._create_widget(spec)
                if widget is None:
                    continue
                if self._current_tool.type in STATISTICAL_TYPES and isinstance(
                        widget, (QSpinBox, QDoubleSpinBox)):
                    unit = str(spec.get("unit", "") or "")
                    if unit:
                        widget.setSuffix(f" {unit}")
                tooltip = _format_spec_tooltip(spec)
                if tooltip:
                    widget.setToolTip(tooltip)
                label_text = field_label(name, spec.get("label"))
                label = QLabel(label_text, self)
                label.setWordWrap(True)
                if tooltip:
                    label.setToolTip(tooltip)
                self._set_widget_value(widget, spec, params.get(name))
                self._connect_widget(widget, spec, kind="param", name=name)
                self._param_widgets[name] = widget
                container, error_label = self._create_field_container(widget)
                self._param_wrappers[name] = container
                self._param_error_labels[name] = error_label
                self._param_labels[name] = label
                if tooltip:
                    container.setToolTip(tooltip)
                target_layout = self._advanced_layout if (
                    (self._current_tool.type == "locator.template_match"
                     and name in {"coarse_cap", "apply_alignment"})
                    or self._current_tool.type in STATISTICAL_TYPES
                ) else self._form_layout
                self._add_stacked_field(target_layout, label, container, widget)
                added_fields = True

        if any(self._is_supported_spec(spec) for spec in self._threshold_specs.values()):
            for name, spec in self._threshold_specs.items():
                if not self._is_supported_spec(spec):
                    continue
                widget = self._create_widget(spec)
                if widget is None:
                    continue
                if self._current_tool.type in STATISTICAL_TYPES and isinstance(
                        widget, (QSpinBox, QDoubleSpinBox)):
                    unit = str(spec.get("unit", "") or "")
                    if unit:
                        widget.setSuffix(f" {unit}")
                tooltip = _format_spec_tooltip(spec)
                if tooltip:
                    widget.setToolTip(tooltip)
                label_text = field_label(name, spec.get("label"))
                label = QLabel(label_text, self)
                label.setWordWrap(True)
                if tooltip:
                    label.setToolTip(tooltip)
                self._set_widget_value(widget, spec, thresholds.get(name))
                self._connect_widget(widget, spec, kind="threshold", name=name)
                self._threshold_widgets[name] = widget
                container, error_label = self._create_field_container(widget)
                self._threshold_wrappers[name] = container
                self._threshold_error_labels[name] = error_label
                if tooltip:
                    container.setToolTip(tooltip)
                target_layout = self._advanced_layout if (
                    self._current_tool.type in STATISTICAL_TYPES
                    and name in {"score_threshold", "total_area_threshold", "min_blob_area"}
                ) else self._threshold_layout
                self._add_stacked_field(target_layout, label, container, widget)
                added_fields = True

        if self._current_tool.type in STATISTICAL_TYPES:
            self._presence_live_values = QLabel("Zatiaľ bez výsledku", self._threshold_container)
            self._presence_live_values.setWordWrap(True)
            self._presence_live_values.setStyleSheet("color: #9aa4af; padding-top: 4px;")
            self._threshold_layout.addRow(self._presence_live_values)

        if not added_fields:
            placeholder = QLabel(
                "Tento nástroj nemá editovateľné parametre ani prahy.",
                self,
            )
            placeholder.setStyleSheet("color: #666;")
            placeholder.setWordWrap(True)
            self._form_layout.addRow(placeholder)

        self._btn_defaults.setEnabled(added_fields)

        if added_fields:
            self._update_locator_mode_visibility()
            self._validate_current_values()

    def _clear_form(self) -> None:
        while self._form_layout.rowCount():
            self._form_layout.removeRow(0)
        while self._threshold_layout.rowCount():
            self._threshold_layout.removeRow(0)
        while self._advanced_layout.rowCount():
            self._advanced_layout.removeRow(0)
        self._param_widgets.clear()
        self._threshold_widgets.clear()
        self._param_wrappers.clear()
        self._threshold_wrappers.clear()
        self._param_labels.clear()
        self._param_error_labels.clear()
        self._threshold_error_labels.clear()
        self._form_error_label.clear()
        self._form_error_label.setVisible(False)

    def _is_supported_spec(self, spec: dict[str, Any]) -> bool:
        field_type = (spec or {}).get("type")
        return field_type in _SUPPORTED_FORM_FIELD_TYPES

    def _update_locator_mode_visibility(self) -> None:
        """Show reference-edge tuning only for the guided A-B locator mode."""
        tool = self._current_tool
        if tool is None or tool.type != "locator.template_match":
            return
        mode_widget = self._param_widgets.get("alignment_mode")
        mode = (
            str(mode_widget.currentData()) if isinstance(mode_widget, QComboBox)
            else str(getattr(tool.params, "values", {}).get("alignment_mode", "translation"))
        )
        reference_names = {
            "reference_search_half_window", "reference_blur_sigma", "reference_scan_step",
            "reference_edge_polarity", "reference_grad_threshold", "reference_min_coverage",
            "reference_max_angle_deg", "reference_use_subpixel",
        }
        visible = mode == "guided_edge"
        for name in reference_names:
            wrapper = self._param_wrappers.get(name)
            label = self._param_labels.get(name)
            if wrapper is not None:
                wrapper.setVisible(visible)
            if label is not None:
                label.setVisible(visible)
    def _clear_test_result(self) -> None:
        self._reset_diagnostics()
        self.locatorPolicyWarningChanged.emit("")

    def set_test_running(self, running: bool) -> None:
        if running:
            self._set_test_button_enabled(False)
            self._status_value_label.setText("Test prebieha…")
        else:
            self._set_test_button_enabled(bool(self._current_tool) and not self._updating)

    def show_test_result(
        self,
        result: ToolRunResult,
        elapsed_ms: float,
        perf_breakdown: Optional[list[dict[str, Any]]] = None,
        status_message: Optional[str] = None,
    ) -> None:
        metrics = dict(result.metrics or {})
        debug_artifacts = (
            result.debug_artifacts if isinstance(result.debug_artifacts, dict) else {}
        )
        diagnostics_payload_raw = (
            debug_artifacts.get("diagnostics") if debug_artifacts else {}
        )
        diagnostics_payload = (
            diagnostics_payload_raw if isinstance(diagnostics_payload_raw, dict) else {}
        )
        diagnostics_breakdown = perf_breakdown
        tool_identifier = debug_artifacts.get("tool_id") if debug_artifacts else None
        if diagnostics_breakdown is None:
            timings = (
                diagnostics_payload.get("timings_ms")
                if isinstance(diagnostics_payload, dict)
                else None
            )
            diagnostics_breakdown = [
                {
                    "tool": tool_identifier or "tool",
                    "latency_ms": float(getattr(result, "latency_ms", elapsed_ms or 0.0) or 0.0),
                    "timings": timings if isinstance(timings, dict) else None,
                }
            ]

        if (self._current_tool is not None
                and self._current_tool.type in STATISTICAL_TYPES):
            reason = self._presence_decision_reason(metrics.get("decision_reason"))
            if reason and (result.status or "").lower() == "nok":
                status_message = "\n".join(filter(None, (status_message, f"Dôvod: {reason}")))
            if self._presence_live_values is not None:
                if self._current_tool.type == "mold.protection_v1":
                    state_labels = {
                        "empty": "Prázdna",
                        "occupied": "Zvyšok nájdený",
                        "fault": "Kontrola nepripravená",
                    }
                    state = state_labels.get(
                        str(metrics.get("inspection_state", "")), "Neznámy"
                    )
                    self._presence_live_values.setText(
                        f"Stav formy: {state}\n"
                        f"Počet zvyškov: {int(metrics.get('residual_count', 0) or 0)}\n"
                        "Najväčší zvyšok: "
                        f"{float(metrics.get('largest_blob_area', 0.0)):.0f} px"
                    )
                else:
                    self._presence_live_values.setText(
                        "Aktuálna anomália: "
                        f"{float(metrics.get('anomaly_area_percent', 0.0)):.1f} %\n"
                        "Najväčší objekt: "
                        f"{float(metrics.get('largest_blob_area', 0.0)):.0f} px\n"
                        f"Počet objektov: {int(metrics.get('blob_count', 0) or 0)}"
                    )

        self._update_diagnostics(
            result.status,
            metrics,
            None,  # Diagnostics panel should not display preview images after tests
            elapsed_ms=elapsed_ms,
            message=status_message,
        )
        self._set_perf_overlay(diagnostics_breakdown)
        self._maybe_emit_locator_warning(metrics, diagnostics_payload)

    @staticmethod
    def _presence_decision_reason(value: object) -> str:
        labels = {
            "area_px": "prekročená chybná plocha",
            "area_percent": "prekročená chybná plocha %",
            "largest_blob": "príliš veľký objekt",
            "blob_count": "príliš veľa objektov",
            "model_not_ready": "model ochrany formy nie je pripravený",
            "no_valid_pixels": "Ignore Mask zakrýva celú kontrolovanú oblasť",
        }
        keys = [item.strip() for item in str(value or "").split(",") if item.strip()]
        return ", ".join(labels.get(key, key) for key in keys)

    def show_test_error(self, message: str) -> None:
        self._update_diagnostics("nok", {}, None, message=message)
        self._set_perf_overlay(None)
        self.locatorPolicyWarningChanged.emit("")

    def _set_perf_overlay(self, breakdown: Optional[list[dict[str, Any]]]) -> None:
        if not breakdown:
            self._perf_overlay_label.clear()
            self._perf_overlay_label.setToolTip("")
            self._perf_overlay_label.setVisible(False)
            return

        total = 0.0
        parts: list[str] = []
        tooltip_parts: list[str] = []
        for entry in breakdown:
            if not isinstance(entry, dict):
                continue
            raw_latency = entry.get("latency_ms") or entry.get("latency") or 0.0
            try:
                latency = float(raw_latency)
            except (TypeError, ValueError):
                latency = 0.0
            tool_label = entry.get("tool") or entry.get("tool_id") or entry.get("type") or "tool"
            tool_text = str(tool_label)
            total += max(latency, 0.0)
            parts.append(f"{tool_text}: {latency:.1f} ms")
            timings = entry.get("timings")
            if isinstance(timings, dict) and timings:
                timing_parts = []
                for name, value in timings.items():
                    try:
                        timing_parts.append(f"{name}={float(value):.1f} ms")
                    except (TypeError, ValueError):
                        continue
                if timing_parts:
                    tooltip_parts.append(f"{tool_text}: " + ", ".join(timing_parts))

        if not parts:
            self._perf_overlay_label.clear()
            self._perf_overlay_label.setToolTip("")
            self._perf_overlay_label.setVisible(False)
            return

        overlay_text = f"Perf: {total:.1f} ms · {' | '.join(parts)}"
        self._perf_overlay_label.setText(overlay_text)
        self._perf_overlay_label.setVisible(True)
        if tooltip_parts:
            self._perf_overlay_label.setToolTip("\n".join(tooltip_parts))
        else:
            self._perf_overlay_label.setToolTip("")

    def _reset_diagnostics(self) -> None:
        self._set_status_indicator_color("#555")
        self._status_value_label.setText("Zatiaľ bez výsledku")
        self._status_message_label.clear()
        self._status_message_label.setVisible(False)
        self._latency_label.setText("Čas: —")
        self._perf_overlay_label.clear()
        self._perf_overlay_label.setVisible(False)
        self._perf_overlay_label.setToolTip("")
        self._metrics_table.setRowCount(0)
        self._metrics_table.setVisible(False)
        self._preview_cache.clear()
        self._preview_before_key = None
        self._preview_aligned_key = None
        self._preview_binarized_key = None
        self._preview_overlay_key = None
        self._active_preview_key = None
        self._preview_toggle_container.setVisible(False)
        for toggle in (
            self._preview_toggle_aligned,
            self._preview_toggle_binarized,
            self._preview_toggle_overlay,
        ):
            toggle.blockSignals(True)
            toggle.setChecked(False)
            toggle.setVisible(False)
            toggle.blockSignals(False)
        self._preview_widget.setVisible(False)
        self._preview_before_label.setText("Náhľad nie je dostupný")
        self._preview_before_label.setPixmap(QPixmap())
        self._preview_after_label.setText("Náhľad nie je dostupný")
        self._preview_after_label.setPixmap(QPixmap())

    def _update_diagnostics(
        self,
        status: Optional[str],
        metrics: dict[str, Any],
        preview: Optional[Any],
        *,
        elapsed_ms: Optional[float] = None,
        message: Optional[str] = None,
    ) -> None:
        status_key = (status or "").lower()
        color = self._STATUS_COLORS.get(status_key, "#555")
        self._set_status_indicator_color(color)
        self._status_value_label.setText(status.upper() if status else "—")

        if message:
            self._status_message_label.setText(message)
            self._status_message_label.setVisible(True)
        else:
            self._status_message_label.clear()
            self._status_message_label.setVisible(False)

        metrics_copy = dict(metrics or {})
        latency_value = metrics_copy.pop("latency_ms", None)
        if latency_value is None:
            latency_value = elapsed_ms
        latency_text = self._format_latency_text(latency_value)
        self._latency_label.setText(f"Čas: {latency_text}")

        self._populate_metrics_table(metrics_copy)
        self._prepare_preview_data(preview)

    @staticmethod
    def _format_latency_text(value: Any | None) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):.1f} ms"
        except (TypeError, ValueError):
            return str(value)

    def _set_status_indicator_color(self, color: str) -> None:
        self._status_indicator.setStyleSheet(
            "border-radius: 6px; border: 1px solid #333; background: {color};".format(color=color)
        )

    def _populate_metrics_table(self, metrics: dict[str, Any]) -> None:
        rows: list[tuple[str, str, str]] = []
        remaining = dict(metrics or {})

        if self._current_metrics_spec:
            spec_entries = sorted(
                self._current_metrics_spec,
                key=lambda spec: (-int(getattr(spec, "priority", 0) or 0), getattr(spec, "key", "")),
            )
            for spec in spec_entries:
                key = getattr(spec, "key", "")
                if key not in remaining:
                    continue
                description = getattr(spec, "description", "")
                label = metric_label(key, description)
                unit = getattr(spec, "unit", None)
                if unit:
                    label = f"{label} [{unit}]"
                raw_value = remaining.pop(key)
                value_text = self._format_metric_value(raw_value)
                rows.append((label, value_text, description))

        if not self._current_metrics_spec:
            for key in sorted(remaining.keys()):
                rows.append((metric_label(str(key)), self._format_metric_value(remaining[key]), ""))

        if not rows:
            self._metrics_table.setRowCount(0)
            self._metrics_table.setVisible(False)
            return

        self._metrics_table.setRowCount(len(rows))
        for row, (name, value, tooltip) in enumerate(rows):
            name_item = QTableWidgetItem(name)
            value_item = QTableWidgetItem(value)
            name_item.setFlags(Qt.ItemIsEnabled)
            value_item.setFlags(Qt.ItemIsEnabled)
            if tooltip and tooltip.strip() and tooltip.strip() != name.strip():
                name_item.setToolTip(tooltip.strip())
            self._metrics_table.setItem(row, 0, name_item)
            self._metrics_table.setItem(row, 1, value_item)
        self._metrics_table.resizeRowsToContents()
        self._metrics_table.setVisible(True)

    @staticmethod
    def _format_metric_value(value: Any) -> str:
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)):
            if math.isnan(value) or math.isinf(value):
                return str(value)
            if abs(value) >= 1e6 or (0 < abs(value) < 0.01):
                return f"{value:.3g}"
            return f"{value:.4f}".rstrip("0").rstrip(".")
        if value is None:
            return "—"
        return str(value)

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _maybe_emit_locator_warning(
        self, metrics: dict[str, Any], diagnostics: dict[str, Any]
    ) -> None:
        tool = self._current_tool
        if tool is None:
            self.locatorPolicyWarningChanged.emit("")
            return

        tool_type = (getattr(tool, "type", "") or "").lower()
        if not (tool_type.startswith("locator.")):
            self.locatorPolicyWarningChanged.emit("")
            return

        quality_warnings = diagnostics.get("quality_warnings", [])
        if quality_warnings:
            self.locatorPolicyWarningChanged.emit("Pozor: " + " ".join(quality_warnings))
            return

        if self._locator_failure_policy != "continue_without_alignment":
            self.locatorPolicyWarningChanged.emit("")
            return

        thresholds_map = getattr(tool.thresholds, "values", {}) or {}
        threshold_value = diagnostics.get("threshold_corr", thresholds_map.get("threshold_corr"))
        threshold_corr = self._coerce_float(threshold_value, 0.55)
        corr_value = self._coerce_float(metrics.get("corr", diagnostics.get("corr")), 0.0)
        found_raw = metrics.get("found", diagnostics.get("found"))
        found_flag = bool(found_raw) if found_raw is not None else True

        message = ""
        if not found_flag:
            reference = diagnostics.get("reference_edge")
            failure = reference.get("failure") if isinstance(reference, dict) else None
            reasons = {
                "missing_reference_edge": "referenčná hrana A–B nie je nastavená",
                "reference_edge_not_found": "referenčná hrana A–B nebola nájdená",
                "low_reference_coverage": "pokrytie referenčnej hrany je príliš nízke",
                "reference_angle_out_of_range": "otočenie referenčnej hrany je mimo povoleného limitu",
                "shift_x_out_of_range": "posun X je mimo povoleného limitu",
                "shift_y_out_of_range": "posun Y je mimo povoleného limitu",
            }
            detail = reasons.get(
                str(failure or diagnostics.get("alignment_failure") or ""),
                "nenašiel platnú pozíciu",
            )
            message = (
                f"Pozor: Locator {detail}. Pri politike „Pokračovať bez zarovnania“ "
                "zostane frame nezarovnaný. Skontroluj downstream nástroje a nastavenia locatora."
            )
        elif corr_value < threshold_corr:
            message = (
                f"Pozor: Locator corr {corr_value:.3f} je pod prahom {threshold_corr:.3f}. "
                "Pri politike „Pokračovať bez zarovnania“ zostane frame nezarovnaný. "
                "Skontroluj downstream nástroje a threshold_corr."
            )

        if message:
            self.locatorPolicyWarningChanged.emit(message)
        else:
            self.locatorPolicyWarningChanged.emit("")

    def _prepare_preview_data(self, preview: Optional[Any]) -> None:
        self._preview_cache.clear()
        self._preview_before_key = None
        self._preview_aligned_key = None
        self._preview_binarized_key = None
        self._preview_overlay_key = None
        self._active_preview_key = None

        if isinstance(preview, dict):
            for key, value in preview.items():
                pixmap = self._pixmap_from_any(value)
                if pixmap is not None:
                    self._preview_cache[key] = pixmap

        candidates_before = ("before", "input", "frame")
        for name in candidates_before:
            if name in self._preview_cache:
                self._preview_before_key = name
                break

        for name in ("aligned", "after", "result"):
            if name in self._preview_cache:
                self._preview_aligned_key = name
                break

        for name in ("binarization", "binarized", "mask"):
            if name in self._preview_cache:
                self._preview_binarized_key = name
                break

        for name in ("overlay", "overlay_preview", "overlay_result"):
            if name in self._preview_cache:
                self._preview_overlay_key = name
                break

        if self._preview_aligned_key is not None:
            self._active_preview_key = self._preview_aligned_key
        elif self._preview_binarized_key is not None:
            self._active_preview_key = self._preview_binarized_key
        else:
            remaining = [key for key in self._preview_cache.keys() if key != self._preview_before_key]
            self._active_preview_key = remaining[0] if remaining else None

        self._update_preview_controls()
        self._refresh_preview_images()

    def _update_preview_controls(self) -> None:
        has_aligned = self._preview_aligned_key is not None
        has_binarized = self._preview_binarized_key is not None
        has_overlay = self._preview_overlay_key is not None

        self._preview_toggle_container.setVisible(has_aligned or has_binarized or has_overlay)

        self._preview_toggle_aligned.blockSignals(True)
        self._preview_toggle_aligned.setVisible(has_aligned)
        self._preview_toggle_aligned.setChecked(has_aligned and self._active_preview_key == self._preview_aligned_key)
        self._preview_toggle_aligned.blockSignals(False)

        self._preview_toggle_binarized.blockSignals(True)
        self._preview_toggle_binarized.setVisible(has_binarized)
        self._preview_toggle_binarized.setChecked(
            has_binarized and self._active_preview_key == self._preview_binarized_key
        )
        self._preview_toggle_binarized.blockSignals(False)

        self._preview_toggle_overlay.blockSignals(True)
        self._preview_toggle_overlay.setVisible(has_overlay)
        self._preview_toggle_overlay.setChecked(
            has_overlay and self._active_preview_key == self._preview_overlay_key
        )
        self._preview_toggle_overlay.blockSignals(False)

    def _refresh_preview_images(self) -> None:
        before_pixmap = None
        if self._preview_before_key and self._preview_before_key in self._preview_cache:
            before_pixmap = self._preview_cache[self._preview_before_key]

        after_pixmap = None
        if self._active_preview_key and self._active_preview_key in self._preview_cache:
            after_pixmap = self._preview_cache[self._active_preview_key]
        elif self._preview_overlay_key and self._preview_overlay_key in self._preview_cache:
            after_pixmap = self._preview_cache[self._preview_overlay_key]
        elif self._preview_aligned_key and self._preview_aligned_key in self._preview_cache:
            after_pixmap = self._preview_cache[self._preview_aligned_key]
        elif self._preview_binarized_key and self._preview_binarized_key in self._preview_cache:
            after_pixmap = self._preview_cache[self._preview_binarized_key]

        self._apply_preview_pixmap(self._preview_before_label, before_pixmap, "Náhľad nie je dostupný")
        self._apply_preview_pixmap(self._preview_after_label, after_pixmap, "Náhľad nie je dostupný")
        self._preview_widget.setVisible(bool(before_pixmap or after_pixmap))

    def _on_preview_toggle_changed(self, mode: str, checked: bool) -> None:
        if not checked:
            if self._active_preview_key is None:
                return
            if mode == "aligned" and self._active_preview_key == self._preview_aligned_key:
                self._active_preview_key = self._preview_binarized_key or self._preview_aligned_key
            elif mode == "binarization" and self._active_preview_key == self._preview_binarized_key:
                self._active_preview_key = self._preview_aligned_key or self._preview_binarized_key
            elif mode == "overlay" and self._active_preview_key == self._preview_overlay_key:
                self._active_preview_key = (
                    self._preview_aligned_key or self._preview_binarized_key
                )
        else:
            if mode == "aligned" and self._preview_aligned_key is not None:
                self._active_preview_key = self._preview_aligned_key
            elif mode == "binarization" and self._preview_binarized_key is not None:
                self._active_preview_key = self._preview_binarized_key
            elif mode == "overlay" and self._preview_overlay_key is not None:
                self._active_preview_key = self._preview_overlay_key
        self._refresh_preview_images()

    def _apply_preview_pixmap(
        self, label: QLabel, pixmap: Optional[QPixmap], placeholder: str
    ) -> None:
        if pixmap is None:
            label.setPixmap(QPixmap())
            label.setText(placeholder)
        else:
            label.setText("")
            label.setPixmap(pixmap)

    @staticmethod
    def _pixmap_from_any(value: Any) -> Optional[QPixmap]:
        if value is None:
            return None
        if isinstance(value, QPixmap):
            return value
        if isinstance(value, QImage):
            return QPixmap.fromImage(value)
        if isinstance(value, np.ndarray):
            arr = np.asarray(value)
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            if arr.ndim != 2:
                return None
            arr_u8 = np.ascontiguousarray(arr.astype(np.uint8))
            height, width = arr_u8.shape
            bytes_per_line = arr_u8.strides[0]
            qimg = QImage(arr_u8.data, width, height, bytes_per_line, QImage.Format_Grayscale8)
            return QPixmap.fromImage(qimg.copy())
        return None

    def _create_widget(self, spec: dict[str, Any]) -> Optional[QWidget]:
        if spec.get("type") not in _SUPPORTED_FORM_FIELD_TYPES:
            return None
        return _create_form_widget(spec, self)

    def _on_test_clicked(self) -> None:
        if self._current_tool is None:
            return
        ok, _, normalized = self._validate_current_values()
        if not ok:
            self.show_test_error("Najprv oprav chyby vo formulári.")
            return
        params = dict(normalized.get("params", {}))
        thresholds = dict(normalized.get("thresholds", {}))
        self.set_test_running(True)
        self.testRequested.emit(params, thresholds)

    def trigger_test(self) -> None:
        if not self._btn_test.isEnabled():
            return
        self._on_test_clicked()

    @staticmethod
    def _add_stacked_field(form, label, container, widget):
        # A single spanning row avoids QFormLayout splitting long labels and
        # controls inconsistently, especially at the inspector's 280px width.
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        container.layout().insertWidget(0, label)
        container.layout().setSpacing(5)
        container.layout().setSizeConstraint(QLayout.SetMinimumSize)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        widget.setMinimumHeight(max(30, widget.sizeHint().height()))
        if isinstance(widget, QComboBox):
            widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(10)
            widget.setToolTip(widget.currentText())
        form.addRow(container)

    def _create_field_container(self, widget: QWidget) -> tuple[QWidget, QLabel]:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(widget)

        error_label = QLabel("", container)
        error_label.setStyleSheet("color: #b03030; font-size: 11px;")
        error_label.setWordWrap(True)
        error_label.setVisible(False)
        layout.addWidget(error_label)

        return container, error_label

    def _set_widget_value(self, widget: QWidget, spec: dict[str, Any], value: Any) -> None:
        self._updating = True
        try:
            _set_form_widget_value(widget, spec, value)
        finally:
            self._updating = False

    def _get_widget_value(self, widget: QWidget, spec: dict[str, Any]) -> Any:
        return _get_form_widget_value(widget, spec)

    def _collect_current_values(self) -> tuple[dict[str, Any], dict[str, Any]]:
        params: dict[str, Any] = {}
        thresholds: dict[str, Any] = {}
        for name, widget in self._param_widgets.items():
            spec = self._param_specs.get(name, {})
            params[name] = self._get_widget_value(widget, spec)
        for name, widget in self._threshold_widgets.items():
            spec = self._threshold_specs.get(name, {})
            thresholds[name] = self._get_widget_value(widget, spec)
        self._current_form_values = {"params": dict(params), "thresholds": dict(thresholds)}
        return params, thresholds

    def _validate_current_values(
        self,
    ) -> tuple[bool, dict[str, Dict[str, list[str]]], dict[str, dict[str, Any]]]:
        if self._current_tool is None:
            return True, {"params": {}, "thresholds": {}}, self._current_form_values

        params, thresholds = self._collect_current_values()
        ok, errors, normalized = _validate_params_and_thresholds(
            params,
            thresholds,
            self._param_specs,
            self._threshold_specs,
        )

        self._validation_ok = ok
        self._last_normalized = normalized

        self._apply_validation_feedback(
            params,
            normalized.get("params", {}),
            errors.get("params", {}),
            self._param_widgets,
            self._param_wrappers,
            self._param_error_labels,
            self._param_specs,
        )
        self._apply_validation_feedback(
            thresholds,
            normalized.get("thresholds", {}),
            errors.get("thresholds", {}),
            self._threshold_widgets,
            self._threshold_wrappers,
            self._threshold_error_labels,
            self._threshold_specs,
        )

        messages: list[str] = []
        for specs, section_errors in (
            (self._param_specs, errors.get("params", {})),
            (self._threshold_specs, errors.get("thresholds", {})),
        ):
            for name, errs in section_errors.items():
                label = specs.get(name, {}).get("label", name)
                for err in errs:
                    messages.append(f"{label}: {err}")

        self._form_error_label.setVisible(bool(messages))
        if messages:
            self._form_error_label.setText("\n".join(messages))
        else:
            self._form_error_label.clear()

        return ok, errors, normalized

    def _apply_validation_feedback(
        self,
        raw_values: dict[str, Any],
        normalized_values: dict[str, Any],
        error_map: dict[str, list[str]],
        widgets: dict[str, QWidget],
        containers: dict[str, QWidget],
        labels: dict[str, QLabel],
        specs: dict[str, dict[str, Any]],
    ) -> None:
        for name, widget in widgets.items():
            container = containers.get(name)
            error_label = labels.get(name)
            errors = error_map.get(name, [])
            self._set_field_error(container, error_label, errors)
            if errors:
                continue
            if name not in normalized_values:
                continue
            normalized_value = normalized_values.get(name)
            if self._values_equal(normalized_value, raw_values.get(name)):
                continue
            spec = specs.get(name, {})
            self._set_widget_value(widget, spec, normalized_value)

    @staticmethod
    def _set_field_error(container: Optional[QWidget], label: Optional[QLabel], errors: list[str]) -> None:
        if container is None or label is None:
            return
        if errors:
            label.setText(" \n".join(errors))
            label.setVisible(True)
            container.setStyleSheet(
                "border: 1px solid #c14842; border-radius: 4px; padding: 4px; background-color: rgba(193, 72, 66, 0.08);"
            )
        else:
            label.clear()
            label.setVisible(False)
            container.setStyleSheet("")

    @staticmethod
    def _values_equal(a: Any, b: Any) -> bool:
        if isinstance(a, float) or isinstance(b, float):
            try:
                return abs(float(a) - float(b)) < 1e-9
            except (TypeError, ValueError):
                return False
        return a == b

    def _connect_widget(self, widget: QWidget, spec: dict[str, Any], *, kind: str, name: str) -> None:
        if isinstance(widget, QCheckBox):
            widget.toggled.connect(
                lambda checked, n=name: self._on_field_changed(kind, n, bool(checked))
            )
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(
                lambda _index, w=widget, n=name: self._on_field_changed(
                    kind, n, w.currentData()
                )
            )
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(
                lambda value, n=name: self._on_field_changed(kind, n, int(value))
            )
        elif isinstance(widget, QDoubleSpinBox):
            widget.valueChanged.connect(
                lambda value, n=name: self._on_field_changed(kind, n, float(value))
            )

    def _on_field_changed(self, kind: str, name: str, value: Any) -> None:
        if self._updating:
            return
        if kind == "param" and name == "alignment_mode":
            self._update_locator_mode_visibility()
        ok, _, normalized = self._validate_current_values()
        if not ok:
            return
        section = "params" if kind == "param" else "thresholds"
        normalized_section = normalized.get(section, {})
        new_value = normalized_section.get(name, self._current_form_values.get(section, {}).get(name))
        if kind == "param":
            self.paramChanged.emit(name, new_value)
        elif kind == "threshold":
            self.thresholdChanged.emit(name, new_value)

    def _on_restore_defaults(self) -> None:
        if self._current_tool is None:
            return
        self._updating = True
        try:
            for name, widget in self._param_widgets.items():
                spec = self._param_specs.get(name, {})
                self._set_widget_value(widget, spec, spec.get("default"))
            for name, widget in self._threshold_widgets.items():
                spec = self._threshold_specs.get(name, {})
                self._set_widget_value(widget, spec, spec.get("default"))
        finally:
            self._updating = False

        for name in self._param_widgets:
            spec = self._param_specs.get(name, {})
            default = spec.get("default")
            self.paramChanged.emit(name, default)
        for name in self._threshold_widgets:
            spec = self._threshold_specs.get(name, {})
            default = spec.get("default")
            self.thresholdChanged.emit(name, default)

        self._validate_current_values()

    def _update_visibility(self) -> None:
        has_tool = self._current_tool is not None
        self._form_container.setVisible(has_tool)
        self._placeholder_label.setVisible(not has_tool)
        enabled_controls = has_tool and bool(self._param_widgets or self._threshold_widgets)
        self._btn_defaults.setEnabled(enabled_controls)
        self._set_test_button_enabled(has_tool and not self._updating)
        self._diagnostics_group.setVisible(has_tool)

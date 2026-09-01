# app/ui/golden_wizard.py
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QImage, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QMessageBox,
    QCheckBox,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QHeaderView,
    QAbstractItemView,
    QTabWidget,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QGroupBox,
    QSizePolicy,
    QButtonGroup,
    QFileDialog,
    QToolButton,
    QScrollArea
)

import os
import time
import math
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Sequence
from functools import partial

import numpy as np
import cv2

from app.utils import overlay as overlay_utils

from app.ui.draw_view import DrawView
from app.ui.roi_mask_editor import (
    MASK_WARN_PIXELS,
    MAX_MASK_PIXELS,
    MAX_ROI_PIXELS,
    ROI_WARN_PIXELS,
    MaskEditor,
    ROIEditor,
)
from app.services.storage_service import save_golden
from app.models.regions import Region, validate_cardinality
from app.services.live_preview_service import LivePreviewService
from app.models.schema import (
    RecipeData,
    RecipeV2,
    Tool,
    RecipeView,
    ViewCameraProfile,
    ToolDefinition,
    ToolMask,
    ToolMetricSpec,
    ToolParams,
    ToolRoi,
    ToolThresholds,
)
from app.services.recipe_service import RecipeService
from app.services.tool_registry import ToolRegistry
from app.services import settings_service
from app.services.tool_service import (
    ToolRunResult,
    run_locator_template_match,
    run_tool_test,
)
from app.services.golden_wizard_logic import (
    _SUPPORTED_FORM_FIELD_TYPES,
    _validate_params_and_thresholds,
)
from app.ui.view_utils import (
    apply_view_image_transform,
    view_image_rotation,
    view_uses_global_golden,
)
from app.ui.camera_profile_utils import (
    apply_camera_state,
    apply_view_camera_profile,
    snapshot_camera_state,
)
from app.ui.golden_wizard.form_widgets import (
    _create_form_widget,
    _format_spec_tooltip,
    _get_form_widget_value,
    _set_form_widget_value,
)
from app.ui.golden_wizard.session_settings_dialog import SessionSettingsDialog
from app.ui.golden_wizard.tool_catalog_dialog import ToolCatalogDialog
from app.ui.golden_wizard.tool_edit_dialog import ToolEditDialog
from app.ui.golden_wizard.view_config_dialog import (
    ViewConfigDialog,
    _DEFAULT_CAMERA_RESOLUTIONS,
)

if TYPE_CHECKING:
    from app.services.modbus_service import ModbusService
    from app.services.pico_service import PicoService


class ToolsTableWidget(QTableWidget):
    """Table with internal drag & drop row reordering support."""

    rowsReordered = Signal(list)

    def dropEvent(self, event):  # type: ignore[override]
        if event.source() is not self or self.dragDropMode() != QAbstractItemView.InternalMove:
            super().dropEvent(event)
            return

        current_order = self._collect_row_ids()
        if not current_order:
            event.ignore()
            return

        selection = self.selectionModel()
        if selection is None:
            event.ignore()
            return

        selected_rows = sorted({index.row() for index in selection.selectedRows()})
        if not selected_rows:
            event.ignore()
            return

        drop_row = self.rowAt(int(event.position().y())) if hasattr(event, "position") else self.rowAt(event.pos().y())
        if drop_row < 0:
            drop_row = self.rowCount()

        # Removing rows shifts the target; account for rows dragged from above.
        insert_at = drop_row
        for row in selected_rows:
            if row < drop_row:
                insert_at -= 1
        insert_at = max(0, min(insert_at, len(current_order)))

        moving_ids = [current_order[row] for row in selected_rows]
        remaining_ids = [tool_id for idx, tool_id in enumerate(current_order) if idx not in selected_rows]
        for offset, tool_id in enumerate(moving_ids):
            remaining_ids.insert(insert_at + offset, tool_id)

        if remaining_ids != current_order:
            self.rowsReordered.emit(remaining_ids)
        event.acceptProposedAction()

    def _collect_row_ids(self) -> list[int]:
        ids: list[int] = []
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is None:
                continue
            data = item.data(Qt.UserRole)
            if data is None:
                continue
            try:
                ids.append(int(data))
            except (TypeError, ValueError):  # pragma: no cover - defensive fallback
                continue
        return ids

















class ToolConfigPanel(QWidget):
    """Side panel for editing tool parameters and thresholds."""

    paramChanged = Signal(str, object)
    thresholdChanged = Signal(str, object)
    testRequested = Signal(dict, dict)
    locatorPolicyWarningChanged = Signal(str)
    testButtonEnabledChanged = Signal(bool)

    _STATUS_COLORS = {"ok": "#237804", "warn": "#b36b00", "nok": "#b03030"}

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._current_tool: Optional[Tool] = None
        self._param_specs: dict[str, dict[str, Any]] = {}
        self._threshold_specs: dict[str, dict[str, Any]] = {}
        self._current_metrics_spec: list[ToolMetricSpec] = []
        self._param_widgets: dict[str, QWidget] = {}
        self._threshold_widgets: dict[str, QWidget] = {}
        self._updating = False
        self._param_wrappers: dict[str, QWidget] = {}
        self._threshold_wrappers: dict[str, QWidget] = {}
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
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("Thresholds & Params", self)
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        self._tool_label = QLabel("", self)
        self._tool_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._tool_label)

        self._description_label = QLabel("", self)
        self._description_label.setStyleSheet("color: #666;")
        self._description_label.setWordWrap(True)
        layout.addWidget(self._description_label)

        self._form_container = QWidget(self)
        self._form_layout = QFormLayout(self._form_container)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setSpacing(6)
        self._form_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        layout.addWidget(self._form_container, 1)

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

        self._diagnostics_group = QGroupBox("Diagnostics", self)
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
        self._metrics_table.setHorizontalHeaderLabels(["Metric", "Value"])
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

        layout.addWidget(self._diagnostics_group)

        self._test_result_label = QLabel("", self)
        self._test_result_label.setStyleSheet(
            "color: #444; font-family: 'JetBrains Mono', 'Courier New', monospace; font-size: 12px;"
        )
        self._test_result_label.setWordWrap(True)
        self._test_result_label.setVisible(False)
        layout.addWidget(self._test_result_label)

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

    def clear(self) -> None:
        self._current_tool = None
        self._param_specs.clear()
        self._threshold_specs.clear()
        self._current_metrics_spec = []
        self._clear_form()
        self._tool_label.setText("No tool selected")
        self._description_label.clear()
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
        self._param_specs = {k: dict(v) for k, v in (schema.get("params") or {}).items()}
        self._threshold_specs = {
            k: dict(v) for k, v in (schema.get("thresholds") or {}).items()
        }
        self._current_metrics_spec = list(getattr(meta, "metrics_spec", []) or [])

        self._tool_label.setText(f"{tool.name} ({tool.type})")
        description = getattr(meta, "description", "") or ""
        self._description_label.setText(description)
        self._description_label.setVisible(bool(description))

        self._rebuild_form()
        self._clear_test_result()
        self._update_visibility()
        self.locatorPolicyWarningChanged.emit("")

    def set_locator_failure_policy(self, policy: str) -> None:
        normalized = "fail" if str(policy or "").strip().lower() == "fail" else "continue_without_alignment"
        self._locator_failure_policy = normalized
        if normalized != "continue_without_alignment":
            self.locatorPolicyWarningChanged.emit("")

    def refresh_values(self, tool: Tool) -> None:
        if tool is None:
            return
        self._current_tool = tool
        self._tool_label.setText(f"{tool.name} ({tool.type})")
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
                widget = self._create_widget(spec)
                if widget is None:
                    continue
                tooltip = _format_spec_tooltip(spec)
                if tooltip:
                    widget.setToolTip(tooltip)
                label_text = spec.get("label")
                if label_text is None:
                    label_text = name
                else:
                    label_text = str(label_text)
                label = QLabel(label_text, self)
                if tooltip:
                    label.setToolTip(tooltip)
                self._set_widget_value(widget, spec, params.get(name))
                self._connect_widget(widget, spec, kind="param", name=name)
                self._param_widgets[name] = widget
                container, error_label = self._create_field_container(widget)
                self._param_wrappers[name] = container
                self._param_error_labels[name] = error_label
                if tooltip:
                    container.setToolTip(tooltip)
                self._form_layout.addRow(label, container)
                added_fields = True

        if any(self._is_supported_spec(spec) for spec in self._threshold_specs.values()):
            header = QLabel("Prahy", self)
            header.setStyleSheet("font-weight: 600; padding-top: 6px;")
            self._form_layout.addRow(header)
            for name, spec in self._threshold_specs.items():
                if not self._is_supported_spec(spec):
                    continue
                widget = self._create_widget(spec)
                if widget is None:
                    continue
                tooltip = _format_spec_tooltip(spec)
                if tooltip:
                    widget.setToolTip(tooltip)
                label_text = spec.get("label")
                if label_text is None:
                    label_text = name
                else:
                    label_text = str(label_text)
                label = QLabel(label_text, self)
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
                self._form_layout.addRow(label, container)
                added_fields = True

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
            self._validate_current_values()

    def _clear_form(self) -> None:
        while self._form_layout.rowCount():
            self._form_layout.removeRow(0)
        self._param_widgets.clear()
        self._threshold_widgets.clear()
        self._param_wrappers.clear()
        self._threshold_wrappers.clear()
        self._param_error_labels.clear()
        self._threshold_error_labels.clear()
        self._form_error_label.clear()
        self._form_error_label.setVisible(False)

    def _is_supported_spec(self, spec: dict[str, Any]) -> bool:
        field_type = (spec or {}).get("type")
        return field_type in _SUPPORTED_FORM_FIELD_TYPES
    def _clear_test_result(self) -> None:
        self._test_result_label.clear()
        self._test_result_label.setVisible(False)
        self._reset_diagnostics()
        self.locatorPolicyWarningChanged.emit("")

    def set_test_running(self, running: bool) -> None:
        if running:
            self._set_test_button_enabled(False)
            self._set_test_message("Test prebieha…", "#666")
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
        status_key = (result.status or "").lower()
        latency_value = metrics.get(
            "latency_ms",
            elapsed_ms if elapsed_ms is not None else getattr(result, "latency_ms", None),
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

        self._update_diagnostics(
            result.status,
            metrics,
            None,  # Diagnostics panel should not display preview images after tests
            elapsed_ms=elapsed_ms,
            message=status_message,
        )
        self._set_perf_overlay(diagnostics_breakdown)
        self._maybe_emit_locator_warning(metrics, diagnostics_payload)

        status_text = result.status.upper() if result.status else "—"
        latency_text = self._format_latency_text(latency_value)
        message = f"Test: {status_text} · {latency_text}"
        color = self._STATUS_COLORS.get(status_key, "#444")
        self._set_test_message(message, color)

    def show_test_error(self, message: str) -> None:
        self._update_diagnostics("nok", {}, None)
        self._set_test_message(message, "#b03030")
        self._set_perf_overlay(None)
        self.locatorPolicyWarningChanged.emit("")

    def _set_test_message(self, message: str, color: str) -> None:
        self._test_result_label.setText(message)
        self._test_result_label.setStyleSheet(
            "color: {color}; font-family: 'JetBrains Mono', 'Courier New', monospace; font-size: 12px;".format(
                color=color
            )
        )
        self._test_result_label.setVisible(True)

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
        self._status_value_label.setText("—")
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
                label = (getattr(spec, "description", "") or key or "Metric").strip()
                unit = getattr(spec, "unit", None)
                if unit:
                    label = f"{label} [{unit}]"
                raw_value = remaining.pop(key, None)
                value_text = self._format_metric_value(raw_value)
                rows.append((label, value_text, getattr(spec, "description", "")))

        if not self._current_metrics_spec:
            for key in sorted(remaining.keys()):
                rows.append((str(key), self._format_metric_value(remaining[key]), ""))

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
        if not (tool_type.startswith("locator.") or tool_type == "template_match"):
            self.locatorPolicyWarningChanged.emit("")
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
            message = (
                "Pozor: Locator nenašiel pozíciu (found = False). "
                "Pri politike „Pokračovať bez zarovnania“ zostane frame nezarovnaný. "
                "Skontroluj downstream nástroje a nastavenia locatora."
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
    ):
        super().__init__(parent)
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
        self.current_img = None

        self._saved_snapshots: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._dirty_views: dict[str, dict[str, bool]] = {}
        self._view_states: dict[str, dict[str, Any]] = {}
        self._views: list[RecipeView] = []
        self._active_view_id: Optional[str] = None
        self._updating_view_selector = False

        # --- Live infra (len video label, bez kreslenia) ---

        dev = os.environ.get("CAM_DEV") or getattr(self.cam, "devices", ["/dev/video0"])[0]
        self._logger.debug("wizard_live_device=%s", dev)
        self._lp = LivePreviewService(dev, 1280, 720, 60)

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(50)  # ~20 FPS
        self._live_timer.timeout.connect(self._live_tick)
        self._live_on = False
        self._runtime_camera_paused_by_wizard = False

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

        self._session_settings_button = QToolButton(self)
        self._session_settings_button.setText("⚙")
        self._session_settings_button.setToolTip("Nastavenia relácie")
        self._session_settings_button.setAutoRaise(True)
        self._session_settings_button.clicked.connect(self._open_session_settings)

        top_primary = QHBoxLayout()
        top_primary.setContentsMargins(0, 0, 0, 0)
        top_primary.setSpacing(8)
        top_primary.addWidget(QLabel("Recept:"))
        top_primary.addWidget(self.recipe_name, 1)
        top_primary.addWidget(QLabel("View:", self))
        top_primary.addWidget(self._view_selector)
        top_primary.addWidget(self.btn_add_view)
        top_primary.addWidget(self.btn_edit_view)
        top_primary.addWidget(self.btn_remove_view)
        top_primary.addStretch(1)
        top_primary.addWidget(self.btn_add_tool)
        top_primary.addWidget(self.btn_live)

        top_secondary = QHBoxLayout()
        top_secondary.setContentsMargins(0, 0, 0, 0)
        top_secondary.setSpacing(8)
        top_secondary.addWidget(self.chk_pose)
        top_secondary.addWidget(self.chk_logging)
        top_secondary.addStretch(1)
        top_secondary.addWidget(QLabel("Zlyhanie locatora:", self))
        top_secondary.addWidget(self.failure_policy_combo)
        top_secondary.addWidget(self._session_settings_button)

        # ---- Dva režimy zobrazenia ----
        # 1) Live LABEL (video) – používa sa len pri Live zapnuté
        self.live_lbl = QLabel("—")
        self.live_lbl.setAlignment(Qt.AlignCenter)
        self.live_lbl.setMinimumHeight(360)
        self.live_lbl.hide()  # default skryté

        # 2) DrawView (kreslenie) – používa sa pri Live vypnuté
        self.view = DrawView(self)
        self.live_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # ---- Ovládacie tlačidlá ----
        btn_cap_golden   = QPushButton("Získať GOLDEN z kamery")
        btn_load_golden  = QPushButton("Načítať GOLDEN z disku")
        self.btn_save_tool = QPushButton("Uložiť nástroj")
        self.btn_test_tool = QPushButton("Otestovať")
        self.btn_test_tool.setEnabled(False)
        self.btn_publish_recipe = QPushButton("Publikovať/Aktualizovať recept")
        self.btn_close_wizard = QPushButton("Zavrieť Golden Wizard")
        self.btn_close_wizard.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; font-weight: 600; }"
            "QPushButton:hover { background-color: #b71c1c; }"
            "QPushButton:pressed { background-color: #8e0000; }"
        )

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addWidget(btn_cap_golden)
        buttons.addWidget(btn_load_golden)
        buttons.addStretch(1)
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
        self.tools_table.setHorizontalHeaderLabels(["Order", "Name", "Type", "Enabled", "Actions"])
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
            "background-color: #fff4d6; border: 1px solid #f0c36d; "
            "color: #8a6d1a; padding: 8px; border-radius: 4px; font-weight: 500;"
        )
        self.locator_policy_banner.setVisible(False)

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self.live_lbl, 1)
        left_layout.addWidget(self.view, 1)
        left_layout.addWidget(tools_label)
        left_layout.addWidget(self.tools_table, 1)
        left_layout.addWidget(self.locator_hint_label)
        left_layout.addWidget(self.locator_policy_banner)
        left_layout.addLayout(buttons)
        left_layout.setStretch(0, 5)
        left_layout.setStretch(1, 5)
        left_layout.setStretch(3, 2)

        content_layout.addLayout(left_layout, 5)
        content_layout.addWidget(self._tool_panel)

        top_controls = QVBoxLayout()
        top_controls.setContentsMargins(0, 0, 0, 0)
        top_controls.setSpacing(6)
        top_controls.addLayout(top_primary)
        top_controls.addLayout(top_secondary)

        content_widget = QWidget(self)
        content_widget_layout = QVBoxLayout(content_widget)
        content_widget_layout.setContentsMargins(0, 0, 0, 0)
        content_widget_layout.setSpacing(12)
        content_widget_layout.addLayout(top_controls)
        content_widget_layout.addLayout(content_layout, 1)

        self._content_scroll = QScrollArea(self)
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QScrollArea.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._content_scroll.setWidget(content_widget)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._content_scroll)

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
        self._tool_panel.paramChanged.connect(self._on_tool_param_changed)
        self._tool_panel.thresholdChanged.connect(self._on_tool_threshold_changed)
        self._tool_panel.testRequested.connect(self._on_tool_test_requested)
        self._tool_panel.testButtonEnabledChanged.connect(self.btn_test_tool.setEnabled)
        self._tool_panel.locatorPolicyWarningChanged.connect(
            self._update_locator_policy_banner
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
        self._refresh_publish_state()

        self.setSizeGripEnabled(True)
        self.resize(1400, 900)
        self.setWindowState(self.windowState() | Qt.WindowFullScreen)

    # ---------- Live ----------
    def _pause_runtime_camera_for_wizard(self) -> None:
        if self._runtime_camera_paused_by_wizard:
            self._logger.debug("wizard_camera_pause skipped: already paused")
            return
        self.cam.pause_for_external()
        self._runtime_camera_paused_by_wizard = True
        self._logger.info("wizard_camera_pause done")

    def _resume_runtime_camera_from_wizard(self) -> None:
        if not self._runtime_camera_paused_by_wizard:
            self._logger.debug("wizard_camera_resume skipped: not paused")
            return
        self.cam.resume_after_external()
        self._runtime_camera_paused_by_wizard = False
        self._logger.info("wizard_camera_resume done")

    def _toggle_live(self, checked: bool):
        if checked and not self._is_live_allowed_by_capture_mode():
            self._logger.info("[GOLDEN_CAPTURE] live disabled in trigger mode")
            self.btn_live.blockSignals(True)
            self.btn_live.setChecked(False)
            self.btn_live.blockSignals(False)
            self._sync_live_policy_ui()
            return
        if checked:
            try:
                self._pause_runtime_camera_for_wizard()
            except Exception as e:
                self._err(f"Live feed sa nepodarilo pripraviť kameru: {e}")
                self.btn_live.setChecked(False)
                return

            # Camera lifecycle: oddelené helpery pre štart/stop preview session.
            try:
                self._start_preview_session()
            except Exception as e:
                self._stop_preview_session(resume_runtime_camera=True, clear_label=True)
                self._err(f"Live feed sa nepodarilo spustiť: {e}")
                self.btn_live.setChecked(False)
        else:
            self._stop_preview_session(resume_runtime_camera=True)

    def _start_preview_session(self) -> None:
        # Zapnúť live: zobraz label, skryť DrawView (žiadne kreslenie počas live)
        self.view.hide()
        self.live_lbl.show()
        self._lp.start()
        self._live_timer.start()
        self._live_on = True
        self.btn_live.setText("Live zapnuté")
        self._logger.info("wizard_preview state=started")

    def _stop_preview_session(
        self,
        *,
        resume_runtime_camera: bool,
        clear_label: bool = False,
    ) -> None:
        # Vypnúť live: skryť label, ukázať DrawView
        self._live_timer.stop()
        try:
            self._lp.stop()
        except Exception:
            pass
        if self._live_on:
            self._logger.info("wizard_preview state=stopped")
        self._live_on = False
        self.btn_live.setText("Live vypnuté")
        self.live_lbl.hide()
        self.view.show()
        if clear_label:
            self.live_lbl.setText("—")
        if resume_runtime_camera:
            try:
                self._resume_runtime_camera_from_wizard()
            except Exception as e:
                self._logger.error("resume_after_external failed: %s", e)

    def _live_tick(self):
        img = self._lp.last_frame_u8()
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
            self._stop_preview_session(resume_runtime_camera=True)
        self.btn_live.setEnabled(allowed)
        if allowed:
            self.btn_live.setText("Live zapnuté" if self._live_on else "Live vypnuté")
            self.btn_live.setToolTip("")
        else:
            self.btn_live.setText("Live nedostupné v TRIGGER režime")
            self.btn_live.setToolTip("Live preview je v TRIGGER režime blokovaný.")

    # ---------- UI util ----------
    def _set_pixmap(self, img_u8):
        # img_u8: numpy uint8 (H, W)
        h, w = img_u8.shape[:2]
        qimg = QImage(img_u8.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy())
        self.view.set_background(pm)
        print("[FIT_TO_VIEW] golden wizard initial image fit scheduled")
        self.view.schedule_fit_to_view(source="golden_wizard_set_pixmap")

    def _view_by_id(self, view_id: Optional[str]) -> Optional[RecipeView]:
        if not view_id:
            return None
        for view in self._views:
            if view.id == view_id:
                return view
        return None

    def _store_view_state(self, view_id: Optional[str] = None) -> None:
        view_id = view_id or self._active_view_id
        if not view_id:
            return
        if view_id not in {view.id for view in self._views}:
            return
        state = self._view_states.setdefault(view_id, {})
        state["golden_image"] = None if self.current_img is None else np.asarray(self.current_img).copy()

    def _refresh_view_list(
        self,
        *,
        recipe: Optional[str] = None,
        select_view_id: Optional[str] = None,
        reset_states: bool = False,
    ) -> None:
        self._store_view_state()
        recipe = recipe or self._current_recipe_name()
        if reset_states:
            self._view_states = {}
        try:
            views = self.recipes.list_views(recipe)
        except Exception as exc:
            print(f"[GoldenWizard] list_views failed for {recipe}: {exc}")
            views = []
        if not views:
            views = [RecipeView(id="view_1", name="View 1", golden_path="golden.png", tools=[])]

        self._views = [view.copy() for view in views]
        valid_ids = {view.id for view in self._views}

        if reset_states:
            self._saved_snapshots[recipe] = {}
            self._dirty_views[recipe] = {}
        else:
            self._saved_snapshots.setdefault(recipe, {})
            self._dirty_views.setdefault(recipe, {})
            for stale in list(self._saved_snapshots[recipe].keys()):
                if stale not in valid_ids:
                    self._saved_snapshots[recipe].pop(stale, None)
            for stale in list(self._dirty_views[recipe].keys()):
                if stale not in valid_ids:
                    self._dirty_views[recipe].pop(stale, None)
            for stale in list(self._view_states.keys()):
                if stale not in valid_ids:
                    self._view_states.pop(stale, None)

        for view in self._views:
            self._view_states.setdefault(view.id, {})

        self._refresh_view_metadata()

        target_view_id = select_view_id or self._active_view_id
        if not target_view_id or target_view_id not in valid_ids:
            target_view_id = self._views[0].id

        index = self._view_selector.findData(target_view_id)
        if index >= 0:
            self._updating_view_selector = True
            self._view_selector.setCurrentIndex(index)
            self._updating_view_selector = False

        recipe_snapshots = self._saved_snapshots.setdefault(recipe, {})
        recipe_dirty = self._dirty_views.setdefault(recipe, {})

        for view in self._views:
            try:
                self.recipes.load_tools(recipe, use_draft=True, view_id=view.id)
            except Exception as exc:
                print(f"[GoldenWizard] load_tools failed for {recipe}/{view.id}: {exc}")
            if reset_states or view.id not in recipe_snapshots:
                self._record_saved_snapshot(recipe, view.id)
            else:
                # Preserve existing dirty flag while ensuring entry exists.
                recipe_dirty.setdefault(view.id, recipe_dirty.get(view.id, False))

        self._switch_active_view(target_view_id, refresh_selector=False)
        self._update_window_title_dirty()

    def _on_view_changed(self) -> None:
        if self._updating_view_selector:
            return
        view_id = self._view_selector.currentData()
        if not isinstance(view_id, str) or not view_id:
            return
        if view_id == self._active_view_id:
            return
        self._switch_active_view(view_id, refresh_selector=False)

    def _switch_active_view(self, view_id: Optional[str], *, refresh_selector: bool = True) -> None:
        if not view_id:
            return
        if refresh_selector:
            index = self._view_selector.findData(view_id)
            if index >= 0:
                self._updating_view_selector = True
                self._view_selector.setCurrentIndex(index)
                self._updating_view_selector = False

        if view_id != self._active_view_id:
            self._store_view_state(self._active_view_id)
            self._active_view_id = view_id

        view = self._view_by_id(view_id)
        self._apply_view_camera_profile(view)

        recipe = self._current_recipe_name()
        try:
            self.recipes.load_tools(recipe, use_draft=True, view_id=view_id)
        except Exception as exc:
            print(f"[GoldenWizard] load_tools failed for {recipe}/{view_id}: {exc}")

        self._selected_tool_row = -1
        self.tools_table.clearSelection()
        self._tool_panel.clear()
        self._refresh_tools_table()
        self._refresh_golden_background(recipe, view_id=view_id)
        self._on_tool_selection_changed()
        self._update_dirty_state(recipe, view_id)

    def _load_saved_golden_image(
        self,
        recipe: Optional[str] = None,
        view_id: Optional[str] = None,
    ) -> Optional[np.ndarray]:
        recipe = recipe or self._current_recipe_name()
        view = self._view_by_id(view_id or self._active_view_id)
        golden_name = view.golden_path if view else "golden.png"
        path = Path("/data") / "recipes" / recipe / golden_name
        if not path.exists():
            return None

        try:
            import imageio.v3 as iio

            image = iio.imread(path)
        except Exception:
            return None

        if image.ndim == 3:
            image = image[:, :, 0]
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)
        return image

    def _refresh_golden_background(
        self,
        recipe: Optional[str] = None,
        view_id: Optional[str] = None,
    ) -> None:
        recipe = recipe or self._current_recipe_name()
        view_id = view_id or self._active_view_id
        if not view_id:
            self.current_img = None
            self.view.set_background(None)
            self.view.set_tool_overlay(None)
            return

        view = self._view_by_id(view_id)
        state = self._view_states.setdefault(view_id, {})
        cached = state.get("golden_image")
        if cached is not None:
            self.current_img = np.asarray(cached).copy()
            self._set_pixmap(self.current_img)
            self._set_selected_tool_overlay()
            return

        saved_golden: Optional[np.ndarray] = None
        if view_uses_global_golden(view) and recipe == getattr(
            self.recipes.tool, "recipe", None
        ):
            cached_tool = getattr(self.recipes.tool, "golden", None)
            if isinstance(cached_tool, np.ndarray):
                saved_golden = np.asarray(cached_tool)
        if saved_golden is None:
            saved_golden = self._load_saved_golden_image(recipe, view_id)
        if saved_golden is None:
            self.current_img = None
            self.view.set_background(None)
            self.view.set_tool_overlay(None)
            state["golden_image"] = None
            return

        saved_array = np.asarray(saved_golden)
        self.current_img = saved_array.copy()
        state["golden_image"] = self.current_img.copy()
        self._set_pixmap(self.current_img)
        self._set_selected_tool_overlay()

    def _set_selected_tool_overlay(self, tools: Optional[Sequence[Tool]] = None) -> None:
        if tools is None:
            recipe = self._current_recipe_name()
            view_id = self._active_view_id
            if not view_id:
                tools = []
            else:
                tools = self.recipes.get_draft_tools(recipe, view_id)

        row = getattr(self, "_selected_tool_row", -1)
        if tools is not None and 0 <= row < len(tools):
            self.view.set_tool_overlay(tools[row])
        else:
            self.view.set_tool_overlay(None)

    def _current_golden_image(self) -> Optional[np.ndarray]:
        if self.current_img is not None:
            return self.current_img

        view_id = self._active_view_id
        if view_id:
            state = self._view_states.get(view_id, {})
            cached = state.get("golden_image")
            if cached is not None:
                return np.asarray(cached).copy()

        view = self._view_by_id(view_id)
        if view_uses_global_golden(view):
            golden = getattr(self.recipes.tool, "golden", None)
            if isinstance(golden, np.ndarray):
                return np.asarray(golden).copy()

        return self._load_saved_golden_image(view_id=view_id)

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
        golden_path = save_golden(
            self.current_img,
            name,
            golden_path=golden_filename,
        )
        if self._active_view_id:
            self._view_states.setdefault(self._active_view_id, {})["golden_image"] = (
                np.asarray(self.current_img).copy()
            )
        recipe_dir = Path("/data") / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        recipe_data = RecipeData(pose_enabled=pose_enabled, regions=regs)
        self.recipes.save_regions(name, recipe_data)

        message = f"Recipe assets saved:\n{golden_path}\n{recipe_dir / 'regions.json'}"
        return True, message

    def _open_session_settings(self) -> None:
        dialog = SessionSettingsDialog(self)
        dialog.exec()

    def _save_tool_draft(self):
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
        pico_note = ""
        if callable(self._publish_flash_to_pico):
            ok_pico, msg_pico = self._publish_flash_to_pico(recipe)
            if ok_pico:
                pico_note = f"\nPico sync: {msg_pico}"
            else:
                pico_note = f"\nPico warning: {msg_pico}"
        self._record_saved_snapshot(recipe, self._active_view_id)
        self._refresh_tools_table()
        message = "Recept publikovaný."
        if autosorted_draft or autosorted_publish:
            message += "\nPoradie nástrojov bolo automaticky upravené: Locator nástroje boli presunuté na začiatok."
        if assets_message:
            message += f"\n{assets_message}"
        if pico_note:
            message += pico_note
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
            tooltip_parts.append(f"Draft updated: {draft_at}")
        if published_at:
            tooltip_parts.append(f"Published: {published_at}")
        self._publish_state_label.setToolTip("\n".join(tooltip_parts) if tooltip_parts else "")

    # ---------- Draft state management ----------
    def _snapshot_tools(self, recipe: str, view_id: str) -> list[dict[str, Any]]:
        try:
            tools = self.recipes.get_draft_tools(recipe, view_id)
        except Exception:
            return []
        return [tool.to_dict() for tool in tools]

    def _record_saved_snapshot(self, recipe: str, view_id: Optional[str] = None) -> None:
        view_id = view_id or self._active_view_id
        if not view_id:
            return
        snapshot = self._snapshot_tools(recipe, view_id)
        recipe_snapshots = self._saved_snapshots.setdefault(recipe, {})
        recipe_snapshots[view_id] = snapshot
        self._dirty_views.setdefault(recipe, {})[view_id] = False

    def _update_dirty_state(self, recipe: Optional[str] = None, view_id: Optional[str] = None) -> None:
        if not hasattr(self, "_saved_snapshots"):
            return
        recipe = recipe or self._current_recipe_name()
        if view_id is None:
            if self._active_view_id:
                self._update_dirty_state(recipe, self._active_view_id)
            return
        current = self._snapshot_tools(recipe, view_id)
        saved = self._saved_snapshots.get(recipe, {}).get(view_id)
        dirty = saved is None or current != saved
        self._dirty_views.setdefault(recipe, {})[view_id] = dirty
        self._update_window_title_dirty()

    def _update_window_title_dirty(self) -> None:
        if not hasattr(self, "_base_title"):
            return
        current_recipe = self._current_recipe_name()
        dirty_map = self._dirty_views.get(current_recipe, {})
        dirty = any(dirty_map.values())
        title = self._base_title + (" *" if dirty else "")
        self.setWindowTitle(title)

    def _has_unsaved_changes(self) -> bool:
        if not hasattr(self, "_dirty_views"):
            return False
        for view_map in self._dirty_views.values():
            if any(view_map.values()):
                return True
        return False

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
    def _suggest_view_id(existing: Sequence[RecipeView]) -> str:
        existing_ids = {view.id for view in existing if getattr(view, "id", "")}
        index = 1
        while True:
            candidate = f"view_{index}"
            if candidate not in existing_ids:
                return candidate
            index += 1

    @staticmethod
    def _suggest_view_name(existing: Sequence[RecipeView]) -> str:
        existing_names = {view.name for view in existing if getattr(view, "name", "")}
        index = 1
        candidate = f"View {index}"
        while candidate in existing_names:
            index += 1
            candidate = f"View {index}"
        return candidate

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

    def _on_add_view(self) -> None:
        recipe = self._current_recipe_name()
        try:
            existing = self.recipes.list_views(recipe)
        except Exception as exc:
            self._err(f"Načítanie view zlyhalo: {exc}")
            return

        proposed_id = self._suggest_view_id(existing)
        proposed_name = self._suggest_view_name(existing)
        source_view = self._view_by_id(self._active_view_id)

        dialog = ViewConfigDialog(
            parent=self,
            mode="add",
            view_id=proposed_id,
            name=proposed_name,
            available_resolutions=self._available_camera_resolutions(),
            current_camera=self._current_camera_config(),
            camera_profile=source_view.camera_profile if source_view else None,
            camera_model=self._camera_model(),
            supported_v4l2_controls=self._camera_v4l2_controls(),
            settle_ms=source_view.settle_ms if source_view else None,
            flash_delay_ms=int(getattr(source_view, "flash_delay_ms", 0) or 0)
            if source_view
            else 0,
            flash_pulse_ms=int(getattr(source_view, "flash_pulse_ms", 200) or 200)
            if source_view
            else 200,
            trigger_mode=getattr(source_view, "trigger_mode", "timed") if source_view else "timed",
            external_trigger_mode=getattr(source_view, "external_trigger_mode", None)
            if source_view
            else None,
            external_source=getattr(source_view, "external_source", None) if source_view else None,
            external_request_input=getattr(source_view, "external_request_input", None)
            if source_view
            else None,
            trigger_interval_ms=getattr(source_view, "trigger_interval_ms", None)
            if source_view
            else None,
            trigger_gap_ms=getattr(source_view, "trigger_gap_ms", None)
            if source_view
            else None,
            available_frame_sources=[
                (view.id, view.name or view.id)
                for view in existing
                if view.id
            ],
            frame_source_view_id=getattr(source_view, "frame_source_view_id", None)
            if source_view
            else None,
            image_rotation=getattr(source_view, "image_rotation", 0) if source_view else 0,
            available_branch_targets=[
                (view.id, view.name or view.id)
                for view in existing
                if view.id
            ],
            branch_enabled=bool(getattr(source_view, "branch_enabled", False))
            if source_view
            else False,
            branch_targets=dict(getattr(source_view, "branch_targets", {}) or {})
            if source_view
            else None,
            branch_default_view_id=(
                getattr(source_view, "branch_default_view_id", None)
                if source_view
                else None
            ),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        data = dialog.values()
        try:
            new_view = self.recipes.add_view(
                recipe,
                source_view_id=self._active_view_id,
                view_id=dialog.view_id(),
                view_name=data.get("name"),
                frame_source_view_id=data.get("frame_source_view_id"),
                camera_profile=data.get("camera_profile"),
                settle_ms=data.get("settle_ms"),
                flash_delay_ms=data.get("flash_delay_ms"),
                flash_pulse_ms=data.get("flash_pulse_ms"),
                trigger_mode=data.get("trigger_mode"),
                external_trigger_mode=data.get("external_trigger_mode"),
                external_source=data.get("external_source"),
                external_request_input=data.get("external_request_input"),
                trigger_interval_ms=data.get("trigger_interval_ms"),
                trigger_gap_ms=data.get("trigger_gap_ms"),
                image_rotation=int(data.get("image_rotation", 0) or 0),
                branch_enabled=bool(data.get("branch_enabled", False)),
                branch_targets=dict(data.get("branch_targets", {}) or {}),
                branch_default_view_id=data.get("branch_default_view_id"),
            )
        except Exception as exc:
            self._err(f"Pridanie view zlyhalo: {exc}")
            return

        self._view_states.setdefault(new_view.id, {})
        self._refresh_view_list(
            recipe=recipe, select_view_id=new_view.id, reset_states=False
        )
        self._refresh_publish_state()

    def _on_edit_view(self) -> None:
        recipe = self._current_recipe_name()
        view = self._view_by_id(self._active_view_id)
        if not view:
            return

        dialog = ViewConfigDialog(
            parent=self,
            mode="edit",
            view_id=view.id,
            name=view.name or view.id,
            available_resolutions=self._available_camera_resolutions(),
            current_camera=self._current_camera_config(),
            camera_profile=view.camera_profile,
            camera_model=self._camera_model(),
            supported_v4l2_controls=self._camera_v4l2_controls(),
            settle_ms=view.settle_ms,
            flash_delay_ms=int(getattr(view, "flash_delay_ms", 0) or 0),
            flash_pulse_ms=int(getattr(view, "flash_pulse_ms", 200) or 200),
            trigger_mode=getattr(view, "trigger_mode", "timed"),
            external_trigger_mode=getattr(view, "external_trigger_mode", None),
            external_source=getattr(view, "external_source", None),
            external_request_input=getattr(view, "external_request_input", None),
            trigger_interval_ms=getattr(view, "trigger_interval_ms", None),
            trigger_gap_ms=getattr(view, "trigger_gap_ms", None),
            available_frame_sources=[
                (other.id, other.name or other.id)
                for other in self._views
                if other.id and other.id != view.id
            ],
            frame_source_view_id=getattr(view, "frame_source_view_id", None),
            image_rotation=getattr(view, "image_rotation", 0),
            available_branch_targets=[
                (other.id, other.name or other.id)
                for other in self._views
                if other.id and other.id != view.id
            ],
            branch_enabled=bool(getattr(view, "branch_enabled", False)),
            branch_targets=dict(getattr(view, "branch_targets", {}) or {}),
            branch_default_view_id=getattr(view, "branch_default_view_id", None),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        data = dialog.values()
        try:
            updated_view = self.recipes.update_view(
                recipe,
                view.id,
                view_name=data.get("name"),
                frame_source_view_id=data.get("frame_source_view_id"),
                camera_profile=data.get("camera_profile"),
                settle_ms=data.get("settle_ms"),
                flash_delay_ms=data.get("flash_delay_ms"),
                flash_pulse_ms=data.get("flash_pulse_ms"),
                trigger_mode=data.get("trigger_mode"),
                external_trigger_mode=data.get("external_trigger_mode"),
                external_source=data.get("external_source"),
                external_request_input=data.get("external_request_input"),
                trigger_interval_ms=data.get("trigger_interval_ms"),
                trigger_gap_ms=data.get("trigger_gap_ms"),
                image_rotation=int(data.get("image_rotation", 0) or 0),
                branch_enabled=bool(data.get("branch_enabled", False)),
                branch_targets=dict(data.get("branch_targets", {}) or {}),
                branch_default_view_id=data.get("branch_default_view_id"),
            )
        except Exception as exc:
            self._err(f"Úprava view zlyhala: {exc}")
            return

        self._refresh_view_list(
            recipe=recipe, select_view_id=updated_view.id, reset_states=False
        )
        self._refresh_publish_state()

    def _on_remove_view(self) -> None:
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        if not view_id:
            return
        try:
            remaining = self.recipes.remove_view(recipe, view_id)
        except ValueError as exc:
            self._warn(str(exc))
            return
        except Exception as exc:
            self._err(f"Odstránenie view zlyhalo: {exc}")
            return
        self._view_states.pop(view_id, None)
        next_view_id = remaining[0].id if remaining else None
        self._refresh_view_list(recipe=recipe, select_view_id=next_view_id, reset_states=False)
        self._refresh_publish_state()

    def _open_tool_catalog(self):
        dialog = ToolCatalogDialog(self.recipes.tool, self)
        if dialog.exec() != QDialog.Accepted:
            return
        tool_type = dialog.selected_type()
        if not tool_type:
            return
        try:
            tool = self.recipes.tool.make_default_tool(tool_type)
            recipe = self._current_recipe_name()
            view_id = self._active_view_id
            if not view_id:
                self._err("Nie je vybraný žiadny view.")
                return
            self.recipes.add_tool(recipe, tool, view_id=view_id)
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
            name_item = QTableWidgetItem(tool.name)
            type_item = QTableWidgetItem(tool.type)
            order_item.setTextAlignment(Qt.AlignCenter)
            is_locator = tool.type.startswith("locator.")
            if is_locator:
                highlight = QColor("#fff2cc")
                type_item.setBackground(highlight)
                order_item.setBackground(highlight)
                name_item.setBackground(highlight)
                type_item.setText(f"{tool.type}  (Locator)")
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

            btn_edit = QPushButton("Upraviť", actions_widget)
            btn_edit.clicked.connect(lambda _, idx=row: self._edit_tool(idx))
            btn_del = QPushButton("Zmazať", actions_widget)
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
            self._tool_panel.set_locator_failure_policy(
                self._current_locator_failure_policy
            )
            self._selected_tool_row = row
            self.view.set_tool_overlay(tool)
        else:
            self._tool_panel.clear()
            self._selected_tool_row = -1
            self.view.set_tool_overlay(None)

    def _refresh_view_metadata(self) -> None:
        self._updating_view_selector = True
        self._view_selector.blockSignals(True)
        self._view_selector.clear()
        for view in self._views:
            label = view.name or view.id or "View"
            self._view_selector.addItem(label, view.id)
        self._view_selector.blockSignals(False)
        self._updating_view_selector = False

        self.btn_remove_view.setEnabled(len(self._views) > 1)
        self.btn_edit_view.setEnabled(bool(self._views))

    def _on_tool_param_changed(self, name: str, value: Any) -> None:
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
        self._update_dirty_state(recipe, view_id)

    def _on_tool_threshold_changed(self, name: str, value: Any) -> None:
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

            preceding_tools = [tool.copy() for tool in tools if tool.order < target_tool.order]
            preceding_tools.sort(key=lambda tool: tool.order)

            params_payload = dict(params or {})
            thresholds_payload = dict(thresholds or {})

            target_copy = target_tool.copy()
            target_copy.params = ToolParams(params_payload)
            target_copy.thresholds = ToolThresholds(thresholds_payload)
            target_copy.enabled = True

            pipeline_tools = preceding_tools + [target_copy]

            test_recipe = RecipeV2(
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
        except Exception as exc:
            self._tool_panel.show_test_error(f"Test zlyhal: {exc}")
        finally:
            self._tool_panel.set_test_running(False)

    def _edit_tool(self, index: int):
        recipe = self._current_recipe_name()
        view_id = self._active_view_id
        if not view_id:
            return
        tools = self.recipes.get_draft_tools(recipe, view_id)
        if 0 <= index < len(tools):
            tool = tools[index]
            try:
                meta = self.recipes.tool.get_tool_meta(tool.type)
            except KeyError:
                self._err(f"Neznámy typ nástroja: {tool.type}")
                return

            golden_img = self._current_golden_image()
            dialog = ToolEditDialog(
                tool,
                golden_img,
                meta,
                camera_service=self.cam,
                live_preview=getattr(self, "_lp", None),
                capture_frame_for_view=self._capture_frame_for_golden,
                active_view_id=self._active_view_id,
                recipe_name=recipe,
                base_dir="/data",
                parent=self,
            )
            if dialog.exec() != QDialog.Accepted:
                return

            updated_tool = dialog.result_tool()
            try:
                self.recipes.update_tool(
                    recipe, index, updated_tool, view_id=self._active_view_id
                )
            except Exception as exc:
                self._err(f"Uloženie nástroja zlyhalo: {exc}")
                return
            self._refresh_tools_table()

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
        self._stop_preview_session(resume_runtime_camera=True)
        e.accept()

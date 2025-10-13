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
    QListWidget,
    QListWidgetItem,
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
)

import os
import math
from pathlib import Path
from typing import Any, Dict, Optional, Sequence
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


_SUPPORTED_FORM_FIELD_TYPES = {"int", "float", "bool", "enum"}


def _coerce_bool_value(value: Any) -> tuple[Optional[bool], Optional[str]]:
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)):
        return bool(value), None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True, None
        if text in {"0", "false", "no", "n", "off"}:
            return False, None
        return None, "Value must be 'true' or 'false'."
    return None, "Invalid boolean value."


def _coerce_numeric_value(value: Any, *, number_type: str) -> tuple[Optional[float], Optional[str]]:
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, "Boolean value is not allowed."
    if isinstance(value, (int, float)):
        return float(value), None
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None, None
        try:
            if number_type == "int":
                return float(int(text, 10)), None
            return float(text)
        except (TypeError, ValueError):
            return None, "Value must be a number."
    return None, "Value must be a number."


def _apply_numeric_constraints(value: float, spec: dict[str, Any], *, number_type: str) -> tuple[Any, list[str]]:
    errors: list[str] = []
    try:
        if math.isnan(value) or math.isinf(value):
            return None, ["Value must be a finite number."]
    except TypeError:
        return None, ["Value must be a number."]

    min_val = spec.get("min")
    max_val = spec.get("max")

    if min_val is not None and value < float(min_val):
        errors.append(f"Value must be ≥ {min_val}.")
    if max_val is not None and value > float(max_val):
        errors.append(f"Value must be ≤ {max_val}.")

    clamped = value
    if min_val is not None:
        clamped = max(clamped, float(min_val))
    if max_val is not None:
        clamped = min(clamped, float(max_val))

    if number_type == "int":
        return int(round(clamped)), errors

    precision = spec.get("precision")
    if precision is None:
        precision = spec.get("decimals")
    if isinstance(precision, int) and precision >= 0:
        clamped = round(clamped, precision)

    return float(clamped), errors


def _normalize_field_value(value: Any, spec: dict[str, Any]) -> tuple[Any, list[str]]:
    errors: list[str] = []
    required = bool(spec.get("required"))
    field_type = (spec.get("type") or "").lower()

    if value is None or value == "":
        if required:
            errors.append("This field is required.")
            return None, errors
        default = spec.get("default")
        return default, errors

    if field_type == "bool":
        coerced, err = _coerce_bool_value(value)
        if err:
            errors.append(err)
            return None, errors
        return coerced, errors

    if field_type == "enum":
        valid_choices = {choice[0] for choice in spec.get("choices", []) or []}
        if value not in valid_choices:
            errors.append("Select one of the available options.")
            return None, errors
        return value, errors

    if field_type in {"int", "float"}:
        coerced, err = _coerce_numeric_value(value, number_type=field_type)
        if err:
            errors.append(err)
            return None, errors
        if coerced is None:
            if required:
                errors.append("This field is required.")
            return None, errors
        normalized, range_errors = _apply_numeric_constraints(
            coerced, spec, number_type=field_type
        )
        errors.extend(range_errors)
        return normalized, errors

    return value, errors


def _validate_values_against_specs(
    values: dict[str, Any], specs: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    normalized = dict(values or {})
    errors: dict[str, list[str]] = {}

    for name, spec in (specs or {}).items():
        raw_value = values.get(name)
        normalized_value, field_errors = _normalize_field_value(raw_value, spec)
        if field_errors:
            errors[name] = field_errors
        if normalized_value is None:
            normalized.pop(name, None)
        else:
            normalized[name] = normalized_value

    return normalized, errors


def _validate_params_and_thresholds(
    params: dict[str, Any],
    thresholds: dict[str, Any],
    param_specs: dict[str, dict[str, Any]],
    threshold_specs: dict[str, dict[str, Any]],
) -> tuple[bool, dict[str, Dict[str, list[str]]], dict[str, dict[str, Any]]]:
    normalized_params, param_errors = _validate_values_against_specs(params, param_specs)
    normalized_thresholds, threshold_errors = _validate_values_against_specs(
        thresholds, threshold_specs
    )

    ok = not param_errors and not threshold_errors
    errors = {"params": param_errors, "thresholds": threshold_errors}
    normalized = {"params": normalized_params, "thresholds": normalized_thresholds}
    return ok, errors, normalized


class SessionSettingsDialog(QDialog):
    """Modal dialog for editing runtime session toggles."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Session settings")
        self.setModal(True)

        self._settings = settings_service.get_session_settings()

        layout = QVBoxLayout(self)

        self._logging_checkbox = QCheckBox("Enable logging for new runs", self)
        self._logging_checkbox.setChecked(bool(self._settings.logging_enabled))

        layout.addWidget(self._logging_checkbox)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        layout.addLayout(form)

        target_dir = self._settings.logging_path
        if not isinstance(target_dir, Path):
            target_dir = Path(str(target_dir))
        if isinstance(target_dir, Path) and target_dir.suffix:
            target_dir = target_dir.parent

        self._path_edit = QLineEdit(str(target_dir), self)
        self._path_edit.setPlaceholderText("/data/logs")
        browse_btn = QPushButton("Browse…", self)
        browse_btn.clicked.connect(self._on_browse_clicked)

        path_row = QHBoxLayout()
        path_row.addWidget(self._path_edit)
        path_row.addWidget(browse_btn)

        path_widget = QWidget(self)
        path_widget.setLayout(path_row)
        form.addRow("Target directory", path_widget)

        self._artifacts_checkbox = QCheckBox("Export aligned frame PNG", self)
        self._artifacts_checkbox.setChecked(bool(self._settings.export_artifacts))
        form.addRow("Artifacts", self._artifacts_checkbox)

        self._overlay_checkbox = QCheckBox("Include overlay PNG", self)
        self._overlay_checkbox.setChecked(bool(self._settings.export_overlay))
        form.addRow("Overlay", self._overlay_checkbox)

        self._artifacts_checkbox.toggled.connect(self._on_artifacts_toggled)
        self._on_artifacts_toggled(self._artifacts_checkbox.isChecked())

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_browse_clicked(self) -> None:
        current = self._path_edit.text().strip() or str(self._settings.logging_path)
        directory = QFileDialog.getExistingDirectory(self, "Select target directory", current)
        if directory:
            self._path_edit.setText(directory)

    def _on_artifacts_toggled(self, enabled: bool) -> None:
        self._overlay_checkbox.setEnabled(bool(enabled))

    def _on_accept(self) -> None:
        path_text = self._path_edit.text().strip()
        if not path_text:
            QMessageBox.warning(self, "Missing path", "Please specify the target directory.")
            return

        target = Path(path_text).expanduser()
        if target.exists() and not target.is_dir():
            QMessageBox.critical(self, "Invalid path", "The target must be a directory.")
            return

        if not target.exists():
            answer = QMessageBox.question(
                self,
                "Create directory?",
                f"The directory '{target}' does not exist.\nCreate it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Creation failed",
                    f"Unable to create directory:\n{exc}",
                )
                return

        try:
            settings_service.update_session_settings(
                logging_enabled=self._logging_checkbox.isChecked(),
                logging_path=target,
                export_artifacts=self._artifacts_checkbox.isChecked(),
                export_overlay=(
                    self._overlay_checkbox.isChecked()
                    and self._artifacts_checkbox.isChecked()
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.critical(self, "Update failed", str(exc))
            return

        self.accept()


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


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _format_spec_tooltip(spec: dict[str, Any]) -> str:
    parts: list[str] = []
    description = (spec.get("description") or "").strip()
    if description:
        parts.append(description)

    min_val = spec.get("min")
    max_val = spec.get("max")
    if min_val is not None or max_val is not None:
        if min_val is not None and max_val is not None:
            parts.append(
                f"Valid range: {_format_number(min_val)} – {_format_number(max_val)}"
            )
        elif min_val is not None:
            parts.append(f"Minimum: {_format_number(min_val)}")
        elif max_val is not None:
            parts.append(f"Maximum: {_format_number(max_val)}")

    step = spec.get("step")
    if step not in (None, 0):
        parts.append(f"Step: {_format_number(step)}")

    if "default" in spec and spec.get("default") is not None:
        parts.append(f"Default: {_format_number(spec.get('default'))}")

    return "\n".join(parts)


def _create_form_widget(spec: dict[str, Any], parent: QWidget) -> QWidget | None:
    field_type = (spec.get("type") or "").lower()
    if field_type == "bool":
        checkbox = QCheckBox(parent)
        checkbox.setTristate(False)
        default = spec.get("default")
        if default is not None:
            checkbox.setChecked(bool(default))
        return checkbox
    if field_type == "enum":
        combo = QComboBox(parent)
        for value, label in spec.get("choices", []) or []:
            combo.addItem(str(label), value)
        default = spec.get("default")
        if default is not None and combo.count():
            index = combo.findData(default)
            if index >= 0:
                combo.setCurrentIndex(index)
        return combo if combo.count() else None
    if field_type == "int":
        spin = QSpinBox(parent)
        spin.setKeyboardTracking(False)
        min_val = spec.get("min")
        max_val = spec.get("max")
        if min_val is None:
            min_val = -10_000_000
        if max_val is None:
            max_val = 10_000_000
        spin.setRange(int(min_val), int(max_val))
        step = spec.get("step")
        if step is not None:
            try:
                spin.setSingleStep(max(1, int(step)))
            except Exception:  # pragma: no cover - defensive fallback
                pass
        default = spec.get("default")
        if default is not None:
            try:
                spin.setValue(int(round(float(default))))
            except Exception:  # pragma: no cover - defensive fallback
                spin.setValue(int(min_val))
        return spin
    if field_type == "float":
        spin = QDoubleSpinBox(parent)
        spin.setKeyboardTracking(False)
        min_val = spec.get("min")
        max_val = spec.get("max")
        if min_val is None:
            min_val = -1e9
        if max_val is None:
            max_val = 1e9
        spin.setRange(float(min_val), float(max_val))
        precision = spec.get("precision")
        if precision is None:
            precision = spec.get("decimals", 4)
        try:
            decimals = max(0, int(precision))
        except Exception:  # pragma: no cover - defensive fallback
            decimals = 4
        spin.setDecimals(decimals)
        step = spec.get("step")
        if step is not None:
            try:
                spin.setSingleStep(float(step))
            except Exception:  # pragma: no cover - defensive fallback
                pass
        default = spec.get("default")
        if default is not None:
            try:
                spin.setValue(float(default))
            except Exception:  # pragma: no cover - defensive fallback
                spin.setValue(float(min_val))
        return spin
    return None


def _set_form_widget_value(widget: QWidget, spec: dict[str, Any], value: Any) -> None:
    field_type = (spec.get("type") or "").lower()
    if value is None:
        value = spec.get("default")
    if field_type == "bool" and isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
        return
    if field_type == "enum" and isinstance(widget, QComboBox):
        if widget.count() == 0:
            return
        index = widget.findData(value)
        if index < 0 and spec.get("default") is not None:
            index = widget.findData(spec.get("default"))
        if index < 0:
            index = 0
        widget.setCurrentIndex(max(0, index))
        return
    if field_type == "int" and isinstance(widget, QSpinBox):
        fallback = spec.get("default")
        if fallback is None:
            fallback = widget.minimum()
        try:
            widget.setValue(int(round(float(value))))
        except Exception:  # pragma: no cover - defensive fallback
            widget.setValue(int(round(float(fallback))))
        return
    if field_type == "float" and isinstance(widget, QDoubleSpinBox):
        fallback = spec.get("default")
        if fallback is None:
            fallback = widget.minimum()
        try:
            widget.setValue(float(value))
        except Exception:  # pragma: no cover - defensive fallback
            widget.setValue(float(fallback))


def _get_form_widget_value(widget: QWidget, spec: dict[str, Any]) -> Any:
    field_type = (spec.get("type") or "").lower()
    if field_type == "bool" and isinstance(widget, QCheckBox):
        return bool(widget.isChecked())
    if field_type == "enum" and isinstance(widget, QComboBox):
        if widget.count() == 0:
            return None
        return widget.currentData()
    if field_type == "int" and isinstance(widget, QSpinBox):
        return int(widget.value())
    if field_type == "float" and isinstance(widget, QDoubleSpinBox):
        return float(widget.value())
    return None


class ToolCatalogDialog(QDialog):
    def __init__(self, tool_service, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tool catalog")
        self._tool_service = tool_service
        self._selected_type: str | None = None

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter tools…")
        self._filter.textChanged.connect(self._apply_filter)

        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._filter)
        layout.addWidget(self._list)
        layout.addWidget(buttons)

        self._entries: list[tuple[str, str, str]] = []
        self._populate_entries()
        self._apply_filter("")

    def _populate_entries(self) -> None:
        self._entries.clear()
        for tool_type in self._tool_service.list_tool_types():
            try:
                meta = self._tool_service.get_tool_meta(tool_type)
                display = f"{getattr(meta, 'name', tool_type)} ({tool_type})"
                tooltip = getattr(meta, "description", tool_type)
            except KeyError:
                display = tool_type
                tooltip = tool_type
            self._entries.append((tool_type, display, tooltip))

    def _apply_filter(self, text: str) -> None:
        pattern = (text or "").strip().lower()
        self._list.clear()
        for tool_type, display, tooltip in self._entries:
            if pattern and pattern not in display.lower() and pattern not in tool_type.lower():
                continue
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, tool_type)
            item.setToolTip(tooltip)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        self._selected_type = item.data(Qt.UserRole)
        super().accept()

    def accept(self) -> None:
        current = self._list.currentItem()
        if current is None:
            return
        self._selected_type = current.data(Qt.UserRole)
        super().accept()

    def selected_type(self) -> str | None:
        return self._selected_type


class TemplateRoiEditor(QWidget):
    """Lightweight ROI editor dedicated to template ROI selection."""

    roiChanged = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._editor = ROIEditor(self, show_toolbar=False)
        self._editor.roiChanged.connect(self.roiChanged)

        self._btn_reset = QPushButton("Reset Template ROI", self)
        self._btn_reset.clicked.connect(self._editor.reset_roi)

        view_layout = QVBoxLayout(self)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(4)
        view_layout.addWidget(self._editor, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addStretch(1)
        controls.addWidget(self._btn_reset)
        view_layout.addLayout(controls)

    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._editor.set_background(pixmap)

    def set_roi(self, roi: Optional[tuple[int, int, int, int]]) -> None:
        self._editor.set_roi(roi)

    def roi(self) -> Optional[tuple[int, int, int, int]]:
        return self._editor.roi()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt API
        super().setEnabled(enabled)
        self._editor.setEnabled(bool(enabled))
        self._btn_reset.setEnabled(bool(enabled))


class ToolEditDialog(QDialog):
    """Dialog providing ROI and ignore mask editing for a tool."""

    def __init__(
        self,
        tool: Tool,
        golden_image: Optional[np.ndarray],
        meta,
        camera_service=None,
        live_preview: Optional[LivePreviewService] = None,
        parent=None,
    ):
        super().__init__(parent)

        self.setWindowTitle(f"Edit Tool – {tool.name}")
        self._tool = tool.copy()
        self._meta = meta
        self._meta_caps = getattr(meta, "meta", meta)
        self._camera_service = camera_service
        self._live_preview = live_preview
        self._golden_image: Optional[np.ndarray] = None if golden_image is None else np.asarray(golden_image).copy()
        self._golden_pixmap: Optional[QPixmap] = None
        self._locator_template_specs: dict[str, dict[str, Any]] = {}

        self._is_locator_template = self._tool.type == "locator.template_match"
        self._use_golden_checkbox: Optional[QCheckBox] = None
        self._template_editor: Optional[TemplateRoiEditor] = None
        self._template_container: Optional[QWidget] = None
        self._locator_panel: Optional[QWidget] = None
        self._locator_metrics_label: Optional[QLabel] = None
        self._locator_message_label: Optional[QLabel] = None
        self._locator_preview_before: Optional[QLabel] = None
        self._locator_preview_after: Optional[QLabel] = None
        self._btn_locator_evaluate: Optional[QPushButton] = None

        self._supports_roi = bool(getattr(self._meta_caps, "supports_roi", True))
        self._supports_mask = bool(getattr(self._meta_caps, "supports_ignore_mask", True))

        self._roi_editor: Optional[ROIEditor] = ROIEditor(self) if self._supports_roi else None
        self._mask_editor: Optional[MaskEditor] = MaskEditor(self) if self._supports_mask else None

        self._param_specs: dict[str, dict[str, Any]] = {}
        self._threshold_specs: dict[str, dict[str, Any]] = {}
        self._param_fields: dict[str, QWidget] = {}
        self._threshold_fields: dict[str, QWidget] = {}
        self._param_wrappers: dict[str, QWidget] = {}
        self._threshold_wrappers: dict[str, QWidget] = {}
        self._param_error_labels: dict[str, QLabel] = {}
        self._threshold_error_labels: dict[str, QLabel] = {}
        self._validation_ok: bool = True
        self._last_validation: dict[str, dict[str, Any]] = {"params": {}, "thresholds": {}}
        self._current_form_values: dict[str, dict[str, Any]] = {"params": {}, "thresholds": {}}
        self._form_errors: list[str] = []
        self._updating_form = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QLabel(f"{tool.name} ({tool.type})", self)
        header.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(header)

        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs, 1)

        roi_tab = QWidget(self)
        roi_layout = QVBoxLayout(roi_tab)
        roi_layout.setContentsMargins(0, 0, 0, 0)
        roi_layout.setSpacing(8)

        if self._supports_roi and self._roi_editor is not None:
            roi_group = QGroupBox("Region of interest", roi_tab)
            roi_group_layout = QVBoxLayout(roi_group)
            roi_group_layout.setContentsMargins(6, 6, 6, 6)
            roi_group_layout.setSpacing(6)
            roi_group_layout.addWidget(self._roi_editor, 1)
            roi_layout.addWidget(roi_group, 1)

        if self._supports_mask and self._mask_editor is not None:
            mask_group = QGroupBox("Ignore mask", roi_tab)
            mask_group_layout = QVBoxLayout(mask_group)
            mask_group_layout.setContentsMargins(6, 6, 6, 6)
            mask_group_layout.setSpacing(6)
            mask_group_layout.addWidget(self._mask_editor, 1)
            roi_layout.addWidget(mask_group, 1)

        if not self._supports_roi and not self._supports_mask:
            info = QLabel("Selected tool does not support ROI or ignore mask editing.", roi_tab)
            info.setStyleSheet("color: #666;")
            info.setWordWrap(True)
            roi_layout.addWidget(info)

        self._roi_layout = roi_layout

        self._info_label = QLabel("", self)
        self._info_label.setStyleSheet("color: #666;")
        self._info_label.setWordWrap(True)
        roi_layout.addWidget(self._info_label)
        roi_layout.addStretch(1)
        self._tabs.addTab(roi_tab, "ROI & Mask")

        self._config_tab = QWidget(self)
        config_layout = QVBoxLayout(self._config_tab)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(0)
        self._config_form = QFormLayout()
        self._config_form.setContentsMargins(8, 8, 8, 8)
        self._config_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        config_layout.addLayout(self._config_form)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(8, 4, 8, 0)
        controls_layout.setSpacing(6)
        controls_layout.addStretch(1)
        self._btn_restore_defaults = QPushButton("Restore defaults", self._config_tab)
        self._btn_restore_defaults.clicked.connect(self._on_restore_defaults_clicked)
        self._btn_restore_defaults.setEnabled(False)
        controls_layout.addWidget(self._btn_restore_defaults)
        config_layout.addLayout(controls_layout)

        self._form_error_label = QLabel("", self._config_tab)
        self._form_error_label.setStyleSheet("color: #b03030; padding: 4px 8px 0 8px;")
        self._form_error_label.setWordWrap(True)
        self._form_error_label.setVisible(False)
        config_layout.addWidget(self._form_error_label)

        config_layout.addStretch(1)
        self._tabs.addTab(self._config_tab, "Thresholds & Params")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._button_box = buttons
        self._ok_button = buttons.button(QDialogButtonBox.Ok)

        self._build_config_form()

        self._golden_pixmap = self._pixmap_from_array(golden_image)
        params_values = dict(getattr(self._tool.params, "values", {}) or {})

        initial_roi: Optional[tuple[int, int, int, int]] = None
        roi_value = params_values.get("roi") if isinstance(params_values, dict) else None
        if isinstance(roi_value, ToolRoi):
            initial_roi = roi_value.rect()
        elif isinstance(roi_value, dict):
            try:
                x = int(round(float(roi_value.get("x", 0))))
                y = int(round(float(roi_value.get("y", 0))))
                w = int(round(float(roi_value.get("w", 0))))
                h = int(round(float(roi_value.get("h", 0))))
                if w > 0 and h > 0:
                    initial_roi = (x, y, w, h)
            except Exception:  # pragma: no cover - defensive
                initial_roi = None
        elif isinstance(roi_value, (tuple, list)) and len(roi_value) == 4:
            try:
                rx, ry, rw, rh = [int(round(float(v))) for v in roi_value]
                if rw > 0 and rh > 0:
                    initial_roi = (rx, ry, rw, rh)
            except Exception:  # pragma: no cover - defensive
                initial_roi = None
        if initial_roi is None:
            initial_roi = self._tool.roi.rect()

        initial_mask: Optional[np.ndarray] = None
        mask_value = params_values.get("ignore_mask") if isinstance(params_values, dict) else None
        if isinstance(mask_value, ToolMask):
            initial_mask = mask_value.value
        elif mask_value is not None:
            try:
                mask_obj = ToolMask.from_obj(mask_value)
                initial_mask = mask_obj.value
            except Exception:  # pragma: no cover - defensive
                initial_mask = None
        elif self._tool.ignore_mask.value is not None:
            initial_mask = np.asarray(self._tool.ignore_mask.value)

        if self._golden_pixmap is not None:
            if self._roi_editor is not None:
                self._roi_editor.set_background(self._golden_pixmap)
                if initial_roi is not None:
                    self._roi_editor.set_roi(initial_roi)
            if self._mask_editor is not None:
                self._mask_editor.set_background(self._golden_pixmap)
                if initial_mask is not None:
                    self._mask_editor.set_mask(initial_mask)
            instructions = [
                "Scroll to zoom, use the middle mouse button or space + drag to pan."
            ]
            if self._supports_roi and self._supports_mask:
                instructions.append("Draw the ROI rectangle and paint the ignore mask directly on the golden image.")
            elif self._supports_roi:
                instructions.append("Draw the ROI rectangle directly on the golden image.")
            elif self._supports_mask:
                instructions.append("Paint the ignore mask directly on the golden image.")
            self._info_label.setText(" ".join(instructions))
        else:
            self._info_label.setText("Golden snapshot is not available – editing is disabled.")
            if self._roi_editor is not None:
                self._roi_editor.setEnabled(False)
            if self._mask_editor is not None:
                self._mask_editor.setEnabled(False)

        if self._is_locator_template:
            self._init_locator_template_panel()

        self.resize(900, 640)

    def _init_locator_template_panel(self) -> None:
        if getattr(self, "_roi_layout", None) is None:
            return

        panel = QGroupBox("Locator preview", self)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(8, 8, 8, 8)
        panel_layout.setSpacing(8)

        params_values = dict(getattr(self._tool.params, "values", {}) or {})
        template_roi_rect = self._tool.template_roi.rect()

        use_spec = self._locator_template_specs.get("use_golden_crop", {"type": "bool", "label": "use_golden_crop", "default": True})
        use_golden_value = params_values.get("use_golden_crop")
        if use_golden_value is None and template_roi_rect:
            use_golden_value = False
        if use_golden_value is None:
            use_golden_value = use_spec.get("default", True)

        self._use_golden_checkbox = QCheckBox("Use golden crop as template", panel)
        self._use_golden_checkbox.setChecked(bool(use_golden_value))
        tooltip = _format_spec_tooltip(use_spec)
        if tooltip:
            self._use_golden_checkbox.setToolTip(tooltip)
        self._use_golden_checkbox.toggled.connect(self._on_locator_use_golden_changed)
        panel_layout.addWidget(self._use_golden_checkbox)

        self._param_specs["use_golden_crop"] = use_spec
        self._param_fields["use_golden_crop"] = self._use_golden_checkbox

        self._template_container = QWidget(panel)
        template_container_layout = QVBoxLayout(self._template_container)
        template_container_layout.setContentsMargins(0, 0, 0, 0)
        template_container_layout.setSpacing(4)

        template_hint = QLabel("Manual template ROI is drawn on the golden reference image.", self._template_container)
        template_hint.setStyleSheet("color: #666;")
        template_container_layout.addWidget(template_hint)

        self._template_editor = TemplateRoiEditor(self._template_container)
        template_container_layout.addWidget(self._template_editor, 1)
        if self._golden_pixmap is not None:
            self._template_editor.set_background(self._golden_pixmap)
        if template_roi_rect:
            self._template_editor.set_roi(template_roi_rect)

        self._template_container.setVisible(not self._use_golden_checkbox.isChecked())
        panel_layout.addWidget(self._template_container, 1)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self._btn_locator_evaluate = QPushButton("Evaluate", panel)
        self._btn_locator_evaluate.clicked.connect(self._on_locator_evaluate)
        self._btn_locator_evaluate.setEnabled(self._golden_image is not None)
        controls_layout.addWidget(self._btn_locator_evaluate)

        self._locator_metrics_label = QLabel("corr: —    dx: —    dy: —", panel)
        controls_layout.addWidget(self._locator_metrics_label)
        controls_layout.addStretch(1)
        panel_layout.addLayout(controls_layout)

        self._locator_message_label = QLabel("", panel)
        self._locator_message_label.setStyleSheet("color: #a33;")
        self._locator_message_label.setVisible(False)
        panel_layout.addWidget(self._locator_message_label)

        preview_layout = QHBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(12)

        before_title = QLabel("Captured frame", panel)
        before_title.setAlignment(Qt.AlignCenter)
        before_label = QLabel("No preview", panel)
        before_label.setAlignment(Qt.AlignCenter)
        before_label.setMinimumSize(200, 200)
        before_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        before_label.setStyleSheet("background-color: #111; color: #777; border: 1px solid #444;")

        after_title = QLabel("Aligned preview", panel)
        after_title.setAlignment(Qt.AlignCenter)
        after_label = QLabel("No preview", panel)
        after_label.setAlignment(Qt.AlignCenter)
        after_label.setMinimumSize(200, 200)
        after_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        after_label.setStyleSheet("background-color: #111; color: #777; border: 1px solid #444;")

        before_container = QVBoxLayout()
        before_container.setContentsMargins(0, 0, 0, 0)
        before_container.setSpacing(4)
        before_container.addWidget(before_title)
        before_container.addWidget(before_label, 1)

        after_container = QVBoxLayout()
        after_container.setContentsMargins(0, 0, 0, 0)
        after_container.setSpacing(4)
        after_container.addWidget(after_title)
        after_container.addWidget(after_label, 1)

        preview_layout.addLayout(before_container, 1)
        preview_layout.addLayout(after_container, 1)
        panel_layout.addLayout(preview_layout)

        panel_layout.addStretch(1)

        self._locator_panel = panel
        self._locator_preview_before = before_label
        self._locator_preview_after = after_label

        self._roi_layout.insertWidget(1, panel)
        self._on_locator_use_golden_changed(self._use_golden_checkbox.isChecked())

    def _set_locator_message(self, text: str, color: Optional[str] = None) -> None:
        if self._locator_message_label is None:
            return
        if not text:
            self._locator_message_label.clear()
            self._locator_message_label.setVisible(False)
            return
        if not color:
            color = "#a33"
        self._locator_message_label.setStyleSheet(f"color: {color};")
        self._locator_message_label.setText(text)
        self._locator_message_label.setVisible(True)

    def _on_locator_use_golden_changed(self, checked: bool) -> None:
        if self._template_container is not None:
            self._template_container.setVisible(not checked)
        if self._template_editor is not None:
            self._template_editor.setEnabled(self._golden_pixmap is not None and not checked)

    def _gather_params_from_widgets(self) -> dict[str, Any]:
        params = dict(getattr(self._tool.params, "values", {}) or {})
        for name, widget in self._param_fields.items():
            spec = self._param_specs.get(name, {})
            params[name] = self._get_widget_value(widget, spec)

        if self._is_locator_template:
            use_golden = True
            if self._use_golden_checkbox is not None:
                use_golden = bool(self._use_golden_checkbox.isChecked())
            params["use_golden_crop"] = use_golden
            if use_golden:
                params["template_roi"] = None
            else:
                rect = self._template_editor.roi() if self._template_editor is not None else None
                params["template_roi"] = self._rect_to_dict(rect)

        return params

    def _gather_thresholds_from_widgets(self) -> dict[str, Any]:
        thresholds = dict(getattr(self._tool.thresholds, "values", {}) or {})
        for name, widget in self._threshold_fields.items():
            spec = self._threshold_specs.get(name, {})
            thresholds[name] = self._get_widget_value(widget, spec)
        return thresholds

    @staticmethod
    def _rect_to_dict(rect: Optional[tuple[int, int, int, int]]) -> Optional[dict[str, int]]:
        if rect is None:
            return None
        x, y, w, h = rect
        return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}

    @staticmethod
    def _ensure_gray_u8(image: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if image is None:
            return None
        arr = np.asarray(image)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8, copy=False)
        return arr

    @staticmethod
    def _align_preview(frame: np.ndarray, dx: float, dy: float) -> np.ndarray:
        h, w = frame.shape[:2]
        matrix = np.array([[1.0, 0.0, -float(dx)], [0.0, 1.0, -float(dy)]], dtype=np.float32)
        aligned = cv2.warpAffine(
            frame,
            matrix,
            (int(w), int(h)),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        return aligned

    def _update_locator_metrics(
        self,
        result: Optional[ToolRunResult],
        diagnostics: Optional[dict[str, Any]] = None,
    ) -> None:
        if self._locator_metrics_label is None:
            return
        if not result:
            self._locator_metrics_label.setText("corr: —    dx: —    dy: —")
            return
        metrics = result.metrics if result.metrics is not None else {}
        diag_metrics = diagnostics or {}
        corr = float(metrics.get("corr", diag_metrics.get("corr", 0.0)))
        dx = float(metrics.get("dx", diag_metrics.get("dx", 0.0)))
        dy = float(metrics.get("dy", diag_metrics.get("dy", 0.0)))
        self._locator_metrics_label.setText(f"corr: {corr:.4f}    dx: {dx:.2f}    dy: {dy:.2f}")

        status = result.status
        if status:
            color_map = {"ok": "#2d8a34", "warn": "#e67e22", "nok": "#a33"}
            self._set_locator_message(
                f"Status: {status.upper()}", color_map.get(status, "#666")
            )
        else:
            self._set_locator_message("")

    def _update_locator_preview(
        self,
        before: Optional[np.ndarray],
        after: Optional[np.ndarray],
    ) -> None:
        def _apply(label: Optional[QLabel], image: Optional[np.ndarray]) -> None:
            if label is None:
                return
            if image is None:
                label.setPixmap(QPixmap())
                label.setText("No preview")
                return
            pixmap = self._pixmap_from_array(image)
            if pixmap is None:
                label.setPixmap(QPixmap())
                label.setText("No preview")
                return
            size = label.size()
            if size.width() > 0 and size.height() > 0:
                pixmap = pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(pixmap)
            label.setText("")

        _apply(self._locator_preview_before, before)
        _apply(self._locator_preview_after, after)

    def _on_locator_evaluate(self) -> None:
        if self._golden_image is None:
            self._set_locator_message("Golden image not available.")
            self._update_locator_metrics(None)
            self._update_locator_preview(None, None)
            return

        frame: Optional[np.ndarray] = None
        if self._live_preview is not None:
            try:
                frame = self._live_preview.last_frame_u8()
            except Exception as exc:  # pragma: no cover - defensive
                self._set_locator_message(f"Live preview error: {exc}")
        if frame is None and self._camera_service is not None:
            try:
                frame = self._camera_service.one_shot()
            except Exception as exc:  # pragma: no cover - capture fallback
                self._set_locator_message(f"Capture failed: {exc}")
        if frame is None:
            self._set_locator_message("Frame not available for evaluation.")
            self._update_locator_metrics(None)
            self._update_locator_preview(None, None)
            return

        frame_u8 = self._ensure_gray_u8(frame)
        golden_u8 = self._ensure_gray_u8(self._golden_image)
        if frame_u8 is None or golden_u8 is None:
            self._set_locator_message("Frame conversion failed.")
            self._update_locator_metrics(None)
            self._update_locator_preview(None, None)
            return

        params = self._gather_params_from_widgets()
        thresholds = self._gather_thresholds_from_widgets()

        search_rect = self._roi_editor.roi() if self._roi_editor is not None else None
        search_roi_obj: Optional[ToolRoi] = None
        if search_rect is not None:
            search_roi_obj = ToolRoi()
            search_roi_obj.set_rect(search_rect)

        try:
            tool_result, diagnostics = run_locator_template_match(
                golden_u8,
                frame_u8,
                params,
                thresholds,
                search_roi_obj,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._set_locator_message(f"Locator evaluation failed: {exc}")
            self._update_locator_metrics(None)
            self._update_locator_preview(frame_u8, None)
            return

        dx = float(tool_result.metrics.get("dx", diagnostics.get("dx", 0.0)))
        dy = float(tool_result.metrics.get("dy", diagnostics.get("dy", 0.0)))
        aligned = self._align_preview(frame_u8, dx, dy)
        self._update_locator_metrics(tool_result, diagnostics)
        self._update_locator_preview(frame_u8, aligned)

    def result_tool(self) -> Tool:
        return self._tool.copy()

    def accept(self) -> None:
        if not self._apply_config_changes():
            return
        supports_roi = bool(getattr(self._meta_caps, "supports_roi", True))
        supports_mask = bool(getattr(self._meta_caps, "supports_ignore_mask", False))

        params_values = dict(self._tool.params.values)

        if supports_roi:
            roi_rect = self._roi_editor.roi() if self._roi_editor is not None else None
            roi = ToolRoi()
            roi.set_rect(roi_rect)
            self._tool.roi = roi
            params_values["roi"] = roi.to_dict() if roi_rect is not None else None
        else:
            self._tool.roi = ToolRoi()
            params_values.pop("roi", None)

        if supports_mask:
            mask = self._mask_editor.mask() if self._mask_editor is not None else None
            if mask is None or not np.any(mask):
                self._tool.ignore_mask = ToolMask(None)
                params_values.pop("ignore_mask", None)
            else:
                mask_obj = ToolMask(mask)
                self._tool.ignore_mask = mask_obj
                encoded_mask = mask_obj.to_dict()
                if encoded_mask is None:
                    params_values.pop("ignore_mask", None)
                else:
                    params_values["ignore_mask"] = encoded_mask
        else:
            self._tool.ignore_mask = ToolMask(None)
            params_values.pop("ignore_mask", None)

        self._tool.params = ToolParams(params_values)

        super().accept()

    def _build_config_form(self) -> None:
        self._param_fields.clear()
        self._threshold_fields.clear()
        self._param_wrappers.clear()
        self._threshold_wrappers.clear()
        self._param_error_labels.clear()
        self._threshold_error_labels.clear()

        while self._config_form.rowCount():
            self._config_form.removeRow(0)

        self._locator_template_specs.clear()

        try:
            schema = ToolRegistry.get_tool_schema(self._tool.type)
        except KeyError:
            schema = {"params": {}, "thresholds": {}}

        self._param_specs = {k: dict(v) for k, v in (schema.get("params") or {}).items()}
        self._threshold_specs = {k: dict(v) for k, v in (schema.get("thresholds") or {}).items()}

        if self._is_locator_template:
            for key in ("use_golden_crop", "template_roi"):
                spec = self._param_specs.pop(key, None)
                if spec is not None:
                    self._locator_template_specs[key] = spec

        fields_added = 0

        param_values = dict(getattr(self._tool.params, "values", {}) or {})
        threshold_values = dict(getattr(self._tool.thresholds, "values", {}) or {})

        if self._param_specs:
            header = QLabel("Parameters", self)
            header.setStyleSheet("font-weight: 600; padding-top: 4px;")
            self._config_form.addRow(header)
            for name, spec in self._param_specs.items():
                if spec.get("type") not in _SUPPORTED_FORM_FIELD_TYPES:
                    continue
                widget = _create_form_widget(spec, self)
                if widget is None:
                    continue
                current_value = param_values.get(name)
                self._set_widget_value(widget, spec, current_value)
                self._param_fields[name] = widget
                label_text = spec.get("label")
                if label_text is None:
                    label_text = name
                else:
                    label_text = str(label_text)
                label_widget = QLabel(label_text, self)
                container, error_label = self._create_field_container(widget)
                self._param_wrappers[name] = container
                self._param_error_labels[name] = error_label
                tooltip = _format_spec_tooltip(spec)
                if tooltip:
                    widget.setToolTip(tooltip)
                    label_widget.setToolTip(tooltip)
                    container.setToolTip(tooltip)
                self._config_form.addRow(label_widget, container)
                self._connect_field_signals(widget, spec, kind="param", name=name)
                fields_added += 1

        if self._threshold_specs:
            header = QLabel("Thresholds", self)
            header.setStyleSheet("font-weight: 600; padding-top: 8px;")
            self._config_form.addRow(header)
            for name, spec in self._threshold_specs.items():
                if spec.get("type") not in _SUPPORTED_FORM_FIELD_TYPES:
                    continue
                widget = _create_form_widget(spec, self)
                if widget is None:
                    continue
                current_value = threshold_values.get(name)
                self._set_widget_value(widget, spec, current_value)
                self._threshold_fields[name] = widget
                label_text = spec.get("label")
                if label_text is None:
                    label_text = name
                else:
                    label_text = str(label_text)
                label_widget = QLabel(label_text, self)
                container, error_label = self._create_field_container(widget)
                self._threshold_wrappers[name] = container
                self._threshold_error_labels[name] = error_label
                tooltip = _format_spec_tooltip(spec)
                if tooltip:
                    widget.setToolTip(tooltip)
                    label_widget.setToolTip(tooltip)
                    container.setToolTip(tooltip)
                self._config_form.addRow(label_widget, container)
                self._connect_field_signals(widget, spec, kind="threshold", name=name)
                fields_added += 1

        if not fields_added:
            placeholder = QLabel("No configurable thresholds or parameters for this tool.", self)
            placeholder.setStyleSheet("color: #666;")
            placeholder.setWordWrap(True)
            self._config_form.addRow(placeholder)

        if hasattr(self, "_btn_restore_defaults") and self._btn_restore_defaults is not None:
            self._btn_restore_defaults.setEnabled(bool(fields_added))

        self._validate_form()

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

    def _connect_field_signals(
        self, widget: QWidget, spec: dict[str, Any], *, kind: str, name: str
    ) -> None:
        if isinstance(widget, QCheckBox):
            widget.toggled.connect(lambda _checked, k=kind, n=name: self._on_field_event(k, n))
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(
                lambda _index, k=kind, n=name: self._on_field_event(k, n)
            )
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.editingFinished.connect(lambda k=kind, n=name: self._on_field_event(k, n))
            widget.valueChanged.connect(lambda _value, k=kind, n=name: self._on_field_event(k, n))

    def _on_field_event(self, kind: str, name: str) -> None:
        if self._updating_form:
            return
        self._validate_form()

    def _on_restore_defaults_clicked(self) -> None:
        if self._updating_form:
            return
        for name, widget in self._param_fields.items():
            spec = self._param_specs.get(name, {})
            self._set_widget_value(widget, spec, spec.get("default"))
        for name, widget in self._threshold_fields.items():
            spec = self._threshold_specs.get(name, {})
            self._set_widget_value(widget, spec, spec.get("default"))
        self._validate_form()

    def _set_widget_value(self, widget: QWidget, spec: dict[str, Any], value: Any) -> None:
        self._updating_form = True
        try:
            _set_form_widget_value(widget, spec, value)
        finally:
            self._updating_form = False

    def _get_widget_value(self, widget: QWidget, spec: dict[str, Any]) -> Any:
        return _get_form_widget_value(widget, spec)

    def _collect_form_values(self) -> tuple[dict[str, Any], dict[str, Any]]:
        params: dict[str, Any] = {}
        thresholds: dict[str, Any] = {}
        for name, widget in self._param_fields.items():
            spec = self._param_specs.get(name, {})
            params[name] = self._get_widget_value(widget, spec)
        for name, widget in self._threshold_fields.items():
            spec = self._threshold_specs.get(name, {})
            thresholds[name] = self._get_widget_value(widget, spec)
        self._current_form_values = {"params": dict(params), "thresholds": dict(thresholds)}
        return params, thresholds

    def _validate_form(self) -> None:
        params, thresholds = self._collect_form_values()
        ok, errors, normalized = _validate_params_and_thresholds(
            params,
            thresholds,
            self._param_specs,
            self._threshold_specs,
        )

        self._validation_ok = ok
        self._last_validation = normalized

        self._apply_validation_feedback(
            params,
            normalized.get("params", {}),
            errors.get("params", {}),
            self._param_fields,
            self._param_wrappers,
            self._param_error_labels,
            self._param_specs,
        )
        self._apply_validation_feedback(
            thresholds,
            normalized.get("thresholds", {}),
            errors.get("thresholds", {}),
            self._threshold_fields,
            self._threshold_wrappers,
            self._threshold_error_labels,
            self._threshold_specs,
        )

        messages: list[str] = []
        for section, specs, section_errors in (
            ("params", self._param_specs, errors.get("params", {})),
            ("thresholds", self._threshold_specs, errors.get("thresholds", {})),
        ):
            for name, errs in section_errors.items():
                label = specs.get(name, {}).get("label", name)
                for err in errs:
                    messages.append(f"{label}: {err}")

        self._form_errors = messages
        self._form_error_label.setVisible(bool(messages))
        if messages:
            self._form_error_label.setText("\n".join(messages))
        else:
            self._form_error_label.clear()

        if hasattr(self, "_ok_button") and self._ok_button is not None:
            self._ok_button.setEnabled(ok)

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
    def _values_equal(a: Any, b: Any) -> bool:
        if isinstance(a, float) or isinstance(b, float):
            try:
                return abs(float(a) - float(b)) < 1e-9
            except (TypeError, ValueError):
                return False
        return a == b

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

    def _apply_config_changes(self) -> bool:
        self._validate_form()
        if not self._validation_ok:
            message = "\n".join(self._form_errors) if self._form_errors else "Please fix validation errors before saving."
            QMessageBox.warning(self, "Validation failed", message)
            return False

        params = dict(self._current_form_values.get("params", {}))
        params.update(self._last_validation.get("params", {}))

        thresholds = dict(self._current_form_values.get("thresholds", {}))
        thresholds.update(self._last_validation.get("thresholds", {}))

        if self._is_locator_template:
            use_golden = bool(params.get("use_golden_crop", True))
            if use_golden:
                params["template_roi"] = None
                self._tool.template_roi = ToolRoi()
            else:
                rect = self._template_editor.roi() if self._template_editor is not None else None
                params["template_roi"] = self._rect_to_dict(rect)
                roi_obj = ToolRoi()
                roi_obj.set_rect(rect)
                self._tool.template_roi = roi_obj

        self._tool.params = ToolParams(params)
        self._tool.thresholds = ToolThresholds(thresholds)
        return True

    @staticmethod
    def _pixmap_from_array(img: Optional[np.ndarray]) -> Optional[QPixmap]:
        if img is None:
            return None
        arr = np.asarray(img)
        if arr.size == 0:
            return None
        if arr.ndim == 2:
            arr_u8 = np.ascontiguousarray(arr.astype(np.uint8))
            height, width = arr_u8.shape
            bytes_per_line = arr_u8.strides[0]
            qimg = QImage(arr_u8.data, width, height, bytes_per_line, QImage.Format_Grayscale8)
            return QPixmap.fromImage(qimg.copy())
        if arr.ndim == 3:
            if arr.shape[2] == 1:
                single = np.ascontiguousarray(arr[:, :, 0].astype(np.uint8))
                height, width = single.shape
                qimg = QImage(single.data, width, height, single.strides[0], QImage.Format_Grayscale8)
                return QPixmap.fromImage(qimg.copy())
            if arr.shape[2] >= 4:
                rgba = cv2.cvtColor(arr[:, :, :4].astype(np.uint8), cv2.COLOR_BGRA2RGBA)
                height, width, _ = rgba.shape
                qimg = QImage(rgba.data, width, height, rgba.strides[0], QImage.Format_RGBA8888)
                return QPixmap.fromImage(qimg.copy())
            bgr = np.ascontiguousarray(arr[:, :, :3].astype(np.uint8))
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            height, width, _ = rgb.shape
            qimg = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format_RGB888)
            return QPixmap.fromImage(qimg.copy())
        reshaped = np.ascontiguousarray(arr.astype(np.uint8).reshape(arr.shape[0], arr.shape[1]))
        height, width = reshaped.shape
        qimg = QImage(reshaped.data, width, height, reshaped.strides[0], QImage.Format_Grayscale8)
        return QPixmap.fromImage(qimg.copy())


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

        self._btn_test = QPushButton("Test", self)
        self._btn_test.clicked.connect(self._on_test_clicked)

        self._btn_defaults = QPushButton("Restore defaults", self)
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

        self._preview_toggle_aligned = QCheckBox("Preview aligned", self._preview_toggle_container)
        self._preview_toggle_aligned.toggled.connect(
            lambda checked: self._on_preview_toggle_changed("aligned", checked)
        )
        toggle_layout.addWidget(self._preview_toggle_aligned)

        self._preview_toggle_binarized = QCheckBox("Preview binarization", self._preview_toggle_container)
        self._preview_toggle_binarized.toggled.connect(
            lambda checked: self._on_preview_toggle_changed("binarization", checked)
        )
        toggle_layout.addWidget(self._preview_toggle_binarized)
        self._preview_toggle_overlay = QCheckBox("Preview overlay", self._preview_toggle_container)
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

        self._preview_before_label = QLabel("No preview", self._preview_widget)
        self._preview_before_label.setAlignment(Qt.AlignCenter)
        self._preview_before_label.setMinimumSize(160, 160)
        self._preview_before_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._preview_before_label.setStyleSheet(
            "background-color: #111; color: #777; border: 1px solid #333;"
        )
        self._preview_before_label.setScaledContents(True)
        preview_layout.addWidget(self._preview_before_label, 1)

        self._preview_after_label = QLabel("No preview", self._preview_widget)
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
            header = QLabel("Parameters", self)
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
            header = QLabel("Thresholds", self)
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
        preview = debug_artifacts.get("preview") if debug_artifacts else None
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
            preview,
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
        self._preview_before_label.setText("No preview")
        self._preview_before_label.setPixmap(QPixmap())
        self._preview_after_label.setText("No preview")
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
            if abs(value) >= 1000 or (0 < abs(value) < 0.01):
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

        self._apply_preview_pixmap(self._preview_before_label, before_pixmap, "No preview")
        self._apply_preview_pixmap(self._preview_after_label, after_pixmap, "No preview")
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
    def __init__(self, camera, recipes: RecipeService, parent=None):
        super().__init__(parent)

        self._base_title = "Golden WIZARD"
        self.setWindowTitle(self._base_title)
        self.setModal(True)
        self.cam = camera
        self.recipes = recipes
        self.current_img = None

        self._saved_snapshots: dict[str, list[dict[str, Any]]] = {}
        self._dirty_recipes: dict[str, bool] = {}

        # --- Live infra (len video label, bez kreslenia) ---

        dev = os.environ.get("CAM_DEV") or getattr(self.cam, "devices", ["/dev/video0"])[0]
        print(f"[GoldenWizard] Live device: {dev}")
        self._lp = LivePreviewService(dev, 1280, 720, 60)

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(50)  # ~20 FPS
        self._live_timer.timeout.connect(self._live_tick)
        self._live_on = False

        # ---- Horná lišta ----
        current_recipe = getattr(self.recipes.tool, "recipe", "default")
        self.recipe_name = QLineEdit(current_recipe, self)
        self.chk_pose    = QCheckBox("Enable pose alignment")
        self.chk_pose.setChecked(getattr(self.recipes.tool, "pose_enabled", False))

        self._updating_policy_combo = False
        self._current_locator_failure_policy = "continue_without_alignment"
        self.failure_policy_combo = QComboBox(self)
        self.failure_policy_combo.addItem(
            "Pokračovať bez zarovnania", "continue_without_alignment"
        )
        self.failure_policy_combo.addItem("Zlyhať pipeline", "fail")
        self.failure_policy_combo.setToolTip(
            "Ako má pipeline reagovať, keď locator nezarovná frame."
        )

        self.btn_add_tool = QPushButton("Add tool")
        self.btn_add_tool.clicked.connect(self._open_tool_catalog)

        # Toggle Live
        self.btn_live = QPushButton("Live OFF")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._toggle_live)

        self._session_settings_button = QToolButton(self)
        self._session_settings_button.setText("⚙")
        self._session_settings_button.setToolTip("Session settings")
        self._session_settings_button.setAutoRaise(True)
        self._session_settings_button.clicked.connect(self._open_session_settings)

        top = QHBoxLayout()
        top.addWidget(QLabel("Recept:")); top.addWidget(self.recipe_name)
        top.addStretch(1)
        top.addWidget(self.chk_pose)
        top.addWidget(QLabel("Zlyhanie locatora:", self))
        top.addWidget(self.failure_policy_combo)
        top.addWidget(self._session_settings_button)
        top.addWidget(self.btn_add_tool)
        top.addWidget(self.btn_live)

        # ---- Dva režimy zobrazenia ----
        # 1) Live LABEL (video) – používa sa len pri Live ON
        self.live_lbl = QLabel("—")
        self.live_lbl.setAlignment(Qt.AlignCenter)
        self.live_lbl.setMinimumHeight(360)
        self.live_lbl.hide()  # default skryté

        # 2) DrawView (kreslenie) – používa sa pri Live OFF
        self.view = DrawView(self)

        # ---- Ovládacie tlačidlá ----
        btn_cap_golden   = QPushButton("Získať GOLDEN z kamery")
        btn_load_golden  = QPushButton("Načítať GOLDEN z disku")
        self.btn_save_tool = QPushButton("Save Tool")
        self.btn_test_tool = QPushButton("Test")
        self.btn_test_tool.setEnabled(False)
        self.btn_publish_recipe = QPushButton("Publish/Update Recipe")

        buttons = QHBoxLayout()
        buttons.addWidget(btn_cap_golden)
        buttons.addWidget(btn_load_golden)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_save_tool)
        buttons.addWidget(self.btn_test_tool)
        self._publish_state_label = QLabel("", self)
        self._publish_state_label.setStyleSheet("color: #999; font-style: italic;")
        self._publish_state_label.setMinimumWidth(160)
        self._publish_state_label.setAlignment(Qt.AlignCenter)
        buttons.addWidget(self._publish_state_label)
        buttons.addWidget(self.btn_publish_recipe)

        # ---- Layout ----
        self._tool_panel = ToolConfigPanel(self)
        self._tool_panel.setMinimumWidth(280)
        self._tool_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        self.tools_table = ToolsTableWidget(0, 5, self)
        self.tools_table.setHorizontalHeaderLabels(["Order", "Name", "Type", "Enabled", "Actions"])
        header = self.tools_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
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
        tools_label = QLabel("Tools in recipe:", self)
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
        left_layout.addWidget(self.tools_table)
        left_layout.addWidget(self.locator_hint_label)
        left_layout.addWidget(self.locator_policy_banner)
        left_layout.addLayout(buttons)

        content_layout.addLayout(left_layout, 3)
        content_layout.addWidget(self._tool_panel, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(content_layout, 1)

        self._tool_panel.clear()

        # signály
        btn_cap_golden.clicked.connect(self._capture_golden)
        btn_load_golden.clicked.connect(self._load_golden)
        self.btn_save_tool.clicked.connect(self._save_tool_draft)
        self.btn_test_tool.clicked.connect(self._trigger_test_shortcut)
        self.btn_publish_recipe.clicked.connect(self._publish_recipe)
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
        try:
            self.recipes.load_tools(self._last_recipe, use_draft=True)
        except Exception as exc:
            print(f"[GoldenWizard] load_tools failed for {self._last_recipe}: {exc}")
        self._record_saved_snapshot(self._last_recipe)
        self._sync_locator_policy_ui(self._last_recipe)
        self._refresh_tools_table()
        self._on_tool_selection_changed()
        self._refresh_publish_state()

    # ---------- Live ----------
    def _toggle_live(self, checked: bool):
        if checked:
            try:
                self.cam.pause_for_external()
            except Exception as e:
                print("[GoldenWizard] pause_for_external:", e)


            # Zapnúť live: zobraz label, skryť DrawView (žiadne kreslenie počas live)
            self.view.hide()
            self.live_lbl.show()
            try:
                self._lp.start()
                self._live_timer.start()
                self._live_on = True
                self.btn_live.setText("Live ON")
            except Exception as e:
                self._err(f"Live feed sa nepodarilo spustiť: {e}")
                self.btn_live.setChecked(False)
                self.live_lbl.setText("—")
                self._live_on = False
        else:
            # Vypnúť live: skryť label, ukázať DrawView
            self._live_timer.stop()
            try:
                self._lp.stop()
            except Exception:
                pass
            self._live_on = False
            self.btn_live.setText("Live OFF")
            self.live_lbl.hide()
            self.view.show()

    def _live_tick(self):
        img = self._lp.last_frame_u8()
        if img is None:
            return
        h, w = img.shape[:2]
        qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy()).scaled(self.live_lbl.width(), self.live_lbl.height(),
                                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.live_lbl.setPixmap(pm)

    # ---------- UI util ----------
    def _set_pixmap(self, img_u8):
        # img_u8: numpy uint8 (H, W)
        h, w = img_u8.shape[:2]
        qimg = QImage(img_u8.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg.copy())
        self.view.set_background(pm)

    def _current_golden_image(self) -> Optional[np.ndarray]:
        if self.current_img is not None:
            return self.current_img

        golden = getattr(self.recipes.tool, "golden", None)
        if isinstance(golden, np.ndarray):
            return golden

        recipe = self._current_recipe_name()
        path = Path("/data") / "recipes" / recipe / "golden.png"
        if not path.exists():
            return None

        try:
            import imageio.v3 as iio

            img = iio.imread(path)
        except Exception:
            return None

        if img.ndim == 3:
            img = img[:, :, 0]
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        return img

    # ---------- Akcie ----------
    def _capture_golden(self):
        try:
            # ak je live ON, zober aktuálny frame a hneď live vypni (freeze)
            frame = (self._lp.last_frame_u8() if self._live_on else None)
            if frame is None:
                frame = self.cam.one_shot()
            self.current_img = frame
            self._set_pixmap(frame)
            if self._live_on:
                self.btn_live.setChecked(False)
                self._toggle_live(False)  # vypnúť live, prepnúť späť na DrawView

            # po vypnutí live obnov kameru
            try:
                self.cam.resume_after_external()
            except Exception as e:
                print("[GoldenWizard] resume_after_external:", e)

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
        golden_path = save_golden(self.current_img, name)
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
        self._record_saved_snapshot(recipe)
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
        self._record_saved_snapshot(recipe)
        try:
            _, autosorted_publish = self.recipes.publish_recipe(recipe)
        except Exception as exc:
            self._err(f"Publikovanie receptu zlyhalo: {exc}")
            return
        self._record_saved_snapshot(recipe)
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
        recipe = self._current_recipe_name()
        try:
            state = self.recipes.publish_state(recipe)
        except Exception:
            state = {"draft_updated_at": None, "published_at": None, "has_unpublished_changes": False}

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
    def _snapshot_tools(self, recipe: str) -> list[dict[str, Any]]:
        try:
            tools = self.recipes.get_draft_tools(recipe)
        except Exception:
            return []
        return [tool.to_dict() for tool in tools]

    def _record_saved_snapshot(self, recipe: str) -> None:
        snapshot = self._snapshot_tools(recipe)
        self._saved_snapshots[recipe] = snapshot
        self._dirty_recipes[recipe] = False
        self._update_dirty_state(recipe)

    def _update_dirty_state(self, recipe: Optional[str] = None) -> None:
        if not hasattr(self, "_saved_snapshots"):
            return
        recipe = recipe or self._current_recipe_name()
        current = self._snapshot_tools(recipe)
        saved = self._saved_snapshots.get(recipe)
        dirty = saved is None or current != saved
        self._dirty_recipes[recipe] = dirty
        self._update_window_title_dirty()

    def _update_window_title_dirty(self) -> None:
        if not hasattr(self, "_base_title"):
            return
        current_recipe = self._current_recipe_name()
        dirty = self._dirty_recipes.get(current_recipe, False)
        title = self._base_title + (" *" if dirty else "")
        self.setWindowTitle(title)

    def _has_unsaved_changes(self) -> bool:
        if not hasattr(self, "_dirty_recipes"):
            return False
        return any(self._dirty_recipes.values())

    def _trigger_test_shortcut(self) -> None:
        panel = getattr(self, "_tool_panel", None)
        if panel is None:
            return
        trigger = getattr(panel, "trigger_test", None)
        if callable(trigger):
            trigger()

    # ---------- Info/Err ----------
    def _info(self, msg):
        QMessageBox.information(self, "Info", msg)

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

    def _on_recipe_changed(self):
        recipe = self._current_recipe_name()
        if recipe == getattr(self, "_last_recipe", None):
            return
        try:
            self.recipes.load_tools(recipe, use_draft=True)
        except Exception as exc:
            print(f"[GoldenWizard] load_tools failed for {recipe}: {exc}")
        self._last_recipe = recipe
        self._record_saved_snapshot(recipe)
        self._sync_locator_policy_ui(recipe)
        self._refresh_tools_table()
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
            self.recipes.add_tool(self._current_recipe_name(), tool)
            self._refresh_tools_table()
        except Exception as exc:
            self._err(f"Pridanie nástroja zlyhalo: {exc}")

    def _refresh_tools_table(self):
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
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

            btn_edit = QPushButton("Edit", actions_widget)
            btn_edit.clicked.connect(lambda _, idx=row: self._edit_tool(idx))
            btn_del = QPushButton("Delete", actions_widget)
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

        self._update_dirty_state(recipe)

    def _delete_tool(self, index: int):
        recipe = self._current_recipe_name()
        self.recipes.remove_tool(recipe, index)
        self._refresh_tools_table()

    def _on_tool_enabled_toggled(self, index: int, enabled: bool) -> None:
        self._toggle_tool_enabled(index, enabled)

    def _toggle_tool_enabled(self, index: int, enabled: bool) -> None:
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
        if not (0 <= index < len(tools)):
            return

        tool = tools[index]
        if bool(tool.enabled) == bool(enabled):
            return

        tool.enabled = bool(enabled)
        try:
            self.recipes.update_tool(recipe, index, tool)
        except Exception as exc:
            self._err(f"Prepnutie nástroja zlyhalo: {exc}")
            self._refresh_tools_table()
            return

        self._selected_tool_row = index
        self._refresh_tools_table()

    def _on_tools_reordered(self, new_order: list[int]) -> None:
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
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
            self.recipes.reorder_tools(recipe, new_order)
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
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
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
                return
            self._tool_panel.set_tool(tool, meta, schema)
            self._tool_panel.set_locator_failure_policy(
                self._current_locator_failure_policy
            )
            self._selected_tool_row = row
        else:
            self._tool_panel.clear()
            self._selected_tool_row = -1

    def _on_tool_param_changed(self, name: str, value: Any) -> None:
        row = getattr(self, "_selected_tool_row", -1)
        if row < 0:
            return
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
        if not (0 <= row < len(tools)):
            return
        tool = tools[row]
        params = dict(getattr(tool.params, "values", {}) or {})
        params[name] = value
        tool.params = ToolParams(params)
        try:
            self.recipes.update_tool(recipe, row, tool)
        except Exception as exc:
            self._err(f"Uloženie parametra zlyhalo: {exc}")
            self._refresh_tools_table()
            return
        self._tool_panel.refresh_values(tool)
        self._update_dirty_state(recipe)

    def _on_tool_threshold_changed(self, name: str, value: Any) -> None:
        row = getattr(self, "_selected_tool_row", -1)
        if row < 0:
            return
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
        if not (0 <= row < len(tools)):
            return
        tool = tools[row]
        thresholds = dict(getattr(tool.thresholds, "values", {}) or {})
        thresholds[name] = value
        tool.thresholds = ToolThresholds(thresholds)
        try:
            self.recipes.update_tool(recipe, row, tool)
        except Exception as exc:
            self._err(f"Uloženie thresholdu zlyhalo: {exc}")
            self._refresh_tools_table()
            return
        self._tool_panel.refresh_values(tool)
        self._update_dirty_state(recipe)

    def _on_tool_test_requested(self, params: dict[str, Any], thresholds: dict[str, Any]) -> None:
        try:
            row = getattr(self, "_selected_tool_row", -1)
            if row < 0:
                self._tool_panel.show_test_error("Najprv vyber nástroj v tabuľke.")
                return

            recipe = self._current_recipe_name()
            tools = self.recipes.get_draft_tools(recipe)
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
            if self._live_on:
                try:
                    frame = self._lp.last_frame_u8()
                except Exception as exc:  # pragma: no cover - defensive
                    capture_errors.append(f"Live preview zlyhal: {exc}")
            if frame is None:
                try:
                    frame = self.cam.one_shot()
                except Exception as exc:  # pragma: no cover - fallback
                    capture_errors.append(f"Zachytenie zlyhalo: {exc}")

            if frame is None:
                message = capture_errors[-1] if capture_errors else "Frame nie je dostupný."
                self._tool_panel.show_test_error(message)
                return

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
        tools = self.recipes.get_draft_tools(recipe)
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
                parent=self,
            )
            if dialog.exec() != QDialog.Accepted:
                return

            updated_tool = dialog.result_tool()
            try:
                self.recipes.update_tool(recipe, index, updated_tool)
            except Exception as exc:
                self._err(f"Uloženie nástroja zlyhalo: {exc}")
                return
            self._refresh_tools_table()

    def _persist_tools(self, recipe: str) -> tuple[bool, bool]:
        tools = self.recipes.get_draft_tools(recipe)
        try:
            _, autosorted = self.recipes.save_tools(recipe, tools)
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
        try:
            self._live_timer.stop()
            self._lp.stop()
        except Exception:
            pass
        e.accept()

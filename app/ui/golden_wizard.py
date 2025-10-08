# app/ui/golden_wizard.py
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QImage, QColor
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
)

import os
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import cv2

from app.ui.draw_view import DrawView, RoiMaskEditor, RoiMaskGraphicsView
from app.services.storage_service import save_golden, save_validation_image
from app.models.regions import Region, validate_cardinality
from app.services.live_preview_service import LivePreviewService
from app.models.schema import (
    RecipeData,
    Tool,
    ToolMask,
    ToolParams,
    ToolRoi,
    ToolThresholds,
)
from app.services.recipe_service import RecipeService
from app.services.tool_service import (
    ToolMeta,
    ToolRunResult,
    run_locator_template_match,
    validate_tool_params,
)


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
                display = f"{meta.display_name} ({tool_type})"
                tooltip = meta.description
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

        self._view = RoiMaskGraphicsView(self)
        self._view.set_mode(RoiMaskGraphicsView.MODE_ROI)
        self._view.maskChanged.connect(lambda *_: None)
        self._view.roiChanged.connect(self.roiChanged)

        self._btn_reset = QPushButton("Reset Template ROI", self)
        self._btn_reset.clicked.connect(self._view.reset_roi)

        view_layout = QVBoxLayout(self)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(4)
        view_layout.addWidget(self._view, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addStretch(1)
        controls.addWidget(self._btn_reset)
        view_layout.addLayout(controls)

    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._view.set_background(pixmap)

    def set_roi(self, roi: Optional[tuple[int, int, int, int]]) -> None:
        self._view.set_roi(roi)

    def roi(self) -> Optional[tuple[int, int, int, int]]:
        return self._view.roi()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt API
        super().setEnabled(enabled)
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

        self._editor = RoiMaskEditor(self)
        self._editor.set_roi_enabled(getattr(meta, "supports_roi", True))
        self._editor.set_mask_enabled(getattr(meta, "supports_ignore_mask", True))

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
        roi_layout.addWidget(self._editor, 1)
        self._roi_layout = roi_layout

        self._info_label = QLabel("", self)
        self._info_label.setStyleSheet("color: #666;")
        self._info_label.setWordWrap(True)
        roi_layout.addWidget(self._info_label)
        self._tabs.addTab(roi_tab, "ROI & Mask")

        self._config_tab = QWidget(self)
        config_layout = QVBoxLayout(self._config_tab)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(0)
        self._config_form = QFormLayout()
        self._config_form.setContentsMargins(8, 8, 8, 8)
        self._config_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        config_layout.addLayout(self._config_form)

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
        if self._golden_pixmap is not None:
            self._editor.set_background(self._golden_pixmap)
            self._info_label.setText("ROI mode selects the inspection window. Mask mode ignores painted pixels.")
            if getattr(meta, "supports_roi", True):
                self._editor.set_roi(self._tool.roi.rect())
            if getattr(meta, "supports_ignore_mask", True):
                mask_value = self._tool.ignore_mask.value
                if mask_value is not None:
                    self._editor.set_mask(mask_value)
        else:
            self._info_label.setText("Golden snapshot is not available – editing is disabled.")
            self._editor.setEnabled(False)

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

        search_rect = self._editor.roi() if self._editor is not None else None
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
        supports_roi = bool(getattr(self._meta, "supports_roi", True))
        supports_mask = bool(getattr(self._meta, "supports_ignore_mask", False))

        if supports_roi:
            roi_rect = self._editor.roi()
            roi = ToolRoi()
            roi.set_rect(roi_rect)
            self._tool.roi = roi
        else:
            self._tool.roi = ToolRoi()

        if supports_mask:
            mask = self._editor.mask()
            if mask is None or not np.any(mask):
                self._tool.ignore_mask = ToolMask(None)
            else:
                self._tool.ignore_mask = ToolMask(mask)
        else:
            self._tool.ignore_mask = ToolMask(None)

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

        self._param_specs = self._normalize_field_definitions(getattr(self._meta, "default_params", {}))
        self._threshold_specs = self._normalize_field_definitions(getattr(self._meta, "default_thresholds", {}))

        if self._is_locator_template:
            for key in ("use_golden_crop", "template_roi"):
                spec = self._param_specs.pop(key, None)
                if spec is not None:
                    self._locator_template_specs[key] = spec

        fields_added = 0

        if self._param_specs:
            header = QLabel("Parameters", self)
            header.setStyleSheet("font-weight: 600; padding-top: 4px;")
            self._config_form.addRow(header)
            for name, spec in self._param_specs.items():
                widget = self._create_input_widget(spec)
                if widget is None:
                    continue
                current_value = self._tool.params.values.get(name)
                self._set_widget_value(widget, spec, current_value)
                self._param_fields[name] = widget
                label_widget = QLabel(spec.get("label", name), self)
                container, error_label = self._create_field_container(widget)
                self._param_wrappers[name] = container
                self._param_error_labels[name] = error_label
                self._config_form.addRow(label_widget, container)
                self._connect_field_signals(widget, spec, kind="param", name=name)
                fields_added += 1

        if self._threshold_specs:
            header = QLabel("Thresholds", self)
            header.setStyleSheet("font-weight: 600; padding-top: 8px;")
            self._config_form.addRow(header)
            for name, spec in self._threshold_specs.items():
                widget = self._create_input_widget(spec)
                if widget is None:
                    continue
                current_value = self._tool.thresholds.values.get(name)
                self._set_widget_value(widget, spec, current_value)
                self._threshold_fields[name] = widget
                label_widget = QLabel(spec.get("label", name), self)
                container, error_label = self._create_field_container(widget)
                self._threshold_wrappers[name] = container
                self._threshold_error_labels[name] = error_label
                self._config_form.addRow(label_widget, container)
                self._connect_field_signals(widget, spec, kind="threshold", name=name)
                fields_added += 1

        if not fields_added:
            placeholder = QLabel("No configurable thresholds or parameters for this tool.", self)
            placeholder.setStyleSheet("color: #666;")
            placeholder.setWordWrap(True)
            self._config_form.addRow(placeholder)

        self._validate_form()

    def _normalize_field_definitions(self, definitions: dict[str, Any]) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for name, raw in (definitions or {}).items():
            if isinstance(raw, dict):
                spec = dict(raw)
            else:
                spec = {"default": raw}
            spec.setdefault("label", name)
            type_name = spec.get("type")
            if type_name is None:
                type_name = self._infer_field_type(spec.get("default"))
            else:
                type_name = str(type_name).lower()
            if type_name == "enum" and "choices" not in spec and "options" in spec:
                spec["choices"] = spec.get("options")
            if type_name not in {"int", "float", "bool", "enum"}:
                continue
            if type_name == "enum":
                choices = spec.get("choices") or []
                normalized_choices: list[tuple[Any, str]] = []
                for choice in choices:
                    if isinstance(choice, dict):
                        value = choice.get("value")
                        label = choice.get("label", str(value))
                    elif isinstance(choice, (list, tuple)) and len(choice) >= 1:
                        value = choice[0]
                        label = choice[1] if len(choice) > 1 else str(choice[0])
                    else:
                        value = choice
                        label = str(choice)
                    normalized_choices.append((value, label))
                spec["choices"] = normalized_choices
            spec["type"] = type_name
            normalized[name] = spec
        return normalized

    @staticmethod
    def _infer_field_type(value: Any) -> str | None:
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int) and not isinstance(value, bool):
            return "int"
        if isinstance(value, float):
            return "float"
        return None

    def _create_input_widget(self, spec: dict[str, Any]) -> QWidget | None:
        field_type = spec.get("type")
        if field_type == "bool":
            checkbox = QCheckBox(self)
            checkbox.setTristate(False)
            checkbox.setChecked(bool(spec.get("default", False)))
            return checkbox
        if field_type == "enum":
            combo = QComboBox(self)
            for value, label in spec.get("choices", []):
                combo.addItem(str(label), value)
            return combo if combo.count() else None
        if field_type == "int":
            spin = QSpinBox(self)
            min_val = spec.get("min")
            max_val = spec.get("max")
            if min_val is not None:
                spin.setMinimum(int(min_val))
            else:
                spin.setMinimum(-10_000_000)
            if max_val is not None:
                spin.setMaximum(int(max_val))
            else:
                spin.setMaximum(10_000_000)
            if (step := spec.get("step")) is not None:
                spin.setSingleStep(max(1, int(step)))
            spin.setValue(int(spec.get("default", 0) or 0))
            return spin
        if field_type == "float":
            spin = QDoubleSpinBox(self)
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
            decimals = int(precision)
            spin.setDecimals(max(0, decimals))
            if (step := spec.get("step")) is not None:
                spin.setSingleStep(float(step))
            else:
                spin.setSingleStep(0.01)
            spin.setValue(float(spec.get("default", 0.0) or 0.0))
            return spin
        return None

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

    def _set_widget_value(self, widget: QWidget, spec: dict[str, Any], value: Any) -> None:
        self._updating_form = True
        try:
            field_type = spec.get("type")
            if value is None:
                value = spec.get("default")
            if field_type == "bool" and isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif field_type == "enum" and isinstance(widget, QComboBox):
                if widget.count() == 0:
                    return
                idx = widget.findData(value)
                if idx < 0:
                    idx = 0
                widget.setCurrentIndex(idx)
            elif field_type == "int" and isinstance(widget, QSpinBox):
                try:
                    widget.setValue(int(round(float(value))))
                except Exception:
                    fallback = spec.get("default")
                    if fallback is None:
                        fallback = widget.minimum()
                    widget.setValue(int(round(float(fallback))))
            elif field_type == "float" and isinstance(widget, QDoubleSpinBox):
                try:
                    widget.setValue(float(value))
                except Exception:
                    fallback = spec.get("default")
                    if fallback is None:
                        fallback = widget.minimum()
                    widget.setValue(float(fallback))
        finally:
            self._updating_form = False

    def _get_widget_value(self, widget: QWidget, spec: dict[str, Any]) -> Any:
        field_type = spec.get("type")
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
        try:
            ok, errors, normalized = validate_tool_params(self._tool.type, params, thresholds)
        except KeyError:
            ok = True
            errors = {"params": {}, "thresholds": {}}
            normalized = {"params": params, "thresholds": thresholds}

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
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.ndim != 2:
            arr = np.mean(arr, axis=-1)
        arr_u8 = np.ascontiguousarray(arr.astype(np.uint8))
        height, width = arr_u8.shape
        bytes_per_line = arr_u8.strides[0]
        qimg = QImage(arr_u8.data, width, height, bytes_per_line, QImage.Format_Grayscale8)
        return QPixmap.fromImage(qimg.copy())


class ToolConfigPanel(QWidget):
    """Side panel for editing tool parameters and thresholds."""

    paramChanged = Signal(str, object)
    thresholdChanged = Signal(str, object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._current_tool: Optional[Tool] = None
        self._param_specs: dict[str, dict[str, Any]] = {}
        self._threshold_specs: dict[str, dict[str, Any]] = {}
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

        self._btn_defaults = QPushButton("Restore defaults", self)
        self._btn_defaults.clicked.connect(self._on_restore_defaults)
        layout.addWidget(self._btn_defaults, 0, Qt.AlignLeft)

        self._update_visibility()

    def clear(self) -> None:
        self._current_tool = None
        self._param_specs.clear()
        self._threshold_specs.clear()
        self._clear_form()
        self._tool_label.setText("No tool selected")
        self._description_label.clear()
        self._update_visibility()

    def set_tool(
        self,
        tool: Tool,
        meta: ToolMeta,
        schema: dict[str, dict[str, dict[str, Any]]],
    ) -> None:
        self._current_tool = tool
        self._param_specs = {k: dict(v) for k, v in (schema.get("params") or {}).items()}
        self._threshold_specs = {
            k: dict(v) for k, v in (schema.get("thresholds") or {}).items()
        }

        self._tool_label.setText(f"{tool.name} ({tool.type})")
        description = getattr(meta, "description", "") or ""
        self._description_label.setText(description)
        self._description_label.setVisible(bool(description))

        self._rebuild_form()
        self._update_visibility()

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
                tooltip = spec.get("description") or ""
                if tooltip:
                    widget.setToolTip(tooltip)
                label = QLabel(spec.get("label", name), self)
                if tooltip:
                    label.setToolTip(tooltip)
                self._set_widget_value(widget, spec, params.get(name))
                self._connect_widget(widget, spec, kind="param", name=name)
                self._param_widgets[name] = widget
                container, error_label = self._create_field_container(widget)
                self._param_wrappers[name] = container
                self._param_error_labels[name] = error_label
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
                tooltip = spec.get("description") or ""
                if tooltip:
                    widget.setToolTip(tooltip)
                label = QLabel(spec.get("label", name), self)
                if tooltip:
                    label.setToolTip(tooltip)
                self._set_widget_value(widget, spec, thresholds.get(name))
                self._connect_widget(widget, spec, kind="threshold", name=name)
                self._threshold_widgets[name] = widget
                container, error_label = self._create_field_container(widget)
                self._threshold_wrappers[name] = container
                self._threshold_error_labels[name] = error_label
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
        return field_type in {"int", "float", "bool", "enum"}

    def _create_widget(self, spec: dict[str, Any]) -> Optional[QWidget]:
        field_type = spec.get("type")
        if field_type == "bool":
            widget = QCheckBox(self)
            widget.setTristate(False)
            widget.setChecked(bool(spec.get("default", False)))
            return widget
        if field_type == "enum":
            combo = QComboBox(self)
            for value, label in spec.get("choices", []):
                combo.addItem(str(label), value)
            return combo if combo.count() else None
        if field_type == "int":
            spin = QSpinBox(self)
            if (min_val := spec.get("min")) is not None:
                spin.setMinimum(int(min_val))
            if (max_val := spec.get("max")) is not None:
                spin.setMaximum(int(max_val))
            if (step := spec.get("step")) is not None:
                spin.setSingleStep(max(1, int(step)))
            spin.setValue(int(spec.get("default", 0) or 0))
            return spin
        if field_type == "float":
            spin = QDoubleSpinBox(self)
            min_val = spec.get("min")
            max_val = spec.get("max")
            if min_val is not None:
                spin.setMinimum(float(min_val))
            if max_val is not None:
                spin.setMaximum(float(max_val))
            decimals = int(spec.get("decimals", 4))
            spin.setDecimals(max(0, decimals))
            if (step := spec.get("step")) is not None:
                spin.setSingleStep(float(step))
            spin.setValue(float(spec.get("default", 0.0) or 0.0))
            return spin
        return None

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
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value if value is not None else spec.get("default", False)))
                return
            if isinstance(widget, QComboBox):
                target = value
                if target is None:
                    target = spec.get("default")
                idx = widget.findData(target)
                if idx < 0 and widget.count():
                    idx = 0
                if idx >= 0:
                    widget.setCurrentIndex(idx)
                return
            if isinstance(widget, QSpinBox):
                try:
                    widget.setValue(int(round(float(value))))
                except Exception:
                    default = spec.get("default", widget.value())
                    widget.setValue(int(round(float(default))))
                return
            if isinstance(widget, QDoubleSpinBox):
                try:
                    widget.setValue(float(value))
                except Exception:
                    default = spec.get("default", widget.value())
                    widget.setValue(float(default))
        finally:
            self._updating = False

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
        try:
            ok, errors, normalized = validate_tool_params(
                self._current_tool.type, params, thresholds
            )
        except KeyError:
            ok = True
            errors = {"params": {}, "thresholds": {}}
            normalized = {"params": params, "thresholds": thresholds}

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
        self._btn_defaults.setEnabled(has_tool and bool(self._param_widgets or self._threshold_widgets))

class GoldenWizard(QDialog):
    """
    Jediné miesto na nastavenie nástroja:
      1) Získať/načítať GOLDEN (1 ks)
      2) Nakresliť oblasti (Blue pose×1, Green ROI×1, Magenta ignore≤5)
      3) Zbierať validáciu (OK/NOK)
      4) Uložiť recept (golden.png + regions.json)
      5) Live feed (ON/OFF) – samostatný náhľad (bez kreslenia)
    """
    def __init__(self, camera, recipes: RecipeService, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Golden WIZARD")
        self.setModal(True)
        self.cam = camera
        self.recipes = recipes
        self.current_img = None

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
        self.shape_sel   = QComboBox(self); self.shape_sel.addItems(["rect","circle","poly"])
        self.type_sel    = QComboBox(self); self.type_sel.addItems(["pose","roi","ignore"])
        self.chk_pose    = QCheckBox("Použiť globálne zarovnanie (pose alignment)")
        self.chk_pose.setChecked(getattr(self.recipes.tool, "pose_enabled", True))

        self.btn_add_tool = QPushButton("Add tool")
        self.btn_add_tool.clicked.connect(self._open_tool_catalog)

        # Toggle Live
        self.btn_live = QPushButton("Live OFF")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._toggle_live)

        top = QHBoxLayout()
        top.addWidget(QLabel("Recept:")); top.addWidget(self.recipe_name)
        top.addWidget(QLabel("Tvar:"));   top.addWidget(self.shape_sel)
        top.addWidget(QLabel("Typ:"));    top.addWidget(self.type_sel)
        top.addStretch(1)
        top.addWidget(self.chk_pose)
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
        self.view.set_shape_type(self.shape_sel.currentText())
        self.view.set_region_type(self.type_sel.currentText())

        # ---- Ovládacie tlačidlá ----
        btn_cap_golden   = QPushButton("Získať GOLDEN z kamery")
        btn_load_golden  = QPushButton("Načítať GOLDEN z disku")
        btn_save_recipe  = QPushButton("Uložiť RECEPT")
        btn_val_ok       = QPushButton("Validačný zber: uložiť Ⓞ OK")
        btn_val_nok      = QPushButton("Validačný zber: uložiť ✕ NOK")

        buttons = QHBoxLayout()
        buttons.addWidget(btn_cap_golden)
        buttons.addWidget(btn_load_golden)
        buttons.addStretch(1)
        buttons.addWidget(btn_val_ok)
        buttons.addWidget(btn_val_nok)
        buttons.addWidget(btn_save_recipe)

        # ---- Layout ----
        self._tool_panel = ToolConfigPanel(self)
        self._tool_panel.setMinimumWidth(280)
        self._tool_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        self.tools_table = QTableWidget(0, 5, self)
        self.tools_table.setHorizontalHeaderLabels(["Order", "Name", "Type", "Enabled", "Actions"])
        header = self.tools_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self.tools_table.verticalHeader().setVisible(False)
        self.tools_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tools_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tools_table.setSelectionMode(QAbstractItemView.SingleSelection)
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
        left_layout.addLayout(buttons)

        content_layout.addLayout(left_layout, 3)
        content_layout.addWidget(self._tool_panel, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(content_layout, 1)

        self._tool_panel.clear()

        # signály
        self.shape_sel.currentTextChanged.connect(self.view.set_shape_type)
        self.type_sel.currentTextChanged.connect(self.view.set_region_type)
        btn_cap_golden.clicked.connect(self._capture_golden)
        btn_load_golden.clicked.connect(self._load_golden)
        btn_save_recipe.clicked.connect(self._save_recipe)
        btn_val_ok.clicked.connect(lambda: self._save_validation(True))
        btn_val_nok.clicked.connect(lambda: self._save_validation(False))
        self.recipe_name.editingFinished.connect(self._on_recipe_changed)
        self.tools_table.itemSelectionChanged.connect(self._on_tool_selection_changed)
        self._tool_panel.paramChanged.connect(self._on_tool_param_changed)
        self._tool_panel.thresholdChanged.connect(self._on_tool_threshold_changed)

        self._selected_tool_row = -1

        self._last_recipe = self._current_recipe_name()
        try:
            self.recipes.load_tools(self._last_recipe)
        except Exception as exc:
            print(f"[GoldenWizard] load_tools failed for {self._last_recipe}: {exc}")
        self._refresh_tools_table()
        self._on_tool_selection_changed()

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
                # Deaktivuj meniče tvar/typ počas live (čisto vizuálne)
                self.shape_sel.setEnabled(False)
                self.type_sel.setEnabled(False)
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
            self.shape_sel.setEnabled(True)
            self.type_sel.setEnabled(True)

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
        from PySide6.QtWidgets import QFileDialog
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

    def _save_recipe(self):
        if self.current_img is None:
            self._err("Najprv zachyť alebo načítaj GOLDEN.")
            return
        regs = self.view.export_regions()
        pose_enabled = self.chk_pose.isChecked()
        ok, msg = validate_cardinality([Region(**r) for r in regs], pose_required=pose_enabled)
        if not ok:
            self._err(msg); return

        name = self.recipe_name.text().strip() or "default"
        # ulož golden
        golden_path = save_golden(self.current_img, name)
        # ulož regions.json
        recipe_dir = Path("/data") / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        recipe_data = RecipeData(pose_enabled=pose_enabled, regions=regs)
        self.recipes.save_regions(name, recipe_data)

        ok, autosorted = self._persist_tools(name)
        if not ok:
            return

        message = f"Recept uložený:\n{golden_path}\n{recipe_dir/'regions.json'}"
        if autosorted:
            message += "\nPoradie nástrojov bolo automaticky upravené: Locator nástroje boli presunuté na začiatok."
        self._info(message)

    def _save_validation(self, is_ok: bool):
        if self.current_img is None:
            try:
                self.current_img = (self._lp.last_frame_u8() if self._live_on else None) or self.cam.one_shot()
            except Exception as e:
                self._err(f"Zachytenie zlyhalo: {e}")
                return
        name = self.recipe_name.text().strip() or "default"
        out = save_validation_image(self.current_img, ok=is_ok, recipe_name=name)
        self._info(f"Validačný snímok uložený:\n{out['thumb']}\n{out['full']}")

    # ---------- Info/Err ----------
    def _info(self, msg):
        QMessageBox.information(self, "Info", msg)

    def _err(self, msg):
        QMessageBox.critical(self, "Chyba", msg)

    # ---------- Tools management ----------
    def _current_recipe_name(self) -> str:
        return self.recipe_name.text().strip() or "default"

    def _on_recipe_changed(self):
        recipe = self._current_recipe_name()
        if recipe == getattr(self, "_last_recipe", None):
            return
        try:
            self.recipes.load_tools(recipe)
        except Exception as exc:
            print(f"[GoldenWizard] load_tools failed for {recipe}: {exc}")
        self._last_recipe = recipe
        self._refresh_tools_table()

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
            name_item = QTableWidgetItem(tool.name)
            type_item = QTableWidgetItem(tool.type)
            enabled_item = QTableWidgetItem("Yes" if tool.enabled else "No")
            enabled_item.setTextAlignment(Qt.AlignCenter)
            order_item.setTextAlignment(Qt.AlignCenter)
            is_locator = tool.type.startswith("locator.")
            if is_locator:
                highlight = QColor("#fff2cc")
                type_item.setBackground(highlight)
                order_item.setBackground(highlight)
                type_item.setText(f"{tool.type}  (Locator)")
                type_item.setToolTip("Locator nástroje vždy bežia pred analyzátormi.")
            else:
                type_item.setToolTip("Analyzátory bežia po locator nástrojoch.")
            self.tools_table.setItem(row, 0, order_item)
            self.tools_table.setItem(row, 1, name_item)
            self.tools_table.setItem(row, 2, type_item)
            self.tools_table.setItem(row, 3, enabled_item)

            actions_widget = QWidget(self.tools_table)
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setSpacing(4)

            btn_up = QPushButton("Up", actions_widget)
            btn_up.clicked.connect(lambda _, idx=row: self._move_tool(idx, -1))
            btn_down = QPushButton("Down", actions_widget)
            btn_down.clicked.connect(lambda _, idx=row: self._move_tool(idx, 1))
            btn_edit = QPushButton("Edit", actions_widget)
            btn_edit.clicked.connect(lambda _, idx=row: self._edit_tool(idx))
            btn_del = QPushButton("Delete", actions_widget)
            btn_del.clicked.connect(lambda _, idx=row: self._delete_tool(idx))

            actions_layout.addWidget(btn_up)
            actions_layout.addWidget(btn_down)
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

    def _move_tool(self, index: int, delta: int):
        recipe = self._current_recipe_name()
        tools = self.recipes.get_draft_tools(recipe)
        target = index + delta
        if target < 0 or target >= len(tools):
            return
        order = list(range(len(tools)))
        order[index], order[target] = order[target], order[index]
        try:
            self.recipes.reorder_tools(recipe, order)
        except Exception as exc:
            self._err(f"Zmena poradia zlyhala: {exc}")
            return
        self._refresh_tools_table()

    def _delete_tool(self, index: int):
        recipe = self._current_recipe_name()
        self.recipes.remove_tool(recipe, index)
        self._refresh_tools_table()

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
        try:
            self._live_timer.stop()
            self._lp.stop()
        except Exception:
            pass
        e.accept()

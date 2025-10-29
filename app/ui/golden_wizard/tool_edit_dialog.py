"""Dialog for editing tool configuration within the Golden Wizard."""
from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.schema import Tool, ToolMask, ToolParams, ToolRoi, ToolThresholds
from app.services.live_preview_service import LivePreviewService
from app.services.tool_registry import ToolRegistry
from app.services.tool_service import ToolRunResult, run_locator_template_match
from app.ui.roi_mask_editor import MaskEditor, ROIEditor

from .form_widgets import (
    _SUPPORTED_FORM_FIELD_TYPES,
    _create_form_widget,
    _format_spec_tooltip,
    _get_form_widget_value,
    _set_form_widget_value,
)
from app.services.golden_wizard_logic import _validate_params_and_thresholds

_ROI_MASK_SECTION_MIN_WIDTH = 360
_ROI_MASK_SECTION_MIN_HEIGHT = 280
_LOCATOR_PREVIEW_MIN_HEIGHT = 280


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

        if self._roi_editor is not None:
            self._roi_editor.setMinimumSize(
                _ROI_MASK_SECTION_MIN_WIDTH, _ROI_MASK_SECTION_MIN_HEIGHT
            )
        if self._mask_editor is not None:
            self._mask_editor.setMinimumSize(
                _ROI_MASK_SECTION_MIN_WIDTH, _ROI_MASK_SECTION_MIN_HEIGHT
            )

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

        sections_layout = QHBoxLayout()
        sections_layout.setContentsMargins(0, 0, 0, 0)
        sections_layout.setSpacing(12)

        has_section = False

        if self._supports_roi and self._roi_editor is not None:
            roi_group = QGroupBox("Region of interest", roi_tab)
            roi_group_layout = QVBoxLayout(roi_group)
            roi_group_layout.setContentsMargins(6, 6, 6, 6)
            roi_group_layout.setSpacing(6)
            roi_group_layout.addWidget(self._roi_editor, 1)
            sections_layout.addWidget(roi_group, 1)
            has_section = True
        else:
            roi_group = None

        if self._supports_mask and self._mask_editor is not None:
            mask_group = QGroupBox("Ignore mask", roi_tab)
            mask_group_layout = QVBoxLayout(mask_group)
            mask_group_layout.setContentsMargins(6, 6, 6, 6)
            mask_group_layout.setSpacing(6)
            mask_group_layout.addWidget(self._mask_editor, 1)
            sections_layout.addWidget(mask_group, 1)
            has_section = True

        if has_section:
            roi_layout.addLayout(sections_layout, 1)
        else:
            info = QLabel("Selected tool does not support ROI or ignore mask editing.", roi_tab)
            info.setStyleSheet("color: #666;")
            info.setWordWrap(True)
            roi_layout.addWidget(info)

        self._roi_layout = roi_layout
        self._roi_sections_layout = sections_layout if has_section else None
        self._roi_group = roi_group

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
        if getattr(self, "_roi_sections_layout", None) is None:
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
        self._template_editor.setMinimumHeight(_LOCATOR_PREVIEW_MIN_HEIGHT)
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
        before_label.setMinimumSize(200, _LOCATOR_PREVIEW_MIN_HEIGHT)
        before_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        before_label.setStyleSheet("background-color: #111; color: #777; border: 1px solid #444;")

        after_title = QLabel("Aligned preview", panel)
        after_title.setAlignment(Qt.AlignCenter)
        after_label = QLabel("No preview", panel)
        after_label.setAlignment(Qt.AlignCenter)
        after_label.setMinimumSize(200, _LOCATOR_PREVIEW_MIN_HEIGHT)
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

        self._roi_sections_layout.addWidget(panel, 1)
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



__all__ = ['TemplateRoiEditor', 'ToolEditDialog']

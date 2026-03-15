"""Dialog for editing tool configuration within the Golden Wizard."""
from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
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
    QLineEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
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

        self._btn_reset = QPushButton("Obnoviť ROI šablóny", self)
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


class AngleRoiEditor(QWidget):
    """ROI editor dedicated to angle estimation."""

    roiChanged = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._editor = ROIEditor(self, show_toolbar=False)
        self._editor.roiChanged.connect(self.roiChanged)

        self._btn_reset = QPushButton("Obnoviť ROI uhla", self)
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


class EdgeAnchorEditor(QWidget):
    """Simple point editor for selecting A-B anchors directly on golden image."""

    pointsChanged = Signal(object, object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._point_a: Optional[tuple[float, float]] = None
        self._point_b: Optional[tuple[float, float]] = None
        self._next_target: str = "a"
        self._roi_rect: Optional[tuple[int, int, int, int]] = None
        self._detected_line: Optional[tuple[tuple[float, float], tuple[float, float]]] = None
        self.setMinimumHeight(220)
        self.setMouseTracking(True)

    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._pixmap = pixmap
        self.update()

    def set_roi_rect(self, roi_rect: Optional[tuple[int, int, int, int]]) -> None:
        self._roi_rect = roi_rect
        self.update()

    def set_detected_line(
        self,
        line: Optional[tuple[tuple[float, float], tuple[float, float]]],
    ) -> None:
        self._detected_line = line
        self.update()

    def clear_points(self) -> None:
        self._point_a = None
        self._point_b = None
        self._next_target = "a"
        self.pointsChanged.emit(self._point_a, self._point_b)
        self.update()

    def set_points(
        self,
        point_a: Optional[tuple[float, float]],
        point_b: Optional[tuple[float, float]],
    ) -> None:
        self._point_a = point_a
        self._point_b = point_b
        self._next_target = "a" if point_a is None else ("b" if point_b is None else "a")
        self.pointsChanged.emit(self._point_a, self._point_b)
        self.update()

    def points(self) -> tuple[Optional[tuple[float, float]], Optional[tuple[float, float]]]:
        return self._point_a, self._point_b

    def _image_rect(self) -> Optional[QRectF]:
        if self._pixmap is None or self._pixmap.isNull():
            return None
        pw = float(self._pixmap.width())
        ph = float(self._pixmap.height())
        ww = float(max(1, self.width()))
        wh = float(max(1, self.height()))
        scale = min(ww / pw, wh / ph)
        rw = pw * scale
        rh = ph * scale
        ox = (ww - rw) * 0.5
        oy = (wh - rh) * 0.5
        return QRectF(ox, oy, rw, rh)

    def _widget_from_image(self, point: tuple[float, float], rect: QRectF) -> QPointF:
        assert self._pixmap is not None
        x = rect.left() + (float(point[0]) / max(1.0, float(self._pixmap.width()))) * rect.width()
        y = rect.top() + (float(point[1]) / max(1.0, float(self._pixmap.height()))) * rect.height()
        return QPointF(x, y)

    def _image_from_widget(self, pos: QPointF, rect: QRectF) -> Optional[tuple[float, float]]:
        if self._pixmap is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        if not rect.contains(pos):
            return None
        xn = (pos.x() - rect.left()) / rect.width()
        yn = (pos.y() - rect.top()) / rect.height()
        x = float(np.clip(xn * self._pixmap.width(), 0.0, max(0.0, self._pixmap.width() - 1.0)))
        y = float(np.clip(yn * self._pixmap.height(), 0.0, max(0.0, self._pixmap.height() - 1.0)))
        return x, y

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        rect = self._image_rect()
        if rect is None:
            return
        point = self._image_from_widget(event.position(), rect)
        if point is None:
            return
        if self._next_target == "a":
            self._point_a = point
            self._next_target = "b"
        else:
            self._point_b = point
            self._next_target = "a"
        self.pointsChanged.emit(self._point_a, self._point_b)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 20))

        rect = self._image_rect()
        if self._pixmap is None or self._pixmap.isNull() or rect is None:
            painter.setPen(QColor(180, 180, 180))
            painter.drawText(self.rect(), Qt.AlignCenter, "Golden image not available")
            return

        painter.drawPixmap(rect.toRect(), self._pixmap)
        painter.setPen(QPen(QColor(110, 110, 110), 1, Qt.DashLine))
        painter.drawRect(rect)

        if self._roi_rect is not None:
            rx, ry, rw, rh = self._roi_rect
            roi_tl = self._widget_from_image((float(rx), float(ry)), rect)
            roi_br = self._widget_from_image((float(rx + rw), float(ry + rh)), rect)
            roi_rect = QRectF(roi_tl, roi_br).normalized()
            painter.setPen(QPen(QColor(80, 230, 120), 2, Qt.DashLine))
            painter.drawRect(roi_rect)

        if self._detected_line is not None:
            p1_img, p2_img = self._detected_line
            p1 = self._widget_from_image(p1_img, rect)
            p2 = self._widget_from_image(p2_img, rect)
            painter.setPen(QPen(QColor(255, 235, 59), 2))
            painter.drawLine(p1, p2)

        def _draw_point(pt: tuple[float, float], label: str, color: QColor) -> QPointF:
            wp = self._widget_from_image(pt, rect)
            painter.setPen(QPen(color, 2))
            painter.setBrush(color)
            painter.drawEllipse(wp, 5.0, 5.0)
            painter.setPen(QPen(color, 1))
            painter.drawText(wp + QPointF(8.0, -8.0), label)
            return wp

        wp_a: Optional[QPointF] = None
        wp_b: Optional[QPointF] = None
        if self._point_a is not None:
            wp_a = _draw_point(self._point_a, "A", QColor(255, 110, 110))
        if self._point_b is not None:
            wp_b = _draw_point(self._point_b, "B", QColor(110, 170, 255))
        if wp_a is not None and wp_b is not None:
            painter.setPen(QPen(QColor(255, 210, 110), 2))
            painter.drawLine(wp_a, wp_b)



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

        self._tool = tool.copy()
        self.setWindowTitle(self._format_window_title(self._tool.name))
        self._meta = meta
        self._meta_caps = getattr(meta, "meta", meta)
        self._camera_service = camera_service
        self._live_preview = live_preview
        self._golden_image: Optional[np.ndarray] = None if golden_image is None else np.asarray(golden_image).copy()
        self._golden_pixmap: Optional[QPixmap] = None
        self._locator_template_specs: dict[str, dict[str, Any]] = {}
        self._locator_angle_specs: dict[str, dict[str, Any]] = {}
        self._angle_field_rows: dict[str, tuple[QLabel, QWidget]] = {}
        self._edge_anchor_editor: Optional[EdgeAnchorEditor] = None
        self._edge_anchor_status: Optional[QLabel] = None
        self._btn_edge_auto_detect: Optional[QPushButton] = None

        self._is_locator_template = self._tool.type == "locator.template_match"
        self._maximize_on_first_show = True
        self._use_golden_checkbox: Optional[QCheckBox] = None
        self._template_editor: Optional[TemplateRoiEditor] = None
        self._template_container: Optional[QWidget] = None
        self._locator_panel: Optional[QWidget] = None
        self._locator_metrics_label: Optional[QLabel] = None
        self._locator_message_label: Optional[QLabel] = None
        self._locator_preview_before: Optional[QLabel] = None
        self._locator_preview_after: Optional[QLabel] = None
        self._btn_locator_evaluate: Optional[QPushButton] = None
        self._angle_mode_combo: Optional[QComboBox] = None
        self._angle_roi_editor: Optional[AngleRoiEditor] = None
        self._angle_roi_container: Optional[QWidget] = None

        self._supports_roi = bool(getattr(self._meta_caps, "supports_roi", True))
        self._supports_mask = bool(getattr(self._meta_caps, "supports_ignore_mask", True))

        self._roi_editor: Optional[ROIEditor] = ROIEditor(self) if self._supports_roi else None
        self._mask_editor: Optional[MaskEditor] = MaskEditor(self) if self._supports_mask else None

        if self._roi_editor is not None and self._mask_editor is not None:
            self._roi_editor.roiChanged.connect(self._mask_editor.set_roi_overlay)

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

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        name_label = QLabel("Názov nástroja:", self)
        self._name_input = QLineEdit(self)
        self._name_input.setPlaceholderText("napr. Kontrola kvality")
        self._name_input.setText(self._tool.name or "")
        self._name_input.textChanged.connect(self._on_name_changed)
        name_row.addWidget(name_label)
        name_row.addWidget(self._name_input, 1)
        layout.addLayout(name_row)

        self._header_label = QLabel(self._format_header_text(self._tool.name), self)
        self._header_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self._header_label)

        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)
        self._roi_tab_index: Optional[int] = None
        self._mask_tab_index: Optional[int] = None
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

        roi_tab = QWidget(self)
        roi_layout = QVBoxLayout(roi_tab)
        roi_layout.setContentsMargins(0, 0, 0, 0)
        roi_layout.setSpacing(8)

        roi_group: Optional[QGroupBox] = None
        sections_layout: Optional[QHBoxLayout] = None

        if self._is_locator_template:
            sections_layout = QHBoxLayout()
            sections_layout.setContentsMargins(0, 0, 0, 0)
            sections_layout.setSpacing(12)
            if self._supports_roi and self._roi_editor is not None:
                roi_group = QGroupBox("Region of interest", roi_tab)
                roi_group_layout = QVBoxLayout(roi_group)
                roi_group_layout.setContentsMargins(6, 6, 6, 6)
                roi_group_layout.setSpacing(6)
                roi_group_layout.addWidget(self._roi_editor, 1)
                sections_layout.addWidget(roi_group, 2)
            else:
                info = QLabel("Selected tool does not support ROI editing.", roi_tab)
                info.setStyleSheet("color: #666;")
                info.setWordWrap(True)
                roi_layout.addWidget(info)
            roi_layout.addLayout(sections_layout, 1)
            self._tabs.addTab(roi_tab, "ROI")
            self._roi_tab_index = self._tabs.indexOf(roi_tab)

            if self._supports_mask and self._mask_editor is not None:
                mask_tab = QWidget(self)
                mask_layout = QVBoxLayout(mask_tab)
                mask_layout.setContentsMargins(0, 0, 0, 0)
                mask_layout.setSpacing(8)
                mask_group = QGroupBox("Ignore mask", mask_tab)
                mask_group_layout = QVBoxLayout(mask_group)
                mask_group_layout.setContentsMargins(6, 6, 6, 6)
                mask_group_layout.setSpacing(6)
                mask_group_layout.addWidget(self._mask_editor, 1)
                mask_layout.addWidget(mask_group, 1)
                self._tabs.addTab(mask_tab, "Ignore Mask")
                self._mask_tab_index = self._tabs.indexOf(mask_tab)
        else:
            if self._supports_roi and self._roi_editor is not None:
                roi_group = QGroupBox("Region of interest", roi_tab)
                roi_group_layout = QVBoxLayout(roi_group)
                roi_group_layout.setContentsMargins(6, 6, 6, 6)
                roi_group_layout.setSpacing(6)
                roi_group_layout.addWidget(self._roi_editor, 1)
                roi_layout.addWidget(roi_group, 1)
            else:
                info = QLabel("Selected tool does not support ROI editing.", roi_tab)
                info.setStyleSheet("color: #666;")
                info.setWordWrap(True)
                roi_layout.addWidget(info)
            self._tabs.addTab(roi_tab, "ROI")
            self._roi_tab_index = self._tabs.indexOf(roi_tab)

            mask_tab = QWidget(self)
            mask_layout = QVBoxLayout(mask_tab)
            mask_layout.setContentsMargins(0, 0, 0, 0)
            mask_layout.setSpacing(8)
            if self._supports_mask and self._mask_editor is not None:
                mask_group = QGroupBox("Ignore mask", mask_tab)
                mask_group_layout = QVBoxLayout(mask_group)
                mask_group_layout.setContentsMargins(6, 6, 6, 6)
                mask_group_layout.setSpacing(6)
                mask_group_layout.addWidget(self._mask_editor, 1)
                mask_layout.addWidget(mask_group, 1)
            else:
                info = QLabel("Selected tool does not support ignore mask editing.", mask_tab)
                info.setStyleSheet("color: #666;")
                info.setWordWrap(True)
                mask_layout.addWidget(info)
            self._tabs.addTab(mask_tab, "Ignore Mask")
            self._mask_tab_index = self._tabs.indexOf(mask_tab)

        self._roi_layout = roi_layout
        self._roi_sections_layout = sections_layout
        self._roi_group = roi_group

        self._info_label = QLabel("", self)
        self._info_label.setStyleSheet("color: #666;")
        self._info_label.setWordWrap(True)
        roi_layout.addWidget(self._info_label)

        self._config_tab = QWidget(self)
        config_layout = QVBoxLayout(self._config_tab)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(0)

        form_scroll = QScrollArea(self._config_tab)
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QScrollArea.NoFrame)

        form_container = QWidget(form_scroll)
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(0)

        self._config_form = QFormLayout()
        self._config_form.setContentsMargins(8, 8, 8, 8)
        self._config_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form_layout.addLayout(self._config_form)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(8, 4, 8, 0)
        controls_layout.setSpacing(6)
        controls_layout.addStretch(1)
        self._btn_restore_defaults = QPushButton("Obnoviť predvolené", self._config_tab)
        self._btn_restore_defaults.clicked.connect(self._on_restore_defaults_clicked)
        self._btn_restore_defaults.setEnabled(False)
        controls_layout.addWidget(self._btn_restore_defaults)
        form_layout.addLayout(controls_layout)

        self._form_error_label = QLabel("", self._config_tab)
        self._form_error_label.setStyleSheet("color: #b03030; padding: 4px 8px 0 8px;")
        self._form_error_label.setWordWrap(True)
        self._form_error_label.setVisible(False)
        form_layout.addWidget(self._form_error_label)
        form_layout.addStretch(1)
        form_scroll.setWidget(form_container)
        config_layout.addWidget(form_scroll, 1)
        self._tabs.addTab(self._config_tab, "Prahy a parametre")

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

        if self._mask_editor is not None:
            self._mask_editor.set_roi_overlay(initial_roi)

        if self._golden_pixmap is not None:
            if self._roi_editor is not None:
                self._roi_editor.set_background(self._golden_pixmap)
                if initial_roi is not None:
                    self._roi_editor.set_roi(initial_roi)
            if self._mask_editor is not None:
                self._mask_editor.set_background(self._golden_pixmap)
                if initial_mask is not None:
                    self._mask_editor.set_mask(initial_mask)
            self._schedule_active_tab_fit(source="initial_load")
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
        if self._tool.type == "edge_profile_deviation":
            self._init_edge_anchor_panel()

        self.resize(1400, 900)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self._maximize_on_first_show:
            self._maximize_on_first_show = False
            self.showMaximized()
        self._schedule_active_tab_fit(source="showEvent")

    def _schedule_active_tab_fit(self, *, source: str) -> None:
        current = self._tabs.currentIndex()
        if self._roi_tab_index is not None and current == self._roi_tab_index and self._roi_editor is not None:
            print("[FIT_TO_VIEW] tab activated roi")
            self._roi_editor.schedule_fit_to_view(source=f"tab_roi:{source}")
            return
        if self._mask_tab_index is not None and current == self._mask_tab_index and self._mask_editor is not None:
            print("[FIT_TO_VIEW] tab activated ignore_mask")
            self._mask_editor.schedule_fit_to_view(source=f"tab_ignore_mask:{source}")

    def _on_tab_changed(self, _index: int) -> None:
        self._schedule_active_tab_fit(source="currentChanged")

    def _format_window_title(self, name: Optional[str]) -> str:
        tool_obj = getattr(self, "_tool", None)
        tool_type = getattr(tool_obj, "type", "") if tool_obj is not None else ""
        display_name = (name or "").strip() or tool_type or "Tool"
        return f"Edit Tool – {display_name}"

    def _format_header_text(self, name: Optional[str]) -> str:
        tool_type = getattr(self._tool, "type", "")
        display_name = (name or "").strip()
        if not display_name:
            display_name = tool_type or "Tool"
        if tool_type and display_name != tool_type:
            return f"{display_name} ({tool_type})"
        return display_name

    def _on_name_changed(self, text: str) -> None:
        self._header_label.setText(self._format_header_text(text))
        self.setWindowTitle(self._format_window_title(text))

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

        self._btn_locator_evaluate = QPushButton("Vyhodnotiť", panel)
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
        before_label = QLabel("Náhľad nie je dostupný", panel)
        before_label.setAlignment(Qt.AlignCenter)
        before_label.setMinimumSize(200, _LOCATOR_PREVIEW_MIN_HEIGHT)
        before_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        before_label.setStyleSheet("background-color: #111; color: #777; border: 1px solid #444;")

        after_title = QLabel("Aligned preview", panel)
        after_title.setAlignment(Qt.AlignCenter)
        after_label = QLabel("Náhľad nie je dostupný", panel)
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

    def _parse_point(self, value: Any) -> Optional[tuple[float, float]]:
        if value is None:
            return None
        if isinstance(value, dict) and "x" in value and "y" in value:
            try:
                return float(value["x"]), float(value["y"])
            except Exception:
                return None
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            try:
                return float(value[0]), float(value[1])
            except Exception:
                return None
        return None

    @staticmethod
    def _point_to_dict(point: Optional[tuple[float, float]]) -> Optional[dict[str, float]]:
        if point is None:
            return None
        return {"x": float(point[0]), "y": float(point[1])}

    def _update_edge_anchor_status(
        self,
        point_a: Optional[tuple[float, float]],
        point_b: Optional[tuple[float, float]],
    ) -> None:
        if self._edge_anchor_status is None:
            return
        if point_a is None and point_b is None:
            self._edge_anchor_status.setText("Klikni do obrázka: najprv bod A, potom bod B.")
            return
        if point_a is not None and point_b is None:
            self._edge_anchor_status.setText(
                f"Bod A = ({point_a[0]:.1f}, {point_a[1]:.1f}). Klikni pre bod B."
            )
            return
        if point_a is None and point_b is not None:
            self._edge_anchor_status.setText(
                f"Bod B = ({point_b[0]:.1f}, {point_b[1]:.1f}). Klikni pre bod A."
            )
            return
        self._edge_anchor_status.setText(
            f"A=({point_a[0]:.1f}, {point_a[1]:.1f}), B=({point_b[0]:.1f}, {point_b[1]:.1f})"
        )

    def _on_edge_anchor_points_changed(self, point_a: object, point_b: object) -> None:
        pa = point_a if isinstance(point_a, tuple) else None
        pb = point_b if isinstance(point_b, tuple) else None
        self._edge_anchor_status.setStyleSheet("color: #444;") if self._edge_anchor_status is not None else None
        self._update_edge_anchor_status(pa, pb)

    def _init_edge_anchor_panel(self) -> None:
        if getattr(self, "_roi_sections_layout", None) is None:
            return
        group = QGroupBox("Anchor points A-B", self)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(8, 8, 8, 8)
        group_layout.setSpacing(6)

        hint = QLabel("Vyber body A a B kliknutím do golden snímky. Body sa ukladajú do parametrov point_a/point_b.", group)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        group_layout.addWidget(hint)

        self._edge_anchor_editor = EdgeAnchorEditor(group)
        if self._golden_pixmap is not None:
            self._edge_anchor_editor.set_background(self._golden_pixmap)
        params_values = dict(getattr(self._tool.params, "values", {}) or {})
        pa = self._parse_point(params_values.get("point_a"))
        pb = self._parse_point(params_values.get("point_b"))
        self._edge_anchor_editor.set_points(pa, pb)
        roi_rect = self._roi_editor.roi() if self._roi_editor is not None else self._tool.roi.rect()
        self._edge_anchor_editor.set_roi_rect(roi_rect)
        self._edge_anchor_editor.pointsChanged.connect(self._on_edge_anchor_points_changed)
        if self._roi_editor is not None:
            self._roi_editor.roiChanged.connect(self._edge_anchor_editor.set_roi_rect)
        group_layout.addWidget(self._edge_anchor_editor, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        btn_clear = QPushButton("Obnoviť A-B", group)
        btn_clear.clicked.connect(self._edge_anchor_editor.clear_points)
        controls.addWidget(btn_clear)
        self._btn_edge_auto_detect = QPushButton("Automaticky nájsť hranu v ROI", group)
        self._btn_edge_auto_detect.clicked.connect(self._on_edge_auto_detect_clicked)
        controls.addWidget(self._btn_edge_auto_detect)
        controls.addStretch(1)
        group_layout.addLayout(controls)

        self._edge_anchor_status = QLabel(group)
        self._edge_anchor_status.setStyleSheet("color: #444;")
        group_layout.addWidget(self._edge_anchor_status)
        self._update_edge_anchor_status(pa, pb)

        self._roi_sections_layout.addWidget(group, 1)

    def _detect_edge_line_in_roi(self) -> tuple[bool, str]:
        if self._edge_anchor_editor is None:
            return False, "Editor anchor points nie je dostupný."
        if self._golden_image is None:
            return False, "Golden snímka nie je dostupná."

        roi_rect = self._roi_editor.roi() if self._roi_editor is not None else None
        if roi_rect is None:
            roi_rect = self._tool.roi.rect()
        if roi_rect is None:
            return False, "Najprv nastav ROI oblasť pre auto detekciu hrany."

        x, y, w, h = roi_rect
        if w <= 2 or h <= 2:
            return False, "ROI je príliš malá."

        img = self._ensure_gray_u8(self._golden_image)
        if img is None:
            return False, "Golden snímku sa nepodarilo previesť."

        ih, iw = img.shape[:2]
        x0 = max(0, min(iw - 1, int(x)))
        y0 = max(0, min(ih - 1, int(y)))
        x1 = max(0, min(iw, int(x + w)))
        y1 = max(0, min(ih, int(y + h)))
        if x1 - x0 < 3 or y1 - y0 < 3:
            return False, "ROI po orezaní mimo obraz je príliš malá."

        roi = img[y0:y1, x0:x1]
        blur = cv2.GaussianBlur(roi, (5, 5), 0)
        edges = cv2.Canny(blur, 60, 180)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180.0,
            threshold=max(20, int(min(roi.shape[:2]) * 0.25)),
            minLineLength=max(20, int(min(roi.shape[:2]) * 0.4)),
            maxLineGap=max(8, int(min(roi.shape[:2]) * 0.08)),
        )
        if lines is None or len(lines) == 0:
            return False, "V ROI sa nepodarilo nájsť hranu."

        best = None
        best_len = -1.0
        for entry in lines.reshape(-1, 4):
            lx1, ly1, lx2, ly2 = [float(v) for v in entry]
            length = float(np.hypot(lx2 - lx1, ly2 - ly1))
            if length > best_len:
                best_len = length
                best = (lx1, ly1, lx2, ly2)

        if best is None:
            return False, "Detekcia hrany zlyhala."

        lx1, ly1, lx2, ly2 = best
        point_a = (x0 + lx1, y0 + ly1)
        point_b = (x0 + lx2, y0 + ly2)

        self._edge_anchor_editor.set_points(point_a, point_b)
        self._edge_anchor_editor.set_roi_rect((x0, y0, x1 - x0, y1 - y0))
        self._edge_anchor_editor.set_detected_line((point_a, point_b))
        return True, f"Auto detekcia úspešná (dĺžka hrany: {best_len:.1f}px)."

    def _on_edge_auto_detect_clicked(self) -> None:
        ok, message = self._detect_edge_line_in_roi()
        if self._edge_anchor_status is None:
            return
        if ok:
            self._edge_anchor_status.setStyleSheet("color: #2d8a34;")
        else:
            self._edge_anchor_status.setStyleSheet("color: #a33;")
        self._edge_anchor_status.setText(message)

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

    @staticmethod
    def _angle_mode_from_params(params: dict[str, Any]) -> str:
        if bool(params.get("angle_enabled", False)):
            return "edge"
        if bool(params.get("rotation_enabled", False)):
            return "brute"
        return "off"

    @staticmethod
    def _angle_flags_from_mode(mode: str) -> tuple[bool, bool]:
        if mode == "edge":
            return True, False
        if mode == "brute":
            return False, True
        return False, False

    def _current_angle_mode(self) -> str:
        if self._angle_mode_combo is None:
            return "off"
        data = self._angle_mode_combo.currentData()
        return str(data or "off")

    def _set_angle_field_visible(self, name: str, visible: bool) -> None:
        row = self._angle_field_rows.get(name)
        if row is None:
            return
        label_widget, field_container = row
        label_widget.setVisible(visible)
        field_container.setVisible(visible)

    def _apply_angle_mode_visibility(self, mode: str) -> None:
        edge_fields = {
            "angle_roi",
            "angle_method",
            "angle_ref_deg",
            "angle_max_dev_deg",
            "angle_smooth",
        }
        brute_fields = {"angle_range_deg", "angle_step_deg"}
        show_edge = mode == "edge"
        show_brute = mode == "brute"
        for name in edge_fields:
            self._set_angle_field_visible(name, show_edge)
        for name in brute_fields:
            self._set_angle_field_visible(name, show_brute)

    def _on_angle_mode_changed(self) -> None:
        if self._updating_form:
            return
        mode = self._current_angle_mode()
        self._apply_angle_mode_visibility(mode)
        self._validate_form()

    def _add_locator_angle_controls(self, param_values: dict[str, Any]) -> int:
        if not self._is_locator_template or not self._locator_angle_specs:
            return 0

        group = QGroupBox("Angle estimation", self)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(8, 8, 8, 8)
        group_layout.setSpacing(6)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(6)
        group_layout.addLayout(form_layout)

        self._angle_mode_combo = QComboBox(group)
        self._angle_mode_combo.addItem("Off", "off")
        self._angle_mode_combo.addItem("Edge-based (fast)", "edge")
        self._angle_mode_combo.addItem("Brute-force (legacy)", "brute")
        current_mode = self._angle_mode_from_params(param_values)
        index = self._angle_mode_combo.findData(current_mode)
        if index >= 0:
            self._angle_mode_combo.setCurrentIndex(index)
        self._angle_mode_combo.currentIndexChanged.connect(self._on_angle_mode_changed)
        mode_label = QLabel("Angle estimation mode", group)
        form_layout.addRow(mode_label, self._angle_mode_combo)

        angle_roi_label = QLabel("Angle ROI", group)
        self._angle_roi_editor = AngleRoiEditor(group)
        self._angle_roi_editor.setMinimumHeight(180)
        if self._golden_pixmap is not None:
            self._angle_roi_editor.set_background(self._golden_pixmap)
        angle_roi_rect = self._rect_from_any(param_values.get("angle_roi"))
        if angle_roi_rect:
            self._angle_roi_editor.set_roi(angle_roi_rect)
        angle_roi_container = QWidget(group)
        angle_roi_layout = QVBoxLayout(angle_roi_container)
        angle_roi_layout.setContentsMargins(0, 0, 0, 0)
        angle_roi_layout.setSpacing(4)
        angle_roi_layout.addWidget(self._angle_roi_editor)
        form_layout.addRow(angle_roi_label, angle_roi_container)
        self._angle_roi_container = angle_roi_container
        self._angle_field_rows["angle_roi"] = (angle_roi_label, angle_roi_container)

        def _add_angle_field(field_name: str) -> Optional[QWidget]:
            spec = self._locator_angle_specs.get(field_name)
            if spec is None:
                return None
            widget = _create_form_widget(spec, self)
            if widget is None:
                return None
            current_value = param_values.get(field_name)
            self._set_widget_value(widget, spec, current_value)
            self._param_fields[field_name] = widget
            label_text = spec.get("label") or field_name
            label_widget = QLabel(str(label_text), group)
            container, error_label = self._create_field_container(widget)
            self._param_wrappers[field_name] = container
            self._param_error_labels[field_name] = error_label
            tooltip = _format_spec_tooltip(spec)
            if tooltip:
                widget.setToolTip(tooltip)
                label_widget.setToolTip(tooltip)
                container.setToolTip(tooltip)
            form_layout.addRow(label_widget, container)
            self._connect_field_signals(widget, spec, kind="param", name=field_name)
            self._angle_field_rows[field_name] = (label_widget, container)
            return widget

        for field_name in (
            "angle_method",
            "angle_ref_deg",
            "angle_max_dev_deg",
            "angle_smooth",
            "angle_range_deg",
            "angle_step_deg",
        ):
            _add_angle_field(field_name)

        self._config_form.addRow(group)
        self._apply_angle_mode_visibility(current_mode)
        return 1

    def _gather_params_from_widgets(self) -> dict[str, Any]:
        params = dict(getattr(self._tool.params, "values", {}) or {})
        for name, widget in self._param_fields.items():
            spec = self._param_specs.get(name, {})
            params[name] = self._get_widget_value(widget, spec)

        if self._tool.type == "edge_profile_deviation" and self._edge_anchor_editor is not None:
            point_a, point_b = self._edge_anchor_editor.points()
            params["point_a"] = self._point_to_dict(point_a)
            params["point_b"] = self._point_to_dict(point_b)

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
            mode = self._current_angle_mode()
            angle_enabled, rotation_enabled = self._angle_flags_from_mode(mode)
            params["angle_enabled"] = angle_enabled
            params["rotation_enabled"] = rotation_enabled
            if self._angle_roi_editor is not None:
                params["angle_roi"] = self._rect_to_dict(self._angle_roi_editor.roi())

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
    def _rect_from_any(value: Any) -> Optional[tuple[int, int, int, int]]:
        if value is None:
            return None
        if isinstance(value, ToolRoi):
            return value.rect()
        if isinstance(value, dict):
            try:
                return (
                    int(round(float(value.get("x", 0)))),
                    int(round(float(value.get("y", 0)))),
                    int(round(float(value.get("w", 0)))),
                    int(round(float(value.get("h", 0)))),
                )
            except Exception:
                return None
        if isinstance(value, (list, tuple)) and len(value) >= 4:
            try:
                x, y, w, h = value[:4]
                return (
                    int(round(float(x))),
                    int(round(float(y))),
                    int(round(float(w))),
                    int(round(float(h))),
                )
            except Exception:
                return None
        return None

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
                label.setText("Náhľad nie je dostupný")
                return
            pixmap = self._pixmap_from_array(image)
            if pixmap is None:
                label.setPixmap(QPixmap())
                label.setText("Náhľad nie je dostupný")
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

        self._tool.name = self._name_input.text().strip()
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
        self._locator_angle_specs.clear()
        self._angle_field_rows.clear()

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
            for key in (
                "angle_enabled",
                "angle_roi",
                "angle_method",
                "angle_ref_deg",
                "angle_max_dev_deg",
                "angle_smooth",
                "angle_range_deg",
                "angle_step_deg",
                "rotation_enabled",
            ):
                spec = self._param_specs.get(key)
                if spec is not None:
                    self._locator_angle_specs[key] = spec

        fields_added = 0

        param_values = dict(getattr(self._tool.params, "values", {}) or {})
        threshold_values = dict(getattr(self._tool.thresholds, "values", {}) or {})

        if self._is_locator_template:
            fields_added += self._add_locator_angle_controls(param_values)

        if self._param_specs:
            header = QLabel("Parametre", self)
            header.setStyleSheet("font-weight: 600; padding-top: 4px;")
            self._config_form.addRow(header)
            for name, spec in self._param_specs.items():
                if name in self._locator_angle_specs:
                    continue
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
            header = QLabel("Prahy", self)
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

        if self._tool.type == "edge_profile_deviation" and self._edge_anchor_editor is not None:
            point_a, point_b = self._edge_anchor_editor.points()
            params["point_a"] = self._point_to_dict(point_a)
            params["point_b"] = self._point_to_dict(point_b)

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
            mode = self._current_angle_mode()
            angle_enabled, rotation_enabled = self._angle_flags_from_mode(mode)
            params["angle_enabled"] = angle_enabled
            params["rotation_enabled"] = rotation_enabled
            if self._angle_roi_editor is not None:
                params["angle_roi"] = self._rect_to_dict(self._angle_roi_editor.roi())

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



__all__ = ['TemplateRoiEditor', 'AngleRoiEditor', 'ToolEditDialog']

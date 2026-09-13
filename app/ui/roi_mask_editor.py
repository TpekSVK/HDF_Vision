"""Reusable ROI and ignore mask editors with zoom/pan support."""

from __future__ import annotations
from app.ui.roi.geometry import _format_pixels, ROI_WARN_PIXELS, MAX_ROI_PIXELS, MASK_WARN_PIXELS, MAX_MASK_PIXELS
from app.ui.roi.mask_view import _MaskView
from app.ui.roi.shared_canvas import _SharedCanvasView


from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QGraphicsPathItem, QGraphicsItem, QGraphicsRectItem, QGraphicsSimpleTextItem, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QSizePolicy, QToolButton, QVBoxLayout, QWidget


from app.ui.image_canvas import CANVAS_TOOLBAR_STYLE, ImageNavigationToolbar, InteractionMode
from app.models.schema import ToolRoi


class ROIEditor(QWidget):
    """Composite widget exposing ROI editing controls."""

    roiChanged = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None, show_toolbar: bool = True) -> None:
        super().__init__(parent)
        self._view = _SharedCanvasView(self)
        self._view.historyChanged.connect(self._update_history_buttons)
        self._view.maskHistoryChanged.connect(self._update_history_buttons)
        self._view.edgeHistoryChanged.connect(self._update_history_buttons)
        self._view.roiChanged.connect(self._on_roi_changed)

        self._btn_undo = QToolButton(self)
        self._btn_undo.setText("Späť")
        self._btn_redo = QToolButton(self)
        self._btn_redo.setText("Znova")
        self._btn_reset = QToolButton(self)
        self._btn_reset.setText("Obnoviť ROI")
        self._btn_undo.clicked.connect(self._undo_active_editor)
        self._btn_redo.clicked.connect(self._redo_active_editor)
        self._btn_reset.clicked.connect(self._reset_active_editor)

        self._info_label = QLabel("ROI: —", self)
        self._info_label.setStyleSheet("color: #bbb;")
        self._hint_label = QLabel("", self)
        self._hint_label.setStyleSheet("color: #d48806; font-size: 11px;")
        self._hint_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._navigation = ImageNavigationToolbar(self._view, self)
        self._shape_buttons = self._navigation.set_draw_tools([
            ("Obdĺžnik", lambda: self._view.set_draw_shape("rect"), "Ťahaním nakresliť obdĺžnikové ROI"),
            ("Kruh", lambda: self._view.set_draw_shape("ellipse"), "Ťahaním nakresliť kruh alebo elipsu"),
            ("Polygón", lambda: self._view.set_draw_shape("polygon"), "Klikajte vrcholy; dvojklik alebo Enter dokončí polygón"),
        ])
        self._navigation.add_history_buttons([
            self._btn_undo,
            self._btn_redo,
            self._btn_reset,
        ])
        layout.addWidget(self._navigation)
        self._geometry_controls = QWidget(self)
        self._geometry_controls.setLayout(self._build_geometry_controls())
        layout.addWidget(self._geometry_controls)
        layout.addWidget(self._view, 1)

        if not show_toolbar:
            self._btn_undo.hide()
            self._btn_redo.hide()
            self._btn_reset.hide()

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._update_history_buttons()
        self._view.lockChanged.connect(self._on_lock_changed)
        self._sync_geometry_controls()

    # ------------------------------------------------------------------
    def _build_geometry_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._geometry_fields: List[QSpinBox] = []
        for label, tooltip in (("X", "Pozícia X"), ("Y", "Pozícia Y"),
                               ("W", "Šírka"), ("H", "Výška")):
            field = QSpinBox(self)
            field.setToolTip(tooltip)
            field.setAccessibleName(tooltip)
            field.setMinimumWidth(60)
            field.setMaximumWidth(100)
            # Typed values commit on Enter/focus loss; arrows commit each step.
            field.setKeyboardTracking(False)
            field.valueChanged.connect(self._apply_geometry_controls)
            row.addWidget(QLabel(label, self))
            row.addWidget(field)
            self._geometry_fields.append(field)
        self._btn_lock = QToolButton(self)
        self._btn_lock.setCheckable(True)
        self._btn_lock.toggled.connect(self._view.set_roi_locked)
        row.addWidget(self._btn_lock)
        row.addStretch(1)
        info_box = QVBoxLayout()
        info_box.setContentsMargins(0, 0, 0, 0)
        info_box.setSpacing(2)
        info_box.addWidget(self._info_label)
        info_box.addWidget(self._hint_label)
        row.addLayout(info_box)
        return row

    def _apply_geometry_controls(self, _value: int) -> None:
        self._view.edit_roi(tuple(field.value() for field in self._geometry_fields))
        # A clamped/no-op edit may not emit roiChanged; always reflect its result.
        self._sync_geometry_controls()

    def _sync_geometry_controls(self) -> None:
        bounds = self._view.scene_rect()
        width, height = int(bounds.width()), int(bounds.height())
        rect = self._view.roi()
        locked = self._view.is_roi_locked()
        available = rect is not None and width > 0 and height > 0
        numeric_editable = available and self._view._shape != "polygon"
        ranges = ((0, max(0, width - 1)), (0, max(0, height - 1)),
                  (1, max(1, width)), (1, max(1, height)))
        values = rect if rect is not None else (0, 0, 1, 1)
        for field, (minimum, maximum), value in zip(self._geometry_fields, ranges, values):
            blocked = field.blockSignals(True)
            try:
                field.setRange(minimum, maximum)
                field.setValue(value)
                field.setEnabled(numeric_editable and not locked)
                field.setToolTip("Číselná úprava geometrie nie je pre polygón dostupná."
                                 if available and not numeric_editable else field.accessibleName())
            finally:
                field.blockSignals(blocked)
        blocked = self._btn_lock.blockSignals(True)
        self._btn_lock.setChecked(locked)
        self._btn_lock.blockSignals(blocked)
        self._btn_lock.setText("ROI zamknuté" if locked else "Zamknúť ROI")
        self._btn_lock.setToolTip("Odomknúť úpravu ROI" if locked else "Zamknúť úpravu ROI v tomto editore")
        self._btn_lock.setEnabled(available)
        if self._active_edit_context() == "roi":
            self._btn_reset.setEnabled(available and not locked)
        self._navigation.mode_buttons[InteractionMode.DRAW].setEnabled(not locked)
        for button in self._shape_buttons:
            button.setEnabled(not locked)

    def _on_lock_changed(self, _locked: bool) -> None:
        self._sync_geometry_controls()

    def update_display_pixmap(self, pixmap: QPixmap) -> None:
        self._view.update_display_pixmap(pixmap)

    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._view.set_pixmap(pixmap)
        self._update_history_buttons()
        self._update_info_label()
        self._sync_geometry_controls()

    def set_roi(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self._view.set_roi(rect)
        self._update_history_buttons()
        self._update_info_label()

    def roi(self) -> Optional[Tuple[int, int, int, int]]:
        return self._view.roi()

    def set_roi_data(self, data: object) -> None:
        self._view.set_roi_data(data)
        self._update_history_buttons()
        self._update_info_label()
        self._sync_geometry_controls()

    def roi_data(self) -> dict:
        return self._view.roi_data()

    def reset_roi(self) -> None:
        self._view.reset_roi()

    def schedule_fit_to_view(self, *, source: str = "roi_editor") -> None:
        self._view.schedule_fit_to_view(source=source)

    def undo(self) -> None:
        self._view.undo()

    def redo(self) -> None:
        self._view.redo()

    # ------------------------------------------------------------------
    def _update_history_buttons(self) -> None:
        if self._active_edit_context() == "edge":
            self._btn_reset.setText("Obnoviť A-B")
            self._btn_undo.setEnabled(self._view.edge_can_undo())
            self._btn_redo.setEnabled(self._view.edge_can_redo())
            self._btn_reset.setEnabled(
                self._view._edge_point_a is not None or self._view._edge_point_b is not None
            )
        elif self._active_edit_context() == "mask":
            self._btn_reset.setText("Vymazať masku")
            self._btn_undo.setEnabled(bool(self._view._mask_undo))
            self._btn_redo.setEnabled(bool(self._view._mask_redo))
            self._btn_reset.setEnabled(
                self._view._mask is not None and bool(np.any(self._view._mask))
            )
        else:
            self._btn_reset.setText("Obnoviť ROI")
            self._btn_undo.setEnabled(self._view.can_undo())
            self._btn_redo.setEnabled(self._view.can_redo())
            self._btn_reset.setEnabled(not self._view.is_roi_locked())

    def _active_edit_context(self) -> str:
        if self._view._edge_editing:
            return "edge"
        return "mask" if self._view._mask_editing else "roi"

    def _undo_active_editor(self) -> None:
        context = self._active_edit_context()
        if context == "edge":
            self._view.edge_undo()
        elif context == "mask":
            self._view.mask_undo()
        else:
            self._view.undo()

    def _redo_active_editor(self) -> None:
        context = self._active_edit_context()
        if context == "edge":
            self._view.edge_redo()
        elif context == "mask":
            self._view.mask_redo()
        else:
            self._view.redo()

    def _reset_active_editor(self) -> None:
        context = self._active_edit_context()
        if context == "edge":
            self._view.reset_edge_anchors()
        elif context == "mask":
            self._view.clear_mask()
        else:
            self._view.reset_roi()

    def _on_roi_changed(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self._update_history_buttons()
        self._update_info_label()
        self._sync_geometry_controls()
        self.roiChanged.emit(rect)

    def _update_info_label(self) -> None:
        rect = self._view.roi()
        if rect is None:
            self._info_label.setText("ROI: —")
            self._hint_label.setVisible(False)
        else:
            x, y, w, h = rect
            area = max(0, int(w) * int(h))
            shape = (
                "Otočený obdĺžnik"
                if self._view._rotated_rect
                else {"rect": "Obdĺžnik", "ellipse": "Kruh", "polygon": "Polygón"}.get(
                    self._view._shape, "ROI"
                )
            )
            self._info_label.setText(
                f"{shape}: {w}×{h} px · {_format_pixels(area)} px @ ({x}, {y})"
            )
            if area > MAX_ROI_PIXELS:
                limit = _format_pixels(MAX_ROI_PIXELS)
                self._hint_label.setText(
                    f"⚠ ROI presahuje limit testu ({limit} px). Zmenši výber."
                )
                self._hint_label.setStyleSheet("color: #b03030; font-size: 11px;")
                self._hint_label.setVisible(True)
            elif area > ROI_WARN_PIXELS:
                self._hint_label.setText("⚠ Veľká ROI – test môže chvíľu trvať.")
                self._hint_label.setStyleSheet("color: #d48806; font-size: 11px;")
                self._hint_label.setVisible(True)
            else:
                self._hint_label.setVisible(False)


class LocatorROIEditor(ROIEditor):
    """One-scene editor for Locator search/template regions and reference edge."""

    locatorRoiChanged = Signal(str, object)
    ignoreMaskChanged = Signal(object)
    edgeAnchorsChanged = Signal(object, object)
    edgeRefineRequested = Signal()
    COLORS = {
        "search": QColor("#2F80ED"), "template": QColor("#22C55E"),
    }
    LABELS = {"search": "HĽADANIE", "template": "ŠABLÓNA"}

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._locator_mode = False
        self._active_area = "search"
        self._areas = {"search": None, "template": None}
        self._locator_syncing = False
        self._use_golden_crop = False
        self._mask_available = False
        self._edge_available = False
        self._area_items: List[QGraphicsItem] = []
        self._result_items: List[QGraphicsItem] = []
        self._locator_buttons = self._navigation.set_draw_tools([
            ("Hľadanie", lambda: self._start_area_draw("search"), "Nakresliť oblasť hľadania"),
            ("Šablóna", lambda: self._start_area_draw("template"), "Nakresliť oblasť šablóny"),
        ])
        for button in self._locator_buttons:
            button.hide()
        self._btn_roi_mode = QToolButton(self._navigation)
        self._btn_roi_mode.setText("ROI")
        self._btn_roi_mode.setCheckable(True)
        self._btn_roi_mode.setChecked(True)
        self._btn_mask_mode = QToolButton(self._navigation)
        self._btn_mask_mode.setText("Ignore mask")
        self._btn_mask_mode.setCheckable(True)
        self._btn_mask_mode.setToolTip(
            "Ignorovaná oblasť – táto časť obrazu sa pri kontrole vynechá."
        )
        self._btn_edge_mode = QToolButton(self._navigation)
        self._btn_edge_mode.setText("Hrana A-B")
        self._btn_edge_mode.setCheckable(True)
        self._btn_edge_mode.setToolTip(
            "Označiť približnú hranu bodmi A a B alebo upraviť existujúce body"
        )
        edit_group = QButtonGroup(self)
        edit_group.setExclusive(True)
        edit_group.addButton(self._btn_roi_mode)
        edit_group.addButton(self._btn_mask_mode)
        edit_group.addButton(self._btn_edge_mode)
        self._btn_roi_mode.clicked.connect(lambda: self.set_edit_context("roi"))
        self._btn_mask_mode.clicked.connect(lambda: self.set_edit_context("mask"))
        self._btn_edge_mode.clicked.connect(lambda: self.set_edit_context("edge"))
        self._btn_edge_refine = QToolButton(self._navigation)
        self._btn_edge_refine.setText("Spresniť hranu")
        self._btn_edge_refine.setToolTip(
            "Automaticky nájsť skutočnú hranu v modrom okolí čiary A-B"
        )
        self._btn_edge_refine.clicked.connect(
            lambda _checked=False: self.edgeRefineRequested.emit()
        )
        self._navigation.add_context_buttons([
            self._btn_roi_mode,
            self._btn_mask_mode,
            self._btn_edge_mode,
            self._btn_edge_refine,
        ])
        self._mask_tool_buttons: List[QToolButton] = []
        mask_tools = (
            ("Štetec", _SharedCanvasView.MASK_BRUSH),
            ("Guma", _SharedCanvasView.MASK_ERASER),
            ("Obdĺžnik", _SharedCanvasView.MASK_RECTANGLE),
            ("Kruh", _SharedCanvasView.MASK_CIRCLE),
            ("Polygón", _SharedCanvasView.MASK_POLYGON),
        )
        self._mask_tool_group = QButtonGroup(self)
        self._mask_tool_group.setExclusive(True)
        for index, (text, mode) in enumerate(mask_tools):
            button = QToolButton(self._navigation)
            button.setText(text)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _checked=False, value=mode: self._start_mask_tool(value))
            self._mask_tool_group.addButton(button)
            self._mask_tool_buttons.append(button)
        self._navigation.add_tool_buttons(self._mask_tool_buttons)

        self._mask_controls = QWidget(self)
        self._mask_controls.setStyleSheet(CANVAS_TOOLBAR_STYLE)
        mask_layout = QVBoxLayout(self._mask_controls)
        mask_layout.setContentsMargins(0, 0, 0, 0)
        mask_layout.setSpacing(4)

        settings_row = QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(4)
        settings_row.addWidget(QLabel("Veľkosť:", self._mask_controls))
        self._mask_brush_size = QSpinBox(self._mask_controls)
        self._mask_brush_size.setObjectName("canvasCompactSpin")
        self._mask_brush_size.setRange(1, 200)
        self._mask_brush_size.setValue(25)
        self._mask_brush_size.setSuffix(" px")
        self._mask_brush_size.setMaximumWidth(76)
        self._mask_brush_size.valueChanged.connect(self._view.set_mask_brush_size)
        settings_row.addWidget(self._mask_brush_size)
        settings_row.addSpacing(8)
        settings_row.addWidget(QLabel("Priehľadnosť:", self._mask_controls))
        self._mask_opacity = QSpinBox(self._mask_controls)
        self._mask_opacity.setObjectName("canvasCompactSpin")
        self._mask_opacity.setRange(10, 90)
        self._mask_opacity.setValue(40)
        self._mask_opacity.setSuffix(" %")
        self._mask_opacity.setMaximumWidth(68)
        self._mask_opacity.valueChanged.connect(self._view.set_mask_opacity)
        settings_row.addWidget(self._mask_opacity)
        settings_row.addSpacing(8)
        self._btn_mask_visible = QToolButton(self._mask_controls)
        self._btn_mask_visible.setText("Zobraziť masku")
        self._btn_mask_visible.setToolTip("Zobraziť alebo skryť Ignore Mask")
        self._btn_mask_visible.setCheckable(True)
        self._btn_mask_visible.setChecked(True)
        self._btn_mask_visible.toggled.connect(self._view.set_mask_visible)
        settings_row.addWidget(self._btn_mask_visible)
        settings_row.addStretch(1)
        mask_layout.addLayout(settings_row)
        self._mask_setting_controls = (
            self._mask_brush_size,
            self._mask_opacity,
            self._btn_mask_visible,
        )
        self.layout().insertWidget(1, self._mask_controls)
        self._mask_controls.hide()
        self._btn_roi_mode.hide()
        self._btn_mask_mode.hide()
        self._btn_edge_mode.hide()
        self._btn_edge_refine.hide()
        for button in self._mask_tool_buttons:
            button.hide()
        self._view.maskChanged.connect(self.ignoreMaskChanged)
        self._view.edgeAnchorsChanged.connect(self._on_edge_anchors_changed)
        self._view.interactionModeChanged.connect(self._sync_mask_interaction_mode)
        self.roiChanged.connect(self._active_area_changed)
        self._view.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (self._locator_mode and watched is self._view.viewport()
                and event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
                and self._view.interaction_mode() == InteractionMode.SELECT):
            point = self._view.mapToScene(event.position().toPoint())
            candidates = []
            for target in ("template", "search"):
                area = self._areas.get(target)
                rect = self._area_rect(area)
                if rect is None or not self._area_path(area).contains(point):
                    continue
                if target == "template" and self._use_golden_crop:
                    continue
                candidates.append((rect[2] * rect[3], target))
            if candidates:
                target = min(candidates)[1]
                if target != self._active_area:
                    self._activate_area(target)
        return super().eventFilter(watched, event)

    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._area_items.clear()  # scene.clear() owns/deletes these items
        self._result_items.clear()
        super().set_background(pixmap)
        self._render_areas()

    def _sync_geometry_controls(self) -> None:
        super()._sync_geometry_controls()
        self._sync_locator_area_labels()

    def _update_history_buttons(self) -> None:
        super()._update_history_buttons()
        self._sync_locator_area_labels()

    def _sync_locator_area_labels(self) -> None:
        if not getattr(self, "_locator_mode", False) or self._active_edit_context() != "roi":
            return
        area = "šablónu" if getattr(self, "_active_area", "search") == "template" else "oblasť hľadania"
        locked = self._view.is_roi_locked()
        self._btn_reset.setText(f"Obnoviť {area}")
        self._btn_lock.setText(f"{area.capitalize()} zamknutá" if locked else f"Zamknúť {area}")
        self._btn_lock.setToolTip(
            f"Odomknúť úpravu: {area}" if locked else f"Zamknúť úpravu: {area}"
        )

    def set_result_overlay(
        self,
        status: Optional[str],
        metrics: Optional[dict] = None,
        rect: Optional[Tuple[int, int, int, int]] = None,
        *,
        locator: bool = False,
    ) -> None:
        self._clear_result_overlay()
        if not status or rect is None:
            return
        color = QColor("#22C55E" if str(status).lower() == "ok" else "#EF4444")
        box = QGraphicsRectItem(QRectF(*rect))
        pen = QPen(color)
        pen.setWidthF(2.0)
        pen.setCosmetic(True)
        box.setPen(pen)
        box.setBrush(Qt.transparent)
        box.setZValue(15)
        box.setAcceptedMouseButtons(Qt.NoButton)
        self._view.scene().addItem(box)
        self._result_items.append(box)

        values = dict(metrics or {})
        score = next((values[key] for key in ("corr", "score", "ssim", "similarity")
                      if key in values), None)
        prefix = "MATCH" if locator else str(status).upper()
        try:
            text = f"{prefix}  {float(score):.3f}" if score is not None else prefix
        except (TypeError, ValueError):
            text = prefix
        badge = QGraphicsSimpleTextItem(text)
        badge.setBrush(color)
        badge.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        badge.setAcceptedMouseButtons(Qt.NoButton)
        badge.setZValue(16)
        badge.setPos(float(rect[0]), float(rect[1]))
        self._view.scene().addItem(badge)
        self._result_items.append(badge)

    def _clear_result_overlay(self) -> None:
        for item in self._result_items:
            if item.scene() is self._view.scene():
                self._view.scene().removeItem(item)
        self._result_items.clear()

    def configure_ignore_mask(self, enabled: bool, mask: Optional[np.ndarray] = None) -> None:
        enabled = bool(enabled)
        self._mask_available = enabled
        self._btn_roi_mode.setVisible(enabled)
        self._btn_mask_mode.setVisible(enabled)
        self._mask_controls.hide()
        for button in self._mask_tool_buttons:
            button.hide()
        self._btn_roi_mode.setChecked(True)
        self._view.configure_mask(enabled, mask)
        self._sync_mask_tool_buttons(None)
        self._set_mask_settings_enabled(False)
        self._update_history_buttons()

    def configure_edge_anchors(
        self,
        enabled: bool,
        point_a: Optional[Tuple[float, float]] = None,
        point_b: Optional[Tuple[float, float]] = None,
        search_half_window: int = 20,
        *,
        label: str = "Hrana A-B",
        refine_label: str = "Spresniť hranu",
        activate: bool = True,
    ) -> None:
        self._edge_available = bool(enabled)
        self._btn_edge_mode.setText(label)
        self._btn_edge_refine.setText(refine_label)
        self._btn_roi_mode.setVisible(self._mask_available or self._edge_available)
        self._btn_edge_mode.setVisible(self._edge_available)
        self._view.configure_edge_anchors(
            self._edge_available,
            point_a,
            point_b,
            search_half_window,
        )
        if activate:
            self.set_edit_context("edge" if self._edge_available else "roi")
        else:
            self.set_edit_context("roi")

    def _on_edge_anchors_changed(self, point_a: object, point_b: object) -> None:
        self._sync_edge_controls()
        self.edgeAnchorsChanged.emit(point_a, point_b)

    def set_edge_search_half_window(self, pixels: int) -> None:
        self._view.set_edge_search_half_window(pixels)

    def edge_points(
        self,
    ) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        return self._view.edge_points()

    def set_edge_detection_result(
        self,
        point_a: Tuple[float, float],
        point_b: Tuple[float, float],
        detected_points: List[Tuple[float, float]],
    ) -> None:
        self._view.set_edge_detection_result(point_a, point_b, detected_points)
        self._sync_edge_controls()

    def set_edge_status(self, message: str, *, error: bool = False) -> None:
        self._hint_label.setText(str(message))
        self._hint_label.setStyleSheet(
            "color: #ef4444; font-size: 11px;" if error
            else "color: #22c55e; font-size: 11px;"
        )
        self._hint_label.setVisible(bool(message))

    def set_edit_context(self, context: str) -> None:
        context = str(context or "roi")
        if context == "edge" and not self._edge_available:
            context = "roi"
        if context == "mask" and not self._mask_available:
            context = "roi"

        edge_editing = context == "edge"
        mask_editing = context == "mask"
        self._view.set_edge_editing(edge_editing)
        self._view.set_mask_editing(mask_editing)

        for button, checked in (
            (self._btn_roi_mode, context == "roi"),
            (self._btn_mask_mode, mask_editing),
            (self._btn_edge_mode, edge_editing),
        ):
            blocked = button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(blocked)

        self._geometry_controls.setVisible(context == "roi")
        for button in self._shape_buttons:
            button.setVisible(context == "roi" and not self._locator_mode)
            button.setEnabled(context == "roi")
        for index, button in enumerate(self._locator_buttons):
            button.setVisible(context == "roi" and self._locator_mode)
            button.setEnabled(
                context == "roi" and self._locator_mode
                and (index != 1 or not self._use_golden_crop)
            )
        for button in self._mask_tool_buttons:
            button.setVisible(mask_editing)
            button.setEnabled(mask_editing)
        self._mask_controls.setVisible(mask_editing)
        if mask_editing:
            self.set_mask_tool(self._view._mask_mode)
        else:
            self._sync_mask_tool_buttons(None)
        self._set_mask_settings_enabled(mask_editing)
        self._sync_edge_controls()
        self._update_history_buttons()

    def _sync_edge_controls(self) -> None:
        edge_editing = self._view._edge_editing
        point_a, point_b = self._view.edge_points()
        self._btn_edge_refine.setVisible(edge_editing)
        self._btn_edge_refine.setEnabled(point_a is not None and point_b is not None)
        if edge_editing:
            if point_a is None:
                self.set_edge_status("Klikni do obrazu a umiestni bod A.")
            elif point_b is None:
                self.set_edge_status("Bod A je nastavený. Klikni a umiestni bod B.")
            else:
                self.set_edge_status(
                    "Body A/B môžeš presúvať myšou alebo šípkami; Shift + šípka = 10 px."
                )
        else:
            self._update_info_label()

    def set_mask_editing(self, editing: bool) -> None:
        self.set_edit_context("mask" if editing else "roi")

    def _sync_mask_interaction_mode(self, mode: InteractionMode) -> None:
        if not self._view._mask_editing:
            return
        self._sync_mask_tool_buttons(
            self._view._mask_mode if mode == InteractionMode.DRAW else None
        )

    def _set_mask_settings_enabled(self, enabled: bool) -> None:
        for control in self._mask_setting_controls:
            control.setEnabled(enabled)

    def set_mask_tool(self, mode: str) -> None:
        self._view.set_mask_mode(mode)
        self._sync_mask_tool_buttons(mode if self._view._mask_editing else None)

    def _sync_mask_tool_buttons(self, active_mode: Optional[str]) -> None:
        self._mask_tool_group.setExclusive(False)
        for button, value in zip(self._mask_tool_buttons, (
                _SharedCanvasView.MASK_BRUSH, _SharedCanvasView.MASK_ERASER,
                _SharedCanvasView.MASK_RECTANGLE, _SharedCanvasView.MASK_CIRCLE,
                _SharedCanvasView.MASK_POLYGON)):
            button.setChecked(value == active_mode)
        self._mask_tool_group.setExclusive(True)

    def _start_mask_tool(self, mode: str) -> None:
        self.set_mask_editing(True)
        self.set_mask_tool(mode)

    def set_mask_brush_size(self, size: int) -> None:
        self._mask_brush_size.setValue(int(size))
        self._view.set_mask_brush_size(size)

    def set_mask_visible(self, visible: bool) -> None:
        self._btn_mask_visible.setChecked(bool(visible))
        self._view.set_mask_visible(visible)

    def set_mask_opacity(self, opacity: int) -> None:
        self._mask_opacity.setValue(int(opacity))
        self._view.set_mask_opacity(opacity)

    def clear_ignore_mask(self) -> None:
        self._view.clear_mask()

    def undo_ignore_mask(self) -> None:
        self._view.mask_undo()

    def redo_ignore_mask(self) -> None:
        self._view.mask_redo()

    def ignore_mask(self) -> Optional[np.ndarray]:
        return self._view.mask()

    def set_locator_mode(self, enabled: bool, *, search=None, template=None,
                         use_golden_crop: bool = False) -> None:
        self._locator_mode = bool(enabled)
        for button in self._shape_buttons:
            button.setVisible(not enabled and not self._view._mask_editing)
        for index, button in enumerate(self._locator_buttons):
            button.setVisible(enabled and not self._view._mask_editing)
            button.setEnabled(enabled and not self._view._mask_editing
                              and (index != 1 or not use_golden_crop))
        if not enabled:
            self._clear_area_items()
            return
        self._use_golden_crop = bool(use_golden_crop)
        self._areas = {
            "search": self._normalize_area(search),
            "template": self._normalize_area(template),
        }
        self._activate_area("search")

    def select_locator_roi(self, target: str) -> None:
        if self._locator_mode and target in self._areas:
            self._activate_area(target)

    def fit_search_to_template(self) -> None:
        template = self._areas.get("template")
        if template is None:
            return
        rect = self._area_rect(template)
        if rect is None:
            return
        x, y, width, height = rect
        mx, my = max(1, round(width * 0.2)), max(1, round(height * 0.2))
        proposed = self._clamp_scene((x - mx, y - my, width + 2 * mx, height + 2 * my))
        current = self._area_rect(self._areas.get("search"))
        self._areas["search"] = self._rect_area(
            self._clamp_scene(self._union(current, proposed)) if current else proposed
        )
        self._activate_area("search")
        self.locatorRoiChanged.emit("search", self._areas["search"])

    def _start_area_draw(self, target: str) -> None:
        if target == "template" and self._areas.get("search") is None:
            self._hint_label.setText("Najprv nastav oblasť hľadania.")
            self._hint_label.setVisible(True)
            return
        if target == "template" and self._use_golden_crop:
            return
        self._activate_area(target)
        self._view.set_draw_shape("rect")

    def _activate_area(self, target: str) -> None:
        self._active_area = target
        self._locator_syncing = True
        try:
            self.set_roi_data(self._areas.get(target) or {})
            self._view._shape_history.clear()
            self._view._shape_redo.clear()
            self._view.historyChanged.emit()
            self._view.set_interaction_mode(InteractionMode.SELECT)
        finally:
            self._locator_syncing = False
        self._style_active_area()
        self._render_areas()
        self._sync_geometry_controls()

    def _active_area_changed(self, rect: object) -> None:
        if not self._locator_mode or self._locator_syncing:
            return
        value = self._normalize_area(self._view.roi_data())
        if value is not None and self._active_area == "template":
            search = self._areas.get("search")
            contained = self._inside(value, search) if search else None
            value = contained if contained is not None else self._areas.get("template")
        elif value is not None and self._active_area == "search" and self._areas.get("template"):
            template_rect = self._area_rect(self._areas["template"])
            value = self._rect_area(self._clamp_scene(self._union(
                self._area_rect(value), template_rect
            ))) if template_rect is not None else value
        self._areas[self._active_area] = value
        if value != self._view.roi_data():
            self._locator_syncing = True
            try:
                self.set_roi_data(value or {})
            finally:
                self._locator_syncing = False
        self._style_active_area()
        self._render_areas()
        self.locatorRoiChanged.emit(self._active_area, value)

    def _style_active_area(self) -> None:
        if self._view._roi_item is not None:
            pen = QPen(self.COLORS[self._active_area]); pen.setWidthF(2.5)
            self._view._roi_item.setPen(pen)

    def _render_areas(self) -> None:
        self._clear_area_items()
        if not self._locator_mode:
            return
        for target in ("search", "template"):
            area = self._areas.get(target)
            rect = self._area_rect(area)
            if rect is None:
                continue
            if target == "template" and self._use_golden_crop:
                continue
            color = self.COLORS[target]
            if target != self._active_area:
                item = QGraphicsPathItem(self._area_path(area))
                pen = QPen(color); pen.setWidthF(1.5)
                item.setPen(pen); item.setBrush(Qt.transparent); item.setZValue(20)
                item.setAcceptedMouseButtons(Qt.NoButton)
                self._view.scene().addItem(item)
                self._area_items.append(item)
            label = QGraphicsSimpleTextItem(self.LABELS[target])
            label.setBrush(color); label.setZValue(130)
            label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
            label.setPos(float(rect[0]), max(0.0, float(rect[1]) - 16.0))
            self._view.scene().addItem(label)
            self._area_items.append(label)

    def _clear_area_items(self) -> None:
        for item in self._area_items:
            if item.scene() is self._view.scene():
                self._view.scene().removeItem(item)
        self._area_items.clear()

    def _clamp_scene(self, rect):
        return self._view._clamp_integer_rect(rect)

    @staticmethod
    def _rect_area(rect):
        if rect is None:
            return None
        x, y, width, height = rect
        return {"x": int(x), "y": int(y), "w": int(width), "h": int(height)}

    @staticmethod
    def _normalize_area(value):
        descriptor = ToolRoi.from_obj(value)
        return descriptor.to_dict() or None

    @staticmethod
    def _area_rect(value):
        return ToolRoi.from_obj(value).rect()

    @staticmethod
    def _area_path(value) -> QPainterPath:
        descriptor = ToolRoi.from_obj(value)
        path = QPainterPath()
        if descriptor.shape() == "polygon":
            points = descriptor.points()
            if points:
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
                path.closeSubpath()
            return path
        rect = descriptor.rect()
        if rect is not None:
            rectf = QRectF(*rect)
            if descriptor.shape() == "ellipse":
                path.addEllipse(rectf)
            else:
                path.addRect(rectf)
        return path

    @classmethod
    def _inside(cls, value, bounds):
        if value is None or bounds is None:
            return None
        bounds_path = cls._area_path(bounds)
        candidate = ToolRoi.from_obj(value)
        points = candidate.points()
        if not points:
            rect = candidate.rect()
            if rect is None:
                return None
            x, y, width, height = rect
            points = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
        if all(bounds_path.contains(QPointF(float(x), float(y))) for x, y in points):
            return candidate.to_dict()
        # Axis-aligned areas can be safely clamped.  Rotated areas remain
        # unchanged rather than silently distorting their geometry.
        if candidate.is_rotated_rect() or ToolRoi.from_obj(bounds).is_rotated_rect():
            return None
        rect = candidate.rect(); bounds_rect = ToolRoi.from_obj(bounds).rect()
        return cls._rect_area(cls._inside_rect(rect, bounds_rect))

    @staticmethod
    def _inside_rect(rect, bounds):
        x, y, width, height = rect; bx, by, bw, bh = bounds
        width, height = min(width, bw), min(height, bh)
        return (min(max(x, bx), bx + bw - width), min(max(y, by), by + bh - height),
                width, height)

    @staticmethod
    def _union(first, second):
        ax, ay, aw, ah = first; bx, by, bw, bh = second
        left, top = min(ax, bx), min(ay, by)
        right, bottom = max(ax + aw, bx + bw), max(ay + ah, by + bh)
        return left, top, right - left, bottom - top


@dataclass
class MaskEditorState:
    mode: str
    brush_radius: int
    fill_mode: str = _MaskView.FILL_INSIDE
    show_roi_overlay: bool = False


class MaskEditor(QWidget):
    """Composite widget for ignore mask editing with undo/redo."""

    maskChanged = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._view = _MaskView(self)
        self._view.maskChanged.connect(self._on_mask_changed)
        self._view.historyChanged.connect(self._update_history_buttons)
        self._roi_overlay_rect: Optional[Tuple[int, int, int, int]] = None

        self._mode_group = QButtonGroup(self)
        self._btn_brush_add = QToolButton(self)
        self._btn_brush_add.setText("Brush +")
        self._btn_brush_add.setCheckable(True)
        self._btn_brush_add.setToolTip("Add to ignore mask")
        self._mode_group.addButton(self._btn_brush_add)

        self._btn_brush_erase = QToolButton(self)
        self._btn_brush_erase.setText("Brush –")
        self._btn_brush_erase.setCheckable(True)
        self._btn_brush_erase.setToolTip("Erase from ignore mask")
        self._mode_group.addButton(self._btn_brush_erase)

        self._btn_polygon = QToolButton(self)
        self._btn_polygon.setText("Polygón")
        self._btn_polygon.setCheckable(True)
        self._btn_polygon.setToolTip("Double click to finish polygon fill")
        self._mode_group.addButton(self._btn_polygon)

        self._btn_circle = QToolButton(self)
        self._btn_circle.setText("Kruh")
        self._btn_circle.setCheckable(True)
        self._btn_circle.setToolTip("Click three points to define a circle")
        self._mode_group.addButton(self._btn_circle)

        self._btn_rectangle = QToolButton(self)
        self._btn_rectangle.setText("Obdĺžnik")
        self._btn_rectangle.setCheckable(True)
        self._btn_rectangle.setToolTip("Click and drag to draw a rectangle")
        self._mode_group.addButton(self._btn_rectangle)

        self._btn_brush_add.setChecked(True)

        self._mode_group.buttonClicked.connect(self._on_mode_changed)

        self._fill_label = QLabel("Výplň:", self)
        self._fill_label.setStyleSheet("color: #666;")
        self._fill_combo = QComboBox(self)
        self._fill_combo.addItem("Vo vnútri", self._view.FILL_INSIDE)
        self._fill_combo.addItem("Okolo", self._view.FILL_AROUND)
        self._fill_combo.currentIndexChanged.connect(self._on_fill_mode_changed)
        self._fill_label.setEnabled(False)
        self._fill_combo.setEnabled(False)

        self._brush_slider = QSlider(Qt.Horizontal, self)
        self._brush_slider.setRange(3, 160)
        self._brush_slider.setValue(24)
        self._brush_slider.valueChanged.connect(self._on_brush_radius_changed)

        self._brush_label = QLabel("", self)
        self._brush_label.setStyleSheet("color: #666;")

        self._btn_undo = QPushButton("Späť", self)
        self._btn_redo = QPushButton("Znova", self)
        self._btn_clear = QPushButton("Vymazať", self)
        self._btn_undo.clicked.connect(self._view.undo)
        self._btn_redo.clicked.connect(self._view.redo)
        self._btn_clear.clicked.connect(self._view.clear_mask)

        self._info_label = QLabel("Ignorované pixely: 0", self)
        self._info_label.setStyleSheet("color: #bbb;")
        self._hint_label = QLabel("", self)
        self._hint_label.setStyleSheet("color: #d48806; font-size: 11px;")
        self._hint_label.setVisible(False)

        self._show_roi_checkbox = QCheckBox("Zobraziť prekrytie ROI", self)
        self._show_roi_checkbox.setChecked(False)
        self._show_roi_checkbox.toggled.connect(self._on_show_roi_toggled)
        self._show_roi_checkbox.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._navigation = ImageNavigationToolbar(self._view, self)
        layout.addWidget(self._navigation)
        layout.addWidget(self._view, 1)

        toolbar_top = QHBoxLayout()
        toolbar_top.setContentsMargins(0, 0, 0, 0)
        toolbar_top.setSpacing(8)
        toolbar_top.addWidget(self._btn_brush_add)
        toolbar_top.addWidget(self._btn_brush_erase)
        toolbar_top.addWidget(self._btn_polygon)
        toolbar_top.addWidget(self._btn_circle)
        toolbar_top.addWidget(self._btn_rectangle)
        toolbar_top.addSpacing(12)
        toolbar_top.addWidget(self._fill_label)
        toolbar_top.addWidget(self._fill_combo)
        toolbar_top.addSpacing(12)
        toolbar_top.addWidget(self._brush_label)
        toolbar_top.addWidget(self._brush_slider, 1)
        layout.addLayout(toolbar_top)

        toolbar_bottom = QHBoxLayout()
        toolbar_bottom.setContentsMargins(0, 0, 0, 0)
        toolbar_bottom.setSpacing(8)
        toolbar_bottom.addWidget(self._btn_undo)
        toolbar_bottom.addWidget(self._btn_redo)
        toolbar_bottom.addWidget(self._btn_clear)
        toolbar_bottom.addSpacing(12)
        toolbar_bottom.addWidget(self._show_roi_checkbox)
        toolbar_bottom.addStretch(1)
        info_box = QVBoxLayout()
        info_box.setContentsMargins(0, 0, 0, 0)
        info_box.setSpacing(2)
        info_box.addWidget(self._info_label)
        info_box.addWidget(self._hint_label)
        toolbar_bottom.addLayout(info_box)
        layout.addLayout(toolbar_bottom)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._update_history_buttons()
        self._on_mode_changed()
        self._on_fill_mode_changed()
        self._on_brush_radius_changed(self._brush_slider.value())
        self._sync_roi_overlay_visibility()

    # ------------------------------------------------------------------
    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._view.set_pixmap(pixmap)
        self._sync_roi_overlay_visibility()
        self._update_history_buttons()
        self._update_info_label()

    def set_mask(self, mask: Optional[np.ndarray]) -> None:
        self._view.set_mask(mask)
        self._update_history_buttons()
        self._update_info_label()

    def mask(self) -> Optional[np.ndarray]:
        return self._view.mask()

    def set_roi_overlay(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self._roi_overlay_rect = tuple(map(int, rect)) if rect is not None else None
        self._sync_roi_overlay_visibility()

    def set_show_roi_overlay(self, show: bool) -> None:
        show = bool(show)
        if self._roi_overlay_rect is None:
            show = False
        if self._show_roi_checkbox.isChecked() != show:
            self._show_roi_checkbox.blockSignals(True)
            self._show_roi_checkbox.setChecked(show)
            self._show_roi_checkbox.blockSignals(False)
        self._sync_roi_overlay_visibility()

    def show_roi_overlay(self) -> bool:
        return self._view.show_roi_overlay()

    def undo(self) -> None:
        self._view.undo()

    def redo(self) -> None:
        self._view.redo()

    def clear(self) -> None:
        self._view.clear_mask()

    def schedule_fit_to_view(self, *, source: str = "mask_editor") -> None:
        self._view.schedule_fit_to_view(source=source)

    def state(self) -> MaskEditorState:
        if self._btn_brush_add.isChecked():
            mode = self._view.MODE_BRUSH_ADD
        elif self._btn_brush_erase.isChecked():
            mode = self._view.MODE_BRUSH_ERASE
        elif self._btn_polygon.isChecked():
            mode = self._view.MODE_POLYGON
        elif self._btn_circle.isChecked():
            mode = self._view.MODE_SHAPE_CIRCLE
        else:
            mode = self._view.MODE_SHAPE_RECTANGLE
        return MaskEditorState(
            mode=mode,
            brush_radius=self._brush_slider.value(),
            fill_mode=self._view.fill_mode(),
            show_roi_overlay=self._view.show_roi_overlay(),
        )

    def restore_state(self, state: MaskEditorState) -> None:
        if state.mode == self._view.MODE_BRUSH_ADD:
            self._btn_brush_add.setChecked(True)
        elif state.mode == self._view.MODE_BRUSH_ERASE:
            self._btn_brush_erase.setChecked(True)
        elif state.mode == self._view.MODE_POLYGON:
            self._btn_polygon.setChecked(True)
        elif state.mode == self._view.MODE_SHAPE_CIRCLE:
            self._btn_circle.setChecked(True)
        elif state.mode == self._view.MODE_SHAPE_RECTANGLE:
            self._btn_rectangle.setChecked(True)
        else:
            self._btn_brush_add.setChecked(True)
        self._on_mode_changed()
        self._brush_slider.setValue(state.brush_radius)
        index = self._fill_combo.findData(state.fill_mode)
        if index < 0:
            index = self._fill_combo.findData(self._view.FILL_INSIDE)
        if index >= 0:
            self._fill_combo.blockSignals(True)
            self._fill_combo.setCurrentIndex(index)
            self._fill_combo.blockSignals(False)
        self._on_fill_mode_changed()
        self.set_show_roi_overlay(state.show_roi_overlay)
        self._update_size_controls()

    # ------------------------------------------------------------------
    def _on_mode_changed(self) -> None:
        if self._btn_brush_add.isChecked():
            self._view.set_mode(self._view.MODE_BRUSH_ADD)
        elif self._btn_brush_erase.isChecked():
            self._view.set_mode(self._view.MODE_BRUSH_ERASE)
        elif self._btn_polygon.isChecked():
            self._view.set_mode(self._view.MODE_POLYGON)
        elif self._btn_circle.isChecked():
            self._view.set_mode(self._view.MODE_SHAPE_CIRCLE)
        else:
            self._view.set_mode(self._view.MODE_SHAPE_RECTANGLE)
        shape_mode = self._btn_circle.isChecked() or self._btn_rectangle.isChecked()
        self._fill_label.setEnabled(shape_mode)
        self._fill_combo.setEnabled(shape_mode)
        if shape_mode:
            data = self._fill_combo.currentData()
            if isinstance(data, str):
                self._view.set_fill_mode(data)
        self._update_size_controls()

    def _on_brush_radius_changed(self, value: int) -> None:
        self._view.set_brush_radius(value)
        self._update_size_controls()

    def _on_fill_mode_changed(self) -> None:
        data = self._fill_combo.currentData()
        if isinstance(data, str):
            self._view.set_fill_mode(data)
        self._update_size_controls()

    def _on_show_roi_toggled(self, checked: bool) -> None:  # noqa: FBT001
        self._sync_roi_overlay_visibility()

    def _on_mask_changed(self, mask: Optional[np.ndarray]) -> None:
        self._update_info_label()
        self._update_history_buttons()
        self.maskChanged.emit(mask.copy() if mask is not None else None)

    def _update_history_buttons(self) -> None:
        self._btn_undo.setEnabled(self._view.can_undo())
        self._btn_redo.setEnabled(self._view.can_redo())

    def _update_size_controls(self) -> None:
        value = int(self._brush_slider.value())
        if self._btn_polygon.isChecked():
            self._brush_slider.setEnabled(False)
            self._brush_label.setEnabled(False)
            self._brush_label.setText("Brush: —")
            return
        if self._btn_circle.isChecked() or self._btn_rectangle.isChecked():
            fill_mode = self._fill_combo.currentData()
            if fill_mode == self._view.FILL_AROUND:
                self._brush_slider.setEnabled(True)
                self._brush_label.setEnabled(True)
                self._brush_label.setText(f"Ring width: {value} px")
            else:
                self._brush_slider.setEnabled(False)
                self._brush_label.setEnabled(False)
                self._brush_label.setText("Ring width: —")
            return
        self._brush_slider.setEnabled(True)
        self._brush_label.setEnabled(True)
        self._brush_label.setText(f"Brush: {value} px")

    def _update_info_label(self) -> None:
        mask = self._view.mask()
        count = int(np.count_nonzero(mask)) if mask is not None else 0
        self._info_label.setText(f"Ignorované pixely: {_format_pixels(count)}")
        if count > MAX_MASK_PIXELS:
            limit = _format_pixels(MAX_MASK_PIXELS)
            self._hint_label.setText(
                f"⚠ Maska presahuje limit testu ({limit} px). Zmenši ju."
            )
            self._hint_label.setStyleSheet("color: #b03030; font-size: 11px;")
            self._hint_label.setVisible(True)
        elif count > MASK_WARN_PIXELS:
            self._hint_label.setText("⚠ Veľká maska – výpočet môže byť pomalší.")
            self._hint_label.setStyleSheet("color: #d48806; font-size: 11px;")
            self._hint_label.setVisible(True)
        else:
            self._hint_label.setVisible(False)

    def _sync_roi_overlay_visibility(self) -> None:
        has_roi = self._roi_overlay_rect is not None
        if not has_roi and self._show_roi_checkbox.isChecked():
            self._show_roi_checkbox.blockSignals(True)
            self._show_roi_checkbox.setChecked(False)
            self._show_roi_checkbox.blockSignals(False)
        self._show_roi_checkbox.setEnabled(has_roi)
        self._view.set_roi_overlay(self._roi_overlay_rect)
        self._view.set_show_roi_overlay(has_roi and self._show_roi_checkbox.isChecked())

"""shared canvas canvas interaction."""
from __future__ import annotations
from app.services.mask_painting import brush_point
from app.ui.roi.history import EditHistory
import math
from typing import List, Optional, Tuple
import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsItem, QGraphicsPixmapItem, QWidget
from app.ui.image_canvas import InteractionMode

from app.ui.roi.geometry import _clamp_point_to_rect
from app.ui.roi.shape_view import _ShapeROIView

class _SharedCanvasView(_ShapeROIView):
    """ROI view with an optional raster ignore-mask layer in the same scene."""

    maskChanged = Signal(object)
    maskHistoryChanged = Signal()
    edgeAnchorsChanged = Signal(object, object)
    edgeHistoryChanged = Signal()
    MASK_BRUSH = "brush"
    MASK_ERASER = "eraser"
    MASK_RECTANGLE = "rectangle"
    MASK_CIRCLE = "circle"
    MASK_POLYGON = "polygon"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._mask_enabled = False
        self._mask_editing = False
        self._mask_mode = self.MASK_BRUSH
        self._mask: Optional[np.ndarray] = None
        self._mask_item: Optional[QGraphicsPixmapItem] = None
        self._mask_rgba: Optional[np.ndarray] = None
        self._mask_visible = True
        self._mask_opacity = 40
        self._mask_brush_size = 25
        self._mask_before: Optional[np.ndarray] = None
        self._mask_last: Optional[QPointF] = None
        self._mask_start: Optional[QPointF] = None
        self._mask_polygon: List[QPointF] = []
        self._mask_preview: Optional[QGraphicsPathItem] = None
        self._mask_history = EditHistory()
        self._edge_enabled = False
        self._edge_editing = False
        self._edge_point_a: Optional[Tuple[float, float]] = None
        self._edge_point_b: Optional[Tuple[float, float]] = None
        self._edge_search_half_window = 20
        self._edge_detected_points: List[Tuple[float, float]] = []
        self._edge_detected_line: Optional[
            Tuple[Tuple[float, float], Tuple[float, float]]
        ] = None
        self._edge_items: List[QGraphicsItem] = []
        self._edge_undo: List[
            Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]
        ] = []
        self._edge_redo: List[
            Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]
        ] = []
        self._edge_drag_target: Optional[str] = None
        self._edge_drag_before: Optional[
            Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]
        ] = None
        self._edge_selected_target: Optional[str] = None
        self._edge_scene_resetting = False

    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:  # type: ignore[override]
        self._edge_scene_resetting = True
        self._edge_items.clear()
        try:
            self.cancel_drawing()
            # The base view clears the scene; discard its scene-owned wrappers first.
            self._mask_item = None
            self._mask_preview = None
            super().set_pixmap(pixmap)
        finally:
            self._edge_scene_resetting = False
        if pixmap is None or pixmap.isNull():
            self._mask = None
        else:
            self._mask = np.zeros((pixmap.height(), pixmap.width()), dtype=np.uint8)
        self._mask_history.past.clear()
        self._mask_history.future.clear()
        self._edge_point_a = None
        self._edge_point_b = None
        self._edge_detected_points.clear()
        self._edge_detected_line = None
        self._edge_undo.clear()
        self._edge_redo.clear()
        self._edge_drag_target = None
        self._edge_drag_before = None
        self._update_mask_overlay()
        self._render_edge_anchors()
        self.maskHistoryChanged.emit()
        self.edgeHistoryChanged.emit()

    def configure_edge_anchors(
        self,
        enabled: bool,
        point_a: Optional[Tuple[float, float]] = None,
        point_b: Optional[Tuple[float, float]] = None,
        search_half_window: int = 20,
    ) -> None:
        self.cancel_drawing()
        self._edge_enabled = bool(enabled)
        self._edge_editing = bool(enabled)
        self._edge_point_a = self._coerce_edge_point(point_a) if enabled else None
        self._edge_point_b = self._coerce_edge_point(point_b) if enabled else None
        self._edge_search_half_window = max(1, int(search_half_window))
        self._edge_detected_points.clear()
        self._edge_detected_line = None
        self._edge_undo.clear()
        self._edge_redo.clear()
        self._edge_selected_target = None
        self.set_interaction_mode(InteractionMode.SELECT)
        self._render_edge_anchors()
        self.edgeHistoryChanged.emit()

    def set_edge_editing(self, editing: bool) -> None:
        self.cancel_drawing()
        self._edge_editing = bool(editing) and self._edge_enabled
        if self._edge_editing:
            self._mask_editing = False
            self.set_interaction_mode(InteractionMode.SELECT)
        self._render_edge_anchors()
        self._update_cursor()

    def _update_cursor(self) -> None:
        super()._update_cursor()
        if (
            getattr(self, "_edge_editing", False)
            and not self._panning
            and not self._space_pressed
        ):
            self.setCursor(Qt.CrossCursor)

    def set_edge_search_half_window(self, pixels: int) -> None:
        self._edge_search_half_window = max(1, int(pixels))
        self._render_edge_anchors()

    def edge_points(
        self,
    ) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        return self._edge_snapshot()

    def edge_can_undo(self) -> bool:
        return bool(self._edge_undo)

    def edge_can_redo(self) -> bool:
        return bool(self._edge_redo)

    def edge_undo(self) -> None:
        if not self._edge_undo:
            return
        self._edge_redo.append(self._edge_snapshot())
        self._restore_edge_snapshot(self._edge_undo.pop())

    def edge_redo(self) -> None:
        if not self._edge_redo:
            return
        self._edge_undo.append(self._edge_snapshot())
        self._restore_edge_snapshot(self._edge_redo.pop())

    def reset_edge_anchors(self) -> None:
        self._commit_edge_points(None, None)

    def set_edge_detection_result(
        self,
        point_a: Tuple[float, float],
        point_b: Tuple[float, float],
        detected_points: List[Tuple[float, float]],
    ) -> None:
        self._commit_edge_points(point_a, point_b, render=False)
        self._edge_detected_points = [
            (float(point[0]), float(point[1])) for point in detected_points
        ]
        self._edge_detected_line = (
            (float(point_a[0]), float(point_a[1])),
            (float(point_b[0]), float(point_b[1])),
        )
        self._render_edge_anchors()

    @staticmethod
    def _coerce_edge_point(
        point: Optional[Tuple[float, float]],
    ) -> Optional[Tuple[float, float]]:
        if point is None:
            return None
        return float(point[0]), float(point[1])

    def _edge_snapshot(
        self,
    ) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        return self._edge_point_a, self._edge_point_b

    def _restore_edge_snapshot(
        self,
        state: Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]],
    ) -> None:
        self._edge_point_a, self._edge_point_b = state
        self._edge_detected_points.clear()
        self._edge_detected_line = None
        self._edge_selected_target = None
        self._render_edge_anchors()
        self.edgeAnchorsChanged.emit(self._edge_point_a, self._edge_point_b)
        self.edgeHistoryChanged.emit()

    def _commit_edge_points(
        self,
        point_a: Optional[Tuple[float, float]],
        point_b: Optional[Tuple[float, float]],
        *,
        render: bool = True,
    ) -> None:
        before = self._edge_snapshot()
        after = self._coerce_edge_point(point_a), self._coerce_edge_point(point_b)
        if before == after:
            return
        self._edge_undo.append(before)
        self._edge_undo = self._edge_undo[-100:]
        self._edge_redo.clear()
        self._edge_point_a, self._edge_point_b = after
        self._edge_detected_points.clear()
        self._edge_detected_line = None
        if render:
            self._render_edge_anchors()
        self.edgeAnchorsChanged.emit(self._edge_point_a, self._edge_point_b)
        self.edgeHistoryChanged.emit()

    def _clear_edge_items(self) -> None:
        if self._edge_scene_resetting:
            self._edge_items.clear()
            return
        scene = self.scene()
        for item in self._edge_items:
            if item.scene() is scene:
                scene.removeItem(item)
        self._edge_items.clear()

    def _render_edge_anchors(self) -> None:
        self._clear_edge_items()
        if not self._edge_enabled:
            return
        scene = self.scene()
        point_a, point_b = self._edge_point_a, self._edge_point_b
        if point_a is not None and point_b is not None:
            ax, ay = point_a
            bx, by = point_b
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy)
            if length > 1e-6:
                nx = -dy / length * self._edge_search_half_window
                ny = dx / length * self._edge_search_half_window
                path = QPainterPath(QPointF(ax + nx, ay + ny))
                path.lineTo(QPointF(bx + nx, by + ny))
                path.lineTo(QPointF(bx - nx, by - ny))
                path.lineTo(QPointF(ax - nx, ay - ny))
                path.closeSubpath()
                band = QGraphicsPathItem(path)
                band_pen = QPen(QColor(70, 150, 255, 180), 1, Qt.DashLine)
                band_pen.setCosmetic(True)
                band.setPen(band_pen)
                band.setBrush(QBrush(QColor(70, 150, 255, 35)))
                band.setZValue(110)
                band.setAcceptedMouseButtons(Qt.NoButton)
                scene.addItem(band)
                self._edge_items.append(band)

            rough_pen = QPen(QColor(255, 210, 110), 2)
            rough_pen.setCosmetic(True)
            rough = scene.addLine(ax, ay, bx, by, rough_pen)
            rough.setZValue(112)
            rough.setAcceptedMouseButtons(Qt.NoButton)
            self._edge_items.append(rough)

        if self._edge_detected_line is not None:
            detected_a, detected_b = self._edge_detected_line
            detected_pen = QPen(QColor(34, 197, 94), 2.5)
            detected_pen.setCosmetic(True)
            line = scene.addLine(
                detected_a[0], detected_a[1], detected_b[0], detected_b[1], detected_pen
            )
            line.setZValue(114)
            line.setAcceptedMouseButtons(Qt.NoButton)
            self._edge_items.append(line)
        stride = max(1, int(math.ceil(len(self._edge_detected_points) / 160)))
        for x, y in self._edge_detected_points[::stride]:
            marker = scene.addEllipse(
                x - 1.75, y - 1.75, 3.5, 3.5,
                QPen(Qt.NoPen), QBrush(QColor(50, 220, 120, 220)),
            )
            marker.setZValue(115)
            marker.setAcceptedMouseButtons(Qt.NoButton)
            self._edge_items.append(marker)

        for target, point, label_text in (
            ("a", point_a, "A"),
            ("b", point_b, "B"),
        ):
            if point is None:
                continue
            selected = self._edge_editing and target == self._edge_selected_target
            color = QColor("#FBBF24" if selected else "#60A5FA")
            marker_pen = QPen(color, 2)
            marker_pen.setCosmetic(True)
            marker = scene.addEllipse(
                point[0] - 5, point[1] - 5, 10, 10,
                marker_pen, QBrush(QColor(17, 24, 39, 210)),
            )
            marker.setZValue(118)
            marker.setAcceptedMouseButtons(Qt.NoButton)
            label = scene.addSimpleText(label_text)
            label.setBrush(color)
            label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
            label.setPos(point[0] + 7, point[1] - 14)
            label.setZValue(119)
            label.setAcceptedMouseButtons(Qt.NoButton)
            self._edge_items.extend((marker, label))

    def _edge_hit_target(self, viewport_pos) -> Optional[str]:
        scene_pos = self.mapToScene(viewport_pos)
        radius = 11.0 / max(0.01, self.zoom())
        candidates = []
        for target, point in (("a", self._edge_point_a), ("b", self._edge_point_b)):
            if point is not None:
                distance = math.hypot(scene_pos.x() - point[0], scene_pos.y() - point[1])
                if distance <= radius:
                    candidates.append((distance, target))
        return min(candidates)[1] if candidates else None

    def _edge_event_point(self, event) -> Tuple[float, float]:
        point = self._clamp_edge_point(
            self.mapToScene(event.position().toPoint())
        )
        return float(point.x()), float(point.y())

    def _clamp_edge_point(self, point: QPointF) -> QPointF:
        point = _clamp_point_to_rect(point, self.scene_rect())
        if self._roi_rect is not None:
            point = _clamp_point_to_rect(point, QRectF(*self._roi_rect))
        return point

    def configure_mask(self, enabled: bool, mask: Optional[np.ndarray] = None) -> None:
        self.cancel_drawing()
        self._mask_enabled = bool(enabled)
        self._mask_editing = False
        self.set_interaction_mode(InteractionMode.SELECT)
        if enabled:
            self.set_mask(mask)
        else:
            self._mask_history.past.clear()
            self._mask_history.future.clear()
            self._update_mask_overlay()
        self._update_cursor()

    def set_mask_editing(self, editing: bool) -> None:
        self.cancel_drawing()
        self._mask_editing = bool(editing) and self._mask_enabled
        self.set_interaction_mode(InteractionMode.SELECT)
        self._update_cursor()

    def set_mask_mode(self, mode: str) -> None:
        if mode not in {self.MASK_BRUSH, self.MASK_ERASER, self.MASK_RECTANGLE,
                        self.MASK_CIRCLE, self.MASK_POLYGON}:
            return
        self.cancel_drawing()
        self._mask_mode = mode
        if self._mask_editing:
            self.set_interaction_mode(InteractionMode.DRAW)

    def set_mask_brush_size(self, size: int) -> None:
        self._mask_brush_size = max(1, min(200, int(size)))

    def set_mask_visible(self, visible: bool) -> None:
        self._mask_visible = bool(visible)
        if self._mask_item is not None:
            self._mask_item.setVisible(self._mask_visible and self._mask_enabled)

    def set_mask_opacity(self, opacity: int) -> None:
        self._mask_opacity = max(10, min(90, int(opacity)))
        self._update_mask_overlay()

    def mask(self) -> Optional[np.ndarray]:
        return None if self._mask is None else self._mask.copy()

    def set_mask(self, mask: Optional[np.ndarray]) -> None:
        bounds = self.scene_rect()
        if bounds.isEmpty():
            self._mask = None if mask is None else np.asarray(mask, dtype=np.uint8).copy()
        else:
            width, height = int(bounds.width()), int(bounds.height())
            if mask is None:
                self._mask = np.zeros((height, width), dtype=np.uint8)
            else:
                value = np.asarray(mask, dtype=np.uint8)
                if value.ndim == 3:
                    value = value[:, :, 0]
                if value.shape != (height, width):
                    value = cv2.resize(value, (width, height), interpolation=cv2.INTER_NEAREST)
                self._mask = value.copy()
        self._mask_history.past.clear()
        self._mask_history.future.clear()
        self._update_mask_overlay()
        self.maskHistoryChanged.emit()

    def clear_mask(self) -> None:
        if self._mask is None or not np.any(self._mask):
            return
        self._push_mask_undo()
        self._mask.fill(0)
        self._mask_history.future.clear()
        self._finish_mask_change()

    def mask_undo(self) -> None:
        if not self._mask_history.past or self._mask is None:
            return
        self._mask_history.future.append(self._mask.copy())
        self._mask = self._mask_history.past.pop()
        self._finish_mask_change()

    def mask_redo(self) -> None:
        if not self._mask_history.future or self._mask is None:
            return
        self._mask_history.past.append(self._mask.copy())
        self._mask = self._mask_history.future.pop()
        self._finish_mask_change()

    def cancel_drawing(self) -> None:
        if self._edge_drag_before is not None:
            self._edge_point_a, self._edge_point_b = self._edge_drag_before
        self._edge_drag_target = None
        self._edge_drag_before = None
        if not self._edge_scene_resetting:
            self._render_edge_anchors()
        if self._mask_before is not None:
            self._mask = self._mask_before
            self._update_mask_overlay()
        self._mask_before = None
        self._mask_last = None
        self._mask_start = None
        self._mask_polygon.clear()
        self._remove_mask_preview()
        super().cancel_drawing()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._edge_editing and not self.is_pan_gesture(event):
            self.setFocus(Qt.MouseFocusReason)
            if event.button() != Qt.LeftButton:
                event.accept()
                return
            target = self._edge_hit_target(event.position().toPoint())
            scene_point = self.mapToScene(event.position().toPoint())
            valid_area = (
                QRectF(*self._roi_rect)
                if self._roi_rect is not None
                else self.scene_rect()
            )
            if target is None and not valid_area.contains(scene_point):
                event.accept()
                return
            point = self._edge_event_point(event)
            if target is not None:
                self._edge_selected_target = target
                self._edge_drag_target = target
                self._edge_drag_before = self._edge_snapshot()
                self._render_edge_anchors()
            elif self._edge_point_a is None:
                self._edge_selected_target = "a"
                self._commit_edge_points(point, self._edge_point_b)
            elif self._edge_point_b is None:
                self._edge_selected_target = "b"
                self._commit_edge_points(self._edge_point_a, point)
            event.accept()
            return
        if (not self._mask_editing or self.is_pan_gesture(event)
                or not self.can_draw() or self._mask is None):
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.MouseFocusReason)
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        point = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
        if self._mask_mode == self.MASK_POLYGON:
            self._mask_polygon.append(point)
            self._update_mask_preview(point)
        elif self._mask_mode in (self.MASK_RECTANGLE, self.MASK_CIRCLE):
            self._mask_start = point
            self._update_mask_preview(point)
        else:
            self._mask_before = self._mask.copy()
            self._mask_last = point
            self._paint_mask(point)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._edge_editing and self._edge_drag_target is not None:
            point = self._edge_event_point(event)
            if self._edge_drag_target == "a":
                self._edge_point_a = point
            else:
                self._edge_point_b = point
            self._edge_detected_points.clear()
            self._edge_detected_line = None
            self._render_edge_anchors()
            event.accept()
            return
        if self._edge_editing and event.buttons() == Qt.NoButton:
            target = self._edge_hit_target(event.position().toPoint())
            self.setCursor(Qt.SizeAllCursor if target is not None else Qt.CrossCursor)
            event.accept()
            return
        if self._panning or self._space_pressed or not self._mask_editing:
            super().mouseMoveEvent(event)
            return
        point = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
        if self._mask_before is not None:
            self._paint_mask_line(point)
        elif self._mask_start is not None or self._mask_polygon:
            self._update_mask_preview(point)
        else:
            super().mouseMoveEvent(event)
            return
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            self._edge_editing
            and self._edge_drag_target is not None
            and event.button() == Qt.LeftButton
        ):
            before = self._edge_drag_before
            self._edge_drag_target = None
            self._edge_drag_before = None
            if before is not None and before != self._edge_snapshot():
                self._edge_undo.append(before)
                self._edge_undo = self._edge_undo[-100:]
                self._edge_redo.clear()
                self.edgeAnchorsChanged.emit(self._edge_point_a, self._edge_point_b)
                self.edgeHistoryChanged.emit()
            self._render_edge_anchors()
            event.accept()
            return
        if not self._mask_editing or event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._mask_before is not None:
            self._mask_history.past.append(self._mask_before)
            self._mask_history.past = self._mask_history.past[-100:]
            self._mask_history.future.clear()
            self._mask_before = None
            self._mask_last = None
            self._finish_mask_change()
            event.accept()
            return
        if self._mask_start is not None:
            point = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
            start, self._mask_start = self._mask_start, None
            self._remove_mask_preview()
            self._apply_mask_shape(start, point)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if (self._mask_editing and self._mask_mode == self.MASK_POLYGON
                and event.button() == Qt.LeftButton):
            self._commit_mask_polygon()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._edge_editing:
            directions = {
                Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
                Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1),
            }
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                if self._edge_selected_target == "a":
                    self._commit_edge_points(None, self._edge_point_b)
                elif self._edge_selected_target == "b":
                    self._commit_edge_points(self._edge_point_a, None)
                event.accept()
                return
            if event.key() in directions and self._edge_selected_target in {"a", "b"}:
                dx, dy = directions[event.key()]
                step = 10 if event.modifiers() & Qt.ShiftModifier else 1
                point = (
                    self._edge_point_a
                    if self._edge_selected_target == "a"
                    else self._edge_point_b
                )
                if point is not None:
                    target = self._clamp_edge_point(
                        QPointF(point[0] + dx * step, point[1] + dy * step)
                    )
                    if self._edge_selected_target == "a":
                        self._commit_edge_points(
                            (target.x(), target.y()), self._edge_point_b
                        )
                    else:
                        self._commit_edge_points(
                            self._edge_point_a, (target.x(), target.y())
                        )
                event.accept()
                return
        if (self._mask_editing and self._mask_mode == self.MASK_POLYGON
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)):
            self._commit_mask_polygon()
            event.accept()
            return
        super().keyPressEvent(event)

    def _paint_mask(self, point: QPointF) -> None:
        if self._mask is None:
            return
        brush_point(self._mask, point.x(), point.y(), max(1, self._mask_brush_size // 2),
                    erase=self._mask_mode == self.MASK_ERASER)
        self._update_mask_overlay()

    def _paint_mask_line(self, point: QPointF) -> None:
        if self._mask is None or self._mask_last is None:
            return
        value = 0 if self._mask_mode == self.MASK_ERASER else 255
        radius = max(1, self._mask_brush_size // 2)
        cv2.line(self._mask, (round(self._mask_last.x()), round(self._mask_last.y())),
                 (round(point.x()), round(point.y())), value, radius * 2, cv2.LINE_8)
        self._mask_last = point
        self._update_mask_overlay()

    def _apply_mask_shape(self, start: QPointF, end: QPointF) -> None:
        if self._mask is None:
            return
        rect = QRectF(start, end).normalized()
        if rect.width() < 1 or rect.height() < 1:
            return
        self._push_mask_undo()
        p1 = (round(rect.left()), round(rect.top()))
        p2 = (round(rect.right()), round(rect.bottom()))
        if self._mask_mode == self.MASK_CIRCLE:
            center = (round(rect.center().x()), round(rect.center().y()))
            axes = (max(1, round(rect.width() / 2)), max(1, round(rect.height() / 2)))
            cv2.ellipse(self._mask, center, axes, 0, 0, 360, 255, -1)
        else:
            cv2.rectangle(self._mask, p1, p2, 255, -1)
        self._mask_history.future.clear()
        self._finish_mask_change()

    def _commit_mask_polygon(self) -> None:
        if self._mask is not None and len(self._mask_polygon) >= 3:
            self._push_mask_undo()
            points = np.array([(round(p.x()), round(p.y())) for p in self._mask_polygon])
            cv2.fillPoly(self._mask, [points.astype(np.int32)], 255)
            self._mask_history.future.clear()
            self._finish_mask_change()
        self._mask_polygon.clear()
        self._remove_mask_preview()

    def _push_mask_undo(self) -> None:
        if self._mask is not None:
            self._mask_history.past.append(self._mask.copy())
            self._mask_history.past = self._mask_history.past[-100:]

    def _finish_mask_change(self) -> None:
        self._update_mask_overlay()
        self.maskChanged.emit(self.mask())
        self.maskHistoryChanged.emit()

    def _update_mask_preview(self, point: QPointF) -> None:
        path = QPainterPath()
        if self._mask_polygon:
            path.moveTo(self._mask_polygon[0])
            for vertex in self._mask_polygon[1:]:
                path.lineTo(vertex)
            path.lineTo(point)
        elif self._mask_start is not None:
            rect = QRectF(self._mask_start, point).normalized()
            path.addEllipse(rect) if self._mask_mode == self.MASK_CIRCLE else path.addRect(rect)
        if self._mask_preview is None:
            self._mask_preview = QGraphicsPathItem()
            self._mask_preview.setPen(QPen(QColor("#D946EF"), 1.5))
            self._mask_preview.setBrush(QColor(217, 70, 239, 60))
            self._mask_preview.setZValue(60)
            self._mask_preview.setAcceptedMouseButtons(Qt.NoButton)
            self.scene().addItem(self._mask_preview)
        self._mask_preview.setPath(path)

    def _remove_mask_preview(self) -> None:
        if self._mask_preview is not None:
            self.scene().removeItem(self._mask_preview)
            self._mask_preview = None

    def _update_mask_overlay(self) -> None:
        if not self._mask_enabled or self._mask is None or not np.any(self._mask):
            if self._mask_item is not None:
                self.scene().removeItem(self._mask_item)
                self._mask_item = None
            self._mask_rgba = None
            return
        pixels = (self._mask > 0).astype(np.uint8)
        rgba = np.zeros((*pixels.shape, 4), dtype=np.uint8)
        rgba[..., :3] = (217, 70, 239)
        rgba[..., 3] = pixels * round(255 * self._mask_opacity / 100)
        self._mask_rgba = rgba
        image = QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0],
                       QImage.Format_RGBA8888)
        pixmap = QPixmap.fromImage(image.copy())
        if self._mask_item is None:
            self._mask_item = self.scene().addPixmap(pixmap)
            self._mask_item.setZValue(5)
            self._mask_item.setAcceptedMouseButtons(Qt.NoButton)
        else:
            self._mask_item.setPixmap(pixmap)
        self._mask_item.setVisible(self._mask_visible)



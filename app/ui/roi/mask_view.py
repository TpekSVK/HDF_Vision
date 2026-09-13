"""mask view canvas interaction."""
from __future__ import annotations
from app.services.mask_painting import brush_point
from app.ui.roi.history import EditHistory
import math
from typing import List, Optional, Tuple
import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem, QWidget
from app.ui.image_canvas import ImageView as _ImageView

from app.ui.roi.geometry import _clamp_point_to_rect, _ROI_COLOR, _MASK_COLOR

class _MaskView(_ImageView):
    """View handling mask painting with brush or polygon tools."""

    maskChanged = Signal(object)
    historyChanged = Signal()

    MODE_BRUSH_ADD = "brush_add"
    MODE_BRUSH_ERASE = "brush_erase"
    MODE_POLYGON = "polygon"
    MODE_SHAPE_CIRCLE = "shape_circle"
    MODE_SHAPE_RECTANGLE = "shape_rectangle"

    FILL_INSIDE = "inside"
    FILL_AROUND = "around"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._mask: Optional[np.ndarray] = None
        self._mask_item: Optional[QGraphicsPixmapItem] = None
        self._mask_rgba: Optional[np.ndarray] = None
        self._mode = self.MODE_BRUSH_ADD
        self._brush_radius = 24
        self._painting = False
        self._stroke_before: Optional[np.ndarray] = None
        self._last_point: Optional[QPointF] = None
        self._polygon_points: List[QPointF] = []
        self._polygon_item: Optional[QGraphicsPathItem] = None
        self._mask_history = EditHistory()
        self._shape_start: Optional[QPointF] = None
        self._circle_points: List[QPointF] = []
        self._shape_preview_item: Optional[QGraphicsPathItem] = None
        self._shape_fill_mode = self.FILL_INSIDE
        self._roi_overlay_rect: Optional[Tuple[int, int, int, int]] = None
        self._roi_overlay_item: Optional[QGraphicsRectItem] = None
        self._show_roi_overlay = False

    # ------------------------------------------------------------------
    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:  # type: ignore[override]
        super().set_pixmap(pixmap)
        if pixmap is None or pixmap.isNull():
            self._mask = None
        else:
            height = pixmap.height()
            width = pixmap.width()
            self._mask = np.zeros((height, width), dtype=np.uint8)
        self._mask_item = None
        self._mask_rgba = None
        self._mask_history.past.clear()
        self._mask_history.future.clear()
        self._polygon_points.clear()
        self._polygon_item = None
        self._shape_start = None
        self._circle_points.clear()
        self._shape_preview_item = None
        self._roi_overlay_item = None
        self._update_mask_item()
        self._update_roi_overlay()
        self.historyChanged.emit()

    def set_mode(self, mode: str) -> None:
        allowed = {
            self.MODE_BRUSH_ADD,
            self.MODE_BRUSH_ERASE,
            self.MODE_POLYGON,
            self.MODE_SHAPE_CIRCLE,
            self.MODE_SHAPE_RECTANGLE,
        }
        if mode not in allowed:
            return
        if self._mode == mode:
            return

        self.cancel_drawing()

        if self._mode == self.MODE_POLYGON:
            self._polygon_points.clear()
            self._remove_polygon_item()
        if self._mode == self.MODE_SHAPE_CIRCLE:
            self._circle_points.clear()
            self._remove_shape_preview()
        if self._mode == self.MODE_SHAPE_RECTANGLE:
            self._shape_start = None
            self._remove_shape_preview()

        self._mode = mode
        self._painting = False
        self._last_point = None

        if self._mode != self.MODE_POLYGON:
            self._polygon_points.clear()
            self._remove_polygon_item()
        if self._mode != self.MODE_SHAPE_CIRCLE:
            self._circle_points.clear()
        if self._mode != self.MODE_SHAPE_RECTANGLE:
            self._shape_start = None
        if self._mode not in (self.MODE_SHAPE_CIRCLE, self.MODE_SHAPE_RECTANGLE):
            self._remove_shape_preview()

    def set_fill_mode(self, fill_mode: str) -> None:
        if fill_mode not in (self.FILL_INSIDE, self.FILL_AROUND):
            return
        if self._shape_fill_mode == fill_mode:
            return
        self._shape_fill_mode = fill_mode

    def fill_mode(self) -> str:
        return self._shape_fill_mode

    def set_show_roi_overlay(self, show: bool) -> None:
        self._show_roi_overlay = bool(show)
        self._update_roi_overlay()

    def show_roi_overlay(self) -> bool:
        return self._show_roi_overlay

    def set_roi_overlay(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self._roi_overlay_rect = tuple(map(int, rect)) if rect is not None else None
        self._update_roi_overlay()

    def set_brush_radius(self, radius: int) -> None:
        self._brush_radius = max(1, int(radius))

    def mask(self) -> Optional[np.ndarray]:
        if self._mask is None:
            return None
        return self._mask.copy()

    def set_mask(self, mask: Optional[np.ndarray]) -> None:
        if mask is None:
            if self._mask is not None:
                self._mask.fill(0)
            self._mask_history.past.clear()
            self._mask_history.future.clear()
            self._update_mask_item()
            self._update_roi_overlay()
            self.maskChanged.emit(self.mask())
            self.historyChanged.emit()
            return

        if mask.ndim == 3:
            mask = mask[:, :, 0]
        mask = np.asarray(mask, dtype=np.uint8)
        if self._mask is None or self.scene_rect().isNull():
            self._mask = mask.copy()
        else:
            height = int(self.scene_rect().height())
            width = int(self.scene_rect().width())
            if mask.shape != (height, width):
                mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
            self._mask = mask.copy()
        self._mask_history.past.clear()
        self._mask_history.future.clear()
        self._update_mask_item()
        self._update_roi_overlay()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def clear_mask(self) -> None:
        if self._mask is None or not np.any(self._mask):
            return
        self._push_undo()
        self._mask.fill(0)
        self._mask_history.future.clear()
        self._update_mask_item()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def undo(self) -> None:
        if not self._mask_history.past:
            return
        current = self._mask.copy() if self._mask is not None else None
        previous = self._mask_history.past.pop()
        if current is not None:
            self._mask_history.future.append(current)
        self._mask = previous.copy()
        self._update_mask_item()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def redo(self) -> None:
        if not self._mask_history.future:
            return
        current = self._mask.copy() if self._mask is not None else None
        next_mask = self._mask_history.future.pop()
        if current is not None:
            self._mask_history.past.append(current)
        self._mask = next_mask.copy()
        self._update_mask_item()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def can_undo(self) -> bool:
        return bool(self._mask_history.past)

    def can_redo(self) -> bool:
        return bool(self._mask_history.future)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.is_pan_gesture(event) or not self.can_draw():
            super().mousePressEvent(event)
            return
        if self._mask is None or self.scene_rect().isNull():
            super().mousePressEvent(event)
            return

        if event.button() == Qt.LeftButton:
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
            if self._mode == self.MODE_SHAPE_CIRCLE:
                self._circle_points.append(scene_pos)
                if len(self._circle_points) >= 3:
                    self._apply_three_point_circle()
                    self._circle_points.clear()
                    self._remove_shape_preview()
                else:
                    self._update_shape_preview(None)
                event.accept()
                return
            if self._mode == self.MODE_SHAPE_RECTANGLE:
                self._shape_start = scene_pos
                self._update_shape_preview(scene_pos)
                event.accept()
                return
            if self._mode == self.MODE_POLYGON:
                self._polygon_points.append(scene_pos)
                self._update_polygon_preview(scene_pos)
                event.accept()
                return
            self._stroke_before = self._mask.copy()
            self._painting = True
            self._last_point = scene_pos
            self._apply_brush_point(scene_pos)
            event.accept()
            return

        if event.button() == Qt.RightButton and self._mode == self.MODE_POLYGON:
            # Cancel polygon drawing
            self._polygon_points.clear()
            self._remove_polygon_item()
            event.accept()
            return

        if event.button() == Qt.RightButton and self._mode == self.MODE_SHAPE_CIRCLE:
            self._circle_points.clear()
            self._remove_shape_preview()
            event.accept()
            return

        if event.button() == Qt.RightButton and self._mode == self.MODE_SHAPE_RECTANGLE:
            self._shape_start = None
            self._remove_shape_preview()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._panning or self._space_pressed:
            super().mouseMoveEvent(event)
            return
        if self._painting and self._mask is not None:
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
            self._apply_brush_segment(scene_pos)
            event.accept()
            return

        if self._mode == self.MODE_SHAPE_CIRCLE and self._circle_points:
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
            self._update_shape_preview(scene_pos)
            event.accept()
            return

        if self._mode == self.MODE_SHAPE_RECTANGLE and self._shape_start is not None:
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
            self._update_shape_preview(scene_pos)
            event.accept()
            return

        if self._mode == self.MODE_POLYGON and self._polygon_points:
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
            self._update_polygon_preview(scene_pos)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            self._mode == self.MODE_SHAPE_RECTANGLE
            and self._shape_start is not None
            and event.button() == Qt.LeftButton
        ):
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
            start = self._shape_start
            self._shape_start = None
            self._remove_shape_preview()
            self._apply_rectangle(start, scene_pos)
            event.accept()
            return

        if self._painting and event.button() == Qt.LeftButton:
            self._painting = False
            self._last_point = None
            if self._stroke_before is not None:
                self._mask_history.past.append(self._stroke_before)
                self._mask_history.past = self._mask_history.past[-100:]
                self._mask_history.future.clear()
                self._stroke_before = None
            self.maskChanged.emit(self.mask())
            self.historyChanged.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.can_draw():
            super().mouseDoubleClickEvent(event)
            return
        if self._mode == self.MODE_POLYGON and self._polygon_points and event.button() == Qt.LeftButton:
            if len(self._polygon_points) >= 3:
                self._push_undo()
                self._mask_history.future.clear()
                pts = np.array([[p.x(), p.y()] for p in self._polygon_points], dtype=np.float32)
                mask = np.zeros_like(self._mask)
                cv2.fillPoly(mask, [pts.astype(np.int32)], 255)
                if self._mask is None:
                    self._mask = mask
                else:
                    self._mask = np.maximum(self._mask, mask)
                self._update_mask_item()
                self.maskChanged.emit(self.mask())
                self.historyChanged.emit()
            self._polygon_points.clear()
            self._remove_polygon_item()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    def cancel_drawing(self) -> None:
        if self._painting and self._stroke_before is not None:
            self._mask = self._stroke_before
            self._update_mask_item()
        self._stroke_before = None
        self._painting = False
        self._last_point = None
        self._polygon_points.clear()
        self._remove_polygon_item()
        self._shape_start = None
        self._circle_points.clear()
        self._remove_shape_preview()

    def _push_undo(self) -> None:
        if self._mask is None:
            return
        self._mask_history.past.append(self._mask.copy())
        if len(self._mask_history.past) > 100:
            self._mask_history.past.pop(0)

    def _apply_brush_point(self, point: QPointF) -> None:
        if self._mask is None:
            return
        brush_point(self._mask, point.x(), point.y(), self._brush_radius,
                    erase=self._mode != self.MODE_BRUSH_ADD)
        self._update_mask_item()

    def _apply_brush_segment(self, point: QPointF) -> None:
        if self._last_point is None:
            self._apply_brush_point(point)
            self._last_point = point
            return
        start = self._last_point
        end = point
        steps = int(max(abs(end.x() - start.x()), abs(end.y() - start.y())) / max(1, self._brush_radius))
        steps = max(1, steps)
        for i in range(1, steps + 1):
            t = i / steps
            interp = QPointF(start.x() + (end.x() - start.x()) * t, start.y() + (end.y() - start.y()) * t)
            self._apply_brush_point(interp)
        self._last_point = point

    def _remove_polygon_item(self) -> None:
        if self._polygon_item is not None:
            self.scene().removeItem(self._polygon_item)
            self._polygon_item = None

    def _update_polygon_preview(self, preview: Optional[QPointF] = None) -> None:
        if not self._polygon_points:
            self._remove_polygon_item()
            return
        path = QPainterPath(self._polygon_points[0])
        for point in self._polygon_points[1:]:
            path.lineTo(point)
        if preview is not None:
            path.lineTo(preview)
        if self._polygon_item is None:
            pen = QPen(QColor(_MASK_COLOR))
            pen.setWidthF(1.5)
            self._polygon_item = QGraphicsPathItem(path)
            self._polygon_item.setPen(pen)
            self._polygon_item.setBrush(Qt.transparent)
            self._polygon_item.setZValue(90)
            self.scene().addItem(self._polygon_item)
        else:
            self._polygon_item.setPath(path)

    def _remove_shape_preview(self) -> None:
        if self._shape_preview_item is not None:
            self.scene().removeItem(self._shape_preview_item)
            self._shape_preview_item = None

    def _rectangle_rect_from_points(self, start: QPointF, end: QPointF) -> QRectF:
        rect = QRectF(start, end)
        return rect.normalized()

    def _update_shape_preview(self, current: Optional[QPointF]) -> None:
        if self.scene_rect().isNull():
            return
        if self._mode == self.MODE_SHAPE_CIRCLE:
            self._update_circle_preview(current)
            return
        if self._mode != self.MODE_SHAPE_RECTANGLE or self._shape_start is None or current is None:
            self._remove_shape_preview()
            return
        rect = self._rectangle_rect_from_points(self._shape_start, current)
        if rect.isNull() or rect.width() < 1 or rect.height() < 1:
            self._remove_shape_preview()
            return
        path = QPainterPath()
        path.addRect(rect)
        if self._shape_preview_item is None:
            pen = QPen(QColor(_MASK_COLOR))
            pen.setWidthF(1.5)
            preview = QGraphicsPathItem(path)
            preview.setPen(pen)
            fill_color = QColor(_MASK_COLOR)
            fill_color.setAlpha(60)
            preview.setBrush(fill_color)
            preview.setZValue(90)
            preview.setAcceptedMouseButtons(Qt.NoButton)
            self.scene().addItem(preview)
            self._shape_preview_item = preview
        else:
            self._shape_preview_item.setPath(path)
        self._shape_preview_item.setVisible(True)

    def _update_circle_preview(self, current: Optional[QPointF]) -> None:
        if len(self._circle_points) < 2:
            self._remove_shape_preview()
            return
        points = self._circle_points[:]
        if current is not None and len(points) == 2:
            points.append(current)
        if len(points) < 3:
            self._remove_shape_preview()
            return
        circle = self._circle_from_points(points[0], points[1], points[2])
        if circle is None:
            self._remove_shape_preview()
            return
        center, radius = circle
        if radius <= 0:
            self._remove_shape_preview()
            return
        rect = QRectF(
            center.x() - radius,
            center.y() - radius,
            radius * 2.0,
            radius * 2.0,
        ).normalized()
        if rect.isNull():
            self._remove_shape_preview()
            return
        path = QPainterPath()
        path.addEllipse(rect)
        if self._shape_preview_item is None:
            pen = QPen(QColor(_MASK_COLOR))
            pen.setWidthF(1.5)
            preview = QGraphicsPathItem(path)
            preview.setPen(pen)
            fill_color = QColor(_MASK_COLOR)
            fill_color.setAlpha(60)
            preview.setBrush(fill_color)
            preview.setZValue(90)
            preview.setAcceptedMouseButtons(Qt.NoButton)
            self.scene().addItem(preview)
            self._shape_preview_item = preview
        else:
            self._shape_preview_item.setPath(path)
        self._shape_preview_item.setVisible(True)

    def _apply_rectangle(self, start: QPointF, end: QPointF) -> None:
        if self._mask is None:
            return
        rect = self._rectangle_rect_from_points(start, end)
        if rect.isNull() or rect.width() < 1 or rect.height() < 1:
            return

        height, width = self._mask.shape
        x0 = max(0, min(int(np.floor(rect.left())), width - 1))
        y0 = max(0, min(int(np.floor(rect.top())), height - 1))
        x1 = max(0, min(int(np.ceil(rect.right())) - 1, width - 1))
        y1 = max(0, min(int(np.ceil(rect.bottom())) - 1, height - 1))
        if x1 <= x0 or y1 <= y0:
            return

        shape_mask = np.zeros_like(self._mask)

        if self._shape_fill_mode == self.FILL_AROUND:
            thickness = max(1, int(self._brush_radius))
            outer_x0 = max(0, x0 - thickness)
            outer_y0 = max(0, y0 - thickness)
            outer_x1 = min(width - 1, x1 + thickness)
            outer_y1 = min(height - 1, y1 + thickness)

            if outer_x1 <= outer_x0 or outer_y1 <= outer_y0:
                return

            cv2.rectangle(shape_mask, (outer_x0, outer_y0), (outer_x1, outer_y1), 255, -1)

            inner = np.zeros_like(shape_mask)
            cv2.rectangle(inner, (x0, y0), (x1, y1), 255, -1)
            cv2.subtract(shape_mask, inner, shape_mask)
        else:
            cv2.rectangle(shape_mask, (x0, y0), (x1, y1), 255, -1)

        if not np.any(shape_mask):
            return

        self._push_undo()
        self._mask_history.future.clear()
        self._mask = np.maximum(self._mask, shape_mask)
        self._update_mask_item()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def _apply_three_point_circle(self) -> None:
        if self._mask is None or len(self._circle_points) < 3:
            return
        circle = self._circle_from_points(
            self._circle_points[0],
            self._circle_points[1],
            self._circle_points[2],
        )
        if circle is None:
            return
        center, radius = circle
        radius_int = int(round(radius))
        if radius_int <= 0:
            return
        center_tuple = (
            int(round(center.x())),
            int(round(center.y())),
        )
        shape_mask = np.zeros_like(self._mask)

        if self._shape_fill_mode == self.FILL_AROUND:
            thickness = max(1, int(self._brush_radius))
            outer_radius = radius_int + thickness
            cv2.circle(shape_mask, center_tuple, outer_radius, 255, -1)

            inner = np.zeros_like(shape_mask)
            cv2.circle(inner, center_tuple, radius_int, 255, -1)
            cv2.subtract(shape_mask, inner, shape_mask)
        else:
            cv2.circle(shape_mask, center_tuple, radius_int, 255, -1)

        if not np.any(shape_mask):
            return

        self._push_undo()
        self._mask_history.future.clear()
        self._mask = np.maximum(self._mask, shape_mask)
        self._update_mask_item()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def _circle_from_points(
        self, p1: QPointF, p2: QPointF, p3: QPointF
    ) -> Optional[Tuple[QPointF, float]]:
        x1, y1 = p1.x(), p1.y()
        x2, y2 = p2.x(), p2.y()
        x3, y3 = p3.x(), p3.y()

        denom = 2.0 * (
            x1 * (y2 - y3)
            + x2 * (y3 - y1)
            + x3 * (y1 - y2)
        )
        if abs(denom) < 1e-6:
            return None

        x1_sq = x1 * x1 + y1 * y1
        x2_sq = x2 * x2 + y2 * y2
        x3_sq = x3 * x3 + y3 * y3

        cx = (
            x1_sq * (y2 - y3)
            + x2_sq * (y3 - y1)
            + x3_sq * (y1 - y2)
        ) / denom
        cy = (
            x1_sq * (x3 - x2)
            + x2_sq * (x1 - x3)
            + x3_sq * (x2 - x1)
        ) / denom

        if not math.isfinite(cx) or not math.isfinite(cy):
            return None

        radius = math.hypot(cx - x1, cy - y1)
        if not math.isfinite(radius):
            return None

        return QPointF(cx, cy), radius

    def _update_roi_overlay(self) -> None:
        if self.scene() is None:
            return
        if self._roi_overlay_item is not None and self._roi_overlay_item.scene() is not self.scene():
            self._roi_overlay_item = None

        if not self._show_roi_overlay or self._roi_overlay_rect is None:
            if self._roi_overlay_item is not None:
                self._roi_overlay_item.setVisible(False)
            return

        left, top, width, height = self._roi_overlay_rect
        if width <= 0 or height <= 0:
            if self._roi_overlay_item is not None:
                self._roi_overlay_item.setVisible(False)
            return

        rect = QRectF(float(left), float(top), float(width), float(height))
        if rect.isNull():
            if self._roi_overlay_item is not None:
                self._roi_overlay_item.setVisible(False)
            return

        if self.scene_rect().isNull():
            return

        fill_color = QColor(_ROI_COLOR)
        fill_color.setAlpha(60)
        pen = QPen(QColor(_ROI_COLOR))
        pen.setWidthF(1.5)
        pen.setCosmetic(True)

        if self._roi_overlay_item is None:
            item = QGraphicsRectItem(rect)
            item.setPen(pen)
            item.setBrush(fill_color)
            item.setZValue(85)
            item.setAcceptedMouseButtons(Qt.NoButton)
            self.scene().addItem(item)
            self._roi_overlay_item = item
        else:
            self._roi_overlay_item.setRect(rect)
            self._roi_overlay_item.setPen(pen)
            self._roi_overlay_item.setBrush(fill_color)
            self._roi_overlay_item.setVisible(True)
        if self._roi_overlay_item is not None:
            self._roi_overlay_item.setVisible(True)

    def _update_mask_item(self) -> None:
        if self._mask is None:
            if self._mask_item is not None:
                self.scene().removeItem(self._mask_item)
                self._mask_item = None
            self._mask_rgba = None
            return
        if not np.any(self._mask):
            if self._mask_item is not None:
                self.scene().removeItem(self._mask_item)
                self._mask_item = None
            self._mask_rgba = None
            return
        mask = (self._mask > 0).astype(np.uint8)
        rgba = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
        rgba[..., 0] = _MASK_COLOR.red()
        rgba[..., 1] = _MASK_COLOR.green()
        rgba[..., 2] = _MASK_COLOR.blue()
        rgba[..., 3] = mask * _MASK_COLOR.alpha()
        self._mask_rgba = rgba
        image = QImage(
            rgba.data,
            rgba.shape[1],
            rgba.shape[0],
            rgba.strides[0],
            QImage.Format_RGBA8888,
        )
        pixmap = QPixmap.fromImage(image.copy())
        if self._mask_item is None:
            self._mask_item = self.scene().addPixmap(pixmap)
            self._mask_item.setZValue(80)
        else:
            self._mask_item.setPixmap(pixmap)



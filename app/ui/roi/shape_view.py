"""shape view canvas interaction."""
from __future__ import annotations
import math
from typing import List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsItem, QGraphicsRectItem, QWidget
from app.ui.image_canvas import InteractionMode

from app.ui.roi.geometry import RoiHandle, _clamp_point_to_rect, _ROI_COLOR, _OVERLAY_COLOR
from app.ui.roi.history import EditHistory
from app.ui.roi.rectangle_view import _ROIView

class _ShapeROIView(_ROIView):
    """ROI view adding ellipse and vertex-editable polygon geometry."""

    EDGE_HIT_RADIUS = 8.0
    ROTATION_HANDLE_OFFSET = 26.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._shape = "rect"
        self._rotated_rect = False
        self._draw_shape = "rect"
        self._points: List[Tuple[int, int]] = []
        self._draft_points: List[QPointF] = []
        self._draft_cursor: Optional[QPointF] = None
        self._vertex_items: List[QGraphicsRectItem] = []
        self._selected_vertex: Optional[int] = None
        self._polygon_origin: Optional[List[Tuple[int, int]]] = None
        self._polygon_edit_start = QPointF()
        self._polygon_edit_vertex: Optional[int] = None
        self._rotation_handle_item: Optional[QGraphicsRectItem] = None
        self._rotation_connector_item: Optional[QGraphicsPathItem] = None
        self._rotation_handle_center: Optional[QPointF] = None
        self._rotation_origin: Optional[dict] = None
        self._rotation_points: List[Tuple[float, float]] = []
        self._rotation_center = QPointF()
        self._rotation_start_angle = 0.0
        self._rotated_rect_edit_origin: Optional[dict] = None
        self._rotated_rect_edit_handle: Optional[RoiHandle] = None
        self._draw_before: Optional[dict] = None
        self._history = EditHistory()

    def set_draw_shape(self, shape: str) -> None:
        self.cancel_drawing()
        self._draw_shape = shape
        self.set_interaction_mode(InteractionMode.DRAW)

    def set_interaction_mode(self, mode: InteractionMode) -> None:
        super().set_interaction_mode(mode)
        if hasattr(self, "_shape"):
            self._render_shape()

    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        self.cancel_drawing()
        # Polygon handles are also owned by the scene cleared in the base view.
        self._vertex_items.clear()
        self._points = []
        self._draft_points = []
        self._draft_cursor = None
        self._selected_vertex = None
        self._polygon_origin = None
        self._polygon_edit_vertex = None
        self._rotation_handle_item = None
        self._rotation_connector_item = None
        self._rotation_handle_center = None
        self._rotation_origin = None
        self._rotation_points = []
        self._rotated_rect_edit_origin = None
        self._rotated_rect_edit_handle = None
        super().set_pixmap(pixmap)
        self._shape, self._points = "rect", []
        self._rotated_rect = False
        self._draft_points, self._vertex_items = [], []
        self._history.past.clear(); self._history.future.clear()

    def set_roi_locked(self, locked: bool) -> None:
        super().set_roi_locked(locked)
        for item in self._vertex_items:
            item.setVisible(not self._roi_locked)

    def _apply_roi(self, rect, push_history: bool) -> None:
        if push_history:
            self._history.past.append(self._snapshot())
        self._roi_rect = tuple(int(round(v)) for v in rect) if rect is not None else None
        if rect is None:
            self._points = []
            self.set_roi_locked(False)
        self._render_shape()
        self.roiChanged.emit(self.roi())

    def _update_roi_item(self, rect: QRectF) -> None:
        self._preview_rect = rect
        if self._roi_item is not None:
            self.scene().removeItem(self._roi_item); self._roi_item = None
        path = QPainterPath()
        path.addEllipse(rect) if self._shape == "ellipse" else path.addRect(rect)
        pen = QPen(QColor(_ROI_COLOR)); pen.setWidthF(2.0)
        item = QGraphicsPathItem(path); item.setPen(pen); item.setBrush(Qt.transparent)
        item.setZValue(100); self.scene().addItem(item); self._roi_item = item  # type: ignore[assignment]
        self._update_handles(rect)

    def _restore_roi_preview(self) -> None:
        self._preview_rect = None
        self._render_shape()

    def roi_data(self) -> dict:
        if self._shape == "polygon":
            result = {"shape": "polygon", "points": [list(point) for point in self._points]}
            if self._rotated_rect and len(self._points) == 4:
                result["rotated_rect"] = True
            return result
        rect = self.roi()
        if rect is None:
            return {}
        x, y, w, h = rect
        result = {"x": x, "y": y, "w": w, "h": h}
        if self._shape == "ellipse":
            result["shape"] = "ellipse"
        return result

    def set_roi_data(self, value) -> None:
        self.cancel_drawing()
        data = dict(value or {}) if isinstance(value, dict) else {}
        if data.get("shape") == "polygon":
            points = [(int(p[0]), int(p[1])) for p in data.get("points", [])]
            self._shape = "polygon"
            self._points = points if len(points) >= 3 else []
            self._rotated_rect = bool(data.get("rotated_rect", False)) and len(self._points) == 4
            self._roi_rect = self._polygon_bounds(self._points) if self._points else None
            self._render_shape()
            self.roiChanged.emit(self.roi())
        else:
            self._shape = "ellipse" if data.get("shape") == "ellipse" else "rect"
            self._points = []
            self._rotated_rect = False
            rect = None
            if {"x", "y", "w", "h"}.issubset(data):
                rect = tuple(int(data[key]) for key in ("x", "y", "w", "h"))
            super().set_roi(rect)
            self._render_shape()
        self._history.past.clear()
        self._history.future.clear()
        self.historyChanged.emit()

    def set_roi(self, rect) -> None:
        if isinstance(rect, dict):
            self.set_roi_data(rect)
            return
        self._shape = "rect"
        self._points = []
        self._rotated_rect = False
        super().set_roi(rect)

    def can_undo(self) -> bool:
        return bool(self._history.past)

    def can_redo(self) -> bool:
        return bool(self._history.future)

    def _snapshot(self) -> dict:
        return self.roi_data()

    def _record(self, before: dict) -> None:
        if before == self.roi_data():
            return
        self._history.record(before)
        self.historyChanged.emit()

    def undo(self) -> None:
        self.cancel_drawing()
        if not self._history.past:
            return
        current = self._snapshot()
        previous = self._history.undo(current)
        self._restore_snapshot(previous)
        self.historyChanged.emit()

    def redo(self) -> None:
        self.cancel_drawing()
        if not self._history.future:
            return
        current = self._snapshot()
        following = self._history.redo(current)
        self._restore_snapshot(following)
        self.historyChanged.emit()

    def _restore_snapshot(self, data: dict) -> None:
        # set_roi_data clears its lists in place; preserve independent lists.
        history, redo = list(self._history.past), list(self._history.future)
        self.set_roi_data(data)
        self._history.past, self._history.future = history, redo

    def reset_roi(self) -> None:
        if self._roi_locked:
            return
        before = self._snapshot()
        self._shape, self._points, self._roi_rect = "rect", [], None
        self._rotated_rect = False
        self._render_shape()
        self._record(before)
        self.roiChanged.emit(None)

    def edit_roi(self, rect) -> None:
        if self._shape == "polygon" or self._roi_locked:
            return
        before = self._snapshot()
        self._roi_rect = self._clamp_integer_rect(rect)
        self._render_shape()
        self._record(before)
        self.roiChanged.emit(self.roi())

    def cancel_drawing(self) -> None:
        if self._rotation_origin is not None:
            self._restore_rotation_origin()
        if self._rotated_rect_edit_origin is not None:
            self._restore_rotated_rect_origin()
        if hasattr(self, "_draft_points"):
            self._draft_points = []
            self._draft_cursor = None
            self._polygon_origin = None
            self._polygon_edit_vertex = None
        super().cancel_drawing()
        if hasattr(self, "_shape"):
            self._render_shape()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.is_pan_gesture(event):
            super().mousePressEvent(event)
            return
        if self._roi_locked:
            event.accept()
            return
        pos = self.mapToScene(event.position().toPoint())
        if (
            event.button() == Qt.LeftButton
            and self.interaction_mode() == InteractionMode.SELECT
            and self._hit_rotation_handle(event.position().toPoint())
        ):
            self._begin_rotation(pos)
            event.accept()
            return
        if (
            event.button() == Qt.LeftButton
            and self.interaction_mode() == InteractionMode.SELECT
            and self._rotated_rect
        ):
            resize_handle = self._hit_rotated_rect_handle(event.position().toPoint())
            if resize_handle is not None:
                self._begin_rotated_rect_resize(resize_handle)
                event.accept()
                return
        if event.button() == Qt.LeftButton and self.interaction_mode() == InteractionMode.DRAW:
            if not self.scene_rect().contains(pos):
                event.accept(); return
            if self._draw_shape == "polygon":
                self._draft_points.append(_clamp_point_to_rect(pos, self.scene_rect()))
                self._draft_cursor = pos
                self._render_shape()
                event.accept(); return
            self._draw_before = self._snapshot()
            self._shape = self._draw_shape
            self._rotated_rect = False
        if (event.button() == Qt.LeftButton and self.interaction_mode() == InteractionMode.SELECT
                and self._shape == "polygon" and self._points):
            vertex = self._hit_vertex(event.position().toPoint())
            if vertex is not None or self._polygon_path().contains(pos):
                self._selected_vertex = vertex
                self._polygon_edit_vertex = vertex
                self._polygon_origin = list(self._points)
                self._polygon_edit_start = pos
                self._render_shape()
                event.accept(); return
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = _clamp_point_to_rect(self.mapToScene(event.position().toPoint()), self.scene_rect())
        if self._rotation_origin is not None:
            self._update_rotation(pos)
            event.accept()
            return
        if self._rotated_rect_edit_origin is not None:
            self._update_rotated_rect_resize(pos)
            event.accept()
            return
        if self._draft_points:
            self._draft_cursor = pos
            self._render_shape(); event.accept(); return
        if self._polygon_origin is not None:
            if self._polygon_edit_vertex is not None:
                points = list(self._polygon_origin)
                points[self._polygon_edit_vertex] = (int(round(pos.x())), int(round(pos.y())))
                self._points = points
            else:
                delta = pos - self._polygon_edit_start
                self._points = self._move_polygon(self._polygon_origin, delta)
            self._roi_rect = self._polygon_bounds(self._points)
            self._render_shape(); event.accept(); return
        if (
            event.buttons() == Qt.NoButton
            and self.interaction_mode() == InteractionMode.SELECT
            and self._hit_rotation_handle(event.position().toPoint())
        ):
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if event.buttons() == Qt.NoButton and self._shape == "polygon" and self._points:
            if self._rotated_rect:
                handle = self._hit_rotated_rect_handle(event.position().toPoint())
                self.setCursor(
                    self._handle_cursor(handle)
                    if handle is not None
                    else (Qt.SizeAllCursor if self._polygon_path().contains(pos) else Qt.ArrowCursor)
                )
                event.accept()
                return
            vertex = self._hit_vertex(event.position().toPoint())
            self.setCursor(Qt.PointingHandCursor if vertex is not None else
                           (Qt.SizeAllCursor if self._polygon_path().contains(pos) else Qt.ArrowCursor))
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._rotation_origin is not None and event.button() == Qt.LeftButton:
            before = self._rotation_origin
            self._clear_rotation_state()
            if before != self._snapshot():
                self._render_shape()
                self._record(before)
                self.roiChanged.emit(self.roi())
            else:
                self._render_shape()
            event.accept()
            return
        if self._rotated_rect_edit_origin is not None and event.button() == Qt.LeftButton:
            before = self._rotated_rect_edit_origin
            self._rotated_rect_edit_origin = None
            self._rotated_rect_edit_handle = None
            if before != self._snapshot():
                self._render_shape()
                self._record(before)
                self.roiChanged.emit(self.roi())
            else:
                self._render_shape()
            event.accept()
            return
        if self._polygon_origin is not None and event.button() == Qt.LeftButton:
            before = {"shape": "polygon", "points": [list(p) for p in self._polygon_origin]}
            if self._rotated_rect:
                before["rotated_rect"] = True
            self._polygon_origin = None
            self._polygon_edit_vertex = None
            self._record(before)
            self.roiChanged.emit(self.roi())
            event.accept(); return
        drawing_box = self._drawing
        before = self._draw_before if drawing_box and self._draw_before is not None else self._snapshot()
        super().mouseReleaseEvent(event)
        if drawing_box and self._roi_rect is not None:
            self._shape = self._draw_shape
            self._points = []
            self._rotated_rect = False
            self._render_shape()
            self._record(before)
        self._draw_before = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._draft_points:
            self._finish_polygon()
            event.accept(); return
        if (event.button() == Qt.LeftButton and self.interaction_mode() == InteractionMode.SELECT
                and self._shape == "polygon" and not self._roi_locked):
            if self._rotated_rect:
                event.accept(); return
            pos = self.mapToScene(event.position().toPoint())
            segment = self._nearest_segment(pos)
            if segment is not None:
                before = self._snapshot()
                self._polygon_origin = None
                self._polygon_edit_vertex = None
                self._points.insert(segment + 1, (int(round(pos.x())), int(round(pos.y()))))
                self._selected_vertex = segment + 1
                self._roi_rect = self._polygon_bounds(self._points)
                self._render_shape(); self._record(before); self.roiChanged.emit(self.roi())
                event.accept(); return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._draft_points:
            self._finish_polygon(); event.accept(); return
        if event.key() == Qt.Key_Escape and self._draft_points:
            self._draft_points = []; self._draft_cursor = None; self._render_shape()
            event.accept(); return
        if (event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._shape == "polygon"
                and self._selected_vertex is not None and len(self._points) > 3 and not self._roi_locked):
            if self._rotated_rect:
                event.accept(); return
            before = self._snapshot(); self._points.pop(self._selected_vertex)
            self._selected_vertex = None; self._roi_rect = self._polygon_bounds(self._points)
            self._render_shape(); self._record(before); self.roiChanged.emit(self.roi())
            event.accept(); return
        directions = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
                      Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if (event.key() in directions and self._shape == "polygon" and self._points
                and self.interaction_mode() == InteractionMode.SELECT and not self._roi_locked
                and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            before = self._snapshot(); dx, dy = directions[event.key()]
            step = 10 if event.modifiers() & Qt.ShiftModifier else 1
            if self._selected_vertex is None:
                self._points = self._move_polygon(self._points, QPointF(dx * step, dy * step))
            else:
                x, y = self._points[self._selected_vertex]
                p = _clamp_point_to_rect(QPointF(x + dx * step, y + dy * step), self.scene_rect())
                self._points[self._selected_vertex] = (int(round(p.x())), int(round(p.y())))
            self._roi_rect = self._polygon_bounds(self._points)
            self._render_shape(); self._record(before); self.roiChanged.emit(self.roi())
            event.accept(); return
        super().keyPressEvent(event)

    def _rotation_corners(self) -> List[Tuple[float, float]]:
        if self._shape == "polygon" and self._rotated_rect and len(self._points) == 4:
            return [(float(x), float(y)) for x, y in self._points]
        if self._shape != "rect" or self._roi_rect is None:
            return []
        rect = QRectF(*self._roi_rect)
        return [
            (rect.left(), rect.top()),
            (rect.right(), rect.top()),
            (rect.right(), rect.bottom()),
            (rect.left(), rect.bottom()),
        ]

    def _rotated_rect_handle_centers(self) -> dict[RoiHandle, QPointF]:
        corners = self._rotation_corners()
        if len(corners) != 4:
            return {}
        points = [QPointF(*point) for point in corners]
        return {
            RoiHandle.TOP_LEFT: points[0],
            RoiHandle.TOP: QPointF(
                (points[0].x() + points[1].x()) / 2.0,
                (points[0].y() + points[1].y()) / 2.0,
            ),
            RoiHandle.TOP_RIGHT: points[1],
            RoiHandle.RIGHT: QPointF(
                (points[1].x() + points[2].x()) / 2.0,
                (points[1].y() + points[2].y()) / 2.0,
            ),
            RoiHandle.BOTTOM_RIGHT: points[2],
            RoiHandle.BOTTOM: QPointF(
                (points[2].x() + points[3].x()) / 2.0,
                (points[2].y() + points[3].y()) / 2.0,
            ),
            RoiHandle.BOTTOM_LEFT: points[3],
            RoiHandle.LEFT: QPointF(
                (points[3].x() + points[0].x()) / 2.0,
                (points[3].y() + points[0].y()) / 2.0,
            ),
        }

    def _hit_rotated_rect_handle(self, view_pos) -> Optional[RoiHandle]:
        for handle, center in self._rotated_rect_handle_centers().items():
            mapped = self.mapFromScene(center)
            if (
                abs(mapped.x() - view_pos.x()) <= self.HANDLE_HIT_RADIUS
                and abs(mapped.y() - view_pos.y()) <= self.HANDLE_HIT_RADIUS
            ):
                return handle
        return None

    def _begin_rotated_rect_resize(self, handle: RoiHandle) -> None:
        if len(self._points) != 4:
            return
        self._rotated_rect_edit_origin = self._snapshot()
        self._rotated_rect_edit_handle = handle
        self._selected_vertex = None

    def _restore_rotated_rect_origin(self) -> None:
        origin = self._rotated_rect_edit_origin
        self._rotated_rect_edit_origin = None
        self._rotated_rect_edit_handle = None
        if origin is None:
            return
        self._shape = "polygon"
        self._points = [tuple(map(int, point)) for point in origin.get("points", [])]
        self._rotated_rect = bool(origin.get("rotated_rect", False)) and len(self._points) == 4
        self._roi_rect = self._polygon_bounds(self._points) if self._points else None
        self._render_shape()

    def _update_rotated_rect_resize(self, point: QPointF) -> None:
        handle = self._rotated_rect_edit_handle
        corners = self._rotation_corners()
        if handle is None or len(corners) != 4:
            return
        p0, p1, _, p3 = [QPointF(*corner) for corner in corners]
        axis_u = p1 - p0
        axis_v = p3 - p0
        width, height = math.hypot(axis_u.x(), axis_u.y()), math.hypot(axis_v.x(), axis_v.y())
        if width < 1e-6 or height < 1e-6:
            return
        unit_u = QPointF(axis_u.x() / width, axis_u.y() / width)
        unit_v = QPointF(axis_v.x() / height, axis_v.y() / height)
        center = QPointF(
            sum(corner[0] for corner in corners) / 4.0,
            sum(corner[1] for corner in corners) / 4.0,
        )
        min_u, max_u = -width / 2.0, width / 2.0
        min_v, max_v = -height / 2.0, height / 2.0
        rel = point - center
        projected_u = rel.x() * unit_u.x() + rel.y() * unit_u.y()
        projected_v = rel.x() * unit_v.x() + rel.y() * unit_v.y()
        minimum = self.MIN_ROI_SIZE / 2.0
        if handle in (RoiHandle.TOP_LEFT, RoiHandle.LEFT, RoiHandle.BOTTOM_LEFT):
            min_u = min(projected_u, max_u - minimum)
        if handle in (RoiHandle.TOP_RIGHT, RoiHandle.RIGHT, RoiHandle.BOTTOM_RIGHT):
            max_u = max(projected_u, min_u + minimum)
        if handle in (RoiHandle.TOP_LEFT, RoiHandle.TOP, RoiHandle.TOP_RIGHT):
            min_v = min(projected_v, max_v - minimum)
        if handle in (RoiHandle.BOTTOM_LEFT, RoiHandle.BOTTOM, RoiHandle.BOTTOM_RIGHT):
            max_v = max(projected_v, min_v + minimum)
        resized = []
        for u, v in ((min_u, min_v), (max_u, min_v), (max_u, max_v), (min_u, max_v)):
            resized.append((
                int(round(center.x() + unit_u.x() * u + unit_v.x() * v)),
                int(round(center.y() + unit_u.y() * u + unit_v.y() * v)),
            ))
        if not all(self.scene_rect().contains(QPointF(x, y)) for x, y in resized):
            return
        self._points = resized
        self._roi_rect = self._polygon_bounds(resized)
        self._render_shape()

    def _rotation_handle_geometry(
        self,
    ) -> Optional[Tuple[QPointF, QPointF, QPointF]]:
        corners = self._rotation_corners()
        if len(corners) != 4:
            return None
        start = QPointF(*corners[0])
        end = QPointF(*corners[1])
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return None
        center = QPointF(
            sum(x for x, _ in corners) / len(corners),
            sum(y for _, y in corners) / len(corners),
        )
        midpoint = QPointF((start.x() + end.x()) / 2.0, (start.y() + end.y()) / 2.0)
        offset = self.ROTATION_HANDLE_OFFSET / max(self.zoom(), 0.001)
        normal_a = QPointF(-dy / length * offset, dx / length * offset)
        normal_b = QPointF(-normal_a.x(), -normal_a.y())
        candidate_a = midpoint + normal_a
        candidate_b = midpoint + normal_b
        handle = (
            candidate_a
            if math.hypot(candidate_a.x() - center.x(), candidate_a.y() - center.y())
            >= math.hypot(candidate_b.x() - center.x(), candidate_b.y() - center.y())
            else candidate_b
        )
        return center, midpoint, handle

    def _hit_rotation_handle(self, view_pos) -> bool:
        if self._rotation_handle_center is None:
            return False
        handle_pos = self.mapFromScene(self._rotation_handle_center)
        return (
            abs(handle_pos.x() - view_pos.x()) <= self.HANDLE_HIT_RADIUS + 3
            and abs(handle_pos.y() - view_pos.y()) <= self.HANDLE_HIT_RADIUS + 3
        )

    def _begin_rotation(self, point: QPointF) -> None:
        corners = self._rotation_corners()
        geometry = self._rotation_handle_geometry()
        if len(corners) != 4 or geometry is None:
            return
        center, _, _ = geometry
        self._rotation_origin = self._snapshot()
        self._rotation_points = corners
        self._rotation_center = center
        self._rotation_start_angle = math.atan2(point.y() - center.y(), point.x() - center.x())
        self._selected_vertex = None

    def _update_rotation(self, point: QPointF) -> None:
        if self._rotation_origin is None:
            return
        center = self._rotation_center
        angle = math.atan2(point.y() - center.y(), point.x() - center.x())
        delta = angle - self._rotation_start_angle
        cosine, sine = math.cos(delta), math.sin(delta)
        rotated = []
        for x, y in self._rotation_points:
            dx, dy = x - center.x(), y - center.y()
            rotated.append((
                int(round(center.x() + dx * cosine - dy * sine)),
                int(round(center.y() + dx * sine + dy * cosine)),
            ))
        bounds = self.scene_rect()
        if not all(bounds.contains(QPointF(x, y)) for x, y in rotated):
            return
        self._shape = "polygon"
        self._rotated_rect = True
        self._points = rotated
        self._roi_rect = self._polygon_bounds(rotated)
        self._render_shape()

    def _clear_rotation_state(self) -> None:
        self._rotation_origin = None
        self._rotation_points = []
        self._rotation_handle_center = None

    def _restore_rotation_origin(self) -> None:
        origin = self._rotation_origin
        self._clear_rotation_state()
        if origin is None:
            return
        if origin.get("shape") == "polygon":
            self._shape = "polygon"
            self._points = [tuple(map(int, point)) for point in origin.get("points", [])]
            self._rotated_rect = bool(origin.get("rotated_rect", False)) and len(self._points) == 4
            self._roi_rect = self._polygon_bounds(self._points) if self._points else None
        else:
            self._shape = "ellipse" if origin.get("shape") == "ellipse" else "rect"
            self._points = []
            self._rotated_rect = False
            self._roi_rect = (
                tuple(int(origin[key]) for key in ("x", "y", "w", "h"))
                if {"x", "y", "w", "h"}.issubset(origin)
                else None
            )
        self._render_shape()

    def _finish_polygon(self) -> None:
        if len(self._draft_points) < 3:
            return
        before = self._snapshot()
        # A double click can deliver the same final point twice.
        points = [(int(round(p.x())), int(round(p.y()))) for p in self._draft_points]
        if len(points) > 1 and points[-1] == points[-2]:
            points.pop()
        if len(points) < 3:
            return
        self._shape, self._points = "polygon", points
        self._rotated_rect = False
        self._roi_rect = self._polygon_bounds(points)
        self._draft_points = []; self._draft_cursor = None; self._selected_vertex = None
        self._render_shape(); self._record(before); self.roiChanged.emit(self.roi())
        self.set_interaction_mode(InteractionMode.SELECT)

    @staticmethod
    def _polygon_bounds(points) -> Tuple[int, int, int, int]:
        xs, ys = zip(*points)
        return min(xs), min(ys), max(1, max(xs) - min(xs)), max(1, max(ys) - min(ys))

    def _move_polygon(self, points, delta: QPointF):
        bounds = self._polygon_bounds(points)
        dx = min(max(int(round(delta.x())), -bounds[0]),
                 int(round(self.scene_rect().right())) - (bounds[0] + bounds[2]))
        dy = min(max(int(round(delta.y())), -bounds[1]),
                 int(round(self.scene_rect().bottom())) - (bounds[1] + bounds[3]))
        return [(x + dx, y + dy) for x, y in points]

    def _polygon_path(self, points=None) -> QPainterPath:
        points = self._points if points is None else points
        path = QPainterPath()
        if points:
            first = points[0]; path.moveTo(float(first[0]), float(first[1]))
            for point in points[1:]: path.lineTo(float(point[0]), float(point[1]))
            if len(points) >= 3: path.closeSubpath()
        return path

    def _hit_vertex(self, view_pos) -> Optional[int]:
        for index, (x, y) in enumerate(self._points):
            mapped = self.mapFromScene(QPointF(x, y))
            if abs(mapped.x() - view_pos.x()) <= self.HANDLE_HIT_RADIUS and abs(mapped.y() - view_pos.y()) <= self.HANDLE_HIT_RADIUS:
                return index
        return None

    def _nearest_segment(self, point: QPointF) -> Optional[int]:
        best = None; best_distance = self.EDGE_HIT_RADIUS / max(self.zoom(), 0.001)
        for index, start in enumerate(self._points):
            end = self._points[(index + 1) % len(self._points)]
            ax, ay = start; bx, by = end; vx, vy = bx - ax, by - ay
            length2 = vx * vx + vy * vy
            t = 0.0 if not length2 else max(0.0, min(1.0, ((point.x()-ax)*vx + (point.y()-ay)*vy)/length2))
            distance = math.hypot(point.x() - (ax + t*vx), point.y() - (ay + t*vy))
            if distance <= best_distance: best, best_distance = index, distance
        return best

    def _render_shape(self) -> None:
        if not hasattr(self, "_shape"):
            return
        self._remove_rotation_handle()
        for item in self._vertex_items:
            self.scene().removeItem(item)
        self._vertex_items = []
        if self._roi_item is not None:
            self.scene().removeItem(self._roi_item)
            self._roi_item = None
        self._remove_handles()
        path = QPainterPath()
        if self._draft_points:
            path.moveTo(self._draft_points[0])
            for point in self._draft_points[1:]:
                path.lineTo(point)
            if self._draft_cursor is not None: path.lineTo(self._draft_cursor)
        elif self._shape == "polygon" and self._points:
            path = self._polygon_path()
        elif self._roi_rect is not None:
            rect = QRectF(*self._roi_rect)
            path.addEllipse(rect) if self._shape == "ellipse" else path.addRect(rect)
        if not path.isEmpty():
            pen = QPen(QColor(_ROI_COLOR)); pen.setWidthF(2.0)
            item = QGraphicsPathItem(path); item.setPen(pen); item.setBrush(Qt.transparent)
            item.setZValue(100); self.scene().addItem(item); self._roi_item = item  # type: ignore[assignment]
        if self._shape == "polygon" and self._rotated_rect and self.interaction_mode() == InteractionMode.SELECT:
            half = self.HANDLE_SIZE / 2.0
            for handle, point in self._rotated_rect_handle_centers().items():
                item = QGraphicsRectItem(-half, -half, self.HANDLE_SIZE, self.HANDLE_SIZE)
                item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                item.setAcceptedMouseButtons(Qt.NoButton)
                item.setPen(QPen(QColor(225, 240, 255)))
                item.setBrush(QColor(75, 165, 235))
                item.setPos(point)
                item.setZValue(110)
                item.setVisible(not self._roi_locked)
                self.scene().addItem(item)
                self._vertex_items.append(item)
        elif self._shape == "polygon" and self._points and self.interaction_mode() == InteractionMode.SELECT:
            half = self.HANDLE_SIZE / 2.0
            for index, point in enumerate(self._points):
                item = QGraphicsRectItem(-half, -half, self.HANDLE_SIZE, self.HANDLE_SIZE)
                item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True); item.setAcceptedMouseButtons(Qt.NoButton)
                item.setPen(QPen(QColor(225, 240, 255)))
                item.setBrush(QColor(255, 190, 55) if index == self._selected_vertex else QColor(75, 165, 235))
                item.setPos(QPointF(*point)); item.setZValue(110); item.setVisible(not self._roi_locked)
                self.scene().addItem(item); self._vertex_items.append(item)
        elif self._draft_points:
            half = self.HANDLE_SIZE / 2.0
            for point in self._draft_points:
                item = QGraphicsRectItem(-half, -half, self.HANDLE_SIZE, self.HANDLE_SIZE)
                item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                item.setAcceptedMouseButtons(Qt.NoButton)
                item.setPen(QPen(QColor(225, 240, 255)))
                item.setBrush(QColor(75, 165, 235)); item.setPos(point); item.setZValue(110)
                self.scene().addItem(item); self._vertex_items.append(item)
        elif self._roi_rect is not None:
            self._update_handles(QRectF(*self._roi_rect))
        self._render_rotation_handle()
        self._update_shape_overlay()

    def _remove_rotation_handle(self) -> None:
        for item in (self._rotation_handle_item, self._rotation_connector_item):
            if item is not None and item.scene() is self.scene():
                self.scene().removeItem(item)
        self._rotation_handle_item = None
        self._rotation_connector_item = None
        self._rotation_handle_center = None

    def _render_rotation_handle(self) -> None:
        if (
            self._roi_locked
            or self.interaction_mode() != InteractionMode.SELECT
            or (self._shape == "polygon" and not self._rotated_rect)
            or self._shape not in {"rect", "polygon"}
        ):
            return
        geometry = self._rotation_handle_geometry()
        if geometry is None:
            return
        _, midpoint, handle = geometry
        connector_path = QPainterPath(midpoint)
        connector_path.lineTo(handle)
        connector = QGraphicsPathItem(connector_path)
        pen = QPen(QColor(110, 170, 255))
        pen.setWidthF(1.5)
        pen.setCosmetic(True)
        connector.setPen(pen)
        connector.setZValue(111)
        connector.setAcceptedMouseButtons(Qt.NoButton)
        self.scene().addItem(connector)
        self._rotation_connector_item = connector

        size = self.HANDLE_SIZE + 2.0
        rotation_handle = QGraphicsRectItem(-size / 2.0, -size / 2.0, size, size)
        rotation_handle.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        rotation_handle.setAcceptedMouseButtons(Qt.NoButton)
        rotation_handle.setPen(QPen(QColor(225, 240, 255)))
        rotation_handle.setBrush(QColor(110, 170, 255))
        rotation_handle.setPos(handle)
        rotation_handle.setZValue(112)
        self.scene().addItem(rotation_handle)
        self._rotation_handle_item = rotation_handle
        self._rotation_handle_center = handle

    def _update_shape_overlay(self) -> None:
        if self.scene_rect().isNull(): return
        inner = QPainterPath()
        if self._shape == "polygon" and self._points: inner = self._polygon_path()
        elif self._roi_rect is not None:
            rect = QRectF(*self._roi_rect)
            inner.addEllipse(rect) if self._shape == "ellipse" else inner.addRect(rect)
        if inner.isEmpty():
            if self._overlay_item is not None: self.scene().removeItem(self._overlay_item); self._overlay_item = None
            return
        outer = QPainterPath(); outer.addRect(self.scene_rect()); overlay = outer.subtracted(inner)
        if self._overlay_item is None:
            self._overlay_item = QGraphicsPathItem(); self._overlay_item.setBrush(_OVERLAY_COLOR)
            self._overlay_item.setPen(Qt.NoPen); self._overlay_item.setZValue(10); self.scene().addItem(self._overlay_item)
        self._overlay_item.setPath(overlay)



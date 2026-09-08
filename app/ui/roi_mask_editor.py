"""Reusable ROI and ignore mask editors with zoom/pan support."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGraphicsPathItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


from app.ui.image_canvas import (
    CANVAS_TOOLBAR_STYLE,
    ImageView as _ImageView,
    ImageNavigationToolbar,
    InteractionMode,
)
from app.models.schema import ToolRoi


_ROI_COLOR = QColor(0, 200, 0, 200)
_MASK_COLOR = QColor(217, 70, 239, 102)
_OVERLAY_COLOR = QColor(0, 0, 0, 120)


ROI_WARN_PIXELS = 900_000
MAX_ROI_PIXELS = 1_600_000
MASK_WARN_PIXELS = 900_000
MAX_MASK_PIXELS = 1_600_000


def _format_pixels(count: int) -> str:
    """Format pixel counts using thin spaces for readability."""

    return f"{int(count):,}".replace(",", "\u202f")


def _clamp_point_to_rect(point: QPointF, rect: QRectF) -> QPointF:
    if rect.isNull():
        return point
    x = min(max(point.x(), rect.left()), rect.right())
    y = min(max(point.y(), rect.top()), rect.bottom())
    return QPointF(x, y)


class RoiHandle(Enum):
    TOP_LEFT = "top_left"
    TOP = "top"
    TOP_RIGHT = "top_right"
    LEFT = "left"
    RIGHT = "right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM = "bottom"
    BOTTOM_RIGHT = "bottom_right"


class _ROIView(_ImageView):
    """View handling rectangle ROI selection with history support."""

    roiChanged = Signal(object)
    historyChanged = Signal()
    lockChanged = Signal(bool)
    MIN_ROI_SIZE = 4.0
    HANDLE_SIZE = 9.0
    HANDLE_HIT_RADIUS = 9.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._roi_item: Optional[QGraphicsRectItem] = None
        self._overlay_item: Optional[QGraphicsPathItem] = None
        self._handle_items: dict[RoiHandle, QGraphicsRectItem] = {}
        self._drawing = False
        self._start_pos = QPointF()
        self._edit_handle: Optional[RoiHandle] = None
        self._moving_roi = False
        self._edit_start_pos = QPointF()
        self._edit_origin: Optional[Tuple[int, int, int, int]] = None
        self._preview_rect: Optional[QRectF] = None
        self._roi_rect: Optional[Tuple[int, int, int, int]] = None
        self._roi_locked = False
        self._undo_stack: List[Optional[Tuple[int, int, int, int]]] = []
        self._redo_stack: List[Optional[Tuple[int, int, int, int]]] = []
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    # ------------------------------------------------------------------
    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:  # type: ignore[override]
        self.cancel_drawing()
        was_locked = self._roi_locked
        # ImageView.set_pixmap() clears the scene and destroys every scene-owned
        # C++ item. Drop Python wrappers before that happens.
        self._roi_item = None
        self._overlay_item = None
        self._handle_items.clear()
        self._roi_rect = None
        self._clear_edit_state()
        super().set_pixmap(pixmap)
        self._roi_locked = False
        if was_locked:
            self.lockChanged.emit(False)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.historyChanged.emit()
        self._update_overlay()

    # ------------------------------------------------------------------
    def set_roi(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self.cancel_drawing()
        self._apply_roi(rect, push_history=False)

    def is_roi_locked(self) -> bool:
        return self._roi_locked

    def set_roi_locked(self, locked: bool) -> None:
        locked = bool(locked) and self._roi_rect is not None
        if locked == self._roi_locked:
            return
        self.cancel_drawing()
        self._roi_locked = locked
        if locked and self.interaction_mode() == InteractionMode.DRAW:
            self.set_interaction_mode(InteractionMode.SELECT)
        for item in self._handle_items.values():
            item.setVisible(not locked)
        self._update_cursor()
        self.lockChanged.emit(locked)

    def edit_roi(self, rect: Tuple[int, int, int, int]) -> None:
        """Commit one numeric or keyboard edit through the shared history path."""
        if self._roi_locked or self._roi_rect is None or self.scene_rect().isEmpty():
            return
        self.cancel_drawing()
        self._commit_roi(rect)

    def _commit_roi(self, rect: Tuple[int, int, int, int]) -> None:
        rect = self._clamp_integer_rect(rect)
        if rect == self.roi():
            self._restore_roi_preview()
            return
        self._push_undo()
        self._redo_stack.clear()
        self._apply_roi(rect, push_history=False)
        self.historyChanged.emit()

    def reset_roi(self) -> None:
        if self._roi_locked:
            return
        self.cancel_drawing()
        self._push_undo()
        self._apply_roi(None, push_history=False)
        self._redo_stack.clear()
        self.historyChanged.emit()

    def roi(self) -> Optional[Tuple[int, int, int, int]]:
        return tuple(self._roi_rect) if self._roi_rect is not None else None

    def undo(self) -> None:
        self.cancel_drawing()
        if not self._undo_stack:
            return
        previous = self._undo_stack.pop()
        current = self.roi()
        self._redo_stack.append(current)
        self._apply_roi(previous, push_history=False)
        self.historyChanged.emit()

    def redo(self) -> None:
        self.cancel_drawing()
        if not self._redo_stack:
            return
        next_rect = self._redo_stack.pop()
        current = self.roi()
        self._undo_stack.append(current)
        self._apply_roi(next_rect, push_history=False)
        self.historyChanged.emit()

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        directions = {
            Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
            Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1),
        }
        if event.key() in directions and self.hasFocus():
            if (
                self.isEnabled()
                and self.interaction_mode() == InteractionMode.SELECT
                and self._roi_rect is not None and not self._roi_locked
                and not self._space_pressed and not self._panning
                and self._edit_origin is None and not self._drawing
                and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
            ):
                dx, dy = directions[event.key()]
                step = 10 if event.modifiers() & Qt.ShiftModifier else 1
                x, y, width, height = self._roi_rect
                self.edit_roi((x + dx * step, y + dy * step, width, height))
            # Do not let inactive nudges fall through to QGraphicsView scrolling.
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.is_pan_gesture(event):
            super().mousePressEvent(event)
            return
        if self._roi_locked:
            event.accept()
            return
        if (
            event.button() == Qt.LeftButton
            and self.isEnabled()
            and self.interaction_mode() == InteractionMode.SELECT
            and self._roi_rect is not None
        ):
            scene_pos = self.mapToScene(event.position().toPoint())
            handle = self._hit_test_handle(event.position().toPoint())
            if handle is not None or self._roi_qrect().contains(scene_pos):
                self._begin_roi_edit(scene_pos, handle)
                event.accept()
                return
        if event.button() == Qt.LeftButton and self.can_draw() and self.scene_rect():
            scene_pos = self.mapToScene(event.position().toPoint())
            if not self.scene_rect().contains(scene_pos):
                event.accept()
                return
            self._drawing = True
            self._start_pos = scene_pos
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._edit_origin is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._update_roi_edit(scene_pos)
            event.accept()
            return
        if self._drawing:
            scene_pos = self.mapToScene(event.position().toPoint())
            scene_pos = _clamp_point_to_rect(scene_pos, self.scene_rect())
            rect = QRectF(self._start_pos, scene_pos).normalized()
            self._update_roi_item(rect)
            event.accept()
            return
        if event.buttons() == Qt.NoButton:
            self._update_roi_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._edit_origin is not None and event.button() == Qt.LeftButton:
            self._finish_roi_edit()
            self._update_roi_cursor(event.position().toPoint())
            event.accept()
            return
        if self._drawing and event.button() == Qt.LeftButton:
            self._drawing = False
            scene_pos = self.mapToScene(event.position().toPoint())
            scene_pos = _clamp_point_to_rect(scene_pos, self.scene_rect())
            rect = QRectF(self._start_pos, scene_pos).normalized()
            if rect.width() >= 1 and rect.height() >= 1:
                self._commit_roi(
                    (
                        int(round(rect.left())),
                        int(round(rect.top())),
                        int(round(rect.width())),
                        int(round(rect.height())),
                    ),
                )
                self.set_interaction_mode(InteractionMode.SELECT)
            else:
                self._restore_roi_preview()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    def cancel_drawing(self) -> None:
        if self._edit_origin is not None:
            self._restore_roi_preview()
            self._clear_edit_state()
        if self._drawing:
            self._drawing = False
            self._restore_roi_preview()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._edit_origin is None:
            self._update_cursor()
        super().leaveEvent(event)

    def _roi_qrect(self) -> QRectF:
        if self._roi_rect is None:
            return QRectF()
        return QRectF(*(float(value) for value in self._roi_rect))

    def _begin_roi_edit(self, scene_pos: QPointF, handle: Optional[RoiHandle]) -> None:
        self._edit_origin = self.roi()
        self._edit_start_pos = scene_pos
        self._edit_handle = handle
        self._moving_roi = handle is None
        self._preview_rect = self._roi_qrect()
        self._set_edit_cursor()

    def _update_roi_edit(self, scene_pos: QPointF) -> None:
        if self._edit_origin is None:
            return
        original = QRectF(*(float(value) for value in self._edit_origin))
        if self._moving_roi:
            preview = self._move_rect(original, scene_pos - self._edit_start_pos)
        elif self._edit_handle is not None:
            preview = self._resize_rect(original, scene_pos, self._edit_handle)
        else:
            return
        self._preview_rect = preview
        self._update_roi_item(preview)

    def _move_rect(self, rect: QRectF, delta: QPointF) -> QRectF:
        return QRectF(*self._clamp_integer_rect((
            rect.left() + delta.x(), rect.top() + delta.y(), rect.width(), rect.height()
        )))

    def _resize_rect(self, rect: QRectF, point: QPointF, handle: RoiHandle) -> QRectF:
        left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()

        if handle in (RoiHandle.TOP_LEFT, RoiHandle.LEFT, RoiHandle.BOTTOM_LEFT):
            left = point.x()
        if handle in (RoiHandle.TOP_RIGHT, RoiHandle.RIGHT, RoiHandle.BOTTOM_RIGHT):
            right = point.x()
        if handle in (RoiHandle.TOP_LEFT, RoiHandle.TOP, RoiHandle.TOP_RIGHT):
            top = point.y()
        if handle in (RoiHandle.BOTTOM_LEFT, RoiHandle.BOTTOM, RoiHandle.BOTTOM_RIGHT):
            bottom = point.y()
        return QRectF(*self._clamp_integer_rect((left, top, right - left, bottom - top), handle=handle))

    def _finish_roi_edit(self) -> None:
        origin = self._edit_origin
        preview = self._preview_rect
        self._clear_edit_state()
        if origin is None or preview is None:
            return
        self._commit_roi((preview.left(), preview.top(), preview.width(), preview.height()))

    def _clamp_integer_rect(
        self, rect: Tuple[float, float, float, float], *, handle: Optional[RoiHandle] = None
    ) -> Tuple[int, int, int, int]:
        """Shared image bounds for drawing, move, resize, numeric edits and nudges.

        Resize keeps the opposite edge fixed and clamps at the UI-02 minimum.
        Other edits preserve valid small legacy ROI sizes (down to one pixel).
        """
        bounds = self.scene_rect()
        x, y, width, height = rect
        if handle is not None:
            right, bottom = x + width, y + height
            if handle in (RoiHandle.TOP_LEFT, RoiHandle.LEFT, RoiHandle.BOTTOM_LEFT):
                minimum = min(self.MIN_ROI_SIZE, right - bounds.left())
                x = min(max(x, bounds.left()), right - minimum)
            if handle in (RoiHandle.TOP_RIGHT, RoiHandle.RIGHT, RoiHandle.BOTTOM_RIGHT):
                minimum = min(self.MIN_ROI_SIZE, bounds.right() - x)
                right = max(min(right, bounds.right()), x + minimum)
            if handle in (RoiHandle.TOP_LEFT, RoiHandle.TOP, RoiHandle.TOP_RIGHT):
                minimum = min(self.MIN_ROI_SIZE, bottom - bounds.top())
                y = min(max(y, bounds.top()), bottom - minimum)
            if handle in (RoiHandle.BOTTOM_LEFT, RoiHandle.BOTTOM, RoiHandle.BOTTOM_RIGHT):
                minimum = min(self.MIN_ROI_SIZE, bounds.bottom() - y)
                bottom = max(min(bottom, bounds.bottom()), y + minimum)
            width, height = right - x, bottom - y
        x, y, width, height = (int(round(value)) for value in (x, y, width, height))
        width = min(max(1, width), int(round(bounds.width())))
        height = min(max(1, height), int(round(bounds.height())))
        x = min(max(int(round(bounds.left())), x), int(round(bounds.right())) - width)
        y = min(max(int(round(bounds.top())), y), int(round(bounds.bottom())) - height)
        return x, y, width, height

    def _clear_edit_state(self) -> None:
        self._edit_handle = None
        self._moving_roi = False
        self._edit_origin = None
        self._preview_rect = None
        self._update_cursor()

    def _handle_centers(self, rect: QRectF) -> dict[RoiHandle, QPointF]:
        return {
            RoiHandle.TOP_LEFT: rect.topLeft(),
            RoiHandle.TOP: QPointF(rect.center().x(), rect.top()),
            RoiHandle.TOP_RIGHT: rect.topRight(),
            RoiHandle.LEFT: QPointF(rect.left(), rect.center().y()),
            RoiHandle.RIGHT: QPointF(rect.right(), rect.center().y()),
            RoiHandle.BOTTOM_LEFT: rect.bottomLeft(),
            RoiHandle.BOTTOM: QPointF(rect.center().x(), rect.bottom()),
            RoiHandle.BOTTOM_RIGHT: rect.bottomRight(),
        }

    def _hit_test_handle(self, view_pos) -> Optional[RoiHandle]:
        rect = self._preview_rect or self._roi_qrect()
        if rect.isNull():
            return None
        for handle, center in self._handle_centers(rect).items():
            handle_pos = self.mapFromScene(center)
            if (
                abs(handle_pos.x() - view_pos.x()) <= self.HANDLE_HIT_RADIUS
                and abs(handle_pos.y() - view_pos.y()) <= self.HANDLE_HIT_RADIUS
            ):
                return handle
        return None

    @staticmethod
    def _handle_cursor(handle: RoiHandle):
        if handle in (RoiHandle.LEFT, RoiHandle.RIGHT):
            return Qt.SizeHorCursor
        if handle in (RoiHandle.TOP, RoiHandle.BOTTOM):
            return Qt.SizeVerCursor
        if handle in (RoiHandle.TOP_LEFT, RoiHandle.BOTTOM_RIGHT):
            return Qt.SizeFDiagCursor
        return Qt.SizeBDiagCursor

    def _set_edit_cursor(self) -> None:
        if self._moving_roi:
            self.setCursor(Qt.SizeAllCursor)
        elif self._edit_handle is not None:
            self.setCursor(self._handle_cursor(self._edit_handle))

    def _update_roi_cursor(self, view_pos) -> None:
        if (
            not self.isEnabled()
            or self.interaction_mode() != InteractionMode.SELECT
            or self._roi_rect is None
            or self._space_pressed
            or self._roi_locked
        ):
            self._update_cursor()
            return
        handle = self._hit_test_handle(view_pos)
        if handle is not None:
            self.setCursor(self._handle_cursor(handle))
            return
        scene_pos = self.mapToScene(view_pos)
        self.setCursor(Qt.SizeAllCursor if self._roi_qrect().contains(scene_pos) else Qt.ArrowCursor)

    def _restore_roi_preview(self) -> None:
        if self._roi_rect is not None:
            self._update_roi_item(QRectF(*self._roi_rect))
        elif self._roi_item is not None:
            self.scene().removeItem(self._roi_item)
            self._roi_item = None
            self._remove_handles()
        self._update_overlay()

    def _push_undo(self) -> None:
        self._undo_stack.append(self.roi())
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    def _apply_roi(self, rect: Optional[Tuple[int, int, int, int]], push_history: bool) -> None:
        if push_history:
            self._push_undo()
        self._roi_rect = tuple(rect) if rect is not None else None
        if rect is None:
            if self._roi_item is not None:
                self.scene().removeItem(self._roi_item)
                self._roi_item = None
            self._remove_handles()
            self.set_roi_locked(False)
        else:
            left, top, width, height = rect
            roi_rectf = QRectF(float(left), float(top), float(width), float(height))
            if self._roi_item is None:
                pen = QPen(QColor(_ROI_COLOR))
                pen.setWidthF(2.0)
                self._roi_item = QGraphicsRectItem(roi_rectf)
                self._roi_item.setPen(pen)
                self._roi_item.setBrush(Qt.transparent)
                self._roi_item.setZValue(100)
                self.scene().addItem(self._roi_item)
            else:
                self._roi_item.setRect(roi_rectf)
            self._update_handles(roi_rectf)
        self._update_overlay()
        self.roiChanged.emit(self.roi())

    def _update_roi_item(self, rect: QRectF) -> None:
        if rect.isNull():
            return
        if self._roi_item is None:
            pen = QPen(QColor(_ROI_COLOR))
            pen.setWidthF(2.0)
            self._roi_item = QGraphicsRectItem(rect)
            self._roi_item.setPen(pen)
            self._roi_item.setBrush(Qt.transparent)
            self._roi_item.setZValue(100)
            self.scene().addItem(self._roi_item)
        else:
            self._roi_item.setRect(rect)
        self._update_handles(rect)
        self._update_overlay(rect)

    def _update_handles(self, rect: QRectF) -> None:
        half = self.HANDLE_SIZE / 2.0
        pen = QPen(QColor(225, 240, 255))
        pen.setWidthF(1.0)
        for handle, center in self._handle_centers(rect).items():
            item = self._handle_items.get(handle)
            if item is None:
                item = QGraphicsRectItem(-half, -half, self.HANDLE_SIZE, self.HANDLE_SIZE)
                item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                item.setAcceptedMouseButtons(Qt.NoButton)
                item.setPen(pen)
                item.setBrush(QColor(75, 165, 235))
                item.setZValue(110)
                self.scene().addItem(item)
                self._handle_items[handle] = item
            item.setPos(center)
            item.setVisible(not self._roi_locked)

    def _remove_handles(self) -> None:
        for item in self._handle_items.values():
            self.scene().removeItem(item)
        self._handle_items.clear()

    def _update_overlay(self, preview_rect: Optional[QRectF] = None) -> None:
        scene_rect = self.scene_rect()
        if scene_rect.isNull():
            if self._overlay_item is not None:
                self.scene().removeItem(self._overlay_item)
                self._overlay_item = None
            return
        roi_rectf: Optional[QRectF]
        if preview_rect is not None:
            roi_rectf = preview_rect
        elif self._roi_rect is not None:
            left, top, width, height = self._roi_rect
            roi_rectf = QRectF(float(left), float(top), float(width), float(height))
        else:
            roi_rectf = None

        if roi_rectf is None or roi_rectf.isNull():
            if self._overlay_item is not None:
                self.scene().removeItem(self._overlay_item)
                self._overlay_item = None
            return

        outer = QPainterPath()
        outer.addRect(scene_rect)
        inner = QPainterPath()
        inner.addRect(roi_rectf)
        overlay_path = outer.subtracted(inner)
        if self._overlay_item is None:
            self._overlay_item = QGraphicsPathItem(overlay_path)
            self._overlay_item.setBrush(_OVERLAY_COLOR)
            self._overlay_item.setPen(Qt.NoPen)
            self._overlay_item.setZValue(10)
            self.scene().addItem(self._overlay_item)
        else:
            self._overlay_item.setPath(overlay_path)


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
        self._shape_history: List[dict] = []
        self._shape_redo: List[dict] = []

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
        self._shape_history.clear(); self._shape_redo.clear()

    def set_roi_locked(self, locked: bool) -> None:
        super().set_roi_locked(locked)
        for item in self._vertex_items:
            item.setVisible(not self._roi_locked)

    def _apply_roi(self, rect, push_history: bool) -> None:
        if push_history:
            self._shape_history.append(self._snapshot())
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
        self._shape_history.clear()
        self._shape_redo.clear()
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
        return bool(self._shape_history)

    def can_redo(self) -> bool:
        return bool(self._shape_redo)

    def _snapshot(self) -> dict:
        return self.roi_data()

    def _record(self, before: dict) -> None:
        if before == self.roi_data():
            return
        self._shape_history.append(before)
        self._shape_history = self._shape_history[-100:]
        self._shape_redo.clear()
        self.historyChanged.emit()

    def undo(self) -> None:
        self.cancel_drawing()
        if not self._shape_history:
            return
        current = self._snapshot()
        previous = self._shape_history.pop()
        self._shape_redo.append(current)
        self._restore_snapshot(previous)
        self.historyChanged.emit()

    def redo(self) -> None:
        self.cancel_drawing()
        if not self._shape_redo:
            return
        current = self._snapshot()
        following = self._shape_redo.pop()
        self._shape_history.append(current)
        self._restore_snapshot(following)
        self.historyChanged.emit()

    def _restore_snapshot(self, data: dict) -> None:
        history, redo = self._shape_history, self._shape_redo
        self.set_roi_data(data)
        self._shape_history, self._shape_redo = history, redo

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
        self._undo_stack: List[np.ndarray] = []
        self._redo_stack: List[np.ndarray] = []
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
        self._undo_stack.clear()
        self._redo_stack.clear()
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
            self._undo_stack.clear()
            self._redo_stack.clear()
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
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_mask_item()
        self._update_roi_overlay()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def clear_mask(self) -> None:
        if self._mask is None or not np.any(self._mask):
            return
        self._push_undo()
        self._mask.fill(0)
        self._redo_stack.clear()
        self._update_mask_item()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def undo(self) -> None:
        if not self._undo_stack:
            return
        current = self._mask.copy() if self._mask is not None else None
        previous = self._undo_stack.pop()
        if current is not None:
            self._redo_stack.append(current)
        self._mask = previous.copy()
        self._update_mask_item()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        current = self._mask.copy() if self._mask is not None else None
        next_mask = self._redo_stack.pop()
        if current is not None:
            self._undo_stack.append(current)
        self._mask = next_mask.copy()
        self._update_mask_item()
        self.maskChanged.emit(self.mask())
        self.historyChanged.emit()

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

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
                self._undo_stack.append(self._stroke_before)
                self._undo_stack = self._undo_stack[-100:]
                self._redo_stack.clear()
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
                self._redo_stack.clear()
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
        self._undo_stack.append(self._mask.copy())
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    def _apply_brush_point(self, point: QPointF) -> None:
        if self._mask is None:
            return
        cx = int(round(point.x()))
        cy = int(round(point.y()))
        radius = int(self._brush_radius)
        if radius <= 0:
            radius = 1
        value = 255 if self._mode == self.MODE_BRUSH_ADD else 0
        cv2.circle(self._mask, (cx, cy), radius, value, -1)
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
        self._redo_stack.clear()
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
        self._redo_stack.clear()
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
        self._mask_undo: List[np.ndarray] = []
        self._mask_redo: List[np.ndarray] = []
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
        self._mask_undo.clear()
        self._mask_redo.clear()
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
            self._mask_undo.clear()
            self._mask_redo.clear()
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
        self._mask_undo.clear()
        self._mask_redo.clear()
        self._update_mask_overlay()
        self.maskHistoryChanged.emit()

    def clear_mask(self) -> None:
        if self._mask is None or not np.any(self._mask):
            return
        self._push_mask_undo()
        self._mask.fill(0)
        self._mask_redo.clear()
        self._finish_mask_change()

    def mask_undo(self) -> None:
        if not self._mask_undo or self._mask is None:
            return
        self._mask_redo.append(self._mask.copy())
        self._mask = self._mask_undo.pop()
        self._finish_mask_change()

    def mask_redo(self) -> None:
        if not self._mask_redo or self._mask is None:
            return
        self._mask_undo.append(self._mask.copy())
        self._mask = self._mask_redo.pop()
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
            self._mask_undo.append(self._mask_before)
            self._mask_undo = self._mask_undo[-100:]
            self._mask_redo.clear()
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
        value = 0 if self._mask_mode == self.MASK_ERASER else 255
        cv2.circle(self._mask, (round(point.x()), round(point.y())),
                   max(1, self._mask_brush_size // 2), value, -1)
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
        self._mask_redo.clear()
        self._finish_mask_change()

    def _commit_mask_polygon(self) -> None:
        if self._mask is not None and len(self._mask_polygon) >= 3:
            self._push_mask_undo()
            points = np.array([(round(p.x()), round(p.y())) for p in self._mask_polygon])
            cv2.fillPoly(self._mask, [points.astype(np.int32)], 255)
            self._mask_redo.clear()
            self._finish_mask_change()
        self._mask_polygon.clear()
        self._remove_mask_preview()

    def _push_mask_undo(self) -> None:
        if self._mask is not None:
            self._mask_undo.append(self._mask.copy())
            self._mask_undo = self._mask_undo[-100:]

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


__all__ = [
    "ROIEditor",
    "LocatorROIEditor",
    "MaskEditor",
    "MaskEditorState",
    "ROI_WARN_PIXELS",
    "MAX_ROI_PIXELS",
    "MASK_WARN_PIXELS",
    "MAX_MASK_PIXELS",
]

"""rectangle view canvas interaction."""
from __future__ import annotations
from typing import List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsItem, QGraphicsRectItem, QWidget
from app.ui.image_canvas import ImageView as _ImageView, InteractionMode

from app.ui.roi.geometry import clamp_integer_rect
from app.ui.roi.geometry import RoiHandle, _clamp_point_to_rect, _ROI_COLOR, _OVERLAY_COLOR

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

    def _clamp_integer_rect(self, rect, *, handle=None):
        return clamp_integer_rect(rect, bounds=self.scene_rect(),
                                  minimum_size=self.MIN_ROI_SIZE, handle=handle)

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



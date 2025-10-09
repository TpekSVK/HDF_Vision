"""Reusable ROI and ignore mask editors with zoom/pan support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


_ROI_COLOR = QColor(0, 200, 0, 200)
_MASK_COLOR = QColor(255, 0, 200, 160)
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


class _ImageView(QGraphicsView):
    """Common graphics view with wheel zoom and panning."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setFrameShape(QFrame.NoFrame)
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._current_scale = 1.0
        self._panning = False
        self._pan_start = QPoint()
        self._space_pressed = False

    # ------------------------------------------------------------------
    # Scene helpers
    # ------------------------------------------------------------------
    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        scene = self.scene()
        if scene is None:
            scene = QGraphicsScene(self)
            self.setScene(scene)
        scene.clear()
        self._pixmap_item = None
        if pixmap is not None and not pixmap.isNull():
            self._pixmap_item = scene.addPixmap(pixmap)
            self._pixmap_item.setZValue(-100)
            scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        else:
            scene.setSceneRect(QRectF())
        self._current_scale = 1.0
        self.resetTransform()
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)
            self._current_scale = 1.0

    def scene_rect(self) -> QRectF:
        scene = self.scene()
        if scene is None:
            return QRectF()
        return scene.sceneRect()

    # ------------------------------------------------------------------
    # Interaction helpers
    # ------------------------------------------------------------------
    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._pixmap_item is None:
            return
        angle = event.angleDelta().y()
        if angle == 0:
            return
        factor = 1.25 if angle > 0 else 0.8
        new_scale = self._current_scale * factor
        new_scale = max(0.1, min(16.0, new_scale))
        factor = new_scale / self._current_scale
        self._current_scale = new_scale
        self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and self._space_pressed):
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(QCursor(Qt.ClosedHandCursor))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MiddleButton or event.button() == Qt.LeftButton:
            if self._panning:
                self._panning = False
                if not self._space_pressed:
                    self.setCursor(QCursor(Qt.ArrowCursor))
                else:
                    self.setCursor(QCursor(Qt.OpenHandCursor))
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key_Space and not self._space_pressed:
            self._space_pressed = True
            if not self._panning:
                self.setCursor(QCursor(Qt.OpenHandCursor))
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key_Space and self._space_pressed:
            self._space_pressed = False
            if not self._panning:
                self.setCursor(QCursor(Qt.ArrowCursor))
            event.accept()
            return
        super().keyReleaseEvent(event)


class _ROIView(_ImageView):
    """View handling rectangle ROI selection with history support."""

    roiChanged = Signal(object)
    historyChanged = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._roi_item: Optional[QGraphicsRectItem] = None
        self._overlay_item: Optional[QGraphicsPathItem] = None
        self._drawing = False
        self._start_pos = QPointF()
        self._roi_rect: Optional[Tuple[int, int, int, int]] = None
        self._undo_stack: List[Optional[Tuple[int, int, int, int]]] = []
        self._redo_stack: List[Optional[Tuple[int, int, int, int]]] = []

    # ------------------------------------------------------------------
    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:  # type: ignore[override]
        super().set_pixmap(pixmap)
        self._roi_item = None
        self._overlay_item = None
        self._roi_rect = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.historyChanged.emit()
        self._update_overlay()

    # ------------------------------------------------------------------
    def set_roi(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self._apply_roi(rect, push_history=False)

    def reset_roi(self) -> None:
        self._push_undo()
        self._apply_roi(None, push_history=False)
        self._redo_stack.clear()
        self.historyChanged.emit()

    def roi(self) -> Optional[Tuple[int, int, int, int]]:
        return tuple(self._roi_rect) if self._roi_rect is not None else None

    def undo(self) -> None:
        if not self._undo_stack:
            return
        previous = self._undo_stack.pop()
        current = self.roi()
        self._redo_stack.append(current)
        self._apply_roi(previous, push_history=False)
        self.historyChanged.emit()

    def redo(self) -> None:
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
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self.scene_rect():
            scene_pos = self.mapToScene(event.pos())
            scene_pos = _clamp_point_to_rect(scene_pos, self.scene_rect())
            self._drawing = True
            self._start_pos = scene_pos
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drawing:
            scene_pos = self.mapToScene(event.pos())
            scene_pos = _clamp_point_to_rect(scene_pos, self.scene_rect())
            rect = QRectF(self._start_pos, scene_pos).normalized()
            self._update_roi_item(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drawing and event.button() == Qt.LeftButton:
            self._drawing = False
            scene_pos = self.mapToScene(event.pos())
            scene_pos = _clamp_point_to_rect(scene_pos, self.scene_rect())
            rect = QRectF(self._start_pos, scene_pos).normalized()
            if rect.width() >= 1 and rect.height() >= 1:
                self._push_undo()
                self._apply_roi(
                    (
                        int(round(rect.left())),
                        int(round(rect.top())),
                        int(round(rect.width())),
                        int(round(rect.height())),
                    ),
                    push_history=False,
                )
                self._redo_stack.clear()
                self.historyChanged.emit()
            else:
                self._update_overlay()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
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
        self._update_overlay(rect)

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


class _MaskView(_ImageView):
    """View handling mask painting with brush or polygon tools."""

    maskChanged = Signal(object)
    historyChanged = Signal()

    MODE_BRUSH_ADD = "brush_add"
    MODE_BRUSH_ERASE = "brush_erase"
    MODE_POLYGON = "polygon"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._mask: Optional[np.ndarray] = None
        self._mask_item: Optional[QGraphicsPixmapItem] = None
        self._mask_rgba: Optional[np.ndarray] = None
        self._mode = self.MODE_BRUSH_ADD
        self._brush_radius = 24
        self._painting = False
        self._last_point: Optional[QPointF] = None
        self._polygon_points: List[QPointF] = []
        self._polygon_item: Optional[QGraphicsPathItem] = None
        self._undo_stack: List[np.ndarray] = []
        self._redo_stack: List[np.ndarray] = []

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
        self._remove_polygon_item()
        self._update_mask_item()
        self.historyChanged.emit()

    def set_mode(self, mode: str) -> None:
        if mode not in (self.MODE_BRUSH_ADD, self.MODE_BRUSH_ERASE, self.MODE_POLYGON):
            return
        if self._mode == mode:
            return
        self._mode = mode
        if self._mode != self.MODE_POLYGON:
            self._polygon_points.clear()
            self._remove_polygon_item()

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
        if self._mask is None or self.scene_rect().isNull():
            super().mousePressEvent(event)
            return

        if event.button() == Qt.LeftButton:
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.pos()), self.scene_rect())
            if self._mode == self.MODE_POLYGON:
                self._polygon_points.append(scene_pos)
                self._update_polygon_preview(scene_pos)
                event.accept()
                return
            self._push_undo()
            self._redo_stack.clear()
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

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._painting and self._mask is not None:
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.pos()), self.scene_rect())
            self._apply_brush_segment(scene_pos)
            event.accept()
            return

        if self._mode == self.MODE_POLYGON and self._polygon_points:
            scene_pos = _clamp_point_to_rect(self.mapToScene(event.pos()), self.scene_rect())
            self._update_polygon_preview(scene_pos)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._painting and event.button() == Qt.LeftButton:
            self._painting = False
            self._last_point = None
            self.maskChanged.emit(self.mask())
            self.historyChanged.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
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


class ROIEditor(QWidget):
    """Composite widget exposing ROI editing controls."""

    roiChanged = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None, show_toolbar: bool = True) -> None:
        super().__init__(parent)
        self._view = _ROIView(self)
        self._view.historyChanged.connect(self._update_history_buttons)
        self._view.roiChanged.connect(self._on_roi_changed)

        self._btn_undo = QPushButton("Undo", self)
        self._btn_redo = QPushButton("Redo", self)
        self._btn_reset = QPushButton("Reset", self)
        self._btn_undo.clicked.connect(self._view.undo)
        self._btn_redo.clicked.connect(self._view.redo)
        self._btn_reset.clicked.connect(self._view.reset_roi)

        self._info_label = QLabel("ROI: —", self)
        self._info_label.setStyleSheet("color: #bbb;")
        self._hint_label = QLabel("", self)
        self._hint_label.setStyleSheet("color: #d48806; font-size: 11px;")
        self._hint_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._view, 1)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        toolbar.addWidget(self._btn_undo)
        toolbar.addWidget(self._btn_redo)
        toolbar.addWidget(self._btn_reset)
        toolbar.addStretch(1)
        info_box = QVBoxLayout()
        info_box.setContentsMargins(0, 0, 0, 0)
        info_box.setSpacing(2)
        info_box.addWidget(self._info_label)
        info_box.addWidget(self._hint_label)
        toolbar.addLayout(info_box)
        layout.addLayout(toolbar)

        if not show_toolbar:
            self._btn_undo.hide()
            self._btn_redo.hide()
            self._btn_reset.hide()

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._update_history_buttons()

    # ------------------------------------------------------------------
    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._view.set_pixmap(pixmap)
        self._update_history_buttons()
        self._update_info_label()

    def set_roi(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self._view.set_roi(rect)
        self._update_history_buttons()
        self._update_info_label()

    def roi(self) -> Optional[Tuple[int, int, int, int]]:
        return self._view.roi()

    def reset_roi(self) -> None:
        self._view.reset_roi()

    def undo(self) -> None:
        self._view.undo()

    def redo(self) -> None:
        self._view.redo()

    # ------------------------------------------------------------------
    def _update_history_buttons(self) -> None:
        self._btn_undo.setEnabled(self._view.can_undo())
        self._btn_redo.setEnabled(self._view.can_redo())

    def _on_roi_changed(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self._update_history_buttons()
        self._update_info_label()
        self.roiChanged.emit(rect)

    def _update_info_label(self) -> None:
        rect = self._view.roi()
        if rect is None:
            self._info_label.setText("ROI: —")
            self._hint_label.setVisible(False)
        else:
            x, y, w, h = rect
            area = max(0, int(w) * int(h))
            self._info_label.setText(
                f"ROI: {w}×{h} px · {_format_pixels(area)} px @ ({x}, {y})"
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


@dataclass
class MaskEditorState:
    mode: str
    brush_radius: int


class MaskEditor(QWidget):
    """Composite widget for ignore mask editing with undo/redo."""

    maskChanged = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._view = _MaskView(self)
        self._view.maskChanged.connect(self._on_mask_changed)
        self._view.historyChanged.connect(self._update_history_buttons)

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
        self._btn_polygon.setText("Polygon")
        self._btn_polygon.setCheckable(True)
        self._btn_polygon.setToolTip("Double click to finish polygon fill")
        self._mode_group.addButton(self._btn_polygon)

        self._btn_brush_add.setChecked(True)

        self._mode_group.buttonClicked.connect(self._on_mode_changed)

        self._brush_slider = QSlider(Qt.Horizontal, self)
        self._brush_slider.setRange(3, 160)
        self._brush_slider.setValue(24)
        self._brush_slider.valueChanged.connect(self._on_brush_radius_changed)

        self._brush_label = QLabel("Brush: 24 px", self)
        self._brush_label.setStyleSheet("color: #666;")

        self._btn_undo = QPushButton("Undo", self)
        self._btn_redo = QPushButton("Redo", self)
        self._btn_clear = QPushButton("Clear", self)
        self._btn_undo.clicked.connect(self._view.undo)
        self._btn_redo.clicked.connect(self._view.redo)
        self._btn_clear.clicked.connect(self._view.clear_mask)

        self._info_label = QLabel("Ignore pixels: 0", self)
        self._info_label.setStyleSheet("color: #bbb;")
        self._hint_label = QLabel("", self)
        self._hint_label.setStyleSheet("color: #d48806; font-size: 11px;")
        self._hint_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._view, 1)

        toolbar_top = QHBoxLayout()
        toolbar_top.setContentsMargins(0, 0, 0, 0)
        toolbar_top.setSpacing(8)
        toolbar_top.addWidget(self._btn_brush_add)
        toolbar_top.addWidget(self._btn_brush_erase)
        toolbar_top.addWidget(self._btn_polygon)
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

    # ------------------------------------------------------------------
    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._view.set_pixmap(pixmap)
        self._update_history_buttons()
        self._update_info_label()

    def set_mask(self, mask: Optional[np.ndarray]) -> None:
        self._view.set_mask(mask)
        self._update_history_buttons()
        self._update_info_label()

    def mask(self) -> Optional[np.ndarray]:
        return self._view.mask()

    def undo(self) -> None:
        self._view.undo()

    def redo(self) -> None:
        self._view.redo()

    def clear(self) -> None:
        self._view.clear_mask()

    def state(self) -> MaskEditorState:
        mode = (
            self._view.MODE_BRUSH_ADD
            if self._btn_brush_add.isChecked()
            else self._view.MODE_BRUSH_ERASE
            if self._btn_brush_erase.isChecked()
            else self._view.MODE_POLYGON
        )
        return MaskEditorState(mode=mode, brush_radius=self._brush_slider.value())

    def restore_state(self, state: MaskEditorState) -> None:
        if state.mode == self._view.MODE_BRUSH_ADD:
            self._btn_brush_add.setChecked(True)
        elif state.mode == self._view.MODE_BRUSH_ERASE:
            self._btn_brush_erase.setChecked(True)
        else:
            self._btn_polygon.setChecked(True)
        self._on_mode_changed()
        self._brush_slider.setValue(state.brush_radius)

    # ------------------------------------------------------------------
    def _on_mode_changed(self) -> None:
        if self._btn_brush_add.isChecked():
            self._view.set_mode(self._view.MODE_BRUSH_ADD)
        elif self._btn_brush_erase.isChecked():
            self._view.set_mode(self._view.MODE_BRUSH_ERASE)
        else:
            self._view.set_mode(self._view.MODE_POLYGON)
        polygon_mode = self._btn_polygon.isChecked()
        self._brush_slider.setEnabled(not polygon_mode)
        self._brush_label.setEnabled(not polygon_mode)

    def _on_brush_radius_changed(self, value: int) -> None:
        self._view.set_brush_radius(value)
        self._brush_label.setText(f"Brush: {int(value)} px")

    def _on_mask_changed(self, mask: Optional[np.ndarray]) -> None:
        self._update_info_label()
        self._update_history_buttons()
        self.maskChanged.emit(mask.copy() if mask is not None else None)

    def _update_history_buttons(self) -> None:
        self._btn_undo.setEnabled(self._view.can_undo())
        self._btn_redo.setEnabled(self._view.can_redo())

    def _update_info_label(self) -> None:
        mask = self._view.mask()
        count = int(np.count_nonzero(mask)) if mask is not None else 0
        self._info_label.setText(f"Ignore pixels: {_format_pixels(count)}")
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


__all__ = [
    "ROIEditor",
    "MaskEditor",
    "MaskEditorState",
    "ROI_WARN_PIXELS",
    "MAX_ROI_PIXELS",
    "MASK_WARN_PIXELS",
    "MAX_MASK_PIXELS",
]

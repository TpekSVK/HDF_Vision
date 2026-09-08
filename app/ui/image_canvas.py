"""Shared image-space canvas navigation, independent of ROI/mask storage."""

from __future__ import annotations

import math
from enum import Enum
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QToolButton, QWidget,
)


CANVAS_TOOLBAR_STYLE = """
    QToolButton { background: #30343b; color: #eee; border: 1px solid #505661;
                  border-radius: 3px; padding: 4px 7px; font-size: 12px; }
    QToolButton:hover { background: #424b58; }
    QToolButton:checked { background: #245e99; border-color: #6ba8e5; }
    QToolButton:disabled { color: #777; }
    QLabel { color: #ddd; font-size: 12px; }
    QSpinBox#canvasCompactSpin {
        background: #30343b; color: #eee; border: 1px solid #505661;
        border-radius: 3px; padding: 3px 5px; font-size: 12px;
    }
    QSpinBox#canvasCompactSpin:hover { background: #424b58; }
    QSpinBox#canvasCompactSpin:focus { border-color: #6ba8e5; }
    QSpinBox#canvasCompactSpin:disabled { color: #777; background: #252930; }
"""


class InteractionMode(Enum):
    SELECT = "select"
    DRAW = "draw"
    PAN = "pan"


class ImageView(QGraphicsView):
    """Navigation changes only the view transform, never scene geometry.

    Editors implement cancel_drawing() and route navigation gestures before
    handling their own drawing events. Temporary pan does not change the
    persistent interaction mode.
    """

    zoomChanged = Signal(float)
    interactionModeChanged = Signal(object)
    imageChanged = Signal(bool)
    MIN_ZOOM = 0.1
    MAX_ZOOM = 48.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(Qt.black)
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._interaction_mode = InteractionMode.SELECT
        self._panning = False
        self._pan_button = Qt.NoButton
        self._pan_start = QPoint()
        self._space_pressed = False
        self._pending_fit_to_view = False
        self._fit_schedule_queued = False
        self._fit_on_resize = True

    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        self.cancel_drawing()
        self._end_pan()
        self._space_pressed = False
        self.set_interaction_mode(InteractionMode.SELECT)
        self.scene().clear()
        self._pixmap_item = None
        if pixmap is not None and not pixmap.isNull():
            self._pixmap_item = self.scene().addPixmap(pixmap)
            self._pixmap_item.setZValue(-100)
            self._pixmap_item.setAcceptedMouseButtons(Qt.NoButton)
            self.scene().setSceneRect(self._pixmap_item.boundingRect())
        else:
            self.scene().setSceneRect(QRectF())
        self.resetTransform()
        self._pending_fit_to_view = self._pixmap_item is not None
        self._fit_on_resize = True
        self.zoomChanged.emit(self.zoom())
        self.imageChanged.emit(self._pixmap_item is not None)
        self.schedule_fit_to_view(source="set_pixmap")

    def scene_rect(self) -> QRectF:
        return self.scene().sceneRect()

    def schedule_fit_to_view(self, *, source: str = "unknown") -> None:
        """Defer the initial fit until layout/show; tab changes preserve zoom."""
        if not self._pending_fit_to_view or self._fit_schedule_queued:
            return
        self._fit_schedule_queued = True
        QTimer.singleShot(0, self._run_scheduled_fit)

    def _run_scheduled_fit(self) -> None:
        self._fit_schedule_queued = False
        if self._pending_fit_to_view:
            self.fit_image_to_view(force=False)

    def fit_image_to_view(self, *, force: bool = True) -> bool:
        if self._pixmap_item is None:
            return False
        if not force and not self.isVisible():
            return False
        if self.viewport().width() <= 1 or self.viewport().height() <= 1:
            return False
        self.cancel_drawing()
        self.resetTransform()
        # Fit may be below MIN_ZOOM for large images: the whole image must fit.
        self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)
        self._pending_fit_to_view = False
        self._fit_on_resize = True
        self.zoomChanged.emit(self.zoom())
        return True

    def zoom(self) -> float:
        return self.transform().m11()

    def set_zoom(self, scale: float, *, anchor: Optional[QPoint] = None) -> None:
        if self._pixmap_item is None:
            return
        scale = float(scale)
        if not math.isfinite(scale):
            raise ValueError("Zoom must be finite")
        scale = max(self.MIN_ZOOM, min(self.MAX_ZOOM, scale))
        anchor = self.viewport().rect().center() if anchor is None else anchor
        before = self.mapToScene(anchor)
        self.setTransform(QTransform.fromScale(scale, scale))
        after = self.mapToScene(anchor)
        delta = after - before
        self.translate(delta.x(), delta.y())
        self._pending_fit_to_view = False
        self._fit_on_resize = False
        self.zoomChanged.emit(self.zoom())

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom() * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom() / 1.25)

    def reset_zoom_100(self) -> None:
        self.set_zoom(1.0)

    def interaction_mode(self) -> InteractionMode:
        return self._interaction_mode

    def set_interaction_mode(self, mode: InteractionMode) -> None:
        mode = InteractionMode(mode)
        if mode != self._interaction_mode:
            self.cancel_drawing()
            self._end_pan()
            self._interaction_mode = mode
            self.interactionModeChanged.emit(mode)
        self._update_cursor()

    def cancel_drawing(self) -> None:
        """Editor hook: discard unfinished preview, preserving committed data."""

    def is_pan_gesture(self, event) -> bool:
        return event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton
            and (self._space_pressed or self._interaction_mode == InteractionMode.PAN)
        )

    def can_draw(self) -> bool:
        return (self._interaction_mode == InteractionMode.DRAW
                and not self._space_pressed and not self._panning)

    def _update_cursor(self) -> None:
        if self._panning:
            cursor = Qt.ClosedHandCursor
        elif self._space_pressed or self._interaction_mode == InteractionMode.PAN:
            cursor = Qt.OpenHandCursor
        elif self._interaction_mode == InteractionMode.DRAW:
            cursor = Qt.CrossCursor
        else:
            cursor = Qt.ArrowCursor
        self.setCursor(cursor)

    def _end_pan(self) -> None:
        self._panning = False
        self._pan_button = Qt.NoButton
        self._update_cursor()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if self._pixmap_item is None or not delta:
            event.ignore()
            return
        factor = 1.25 if delta > 0 else 0.8
        self.set_zoom(self.zoom() * factor, anchor=event.position().toPoint())
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.is_pan_gesture(event):
            self.cancel_drawing()
            self._panning = True
            self._pan_button = event.button()
            self._pan_start = event.position().toPoint()
            self._pending_fit_to_view = False
            self._fit_on_resize = False
            self._update_cursor()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning:
            position = event.position().toPoint()
            delta = position - self._pan_start
            self._pan_start = position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.button() == self._pan_button:
            self._end_pan()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def event(self, event) -> bool:
        # Let canvas Esc cancel a preview instead of rejecting its QDialog.
        if event.type() == QEvent.ShortcutOverride and event.key() in (
            Qt.Key_Escape, Qt.Key_Space, Qt.Key_Plus, Qt.Key_Equal,
            Qt.Key_Minus, Qt.Key_F, Qt.Key_1,
        ) and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key_Space:
            if not event.isAutoRepeat():
                self.cancel_drawing()
                self._space_pressed = True
                self._update_cursor()
        elif key == Qt.Key_Escape:
            self.cancel_drawing()
            self._end_pan()
            self._space_pressed = False
            self.set_interaction_mode(InteractionMode.SELECT)
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
        elif key == Qt.Key_Minus:
            self.zoom_out()
        elif key == Qt.Key_F:
            self.fit_image_to_view()
        elif key == Qt.Key_1:
            self.reset_zoom_100()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Space:
            if not event.isAutoRepeat():
                self._space_pressed = False
                self._update_cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.cancel_drawing()
        self._space_pressed = False
        self._end_pan()
        super().focusOutEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.schedule_fit_to_view(source="showEvent")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Fullscreen/layout can resize after the first show timer. Keep Fit
        # active until the user deliberately zooms or pans.
        if self._fit_on_resize and self._pixmap_item is not None:
            self._pending_fit_to_view = True
        self.schedule_fit_to_view(source="resizeEvent")


class ImageNavigationToolbar(QWidget):
    """Compact controls shared by ROI, template, angle and mask editors."""

    def __init__(self, view: ImageView, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._view = view
        self.setStyleSheet(CANVAS_TOOLBAR_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._context_layout = QHBoxLayout()
        self._context_layout.setContentsMargins(0, 0, 0, 0)
        self._context_layout.setSpacing(4)
        layout.addLayout(self._context_layout)
        self._modes = QButtonGroup(self)
        self._mode_layout = layout
        self.draw_buttons: list[QToolButton] = []
        self.mode_buttons: dict[InteractionMode, QToolButton] = {}
        for mode, label in ((InteractionMode.SELECT, "Vybrať"),
                            (InteractionMode.DRAW, "Kresliť"), (InteractionMode.PAN, "Posun")):
            button = self._button(label, lambda m=mode: view.set_interaction_mode(m))
            button.setCheckable(True)
            self._modes.addButton(button)
            self.mode_buttons[mode] = button
            layout.addWidget(button)
        self.mode_buttons[InteractionMode.SELECT].setToolTip("Vybrať a upraviť existujúcu geometriu (Esc)")
        self.mode_buttons[InteractionMode.DRAW].setToolTip("Kresliť aktuálnym ROI alebo maskovacím nástrojom")
        self.mode_buttons[InteractionMode.PAN].setToolTip("Posun: ťahanie; dočasne Space + ťahanie alebo stredné tlačidlo")
        self._tool_extension_layout = QHBoxLayout()
        self._tool_extension_layout.setContentsMargins(0, 0, 0, 0)
        self._tool_extension_layout.setSpacing(4)
        layout.addLayout(self._tool_extension_layout)
        layout.addSpacing(8)
        self._history_layout = QHBoxLayout()
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(4)
        layout.addLayout(self._history_layout)
        layout.addStretch(1)
        self.zoom_out_button = self._button("−", view.zoom_out, "Zoom out (−)")
        layout.addWidget(self.zoom_out_button)
        self.zoom_label = QLabel(self)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(52)
        layout.addWidget(self.zoom_label)
        self.zoom_in_button = self._button("+", view.zoom_in, "Zoom in (+ or =)")
        layout.addWidget(self.zoom_in_button)
        self.fit_button = self._button("Prispôsobiť", view.fit_image_to_view, "Prispôsobiť celý obraz (F)")
        layout.addWidget(self.fit_button)
        self.actual_size_button = self._button("1:1", view.reset_zoom_100, "100%: one image pixel per view pixel (1)")
        layout.addWidget(self.actual_size_button)
        view.zoomChanged.connect(self._update_zoom)
        view.interactionModeChanged.connect(self._update_mode)
        view.imageChanged.connect(self.setEnabled)
        self.setEnabled(view._pixmap_item is not None)
        self._update_zoom(view.zoom())
        self._update_mode(view.interaction_mode())

    def set_draw_tools(self, tools: list[tuple[str, Callable, str]]) -> list[QToolButton]:
        """Replace the generic Draw action with editor-specific draw tools."""
        generic = self.mode_buttons[InteractionMode.DRAW]
        generic.hide()
        insert_at = self._mode_layout.indexOf(generic)
        new_buttons: list[QToolButton] = []
        for offset, (label, action, tooltip) in enumerate(tools):
            button = self._button(label, action, tooltip)
            button.setCheckable(True)
            self._modes.addButton(button)
            self._mode_layout.insertWidget(insert_at + offset, button)
            self.draw_buttons.append(button)
            new_buttons.append(button)
        return new_buttons

    def add_context_buttons(self, buttons: list[QToolButton]) -> None:
        for button in buttons:
            self._context_layout.addWidget(button)
        if buttons:
            self._context_layout.addSpacing(8)

    def add_tool_buttons(self, buttons: list[QToolButton]) -> None:
        for button in buttons:
            self._tool_extension_layout.addWidget(button)

    def add_history_buttons(self, buttons: list[QToolButton]) -> None:
        for button in buttons:
            self._history_layout.addWidget(button)

    def _button(self, text: str, action: Callable, tooltip: str = "") -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False: self._activate(action))
        return button

    def _activate(self, action: Callable) -> None:
        action()
        self._view.setFocus(Qt.OtherFocusReason)

    def _update_zoom(self, scale: float) -> None:
        self.zoom_label.setText(f"{scale * 100:.0f} %")

    def _update_mode(self, mode: InteractionMode) -> None:
        if mode == InteractionMode.DRAW and self.draw_buttons:
            if not any(button.isChecked() for button in self.draw_buttons):
                self.draw_buttons[0].setChecked(True)
        else:
            self.mode_buttons[mode].setChecked(True)

# app/ui/draw_view.py
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPen, QBrush, QColor, QPainterPath, QPainter, QImage, QPixmap
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsPathItem,
    QWidget, QVBoxLayout, QHBoxLayout, QRadioButton, QButtonGroup,
    QSlider, QLabel, QPushButton
)

from collections import deque
import math
from typing import Optional, Tuple

import cv2
import numpy as np

# Farby podľa špecifikácie
COLOR_POSE   = QColor(0, 153, 255)   # Modrá
PEN_W = 2.0

def _pen(color):   return QPen(color, PEN_W, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
def _brush(color): return QBrush(Qt.transparent)

# --- Vykresľovacie itemy (po dokončení kreslenia budú movable/selectable) ---

class RectItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, color: QColor):
        super().__init__(rect.normalized())
        self.reg_type = None
        self.setPen(_pen(color))
        self.setBrush(_brush(color))
        # POZOR: počas kreslenia nepovoľujeme pohyb, nastavíme až po dokončení.

    def finalize(self):
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

class CircleItem(QGraphicsEllipseItem):
    def __init__(self, cx: float, cy: float, r: float, color: QColor):
        super().__init__(cx - r, cy - r, 2*r, 2*r)
        self.reg_type = None
        self.setPen(_pen(color))
        self.setBrush(_brush(color))

    def center_radius(self):
        r = self.rect()
        cx = r.center().x()
        cy = r.center().y()
        rad = r.width() / 2.0
        return cx, cy, rad

    def set_center_radius(self, cx, cy, rad):
        self.setRect(QRectF(cx - rad, cy - rad, 2*rad, 2*rad))

    def finalize(self):
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

class PolyItem(QGraphicsPathItem):
    def __init__(self, pts, color: QColor):
        super().__init__()
        self.reg_type = None
        self._pts = [QPointF(p[0], p[1]) for p in pts]
        self.setPen(_pen(color))
        self.setBrush(_brush(color))
        self._update_path()

    def _update_path(self, preview_point: QPointF | None = None):
        path = QPainterPath()
        if self._pts:
            path.moveTo(self._pts[0])
            for p in self._pts[1:]:
                path.lineTo(p)
            if preview_point is not None:
                path.lineTo(preview_point)
        self.setPath(path)

    def points(self):
        return [(p.x(), p.y()) for p in self._pts]

    def add_point(self, p: QPointF):
        self._pts.append(p)
        self._update_path()

    def finalize(self):
        # uzavriť polygon
        if len(self._pts) >= 3:
            path = QPainterPath()
            path.moveTo(self._pts[0])
            for p in self._pts[1:]:
                path.lineTo(p)
            path.closeSubpath()
            self.setPath(path)
            self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

# --- Pomocné výpočty pre 3-bodovú kružnicu ---
def _circumcircle(p1: QPointF, p2: QPointF, p3: QPointF):
    """
    Vráti (cx, cy, r) kružnice cez tri body; ak sú kolineárne → None.
    """
    x1, y1 = p1.x(), p1.y()
    x2, y2 = p2.x(), p2.y()
    x3, y3 = p3.x(), p3.y()

    d = 2 * ((x1*(y2 - y3)) + x2*(y3 - y1) + x3*(y1 - y2))
    if abs(d) < 1e-6:
        return None

    ux = ((x1*x1 + y1*y1)*(y2 - y3) + (x2*x2 + y2*y2)*(y3 - y1) + (x3*x3 + y3*y3)*(y1 - y2)) / d
    uy = ((x1*x1 + y1*y1)*(x3 - x2) + (x2*x2 + y2*y2)*(x1 - x3) + (x3*x3 + y3*y3)*(x2 - x1)) / d
    r  = math.hypot(ux - x1, uy - y1)
    return ux, uy, r

class DrawView(QGraphicsView):
    """
    Režimy:
      - shape: "rect" | "circle" | "poly"
      - reg_type: len globálny "pose"
    Ovládanie:
      - RECT: 1. klik = začiatok, ťah myšou = náhľad, uvoľnenie = fixácia
      - CIRCLE: 1. a 2. klik = body na obvode; po 2. kliku živý náhľad (3. bod = kurzor);
                3. klik = fixácia kružnice cez 3 body
      - POLY: klikmi pridávaš vrcholy; náhľad k biežnemu kurzoru; dvojklik/pravý klik = uzavrie polygon
      - Delete = zmazať vybratý item
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.TextAntialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.current_shape = "rect"

        self._bg = None

        # stavové premenné pre kreslenie
        self._drawing_rect = False
        self._rect_start: QPointF | None = None
        self._rect_item: RectItem | None = None

        self._circle_pts: list[QPointF] = []   # uložíme 1–3 body
        self._circle_item: CircleItem | None = None

        self._poly_item: PolyItem | None = None
        self._poly_preview_on = False

    # API z Golden WIZARD
    def set_background(self, qpixmap):
        self._scene.clear()
        self._bg = self._scene.addPixmap(qpixmap)
        self._bg.setZValue(-1000)

    def set_shape_type(self, shape: str):
        self.current_shape = shape

    # --- myš a klávesy ---
    def mousePressEvent(self, ev):
        pos = self.mapToScene(ev.pos())

        if ev.button() == Qt.LeftButton:
            color = COLOR_POSE

            if self.current_shape == "rect":
                if self._pose_count() >= 1:
                    # už existuje – nič nové nevytváraj (užívateľ môže predošlý zmazať a nakresliť znova)
                    return
                # začni kresliť rectangle – 1. klik fixuje začiatočný bod
                self._drawing_rect = True
                self._rect_start = pos
                self._rect_item = RectItem(QRectF(pos.x(), pos.y(), 1, 1), color)
                self._rect_item.reg_type = "pose"
                self._scene.addItem(self._rect_item)
                return

            elif self.current_shape == "circle":
                if self._pose_count() >= 1 and len(self._circle_pts) == 0:
                    return

                # 3-bodové: zbieraj body na obvode
                self._circle_pts.append(pos)
                if len(self._circle_pts) == 1:
                    # ešte nič nekreslíme
                    return
                elif len(self._circle_pts) == 2:
                    # po 2. kliku začni zobrazovať náhľad podľa kurzora
                    # dočasne si pripravíme prázdny CircleItem s r=1
                    p1, p2 = self._circle_pts
                    cx = (p1.x() + p2.x()) / 2.0
                    cy = (p1.y() + p2.y()) / 2.0
                    self._circle_item = CircleItem(cx, cy, 1.0, color)
                    self._circle_item.reg_type = "pose"
                    self._scene.addItem(self._circle_item)
                    return
                elif len(self._circle_pts) == 3:
                    # 3. klik = finálne uzamknutie na circumcircle
                    p1, p2, p3 = self._circle_pts
                    cc = _circumcircle(p1, p2, p3)
                    if cc is not None:
                        cx, cy, r = cc
                        self._circle_item.set_center_radius(cx, cy, r)
                        self._circle_item.finalize()
                    else:
                        # body sú kolineárne → kružnicu nemožno definovať
                        # zmažeme dočasný item
                        if self._circle_item is not None:
                            self._scene.removeItem(self._circle_item)
                        self._circle_item = None
                    # reset stavu
                    self._circle_pts = []
                    self._circle_item = None
                    return

            elif self.current_shape == "poly":
                if self._poly_item is None:
                    if self._pose_count() >= 1:
                        return
                    # založ nový polygon...

                if self._poly_item is None:
                    # založ nový polygon s 1. bodom
                    self._poly_item = PolyItem([(pos.x(), pos.y())], color)
                    self._poly_item.reg_type = "pose"
                    self._scene.addItem(self._poly_item)
                    self._poly_preview_on = True
                else:
                    # pridaj ďalší vrchol
                    self._poly_item.add_point(pos)
                    self._poly_preview_on = True
                return

        elif ev.button() == Qt.RightButton:
            # ukončenie polygonu pravým klikom
            if self.current_shape == "poly" and self._poly_item is not None:
                if len(self._poly_item.points()) >= 3:
                    self._poly_item.finalize()
                else:
                    self._scene.removeItem(self._poly_item)
                self._poly_item = None
                self._poly_preview_on = False
                return

        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        pos = self.mapToScene(ev.pos())

        # živý náhľad rect počas ťahu
        if self._drawing_rect and self._rect_item and self._rect_start:
            x0, y0 = self._rect_start.x(), self._rect_start.y()
            w = pos.x() - x0
            h = pos.y() - y0
            self._rect_item.setRect(QRectF(x0, y0, w, h).normalized())

        # živý náhľad 3-bodovej kružnice (po druhom kliku)
        if self._circle_item is not None and len(self._circle_pts) == 2:
            p1, p2 = self._circle_pts
            p3 = pos
            cc = _circumcircle(p1, p2, p3)
            if cc is not None:
                cx, cy, r = cc
                # zmysluplný minimálny polomer
                r = max(r, 1.0)
                self._circle_item.set_center_radius(cx, cy, r)

        # živý náhľad polygonu (hrana k biežnemu kurzoru)
        if self._poly_item is not None and self._poly_preview_on:
            self._poly_item._update_path(preview_point=pos)

        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            # dokončenie rect po pustení myši
            if self._drawing_rect and self._rect_item is not None:
                rect = self._rect_item.rect().normalized()
                if rect.width() < 2 or rect.height() < 2:
                    # príliš malý – zahoď
                    self._scene.removeItem(self._rect_item)
                else:
                    self._rect_item.finalize()
                self._drawing_rect = False
                self._rect_item = None
                self._rect_start = None
                return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        # dvojklik uzavrie polygon
        if self.current_shape == "poly" and self._poly_item is not None:
            if len(self._poly_item.points()) >= 3:
                self._poly_item.finalize()
                self._poly_item = None
                self._poly_preview_on = False
                return
        super().mouseDoubleClickEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Delete:
            for it in self._scene.selectedItems():
                self._scene.removeItem(it)
            return
        super().keyPressEvent(ev)

    # --- export do JSON-friendly štruktúr ---
    def export_regions(self):
        regs = []
        for it in self._scene.items():
            if it is self._bg:
                continue
            if getattr(it, "reg_type", None) not in (None, "pose"):
                # globálne ROI/ignore už nepodporujeme
                continue
            if isinstance(it, RectItem):
                r = it.rect().normalized()
                regs.append({
                    "reg_type": "pose",
                    "shape": "rect",
                    "geom": [r.x(), r.y(), r.width(), r.height()],
                })
            elif isinstance(it, CircleItem):
                cx, cy, rad = it.center_radius()
                regs.append({
                    "reg_type": "pose",
                    "shape": "circle",
                    "geom": [cx, cy, rad],
                })
            elif isinstance(it, PolyItem):
                regs.append({
                    "reg_type": "pose",
                    "shape": "poly",
                    "geom": it.points(),
                })
        return regs
    
    def _pose_count(self) -> int:
        n = 0
        for it in self._scene.items():
            if it is self._bg:
                continue
            if getattr(it, "reg_type", None) == "pose":
                n += 1
        return n


    def set_background_image(self, img_u8):
        """
        Aktualizuje podklad kreslenia (pozadie) novou snímkou (uint8, GRAY8).
        Zachová všetky prekreslené ROI/POSE/IGNORE vrsty.
        """
        if img_u8 is None:
            return
        h, w = img_u8.shape[:2]
        q = QImage(img_u8.data, w, h, w, QImage.Format_Grayscale8)
        pm = QPixmap.fromImage(q.copy())
        if hasattr(self, "_bg") and self._bg is not None:
            self._bg.setPixmap(pm)
            if hasattr(self, "_scene"):
                self._scene.setSceneRect(0, 0, w, h)
            self.update()


class RoiMaskGraphicsView(QGraphicsView):
    """Graphics view that allows ROI selection and ignore mask painting."""

    roiChanged = Signal(object)
    maskChanged = Signal(object)

    MODE_ROI = "roi"
    MODE_MASK = "mask"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)

        scene = QGraphicsScene(self)
        self.setScene(scene)
        self._scene: QGraphicsScene = scene

        self._bg_item = None
        self._mask_item = None
        self._roi_item = None

        self._mode = self.MODE_ROI
        self._brush_size = 24
        self._mask: Optional[np.ndarray] = None
        self._mask_history: deque[np.ndarray] = deque(maxlen=20)

        self._painting = False
        self._paint_add = True
        self._mask_dirty = False
        self._last_paint_point: Optional[Tuple[int, int]] = None

        self._roi_start: Optional[QPointF] = None
        self._roi_rect: Optional[Tuple[int, int, int, int]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self._scene.clear()
        self._bg_item = None
        self._mask_item = None
        self._roi_item = None
        self._mask = None
        self._mask_history.clear()
        self._roi_rect = None
        self._roi_start = None

        if pixmap is None or pixmap.isNull():
            return

        self._bg_item = self._scene.addPixmap(pixmap)
        self._bg_item.setZValue(-100)
        self._scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self._ensure_mask_graphics()
        self._mask = np.zeros((pixmap.height(), pixmap.width()), dtype=np.uint8)
        self._update_mask_item()

    def set_mode(self, mode: str) -> None:
        self._mode = self.MODE_MASK if mode == self.MODE_MASK else self.MODE_ROI

    def set_brush_size(self, radius: int) -> None:
        radius = max(1, int(radius))
        self._brush_size = radius

    def roi(self) -> Optional[Tuple[int, int, int, int]]:
        return tuple(self._roi_rect) if self._roi_rect is not None else None

    def mask(self) -> Optional[np.ndarray]:
        if self._mask is None:
            return None
        return self._mask.copy()

    def set_roi(self, roi: Optional[Tuple[int, int, int, int]]) -> None:
        self._apply_roi(None if roi is None else tuple(map(int, roi)), emit=False)

    def reset_roi(self) -> None:
        self._apply_roi(None, emit=True)

    def clear_mask(self) -> None:
        if self._mask is None:
            return
        if not np.any(self._mask):
            return
        self._push_mask_history()
        self._mask.fill(0)
        self._update_mask_item()
        self._emit_mask_changed()

    def undo_mask(self) -> None:
        if not self._mask_history:
            return
        self._mask = self._mask_history.pop()
        self._update_mask_item()
        self._emit_mask_changed()

    def set_mask(self, mask: Optional[np.ndarray]) -> None:
        if self._bg_item is None:
            self._mask = None if mask is None else np.asarray(mask, dtype=np.uint8)
            return

        self._ensure_mask_graphics()
        rect = self._scene.sceneRect()
        width = int(rect.width())
        height = int(rect.height())

        if mask is None:
            self._mask = np.zeros((height, width), dtype=np.uint8)
        else:
            arr = np.asarray(mask)
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            if arr.ndim != 2:
                raise ValueError("Mask must be 2D")
            if arr.shape != (height, width):
                if width <= 0 or height <= 0:
                    return
                arr = cv2.resize(arr, (width, height), interpolation=cv2.INTER_NEAREST)
            self._mask = arr.astype(np.uint8, copy=False)

        self._mask_history.clear()
        self._update_mask_item()
        self._emit_mask_changed()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        scene_pos = self.mapToScene(event.pos())

        if self._mode == self.MODE_ROI and event.button() == Qt.LeftButton:
            if self._bg_item is None:
                return
            self._roi_start = scene_pos
            return

        if self._mode == self.MODE_MASK and event.button() in (Qt.LeftButton, Qt.RightButton):
            if not self._ensure_mask_array():
                return
            self._push_mask_history()
            self._painting = True
            self._paint_add = event.button() == Qt.LeftButton
            self._mask_dirty = False
            self._last_paint_point = None
            self._apply_mask_point(scene_pos)
            self._mask_dirty = True
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        scene_pos = self.mapToScene(event.pos())

        if self._mode == self.MODE_ROI and self._roi_start is not None:
            self._update_roi_preview(self._roi_start, scene_pos)
            return

        if self._mode == self.MODE_MASK and self._painting:
            self._apply_mask_point(scene_pos)
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._mode == self.MODE_ROI and event.button() == Qt.LeftButton and self._roi_start is not None:
            rect = self._rect_from_points(self._roi_start, self.mapToScene(event.pos()))
            self._roi_start = None
            self._apply_roi(rect, emit=True)
            return

        if self._mode == self.MODE_MASK and event.button() in (Qt.LeftButton, Qt.RightButton) and self._painting:
            self._painting = False
            self._last_paint_point = None
            if self._mask_dirty:
                self._emit_mask_changed()
            return

        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._mode == self.MODE_ROI and self._roi_start is not None:
            self._roi_start = None
            self._apply_roi(self._roi_rect, emit=False)
        super().leaveEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_mask_graphics(self) -> None:
        if self._mask_item is not None:
            return
        rect = self._scene.sceneRect()
        width = int(rect.width())
        height = int(rect.height())
        if width <= 0 or height <= 0:
            return
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.transparent)
        self._mask_item = self._scene.addPixmap(pixmap)
        self._mask_item.setZValue(-50)
        self._mask_item.setVisible(False)
        self._mask_item.setAcceptedMouseButtons(Qt.NoButton)

    def _ensure_mask_array(self) -> bool:
        if self._mask is not None:
            return True
        rect = self._scene.sceneRect()
        width = int(rect.width())
        height = int(rect.height())
        if width <= 0 or height <= 0:
            return False
        self._mask = np.zeros((height, width), dtype=np.uint8)
        self._ensure_mask_graphics()
        self._update_mask_item()
        return True

    def _push_mask_history(self) -> None:
        if self._mask is None:
            return
        self._mask_history.append(self._mask.copy())

    def _emit_mask_changed(self) -> None:
        if self._mask is None:
            self.maskChanged.emit(None)
        else:
            self.maskChanged.emit(self._mask.copy())

    def _apply_roi(self, rect: Optional[Tuple[int, int, int, int]], *, emit: bool) -> None:
        self._roi_rect = rect if rect is not None else None
        self._update_roi_item(None if rect is None else QRectF(rect[0], rect[1], rect[2], rect[3]))
        if emit:
            self.roiChanged.emit(self.roi())

    def _update_roi_item(self, rect: Optional[QRectF]) -> None:
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            if self._roi_item is not None:
                self._scene.removeItem(self._roi_item)
                self._roi_item = None
            return

        if self._roi_item is None:
            pen = QPen(COLOR_ROI)
            pen.setWidthF(2.0)
            pen.setCosmetic(True)
            self._roi_item = self._scene.addRect(rect, pen, QBrush(Qt.transparent))
            self._roi_item.setZValue(200)
        else:
            self._roi_item.setRect(rect)
        self._roi_item.setVisible(True)

    def _clamp_point(self, point: QPointF) -> Tuple[int, int]:
        rect = self._scene.sceneRect()
        width = max(1, int(rect.width()))
        height = max(1, int(rect.height()))
        x = int(round(point.x()))
        y = int(round(point.y()))
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        return x, y

    def _rect_from_points(self, p1: QPointF, p2: QPointF) -> Optional[Tuple[int, int, int, int]]:
        if self._bg_item is None:
            return None
        x1, y1 = self._clamp_point(p1)
        x2, y2 = self._clamp_point(p2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)
        if w < 2 or h < 2:
            return None
        x = min(x1, x2)
        y = min(y1, y2)
        return x, y, w, h

    def _update_roi_preview(self, start: QPointF, current: QPointF) -> None:
        rect = self._rect_from_points(start, current)
        if rect is None:
            self._update_roi_item(None)
        else:
            self._update_roi_item(QRectF(rect[0], rect[1], rect[2], rect[3]))

    def _apply_mask_point(self, pos: QPointF) -> None:
        if self._mask is None:
            return
        width = self._mask.shape[1]
        height = self._mask.shape[0]
        if width == 0 or height == 0:
            return

        x = int(round(pos.x()))
        y = int(round(pos.y()))
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        pt = (x, y)
        value = 255 if self._paint_add else 0

        if self._last_paint_point is None:
            cv2.circle(self._mask, pt, self._brush_size, value, thickness=-1)
        else:
            cv2.line(self._mask, self._last_paint_point, pt, value, thickness=max(1, self._brush_size * 2), lineType=cv2.LINE_AA)
            cv2.circle(self._mask, pt, self._brush_size, value, thickness=-1)

        self._last_paint_point = pt
        self._update_mask_item()
        self._mask_dirty = True

    def _update_mask_item(self) -> None:
        if self._mask_item is None or self._mask is None:
            return

        if not np.any(self._mask):
            pixmap = self._mask_item.pixmap()
            if not pixmap.isNull():
                pixmap.fill(Qt.transparent)
                self._mask_item.setPixmap(pixmap)
            self._mask_item.setVisible(False)
            return

        height, width = self._mask.shape
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        mask_idx = self._mask > 0
        rgba[mask_idx, 0] = 255
        rgba[mask_idx, 1] = 0
        rgba[mask_idx, 2] = 200
        rgba[mask_idx, 3] = 110
        img = QImage(rgba.data, width, height, width * 4, QImage.Format_RGBA8888)
        self._mask_item.setPixmap(QPixmap.fromImage(img.copy()))
        self._mask_item.setVisible(True)


class RoiMaskEditor(QWidget):
    """Composite widget bundling ROI selection and ignore mask drawing."""

    roiChanged = Signal(object)
    maskChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._roi_enabled = True
        self._mask_enabled = True

        self.view = RoiMaskGraphicsView(self)
        self.view.roiChanged.connect(self.roiChanged)
        self.view.maskChanged.connect(self.maskChanged)

        self._mode_roi = QRadioButton("ROI", self)
        self._mode_mask = QRadioButton("Mask", self)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._mode_roi)
        self._mode_group.addButton(self._mode_mask)
        self._mode_roi.setChecked(True)

        self._mode_roi.toggled.connect(self._on_mode_changed)
        self._mode_mask.toggled.connect(self._on_mode_changed)

        self._brush_slider = QSlider(Qt.Horizontal, self)
        self._brush_slider.setRange(3, 120)
        self._brush_slider.setValue(24)
        self._brush_slider.valueChanged.connect(self._on_brush_changed)

        self._brush_label = QLabel(self)
        self._update_brush_label(self._brush_slider.value())

        self._btn_reset_roi = QPushButton("Reset ROI", self)
        self._btn_clear_mask = QPushButton("Clear Mask", self)
        self._btn_undo_mask = QPushButton("Undo", self)

        self._btn_reset_roi.clicked.connect(self._on_reset_roi)
        self._btn_clear_mask.clicked.connect(self.view.clear_mask)
        self._btn_undo_mask.clicked.connect(self.view.undo_mask)

        self._mask_hint = QLabel("Mask mode: Left click adds, right click erases.", self)
        self._mask_hint.setStyleSheet("color: #666; font-size: 11px;")

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        controls.addWidget(self._mode_roi)
        controls.addWidget(self._mode_mask)
        controls.addSpacing(12)
        controls.addWidget(self._brush_label)
        controls.addWidget(self._brush_slider, 1)
        controls.addSpacing(12)
        controls.addWidget(self._btn_undo_mask)
        controls.addWidget(self._btn_clear_mask)
        controls.addSpacing(12)
        controls.addWidget(self._btn_reset_roi)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.view)
        layout.addLayout(controls)
        layout.addWidget(self._mask_hint)

        self.view.set_mode(RoiMaskGraphicsView.MODE_ROI)
        self.view.set_brush_size(self._brush_slider.value())
        self._update_mask_controls()

    # ------------------------------------------------------------------
    # Public facade
    # ------------------------------------------------------------------
    def set_background(self, pixmap: Optional[QPixmap]) -> None:
        self.view.set_background(pixmap)

    def set_roi(self, roi: Optional[Tuple[int, int, int, int]]) -> None:
        self.view.set_roi(roi)

    def set_mask(self, mask: Optional[np.ndarray]) -> None:
        self.view.set_mask(mask)

    def roi(self) -> Optional[Tuple[int, int, int, int]]:
        return self.view.roi()

    def mask(self) -> Optional[np.ndarray]:
        return self.view.mask()

    def set_roi_enabled(self, enabled: bool) -> None:
        self._roi_enabled = bool(enabled)
        if not self._roi_enabled and self._mode_roi.isChecked():
            self._mode_mask.setChecked(True)
        self._mode_roi.setEnabled(self._roi_enabled)
        self._btn_reset_roi.setEnabled(self._roi_enabled)
        self._update_mask_controls()

    def set_mask_enabled(self, enabled: bool) -> None:
        self._mask_enabled = bool(enabled)
        if not self._mask_enabled and self._mode_mask.isChecked():
            self._mode_roi.setChecked(True)
        self._mode_mask.setEnabled(self._mask_enabled)
        self._btn_clear_mask.setEnabled(self._mask_enabled)
        self._btn_undo_mask.setEnabled(self._mask_enabled)
        self._update_mask_controls()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_mode_changed(self) -> None:
        mode = RoiMaskGraphicsView.MODE_MASK if self._mode_mask.isChecked() else RoiMaskGraphicsView.MODE_ROI
        self.view.set_mode(mode)
        self._update_mask_controls()

    def _on_brush_changed(self, value: int) -> None:
        self.view.set_brush_size(value)
        self._update_brush_label(value)

    def _on_reset_roi(self) -> None:
        if not self._roi_enabled:
            return
        self.view.reset_roi()

    def _update_brush_label(self, value: int) -> None:
        self._brush_label.setText(f"Brush: {int(value)} px")

    def _update_mask_controls(self) -> None:
        mask_controls = self._mask_enabled and self._mode_mask.isChecked()
        self._brush_slider.setEnabled(mask_controls)
        self._brush_label.setEnabled(mask_controls)
        self._mask_hint.setVisible(mask_controls)


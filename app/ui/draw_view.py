# app/ui/draw_view.py
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPen, QBrush, QColor, QPainterPath, QPainter
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsPathItem

import math

COLOR_POSE   = QColor(0, 153, 255)   # Blue
COLOR_ROI    = QColor(0, 200, 0)     # Green
COLOR_IGNORE = QColor(255, 0, 200)   # Magenta

PEN_W = 2.0

def _pen(color):   return QPen(color, PEN_W, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
def _brush(color): return QBrush(color, Qt.Dense4Pattern)

class HandleItem(QGraphicsEllipseItem):
    def __init__(self, x, y, r=6, parent=None):
        super().__init__(-r, -r, 2*r, 2*r, parent)
        self.setPos(QPointF(x, y))
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.black, 1))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemIgnoresTransformations)

class RectItem(QGraphicsRectItem):
    def __init__(self, x, y, w, h, color):
        super().__init__(x, y, w, h)
        self.color = color
        self.setPen(_pen(color))
        self.setBrush(QBrush(Qt.transparent))
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

class CircleItem(QGraphicsEllipseItem):
    def __init__(self, cx, cy, r, color):
        super().__init__(cx-r, cy-r, 2*r, 2*r)
        self.color = color
        self.setPen(_pen(color))
        self.setBrush(QBrush(Qt.transparent))
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

class PolyItem(QGraphicsPathItem):
    def __init__(self, pts, color):
        super().__init__()
        self.color = color
        self._pts = [QPointF(p[0], p[1]) for p in pts]
        self.update_path()
        self.setPen(_pen(color))
        self.setBrush(QBrush(Qt.transparent))
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

    def update_path(self):
        path = QPainterPath()
        if self._pts:
            path.moveTo(self._pts[0])
            for p in self._pts[1:]:
                path.lineTo(p)
            path.closeSubpath()
        self.setPath(path)

    def set_points(self, pts):
        self._pts = [QPointF(p[0], p[1]) for p in pts]
        self.update_path()

    def points(self):
        return [(p.x(), p.y()) for p in self._pts]

class DrawView(QGraphicsView):
    """
    Jednoduché kreslenie nad bitmapou:
      - moda: "rect" | "circle" | "poly"
      - typ regiónu: "pose" | "roi" | "ignore"
      - Del = zmazať vybraný item
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.TextAntialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setMouseTracking(True)

        self.current_shape = "rect"
        self.current_type  = "pose"
        self._start = None
        self._temp_item = None

    def set_background(self, qpixmap):
        self.scene.clear()
        self._bg = self.scene.addPixmap(qpixmap)
        self._bg.setZValue(-1000)

    def _color_for_type(self, reg_type):
        return COLOR_POSE if reg_type == "pose" else (COLOR_ROI if reg_type == "roi" else COLOR_IGNORE)

    def set_shape_type(self, shape: str):
        self.current_shape = shape

    def set_region_type(self, reg_type: str):
        self.current_type = reg_type

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._start = self.mapToScene(ev.pos())
            color = self._color_for_type(self.current_type)
            if self.current_shape == "rect":
                self._temp_item = RectItem(self._start.x(), self._start.y(), 1, 1, color)
                self.scene.addItem(self._temp_item)
            elif self.current_shape == "circle":
                self._temp_item = CircleItem(self._start.x(), self._start.y(), 1, color)
                self.scene.addItem(self._temp_item)
            elif self.current_shape == "poly":
                # začneme poly s dvomi rovnakými bodmi; ukončíme pravým klikom
                self._poly_pts = [self._start, self._start]
                self._temp_item = PolyItem([(p.x(), p.y()) for p in self._poly_pts], color)
                self.scene.addItem(self._temp_item)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._temp_item and self._start:
            pos = self.mapToScene(ev.pos())
            if isinstance(self._temp_item, RectItem):
                x0, y0 = self._start.x(), self._start.y()
                w = pos.x() - x0
                h = pos.y() - y0
                self._temp_item.setRect(QRectF(x0, y0, w, h).normalized())
            elif isinstance(self._temp_item, CircleItem):
                cx, cy = self._start.x(), self._start.y()
                r = math.hypot(pos.x() - cx, pos.y() - cy)
                self._temp_item.setRect(QRectF(cx - r, cy - r, 2*r, 2*r))
            elif isinstance(self._temp_item, PolyItem):
                self._poly_pts[-1] = self.mapToScene(ev.pos())
                self._temp_item.set_points([(p.x(), p.y()) for p in self._poly_pts])
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._temp_item:
            if isinstance(self._temp_item, PolyItem):
                # nič – poly ukončíme pravým tlačidlom
                pass
            else:
                self._temp_item = None
                self._start = None
        elif ev.button() == Qt.RightButton and isinstance(self._temp_item, PolyItem):
            # ukonči polygon
            if len(self._poly_pts) >= 3:
                self._temp_item = None
                self._start = None
            else:
                # príliš krátke – zmaž
                self.scene.removeItem(self._temp_item)
                self._temp_item = None
                self._start = None
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if self._temp_item and isinstance(self._temp_item, PolyItem):
            # dvojklik = uzavri polygon
            if len(self._poly_pts) >= 3:
                self._temp_item = None
                self._start = None
        else:
            super().mouseDoubleClickEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Delete:
            for it in self.scene.selectedItems():
                self.scene.removeItem(it)
        super().keyPressEvent(ev)

    # --- export všetkých regiónov vo view ---
    def export_regions(self):
        regs = []
        for it in self.scene.items():
            if it is self._bg:
                continue
            if isinstance(it, RectItem):
                r = it.rect().normalized()
                reg_type = self._type_from_color(it.color)
                regs.append({"reg_type": reg_type, "shape": "rect", "geom": [r.x(), r.y(), r.width(), r.height()]})
            elif isinstance(it, CircleItem):
                r = it.rect()
                cx = r.center().x(); cy = r.center().y()
                rad = r.width()/2.0
                reg_type = self._type_from_color(it.color)
                regs.append({"reg_type": reg_type, "shape": "circle", "geom": [cx, cy, rad]})
            elif isinstance(it, PolyItem):
                reg_type = self._type_from_color(it.color)
                regs.append({"reg_type": reg_type, "shape": "poly", "geom": it.points()})
        return regs

    def _type_from_color(self, c: QColor) -> str:
        if c == COLOR_POSE: return "pose"
        if c == COLOR_ROI:  return "roi"
        return "ignore"

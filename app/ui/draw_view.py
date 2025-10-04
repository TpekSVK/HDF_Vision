# app/ui/draw_view.py
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPen, QBrush, QColor, QPainterPath, QPainter, QImage, QPixmap
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsPathItem
)

import math

# Farby podľa špecifikácie
COLOR_POSE   = QColor(0, 153, 255)   # Modrá
COLOR_ROI    = QColor(0, 200, 0)     # Zelená
COLOR_IGNORE = QColor(255, 0, 200)   # Magenta
PEN_W = 2.0

def _pen(color):   return QPen(color, PEN_W, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
def _brush(color): return QBrush(Qt.transparent)

def _color_for_type(reg_type: str) -> QColor:
    return COLOR_POSE if reg_type == "pose" else (COLOR_ROI if reg_type == "roi" else COLOR_IGNORE)

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
      - reg_type: "pose" | "roi" | "ignore"
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
        self.current_type  = "pose"

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

    def set_region_type(self, reg_type: str):
        self.current_type = reg_type

    # --- myš a klávesy ---
    def mousePressEvent(self, ev):
        pos = self.mapToScene(ev.pos())

        if ev.button() == Qt.LeftButton:
            color = _color_for_type(self.current_type)

            if self.current_shape == "rect":
                if self.current_type in ("pose", "roi") and self._count_type(self.current_type) >= 1:
                    # už existuje – nič nové nevytváraj (užívateľ môže predošlý zmazať a nakresliť znova)
                    return
                # začni kresliť rectangle – 1. klik fixuje začiatočný bod
                self._drawing_rect = True
                self._rect_start = pos
                self._rect_item = RectItem(QRectF(pos.x(), pos.y(), 1, 1), color)
                self._rect_item.reg_type = self.current_type
                self._scene.addItem(self._rect_item)
                return

            elif self.current_shape == "circle":
                if self.current_type in ("pose", "roi") and self._count_type(self.current_type) >= 1 and len(self._circle_pts) == 0:
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
                    self._circle_item.reg_type = self.current_type
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
                    if self.current_type in ("pose", "roi") and self._count_type(self.current_type) >= 1:
                        return
                    # založ nový polygon...

                if self._poly_item is None:
                    # založ nový polygon s 1. bodom
                    self._poly_item = PolyItem([(pos.x(), pos.y())], color)
                    self._poly_item.reg_type = self.current_type
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
            if isinstance(it, RectItem):
                r = it.rect().normalized()
                regs.append({
                    "reg_type": it.reg_type,
                    "shape": "rect",
                    "geom": [r.x(), r.y(), r.width(), r.height()],
                })
            elif isinstance(it, CircleItem):
                cx, cy, rad = it.center_radius()
                regs.append({
                    "reg_type": it.reg_type,
                    "shape": "circle",
                    "geom": [cx, cy, rad],
                })
            elif isinstance(it, PolyItem):
                regs.append({
                    "reg_type": it.reg_type,
                    "shape": "poly",
                    "geom": it.points(),
                })
        return regs
    
    def _count_type(self, reg_type: str) -> int:
        n = 0
        for it in self._scene.items():
            if it is self._bg:
                continue
            if hasattr(it, "reg_type") and it.reg_type == reg_type:
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

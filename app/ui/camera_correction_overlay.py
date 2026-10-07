"""Viewport-only camera correction arrows; RAW pixels remain untouched."""
import math
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from app.ui.image_canvas import ImageView


class CorrectionImageView(ImageView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.corrections = ()

    def set_corrections(self, corrections):
        self.corrections = tuple(dict.fromkeys(c[0] for c in corrections if c and c[0] in '→←↓↑↻↺'))
        self.viewport().update()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if self._pixmap_item is None or not self.corrections:
            return
        area = self.mapFromScene(self._pixmap_item.sceneBoundingRect()).boundingRect().intersected(self.viewport().rect())
        if area.width() < 40 or area.height() < 40:
            return
        size = min(52, area.height() / 3, (area.width() - 16) / len(self.corrections))
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.Antialiasing)
        for i, direction in enumerate(self.corrections):
            painter.save()
            painter.translate(area.center().x() + (i - (len(self.corrections)-1)/2)*size, area.top()+size*.7)
            painter.scale(size/50, size/50)
            path = QPainterPath()
            if direction in '→←↓↑':
                painter.rotate({'→': 0, '↓': 90, '←': 180, '↑': 270}[direction])
                path.addPolygon(QPolygonF([QPointF(x,y) for x,y in [(-20,-5),(3,-5),(3,-14),(21,0),(3,14),(3,5),(-20,5)]]))
                path.closeSubpath()
            else:
                sign = 1 if direction == '↻' else -1
                points = [QPointF(14*math.cos(math.radians(-210+sign*j*240/40)),14*math.sin(math.radians(-210+sign*j*240/40))) for j in range(41)]
                arc = QPainterPath(points[0])
                for point in points[1:]:
                    arc.lineTo(point)
                for color, width in [('#fff4d4', 11), ('#171c24', 9), ('#ffc247', 6)]:
                    painter.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    painter.drawPath(arc)
                end = points[-1]
                angle = math.radians(-210+sign*240)
                tangent = QPointF(-sign*math.sin(angle), sign*math.cos(angle))
                normal = QPointF(-tangent.y(), tangent.x())
                path.addPolygon(QPolygonF([end+tangent*9, end-tangent*5+normal*8, end-tangent*5-normal*8]))
                path.closeSubpath()
            painter.setBrush(QColor('#ffc247'))
            for color, width in [('#fff4d4', 4), ('#171c24', 2)]:
                painter.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPath(path)
            painter.restore()
        painter.restore()

# app/ui/results_strip.py
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QScrollArea, QPushButton
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt
import imageio.v3 as iio
import os

class ThumbLabel(QLabel):
    def __init__(self, path:str, ok:bool, info:str=""):
        super().__init__()
        self.path = path
        self.ok = ok
        self.info = info
        self.setToolTip(info)
        self.setFixedSize(120, 90)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("border: 3px solid " + ("#33dd66" if ok else "#ff3366") + ";")
        self.refresh()

    def refresh(self):
        if not self.path or not os.path.exists(self.path):
            self.setText("—")
            return
        try:
            img = iio.imread(self.path)
            if img.ndim == 3:
                img = img[:,:,0]
            h, w = img.shape[:2]
            q = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
            pm = QPixmap.fromImage(q.copy()).scaled(self.width()-6, self.height()-6, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(pm)
        except Exception:
            self.setText("X")

class ResultsStrip(QWidget):
    """
    Horizontálny strip posledných N thumbov za dnešok pre aktuálny recept.
    Očakáva .db (DbService) a .current_recipe_name()
    """
    def __init__(self, mw, limit=12):
        super().__init__(mw)
        self.mw = mw
        self.limit = int(limit)

        self.area = QScrollArea(self)
        self.area.setWidgetResizable(True)
        self.wrap = QWidget()
        self.h = QHBoxLayout(self.wrap)
        self.h.setContentsMargins(4,4,4,4)
        self.h.setSpacing(6)
        self.area.setWidget(self.wrap)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0)
        lay.addWidget(self.area)

    def reload(self):
        # vyčisti
        while self.h.count():
            item = self.h.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        # načítaj
        rid = self.mw.db.recipe_id(self.mw.current_recipe_name())
        if rid is None: return
        rows = self.mw.db.recent_results(rid, self.limit)
        for r in rows:
            info = f"SSIM={r['ssim']}  blobs={r['blob_count']}  area={r['total_area']}"
            t = ThumbLabel(r["thumb"], r["ok"], info)
            t.mousePressEvent = lambda e, rr=r: self._on_click(rr)
            self.h.addWidget(t)
        self.h.addStretch(1)

    def _on_click(self, row):
        # otvoríme full (ak je), inak thumb – v externom prehliadači (inside kontajnera to býva ťažké),
        # tak aspoň nastavíme status text
        full = row.get("full") or row.get("thumb")
        self.mw.lbl_status.setText(f"Open: {full}")

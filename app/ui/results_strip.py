# app/ui/results_strip.py
import json
import os
from pathlib import Path

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QScrollArea, QPushButton
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt
import imageio.v3 as iio

class ThumbLabel(QLabel):
    def __init__(self, path: str, ok: bool, info: str = "", status: str | None = None):
        super().__init__()
        self.path = path
        self.ok = ok
        self.info = info
        self.status = status
        self.setToolTip(info)
        self.setFixedSize(120, 90)
        self.setAlignment(Qt.AlignCenter)
        self._apply_border()
        self.refresh()

    def _apply_border(self) -> None:
        if self.status:
            color_map = {"ok": "#33dd66", "warn": "#e67e22", "nok": "#ff3366"}
            color = color_map.get(self.status, "#999999")
        else:
            color = "#33dd66" if self.ok else "#ff3366"
        self.setStyleSheet(f"border: 3px solid {color};")

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
            locator_status = None
            locator_info = self._load_locator_info(r)
            if locator_info:
                metrics = locator_info.get("metrics", {})
                corr = metrics.get("corr")
                dx = metrics.get("dx")
                dy = metrics.get("dy")
                locator_status = locator_info.get("status")
                parts = []
                if corr is not None:
                    parts.append(f"corr={corr:.3f}")
                if dx is not None:
                    parts.append(f"dx={dx:.2f}")
                if dy is not None:
                    parts.append(f"dy={dy:.2f}")
                if parts:
                    info += "  locator:" + "  ".join(parts)
                if locator_status:
                    info += f"  status={locator_status}"

            t = ThumbLabel(r["thumb"], r["ok"], info, locator_status)
            t.mousePressEvent = lambda e, rr=r: self._on_click(rr)
            self.h.addWidget(t)
        self.h.addStretch(1)

    def _load_locator_info(self, row: dict) -> dict | None:
        thumb_path = row.get("thumb")
        if not thumb_path:
            return None
        try:
            thumb = Path(thumb_path)
        except Exception:
            return None
        meta_path = thumb.with_suffix(".json")
        # thumbs/<file> -> meta/<file>
        if thumb.parent.name == "thumbs":
            meta_path = thumb.parent.parent / "meta" / meta_path.name
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except Exception:
            return None
        entries: list[dict] | None = None
        if isinstance(meta, dict):
            raw_tool_results = meta.get("tool_results")
            if isinstance(raw_tool_results, list):
                entries = [entry for entry in raw_tool_results if isinstance(entry, dict)]
            if entries is None:
                raw_tools = meta.get("tools")
                if isinstance(raw_tools, list):
                    entries = [entry for entry in raw_tools if isinstance(entry, dict)]
            if entries is None:
                raw_per_tool = meta.get("per_tool")
                if isinstance(raw_per_tool, list):
                    entries = [entry for entry in raw_per_tool if isinstance(entry, dict)]
        if not entries:
            return None
        for entry in entries:
            entry_type = entry.get("type") if isinstance(entry, dict) else None
            if not entry_type:
                continue
            if str(entry_type).startswith("locator.") or entry_type == "template_match":
                return entry
        return None

    def _on_click(self, row):
        # otvoríme full (ak je), inak thumb – v externom prehliadači (inside kontajnera to býva ťažké),
        # tak aspoň nastavíme status text
        full = row.get("full") or row.get("thumb")
        self.mw.lbl_status.setText(f"Open: {full}")

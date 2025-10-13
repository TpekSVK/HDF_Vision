# app/ui/results_strip.py
import json
import math
import os
from numbers import Real
from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QScrollArea, QPushButton
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt
import imageio.v3 as iio

from app.services.tool_registry import ToolRegistry
from app.utils import overlay as overlay_utils

class ThumbLabel(QLabel):
    def __init__(
        self,
        path: str,
        ok: bool,
        info: str = "",
        status: str | None = None,
        tool_entries: list[dict[str, Any]] | None = None,
    ):
        super().__init__()
        self.path = path
        self.ok = ok
        self.info = info
        self.status = status
        self.tool_entries = tool_entries or []
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
            tool_entries = self._load_tool_entries(r)
            info = self._format_tooltip(tool_entries)
            status = self._aggregate_status(tool_entries)
            if not info:
                info = self._format_fallback_info(r)
            t = ThumbLabel(
                r["thumb"],
                r["ok"],
                info,
                status,
                tool_entries=tool_entries,
            )
            t.mousePressEvent = lambda e, rr=r: self._on_click(rr)
            self.h.addWidget(t)
        self.h.addStretch(1)

    def _format_fallback_info(self, row: dict[str, Any]) -> str:
        parts: list[str] = []
        ssim = row.get("ssim")
        if ssim is not None:
            parts.append(f"ssim={self._format_metric_value(ssim)}")
        blob_count = row.get("blob_count")
        if blob_count is not None:
            parts.append(f"blob_count={blob_count}")
        total_area = row.get("total_area")
        if total_area is not None:
            parts.append(f"area={total_area}")
        return "  ".join(parts) if parts else "—"

    def _load_tool_entries(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        meta = self._load_meta_payload(row)
        if not isinstance(meta, dict):
            return []

        entries: list[dict[str, Any]] = []
        for candidate in ("per_tool", "tool_results", "tools"):
            raw = meta.get(candidate)
            if isinstance(raw, list):
                entries.extend([e for e in raw if isinstance(e, dict)])

        result_entries: list[dict[str, Any]] = []
        for entry in entries:
            normalized = self._normalize_tool_entry(entry, row)
            if normalized is not None:
                result_entries.append(normalized)
        return result_entries

    def _load_meta_payload(self, row: dict[str, Any]) -> dict[str, Any] | None:
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
                return json.load(fh)
        except Exception:
            return None
        return None

    def _normalize_tool_entry(
        self, entry: dict[str, Any], row: dict[str, Any]
    ) -> dict[str, Any] | None:
        tool_type = str(entry.get("type") or entry.get("tool_type") or "").strip()
        definition = ToolRegistry.get_tool_definition(tool_type) if tool_type else None

        name = entry.get("name") or entry.get("tool_id")
        if not name and definition is not None:
            name = definition.name
        if not name:
            name = tool_type or "Tool"

        status_value = entry.get("status")
        if status_value is None and "ok" in entry:
            status_value = "ok" if entry.get("ok") else "nok"
        status = str(status_value).lower() if isinstance(status_value, str) else status_value
        if isinstance(status, bool):
            status = "ok" if status else "nok"

        metrics: dict[str, Any] = {}
        raw_metrics = entry.get("metrics")
        if isinstance(raw_metrics, dict):
            metrics.update(raw_metrics)

        diagnostics = entry.get("diagnostics")
        if isinstance(diagnostics, dict):
            for key in ("corr", "dx", "dy", "blob_count", "total_area", "ssim"):
                if key in diagnostics and key not in metrics:
                    metrics[key] = diagnostics[key]

        for key in ("ssim", "blob_count", "total_area", "corr", "dx", "dy"):
            if key in entry and key not in metrics:
                metrics[key] = entry[key]

        if not metrics:
            for fallback_key in ("ssim", "blob_count", "total_area"):
                if fallback_key in row and row[fallback_key] is not None:
                    metrics[fallback_key] = row[fallback_key]

        metrics_lines = self._format_metrics(definition, metrics)

        overlay_sources: list[Any] = []
        overlay_value = entry.get("overlay_items")
        if overlay_value is not None and not isinstance(overlay_value, (str, bytes)):
            overlay_sources.append(overlay_value)
        display_value = entry.get("display_items")
        if display_value is not None and not isinstance(display_value, (str, bytes)):
            overlay_sources.append(display_value)
        overlay_items: list[overlay_utils.OverlayItem] = []
        if overlay_sources:
            overlay_items = overlay_utils.parse_display_items(
                overlay_sources,
                default_color=(0, 255, 0),
                default_label=str(name),
            )

        return {
            "tool_type": tool_type,
            "tool_id": str(entry.get("tool_id") or name),
            "name": str(name),
            "status": status,
            "metrics": metrics,
            "metrics_lines": metrics_lines,
            "overlay_items": overlay_items,
        }

    def _format_metrics(
        self,
        definition,
        metrics: Dict[str, Any],
    ) -> List[str]:
        ordered: list[str] = []
        values = dict(metrics or {})
        if definition is not None:
            spec = getattr(definition, "metrics_spec", ()) or ()
            sorted_spec = sorted(
                spec,
                key=lambda s: (
                    -int(getattr(s, "priority", 0) or 0),
                    str(getattr(s, "key", "")),
                ),
            )
            for entry in sorted_spec:
                key = getattr(entry, "key", "")
                if not key or key not in values:
                    continue
                ordered.append(f"{key}={self._format_metric_value(values.pop(key))}")

        for key in sorted(values.keys()):
            ordered.append(f"{key}={self._format_metric_value(values[key])}")

        return ordered

    @staticmethod
    def _format_metric_value(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, Real) and not isinstance(value, bool):
            val = float(value)
            if math.isfinite(val):
                if abs(val) >= 1000 or 0 < abs(val) < 0.001:
                    return f"{val:.3g}"
                text = f"{val:.4f}".rstrip("0").rstrip(".")
                return text or "0"
            return str(val)
        return str(value)

    def _format_tooltip(self, tool_entries: list[dict[str, Any]]) -> str:
        if not tool_entries:
            return ""
        lines: list[str] = []
        for entry in tool_entries:
            header = entry.get("name", "Tool")
            status = entry.get("status")
            if status:
                header = f"{header} [{status}]"
            lines.append(header)
            for metric_line in entry.get("metrics_lines", []):
                lines.append(f"  {metric_line}")
        return "\n".join(lines)

    @staticmethod
    def _aggregate_status(tool_entries: list[dict[str, Any]]) -> str | None:
        if not tool_entries:
            return None
        priority = {"nok": 2, "warn": 1, "ok": 0}
        current = None
        current_priority = -1
        for entry in tool_entries:
            status = str(entry.get("status") or "").lower()
            if status not in priority:
                continue
            value = priority[status]
            if value > current_priority:
                current_priority = value
                current = status
        if current is None and any(entry.get("status") for entry in tool_entries):
            return str(tool_entries[0].get("status"))
        return current

    def _on_click(self, row):
        # otvoríme full (ak je), inak thumb – v externom prehliadači (inside kontajnera to býva ťažké),
        # tak aspoň nastavíme status text
        full = row.get("full") or row.get("thumb")
        self.mw.lbl_status.setText(f"Open: {full}")

# app/ui/results_strip.py
import json
import logging
import math
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from numbers import Integral, Real
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from PySide6.QtGui import QDesktopServices, QPixmap, QImageReader
from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QPushButton,
    QComboBox,
)

from app.services.tool_registry import ToolRegistry
from app.utils import overlay as overlay_utils

logger = logging.getLogger(__name__)


class ThumbLabel(QLabel):
    def __init__(
        self,
        path: str,
        ok: bool,
        info: str = "",
        status: str | None = None,
        tool_entries: list[dict[str, Any]] | None = None,
        *,
        loader,
    ):
        super().__init__()
        self.path = path
        self.ok = ok
        self.info = info
        self.status = status
        self.tool_entries = tool_entries or []
        self._loader = loader
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
        if not self.path:
            self.setText("—")
            self.setPixmap(QPixmap())
            return
        pixmap = None
        try:
            pixmap = self._loader(self.path, QSize(self.width() - 6, self.height() - 6))
        except Exception as exc:
            logger.debug("Thumbnail load failed for %s: %s", self.path, exc)
        if pixmap is None:
            self.setText("—")
            self.setPixmap(QPixmap())
            return
        self.setPixmap(pixmap)


class ResultsStrip(QWidget):
    """
    Horizontálny strip posledných N thumbov za dnešok pre aktuálny recept.
    Očakáva .db (DbService) a .current_recipe_name()
    """

    def __init__(self, mw, limit=12):
        super().__init__(mw)
        self.mw = mw
        self.limit = int(limit)
        self._thumb_cache: dict[str, tuple[float, QSize, QPixmap]] = {}
        self._last_folder_to_open: Optional[Path] = None

        self.area = QScrollArea(self)
        self.area.setWidgetResizable(True)
        self.wrap = QWidget()
        self.h = QHBoxLayout(self.wrap)
        self.h.setContentsMargins(4, 4, 4, 4)
        self.h.setSpacing(6)
        self.area.setWidget(self.wrap)

        self.variant_selector = QComboBox(self)
        self.variant_selector.addItem("Auto (Prekrytie → Zarovnaný → Pôvodný)", "auto")
        self.variant_selector.addItem("Prekrytie", "overlay")
        self.variant_selector.addItem("Zarovnaný", "aligned")
        self.variant_selector.addItem("Pôvodný", "raw")
        self.variant_selector.currentIndexChanged.connect(lambda _=0: self.reload())

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addStretch(1)
        controls.addWidget(QLabel("Variant:"))
        controls.addWidget(self.variant_selector)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(controls)
        lay.addWidget(self.area)

    def reload(self):
        self._clear_layout()

        recipe_name = self.mw.current_recipe_name()
        rid = self.mw.db.recipe_id(recipe_name)
        if rid is None:
            self._show_placeholder(self._default_folder(recipe_name))
            return

        selection = getattr(self.mw.cmb_tool, "currentData", lambda: None)()
        tool_key: Optional[str] = None
        if isinstance(selection, Mapping):
            tool_key = (
                selection.get("id") or selection.get("name") or selection.get("tool_id")
            )
        elif selection:
            tool_key = str(selection)

        try:
            records = self.mw.db.recent_image_records(
                rid,
                limit=self.limit * 2,
                tool_key=tool_key,
                view_id=self.mw.active_view_id,
            )
        except Exception as exc:
            logger.debug("Failed to fetch recent image records: %s", exc)
            records = []

        entries: list[dict[str, Any]] = []
        for record in records:
            prepared = self._prepare_entry(record)
            if prepared is not None:
                entries.append(prepared)

        if not entries:
            entries = self._load_filesystem_entries(recipe_name)

        if not entries:
            self._show_placeholder(self._default_folder(recipe_name))
            return

        entries = entries[: self.limit]
        self._last_folder_to_open = None
        for row in entries:
            if not self._last_folder_to_open and row.get("display_path"):
                try:
                    self._last_folder_to_open = Path(row["display_path"]).parent
                except Exception:
                    self._last_folder_to_open = None

            tool_entries = self._load_tool_entries(row)
            tooltip = self._build_tooltip(row, tool_entries)
            status = row.get("status") or self._aggregate_status(tool_entries)
            ok_value = row.get("ok")
            if ok_value is None:
                ok_value = False if str(status).lower() == "nok" else True
            thumb = ThumbLabel(
                row.get("display_path"),
                bool(ok_value),
                tooltip,
                str(status).lower() if isinstance(status, str) else status,
                tool_entries=tool_entries,
                loader=self._thumbnail_loader,
            )
            thumb.mousePressEvent = lambda event, r=row: self._on_click(r, event)
            thumb.mouseDoubleClickEvent = lambda event, r=row: self._on_double_click(
                r, event
            )
            self.h.addWidget(thumb)

        self.h.addStretch(1)

    def _clear_layout(self) -> None:
        while self.h.count():
            item = self.h.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _current_variant(self) -> str:
        data = self.variant_selector.currentData()
        if isinstance(data, str):
            return data
        return "auto"

    def _prepare_entry(self, record: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        data = dict(record)
        display_path, source_key = self._select_display_path(data)
        if not display_path:
            return None

        data.setdefault("display_path", display_path)
        data.setdefault("thumb", data.get("thumb_path") or display_path)
        data.setdefault("full", data.get("full_path"))
        if data.get("status"):
            data["status"] = str(data["status"]).lower()

        meta_json = data.get("meta_json")
        if isinstance(meta_json, str) and not data.get("meta"):
            try:
                parsed = json.loads(meta_json)
                if isinstance(parsed, dict):
                    data["meta"] = parsed
            except Exception:
                pass

        metrics = data.get("metrics")
        if not isinstance(metrics, Mapping):
            data["metrics"] = {}

        data["_display_source"] = source_key
        return data

    def _select_display_path(
        self, record: Mapping[str, Any]
    ) -> tuple[Optional[str], Optional[str]]:
        variant = self._current_variant()
        auto_order = [
            "overlay_path",
            "aligned_path",
            "thumb_path",
            "full_path",
            "raw_path",
        ]
        if variant == "overlay":
            preference = ["overlay_path"] + [
                key for key in auto_order if key != "overlay_path"
            ]
        elif variant == "aligned":
            preference = ["aligned_path"] + [
                key for key in auto_order if key != "aligned_path"
            ]
        elif variant == "raw":
            preference = ["raw_path", "full_path"] + [
                key for key in auto_order if key not in {"raw_path", "full_path"}
            ]
        else:
            preference = auto_order

        for key in preference:
            path = record.get(key)
            if self._is_valid_image(path):
                return path, key
        return None, None

    def _is_valid_image(self, path: Optional[str]) -> bool:
        if not path:
            return False
        try:
            if not os.path.exists(path):
                logger.debug("Image path not found: %s", path)
                return False
            if os.path.getsize(path) <= 0:
                logger.debug("Image path empty: %s", path)
                return False
        except OSError as exc:
            logger.debug("Image check failed for %s: %s", path, exc)
            return False
        return True

    def _default_folder(self, recipe_name: str) -> Path:
        base = Path("/data/runs")
        try:
            recipe_name = recipe_name or "default"
        except Exception:
            recipe_name = "default"
        day_dir = base / datetime.now().strftime("%Y%m%d")
        if day_dir.exists():
            if (day_dir / recipe_name).exists():
                return day_dir / recipe_name
            prefixed = sorted(
                [
                    child
                    for child in day_dir.iterdir()
                    if child.is_dir() and child.name.startswith(f"{recipe_name}_")
                ],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if prefixed:
                return prefixed[0]
            return day_dir
        if base.exists():
            return base
        return Path("/data")

    def _show_placeholder(self, folder: Path) -> None:
        container = QWidget(self.wrap)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        label = QLabel("Nenašli sa žiadne obrázky", container)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        button = QPushButton("Otvoriť priečinok", container)
        button.setFixedWidth(140)
        button.clicked.connect(self._open_folder)
        layout.addWidget(button, alignment=Qt.AlignCenter)

        self._last_folder_to_open = folder
        self.h.addWidget(container)
        self.h.addStretch(1)

    def _open_folder(self) -> None:
        candidate = self._last_folder_to_open or Path("/data/runs")
        folder = self._determine_folder_to_open(candidate)
        if folder is None:
            fallback = self._existing_folder(Path("/data/runs")) or Path("/data")
            folder = fallback
        if not self._open_with_desktop(folder):
            logger.debug("Desktop open failed for folder %s", folder)

    def _open_with_desktop(self, path: Path, *, prefer_image_viewer: bool | None = None) -> bool:
        prefers_viewer = prefer_image_viewer
        if prefers_viewer is None:
            prefers_viewer = path.is_file()

        if prefers_viewer and self._launch_image_viewer(path):
            return True

        try:
            if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                return True
        except Exception as exc:
            logger.debug("QDesktopServices failed for %s: %s", path, exc)

        if prefers_viewer and self._launch_image_viewer(path):
            return True

        if sys.platform.startswith("linux"):
            for command in (("xdg-open", str(path)), ("gio", "open", str(path))):
                try:
                    subprocess.Popen(command)
                    return True
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    logger.debug(
                        "Failed to launch %s for %s: %s", command[0], path, exc
                    )
        elif sys.platform.startswith("win"):
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                return True
            except OSError as exc:
                logger.debug("os.startfile failed for %s: %s", path, exc)
        elif sys.platform == "darwin":
            try:
                subprocess.Popen(["open", str(path)])
                return True
            except Exception as exc:
                logger.debug("open command failed for %s: %s", path, exc)
        return False

    def _launch_image_viewer(self, path: Path) -> bool:
        commands: list[list[str]] = []
        for env_var in ("HDF_IMAGE_VIEWER", "IMAGE_VIEWER"):
            configured = os.environ.get(env_var)
            if configured:
                try:
                    commands.append(shlex.split(configured) + [str(path)])
                except ValueError as exc:
                    logger.debug(
                        "Ignoring invalid %s command %r: %s", env_var, configured, exc
                    )
        commands.append(["imageviewer", str(path)])

        for command in commands:
            executable = command[0]
            if shutil.which(executable) is None:
                continue
            try:
                subprocess.Popen(command)
                return True
            except Exception as exc:
                logger.debug("Image viewer %s failed for %s: %s", executable, path, exc)
        return False

    def _thumbnail_loader(self, path: str, target_size: QSize) -> Optional[QPixmap]:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cache_entry = self._thumb_cache.get(path)
        if cache_entry and cache_entry[0] == mtime and cache_entry[1] == target_size:
            return cache_entry[2]
        reader = QImageReader(path)
        if target_size.width() > 0 and target_size.height() > 0:
            try:
                original_size = reader.size()
            except Exception:
                original_size = QSize()
            if original_size.isValid():
                scaled_size = original_size.scaled(target_size, Qt.KeepAspectRatio)
                reader.setScaledSize(scaled_size)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        if target_size.width() > 0 and target_size.height() > 0:
            pixmap = pixmap.scaled(
                target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        self._thumb_cache[path] = (mtime, target_size, pixmap)
        return pixmap

    def _load_filesystem_entries(self, recipe_name: str) -> list[dict[str, Any]]:
        base = Path("/data/runs")
        if not base.exists():
            return []

        candidates: list[tuple[float, Path]] = []
        try:
            day_dirs = sorted(
                base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
            )
        except Exception:
            day_dirs = []

        for day_dir in day_dirs:
            if not day_dir.is_dir():
                continue
            candidate_roots: list[Path] = []
            recipe_dir = day_dir / recipe_name
            if recipe_dir.exists():
                candidate_roots.append(recipe_dir)
            try:
                for child in day_dir.iterdir():
                    if not child.is_dir():
                        continue
                    if child.name.startswith(f"{recipe_name}_"):
                        candidate_roots.append(child)
            except Exception:
                pass
            if not candidate_roots:
                candidate_roots.append(day_dir)

            for root in candidate_roots:
                for subdir in ("overlay", "aligned", "thumbs", "full"):
                    directory = root / subdir
                    if not directory.exists():
                        continue
                    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
                        for path in directory.rglob(ext):
                            try:
                                candidates.append((path.stat().st_mtime, path))
                            except OSError:
                                continue
                for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
                    for path in root.glob(ext):
                        try:
                            candidates.append((path.stat().st_mtime, path))
                        except OSError:
                            continue
            if len(candidates) >= self.limit * 3:
                break

        entries: list[dict[str, Any]] = []
        for _, path in sorted(candidates, key=lambda item: item[0], reverse=True):
            if not self._is_valid_image(str(path)):
                continue
            entry = {
                "display_path": str(path),
                "thumb": str(path),
                "full": str(path),
                "ok": True,
                "status": None,
                "metrics": {},
            }
            entries.append(entry)
            if len(entries) >= self.limit:
                break
        return entries

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

    def _load_tool_entries(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        meta = row.get("meta")
        if not meta:
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

    def _load_meta_payload(self, row: Mapping[str, Any]) -> dict[str, Any] | None:
        meta_json = row.get("meta_json")
        if isinstance(meta_json, str):
            try:
                parsed = json.loads(meta_json)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        meta_path = row.get("meta_path")
        thumb_path = row.get("thumb") or row.get("thumb_path")
        candidate_paths: list[Path] = []
        if isinstance(meta_path, str):
            candidate_paths.append(Path(meta_path))
        if isinstance(thumb_path, str):
            try:
                thumb = Path(thumb_path)
                candidate_paths.append(thumb.with_suffix(".json"))
                if thumb.parent.name == "thumbs":
                    candidate_paths.append(
                        thumb.parent.parent / "meta" / thumb.with_suffix(".json").name
                    )
            except Exception:
                pass

        for path in candidate_paths:
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                continue
        return None

    def _normalize_tool_entry(
        self, entry: dict[str, Any], row: Mapping[str, Any]
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
        status = (
            str(status_value).lower() if isinstance(status_value, str) else status_value
        )
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
        overlay_items: list[overlay_utils.PrekrytieItem] = []
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
        if isinstance(value, Integral):
            return str(int(value))
        if isinstance(value, Real):
            val = float(value)
            if not math.isfinite(val):
                return str(val)
            if abs(val - round(val)) < 1e-6:
                return str(int(round(val)))
            if abs(val) >= 1e6 or 0 < abs(val) < 0.001:
                return f"{val:.3g}"
            text = f"{val:.4f}".rstrip("0").rstrip(".")
            return text or "0"
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

    def _build_tooltip(
        self, row: Mapping[str, Any], tool_entries: list[dict[str, Any]]
    ) -> str:
        lines: list[str] = []
        ts_ms = row.get("ts_ms")
        if ts_ms:
            try:
                dt = datetime.fromtimestamp(int(ts_ms) / 1000)
                lines.append(dt.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                lines.append(str(ts_ms))
        status = row.get("status")
        if status:
            lines.append(f"Status: {status}")
        tool_key = row.get("tool_key")
        if tool_key:
            lines.append(f"Tool: {tool_key}")

        metrics = row.get("metrics")
        if isinstance(metrics, Mapping):
            metric_lines = []
            for key, value in metrics.items():
                metric_lines.append(f"{key}={self._format_metric_value(value)}")
            if metric_lines:
                lines.append(" | ".join(metric_lines))

        details = self._format_tooltip(tool_entries)
        if details:
            if lines:
                lines.append("")
            lines.append(details)
        if not lines:
            return self._format_fallback_info(dict(row))
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

    def _on_click(self, row, event=None):
        # otvoríme full (ak je), inak thumb – v externom prehliadači (inside kontajnera to býva ťažké),
        # tak aspoň nastavíme status text
        full = row.get("full") or row.get("display_path") or row.get("thumb")
        if event is not None:
            event.accept()
        self.mw.lbl_status.setText(f"Open: {full}")

    def _on_double_click(self, row, event=None):
        path = row.get("full") or row.get("display_path") or row.get("thumb")
        if event is not None:
            event.accept()

        target_path = self._coerce_to_path(path)
        if target_path and target_path.is_file():
            if self._open_with_desktop(target_path, prefer_image_viewer=True):
                self._last_folder_to_open = target_path.parent
                self.mw.lbl_status.setText(f"Otvoriť obrázok: {target_path}")
                return

        folder: Optional[Path] = None
        if target_path:
            folder = self._determine_folder_to_open(target_path)
        if folder and self._open_with_desktop(folder, prefer_image_viewer=False):
            self._last_folder_to_open = folder
            self.mw.lbl_status.setText(f"Otvoriť priečinok: {folder}")
            return

        logger.debug(
            "Unable to open image or folder for thumbnail double click: %s", path
        )
        self._open_folder()

    def _determine_folder_to_open(self, path_value: Any) -> Optional[Path]:
        path = self._coerce_to_path(path_value)
        if path is None:
            return None
        folder = path if path.is_dir() else path.parent
        if not folder:
            return None
        folder = self._existing_folder(folder)
        if folder is None:
            return None
        try:
            return folder.resolve(strict=False)
        except Exception:
            return folder

    def _coerce_to_path(self, value: Any) -> Optional[Path]:
        if value is None:
            return None
        if isinstance(value, Path):
            candidate = value
        else:
            text = str(value)
            if not text:
                return None
            url = QUrl(text)
            candidate = None
            if url.isValid() and url.isLocalFile():
                local_file = url.toLocalFile()
                if local_file:
                    candidate = Path(local_file)
            if candidate is None and text.startswith("file://"):
                candidate = Path(text[7:])
            if candidate is None:
                try:
                    candidate = Path(text)
                except Exception as exc:
                    logger.debug("Unable to interpret path %s: %s", text, exc)
                    return None
        try:
            candidate = candidate.expanduser()
        except Exception:
            pass
        try:
            return candidate.resolve(strict=False)
        except Exception:
            return candidate

    def _existing_folder(self, folder: Path) -> Optional[Path]:
        current = folder
        while current and not current.exists():
            parent = current.parent
            if not parent or parent == current:
                return None
            current = parent
        return current if current and current.exists() else None

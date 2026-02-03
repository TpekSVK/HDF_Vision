"""Services for JSONL logging of pipeline executions and artifact export."""

from __future__ import annotations

import atexit
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import imageio.v3 as iio
import numpy as np

from app.models.schema import RecipeV2, Tool
from app.services.settings_service import DEFAULT_LOG_DIR, get_session_settings
from app.utils import overlay as overlay_utils

if TYPE_CHECKING:  # pragma: no cover - typing helpers
    from app.services.tool_service import PipelineResult, PipelineToolReport


_DEFAULT_LOG_PATH = DEFAULT_LOG_DIR / "pipeline_runs.jsonl"
_DEFAULT_ARTIFACT_DIR = DEFAULT_LOG_DIR / "artifacts"


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if np.isnan(value) or np.isinf(value):
            return None
        return float(value)
    if hasattr(value, "item"):
        try:
            scalar = value.item()
            if isinstance(scalar, (str, int, float, bool)):
                return _json_safe(scalar)
        except Exception:
            pass
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, Sequence)):
        return [_json_safe(v) for v in value]
    return str(value)


class JsonlRunLogger:
    """Append-only JSONL logger with simple size-based rotation."""

    def __init__(
        self,
        path: Path = _DEFAULT_LOG_PATH,
        *,
        flush_every: int = 1,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.path = path
        self.flush_every = max(1, int(flush_every))
        self.max_bytes = max_bytes
        self._buffer: List[str] = []
        self._lock = threading.Lock()

    def log(self, entry: Mapping[str, Any]) -> None:
        line = json.dumps(_json_safe(dict(entry)), ensure_ascii=False)
        with self._lock:
            self._buffer.append(line)
            if len(self._buffer) >= self.flush_every:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        _ensure_parent(self.path)
        with open(self.path, "a", encoding="utf-8") as f:
            for line in self._buffer:
                f.write(line + "\n")
        self._buffer.clear()
        self._rotate_if_needed()

    def _rotate_if_needed(self) -> None:
        if self.max_bytes <= 0:
            return
        if not self.path.exists():
            return
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size <= self.max_bytes:
            return
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        rotated = self.path.with_name(f"{self.path.stem}.{timestamp}{self.path.suffix}")
        try:
            self.path.rename(rotated)
        except OSError:
            pass


_RUN_LOGGER = JsonlRunLogger(flush_every=1)

atexit.register(_RUN_LOGGER.flush)


def _is_locator(tool: Tool) -> bool:
    return tool.type.startswith("locator.") or tool.type == "template_match"


def _to_u8(arr: Optional[np.ndarray]) -> Optional[np.ndarray]:
    if arr is None:
        return None
    data = np.asarray(arr)
    if data.dtype == np.uint16:
        if data.size == 0:
            return np.zeros_like(data, dtype=np.uint8)
        maxi = int(data.max())
        if maxi <= 0:
            return np.zeros_like(data, dtype=np.uint8)
        if maxi <= 4095:
            return (data >> 4).astype(np.uint8)
        return (data.astype(np.float32) * (255.0 / 65535.0)).astype(np.uint8)
    if data.ndim == 3 and data.shape[2] == 3:
        data = data[:, :, 0]
    return data.astype(np.uint8, copy=False)


def _sanitize_recipe_name(name: Optional[str]) -> str:
    if not name:
        return "unknown"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return safe or "unknown"


def _export_artifacts(
    *,
    recipe_name: Optional[str],
    result: "PipelineResult",
    recipe: RecipeV2,
    artifact_root: Path,
    export_overlay: bool,
) -> Dict[str, str]:
    context = result.context
    frame = getattr(context, "frame_aligned", None)
    if frame is None:
        frame = getattr(context, "frame", None)
    frame_u8 = _to_u8(frame)
    if frame_u8 is None:
        return {}

    safe_name = _sanitize_recipe_name(recipe_name)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_id = uuid.uuid4().hex[:8]
    base_dir = artifact_root / datetime.utcnow().strftime("%Y%m%d")
    base_dir.mkdir(parents=True, exist_ok=True)

    aligned_path = base_dir / f"{safe_name}_{ts}_{run_id}_aligned.png"
    iio.imwrite(aligned_path, frame_u8)

    artifacts: Dict[str, str] = {"aligned_png": str(aligned_path)}

    overlay_items = getattr(result, "overlay_items", None)
    overlay = None
    if export_overlay and overlay_items:
        overlay = overlay_utils.render_overlay(frame_u8.shape[:2], overlay_items)
    if overlay is not None:
        overlay_path = base_dir / f"{safe_name}_{ts}_{run_id}_overlay.png"
        iio.imwrite(overlay_path, overlay)
        artifacts["overlay_png"] = str(overlay_path)

    return artifacts


def _collect_locator_payload(
    result: "PipelineResult",
    locator_reports: Iterable["PipelineToolReport"],
) -> Dict[str, Any]:
    locator_info: Dict[str, Any] = {
        "corr": None,
        "dx": None,
        "dy": None,
        "T_total": None,
        "found": None,
    }

    first_locator = None
    for report in locator_reports:
        first_locator = report
        break

    if first_locator is not None:
        metrics = first_locator.metrics or {}
        locator_info["corr"] = metrics.get("corr")
        locator_info["dx"] = metrics.get("dx")
        locator_info["dy"] = metrics.get("dy")
        locator_info["found"] = metrics.get("found")

    context = result.context
    T_total = getattr(context, "T_total", None)
    if T_total is not None:
        locator_info["T_total"] = _json_safe(np.asarray(T_total).tolist())

    return locator_info


def record_pipeline_run(
    *,
    recipe: RecipeV2,
    result: "PipelineResult",
    recipe_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    settings = get_session_settings()
    if not settings.logging_enabled or not bool(getattr(recipe, "logging_enabled", True)):
        return

    logging_path = getattr(settings, "logging_path", DEFAULT_LOG_DIR)
    if not isinstance(logging_path, Path):
        logging_path = Path(str(logging_path))
    base_dir = logging_path
    log_file = logging_path
    if not log_file.suffix:
        base_dir = logging_path
        log_file = logging_path / "pipeline_runs.jsonl"
    else:
        base_dir = logging_path.parent or DEFAULT_LOG_DIR

    artifact_root = base_dir / "artifacts"

    if _RUN_LOGGER.path != log_file:
        _RUN_LOGGER.flush()
        _RUN_LOGGER.path = log_file

    timestamp = _utc_now_iso()
    run_entry: Dict[str, Any] = {
        "timestamp": timestamp,
        "recipe_id": recipe_name or "unknown",
        "recipe_name": recipe_name or "unknown",
        "ok": bool(result.status == "ok"),
        "status": result.status,
        "cycle_time_ms": float(result.cycle_time_ms),
        "policy_applied": getattr(result, "policy_applied", None),
    }

    locator_reports = [report for report in result.per_tool if _is_locator(report.tool)]
    run_entry["locator"] = _collect_locator_payload(result, locator_reports)

    tools_payload: List[Dict[str, Any]] = []
    for report in result.per_tool:
        tool_payload = {
            "name": report.tool.name or report.tool_id,
            "type": report.tool.type,
            "ok": bool(report.status == "ok"),
            "status": report.status,
            "latency_ms": float(report.latency_ms),
            "metrics": _json_safe(report.metrics),
        }
        tools_payload.append(tool_payload)
    run_entry["tools"] = tools_payload

    if notes:
        run_entry["notes"] = str(notes)

    artifacts: Dict[str, str] = {}
    if settings.export_artifacts and bool(getattr(recipe, "export_artifacts", False)):
        try:
            artifacts = _export_artifacts(
                recipe_name=recipe_name,
                result=result,
                recipe=recipe,
                artifact_root=artifact_root,
                export_overlay=settings.export_overlay,
            )
        except Exception as exc:
            run_entry.setdefault("notes", "")
            existing = run_entry["notes"].strip()
            extra = f"artifact export failed: {exc}"
            run_entry["notes"] = (existing + "; " + extra).strip("; ")
    if artifacts:
        run_entry["artifacts"] = artifacts

    _RUN_LOGGER.log(run_entry)

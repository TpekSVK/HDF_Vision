"""Production execution and sequencing without widgets or Qt event processing.

Owned by one inspection worker. Database connections are created on that worker.
The UI receives completed data only; it never lends a SQLite connection or widget.
"""
from __future__ import annotations
from app.services.view_capture import ViewCapture

import json
import logging
import math
import time
import uuid
from pathlib import Path
from collections.abc import Mapping, Sequence
from numbers import Integral, Real
from typing import Any
import numpy as np
from app.models.schema import RecipeV2
from app.services.db_service import DbService
from app.services.storage_service import load_recipe_config, save_production_result
from app.services.tool_pipeline import run_pipeline
from app.services.frame_coordinates import InspectionFrame, reused_view_frame
from app.services.camera_profiles import apply_view_camera_profile, snapshot_camera_state
from app.services.view_images import apply_view_image_transform, apply_view_rotation, view_image_rotation
from app.services.view_aggregation import aggregate_branching_statuses
from app.utils.trigger_timing import get_default_trigger_gap_ms
from app.utils import overlay as overlay_utils


class InspectionRuntime:
    def __init__(self, camera, pico, pico_config, modbus, db_path, *, data_root=Path('/data'), camera_id='camera_1', pico_id='pico_1', session_settings=None):
        self.cam, self.pico, self.pico_config, self.modbus = camera, pico, pico_config, modbus
        from app.services.settings_service import SessionSettingsStore
        self.session_settings = session_settings or SessionSettingsStore(Path(data_root) / "logs")
        self.camera_id, self.pico_id = camera_id, pico_id
        self.db_path, self.data_root = Path(db_path), Path(data_root)
        self._logger = logging.getLogger(__name__)
        self._golden_cache = {}
        self._active_view_id = None
        self.mode = 'RUN'
        self.live_enabled = False
        self.reset()

    def reset(self):
        self._manual_trigger_positions = {}
        self._manual_trigger_statuses = {}
        self._external_sequence_index = {'pico': 0, 'modbus': 0}
        self._external_sequence_statuses = {}
        self._sequence_contexts = {}
        self._view_states = {}

    def prepare(self, recipe_name, capture_mode, active_view_id):
        config = load_recipe_config(recipe_name, base_dir=self.data_root)
        self.reset()
        if not self.pico.is_available() and not self.pico.connect():
            raise RuntimeError('Pico nie je dostupné.')
        if capture_mode is None:
            capture_mode = 'trigger' if int(self.cam.get_stream_mode()) == 1 else 'master'
        self.recipe_name, self.capture_mode = recipe_name, capture_mode
        self._active_view_id = active_view_id
        view = config.get_view(active_view_id) or config.views[0]
        apply_view_camera_profile(self.cam, {}, view.camera_profile)
        if capture_mode == 'trigger':
            self.pico.prepare_trigger(self.cam)
        else:
            self.pico.prepare_master(self.cam)
            if not self.cam.is_pipeline_open():
                self.cam.start(caller='inspection_prepare_master')

    def quiesce(self):
        self.pico.quiesce()
        if getattr(self, 'capture_mode', 'master') == 'trigger':
            self.cam.exit_trigger_session(restore_master=False)
        self.reset()

    def shutdown(self):
        self.quiesce()
        self.cam.stop(caller='inspection_close')
        self.modbus.close()
        self.pico.close()

    def run(self, controller, request, options):
        if request.camera_id != self.camera_id or not controller.owns(request):
            raise ValueError("Požiadavka patrí inej kamerovej stanici.")
        if getattr(self, 'recipe_name', None) != options['recipe_name']:
            self.reset()
        self.recipe_name = options['recipe_name']
        self.capture_mode = options['capture_mode']
        self._active_view_id = options.get('active_view_id')
        self.capture_filtered_roi = options.get('capture_filtered_roi', False)
        self.records = []
        state = None
        self.db = None
        try:
            if options.get('software_button') and self.capture_mode == 'master':
                config = load_recipe_config(self.recipe_name, base_dir=self.data_root)
                selected = next((view for view in config.views if view.id == self._active_view_id
                    and view.trigger_mode == 'external' and view.external_source == 'pico'
                    and view.external_trigger_mode == 'explicit'), None)
                if selected is not None:
                    options = dict(options, spec={'view_id': selected.id})
            if self.capture_mode == 'trigger':
                self.cam.begin_trigger_capture()
            state = self._prepare_run_trigger(trigger_source=request.source,
                trigger_input_index=options.get('input_index'), requested_spec=options.get('spec'))
            if state is None:
                raise ValueError('Požiadavka nemá platný cieľový pohľad.')
            state['frame_request'] = options.get('frame_request')
            if state['logging_enabled']:
                self.db = DbService(self.db_path)
            controller.run_sequence(request, state, self._execute_view_trigger, self._finalize_run_trigger)
            return state
        except Exception:
            self.reset()
            try:
                self.modbus.signal_result('nok')
            finally:
                self.pico.quiesce()
            raise
        finally:
            if self.capture_mode == 'trigger':
                self.cam.end_trigger_capture()
            if self.db is not None:
                self.db.close()
                self.db = None

    def current_recipe_name(self):
        return self.recipe_name

    def get_capture_mode(self):
        return self.capture_mode

    def _reset_view_sequence_state(self):
        self._view_states.clear()

    def _clone_frame(self, frame):
        return frame.copy() if frame is not None else None

    def _enter_run_trigger_session(self, *, trigger_gap_ms=None):
        self.pico.prepare_trigger(self.cam)

    def _reset_manual_trigger_progress(self, recipe_name: str | None = None) -> None:
        if recipe_name is None:
            self._manual_trigger_positions.clear()
            self._manual_trigger_statuses.clear()
            return
        self._manual_trigger_positions.pop(recipe_name, None)
        self._manual_trigger_statuses.pop(recipe_name, None)


    def _build_runtime_view_spec(self, view: Any, index: int) -> dict[str, Any]:
        settle_ms = getattr(view, "settle_ms", None)
        settle_ms = int(settle_ms) if isinstance(settle_ms, Integral) else None
        if settle_ms is not None and settle_ms < 0:
            settle_ms = 0

        # manual = view sa spracuje iba po kliknutí TRIGGER (bez auto-sleep medzi viewmi)
        # timed = po spracovaní sa čaká trigger_interval_ms
        # external = view čaká na externý trigger (Pico/Modbus), interval sa nepoužíva
        trigger_mode = str(getattr(view, "trigger_mode", "timed") or "timed").strip().lower()
        if trigger_mode not in {"timed", "external", "manual"}:
            trigger_mode = "timed"

        interval_ms = getattr(view, "trigger_interval_ms", None)
        interval_ms = int(interval_ms) if isinstance(interval_ms, Integral) else None
        if interval_ms is not None and interval_ms < 0:
            interval_ms = 0
        if trigger_mode != "timed":
            interval_ms = None

        trigger_gap_ms = getattr(view, "trigger_gap_ms", None)
        trigger_gap_ms = float(trigger_gap_ms) if isinstance(trigger_gap_ms, (int, float)) else None
        if trigger_gap_ms is not None and trigger_gap_ms <= 0:
            trigger_gap_ms = None

        profile = getattr(view, "camera_profile", None)
        width = getattr(profile, "width", None) or getattr(self.cam, "width", None)
        height = getattr(profile, "height", None) or getattr(self.cam, "height", None)
        fps = getattr(profile, "fps", None) or getattr(self.cam, "fps", None)
        if trigger_gap_ms is None:
            trigger_gap_ms = get_default_trigger_gap_ms(width, height, fps)

        frame_source_view_id = str(getattr(view, "frame_source_view_id", "") or "").strip() or None
        external_trigger_mode = str(
            getattr(view, "external_trigger_mode", "sequential") or "sequential"
        ).strip().lower()
        if external_trigger_mode not in {"sequential", "explicit"}:
            external_trigger_mode = "sequential"
        external_request_input_raw = getattr(view, "external_request_input", None)
        external_source = str(getattr(view, "external_source", "modbus") or "modbus").lower()
        external_request_input = (
            int(external_request_input_raw)
            if isinstance(external_request_input_raw, Integral)
            else None
        )
        if external_request_input is not None and not (1 <= external_request_input <= 8):
            external_request_input = None
        return {
            "index": index,
            "view": view,
            "image_rotation": int(getattr(view, "image_rotation", 0) or 0),
            "settle_ms": settle_ms,
            "trigger_mode": trigger_mode,
            "interval_ms": interval_ms,
            "trigger_gap_ms": trigger_gap_ms,
            "frame_source_view_id": frame_source_view_id,
            "external_trigger_mode": external_trigger_mode,
            "external_source": external_source if external_source in {"pico", "modbus"} else "modbus",
            "external_request_input": external_request_input,
            "branch_enabled": bool(getattr(view, "branch_enabled", False)),
            "branch_targets": dict(getattr(view, "branch_targets", {}) or {}),
            "branch_default_view_id": str(getattr(view, "branch_default_view_id", "") or "").strip() or None,
        }


    def _resolve_external_trigger_view(
        self,
        *,
        view_specs: list[dict[str, Any]],
        source: str,
        input_index: int,
    ) -> dict[str, Any] | None:
        """Resolve an external event without falling back to unrelated views."""
        source = str(source or "").strip().lower()
        if self.mode != "RUN" or source not in self._external_sequence_index:
            return None
        if isinstance(input_index, bool) or not isinstance(input_index, Integral):
            return None
        input_index = int(input_index)
        if not 1 <= input_index <= 8:
            return None

        if source == "pico" and not self.pico_config.is_input_enabled(input_index):
            self._logger.info(
                "[RUN] external trigger ignored source=pico input=%s reason=disabled",
                input_index,
            )
            return None

        external_specs = [
            spec for spec in view_specs
            if spec.get("trigger_mode") == "external"
            and spec.get("external_source") == source
        ]
        explicit = [
            spec for spec in external_specs
            if spec.get("external_trigger_mode") == "explicit"
            and spec.get("external_request_input") == input_index
        ]
        if len(explicit) > 1:
            self._logger.error(
                "[RUN] external trigger ignored source=%s input=%s reason=duplicate_explicit_match",
                source,
                input_index,
            )
            return None
        if explicit:
            view = explicit[0]["view"]
            self._logger.info(
                "[RUN] resolved external trigger mode=explicit source=%s input=%s view=%s",
                source,
                input_index,
                getattr(view, "name", None) or getattr(view, "id", None),
            )
            return explicit[0]

        sequential = [
            spec for spec in external_specs
            if spec.get("external_trigger_mode") == "sequential"
        ]
        if sequential:
            position = self._external_sequence_index[source] % len(sequential)
            selected = dict(sequential[position])
            self._external_sequence_index[source] = (position + 1) % len(sequential)
            selected["sequence_key"] = source
            selected["sequence_position"] = position
            selected["sequence_length"] = len(sequential)
            view = selected["view"]
            self._logger.info(
                "[RUN] resolved external trigger mode=sequential source=%s index=%s view=%s",
                source,
                position,
                getattr(view, "name", None) or getattr(view, "id", None),
            )
            return selected

        self._logger.info(
            "[RUN] external trigger ignored source=%s input=%s reason=no_matching_view",
            source,
            input_index,
        )
        return None


    def _resolve_manual_sequence_view(
        self,
        *,
        recipe_name: str,
        view_specs: list[dict[str, Any]],
        external_source: str | None = None,
    ) -> dict[str, Any] | None:
        """Let the RUN button simulate the next signal of an external sequence."""
        sequential = [
            spec for spec in view_specs
            if spec.get("trigger_mode") == "external"
            and spec.get("external_trigger_mode") == "sequential"
            and (
                external_source is None
                or spec.get("external_source") == str(external_source).lower()
            )
        ]
        if not sequential:
            return None
        position = self._manual_trigger_positions.get(recipe_name, 0) % len(sequential)
        self._manual_trigger_positions[recipe_name] = (position + 1) % len(sequential)
        selected = dict(sequential[position])
        selected["sequence_key"] = f"manual:{recipe_name}"
        selected["sequence_position"] = position
        selected["sequence_length"] = len(sequential)
        return selected


    def _handle_master_flash_capture_flow(self, *, view, capture_request_source):
        return ViewCapture(self.cam, self.pico, self.pico_config, "master",
                           self._active_view_id).master_frame(
            view=view, capture_request_source=capture_request_source)


    def _capture_frame_for_view(
        self, *, trigger_mode_label: str, master_caller: str,
        view: Any | None = None, view_id: str | None = None,
        base_camera_state: Mapping[str, Any] | None = None,
        settle_ms: int | None = None, transform_stage: str = "inspection",
        image_rotation_override: int | None = None,
        capture_request_source: str = "manual", frame_request=None,
    ):
        capture = ViewCapture(self.cam, self.pico, self.pico_config,
                              self.get_capture_mode(), self._active_view_id)
        return capture.capture(
            trigger_mode_label=trigger_mode_label, master_caller=master_caller,
            view=view, view_id=view_id, base_camera_state=base_camera_state,
            settle_ms=settle_ms, transform_stage=transform_stage,
            image_rotation_override=image_rotation_override,
            capture_request_source=capture_request_source, frame_request=frame_request,
        )


    def _run_trigger_context(self, *, requested_stream_mode: int | None = None) -> dict[str, Any]:
        current_stream_mode: int | None = None
        stream_mode_error: str | None = None
        try:
            current_stream_mode = int(self.cam.get_stream_mode())
        except Exception as exc:
            stream_mode_error = str(exc)

        return {
            "requested_stream_mode": requested_stream_mode,
            "current_stream_mode": current_stream_mode,
            "stream_mode_error": stream_mode_error,
            "pipeline_open": bool(getattr(self.cam, "is_pipeline_open", lambda: False)()),
            "live_active": bool(self.live_enabled),
            "active_view_id": self._active_view_id,
            "video_device": getattr(self.cam, "device", None),
            "hid_device": getattr(self.cam, "get_hid_device", lambda: None)(),
        }


    def _log_run_trigger_context(
        self,
        message: str,
        *,
        requested_stream_mode: int | None = None,
        hid_set: str = "not_applicable",
    ) -> None:
        ctx = self._run_trigger_context(requested_stream_mode=requested_stream_mode)
        ctx["hid_set"] = hid_set
        self._logger.debug(
            "%s | requested=%s current=%s pipeline_open=%s live_active=%s active_view_id=%s video_device=%s hid_device=%s hid_set=%s stream_mode_error=%s",
            message,
            ctx.get("requested_stream_mode"),
            ctx.get("current_stream_mode"),
            ctx.get("pipeline_open"),
            ctx.get("live_active"),
            ctx.get("active_view_id"),
            ctx.get("video_device"),
            ctx.get("hid_device"),
            ctx.get("hid_set"),
            ctx.get("stream_mode_error"),
        )


    def _log_trigger_cycle(
        self,
        event: str,
        *,
        active_view: str | None = None,
        trigger_mode: str | None = None,
        preview_state: str | None = None,
        trigger_primed: bool | None = None,
        frame_received: bool = False,
        note: str | None = None,
    ) -> None:
        stream_mode: int | None = None
        stream_mode_error: str | None = None
        try:
            stream_mode = int(self.cam.get_stream_mode())
        except Exception as exc:
            stream_mode_error = str(exc)
        self._logger.debug(
            "trigger_cycle event=%s active_recipe=%s active_view=%s stream_mode=%s pipeline_open=%s "
            "trigger_mode=%s preview=%s trigger_primed=%s frame_received=%s note=%s stream_mode_error=%s",
            event,
            self.current_recipe_name(),
            active_view or self._active_view_id,
            stream_mode,
            bool(getattr(self.cam, "is_pipeline_open", lambda: False)()),
            trigger_mode,
            preview_state,
            trigger_primed,
            frame_received,
            note,
            stream_mode_error,
        )


    def _simplify_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return float(value) if math.isfinite(value) else None
        if hasattr(value, "item"):
            try:
                return self._simplify_value(value.item())
            except Exception:
                return None
        if isinstance(value, Mapping):
            return {str(k): self._simplify_value(v) for k, v in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self._simplify_value(v) for v in value]
        try:
            return float(value)
        except Exception:
            return str(value)


    def _serialize_tool_report(self, report) -> dict[str, Any]:
        metrics = {}
        raw_metrics = getattr(report, "metrics", None)
        if isinstance(raw_metrics, Mapping):
            metrics = {str(k): self._simplify_value(v) for k, v in raw_metrics.items()}
        diagnostics = {}
        raw_diag = getattr(report, "diagnostics", None)
        if isinstance(raw_diag, Mapping):
            diagnostics = {str(k): self._simplify_value(v) for k, v in raw_diag.items()}

        latency_value = self._simplify_value(getattr(report, "latency_ms", None))
        if latency_value is not None:
            metrics.setdefault("latency_ms", latency_value)

        tool = getattr(report, "tool", None)
        tool_name = getattr(tool, "name", None) if tool is not None else None
        tool_type = getattr(tool, "type", None) if tool is not None else None
        tool_order = getattr(tool, "order", None) if tool is not None else None
        tool_id = getattr(report, "tool_id", None) or tool_name or (f"tool_{tool_order}" if tool_order is not None else None)

        # Persist geometry and thresholds from the executed tool, never rebuild
        # historical overlays from a subsequently edited recipe.
        history_overlays = []
        if tool is not None:
            geometry = overlay_utils.tool_overlay_items(
                tool, color=(255, 170, 73), include_ignore_mask=False,
            )
            for item in [item for item in geometry if item.z_index == 20] + list(getattr(report, "overlay_items", []) or []):
                if item.kind not in {"rect", "polygon", "polyline"}:
                    continue
                history_overlays.append({
                    "rect": self._simplify_value(item.rect),
                    "points": item.points.tolist() if item.points is not None else None,
                    "closed": item.closed,
                    "error": item.z_index >= 30 and str(getattr(report, "status", "")).lower() == "nok",
                })
            if str(getattr(report, "status", "")).lower() == "nok":
                for blob in (raw_metrics or {}).get("blobs", []):
                    if isinstance(blob, Mapping) and "image_x" in blob and "image_y" in blob:
                        history_overlays.append({
                            "rect": self._simplify_value([blob["image_x"], blob["image_y"], blob.get("width", 0), blob.get("height", 0)]),
                            "error": True,
                        })

        if tool is not None and tool.type == "mold.protection_v2":
            from app.services.empty_mold_v2.overlays import items as v2_overlay_items
            transform = metrics.get("alignment_transform")
            history_overlays = [{
                "rect": self._simplify_value(item.rect),
                "points": item.points.tolist() if item.points is not None else None,
                "closed": item.closed,
                "error": item.z_index >= 30 and str(getattr(report, "status", "")) == "nok",
            } for item in v2_overlay_items(tool, metrics, affine=transform)
              if item.kind in {"rect", "polygon", "polyline"}]

        return {
            "id": tool_id,
            "name": tool_name or tool_id or "Tool",
            "type": tool_type or diagnostics.get("type"),
            "order": getattr(report, "order", tool_order),
            "status": getattr(report, "status", None),
            "latency_ms": latency_value,
            "metrics": metrics,
            "diagnostics": diagnostics,
            "thresholds": self._simplify_value(getattr(getattr(tool, "thresholds", None), "values", {})),
            "roi": tool.roi.to_dict() if tool is not None else None,
            "history_overlays": history_overlays,
        }


    def _merge_pipeline_metrics(self, reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
        combined: dict[str, Any] = {}
        for entry in reports:
            metrics = entry.get("metrics")
            if not isinstance(metrics, Mapping):
                continue
            for key, value in metrics.items():
                if key not in combined and value is not None:
                    combined[key] = value
        return combined


    def _load_view_golden_array(self, recipe_name: str, view: object):
        import imageio.v3 as iio

        golden_name = getattr(view, "golden_path", "golden.png") or "golden.png"
        path = self.data_root / "recipes" / recipe_name / golden_name
        cache_key = (recipe_name, golden_name)

        try:
            stat = path.stat()
        except OSError:
            self._golden_cache.pop(cache_key, None)
            return None

        mtime_ns = getattr(stat, "st_mtime_ns", None)
        if mtime_ns is None:
            mtime_ns = int(stat.st_mtime * 1_000_000_000)

        cached = self._golden_cache.get(cache_key)
        if cached and cached[0] == mtime_ns:
            return cached[1]

        try:
            arr = iio.imread(path)
        except Exception as exc:
            print(f"[Run] Golden read failed for {golden_name}: {exc}")
            self._golden_cache.pop(cache_key, None)
            return None

        if arr is None:
            self._golden_cache.pop(cache_key, None)
            return None

        arr = np.asarray(arr)
        if arr.ndim == 3:
            try:
                import cv2

                if arr.shape[2] >= 3:
                    arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
                else:
                    arr = arr[:, :, 0]
            except Exception:
                arr = arr[:, :, 0]

        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8)
        if not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr)

        arr.setflags(write=False)
        self._golden_cache[cache_key] = (mtime_ns, arr)
        return arr


    def _record_run_result(
        self,
        recipe_name: str,
        *,
        status: str,
        metrics: Mapping[str, Any] | None,
        artifacts: Mapping[str, Any] | None,
    ) -> None:
        if not isinstance(artifacts, Mapping):
            return

        try:
            rid = self.db.recipe_id(recipe_name)
            if rid is None:
                rid = self.db.ensure_recipe(recipe_name)
        except Exception as exc:
            raise RuntimeError(f"Zápis receptu do výsledkov zlyhal: {exc}") from exc

        try:
            view_id = artifacts.get("view_id")
            run_id = artifacts.get("run_id")
            ts_ms = artifacts.get("ts_ms")
            meta_payload = artifacts.get("meta_payload")
            if not isinstance(meta_payload, Mapping):
                meta_payload = {}

            meta_dict = {str(k): v for k, v in dict(meta_payload).items()}
            if ts_ms is None:
                ts_ms = int(time.time() * 1000)
            meta_dict.setdefault("ts_ms", ts_ms)
            meta_dict.setdefault("status", status)
            meta_dict.setdefault("recipe", recipe_name)
            meta_dict.setdefault("nok", status != "ok")
            if view_id is not None:
                meta_dict.setdefault("view_id", view_id)
            if run_id is not None:
                meta_dict.setdefault("run_id", run_id)

            thumb_path = artifacts.get("thumb") or ""
            full_path = artifacts.get("full")

            self.db.insert_result(
                ts_ms=int(ts_ms),
                recipe_id=int(rid),
                ok=str(status).lower() == "ok",
                metrics=dict(metrics or {}),
                thumb_path=str(thumb_path),
                full_path=str(full_path) if full_path else None,
                meta_json=json.dumps(meta_dict, ensure_ascii=False),
                view_id=view_id,
                run_id=run_id,
            )
        except Exception as exc:
            raise RuntimeError(f"Zápis výsledku zlyhal: {exc}") from exc


    def _prepare_run_trigger(
        self,
        *,
        trigger_source: str,
        trigger_input_index: int | None = None,
        requested_spec: object | None = None,
    ) -> dict[str, Any] | None:
        recipe_name = self.current_recipe_name()
        recipe_cfg = load_recipe_config(recipe_name, base_dir=self.data_root)
        was_live_enabled = False
        gst_starts_before = int(getattr(self.cam, "gst_start_count", lambda: 0)())
        base_camera_state = snapshot_camera_state(self.cam)
        self._logger.info("snapshot camera state taken")
        self._logger.info("[CAPTURE_MODE] %s", self.capture_mode)

        view_specs: list[dict[str, Any]] = [
            self._build_runtime_view_spec(view, index)
            for index, view in enumerate(recipe_cfg.views)
        ]

        manual_specs = [spec for spec in view_specs if spec["trigger_mode"] == "manual"]
        all_manual = bool(manual_specs) and len(manual_specs) == len(view_specs)
        external_spec = None
        requested_view_id = ""
        if isinstance(requested_spec, Mapping):
            requested_view_id = str(
                getattr(requested_spec.get("view"), "id", "")
                or requested_spec.get("view_id")
                or ""
            )
            for spec in view_specs:
                if str(getattr(spec["view"], "id", "")) == requested_view_id:
                    external_spec = dict(spec)
                    for key in ("sequence_key", "sequence_position", "sequence_length"):
                        if key in requested_spec:
                            external_spec[key] = requested_spec[key]
                    all_manual = True
                    break
        is_routed_external = trigger_source in {"pico", "modbus"}
        if external_spec is None and is_routed_external and isinstance(trigger_input_index, Integral):
            external_spec = self._resolve_external_trigger_view(
                view_specs=view_specs,
                source=trigger_source,
                input_index=int(trigger_input_index),
            )
        elif external_spec is None and trigger_source == "manual":
            external_spec = self._resolve_manual_sequence_view(
                recipe_name=recipe_name,
                view_specs=view_specs,
            )
            if external_spec is not None:
                all_manual = True

        if isinstance(requested_spec, Mapping) and external_spec is None:
            self._logger.warning(
                "[RUN] software Pico trigger ignored reason=requested_view_not_found view=%s",
                requested_view_id or "unknown",
            )
            return None

        if is_routed_external and external_spec is None:
            return None
        if external_spec is not None:
            sequence_key = str(external_spec.get("sequence_key") or "")
            sequence_position = int(external_spec.get("sequence_position", 0))
            if sequence_position == 0:
                self._reset_view_sequence_state()
                per_view_statuses = {}
                if sequence_key.startswith("manual:"):
                    self._manual_trigger_statuses[recipe_name] = {}
                else:
                    self._external_sequence_statuses[sequence_key] = {}
            elif sequence_key.startswith("manual:"):
                per_view_statuses = dict(self._manual_trigger_statuses.get(recipe_name, {}))
            else:
                per_view_statuses = dict(self._external_sequence_statuses.get(sequence_key, {}))
            views_to_process = [external_spec]
            if not sequence_key.startswith("manual:"):
                self._reset_manual_trigger_progress(recipe_name)
        elif all_manual:
            cycle_position = self._manual_trigger_positions.get(recipe_name, 0)
            index_in_cycle = cycle_position % len(manual_specs)
            current_spec = manual_specs[index_in_cycle]
            self._manual_trigger_positions[recipe_name] = (index_in_cycle + 1) % len(manual_specs)
            if index_in_cycle == 0:
                self._manual_trigger_statuses[recipe_name] = {}
                self._reset_view_sequence_state()
                per_view_statuses = {}
            else:
                per_view_statuses = dict(self._manual_trigger_statuses.get(recipe_name, {}))
            views_to_process = [current_spec]
        else:
            self._reset_view_sequence_state()
            per_view_statuses = {}
            views_to_process = view_specs
            self._reset_manual_trigger_progress(recipe_name)

        if len(views_to_process) == 1:
            selected_view = views_to_process[0]["view"]
            selected_view_id = (
                getattr(selected_view, "id", None)
                or f"view_{views_to_process[0]['index'] + 1}"
            )
            self._active_view_id = selected_view_id

        state = {
            "was_live_enabled": was_live_enabled,
            "gst_starts_before": gst_starts_before,
            "recipe_name": recipe_name,
            "recipe_cfg": recipe_cfg,
            "logging_enabled": bool(getattr(recipe_cfg, "logging_enabled", True)),
            "run_id": f"{recipe_name}_{uuid.uuid4().hex[:8]}",
            "view_specs": view_specs,
            "views_to_process": views_to_process,
            "all_manual": all_manual,
            "sequence_key": str(views_to_process[0].get("sequence_key") or "")
            if len(views_to_process) == 1 else "",
            "per_view_statuses": per_view_statuses,
            "ignored_for_aggregation": set(),
            "last_preview_frame": None,
            "last_view_id": None,
            "captured_frames": {},
            "pending_overlays": {},
            "trigger_start_ts": time.monotonic(),
            "spec_lookup": {
                getattr(spec["view"], "id", None) or f"view_{spec['index']+1}": spec
                for spec in view_specs
            },
            "base_camera_state": base_camera_state,
            "trigger_source": trigger_source,
            "trigger_input_index": (
                int(trigger_input_index) if trigger_input_index is not None else None
            ),
            "fail_fast": bool(getattr(recipe_cfg.aggregation, "fail_fast", False)),
        }
        key = state['sequence_key']
        position = int(views_to_process[0].get('sequence_position', 0))
        if key:
            if position == 0:
                self._sequence_contexts[key] = {'cycle_id': uuid.uuid4().hex, 'captured_frames': {}, 'frame_ids': {}}
            sequence = self._sequence_contexts.get(key)
            if sequence is None:
                raise ValueError('Chýba začiatok vstupnej sekvencie.')
            state.update(sequence)
        else:
            state['cycle_id'] = uuid.uuid4().hex
            state['frame_ids'] = {}
        state['run_id'] = state['cycle_id']
        return state

    def _execute_view_trigger(self, spec: dict[str, Any], trigger_state: dict[str, Any]) -> dict[str, Any]:
        recipe_name = trigger_state["recipe_name"]
        recipe_cfg = trigger_state["recipe_cfg"]
        base_camera_state = trigger_state["base_camera_state"]
        captured_frames = trigger_state["captured_frames"]
        per_view_statuses = trigger_state["per_view_statuses"]
        all_manual = trigger_state["all_manual"]

        view = spec["view"]
        index = spec["index"]
        view_id = getattr(view, "id", None) or f"view_{index+1}"
        view_name = getattr(view, "name", view_id)
        trigger_mode = spec["trigger_mode"]
        settle_ms = spec["settle_ms"]
        interval_ms = spec["interval_ms"]
        self._logger.info("active view id: %s", view_id)
        self._logger.info("trigger mode for current view: %s", trigger_mode)
        self._log_trigger_cycle(
            "view_start",
            active_view=view_id,
            trigger_mode=trigger_mode,
            preview_state="paused",
            trigger_primed=bool(getattr(self.cam, "_trigger_primed", False)),
        )

        golden = self._load_view_golden_array(recipe_name, view)
        inspection_finished_ts: float | None = None
        view_frame_u8 = reused_view_frame(spec, captured_frames, view_image_rotation(view), camera_id=self.camera_id)

        trigger_requested_ts = time.monotonic()
        frame_received_ts = trigger_requested_ts

        if view_frame_u8 is None:
            self._log_run_trigger_context(
                f"RUN trigger capture flow for view={view_id}",
                requested_stream_mode=None,
                hid_set="skipped",
            )
            view_frame = self._capture_frame_for_view(
                trigger_mode_label=trigger_mode,
                master_caller="run_manual_trigger_master",
                view=view,
                base_camera_state=base_camera_state,
                settle_ms=settle_ms,
                transform_stage="inspection",
                capture_request_source=trigger_state.get("trigger_source", "manual"),
                frame_request=trigger_state.pop("frame_request", None),
            )
            if view_frame is None:
                raise RuntimeError(f"Kamera nevrátila snímku pre pohľad {view_id}.")
            view_frame_u8 = view_frame.copy()
            frame_received_ts = time.monotonic()
        else:
            frame_received_ts = time.monotonic()

        self._log_trigger_cycle(
            "view_capture_done",
            active_view=view_id,
            trigger_mode=trigger_mode,
            preview_state="paused",
            trigger_primed=bool(getattr(self.cam, "_trigger_primed", False)),
            frame_received=view_frame_u8 is not None,
        )

        if golden is None:
            status = "nok"
            reports = []
            diagnostics_payload = ["missing_golden"]
            combined_metrics = {}
            policy_applied = None
            result = None
            last_preview_frame = view_frame_u8.copy()
            inspection_finished_ts = time.monotonic()
        else:
            view_recipe = RecipeV2(
                pose_enabled=recipe_cfg.pose_enabled,
                regions=[dict(r) for r in recipe_cfg.regions],
                tools=[tool.copy() for tool in getattr(view, "tools", [])],
                views=[view.copy()],
                aggregation=recipe_cfg.aggregation.copy(),
                on_locator_failure=recipe_cfg.on_locator_failure,
                export_artifacts=recipe_cfg.export_artifacts,
                logging_enabled=recipe_cfg.logging_enabled,
            )
            result = run_pipeline(
                golden,
                view_frame_u8,
                view_recipe,
                recipe_name=recipe_name,
                notes=f"camera={self.camera_id};pico={self.pico_id};view={view_id};request={trigger_state.get('request_id')}",
                capture_filtered_roi=self.capture_filtered_roi,
                session_settings=self.session_settings.get_session_settings(),
            )
            inspection_finished_ts = time.monotonic()
            status = (result.status or "ok").lower()
            diagnostics_payload = [
                self._simplify_value(diag) for diag in getattr(result, "diagnostics", []) or []
            ]
            reports = [self._serialize_tool_report(report) for report in result.per_tool]
            combined_metrics = self._merge_pipeline_metrics(reports)
            policy_applied = getattr(result, "policy_applied", None)
            context_frame = getattr(result.context, "frame_aligned", None)
            if context_frame is None:
                context_frame = getattr(result.context, "frame", None)
            if isinstance(context_frame, np.ndarray):
                trigger_state["pending_overlays"][view_id] = (
                    context_frame,
                    view,
                    result,
                )
                # Pipeline frames already use the rotated inspection coordinates.
            last_preview_frame = context_frame.copy() if isinstance(context_frame, np.ndarray) else view_frame_u8.copy()

        per_view_statuses[view_id] = status
        if all_manual:
            self._manual_trigger_statuses[recipe_name] = dict(per_view_statuses)
        sequence_key = trigger_state.get("sequence_key")
        if sequence_key and not str(sequence_key).startswith("manual:"):
            self._external_sequence_statuses[str(sequence_key)] = dict(per_view_statuses)

        result_time_ts = inspection_finished_ts or time.monotonic()
        cycle_time_value = float(result.cycle_time_ms) if result is not None else None
        total_cycle_time_value = (result_time_ts - trigger_state["trigger_start_ts"]) * 1000.0
        capture_time_value = (frame_received_ts - trigger_requested_ts) * 1000.0
        processing_time_value = (result_time_ts - frame_received_ts) * 1000.0
        source_id = spec.get('frame_source_view_id')
        frame_id = trigger_state['frame_ids'].get(source_id) if source_id else None
        if spec.get('injected_capture') is not None:
            frame_id = spec.get('injected_frame_id')
        trigger_state['frame_ids'][view_id] = frame_id or f"{trigger_state['request_id']}:{view_id}"
        meta_payload = {
            'cycle_id': trigger_state['cycle_id'],
            'frame_id': trigger_state['frame_ids'][view_id],
            "mode": "manual",
            "request_id": trigger_state.get("request_id"),
            "trigger_source": trigger_state.get("trigger_source"),
            "trigger_input_index": trigger_state.get("trigger_input_index"),
            "status": status,
            "view_id": view_id,
            "view_name": view_name,
            "cycle_time_ms": cycle_time_value,
            "capture_time_ms": capture_time_value,
            "processing_time_ms": processing_time_value,
            "total_cycle_time_ms": total_cycle_time_value,
            "per_tool": reports,
            "diagnostics": diagnostics_payload,
            "metrics": combined_metrics,
            "sequence_statuses": dict(per_view_statuses),
        }
        if result is not None and any(report.get("type") == "mold.protection_v2" for report in reports):
            import hashlib
            meta_payload["recipe_version"] = hashlib.sha256(
                json.dumps(recipe_cfg.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            meta_payload["recipe_id"] = self.db.recipe_id(recipe_name) if self.db is not None else None
            meta_payload["ts_ms"] = int(time.time() * 1000)
            matrix = getattr(result.context, "T_total", None)
            meta_payload["empty_mold_v2_alignment"] = {
                "T_total": matrix.tolist() if matrix is not None else None,
                "valid": not any(item.get("locator_failure") for item in diagnostics_payload),
                "stored_frame_space": "original_rotated",
            }
        meta_payload.update(camera_id=self.camera_id, pico_id=self.pico_id)
        if policy_applied:
            meta_payload["policy_applied"] = policy_applied

        if trigger_state["logging_enabled"]:
            artifacts = save_production_result(
                view_frame_u8,
                meta_payload,
                recipe_name,
                store_full_nok=True,
                nok=status != "ok",
                run_id=trigger_state["run_id"],
                view_id=view_id,
                base_dir=self.data_root,
            )
            self._record_run_result(recipe_name, status=status, metrics=combined_metrics, artifacts=artifacts)
        captured_frames[view_id] = InspectionFrame(
            self._clone_frame(view_frame_u8), view_image_rotation(view), self.camera_id
        )
        trigger_state["last_preview_frame"] = last_preview_frame
        trigger_state["last_view_id"] = view_id
        self._view_states[view_id] = {'reports': reports}
        self.records.append({'camera_id': self.camera_id, 'pico_id': self.pico_id, 'view_id': view_id, 'status': status, 'frame': last_preview_frame,
            'v2_feedback': ({'frame': self._clone_frame(view_frame_u8), 'metadata': dict(meta_payload, recipe=recipe_name)}
                            if 'empty_mold_v2_alignment' in meta_payload else None),
            'reports': reports, 'cycle_time_ms': cycle_time_value, 'capture_time_ms': capture_time_value,
            'processing_time_ms': processing_time_value, 'total_cycle_time_ms': total_cycle_time_value})

        branch_target_id = None
        replace_queue = None
        if bool(spec["branch_enabled"]):
            if index == 0:
                trigger_state["ignored_for_aggregation"].add(view_id)
            branch_map = dict(spec["branch_targets"])
            branch_target_id = branch_map.get(status) or spec.get("branch_default_view_id")
            if branch_target_id and branch_target_id != view_id:
                target_spec = trigger_state["spec_lookup"].get(branch_target_id)
                if target_spec:
                    queued_spec = dict(target_spec)
                    queued_spec["injected_capture"] = captured_frames[view_id]
                    queued_spec["injected_frame_id"] = trigger_state["frame_ids"][view_id]
                    replace_queue = [queued_spec]
                else:
                    replace_queue = []

        should_break = bool(trigger_state["fail_fast"] and status == "nok" and not branch_target_id)
        if trigger_mode == "timed" and interval_ms is not None and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)
        return {"replace_queue": replace_queue, "should_break": should_break}


    def _finalize_run_trigger(self, state):
        status = aggregate_branching_statuses(state['recipe_cfg'].aggregation,
            state['per_view_statuses'], state['ignored_for_aggregation'])
        self.modbus.emit_heartbeat()
        self.modbus.signal_result(status)
        state['status'] = status
        state['records'] = list(self.records)
        state['relevant_reports'] = [report
            for view_id, value in state['per_view_statuses'].items() if value == status
            for report in self._view_states.get(view_id, {}).get('reports', [])]

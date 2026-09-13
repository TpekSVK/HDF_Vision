"""Ordering, execution, alignment and reporting of tool pipelines."""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Literal, Optional, Sequence
import time
import numpy as np
from app.services import logging_service, settings_service
from app.models.schema import RecipeV2, Tool, ToolDefinition
from app.utils import overlay as overlay_utils
from app.utils.tool_identity import compute_tool_identity
from app.services.tool_contracts import PipelineResult, PipelineToolReport, ToolRunResult, ToolRunnerContext, ToolTestRun
from app.services.tool_status import _safe_float

logger = logging.getLogger(__name__)


def compose_affine(
    T_total: np.ndarray | None, T_new: np.ndarray | None
) -> np.ndarray:
    """Left-compose 2×3 affine transforms.

    The resulting matrix corresponds to applying ``T_total`` first and then
    ``T_new`` (``T_new ∘ T_total``). ``None`` inputs are treated as identity
    transforms.
    """

    import numpy as np

    def _to_homogeneous(mat: np.ndarray | None) -> np.ndarray:
        if mat is None:
            return np.eye(3, dtype=np.float32)

        arr = np.asarray(mat, dtype=np.float32)
        if arr.shape != (2, 3):  # pragma: no cover - defensive programming
            raise ValueError("Affine transform must have shape (2, 3)")

        homo = np.eye(3, dtype=np.float32)
        homo[:2, :3] = arr
        return homo

    M_total = _to_homogeneous(T_total)
    M_new = _to_homogeneous(T_new)
    composed = M_new @ M_total
    return composed[:2, :3]


def _identity_affine() -> np.ndarray:
    return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)


def _validate_roi(tool: Tool, definition: ToolDefinition) -> None:
    roi_rect = tool.roi.rect()
    if roi_rect is None:
        return
    if not definition.meta.supports_roi:
        raise ValueError(
            f"Tool '{tool.name}' of type '{tool.type}' does not support ROI but one was provided"
        )
    x, y, w, h = roi_rect
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid ROI dimensions for tool '{tool.name}'")


def _validate_ignore_mask(tool: Tool, definition: ToolDefinition) -> None:
    mask = tool.ignore_mask.value
    if mask is None:
        return
    if not definition.meta.supports_ignore_mask:
        raise ValueError(
            f"Tool '{tool.name}' of type '{tool.type}' does not support ignore mask"
        )
    if mask.ndim != 2:
        raise ValueError(f"Ignore mask for tool '{tool.name}' must be 2D")


def _validate_params(tool: Tool) -> None:
    params = tool.params.values
    if params is None:
        return
    if not isinstance(params, dict):
        raise ValueError(f"Params for tool '{tool.name}' must be a dictionary")


def _make_report_tool_copy(tool: Tool) -> Tool:
    """Create a lightweight tool copy for pipeline reports without duplicating large masks."""

    return tool.copy(copy_mask=False)


class PipelineOrchestrator:
    """Central orchestrator ensuring ordered tool execution with shared context."""

    _LOCATOR_PREFIX = "locator."

    def run_pipeline(
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        recipe: RecipeV2,
        recipe_name: str | None = None,
        notes: str | None = None,
        capture_filtered_roi: bool = False,
        session_settings=None,
    ) -> PipelineResult:
        """Execute the configured pipeline and return aggregated results."""

        import numpy as np
        from app.services.tool_registry import ToolRegistry
        from app.utils import imaging

        from app.models.recipe_contract import validate_tool_contract
        for tool in recipe.tools:
            if ToolRegistry.get_tool_definition(tool.type) is None:
                raise ValueError(f"Tool type '{tool.type}' is not registered")
            validate_tool_contract(tool.type, tool.params.values)

        start_time = time.perf_counter()

        golden_array = np.asarray(golden)
        frame_array = np.asarray(frame)
        golden_gray = imaging.to_gray_u8(golden_array)
        frame_gray = imaging.to_gray_u8(frame_array)

        context = ToolRunnerContext(
            frame=frame_array,
            frame_aligned=frame_array,
            T_total=_identity_affine(),
            frame_is_aligned=False,
            frame_gray=frame_gray,
            frame_aligned_gray=frame_gray,
            golden_gray=golden_gray,
        )

        diagnostics: List[Dict[str, Any]] = []
        per_tool: List[PipelineToolReport] = []
        policy_applied: Optional[str] = None
        session_settings = session_settings or settings_service.get_session_settings()
        logging_enabled = session_settings.logging_enabled and bool(
            getattr(recipe, "logging_enabled", True)
        )
        collect_overlay = (
            logging_enabled
            and session_settings.export_artifacts
            and session_settings.export_overlay
            and bool(getattr(recipe, "export_artifacts", False))
        )

        overlay_palette = overlay_utils.default_palette() if collect_overlay else []
        overlay_index = 0
        pipeline_overlay_items: List[overlay_utils.OverlayItem] = []

        failure_policy = self._normalize_failure_policy(
            getattr(recipe, "on_locator_failure", "continue_without_alignment")
        )
        from app.services.learning_context import pipeline_learning_signatures
        learning_signatures = pipeline_learning_signatures(golden_array, recipe)
        tools = self._order_tools(recipe.tools)

        used_tool_ids: set[str] = set()

        for index, tool in enumerate(tools):
            definition = ToolRegistry.get_tool_definition(tool.type)
            if definition is None:
                raise ValueError(f"Tool type '{tool.type}' is not registered")

            _validate_roi(tool, definition)
            _validate_ignore_mask(tool, definition)
            _validate_params(tool)

            tool_id, tool_label, tool_order = compute_tool_identity(
                tool,
                fallback_index=index,
                used_ids=used_tool_ids,
            )
            diag_entry: Dict[str, Any] = {
                "tool_id": tool_id,
                "tool": tool_label,
                "type": tool.type,
                "order": tool_order,
                "status": "skipped",
            }

            if not tool.enabled:
                diag_entry["disabled"] = True
                diagnostics.append(diag_entry)
                continue

            runner = ToolRegistry.create_tool(tool.type)
            runner.prepare({"tool": tool, "tool_id": tool_id, "runner_context": context, "learning_signature": learning_signatures.get(id(tool)), "learning_alignment_valid": not any(entry.get("locator_failure") for entry in diagnostics), "capture_filtered_roi": capture_filtered_roi})

            frame_for_tool = (
                context.frame_aligned if context.frame_aligned is not None else context.frame
            )
            if frame_for_tool is None:
                raise ValueError("Frame data not available for tool execution")

            result = runner.run(
                golden_array,
                frame_for_tool,
                tool.params,
                tool.thresholds,
                {"roi": tool.roi},
            )
            diagnostics_payload = getattr(runner, "last_diagnostics", {})
            diag_data = diagnostics_payload if isinstance(diagnostics_payload, dict) else {}
            runner.teardown()

            if isinstance(diagnostics_payload, dict):
                diag_entry.update(diagnostics_payload)
            if result.debug_artifacts and isinstance(
                result.debug_artifacts.get("diagnostics"), dict
            ):
                diag_entry.update(result.debug_artifacts["diagnostics"])

            diag_entry["status"] = result.status
            diag_entry.setdefault("latency_ms", float(result.latency_ms))

            metrics = dict(result.metrics or {})
            metrics.setdefault("latency_ms", float(result.latency_ms))

            tool_overlay_items: List[overlay_utils.OverlayItem] = []
            if collect_overlay:
                if overlay_palette:
                    tool_color = overlay_palette[overlay_index % len(overlay_palette)]
                    overlay_index += 1
                else:  # pragma: no cover - defensive fallback
                    tool_color = (255, 0, 0)

                display_sources: List[Any] = []
                display_sources.extend(
                    overlay_utils.extract_display_items_from_artifacts(
                        result.debug_artifacts
                    )
                )
                if isinstance(diagnostics_payload, dict):
                    display_sources.extend(
                        overlay_utils.extract_display_items_from_artifacts(
                            diagnostics_payload
                        )
                    )

                overlay_affine = None
                if self._is_locator(tool):
                    overlay_affine = compose_affine(context.T_total, diag_entry.get("T"))
                else:
                    overlay_affine = context.T_total

                tool_overlay_items = overlay_utils.tool_overlay_items(
                    tool,
                    color=tool_color,
                    display_items=display_sources,
                    label=str(tool_label),
                    affine=overlay_affine,
                )
                pipeline_overlay_items.extend(tool_overlay_items)

            diagnostics.append(diag_entry)

            report_start = time.perf_counter() if logger.isEnabledFor(logging.DEBUG) else 0.0
            per_tool.append(
                PipelineToolReport(
                    tool=_make_report_tool_copy(tool),
                    tool_id=str(tool_id),
                    order=int(tool_order),
                    status=result.status,
                    metrics=metrics,
                    latency_ms=float(result.latency_ms),
                    diagnostics=dict(diag_entry),
                    overlay_items=tool_overlay_items,
                    filtered_roi=getattr(runner, "filtered_roi", None),
                )
            )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "PipelineToolReport created tool=%s took=%.2fms",
                    tool.name or tool.type,
                    (time.perf_counter() - report_start) * 1000.0,
                )

            if self._is_locator(tool):
                locator_found = bool(metrics.get("found", diag_data.get("found", True)))
                corr_value = _safe_float(metrics.get("corr", diag_data.get("corr")), 0.0)
                thresholds_map = dict(getattr(tool.thresholds, "values", {}) or {})
                threshold_raw = diag_data.get("threshold_corr", thresholds_map.get("threshold_corr"))
                threshold_corr = _safe_float(threshold_raw, 0.55)

                diag_entry["found"] = locator_found
                diag_entry["corr"] = corr_value
                diag_entry["threshold_corr"] = threshold_corr

                locator_failure = False
                failure_reason = None
                if not locator_found:
                    locator_failure = True
                    failure_reason = "not_found"
                elif corr_value < threshold_corr:
                    locator_failure = True
                    failure_reason = "low_corr"
                elif result.status == "nok":
                    locator_failure = True
                    failure_reason = "status_nok"

                if locator_failure:
                    diag_entry["locator_failure"] = True
                    if failure_reason:
                        diag_entry["locator_failure_reason"] = failure_reason
                    diag_entry["policy_applied"] = failure_policy
                    policy_applied = policy_applied or failure_policy
                    self._reset_alignment(context)
                    if failure_policy == "fail":
                        break
                else:
                    self._apply_locator_alignment(tool, context, diag_entry)

        cycle_time_ms = (time.perf_counter() - start_time) * 1000.0
        pipeline_status = self._aggregate_status(per_tool)

        result = PipelineResult(
            context=context,
            per_tool=per_tool,
            diagnostics=diagnostics,
            cycle_time_ms=float(cycle_time_ms),
            status=pipeline_status,
            policy_applied=policy_applied,
            overlay_items=pipeline_overlay_items if collect_overlay else [],
        )

        try:
            logging_service.record_pipeline_run(
                recipe=recipe,
                recipe_name=recipe_name,
                result=result,
                notes=notes,
                settings=session_settings,
            )
        except Exception as exc:  # pragma: no cover - logging must not break pipeline
            print("[pipeline][log][err]", exc)

        return result

    def run_tool_test(
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        recipe: RecipeV2,
    ) -> ToolTestRun:
        """Execute tools up to the last entry in ``recipe`` for wizard tests."""

        import numpy as np
        from app.services.tool_registry import ToolRegistry
        from app.utils import imaging

        from app.models.recipe_contract import validate_tool_contract
        for tool in recipe.tools:
            if ToolRegistry.get_tool_definition(tool.type) is None:
                raise ValueError(f"Tool type '{tool.type}' is not registered")
            validate_tool_contract(tool.type, tool.params.values)

        start_time = time.perf_counter()

        golden_array = np.asarray(golden)
        frame_array = np.asarray(frame)
        golden_gray = imaging.to_gray_u8(golden_array)
        frame_gray = imaging.to_gray_u8(frame_array)

        context = ToolRunnerContext(
            frame=frame_array,
            frame_aligned=frame_array,
            T_total=_identity_affine(),
            frame_is_aligned=False,
            frame_gray=frame_gray,
            frame_aligned_gray=frame_gray,
            golden_gray=golden_gray,
        )

        diagnostics: List[Dict[str, Any]] = []
        per_tool: List[PipelineToolReport] = []
        policy_applied: Optional[str] = None

        failure_policy = self._normalize_failure_policy(
            getattr(recipe, "on_locator_failure", "continue_without_alignment")
        )
        from app.services.learning_context import pipeline_learning_signatures
        learning_signatures = pipeline_learning_signatures(golden_array, recipe)
        tools = self._order_tools(recipe.tools)
        if not tools:
            raise ValueError("Recipe does not contain any tools")

        collect_overlay = True
        overlay_palette = overlay_utils.default_palette() if collect_overlay else []
        overlay_index = 0
        pipeline_overlay_items: List[overlay_utils.OverlayItem] = []

        target_result: ToolRunResult | None = None
        target_report: PipelineToolReport | None = None

        used_tool_ids: set[str] = set()

        for index, tool in enumerate(tools):
            definition = ToolRegistry.get_tool_definition(tool.type)
            if definition is None:
                raise ValueError(f"Tool type '{tool.type}' is not registered")

            _validate_roi(tool, definition)
            _validate_ignore_mask(tool, definition)
            _validate_params(tool)

            tool_id, tool_label, tool_order = compute_tool_identity(
                tool,
                fallback_index=index,
                used_ids=used_tool_ids,
            )
            diag_entry: Dict[str, Any] = {
                "tool_id": tool_id,
                "tool": tool_label,
                "type": tool.type,
                "order": tool_order,
                "status": "skipped",
            }

            if not tool.enabled:
                diag_entry["disabled"] = True
                diagnostics.append(diag_entry)
                continue

            runner = ToolRegistry.create_tool(tool.type)
            runner.prepare({"tool": tool, "tool_id": tool_id, "runner_context": context, "learning_signature": learning_signatures.get(id(tool)), "learning_alignment_valid": not any(entry.get("locator_failure") for entry in diagnostics)})

            frame_for_tool = (
                context.frame_aligned if context.frame_aligned is not None else context.frame
            )
            if frame_for_tool is None:
                raise ValueError("Frame data not available for tool execution")

            result = runner.run(
                golden_array,
                frame_for_tool,
                tool.params,
                tool.thresholds,
                {"roi": tool.roi},
            )
            diagnostics_payload = getattr(runner, "last_diagnostics", {})
            runner.teardown()

            if isinstance(diagnostics_payload, dict):
                diag_entry.update(diagnostics_payload)
            if result.debug_artifacts and isinstance(
                result.debug_artifacts.get("diagnostics"), dict
            ):
                diag_entry.update(result.debug_artifacts["diagnostics"])

            diag_entry["status"] = result.status
            diag_entry.setdefault("latency_ms", float(result.latency_ms))

            metrics = dict(result.metrics or {})
            metrics.setdefault("latency_ms", float(result.latency_ms))
            result.metrics = metrics

            tool_overlay_items: List[overlay_utils.OverlayItem] = []
            if collect_overlay:
                if overlay_palette:
                    tool_color = overlay_palette[overlay_index % len(overlay_palette)]
                    overlay_index += 1
                else:  # pragma: no cover - defensive fallback
                    tool_color = (255, 0, 0)

                display_sources: List[Any] = []
                display_sources.extend(
                    overlay_utils.extract_display_items_from_artifacts(result.debug_artifacts)
                )
                if isinstance(diagnostics_payload, dict):
                    display_sources.extend(
                        overlay_utils.extract_display_items_from_artifacts(
                            diagnostics_payload
                        )
                    )

                overlay_affine = None
                if self._is_locator(tool):
                    overlay_affine = compose_affine(context.T_total, diag_entry.get("T"))
                else:
                    overlay_affine = context.T_total

                tool_overlay_items = overlay_utils.tool_overlay_items(
                    tool,
                    color=tool_color,
                    display_items=display_sources,
                    label=str(tool_label),
                    affine=overlay_affine,
                )
                pipeline_overlay_items.extend(tool_overlay_items)

            diagnostics.append(diag_entry)

            report_start = time.perf_counter() if logger.isEnabledFor(logging.DEBUG) else 0.0
            report = PipelineToolReport(
                tool=_make_report_tool_copy(tool),
                tool_id=str(tool_id),
                order=int(tool_order),
                status=result.status,
                metrics=dict(metrics),
                latency_ms=float(result.latency_ms),
                diagnostics=dict(diag_entry),
                overlay_items=tool_overlay_items,
            )
            per_tool.append(report)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "PipelineToolReport created tool=%s took=%.2fms",
                    tool.name or tool.type,
                    (time.perf_counter() - report_start) * 1000.0,
                )

            if self._is_locator(tool):
                diag_data = diagnostics_payload if isinstance(diagnostics_payload, dict) else {}
                locator_found = bool(metrics.get("found", diag_data.get("found", True)))
                corr_value = _safe_float(metrics.get("corr", diag_data.get("corr")), 0.0)
                thresholds_map = dict(getattr(tool.thresholds, "values", {}) or {})
                threshold_raw = diag_data.get("threshold_corr", thresholds_map.get("threshold_corr"))
                threshold_corr = _safe_float(threshold_raw, 0.55)

                diag_entry["found"] = locator_found
                diag_entry["corr"] = corr_value
                diag_entry["threshold_corr"] = threshold_corr

                locator_failure = False
                failure_reason: Optional[str] = None
                if not locator_found:
                    locator_failure = True
                    failure_reason = "not_found"
                elif corr_value < threshold_corr:
                    locator_failure = True
                    failure_reason = "low_corr"
                elif result.status == "nok":
                    locator_failure = True
                    failure_reason = "status_nok"

                if locator_failure:
                    diag_entry["locator_failure"] = True
                    if failure_reason:
                        diag_entry["locator_failure_reason"] = failure_reason
                    diag_entry["policy_applied"] = failure_policy
                    policy_applied = policy_applied or failure_policy
                    self._reset_alignment(context)
                    if failure_policy == "fail":
                        break
                else:
                    self._apply_locator_alignment(tool, context, diag_entry)

            if index == len(tools) - 1:
                target_result = result
                target_report = report

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if target_result is None or target_report is None:
            failure_entry = next(
                (entry for entry in reversed(diagnostics) if entry.get("locator_failure")),
                None,
            )
            if failure_entry:
                tool_name = failure_entry.get("tool_id") or failure_entry.get("type") or "locator"
                reason = failure_entry.get("locator_failure_reason") or "locator failure"
                raise RuntimeError(
                    f"Pipeline stopped after locator '{tool_name}': {reason}."
                )
            raise RuntimeError("Target tool was not executed")

        return ToolTestRun(
            result=target_result,
            report=target_report,
            reports=per_tool,
            diagnostics=diagnostics,
            context=context,
            elapsed_ms=float(elapsed_ms),
            policy_applied=policy_applied,
            overlay_items=pipeline_overlay_items,
        )

    def _order_tools(self, tools: Sequence[Tool]) -> List[Tool]:
        sorted_tools = sorted(tools, key=lambda t: t.order)
        locators = [tool for tool in sorted_tools if self._is_locator(tool)]
        analyzers = [tool for tool in sorted_tools if not self._is_locator(tool)]
        return locators + analyzers

    def _is_locator(self, tool: Tool) -> bool:
        return tool.type.startswith(self._LOCATOR_PREFIX)

    def _apply_locator_alignment(
        self, tool: Tool, context: ToolRunnerContext, diagnostics: Dict[str, Any]
    ) -> None:
        from app.utils import imaging

        T_new = diagnostics.get("T")
        context.T_total = compose_affine(context.T_total, T_new)

        params_dict = dict(tool.params.values or {})
        apply_alignment = bool(params_dict.get("apply_alignment", True))
        if apply_alignment:
            source = (
                context.frame_aligned if context.frame_aligned is not None else context.frame
            )
            if source is None:
                raise ValueError("Source frame missing for locator alignment")
            if context.frame_is_aligned and context.frame_aligned_gray is not None:
                source_gray = context.frame_aligned_gray
            elif source is context.frame and context.frame_gray is not None:
                source_gray = context.frame_gray
            else:
                source_gray = imaging.to_gray_u8(source)
            if T_new is None:
                context.frame_aligned = source
                context.frame_aligned_gray = source_gray
            else:
                T_inv = imaging.invert_affine(T_new)
                context.frame_aligned = imaging.warp_by_affine_u8(source, T_inv)
                context.frame_aligned_gray = imaging.warp_by_affine_u8(source_gray, T_inv)
            context.frame_is_aligned = True
        else:
            context.frame_aligned = context.frame
            context.frame_aligned_gray = context.frame_gray
            context.frame_is_aligned = False

    def _reset_alignment(self, context: ToolRunnerContext) -> None:
        context.T_total = _identity_affine()
        context.frame_aligned = context.frame
        context.frame_aligned_gray = context.frame_gray
        context.frame_is_aligned = False

    @staticmethod
    def _normalize_failure_policy(
        policy: str | None,
    ) -> Literal["fail", "continue_without_alignment"]:
        if not isinstance(policy, str):
            return "continue_without_alignment"
        normalized = policy.lower().strip()
        if normalized == "fail":
            return "fail"
        return "continue_without_alignment"

    @staticmethod
    def _aggregate_status(
        per_tool: Sequence[PipelineToolReport],
    ) -> Literal["ok", "nok", "warn"]:
        priority: Dict[str, int] = {"ok": 0, "warn": 1, "nok": 2}
        current: Literal["ok", "nok", "warn"] = "ok"
        for entry in per_tool:
            if priority[entry.status] > priority[current]:
                current = entry.status
        return current


def run_pipeline(
    golden: np.ndarray,
    frame: np.ndarray,
    recipe: RecipeV2,
    *,
    recipe_name: str | None = None,
    notes: str | None = None,
    capture_filtered_roi: bool = False,
    session_settings=None,
) -> PipelineResult:
    """Execute the configured pipeline using the shared orchestrator."""

    orchestrator = PipelineOrchestrator()
    return orchestrator.run_pipeline(
        golden,
        frame,
        recipe,
        recipe_name=recipe_name,
        notes=notes,
        capture_filtered_roi=capture_filtered_roi,
        session_settings=session_settings,
    )


def run_tool_test(
    golden: np.ndarray,
    frame: np.ndarray,
    recipe: RecipeV2,
) -> ToolTestRun:
    """Execute a partial pipeline for wizard tool tests."""

    orchestrator = PipelineOrchestrator()
    return orchestrator.run_tool_test(golden, frame, recipe)

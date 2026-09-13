"""Shared data and runner interfaces; no registry or implementations."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable
import numpy as np
from app.models.schema import Tool, ToolParams, ToolThresholds
from app.utils import overlay as overlay_utils


@dataclass(slots=True)
class ToolRunResult:
    """Normalized result returned by tool runners."""

    status: Literal["ok", "nok", "warn"]
    metrics: Dict[str, Any]
    latency_ms: float
    debug_artifacts: Optional[Dict[str, Any]] = None


@runtime_checkable
class ITool(Protocol):
    """Common interface that all tool implementations must follow."""

    def prepare(self, context: dict[str, Any]) -> None:
        ...

    def run(
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        params: ToolParams,
        thresholds: ToolThresholds,
        context: dict[str, Any],
    ) -> ToolRunResult:
        ...

    def teardown(self) -> None:
        ...


class BaseTool:
    """Base helper implementing shared lifecycle for tools."""

    def __init__(self) -> None:
        self._prepared_context: dict[str, Any] = {}
        self.last_diagnostics: dict[str, Any] = {}

    def prepare(self, context: dict[str, Any]) -> None:  # type: ignore[override]
        self._prepared_context = dict(context or {})
        self.last_diagnostics = {}
        self.filtered_roi = None

    def teardown(self) -> None:  # type: ignore[override]
        self._prepared_context.clear()

    def run(  # type: ignore[override]
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        params: ToolParams,
        thresholds: ToolThresholds,
        context: dict[str, Any],
    ) -> ToolRunResult:
        raise NotImplementedError


@dataclass(slots=True)
class ToolRunnerContext:
    """Context shared across tool execution within the pipeline."""

    frame: np.ndarray
    frame_aligned: np.ndarray | None = None
    T_total: np.ndarray | None = None
    frame_is_aligned: bool = False
    frame_gray: np.ndarray | None = None
    frame_aligned_gray: np.ndarray | None = None
    golden_gray: np.ndarray | None = None


@dataclass(slots=True)
class PipelineToolReport:
    """Aggregated result for a single tool within the pipeline."""

    tool: Tool
    tool_id: str
    order: int
    status: Literal["ok", "nok", "warn"]
    metrics: Dict[str, Any]
    latency_ms: float
    diagnostics: Dict[str, Any]
    overlay_items: list[overlay_utils.OverlayItem] = field(default_factory=list)
    filtered_roi: Any = None


@dataclass(slots=True)
class PipelineResult:
    """Result of executing the entire tool pipeline."""

    context: ToolRunnerContext
    per_tool: List[PipelineToolReport]
    diagnostics: List[Dict[str, Any]]
    cycle_time_ms: float
    status: Literal["ok", "nok", "warn"]
    policy_applied: Optional[str] = None
    overlay_items: List[overlay_utils.OverlayItem] = field(default_factory=list)


@dataclass(slots=True)
class ToolTestRun:
    """Result of executing a partial pipeline for tool testing."""

    result: ToolRunResult
    report: PipelineToolReport
    reports: List[PipelineToolReport]
    diagnostics: List[Dict[str, Any]]
    context: ToolRunnerContext
    elapsed_ms: float
    policy_applied: Optional[str] = None
    overlay_items: List[overlay_utils.OverlayItem] = field(default_factory=list)

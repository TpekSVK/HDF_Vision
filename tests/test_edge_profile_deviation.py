import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("cv2")

from app.models.schema import Tool, ToolMask, ToolParams, ToolRoi, ToolThresholds
from app.services.tool_service import ToolRunnerContext
from app.services.tools.edge_profile_deviation import EdgeProfileDeviationTool


def _make_edge_image(height: int, width: int, y_profile: np.ndarray) -> np.ndarray:
    image = np.zeros((height, width), dtype=np.uint8)
    for x in range(width):
        y_edge = int(round(y_profile[x]))
        y_edge = max(0, min(height - 1, y_edge))
        image[y_edge:, x] = 255
    return image


def _run_tool(image: np.ndarray, params: dict, thresholds: dict, tool_mask: ToolMask | None = None):
    tool = EdgeProfileDeviationTool()
    tool_model = Tool(
        type="edge_profile_deviation",
        name="edge_profile_deviation",
        roi=ToolRoi({"x": 0, "y": 0, "w": image.shape[1], "h": image.shape[0]}),
        ignore_mask=tool_mask or ToolMask(),
        params=ToolParams(params),
        thresholds=ToolThresholds(thresholds),
    )
    runner_context = ToolRunnerContext(frame=image, frame_aligned=None, T_total=None, frame_is_aligned=False)
    tool.prepare({"tool": tool_model, "tool_id": "edge_profile_deviation", "runner_context": runner_context})
    return tool.run(image, image, ToolParams(params), ToolThresholds(thresholds), {})


def test_edge_profile_deviation_straight_edge() -> None:
    height, width = 120, 200
    y_profile = np.full(width, 50.0)
    image = _make_edge_image(height, width, y_profile)

    params = {
        "point_a": {"x": 20, "y": 50},
        "point_b": {"x": 180, "y": 50},
        "points_in_roi": True,
        "orientation": "auto",
        "blur_sigma": 0.5,
        "scan_step": 2,
        "edge_polarity": "dark_to_light",
        "grad_threshold": 5.0,
        "search_half_window": 6,
        "outlier_trim_pct": 0.0,
        "min_coverage": 0.5,
    }
    thresholds = {"max_deviation_max": 1.0, "coverage_min": 0.5}

    result = _run_tool(image, params, thresholds)
    assert result.status == "ok"
    assert result.metrics["max_deviation"] <= 1.0
    assert result.metrics["coverage"] >= 0.5


def test_edge_profile_deviation_detects_deviation() -> None:
    height, width = 120, 200
    x = np.arange(width)
    amplitude = 5.0
    y_profile = 50.0 + amplitude * np.sin(2.0 * np.pi * x / width)
    image = _make_edge_image(height, width, y_profile)

    params = {
        "point_a": {"x": 0, "y": 50},
        "point_b": {"x": width - 1, "y": 50},
        "points_in_roi": True,
        "orientation": "horizontal",
        "blur_sigma": 0.5,
        "scan_step": 1,
        "edge_polarity": "any",
        "grad_threshold": 5.0,
        "search_half_window": 10,
        "outlier_trim_pct": 0.0,
        "min_coverage": 0.6,
    }
    thresholds = {"max_deviation_max": 3.0, "coverage_min": 0.6}

    result = _run_tool(image, params, thresholds)
    assert result.status == "nok"
    assert result.metrics["max_deviation"] >= 3.0


def test_edge_profile_deviation_warns_on_no_coverage() -> None:
    height, width = 80, 120
    y_profile = np.full(width, 30.0)
    image = _make_edge_image(height, width, y_profile)

    ignore_mask = np.ones_like(image, dtype=np.uint8) * 255
    params = {
        "point_a": {"x": 10, "y": 30},
        "point_b": {"x": 100, "y": 30},
        "points_in_roi": True,
        "orientation": "horizontal",
        "blur_sigma": 0.0,
        "scan_step": 2,
        "edge_polarity": "any",
        "grad_threshold": 5.0,
        "search_half_window": 5,
        "outlier_trim_pct": 0.0,
        "min_coverage": 0.5,
    }
    thresholds = {"max_deviation_max": 1.0, "coverage_min": 0.5}

    result = _run_tool(image, params, thresholds, ToolMask(ignore_mask))
    assert result.status == "warn"
    assert result.metrics["coverage"] == 0.0


def test_edge_profile_deviation_defaults_points_to_global_coordinates() -> None:
    height, width = 140, 240
    y_profile = np.full(width, 70.0)
    image = _make_edge_image(height, width, y_profile)

    params = {
        "point_a": {"x": 60, "y": 70},
        "point_b": {"x": 180, "y": 70},
        # points_in_roi intentionally omitted (UI stores points in golden coords)
        "orientation": "auto",
        "blur_sigma": 0.5,
        "scan_step": 2,
        "edge_polarity": "dark_to_light",
        "grad_threshold": 5.0,
        "search_half_window": 8,
        "outlier_trim_pct": 0.0,
        "min_coverage": 0.5,
    }
    thresholds = {"max_deviation_max": 1.0, "coverage_min": 0.5}

    tool = EdgeProfileDeviationTool()
    tool_model = Tool(
        type="edge_profile_deviation",
        name="edge_profile_deviation",
        roi=ToolRoi({"x": 40, "y": 20, "w": 170, "h": 90}),
        ignore_mask=ToolMask(),
        params=ToolParams(params),
        thresholds=ToolThresholds(thresholds),
    )
    runner_context = ToolRunnerContext(frame=image, frame_aligned=None, T_total=None, frame_is_aligned=False)
    tool.prepare({"tool": tool_model, "tool_id": "edge_profile_deviation", "runner_context": runner_context})

    result = tool.run(image, image, ToolParams(params), ToolThresholds(thresholds), {})
    assert result.status == "ok"
    assert result.metrics["coverage"] >= 0.5

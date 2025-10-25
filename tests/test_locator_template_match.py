import sys
from pathlib import Path

import math
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("cv2")
import cv2

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import LocatorTemplateMatchTool, run_locator_template_match


def test_run_locator_template_match_returns_expected_translation():
    golden = np.zeros((20, 20), dtype=np.uint8)
    pattern = np.array(
        [
            [10, 20, 30, 40],
            [50, 60, 70, 80],
            [90, 100, 110, 120],
            [130, 140, 150, 160],
        ],
        dtype=np.uint8,
    )

    golden[5:9, 7:11] = pattern

    frame = np.zeros_like(golden)
    frame[8:12, 9:13] = pattern

    params = {
        "use_golden_crop": False,
        "template_roi": {"x": 7, "y": 5, "w": 4, "h": 4},
        "coarse_cap": 32,
    }
    thresholds = {"threshold_corr": 0.1}
    search_roi = {"x": 5, "y": 3, "w": 12, "h": 12}

    run_result, diagnostics = run_locator_template_match(
        golden, frame, params, thresholds, search_roi
    )

    assert run_result.status == "ok"
    assert run_result.metrics["dx"] == pytest.approx(2.0, abs=1e-3)
    assert run_result.metrics["dy"] == pytest.approx(3.0, abs=1e-3)
    assert run_result.metrics["theta_deg"] == pytest.approx(0.0, abs=1e-3)
    assert "latency_ms" in run_result.metrics
    assert run_result.metrics["latency_ms"] >= 0.0
    assert diagnostics["latency_ms"] >= 0.0
    assert diagnostics["theta_deg"] == pytest.approx(0.0, abs=1e-3)
    expected_T = np.array([[1.0, 0.0, 2.0], [0.0, 1.0, 3.0]], dtype=np.float32)
    assert np.allclose(diagnostics["T"], expected_T)


def test_run_locator_template_match_clamps_out_of_bounds_roi():
    golden = np.zeros((10, 10), dtype=np.uint8)
    frame = np.zeros_like(golden)

    params = {"use_golden_crop": True, "coarse_cap": 32}
    thresholds = {"threshold_corr": 0.9}
    search_roi = {"x": -5, "y": -5, "w": 4, "h": 4}

    run_result, diagnostics = run_locator_template_match(
        golden, frame, params, thresholds, search_roi
    )

    assert run_result.metrics["dx"] == pytest.approx(0.0)
    assert run_result.metrics["dy"] == pytest.approx(0.0)
    assert run_result.metrics["theta_deg"] == pytest.approx(0.0)
    assert run_result.status == "warn"
    assert "latency_ms" in run_result.metrics
    assert run_result.metrics["latency_ms"] >= 0.0
    assert diagnostics["latency_ms"] >= 0.0
    assert np.allclose(
        diagnostics["T"], np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    )


@pytest.mark.parametrize(
    "threshold, expected_status",
    [
        (0.99, "nok"),
        (0.10, "ok"),
    ],
)
def test_locator_status_respects_threshold(threshold: float, expected_status: str) -> None:
    golden = np.zeros((20, 20), dtype=np.uint8)
    golden[4:12, 5:13] = 180
    frame = np.zeros_like(golden)
    frame[6:14, 7:15] = 180

    params = {"use_golden_crop": True, "coarse_cap": 32}
    thresholds = {"threshold_corr": threshold}
    search_roi = {"x": 0, "y": 0, "w": 20, "h": 20}

    run_result, diagnostics = run_locator_template_match(
        golden, frame, params, thresholds, search_roi
    )

    assert run_result.status == expected_status
    assert diagnostics["status"] == expected_status
    assert "latency_ms" in run_result.metrics
    assert diagnostics["latency_ms"] >= 0.0


def test_locator_template_match_detects_rotation() -> None:
    golden = np.zeros((40, 40), dtype=np.uint8)
    block = np.arange(64, dtype=np.uint8).reshape(8, 8)
    golden[12:20, 16:24] = block

    center = (golden.shape[1] / 2.0, golden.shape[0] / 2.0)
    angle_deg = 12.0
    rot = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    frame = cv2.warpAffine(golden, rot, golden.shape[::-1], flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)

    params = {
        "use_golden_crop": True,
        "coarse_cap": 64,
        "rotation_enabled": True,
        "angle_range_deg": 15.0,
        "angle_step_deg": 1.0,
    }
    thresholds = {"threshold_corr": 0.1}
    search_roi = {"x": 0, "y": 0, "w": 40, "h": 40}

    run_result, diagnostics = run_locator_template_match(
        golden, frame, params, thresholds, search_roi
    )

    assert run_result.status == "ok"
    assert run_result.metrics["theta_deg"] == pytest.approx(angle_deg, abs=1.0)
    assert diagnostics["theta_deg"] == pytest.approx(angle_deg, abs=1.0)
    assert run_result.metrics["dx"] == pytest.approx(0.0, abs=2.0)
    assert run_result.metrics["dy"] == pytest.approx(0.0, abs=2.0)
    cos_t = math.cos(math.radians(diagnostics["theta_deg"]))
    sin_t = math.sin(math.radians(diagnostics["theta_deg"]))
    expected_T = np.array([[cos_t, -sin_t, run_result.metrics["dx"]], [sin_t, cos_t, run_result.metrics["dy"]]])
    assert np.allclose(diagnostics["T"], expected_T, atol=1e-1)

def test_locator_template_cache_reuses_downsample(monkeypatch) -> None:
    tool = LocatorTemplateMatchTool()
    tool.prepare({"tool_id": "locator.cache"})

    golden = np.zeros((64, 64), dtype=np.uint8)
    golden[10:26, 14:30] = 180
    frame = np.zeros_like(golden)
    frame[13:29, 17:33] = 180

    params = ToolParams(
        {
            "use_golden_crop": False,
            "template_roi": {"x": 14, "y": 10, "w": 16, "h": 16},
            "coarse_cap": 12,
        }
    )
    thresholds = ToolThresholds({"threshold_corr": 0.05})
    roi = {"x": 12, "y": 8, "w": 24, "h": 24}

    resize_calls = {"count": 0}
    original_resize = cv2.resize

    def counting_resize(*args, **kwargs):
        resize_calls["count"] += 1
        return original_resize(*args, **kwargs)

    monkeypatch.setattr(cv2, "resize", counting_resize)

    tool.run(golden, frame, params, thresholds, {"roi": roi})
    first_pass = resize_calls["count"]
    assert first_pass >= 2

    tool.run(golden, frame, params, thresholds, {"roi": roi})
    second_pass = resize_calls["count"] - first_pass
    assert second_pass == 1


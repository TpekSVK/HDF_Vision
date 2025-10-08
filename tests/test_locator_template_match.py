from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("cv2")

from app.services.tool_service import run_locator_template_match


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

    result = run_locator_template_match(golden, frame, params, thresholds, search_roi)

    assert result["status"] == "OK"
    assert result["dx"] == pytest.approx(2.0, abs=1e-3)
    assert result["dy"] == pytest.approx(3.0, abs=1e-3)
    expected_T = np.array([[1.0, 0.0, 2.0], [0.0, 1.0, 3.0]], dtype=np.float32)
    assert np.allclose(result["T"], expected_T)


def test_run_locator_template_match_clamps_out_of_bounds_roi():
    golden = np.zeros((10, 10), dtype=np.uint8)
    frame = np.zeros_like(golden)

    params = {"use_golden_crop": True, "coarse_cap": 32}
    thresholds = {"threshold_corr": 0.9}
    search_roi = {"x": -5, "y": -5, "w": 4, "h": 4}

    result = run_locator_template_match(golden, frame, params, thresholds, search_roi)

    assert result["dx"] == pytest.approx(0.0)
    assert result["dy"] == pytest.approx(0.0)
    assert result["status"] == "WARN"
    assert np.allclose(
        result["T"], np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    )


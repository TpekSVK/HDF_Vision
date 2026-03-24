import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_presence_absence_bright_polarity_detects_light_object():
    cv2 = pytest.importorskip("cv2")

    from app.tools.presence_absence import (
        PresenceAbsenceCheckParams,
        PresenceAbsenceCheckTool,
        ToolContext,
        ToolResult,
    )

    image = np.zeros((128, 128), dtype=np.uint8)
    cv2.rectangle(image, (30, 30), (90, 90), 255, -1)

    roi_mask = np.zeros_like(image, dtype=np.uint8)
    roi_mask[20:100, 20:100] = 255

    tool = PresenceAbsenceCheckTool()
    params = PresenceAbsenceCheckParams(
        polarity="bright",
        binary_threshold=128,
        min_area_px=3000,
        max_area_px=5000,
        min_fill_ratio=0.4,
        max_fill_ratio=0.8,
    )

    result = tool.run(image, roi_mask, ToolContext(params=params))
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.metrics["foreground_polarity"] == "bright"
    assert result.metrics["area_px"] > 3000


def test_presence_absence_dark_polarity_detects_dark_object():
    cv2 = pytest.importorskip("cv2")

    from app.tools.presence_absence import (
        PresenceAbsenceCheckParams,
        PresenceAbsenceCheckTool,
        ToolContext,
    )

    image = np.full((120, 120), 220, dtype=np.uint8)
    cv2.circle(image, (60, 60), 25, 10, -1)

    roi_mask = np.zeros_like(image, dtype=np.uint8)
    roi_mask[20:100, 20:100] = 255

    tool = PresenceAbsenceCheckTool()
    params = PresenceAbsenceCheckParams(
        polarity="dark",
        binary_threshold=50,
        min_area_px=1500,
        max_area_px=2500,
        min_fill_ratio=0.2,
        max_fill_ratio=0.5,
    )

    result = tool.run(image, roi_mask, ToolContext(params=params))
    assert result.ok is True
    assert result.metrics["foreground_polarity"] == "dark"


def test_presence_absence_ignore_mask_excludes_pixels_from_area_and_fill_ratio():
    pytest.importorskip("cv2")

    from app.tools.presence_absence import (
        PresenceAbsenceCheckParams,
        PresenceAbsenceCheckTool,
        ToolContext,
    )

    image = np.full((10, 10), 255, dtype=np.uint8)
    roi_mask = np.zeros_like(image, dtype=np.uint8)
    roi_mask[:5, :] = 255

    tool = PresenceAbsenceCheckTool()
    params = PresenceAbsenceCheckParams(
        polarity="bright",
        binary_threshold=128,
        min_area_px=50,
        max_area_px=50,
        min_fill_ratio=1.0,
        max_fill_ratio=1.0,
    )

    result = tool.run(image, roi_mask, ToolContext(params=params))
    assert result.ok is True
    assert result.metrics["effective_pixels"] == 50
    assert result.metrics["area_px"] == 50
    assert result.metrics["fill_ratio"] == pytest.approx(1.0)


def test_presence_absence_zero_effective_pixels_is_safe_and_returns_nok():
    pytest.importorskip("cv2")

    from app.tools.presence_absence import (
        PresenceAbsenceCheckParams,
        PresenceAbsenceCheckTool,
        ToolContext,
    )

    image = np.full((8, 8), 255, dtype=np.uint8)
    roi_mask = np.zeros_like(image, dtype=np.uint8)

    tool = PresenceAbsenceCheckTool()
    params = PresenceAbsenceCheckParams(
        polarity="bright",
        binary_threshold=100,
        min_area_px=1,
        max_area_px=64,
        min_fill_ratio=0.1,
        max_fill_ratio=1.0,
    )

    result = tool.run(image, roi_mask, ToolContext(params=params))
    assert result.ok is False
    assert result.metrics["effective_pixels"] == 0
    assert "effective_pixels is zero" in (result.reason or "")

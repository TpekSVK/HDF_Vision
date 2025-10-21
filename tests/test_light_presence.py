import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def test_light_presence_circle_detection_within_expected_area():
    cv2 = pytest.importorskip("cv2")

    from app.tools.light_presence import (
        LightPresenceCheckParams,
        LightPresenceCheckTool,
        ToolContext,
        ToolResult,
    )

    tool = LightPresenceCheckTool()

    image = np.zeros((256, 256), dtype=np.uint8)
    center = (128, 128)
    radius = 20
    cv2.circle(image, center, radius, 255, -1)

    roi_mask = np.zeros_like(image, dtype=np.uint8)
    roi_mask[center[1] - radius - 5 : center[1] + radius + 5, center[0] - radius - 5 : center[0] + radius + 5] = 255

    params = LightPresenceCheckParams(
        binary_threshold=128,
        min_area_px=1000,
        max_area_px=2000,
    )
    context = ToolContext(params=params)

    result = tool.run(image, roi_mask, context)
    assert isinstance(result, ToolResult)
    assert result.ok is True

    area_px = result.metrics["area_px"]
    expected_area = math.pi * radius * radius
    assert expected_area * 0.9 <= area_px <= expected_area * 1.1
    assert result.metrics["threshold"] == 128
    assert "binary" in result.debug_images
    assert result.debug_images["binary"].dtype == np.uint8

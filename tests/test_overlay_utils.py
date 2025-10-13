from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("cv2")

from app.models.schema import Tool, ToolMask, ToolParams, ToolRoi, ToolThresholds  # noqa: E402
from app.utils import overlay as overlay_utils  # noqa: E402


def test_render_overlay_mask_preserves_holes() -> None:
    mask = np.ones((10, 10), dtype=np.uint8)
    mask[3:7, 3:7] = 0
    item = overlay_utils.OverlayItem.from_mask(mask, color=(0, 255, 0), alpha=120)
    assert item is not None

    overlay = overlay_utils.render_overlay(mask.shape, [item])
    assert overlay is not None
    assert overlay.shape == (10, 10, 4)

    # inside mask
    assert overlay[1, 1, 3] > 0
    np.testing.assert_array_equal(overlay[1, 1, :3], np.array([0, 255, 0], dtype=np.uint8))

    # hole remains transparent
    assert overlay[5, 5, 3] == 0

    # outside mask transparent
    assert overlay[9, 9, 3] == 0


def test_tool_overlay_items_include_roi_and_mask() -> None:
    tool = Tool(
        type="ssim",
        name="ssim",
        enabled=True,
        order=0,
        roi=ToolRoi({"x": 1, "y": 2, "w": 5, "h": 4}),
        ignore_mask=ToolMask(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.uint8)),
        params=ToolParams({}),
        thresholds=ToolThresholds({}),
    )

    items = overlay_utils.tool_overlay_items(tool, color=(255, 0, 0))
    kinds = {item.kind for item in items}
    assert "rect" in kinds
    assert "mask" in kinds


def test_render_overlay_draws_roi_on_top_of_mask() -> None:
    mask = np.ones((8, 8), dtype=np.uint8)
    mask_item = overlay_utils.OverlayItem.from_mask(mask, color=(0, 0, 255), alpha=80)
    rect_item = overlay_utils.OverlayItem.rect((0, 0, 8, 8), color=(255, 0, 0), thickness=2, alpha=255)
    overlay = overlay_utils.render_overlay(mask.shape, [mask_item, rect_item])
    assert overlay is not None

    # Corner pixel should reflect rectangle color (blue in BGR)
    np.testing.assert_array_equal(overlay[0, 0, :3], np.array([255, 0, 0], dtype=np.uint8))
    assert overlay[0, 0, 3] > 0

    # Center pixel should show mask color (red in BGR)
    np.testing.assert_array_equal(overlay[4, 4, :3], np.array([0, 0, 255], dtype=np.uint8))
    assert overlay[4, 4, 3] > 0


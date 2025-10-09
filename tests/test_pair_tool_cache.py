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
from app.services.tools.mse import MSETool


def test_pair_tool_mask_cache_respects_roi_changes() -> None:
    golden = np.zeros((40, 40), dtype=np.uint8)
    frame = golden.copy()
    ignore_mask = np.zeros_like(golden, dtype=np.uint8)
    ignore_mask[6:18, 7:19] = 255

    tool_model = Tool(
        type="mse",
        name="mse",
        roi=ToolRoi({"x": 4, "y": 5, "w": 14, "h": 12}),
        ignore_mask=ToolMask(ignore_mask),
        params=ToolParams({}),
        thresholds=ToolThresholds({}),
    )

    runner_context = ToolRunnerContext(frame=frame, frame_aligned=None, T_total=None, frame_is_aligned=False)

    tool = MSETool()
    tool.prepare({"tool": tool_model, "tool_id": "mse", "runner_context": runner_context})

    params = ToolParams({})
    thresholds = ToolThresholds({})

    tool.run(golden, frame, params, thresholds, {})
    first_mask = tool._roi_mask_cache_entry["mask"]
    assert first_mask is not None

    tool.run(golden, frame, params, thresholds, {})
    second_mask = tool._roi_mask_cache_entry["mask"]
    assert second_mask is first_mask

    tool_model.roi = ToolRoi({"x": 5, "y": 5, "w": 14, "h": 12})
    tool.run(golden, frame, params, thresholds, {})
    third_mask = tool._roi_mask_cache_entry["mask"]
    assert third_mask is not None
    assert third_mask is not first_mask

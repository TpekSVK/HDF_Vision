from __future__ import annotations

import numpy as np

from app.models.schema import ToolNode
from app.utils import imaging as img


def evaluate(golden_u8: np.ndarray, frame_u8: np.ndarray, tool: ToolNode) -> dict:
    x, y, w, h = tool.roi.x, tool.roi.y, tool.roi.w, tool.roi.h
    g_roi = golden_u8[y : y + h, x : x + w]
    f_roi = frame_u8[y : y + h, x : x + w]
    mask_roi = None
    if tool.ignore_mask is not None:
        mask_roi = tool.ignore_mask[y : y + h, x : x + w]
        if mask_roi.size == 0:
            mask_roi = None
    ssim_val = float(img.ssim_u8(f_roi, g_roi, mask_roi))
    ssim_min = float(tool.thresholds.get("ssim_min", 0.88))
    return {
        "ok": ssim_val >= ssim_min,
        "metrics": {
            "ssim": ssim_val,
            "ssim_min": ssim_min,
        },
        "frame_out": None,
    }

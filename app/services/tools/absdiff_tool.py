from __future__ import annotations

import cv2
import numpy as np

from app.models.schema import ToolNode
from app.utils import imaging as img


def _apply_ignore_mask(bin_mask: np.ndarray, ignore_mask: np.ndarray | None) -> np.ndarray:
    if ignore_mask is None or ignore_mask.size == 0:
        return bin_mask
    inv = cv2.bitwise_not(ignore_mask)
    return cv2.bitwise_and(bin_mask, inv)


def evaluate(golden_u8: np.ndarray, frame_u8: np.ndarray, tool: ToolNode) -> dict:
    x, y, w, h = tool.roi.x, tool.roi.y, tool.roi.w, tool.roi.h
    g_roi = golden_u8[y : y + h, x : x + w]
    f_roi = frame_u8[y : y + h, x : x + w]
    if g_roi.size == 0 or f_roi.size == 0:
        return {
            "ok": True,
            "metrics": {"blob_count": 0, "total_area": 0},
            "frame_out": None,
        }

    ignore_roi = None
    if tool.ignore_mask is not None:
        ignore_roi = tool.ignore_mask[y : y + h, x : x + w]
        if ignore_roi.size == 0:
            ignore_roi = None

    g_blur = img.blur_gaussian_u8(g_roi, sigma=0.8)
    f_blur = img.blur_gaussian_u8(f_roi, sigma=0.8)
    diff = img.absdiff_u8(g_blur, f_blur)

    diff_thresh = int(tool.thresholds.get("diff_thresh", 22))
    if diff_thresh > 0:
        bin_mask = img.threshold_bin_u8(diff, diff_thresh, 255, cv2.THRESH_BINARY)
    else:
        masked = diff if ignore_roi is None else cv2.bitwise_and(diff, cv2.bitwise_not(ignore_roi))
        bin_mask = img.threshold_bin_u8(masked, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    if ignore_roi is not None:
        bin_mask = _apply_ignore_mask(bin_mask, ignore_roi)

    bin_mask = img.morphology_open_then_dilate_u8(bin_mask, k_open=3, k_dil=3)

    cnts, _ = cv2.findContours(bin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_blob_area = int(tool.thresholds.get("min_blob_area", 50))
    areas = [cv2.contourArea(c) for c in cnts if cv2.contourArea(c) >= min_blob_area]
    total_area = float(np.sum(areas))
    blob_count = int(len(areas))

    max_total_area = int(tool.thresholds.get("max_total_area", 2000))
    max_blob_count = int(tool.thresholds.get("max_blob_count", 10))

    ok = (blob_count <= max_blob_count) and (total_area <= max_total_area)

    return {
        "ok": ok,
        "metrics": {
            "blob_count": blob_count,
            "total_area": int(total_area),
            "min_blob_area": min_blob_area,
            "max_total_area": max_total_area,
            "max_blob_count": max_blob_count,
        },
        "frame_out": None,
    }

from __future__ import annotations

from app.models.schema import ToolNode
from app.utils import imaging as img


def evaluate(golden_u8: np.ndarray, frame_u8: np.ndarray, tool: ToolNode) -> dict:
    x, y, w, h = tool.roi.x, tool.roi.y, tool.roi.w, tool.roi.h
    min_corr = float(tool.thresholds.get("min_corr", 0.55))
    search_margin = int(tool.thresholds.get("search_margin", 200))

    templ = golden_u8[y : y + h, x : x + w]
    if templ.size == 0:
        return {
            "ok": False,
            "metrics": {"corr": 0.0, "dx": 0.0, "dy": 0.0, "used": 0, "min_corr": min_corr},
            "frame_out": None,
        }
    H, W = frame_u8.shape[:2]
    xs = max(0, x - search_margin)
    ys = max(0, y - search_margin)
    xe = min(W, x + w + search_margin)
    ye = min(H, y + h + search_margin)

    roi_w = max(1, xe - xs)
    roi_h = max(1, ye - ys)

    dx_rel, dy_rel, corr, used = img.match_template_u8(
        frame_u8,
        templ,
        roi=(xs, ys, roi_w, roi_h),
        search_margin=0,
        coarse_cap=600,
    )

    dx = float((xs + dx_rel) - x)
    dy = float((ys + dy_rel) - y)

    frame_out = None
    if int(used) == 1:
        frame_out = img.warp_by_translation_u8(frame_u8, -dx, -dy)

    return {
        "ok": float(corr) >= min_corr,
        "metrics": {
            "corr": float(corr),
            "dx": dx,
            "dy": dy,
            "used": int(used),
            "min_corr": min_corr,
        },
        "frame_out": frame_out,
    }

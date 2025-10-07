from __future__ import annotations

from typing import Dict, Any, List

import numpy as np

from app.models.schema import RecipeDefinition, ToolNode, ToolKind
from app.services.mask_utils import regions_to_masks
from app.services.tools import ssim_tool, templatematch_tool, absdiff_tool

TOOL_EVALUATORS = {
    "SSIM": ssim_tool.evaluate,
    "TemplateMatch": templatematch_tool.evaluate,
    "AbsDiff": absdiff_tool.evaluate,
}


def _ensure_frame(frame: np.ndarray) -> np.ndarray:
    if frame.dtype != np.uint8:
        return frame.astype(np.uint8)
    return frame


def _pose_align_if_needed(recipe: RecipeDefinition, golden: np.ndarray, frame: np.ndarray):
    if not recipe.pose_enabled:
        return frame.copy(), np.eye(2, 3, dtype=np.float32)
    if not recipe.regions:
        return frame.copy(), np.eye(2, 3, dtype=np.float32)
    mask_pose, _, _ = regions_to_masks([r.to_dict() for r in recipe.regions], golden.shape[:2])
    if mask_pose is None:
        mask_pose = np.zeros_like(golden, dtype=np.uint8)
    from app.services.compare_service import _align_by_pose  # lazy import to avoid cycles

    aligned, warp = _align_by_pose(golden, frame, mask_pose)
    return aligned, warp


def run(recipe: RecipeDefinition, golden_u8: np.ndarray, frame_u8: np.ndarray) -> Dict[str, Any]:
    if golden_u8 is None or frame_u8 is None:
        raise ValueError("Pipeline vyžaduje načítaný golden aj aktuálny frame.")

    golden_u8 = _ensure_frame(golden_u8)
    frame_u8 = _ensure_frame(frame_u8)

    current_frame, warp = _pose_align_if_needed(recipe, golden_u8, frame_u8)
    tool_results: List[Dict[str, Any]] = []
    overall_ok = True

    for index, tool in enumerate(recipe.tools):
        if not tool.enabled:
            tool_results.append(
                {
                    "id": tool.id,
                    "name": tool.name,
                    "kind": tool.kind,
                    "ok": True,
                    "enabled": False,
                    "skipped": True,
                    "metrics": {},
                }
            )
            continue

        evaluator = TOOL_EVALUATORS.get(tool.kind)
        if evaluator is None:
            tool_results.append(
                {
                    "id": tool.id,
                    "name": tool.name,
                    "kind": tool.kind,
                    "ok": False,
                    "enabled": True,
                    "skipped": False,
                    "metrics": {"error": f"Neznámy tool {tool.kind}"},
                }
            )
            overall_ok = False
            continue

        result = evaluator(golden_u8, current_frame, tool)
        current_frame = result.get("frame_out") or current_frame
        ok = bool(result.get("ok", False))
        overall_ok = overall_ok and ok
        tool_results.append(
            {
                "id": tool.id,
                "name": tool.name,
                "kind": tool.kind,
                "ok": ok,
                "enabled": True,
                "skipped": False,
                "metrics": result.get("metrics", {}),
            }
        )

    return {
        "ok": overall_ok,
        "tool_results": tool_results,
        "summary": {
            "pose_enabled": bool(recipe.pose_enabled),
            "warp": warp.tolist(),
        },
        "frame_out": current_frame,
    }

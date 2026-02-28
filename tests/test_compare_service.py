import numpy as np

from app.services.compare_service import analyze


def test_analyze_nok_contains_defect_mask_and_contour() -> None:
    golden = np.zeros((64, 64), dtype=np.uint8)
    frame = golden.copy()
    frame[20:34, 22:38] = 255

    regions = [{"kind": "roi", "shape": "rect", "x": 0, "y": 0, "w": 64, "h": 64}]

    result = analyze(
        golden,
        regions,
        frame,
        {
            "ssim_min": 0.999,
            "diff_thresh": 10,
            "min_blob_area": 5,
            "max_total_area": 1,
            "max_blob_count": 100,
        },
        pose_enabled=False,
    )

    assert result["ok"] is False
    display_items = result.get("display_items") or []
    assert any(item.get("kind") == "mask" for item in display_items)
    assert any(item.get("kind") == "contour" for item in display_items)

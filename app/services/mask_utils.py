# app/services/mask_utils.py
import numpy as np
import cv2

def _poly_to_mask(shape, pts):
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(pts, dtype=np.int32)], 255)
    return mask

def regions_to_masks(regions, shape):
    """
    Vytvorí masky (pose, roi, ignore) zo zoznamu regiónov (dicty z regions.json).
    shape: (H, W)
    """
    H, W = shape
    m_pose   = np.zeros((H, W), dtype=np.uint8)
    m_roi    = np.zeros((H, W), dtype=np.uint8)
    m_ignore = np.zeros((H, W), dtype=np.uint8)

    for r in regions:
        t = r["reg_type"]; s = r["shape"]; g = r["geom"]
        if s == "rect":
            x, y, w, h = map(int, g)
            x2, y2 = x + w, y + h
            x = max(0, min(W-1, x)); y = max(0, min(H-1, y))
            x2 = max(0, min(W, x2)); y2 = max(0, min(H, y2))
            mask = np.zeros((H, W), dtype=np.uint8)
            mask[y:y2, x:x2] = 255
        elif s == "circle":
            cx, cy, r = g
            mask = np.zeros((H, W), dtype=np.uint8)
            cv2.circle(mask, (int(round(cx)), int(round(cy))), int(round(r)), 255, -1)
        elif s == "poly":
            mask = _poly_to_mask((H, W), [(int(x), int(y)) for x, y in g])
        else:
            continue

        if t == "pose":
            m_pose = cv2.bitwise_or(m_pose, mask)
        elif t == "roi":
            m_roi = cv2.bitwise_or(m_roi, mask)
        elif t == "ignore":
            m_ignore = cv2.bitwise_or(m_ignore, mask)

    # ROI bez ignore
    m_eff = cv2.bitwise_and(m_roi, cv2.bitwise_not(m_ignore))
    return m_pose, m_eff, m_ignore

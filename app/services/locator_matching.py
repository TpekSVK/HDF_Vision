"""Shape-aware, bounded coarse-to-fine locator matching."""

import cv2
import numpy as np


def match_shape(search, template, template_mask, search_mask, coarse_cap=600):
    """Return full-resolution NCC and position; reject pixels outside search.

    Keep separate spatial candidates so a repeated feature is not confused
    with adjacent samples of the same correlation peak.
    """
    mask = np.asarray(template_mask, dtype=np.uint8)
    support = np.asarray(search_mask, dtype=np.uint8)
    values = template[mask > 0]
    contrast = float(values.std()) if values.size else 0.0
    info = {"template_contrast": contrast, "second_corr": None}
    if values.size < 4 or contrast < 1e-6:
        return 0.0, 0.0, 0.0, 0, info
    sh, sw = search.shape
    th, tw = template.shape
    if sh < th or sw < tw:
        return 0.0, 0.0, 0.0, 0, info

    def response(s, t, m, allowed):
        if np.count_nonzero(m) < 4:
            return np.full((s.shape[0]-t.shape[0]+1, s.shape[1]-t.shape[1]+1), -2.0, dtype=np.float32)
        # OpenCV masked CCOEFF retains brightness-offset invariance.
        if np.all(m):
            result = cv2.matchTemplate(s, t, cv2.TM_CCOEFF_NORMED)
        else:
            result = cv2.matchTemplate(s, t, cv2.TM_CCOEFF_NORMED, mask=m)
        result[~np.isfinite(result)] = -2.0
        # Number of template-support pixels outside the permitted search shape.
        if not np.all(allowed):
            outside = cv2.matchTemplate(
                (allowed == 0).astype(np.float32), m.astype(np.float32), cv2.TM_CCORR
            )
            result[outside > 0.5] = -2.0
        return result

    scale = min(1.0, max(1, coarse_cap) / max(sh, sw))
    # Do not destroy small templates while reducing a large search area.
    scale = min(1.0, max(scale, 8.0 / min(th, tw)))
    if scale < 1.0:
        size = (max(1, round(sw * scale)), max(1, round(sh * scale)))
        tsize = (max(1, round(tw * scale)), max(1, round(th * scale)))
        s = cv2.resize(search, size, interpolation=cv2.INTER_AREA)
        t = cv2.resize(template, tsize, interpolation=cv2.INTER_AREA)
        m = cv2.resize(mask, tsize, interpolation=cv2.INTER_NEAREST)
        allowed = cv2.resize(support, size, interpolation=cv2.INTER_NEAREST)
    else:
        s, t, m, allowed = search, template, mask, support
    scores = response(s, t, m, allowed)
    candidates = []
    for _ in range(3):
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score <= -2.0:
            break
        candidates.append((x, y))
        rx, ry = max(2, t.shape[1] // 2), max(2, t.shape[0] // 2)
        scores[max(0, y-ry):y+ry+1, max(0, x-rx):x+rx+1] = -2.0

    refined = []
    for x, y in candidates:
        px, py = min(sw-tw, round(x / scale)), min(sh-th, round(y / scale))
        pad = max(3, int(np.ceil(2 / scale)))
        x0, y0 = max(0, px-pad), max(0, py-pad)
        x1, y1 = min(sw, px+tw+pad), min(sh, py+th+pad)
        scores = response(search[y0:y1, x0:x1], template, mask, support[y0:y1, x0:x1])
        _, score, _, (fx, fy) = cv2.minMaxLoc(scores)
        if score > -2.0:
            refined.append((float(score), x0+fx, y0+fy))
    if not refined:
        return 0.0, 0.0, 0.0, 0, info
    refined.sort(reverse=True)
    best, x, y = refined[0]
    others = [score for score, ox, oy in refined[1:]
              if abs(ox-x) > tw/2 or abs(oy-y) > th/2]
    info["second_corr"] = max(others) if others else None
    return float(x), float(y), best, 1, info

# app/services/compare_service.py
import numpy as np
import cv2
from typing import Dict, Any, Tuple
from app.services.mask_utils import regions_to_masks

# ---------- Pomocné okná/filtre ----------
def _hanning_window(shape):
    hy = cv2.createHanningWindow((shape[1], 1), cv2.CV_32F)
    hx = cv2.createHanningWindow((1, shape[0]), cv2.CV_32F)
    return (hx @ hy).astype(np.float32)

def estimate_translation_phasecorr(img_u8: np.ndarray, ref_u8: np.ndarray) -> tuple[float,float,float]:
    assert img_u8.shape == ref_u8.shape
    a = img_u8.astype(np.float32)
    b = ref_u8.astype(np.float32)
    win = _hanning_window(a.shape)
    a = a * win
    b = b * win
    (shift, response) = cv2.phaseCorrelate(a, b)
    dx = float(shift[0])
    dy = float(shift[1])
    return dx, dy, float(response)

def warp_by_translation(frame_u8: np.ndarray, dx: float, dy: float) -> np.ndarray:
    M = np.array([[1, 0, dx],
                  [0, 1, dy]], dtype=np.float32)
    h, w = frame_u8.shape[:2]
    return cv2.warpAffine(frame_u8, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)

def _gauss_soft(img_u8: np.ndarray, sigma: float = 0.8) -> np.ndarray:
    k = max(3, int(round(sigma*6))|1)
    return cv2.GaussianBlur(img_u8, (k, k), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REFLECT101)

def _ssim(img1_u8: np.ndarray, img2_u8: np.ndarray, mask_u8: np.ndarray | None = None) -> float:
    x = img1_u8.astype(np.float32)
    y = img2_u8.astype(np.float32)

    if mask_u8 is not None:
        m = mask_u8 > 0
        if not np.any(m):
            return 1.0
        x = x[m]; y = y[m]
    if x.size == 0 or y.size == 0:
        return 1.0

    ux, uy = float(np.mean(x)), float(np.mean(y))
    vx, vy = float(np.var(x)), float(np.var(y))
    cxy = float(np.mean((x - ux) * (y - uy)))

    L = 255.0
    C1 = (0.01 * L) ** 2
    C2 = (0.03 * L) ** 2

    num = (2 * ux * uy + C1) * (2 * cxy + C2)
    den = (ux * ux + uy * uy + C1) * (vx + vy + C2)
    if den <= 0:
        return 1.0 if np.allclose(x, y) else 0.0
    return float(num / den)

# ---------- Globálne zarovnanie (kamera/fixture) ----------
def _prep_for_ecc(img_u8: np.ndarray) -> np.ndarray:
    f = img_u8.astype(np.float32)
    if f.max() > 0:
        f = f * (255.0 / f.max())
    f = cv2.GaussianBlur(f, (5,5), 1.2)
    return f

def _ecc_multiscale(golden_u8: np.ndarray, frame_u8: np.ndarray, mask_pose: np.ndarray | None):
    mp = None
    if mask_pose is not None and mask_pose.any():
        mp = cv2.erode(mask_pose, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,7)), iterations=1)

    g0 = golden_u8; i0 = frame_u8
    gF = _prep_for_ecc(g0); iF = _prep_for_ecc(i0)
    mF = (mp > 0).astype(np.uint8) if mp is not None else None

    def pyr(x):
        x2 = cv2.pyrDown(x) if min(x.shape[:2]) >= 4 else x
        x4 = cv2.pyrDown(x2) if min(x2.shape[:2]) >= 4 else x2
        return x4, x2, x

    g4, g2, g1 = pyr(gF)
    i4, i2, i1 = pyr(iF)
    m4 = cv2.pyrDown(mF) if mF is not None and min(mF.shape[:2]) >= 4 else mF
    m2 = cv2.pyrDown(m4) if m4 is not None and min(m4.shape[:2]) >= 4 else (cv2.pyrDown(mF) if mF is not None else None)
    m1 = mF

    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-7)
    mode = cv2.MOTION_EUCLIDEAN

    def ecc_step(g, i, m, w):
        wloc = w.copy()
        try:
            _ = cv2.findTransformECC(g, i, wloc, mode, crit, inputMask=m, gaussFiltSize=5)
            wloc[:,2] *= 2.0  # upscaling posunu pre ďalšiu úroveň
            return wloc, True
        except cv2.error:
            return w, False

    w4, ok4 = ecc_step(g4, i4, m4, warp)
    w2, ok2 = ecc_step(g2, i2, m2, w4)
    w1, ok1 = ecc_step(g1, i1, m1, w2)

    if not (ok4 or ok2 or ok1):
        try:
            ref = gF if mF is None else cv2.bitwise_and(gF, gF, mask=mF)
            cur = iF if mF is None else cv2.bitwise_and(iF, iF, mask=mF)
            (dx, dy), _ = cv2.phaseCorrelate(ref, cur)
            wfb = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
            aligned = cv2.warpAffine(frame_u8, wfb, (frame_u8.shape[1], frame_u8.shape[0]),
                                     flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
            return aligned, wfb
        except Exception:
            return frame_u8, np.eye(2,3, dtype=np.float32)

    warp_final = w1
    aligned = cv2.warpAffine(frame_u8, warp_final, (frame_u8.shape[1], frame_u8.shape[0]),
                             flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
    return aligned, warp_final

def _align_by_pose(golden: np.ndarray, img: np.ndarray, mask_pose: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    return _ecc_multiscale(golden, img, mask_pose)

# ---------- NOVÉ: Lokálne „dosadenie“ objektu v ROI (template matching) ----------
def _roi_bbox(mask_u8: np.ndarray) -> Tuple[int,int,int,int] | None:
    ys, xs = np.where(mask_u8 > 0)
    if xs.size == 0 or ys.size == 0:
        return None
    x, y, w, h = int(xs.min()), int(ys.min()), int(xs.max()-xs.min()+1), int(ys.max()-ys.min()+1)
    return x, y, w, h

def _template_align_in_roi(golden_u8: np.ndarray,
                           frame_u8: np.ndarray,
                           mask_roi: np.ndarray,
                           search_margin: int = 20) -> Tuple[np.ndarray, Dict[str, float]]:
    """
    V ROI vyreže template z golden-u a hľadá ho vo frame v okne ±margin.
    Vráti zarovnaný frame (translačne) a metriky: dx, dy, corr.
    """
    bbox = _roi_bbox(mask_roi)
    if bbox is None:
        return frame_u8, {"tm_dx": 0.0, "tm_dy": 0.0, "tm_corr": 1.0, "tm_used": 0}

    x, y, w, h = bbox
    # template (golden ROI, len pixle v maske – nepovinné; pre jednoduchosť berieme plný rect)
    templ = golden_u8[y:y+h, x:x+w]

    # vyhľadávacie okno vo frame
    H, W = frame_u8.shape[:2]
    xs = max(0, x - search_margin)
    ys = max(0, y - search_margin)
    xe = min(W, x + w + search_margin)
    ye = min(H, y + h + search_margin)
    search = frame_u8[ys:ye, xs:xe]
    if search.shape[0] < h or search.shape[1] < w:
        # okno je menšie než template – nedá sa hľadať
        return frame_u8, {"tm_dx": 0.0, "tm_dy": 0.0, "tm_corr": 0.0, "tm_used": 0}

    # match
    res = cv2.matchTemplate(search, templ, cv2.TM_CCOEFF_NORMED)
    minVal, maxVal, minLoc, maxLoc = cv2.minMaxLoc(res)
    # pozícia nájdenia v rámci search
    best_x, best_y = maxLoc
    # posun frame ROI voči golden ROI (kladný znamená: obsah vo frame je posunutý doprava/dole)
    dx = float((xs + best_x) - x)
    dy = float((ys + best_y) - y)

    # Na dorovnanie posunieme celý frame o (-dx, -dy)
    aligned = warp_by_translation(frame_u8, -dx, -dy)

    return aligned, {"tm_dx": dx, "tm_dy": dy, "tm_corr": float(maxVal), "tm_used": 1}

# ---------- Hlavná analýza ----------
def analyze(golden: np.ndarray,
            regions: list[dict],
            frame: np.ndarray,
            thresholds: Dict[str, float] | None = None) -> Dict[str, Any]:

    th = dict(
        ssim_min=0.88,
        diff_thresh=22,
        min_blob_area=50,
        max_total_area=2000,
        max_blob_count=10,
        # Template matching tuning:
        tm_enable=1,          # 1=zap., 0=vyp.
        tm_margin=200,         # px okolo ROI
        tm_min_corr=0.55      # ak korelácia veľmi nízka, efekt bude slabý
    )
    if thresholds:
        th.update(thresholds)

    H, W = golden.shape[:2]
    mask_pose, mask_roi_eff, _mask_ignore = regions_to_masks(regions, (H, W))

    # 1) Globálne zarovnanie podľa „pose“
    frame_aligned, warp = _align_by_pose(golden, frame, mask_pose)

    # 2) Lokálne dosadenie objektu v ROI (template matching)
    tm_info = {"tm_dx": 0.0, "tm_dy": 0.0, "tm_corr": 0.0, "tm_used": 0}
    if th["tm_enable"]:
        frame_aligned, tm_info = _template_align_in_roi(
            golden, frame_aligned, mask_roi_eff, search_margin=int(th["tm_margin"])
        )

    # 3) SSIM v ROI
    ssim_val = _ssim(golden, frame_aligned, mask_roi_eff)

    # 4) „Mäkší“ diff: blur → absdiff → threshold → morfológia
    g_blur = cv2.GaussianBlur(golden, (3,3), 0.8)
    f_blur = cv2.GaussianBlur(frame_aligned, (3,3), 0.8)
    diff = cv2.absdiff(g_blur, f_blur)

    if th["diff_thresh"] > 0:
        _, binm = cv2.threshold(diff, th["diff_thresh"], 255, cv2.THRESH_BINARY)
    else:
        _, binm = cv2.threshold(cv2.bitwise_and(diff, mask_roi_eff), 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    binm = cv2.bitwise_and(binm, mask_roi_eff)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
    binm = cv2.morphologyEx(binm, cv2.MORPH_OPEN, kernel, iterations=1)
    binm = cv2.dilate(binm, kernel, iterations=1)

    cnts, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    areas = [cv2.contourArea(c) for c in cnts if cv2.contourArea(c) >= th["min_blob_area"]]
    total_area = float(np.sum(areas))
    blob_count = int(len(areas))

    nok = (ssim_val < th["ssim_min"]) or (total_area > th["max_total_area"]) or (blob_count > th["max_blob_count"])

    metrics = {
        "ssim": round(float(ssim_val), 5),
        "blob_count": blob_count,
        "total_area": int(total_area),
        "warp": warp.tolist(),
        "diff_thresh": th["diff_thresh"],
        "min_blob_area": th["min_blob_area"],
        # template matching diagnostika:
        "tm_used": int(tm_info.get("tm_used", 0)),
        "tm_dx": round(float(tm_info.get("tm_dx", 0.0)), 3),
        "tm_dy": round(float(tm_info.get("tm_dy", 0.0)), 3),
        "tm_corr": round(float(tm_info.get("tm_corr", 0.0)), 4),
    }

    return {"ok": not nok, "metrics": metrics}

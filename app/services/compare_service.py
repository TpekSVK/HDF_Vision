# app/services/compare_service.py
import numpy as np
import cv2
from typing import Dict, Any, Tuple
from app.services.mask_utils import regions_to_masks


# ----------------------------
# Pomocné funkcie na zarovnanie
# ----------------------------
def _hanning_window(shape):
    """2D Hanning pre fázovú koreláciu (zvyšuje stabilitu pri okrajoch)."""
    hy = cv2.createHanningWindow((shape[1], 1), cv2.CV_32F)
    hx = cv2.createHanningWindow((1, shape[0]), cv2.CV_32F)
    return (hx @ hy).astype(np.float32)


def estimate_translation_phasecorr(img_u8: np.ndarray, ref_u8: np.ndarray) -> tuple[float, float, float]:
    """
    Sub-pixel posun (dx, dy) medzi img a ref (oba GRAY8, rovnaký rozmer).
    Vracia: dx, dy, response (kvalita 0..1).
    """
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
    """Posunie obraz o (dx, dy). Kladné dx doprava, dy nadol. Border=reflect."""
    M = np.array([[1, 0, dx],
                  [0, 1, dy]], dtype=np.float32)
    h, w = frame_u8.shape[:2]
    return cv2.warpAffine(frame_u8, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)


def _gauss_soft(img_u8: np.ndarray, sigma: float = 0.8) -> np.ndarray:
    """Jemné vyhladenie pred SSIM (stabilizácia). Sigma ~0.6–1.0."""
    k = max(3, int(round(sigma * 6)) | 1)  # nepárne
    return cv2.GaussianBlur(img_u8, (k, k), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REFLECT101)


# -------------
# SSIM (maskované)
# -------------
def _ssim(img1_u8: np.ndarray, img2_u8: np.ndarray, mask_u8: np.ndarray | None = None) -> float:
    """
    SSIM nad 8-bit šedotónmi (0..255). Ak je maska, počítame len nad maskovanou oblasťou (>0).
    Bez externých knižníc (skimage).
    """
    x = img1_u8.astype(np.float32)
    y = img2_u8.astype(np.float32)

    if mask_u8 is not None:
        m = mask_u8 > 0
        if not np.any(m):
            return 1.0
        x = x[m]
        y = y[m]

    if x.size == 0 or y.size == 0:
        return 1.0

    ux = float(np.mean(x))
    uy = float(np.mean(y))
    vx = float(np.var(x))
    vy = float(np.var(y))
    cxy = float(np.mean((x - ux) * (y - uy)))

    L = 255.0
    C1 = (0.01 * L) ** 2
    C2 = (0.03 * L) ** 2

    num = (2 * ux * uy + C1) * (2 * cxy + C2)
    den = (ux * ux + uy * uy + C1) * (vx + vy + C2)
    if den <= 0:
        return 1.0 if np.allclose(x, y) else 0.0
    return float(num / den)


# --------------------------
# Zarovnanie (ECC + fallback)
# --------------------------
def _prep_for_ecc(img_u8: np.ndarray) -> np.ndarray:
    # normalizácia + jemné rozmazanie pre stabilnejší ECC
    f = img_u8.astype(np.float32)
    if f.max() > 0:
        f = f * (255.0 / f.max())
    f = cv2.GaussianBlur(f, (5, 5), 1.2)
    return f


def _ecc_multiscale(golden_u8: np.ndarray, frame_u8: np.ndarray, mask_pose: np.ndarray | None):
    """Pyramídové ECC (1/4 -> 1/2 -> 1×) + fallback na phaseCorrelate (čistý posun).
       Vráti (aligned_u8, 2x3_warp)."""
    mp = None
    if mask_pose is not None and mask_pose.any():
        mp = cv2.erode(mask_pose, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=1)

    g0 = golden_u8
    i0 = frame_u8

    gF = _prep_for_ecc(g0)
    iF = _prep_for_ecc(i0)
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
    mode = cv2.MOTION_EUCLIDEAN  # translácia + rotácia (bez scale)

    def ecc_step(g, i, m, warp_init):
        w = warp_init.copy()
        try:
            _ = cv2.findTransformECC(g, i, w, mode, crit, inputMask=m, gaussFiltSize=5)
            return w, True
        except cv2.error:
            return warp_init, False

    # 1/4
    w4, ok4 = ecc_step(g4, i4, m4, warp)
    # pri prechode na vyššiu úroveň posun zväčšíme 2× (preklad)
    w4_up = w4.copy(); w4_up[:, 2] *= 2.0

    # 1/2
    w2, ok2 = ecc_step(g2, i2, m2, w4_up)
    w2_up = w2.copy(); w2_up[:, 2] *= 2.0

    # 1×
    w1, ok1 = ecc_step(g1, i1, m1, w2_up)

    if not (ok4 or ok2 or ok1):
        # Fallback: phase correlation (čistý posun v pose maske alebo celej ploche)
        try:
            ref = gF if mF is None else cv2.bitwise_and(gF, gF, mask=mF)
            cur = iF if mF is None else cv2.bitwise_and(iF, iF, mask=mF)
            (dx, dy), _ = cv2.phaseCorrelate(ref, cur)
            wfb = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
            aligned = cv2.warpAffine(frame_u8, wfb, (frame_u8.shape[1], frame_u8.shape[0]),
                                     flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
            return aligned, wfb
        except Exception:
            return frame_u8, np.eye(2, 3, dtype=np.float32)

    warp_final = w1
    aligned = cv2.warpAffine(frame_u8, warp_final, (frame_u8.shape[1], frame_u8.shape[0]),
                             flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
    return aligned, warp_final


def _align_by_pose(golden: np.ndarray, img: np.ndarray, mask_pose: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    # nahradené multi-scale verziou
    return _ecc_multiscale(golden, img, mask_pose)


# -------------
# Hlavná analýza
# -------------
def analyze(golden: np.ndarray,
            regions: list[dict],
            frame: np.ndarray,
            thresholds: Dict[str, float] | None = None) -> Dict[str, Any]:
    """
    golden, frame ........ GRAY8 (H, W)
    regions .............. zoznam regiónov z regions.json (pose/roi/ignore)
    thresholds ........... prahy (ssim_min, diff_thresh, min_blob_area, max_total_area, max_blob_count)
    """
    th = dict(
        ssim_min=0.88,
        diff_thresh=22,
        min_blob_area=50,
        max_total_area=2000,
        max_blob_count=10,
        # zarovnanie – môžeš UI doplniť neskôr
        ssim_sigma=0.8
    )
    if thresholds:
        th.update(thresholds)

    H, W = golden.shape[:2]
    mask_pose, mask_roi_eff, _mask_ignore = regions_to_masks(regions, (H, W))

    # 1) Zarovnanie (robustné)
    frame_aligned, warp = _align_by_pose(golden, frame, mask_pose)

    # 2) Jemné vyhladenie pred SSIM (stabilizácia)
    if th.get("ssim_sigma", 0) > 0:
        g_eval = _gauss_soft(golden, th["ssim_sigma"])
        f_eval = _gauss_soft(frame_aligned, th["ssim_sigma"])
    else:
        g_eval = golden
        f_eval = frame_aligned

    # 3) SSIM v ROI
    ssim_val = _ssim(g_eval, f_eval, mask_roi_eff)

    # 4) „Mäkší“ diff v ROI: blur → absdiff → threshold → morfológia
    g_blur = cv2.GaussianBlur(golden, (3, 3), 0.8)
    f_blur = cv2.GaussianBlur(frame_aligned, (3, 3), 0.8)
    diff = cv2.absdiff(g_blur, f_blur)

    if th["diff_thresh"] > 0:
        _, binm = cv2.threshold(diff, th["diff_thresh"], 255, cv2.THRESH_BINARY)
    else:
        _, binm = cv2.threshold(cv2.bitwise_and(diff, mask_roi_eff), 0, 255,
                                cv2.THRESH_BINARY + cv2.OTSU)

    binm = cv2.bitwise_and(binm, mask_roi_eff)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binm = cv2.morphologyEx(binm, cv2.MORPH_OPEN, kernel, iterations=1)
    binm = cv2.dilate(binm, kernel, iterations=1)

    cnts, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    areas = [cv2.contourArea(c) for c in cnts if cv2.contourArea(c) >= th["min_blob_area"]]
    total_area = float(np.sum(areas))
    blob_count = int(len(areas))

    nok = (ssim_val < th["ssim_min"]) or (total_area > th["max_total_area"]) or (blob_count > th["max_blob_count"])

    return {
        "ok": not nok,
        "metrics": {
            "ssim": round(float(ssim_val), 5),
            "blob_count": blob_count,
            "total_area": int(total_area),
            "warp": warp.tolist(),
            "diff_thresh": th["diff_thresh"],
            "min_blob_area": th["min_blob_area"],
        }
    }

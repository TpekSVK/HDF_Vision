# app/services/compare_service.py
import numpy as np
import cv2
from typing import Dict, Any, Tuple
from app.services.mask_utils import regions_to_masks

def _prep_for_ecc(img_u8: np.ndarray) -> np.ndarray:
    # normalizácia + jemné rozmazanie pre stabilnejší ECC
    f = img_u8.astype(np.float32)
    if f.max() > 0:
        f = f * (255.0 / f.max())
    f = cv2.GaussianBlur(f, (5,5), 1.2)
    return f

def _ecc_multiscale(golden_u8: np.ndarray, frame_u8: np.ndarray, mask_pose: np.ndarray | None):
    """Pyramídové ECC (1/4 -> 1/2 -> 1×) + fallback na phaseCorrelate (posun).
       Vráti (aligned_u8, 2x3_warp)."""
    # bezpečnostná erózia masky, aby sa ECC nechytal okrajov
    mp = None
    if mask_pose is not None and mask_pose.any():
        mp = cv2.erode(mask_pose, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,7)), iterations=1)

    g0 = golden_u8
    i0 = frame_u8

    # priprav float verzie pre ECC
    gF = _prep_for_ecc(g0)
    iF = _prep_for_ecc(i0)
    if mp is not None:
        mF = (mp > 0).astype(np.uint8)
    else:
        mF = None

    # pyramídy
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

    def ecc_step(g, i, m, warp_init, scale):
        w = warp_init.copy()
        try:
            _ = cv2.findTransformECC(g, i, w, mode, crit, inputMask=m, gaussFiltSize=5)
            # upscalni pre ďalšiu úroveň
            w[:,2] *= 2.0  # posun je v pixloch; pri prechode z 1/4->1/2->1× sa násobí 2
            return w, True
        except cv2.error:
            return warp_init, False

    # úroveň 1/4
    w4, ok4 = ecc_step(g4, i4, m4, warp, 0.25)
    # úroveň 1/2
    w2, ok2 = ecc_step(g2, i2, m2, w4, 0.5)
    # úroveň 1×
    w1, ok1 = ecc_step(g1, i1, m1, w2, 1.0)

    if not (ok4 or ok2 or ok1):
        # Fallback: phase correlation (čistý posun v pose maske)
        try:
            ref = gF if mF is None else cv2.bitwise_and(gF, gF, mask=mF)
            cur = iF if mF is None else cv2.bitwise_and(iF, iF, mask=mF)
            (dx, dy), _ = cv2.phaseCorrelate(ref, cur)
            wfb = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
            aligned = cv2.warpAffine(frame_u8, wfb, (frame_u8.shape[1], frame_u8.shape[0]),
                                     flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
            return aligned, wfb
        except Exception:
            # v najhoršom vráť pôvodný
            return frame_u8, np.eye(2,3, dtype=np.float32)

    warp_final = w1
    aligned = cv2.warpAffine(frame_u8, warp_final, (frame_u8.shape[1], frame_u8.shape[0]),
                             flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
    return aligned, warp_final

def _align_by_pose(golden: np.ndarray, img: np.ndarray, mask_pose: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    # nahradené multi-scale verziou
    return _ecc_multiscale(golden, img, mask_pose)

def analyze(golden: np.ndarray,
            regions: list[dict],
            frame: np.ndarray,
            thresholds: Dict[str, float] | None = None) -> Dict[str, Any]:
    th = dict(ssim_min=0.88, diff_thresh=22, min_blob_area=50, max_total_area=2000, max_blob_count=10)
    if thresholds:
        th.update(thresholds)

    H, W = golden.shape[:2]
    mask_pose, mask_roi_eff, _mask_ignore = regions_to_masks(regions, (H, W))

    # 1) Zarovnanie (robustné)
    frame_aligned, warp = _align_by_pose(golden, frame, mask_pose)

    # 2) SSIM v ROI
    ssim_val = _ssim(golden, frame_aligned, mask_roi_eff)

    # 3) „Mäkší“ diff: jemne blur → absdiff → threshold → morfológia
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

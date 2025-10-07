# app/services/compare_service.py
from __future__ import annotations
from typing import Dict, Any, Tuple, Optional

import numpy as np
import cv2
import time
from app.services.mask_utils import regions_to_masks
from app.utils import imaging as img


# ---------- Pomocné okná/filtre (CPU) ----------
def _hanning_window(shape: tuple[int, int]) -> np.ndarray:
    # shape = (H, W)
    return cv2.createHanningWindow((shape[1], shape[0]), cv2.CV_32F)

def estimate_translation_phasecorr(img_u8: np.ndarray, ref_u8: np.ndarray) -> tuple[float, float, float]:
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

def _gauss_soft(img_u8_src: np.ndarray, sigma: float = 0.8) -> np.ndarray:
    return img.blur_gaussian_u8(img_u8_src, sigma=sigma, kmin=3)


# ---------- Globálne zarovnanie (kamera/fixture) – ECC s maskou + fallback ----------
def _prep_for_ecc(img_u8: np.ndarray) -> np.ndarray:
    f = img_u8.astype(np.float32)
    m = float(f.max())
    if m > 0:
        f = f * (255.0 / m)
    f = cv2.GaussianBlur(f, (5, 5), 1.2)
    return f

def _ecc_multiscale(golden_u8: np.ndarray, frame_u8: np.ndarray, mask_pose: Optional[np.ndarray]):
    mp = None
    if mask_pose is not None and mask_pose.any():
        mp = cv2.erode(mask_pose, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=1)

    gF = _prep_for_ecc(golden_u8)
    iF = _prep_for_ecc(frame_u8)
    mF = (mp > 0).astype(np.uint8) if mp is not None else None

    # Ak ECC v build-e nie je, urob okamžitý fallback na phase correlation
    has_ecc = hasattr(cv2, "findTransformECC")
    if not has_ecc:
        try:
            ref = gF if mF is None else cv2.bitwise_and(gF, gF, mask=mF)
            cur = iF if mF is None else cv2.bitwise_and(iF, iF, mask=mF)
            (dx, dy), _ = cv2.phaseCorrelate(ref, cur)
            wfb = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
            aligned = cv2.warpAffine(
                frame_u8, wfb, (frame_u8.shape[1], frame_u8.shape[0]),
                flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP
            )
            return aligned, wfb
        except Exception:
            return frame_u8, np.eye(2, 3, dtype=np.float32)

    def pyr(x: np.ndarray):
        x2 = cv2.pyrDown(x) if min(x.shape[:2]) >= 4 else x
        x4 = cv2.pyrDown(x2) if min(x2.shape[:2]) >= 4 else x2
        return x4, x2, x

    g4, g2, g1 = pyr(gF)
    i4, i2, i1 = pyr(iF)
    m4 = cv2.pyrDown(mF) if mF is not None and min(mF.shape[:2]) >= 4 else mF
    m2 = cv2.pyrDown(m4) if m4 is not None and min(m4.shape[:2]) >= 4 else (cv2.pyrDown(mF) if mF is not None else None)
    m1 = mF

    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, int(th.get("ecc_iters", 80)), float(th.get("ecc_eps", 1e-6)))
    mode = getattr(cv2, "MOTION_EUCLIDEAN", 1)

def _ecc_multiscale_cfg(golden_u8: np.ndarray, frame_u8: np.ndarray, mask_pose: Optional[np.ndarray],
                        ecc_iters: int = 80, ecc_eps: float = 1e-6):
    mp = None
    if mask_pose is not None and mask_pose.any():
        mp = cv2.erode(mask_pose, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=1)

    gF = _prep_for_ecc(golden_u8)
    iF = _prep_for_ecc(frame_u8)
    mF = (mp > 0).astype(np.uint8) if mp is not None else None

    # fallback ak chýba ECC
    if not hasattr(cv2, "findTransformECC"):
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

    def pyr(x: np.ndarray):
        x2 = cv2.pyrDown(x) if min(x.shape[:2]) >= 4 else x
        x4 = cv2.pyrDown(x2) if min(x2.shape[:2]) >= 4 else x2
        return x4, x2, x

    g4, g2, g1 = pyr(gF)
    i4, i2, i1 = pyr(iF)
    m4 = cv2.pyrDown(mF) if mF is not None and min(mF.shape[:2]) >= 4 else mF
    m2 = cv2.pyrDown(m4) if m4 is not None and min(m4.shape[:2]) >= 4 else (cv2.pyrDown(mF) if mF is not None else None)
    m1 = mF

    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, int(ecc_iters), float(ecc_eps))
    mode = getattr(cv2, "MOTION_EUCLIDEAN", 1)

    def ecc_step(g, i, m, w):
        wloc = w.copy()
        try:
            _ = cv2.findTransformECC(g, i, wloc, mode, crit, inputMask=m, gaussFiltSize=5)
            wloc[:, 2] *= 2.0
            return wloc, True
        except (cv2.error, AttributeError):
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
            return frame_u8, np.eye(2, 3, dtype=np.float32)

    warp_final = w1
    aligned = cv2.warpAffine(frame_u8, warp_final, (frame_u8.shape[1], frame_u8.shape[0]),
                            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
    return aligned, warp_final

def _align_by_pose(golden: np.ndarray, img_src: np.ndarray, mask_pose: Optional[np.ndarray],
                   mode: str = "phase",
                   ecc_iters: int = 80,
                   ecc_eps: float = 1e-6) -> Tuple[np.ndarray, np.ndarray]:
    if mode == "phase":
        # veľmi rýchle zarovnanie len transláciou
        gF = _prep_for_ecc(golden)
        iF = _prep_for_ecc(img_src)
        mF = (mask_pose > 0).astype(np.uint8) if (mask_pose is not None and mask_pose.any()) else None
        try:
            ref = gF if mF is None else cv2.bitwise_and(gF, gF, mask=mF)
            cur = iF if mF is None else cv2.bitwise_and(iF, iF, mask=mF)
            (dx, dy), _ = cv2.phaseCorrelate(ref, cur)
            w = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
            aligned = cv2.warpAffine(img_src, w, (img_src.shape[1], img_src.shape[0]),
                                     flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
            return aligned, w
        except Exception:
            return img_src, np.eye(2, 3, dtype=np.float32)
    else:
        # ECC s miernejšími kritériami
        return _ecc_multiscale_cfg(golden, img_src, mask_pose, ecc_iters=ecc_iters, ecc_eps=ecc_eps)



# ---------- Lokálne „dosadenie“ objektu v ROI (template matching) ----------
def _roi_bbox(mask_u8: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    ys, xs = np.where(mask_u8 > 0)
    if xs.size == 0 or ys.size == 0:
        return None
    x, y, w, h = int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    return x, y, w, h

def _template_align_in_roi(
    golden_u8: np.ndarray,
    frame_u8: np.ndarray,
    mask_roi: np.ndarray,
    search_margin: int = 20,
) -> tuple[np.ndarray, dict]:
    """
    Coarse→fine template matching v ROI (GPU ak je, inak CPU) s následným posunom.
    Vracia zarovnaný frame a diagnostiku.
    """
    bbox = _roi_bbox(mask_roi)
    if bbox is None:
        return frame_u8, {"tm_dx": 0.0, "tm_dy": 0.0, "tm_corr": 1.0, "tm_used": 0}

    x, y, w, h = bbox
    templ = golden_u8[y : y + h, x : x + w]

    # vyhľadávacie okno (ROI zväčšená o margin)
    H, W = frame_u8.shape[:2]
    xs = max(0, x - search_margin)
    ys = max(0, y - search_margin)
    xe = min(W, x + w + search_margin)
    ye = min(H, y + h + search_margin)

    # spoločná utilita – GPU/CPU podľa dostupnosti
    dx_rel, dy_rel, corr, used = img.match_template_u8(
        frame_u8,
        templ,
        roi=(xs, ys, xe - xs, ye - ys),
        search_margin=0,   # search už je zúžený ROI vyššie
        coarse_cap=600,
    )

    # relatívne → globálne
    dx = float((xs + dx_rel) - x)
    dy = float((ys + dy_rel) - y)

    aligned = img.warp_by_translation_u8(frame_u8, -dx, -dy)
    return aligned, {"tm_dx": dx, "tm_dy": dy, "tm_corr": float(corr), "tm_used": int(used)}


# ---------- Hlavná analýza ----------
def analyze(
    golden: np.ndarray,
    regions: list[dict],
    frame: np.ndarray,
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:

    th = dict(
        ssim_min=0.88,
        diff_thresh=22,
        min_blob_area=50,
        max_total_area=2000,
        max_blob_count=10,
        # Template matching:
        tm_enable=1,
        tm_margin=200,
        tm_min_corr=0.55,
        # NEW: režim zarovnania "pose"
        pose_mode="phase",    # "phase" | "ecc"
        ecc_iters=80,         # menej ako 200 -> rýchlejšie
        ecc_eps=1e-6
    )
    if thresholds:
        th.update(thresholds)

    H, W = golden.shape[:2]
    mask_pose, mask_roi_eff, _mask_ignore = regions_to_masks(regions, (H, W))

    # 1) Globálne zarovnanie podľa „pose“
    t0 = time.perf_counter()
    frame_aligned, warp = _align_by_pose(
        golden, frame, mask_pose,
        mode=str(th.get("pose_mode", "phase")),
        ecc_iters=int(th.get("ecc_iters", 80)),
        ecc_eps=float(th.get("ecc_eps", 1e-6)),
    )
    t_pose = time.perf_counter() - t0

    t1 = time.perf_counter()
    tm_info = {"tm_dx": 0.0, "tm_dy": 0.0, "tm_corr": 0.0, "tm_used": 0}
    if th["tm_enable"]:
        frame_aligned, tm_info = _template_align_in_roi(
            golden, frame_aligned, mask_roi_eff, search_margin=int(th["tm_margin"])
        )
    t_tm = time.perf_counter() - t1

    t2 = time.perf_counter()
    ssim_val = img.ssim_u8(golden, frame_aligned, mask_roi_eff)
    t_ssim = time.perf_counter() - t2

    t3 = time.perf_counter()
    g_blur = img.blur_gaussian_u8(golden, sigma=0.8)
    f_blur = img.blur_gaussian_u8(frame_aligned, sigma=0.8)
    diff = img.absdiff_u8(g_blur, f_blur)
    if th["diff_thresh"] > 0:
        binm = img.threshold_bin_u8(diff, th["diff_thresh"], 255, cv2.THRESH_BINARY)
    else:
        diff_roi = cv2.bitwise_and(diff, mask_roi_eff)
        binm = img.threshold_bin_u8(diff_roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binm = cv2.bitwise_and(binm, mask_roi_eff)
    binm = img.morphology_open_then_dilate_u8(binm, k_open=3, k_dil=3)
    t_diff = time.perf_counter() - t3


    # 5) metriky z kontúr
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
        # GPU info (iba informačne)
        "timing_ms": {
            "pose": int(t_pose*1000),
            "tm": int(t_tm*1000),
            "ssim": int(t_ssim*1000),
            "diff": int(t_diff*1000),
        },
        "gpu": 1 if img.USE_CUDA else 0,
        "pose_mode": str(th.get("pose_mode", "phase")),
    }

    return {"ok": not nok, "metrics": metrics}

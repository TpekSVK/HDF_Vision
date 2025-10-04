# app/services/compare_service.py
import numpy as np
import cv2
from typing import Dict, Any, Tuple

from app.services.mask_utils import regions_to_masks

def _ssim(img1: np.ndarray, img2: np.ndarray, mask: np.ndarray | None = None) -> float:
    """
    Jednoduchá SSIM implementácia (grayscale, 8-bit).
    """
    img1 = img1.astype(np.float32)
    img2 = img2.astype(np.float32)

    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2

    ksize = (7, 7)
    sigma = 1.5
    mu1 = cv2.GaussianBlur(img1, ksize, sigma)
    mu2 = cv2.GaussianBlur(img2, ksize, sigma)

    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2

    sigma1_sq = cv2.GaussianBlur(img1 * img1, ksize, sigma) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(img2 * img2, ksize, sigma) - mu2_sq
    sigma12   = cv2.GaussianBlur(img1 * img2, ksize, sigma) - mu1_mu2

    num = (2 * mu1_mu2 + C1) * (2 * sigma12 + C2)
    den = (mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2)
    ssim_map = num / (den + 1e-12)

    if mask is not None:
        m = (mask > 0).astype(np.float32)
        # vyhneme sa nulovému deleniu
        s = m.sum()
        if s < 1:
            return 1.0
        return float((ssim_map * m).sum() / s)
    else:
        return float(ssim_map.mean())

def _align_by_pose(golden: np.ndarray, img: np.ndarray, mask_pose: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Zarovnanie podľa 'pose' masky: ECC (MOTION_EUCLIDEAN – posun+rotácia).
    Vráti (img_warped, warp_mat).
    """
    # použijeme len oblasť pose
    g = golden.copy()
    i = img.copy()
    if mask_pose is not None and mask_pose.any():
        g = cv2.bitwise_and(g, mask_pose)
        i = cv2.bitwise_and(i, mask_pose)

    warp_mode = cv2.MOTION_EUCLIDEAN
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6)
    try:
        _ = cv2.findTransformECC(g.astype(np.float32), i.astype(np.float32), warp, warp_mode, criteria, inputMask=mask_pose)
        aligned = cv2.warpAffine(img, warp, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
        return aligned, warp
    except cv2.error:
        # ak zlyhá, vráť pôvodný
        return img, warp

def analyze(golden: np.ndarray,
            regions: list[dict],
            frame: np.ndarray,
            thresholds: Dict[str, float] | None = None) -> Dict[str, Any]:
    """
    Hlavná analýza: zarovnanie, SSIM, diff blob-y a metriky.
    thresholds:
      - ssim_min (default 0.92)
      - diff_thresh (bin thr pre abs diff, default 15)
      - min_blob_area (default 20 px)
      - max_total_area (default 2000 px)
      - max_blob_count (default 10)
    """
    th = dict(ssim_min=0.92, diff_thresh=15, min_blob_area=20, max_total_area=2000, max_blob_count=10)
    if thresholds:
        th.update(thresholds)

    H, W = golden.shape[:2]
    mask_pose, mask_roi_eff, _mask_ignore = regions_to_masks(regions, (H, W))

    # 1) zarovnanie podľa pose
    frame_aligned, warp = _align_by_pose(golden, frame, mask_pose)

    # 2) SSIM v ROI (bez ignore)
    ssim_val = _ssim(golden, frame_aligned, mask_roi_eff)

    # 3) abs diff + maskovanie + morfológia
    diff = cv2.absdiff(golden, frame_aligned)
    if th["diff_thresh"] > 0:
        _, binm = cv2.threshold(diff, th["diff_thresh"], 255, cv2.THRESH_BINARY)
    else:
        # Otsu v ROI
        _, binm = cv2.threshold(cv2.bitwise_and(diff, mask_roi_eff), 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    binm = cv2.bitwise_and(binm, mask_roi_eff)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
    binm = cv2.morphologyEx(binm, cv2.MORPH_OPEN, kernel, iterations=1)
    binm = cv2.dilate(binm, kernel, iterations=1)

    # 4) kontúry → metriky
    cnts, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    areas = [cv2.contourArea(c) for c in cnts if cv2.contourArea(c) >= th["min_blob_area"]]
    total_area = float(np.sum(areas))
    blob_count = int(len(areas))

    # 5) rozhodnutie
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

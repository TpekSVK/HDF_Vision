# --- hore v súbore (importy + CUDA detekcia) ---
import numpy as np
import cv2
from typing import Dict, Any, Tuple
from app.services.mask_utils import regions_to_masks

USE_CUDA = hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0

# Pomocné GPU funkcie
def _gpu_upload(u8: np.ndarray):
    g = cv2.cuda_GpuMat()
    g.upload(u8)
    return g

def _gpu_gauss(gpu_src, ksize: int = 3, sigma: float = 0.8):
    k = (ksize, ksize)
    gf = cv2.cuda.createGaussianFilter(cv2.CV_8UC1, cv2.CV_8UC1, k, sigma)
    return gf.apply(gpu_src)

def _gpu_absdiff(a, b):
    return cv2.cuda.absdiff(a, b)

def _gpu_threshold(gpu_src, thresh, maxv=255, typ=cv2.THRESH_BINARY):
    # returns (retval, dst)
    return cv2.cuda.threshold(gpu_src, thresh, maxv, typ)[1]

def _gpu_morph_open_then_dilate(gpu_bin, k_open=3, k_dil=3):
    se_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_open, k_open))
    se_dil  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_dil, k_dil))
    f_open = cv2.cuda.createMorphologyFilter(cv2.MORPH_OPEN, cv2.CV_8UC1, se_open, iterations=1)
    f_dil  = cv2.cuda.createMorphologyFilter(cv2.MORPH_DILATE, cv2.CV_8UC1, se_dil, iterations=1)
    out = f_open.apply(gpu_bin)
    out = f_dil.apply(out)
    return out

def _gpu_warp_by_translation(gpu_src, dx, dy):
    # size() -> (rows, cols)
    rows, cols = gpu_src.size()
    M = np.array([[1, 0, dx],
                  [0, 1, dy]], np.float32)
    return cv2.cuda.warpAffine(
        gpu_src, M, (cols, rows),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT101
    )

def _gpu_match_template_coarse_fine(frame_u8, templ_u8, xs, ys, xe, ye):
    """
    GPU coarse->fine matchTemplate v obdĺžniku [xs:xe, ys:ye].
    Vracia (dx, dy, corr) relatívne voči (xs, ys).
    """
    H, W = frame_u8.shape[:2]
    search = frame_u8[ys:ye, xs:xe]
    sh, sw = search.shape[:2]
    th, tw = templ_u8.shape[:2]

    # --- coarse scale ---
    max_dim = max(sh, sw)
    scale = 1.0 if max_dim <= 600 else 600.0 / max_dim
    if scale < 1.0:
        sS = cv2.resize(search, (int(sw*scale), int(sh*scale)), interpolation=cv2.INTER_AREA)
        sT = cv2.resize(templ_u8, (int(tw*scale), int(th*scale)), interpolation=cv2.INTER_AREA)
        gS, gT = _gpu_upload(sS), _gpu_upload(sT)
        gRes = cv2.cuda.matchTemplate(gS, gT, cv2.TM_CCOEFF_NORMED)
        res  = gRes.download()
        _, maxVal_s, _, maxLoc_s = cv2.minMaxLoc(res)
        coarse_x = int(round(maxLoc_s[0] / scale))
        coarse_y = int(round(maxLoc_s[1] / scale))
    else:
        gS, gT = _gpu_upload(search), _gpu_upload(templ_u8)
        gRes = cv2.cuda.matchTemplate(gS, gT, cv2.TM_CCOEFF_NORMED)
        res  = gRes.download()
        _, maxVal, _, maxLoc = cv2.minMaxLoc(res)
        dx = float(maxLoc[0])
        dy = float(maxLoc[1])
        return dx, dy, float(maxVal)

    # --- fine okno v plnom rozlíšení ---
    pad = 20
    fx1 = xs + max(0, coarse_x - pad)
    fy1 = ys + max(0, coarse_y - pad)
    fx2 = min(xe, fx1 + tw + 2*pad)
    fy2 = min(ye, fy1 + th + 2*pad)
    fine = frame_u8[fy1:fy2, fx1:fx2]
    if fine.shape[0] < th or fine.shape[1] < tw:
        return float(coarse_x), float(coarse_y), float(maxVal_s)

    gF, gT = _gpu_upload(fine), _gpu_upload(templ_u8)
    gRes = cv2.cuda.matchTemplate(gF, gT, cv2.TM_CCOEFF_NORMED)
    res = gRes.download()
    _, maxVal_f, _, maxLoc_f = cv2.minMaxLoc(res)

    best_x = (fx1 - xs) + maxLoc_f[0]
    best_y = (fy1 - ys) + maxLoc_f[1]
    return float(best_x), float(best_y), float(maxVal_f)

def _gpu_ssim_u8(img_u8, ref_u8, mask_u8=None):
    """
    SSIM na GPU: pracuje v 32F (Gaussian + element-wise),
    výsledné štatistiky sa spriemerujú na CPU (celé alebo maskované).
    """
    gI8 = _gpu_upload(img_u8)
    gR8 = _gpu_upload(ref_u8)

    gI32 = cv2.cuda.convertTo(gI8, cv2.CV_32F)
    gR32 = cv2.cuda.convertTo(gR8, cv2.CV_32F)

    gGauss = cv2.cuda.createGaussianFilter(cv2.CV_32F, cv2.CV_32F, (11, 11), 1.5)
    muI = gGauss.apply(gI32)
    muR = gGauss.apply(gR32)

    GI2 = gGauss.apply(cv2.cuda.multiply(gI32, gI32))
    GR2 = gGauss.apply(cv2.cuda.multiply(gR32, gR32))
    muI2 = cv2.cuda.multiply(muI, muI)
    muR2 = cv2.cuda.multiply(muR, muR)
    varI = cv2.cuda.subtract(GI2, muI2)
    varR = cv2.cuda.subtract(GR2, muR2)

    GIR = gGauss.apply(cv2.cuda.multiply(gI32, gR32))
    muImuR = cv2.cuda.multiply(muI, muR)
    cov = cv2.cuda.subtract(GIR, muImuR)

    muI_np = muI.download(); muR_np = muR.download()
    varI_np = varI.download(); varR_np = varR.download(); cov_np = cov.download()

    if mask_u8 is not None and mask_u8.any():
        m = mask_u8.astype(bool)
        ux = float(muI_np[m].mean()); uy = float(muR_np[m].mean())
        vx = float(varI_np[m].mean()); vy = float(varR_np[m].mean())
        cxy = float(cov_np[m].mean())
    else:
        ux = float(muI_np.mean()); uy = float(muR_np.mean())
        vx = float(varI_np.mean()); vy = float(varR_np.mean())
        cxy = float(cov_np.mean())

    L = 255.0; C1 = (0.01*L)**2; C2 = (0.03*L)**2
    num = (2*ux*uy + C1) * (2*cxy + C2)
    den = (ux*ux + uy*uy + C1) * (vx + vy + C2)
    return 1.0 if den <= 0 else float(num/den)


# ---------- Pomocné okná/filtre ----------
def _hanning_window(shape):
    # shape = (H, W)
    return cv2.createHanningWindow((shape[1], shape[0]), cv2.CV_32F)

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
                           search_margin: int = 20) -> tuple[np.ndarray, dict]:
    """
    Coarse-to-fine template matching v ROI:
      - 1) downscale vyhľadávacie okno aj template (r ~ 0.5..0.25), nájdi hrubú polohu
      - 2) okolo nájdenej polohy sprav malý fine search v plnom rozlíšení
    Vracia zarovnaný frame a diagnostiku.
    """
    bbox = _roi_bbox(mask_roi)
    if bbox is None:
        return frame_u8, {"tm_dx": 0.0, "tm_dy": 0.0, "tm_corr": 1.0, "tm_used": 0}

    x, y, w, h = bbox
    templ = golden_u8[y:y+h, x:x+w]

    H, W = frame_u8.shape[:2]
    xs = max(0, x - search_margin)
    ys = max(0, y - search_margin)
    xe = min(W, x + w + search_margin)
    ye = min(H, y + h + search_margin)
    search = frame_u8[ys:ye, xs:xe]
    sh, sw = search.shape[:2]
    if sh < h or sw < w:
        return frame_u8, {"tm_dx": 0.0, "tm_dy": 0.0, "tm_corr": 0.0, "tm_used": 0}

    # --- Coarse stage ---
    max_dim = max(sh, sw)
    scale = 1.0 if max_dim <= 600 else 600.0 / max_dim

    if scale < 1.0:
        dsize_s = (max(1, int(sw * scale)), max(1, int(sh * scale)))
        dsize_t = (max(1, int(w  * scale)), max(1, int(h  * scale)))
        search_s = cv2.resize(search, dsize_s, interpolation=cv2.INTER_AREA)
        templ_s  = cv2.resize(templ,  dsize_t, interpolation=cv2.INTER_AREA)
        res_s = cv2.matchTemplate(search_s, templ_s, cv2.TM_CCOEFF_NORMED)
        _, maxVal_s, _, maxLoc_s = cv2.minMaxLoc(res_s)
        coarse_x = int(round(maxLoc_s[0] / scale))
        coarse_y = int(round(maxLoc_s[1] / scale))
    else:
        res = cv2.matchTemplate(search, templ, cv2.TM_CCOEFF_NORMED)
        _, maxVal, _, maxLoc = cv2.minMaxLoc(res)
        best_x = maxLoc[0]; best_y = maxLoc[1]
        dx = float((xs + best_x) - x)
        dy = float((ys + best_y) - y)
        aligned = warp_by_translation(frame_u8, -dx, -dy)
        return aligned, {"tm_dx": dx, "tm_dy": dy, "tm_corr": float(maxVal), "tm_used": 1}

    # --- Fine stage ---
    pad = 20
    fx1 = xs + max(0, coarse_x - pad)
    fy1 = ys + max(0, coarse_y - pad)
    fx2 = min(xe, fx1 + w + 2*pad)
    fy2 = min(ye, fy1 + h + 2*pad)
    fine = frame_u8[fy1:fy2, fx1:fx2]
    if fine.shape[0] < h or fine.shape[1] < w:
        best_x = coarse_x
        best_y = coarse_y
        corr   = float(maxVal_s if scale < 1.0 else 0.0)
    else:
        res_f = cv2.matchTemplate(fine, templ, cv2.TM_CCOEFF_NORMED)
        _, maxVal_f, _, maxLoc_f = cv2.minMaxLoc(res_f)
        best_x = (fx1 - x) + maxLoc_f[0]
        best_y = (fy1 - y) + maxLoc_f[1]
        corr   = float(maxVal_f)

    dx = float(best_x)
    dy = float(best_y)
    aligned = warp_by_translation(frame_u8, -dx, -dy)
    return aligned, {"tm_dx": dx, "tm_dy": dy, "tm_corr": float(corr), "tm_used": 1}

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
        tm_enable=1,         # 1=zap., 0=vyp.
        tm_margin=200,       # px okolo ROI
        tm_min_corr=0.55     # ak korelácia veľmi nízka, efekt bude slabý
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

    # 3) SSIM v ROI (CPU alebo GPU podľa chuti; necháme CPU pre stabilitu)
    ssim_val = _ssim(golden, frame_aligned, mask_roi_eff)
    # Alternatíva (zapnúť ak chceš GPU): ssim_val = _gpu_ssim_u8(golden, frame_aligned, mask_roi_eff) if USE_CUDA else _ssim(golden, frame_aligned, mask_roi_eff)

    # 4) „Mäkší“ diff: blur → absdiff → threshold → morfológia (GPU ak je dostupná)
    if USE_CUDA:
        g_g = _gpu_upload(golden)
        g_f = _gpu_upload(frame_aligned)

        g_gb = _gpu_gauss(g_g, ksize=3, sigma=0.8)
        g_fb = _gpu_gauss(g_f, ksize=3, sigma=0.8)

        g_diff = _gpu_absdiff(g_gb, g_fb)

        if th["diff_thresh"] > 0:
            _, g_bin = cv2.cuda.threshold(g_diff, th["diff_thresh"], 255, cv2.THRESH_BINARY)
        else:
            # OTSU na ROI na CPU
            diff_cpu = cv2.bitwise_and(g_diff.download(), mask_roi_eff)
            _, binm_cpu = cv2.threshold(diff_cpu, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            g_bin = _gpu_upload(binm_cpu)

        # aplikuj ROI masku
        g_mask = _gpu_upload(mask_roi_eff)
        g_bin = cv2.cuda.bitwise_and(g_bin, g_mask)

        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
        f_open = cv2.cuda.createMorphologyFilter(cv2.MORPH_OPEN,  cv2.CV_8UC1, se, iterations=1)
        f_dil  = cv2.cuda.createMorphologyFilter(cv2.MORPH_DILATE, cv2.CV_8UC1, se, iterations=1)
        g_bin2 = f_open.apply(g_bin)
        g_bin3 = f_dil.apply(g_bin2)

        binm = g_bin3.download()
    else:
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

# app/utils/imaging.py
from __future__ import annotations
import base64
import math
from typing import Any, Dict, Tuple, Optional

import numpy as np
import cv2

# ------------------------------------------------------------
# CUDA prítomnosť + inicializácia
# ------------------------------------------------------------
def _detect_cuda() -> bool:
    try:
        if not hasattr(cv2, "cuda"):
            return False
        n = cv2.cuda.getCudaEnabledDeviceCount()
        return int(n) > 0
    except Exception:
        return False

USE_CUDA: bool = _detect_cuda()
_INITIALIZED: bool = False

def device_info() -> Dict[str, str | int | float] | None:
    """Vráti info o CUDA zariadení alebo None, ak GPU nepoužívame."""
    if not USE_CUDA:
        return None
    try:
        # cv2 nemá priamu Python API na získanie všetkých polí; vypíšeme aspoň krátky súhrn
        # a urobíme krátky warmup call (pomáha JIT/driveru).
        cv2.cuda.printShortCudaDeviceInfo(0)
        return {"device_index": 0}
    except Exception:
        return {"device_index": 0}

def ensure_initialized() -> None:
    """Jednorazový warm-up pre CUDA (bezpečný aj na CPU)."""
    global _INITIALIZED
    global USE_CUDA
    if _INITIALIZED:
        return
    if USE_CUDA:
        try:
            a = np.zeros((8, 8), np.uint8)
            g = cv2.cuda_GpuMat()
            g.upload(a)
            f = cv2.cuda.createGaussianFilter(cv2.CV_8UC1, cv2.CV_8UC1, (3, 3), 0.8)
            _ = f.apply(g)
            _ = _.download()
        except Exception:
            # Ak by GPU cesta padla, prepni na CPU
              # type: ignore[redefined-outer-name]
            USE_CUDA = False
    _INITIALIZED = True


# ------------------------------------------------------------
# Pomocné low-level GPU wrappery (na interné použitie)
# ------------------------------------------------------------
def _gpu_upload(u8: np.ndarray) -> "cv2.cuda_GpuMat":
    g = cv2.cuda_GpuMat()
    g.upload(u8)
    return g

def _gpu_download(g: "cv2.cuda_GpuMat") -> np.ndarray:
    return g.download()


# ------------------------------------------------------------
# Cache filtrov a jadier (aby sa pri každom volaní nealokovali)
# ------------------------------------------------------------
_gauss_cache: Dict[Tuple[int, int, float], "cv2.cuda_Filter"] = {}
_morph_cache: Dict[Tuple[int, int, int], "cv2.cuda_Filter"] = {}  # (op, k, shape)
_kernel_cache: Dict[Tuple[int, int], np.ndarray] = {}             # (k, shape)

def _get_structuring_element(k: int, shape: int = cv2.MORPH_ELLIPSE) -> np.ndarray:
    key = (k, shape)
    if key not in _kernel_cache:
        _kernel_cache[key] = cv2.getStructuringElement(shape, (k, k))
    return _kernel_cache[key]

def _get_gauss_filter(k: int, sigma: float) -> "cv2.cuda_Filter":
    key = (cv2.CV_8UC1, k, sigma)
    if key not in _gauss_cache:
        _gauss_cache[key] = cv2.cuda.createGaussianFilter(cv2.CV_8UC1, cv2.CV_8UC1, (k, k), sigma)
    return _gauss_cache[key]

def _get_morph_filter(op: int, k: int, shape: int = cv2.MORPH_ELLIPSE) -> "cv2.cuda_Filter":
    key = (op, k, shape)
    if key not in _morph_cache:
        se = _get_structuring_element(k, shape)
        _morph_cache[key] = cv2.cuda.createMorphologyFilter(op, cv2.CV_8UC1, se)
    return _morph_cache[key]


# ------------------------------------------------------------
# Verejné utility – prijímajú a vracajú np.ndarray (u8), interne použijú GPU ak je dostupná
# ------------------------------------------------------------
def to_gray_u8(img: np.ndarray) -> np.ndarray:
    """Bezpečne skonvertuje na 1-kanálové uint8 (0–255)."""
    if img.ndim == 2:
        x = img
    elif img.ndim == 3:
        # BGR->GRAY
        x = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError("Unsupported image shape")
    if x.dtype != np.uint8:
        x = np.clip(x, 0, 255).astype(np.uint8)
    return x

def blur_gaussian_u8(src: np.ndarray, sigma: float = 0.8, kmin: int = 3) -> np.ndarray:
    """Gaussian blur (u8→u8), GPU fallback na CPU. K = roundup(σ*6)|1, aspoň kmin."""
    ensure_initialized()
    k = max(kmin, int(round(sigma * 6)) | 1)
    if USE_CUDA:
        try:
            gf = _get_gauss_filter(k, sigma)
            g = _gpu_upload(src)
            out = gf.apply(g)
            return _gpu_download(out)
        except Exception:
            pass
    return cv2.GaussianBlur(src, (k, k), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REFLECT101)

def absdiff_u8(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """|a - b| (u8), GPU fallback na CPU."""
    ensure_initialized()
    if USE_CUDA:
        try:
            ga, gb = _gpu_upload(a), _gpu_upload(b)
            out = cv2.cuda.absdiff(ga, gb)
            return _gpu_download(out)
        except Exception:
            pass
    return cv2.absdiff(a, b)

def threshold_bin_u8(src: np.ndarray, thresh: float, maxv: int = 255, typ: int = cv2.THRESH_BINARY) -> np.ndarray:
    """Threshold (u8), GPU fallback na CPU."""
    ensure_initialized()
    if USE_CUDA:
        try:
            g = _gpu_upload(src)
            _, out = cv2.cuda.threshold(g, thresh, float(maxv), typ)
            return _gpu_download(out)
        except Exception:
            pass
    _, out = cv2.threshold(src, thresh, maxv, typ)
    return out

def morphology_open_then_dilate_u8(src_bin: np.ndarray, k_open: int = 3, k_dil: int = 3) -> np.ndarray:
    """(Open ⟶ Dilate) na binárnom obraze u8, GPU fallback na CPU."""
    ensure_initialized()
    if USE_CUDA:
        try:
            g = _gpu_upload(src_bin)
            f_open = _get_morph_filter(cv2.MORPH_OPEN, k_open)
            f_dil  = _get_morph_filter(cv2.MORPH_DILATE, k_dil)
            out = f_open.apply(g)
            out = f_dil.apply(out)
            return _gpu_download(out)
        except Exception:
            pass
    se_open = _get_structuring_element(k_open)
    se_dil  = _get_structuring_element(k_dil)
    out = cv2.morphologyEx(src_bin, cv2.MORPH_OPEN, se_open, iterations=1)
    out = cv2.dilate(out, se_dil, iterations=1)
    return out

def warp_by_translation_u8(src: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """2D posun, okraje REFLECT101. GPU fallback na CPU."""
    ensure_initialized()
    h, w = src.shape[:2]
    M = np.array([[1, 0, dx], [0, 1, dy]], np.float32)
    if USE_CUDA:
        try:
            g = _gpu_upload(src)
            out = cv2.cuda.warpAffine(g, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
            return _gpu_download(out)
        except Exception:
            pass
    return cv2.warpAffine(src, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)

def minmax_u8(src: np.ndarray) -> Tuple[float, float]:
    """Min, max; ak je GPU, použije cv2.cuda.minMax (rýchlejší)."""
    ensure_initialized()
    if USE_CUDA:
        try:
            g = _gpu_upload(src)
            mn, mx = cv2.cuda.minMax(g)
            return float(mn), float(mx)
        except Exception:
            pass
    mn, mx, _, _ = cv2.minMaxLoc(src)
    return float(mn), float(mx)

def match_template_u8(
    frame_u8: np.ndarray,
    templ_u8: np.ndarray,
    roi: Optional[Tuple[int, int, int, int]] = None,  # (x, y, w, h) na frame
    search_margin: int = 20,
    coarse_cap: int = 600,
) -> Tuple[float, float, float, int]:
    """
    Coarse→Fine matchTemplate v ROI. Vracia (dx, dy, corr, used),
    kde dx,dy sú posuny voči (x,y) ROI (ak ROI=celý obraz, tak voči (0,0)).
    """
    ensure_initialized()
    H, W = frame_u8.shape[:2]
    if roi is None:
        x, y, w, h = 0, 0, W, H
    else:
        x, y, w, h = roi

    xs = max(0, x - search_margin)
    ys = max(0, y - search_margin)
    xe = min(W, x + w + search_margin)
    ye = min(H, y + h + search_margin)

    search = frame_u8[ys:ye, xs:xe]
    if search.shape[0] < templ_u8.shape[0] or search.shape[1] < templ_u8.shape[1]:
        return 0.0, 0.0, 0.0, 0

    # Coarse – downscale veľkých okien
    sh, sw = search.shape[:2]
    max_dim = max(sh, sw)
    scale = 1.0 if max_dim <= coarse_cap else (coarse_cap / max_dim)

    def _run_mt(S: np.ndarray, T: np.ndarray, gpu: bool) -> Tuple[float, Tuple[int, int]]:
        if gpu:
            gS, gT = _gpu_upload(S), _gpu_upload(T)
            gRes = cv2.cuda.matchTemplate(gS, gT, cv2.TM_CCOEFF_NORMED)
            res = _gpu_download(gRes)
        else:
            res = cv2.matchTemplate(S, T, cv2.TM_CCOEFF_NORMED)
        _, maxVal, _, maxLoc = cv2.minMaxLoc(res)
        return float(maxVal), (int(maxLoc[0]), int(maxLoc[1]))

    # Coarse stage
    if scale < 1.0:
        dsize_s = (max(1, int(sw * scale)), max(1, int(sh * scale)))
        dsize_t = (max(1, int(templ_u8.shape[1] * scale)), max(1, int(templ_u8.shape[0] * scale)))
        search_s = cv2.resize(search, dsize_s, interpolation=cv2.INTER_AREA)
        templ_s  = cv2.resize(templ_u8, dsize_t, interpolation=cv2.INTER_AREA)

        try_gpu = USE_CUDA
        try:
            corr_s, maxLoc_s = _run_mt(search_s, templ_s, try_gpu)
        except Exception:
            corr_s, maxLoc_s = _run_mt(search_s, templ_s, False)

        coarse_x = int(round(maxLoc_s[0] / scale))
        coarse_y = int(round(maxLoc_s[1] / scale))
        # Fine stage – malý výrez v plnom rozlíšení
        pad = 20
        fx1 = xs + max(0, coarse_x - pad)
        fy1 = ys + max(0, coarse_y - pad)
        fx2 = min(xe, fx1 + templ_u8.shape[1] + 2 * pad)
        fy2 = min(ye, fy1 + templ_u8.shape[0] + 2 * pad)

        fine = frame_u8[fy1:fy2, fx1:fx2]
        if fine.shape[0] < templ_u8.shape[0] or fine.shape[1] < templ_u8.shape[1]:
            best_x = coarse_x
            best_y = coarse_y
            corr = corr_s
        else:
            try:
                corr_f, maxLoc_f = _run_mt(fine, templ_u8, try_gpu)
            except Exception:
                corr_f, maxLoc_f = _run_mt(fine, templ_u8, False)
            best_x = (fx1 - xs) + maxLoc_f[0]
            best_y = (fy1 - ys) + maxLoc_f[1]
            corr = corr_f
    else:
        try_gpu = USE_CUDA
        try:
            corr, maxLoc = _run_mt(search, templ_u8, try_gpu)
        except Exception:
            corr, maxLoc = _run_mt(search, templ_u8, False)
        best_x, best_y = maxLoc

    dx = float(best_x)  # relatívne v ROI
    dy = float(best_y)
    return dx, dy, float(corr), 1

def ssim_u8(img_u8: np.ndarray, ref_u8: np.ndarray, mask_u8: Optional[np.ndarray] = None) -> float:
    """SSIM (0..1). GPU (ak je) s CPU fallbackom; výsledok je skalar."""
    ensure_initialized()
    if img_u8.shape != ref_u8.shape:
        raise ValueError("ssim_u8: obraz a referenčný musia mať rovnaký tvar")
    # GPU cesta
    if USE_CUDA:
        try:
            gI = _gpu_upload(img_u8)
            gR = _gpu_upload(ref_u8)
            gGauss = cv2.cuda.createGaussianFilter(cv2.CV_8UC1, cv2.CV_32FC1, (11, 11), 1.5)
            muI = gGauss.apply(gI)
            muR = gGauss.apply(gR)

            gI32 = cv2.cuda.convertTo(gI, cv2.CV_32F)
            gR32 = cv2.cuda.convertTo(gR, cv2.CV_32F)

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
                m = mask_u8 > 0
                ux = float(muI_np[m].mean()); uy = float(muR_np[m].mean())
                vx = float(varI_np[m].mean()); vy = float(varR_np[m].mean())
                cxy = float(cov_np[m].mean())
            else:
                ux = float(muI_np.mean()); uy = float(muR_np.mean())
                vx = float(varI_np.mean()); vy = float(varR_np.mean())
                cxy = float(cov_np.mean())

            L = 255.0; C1 = (0.01 * L) ** 2; C2 = (0.03 * L) ** 2
            num = (2 * ux * uy + C1) * (2 * cxy + C2)
            den = (ux * ux + uy * uy + C1) * (vx + vy + C2)
            return 1.0 if den <= 0 else float(num / den)
        except Exception:
            # fallback nižšie
            pass

    # CPU fallback – rýchla implementácia
    x = img_u8.astype(np.float32)
    y = ref_u8.astype(np.float32)
    if mask_u8 is not None and mask_u8.any():
        m = mask_u8 > 0
        if not np.any(m):
            return 1.0
        x = x[m]; y = y[m]
    if x.size == 0:
        return 1.0
    ux, uy = float(np.mean(x)), float(np.mean(y))
    vx, vy = float(np.var(x)), float(np.var(y))
    cxy = float(np.mean((x - ux) * (y - uy)))
    L = 255.0; C1 = (0.01 * L) ** 2; C2 = (0.03 * L) ** 2
    num = (2 * ux * uy + C1) * (2 * cxy + C2)
    den = (ux * ux + uy * uy + C1) * (vx + vy + C2)
    if den <= 0:
        return 1.0 if np.allclose(x, y) else 0.0
    return float(num / den)


def encode_mask_to_blob(mask: Optional[np.ndarray]) -> Optional[Dict[str, Any]]:
    """Encode a binary mask into a JSON-serializable blob."""

    if mask is None:
        return None

    arr = np.asarray(mask)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    if arr.ndim != 2:
        raise ValueError("Mask must be a 2D array")

    arr_u8 = arr.astype(np.uint8, copy=False)
    h, w = arr_u8.shape[:2]

    success, buf = cv2.imencode(".png", arr_u8)
    if not success:
        raise ValueError("Failed to encode mask as PNG")

    data_b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return {
        "encoding": "png_base64",
        "width": int(w),
        "height": int(h),
        "data": data_b64,
    }


def decode_mask_from_blob(blob: Optional[Dict[str, Any]]) -> Optional[np.ndarray]:
    """Decode a JSON blob created by :func:`encode_mask_to_blob`."""

    if not blob:
        return None

    if blob.get("encoding") != "png_base64":
        return None

    try:
        raw = base64.b64decode(blob.get("data", ""))
    except Exception:
        return None

    if not raw:
        return None

    buf = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None

    if img.ndim == 3:
        img = img[:, :, 0]

    return img.astype(np.uint8, copy=False)

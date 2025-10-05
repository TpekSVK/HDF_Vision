# app/services/storage_service.py
import os, json, time, threading, queue, math, uuid
from pathlib import Path
from datetime import datetime
import imageio.v3 as iio
import numpy as np

# --- Konfigurácia ---
_CFG_PATH = Path("/data/config.json")
_CFG_DEFAULT = {
    "store_full_nok": True,
    "thumb_jpeg_quality": 30,
    "full_webp_quality": 95,
    "retention_days": 7,
    "retention_max_gb": 5,
    "disk_guard_min_free_percent": 10
}
def _load_cfg():
    try:
        if _CFG_PATH.exists():
            with open(_CFG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                return {**_CFG_DEFAULT, **d}
    except Exception:
        pass
    return dict(_CFG_DEFAULT)

CFG = _load_cfg()

# --- Disk guard ---
def _stat_free_percent(path="/data"):
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free  = st.f_bavail * st.f_frsize
    if total <= 0: return 100.0
    return 100.0 * free / total

def _minimal_mode():
    return _stat_free_percent("/data") < float(CFG.get("disk_guard_min_free_percent", 10))

# --- Helpery cesty ---
def _ensure_dirs(recipe: str):
    base = Path("/data")
    (base / "recipes" / recipe).mkdir(parents=True, exist_ok=True)
    # runtime dirs: runs/YYYYMMDD/{thumbs,full,meta}
    day = datetime.now().strftime("%Y%m%d")
    run_dir = base / "runs" / day
    (run_dir / "thumbs").mkdir(parents=True, exist_ok=True)
    (run_dir / "full").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    # validation
    (base / "validation" / "ok").mkdir(parents=True, exist_ok=True)
    (base / "validation" / "nok").mkdir(parents=True, exist_ok=True)
    return run_dir

def _to_u8(img):
    if img is None:
        return None
    arr = img
    if arr.dtype == np.uint16:
        # Y12/Y16 -> u8
        # škáluj konzistentne na 0..255 (predpoklad 12-bit -> delíme /16)
        maxi = int(arr.max()) if arr.size else 0
        if maxi <= 0:
            return np.zeros_like(arr, dtype=np.uint8)
        if maxi <= 4095:
            return (arr >> 4).astype(np.uint8)
        return (arr.astype(np.float32) * (255.0/65535.0)).astype(np.uint8)
    if arr.ndim == 3 and arr.shape[2] == 3:
        # bezpečne zober prvý kanál (máme grayscale pipeline; toto je fallback)
        arr = arr[:, :, 0]
    return arr.astype(np.uint8, copy=False)

# --- Async fronta ---
class _SaveQueue:
    def __init__(self, maxsize=200):
        self.q = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._worker, daemon=True)

    def start(self):
        if not self._t.is_alive():
            self._t.start()

    def stop(self, timeout=1.0):
        self._stop.set()
        try:
            self.q.put_nowait(("__STOP__", None))
        except Exception:
            pass
        self._t.join(timeout=timeout)

    def put(self, job):
        self.q.put(job)

    def _worker(self):
        while not self._stop.is_set():
            job, payload = self.q.get()
            if job == "__STOP__": break
            try:
                if job == "prod":
                    _do_save_production(**payload)
                elif job == "golden":
                    _do_save_golden(**payload)
                elif job == "validation":
                    _do_save_validation(**payload)
            except Exception as e:
                # len log do konzoly – nech to neblokuje
                print("[SaveQueue][ERR]", e)

_SAVEQ = _SaveQueue()
_SAVEQ.start()

# --- Public API (zachovávame signatúry) ---
def save_golden(frame_u8, recipe_name: str):
    recipe = recipe_name or "default"
    _ensure_dirs(recipe)
    payload = {"frame": _to_u8(frame_u8), "recipe": recipe}
    _SAVEQ.put(("golden", payload))
    # vrátime očakávanú cestu (asynchrónne sa zapíše)
    path = Path("/data") / "recipes" / recipe / "golden.png"
    return str(path)

def save_validation_image(frame_u8, ok: bool, recipe_name: str):
    recipe = recipe_name or "default"
    _ensure_dirs(recipe)
    # vrátime hneď cesty; zápis ide async
    ts = int(time.time() * 1000)
    base = Path("/data")
    if ok:
        ffull = base / "validation" / "ok" / f"{ts}.webp"
        fthumb = base / "validation" / "ok" / f"{ts}_thumb.jpg"
    else:
        ffull = base / "validation" / "nok" / f"{ts}.webp"
        fthumb = base / "validation" / "nok" / f"{ts}_thumb.jpg"
    payload = {"frame": _to_u8(frame_u8), "ffull": ffull, "fthumb": fthumb}
    _SAVEQ.put(("validation", payload))
    return {"full": str(ffull), "thumb": str(fthumb)}

def save_production_result(frame_u8, meta: dict, recipe_name: str, store_full_nok: bool, nok: bool):
    recipe = recipe_name or "default"
    run_dir = _ensure_dirs(recipe)
    ts = int(time.time() * 1000)
    uid = uuid.uuid4().hex[:8]
    # očakávané cesty
    fthumb = run_dir / "thumbs" / f"{ts}_{uid}.jpg"
    ffull  = run_dir / "full"   / f"{ts}_{uid}.webp"
    fmeta  = run_dir / "meta"   / f"{ts}_{uid}.json"

    # aplikuj politiku (NOK-only/full + disk guard)
    cfg_store_full = bool(CFG.get("store_full_nok", True))
    guard_minimal = _minimal_mode()
    do_full = (not guard_minimal) and ((cfg_store_full and nok) or (not cfg_store_full and store_full_nok))

    payload = {
        "frame": _to_u8(frame_u8),
        "fthumb": fthumb, "ffull": ffull, "fmeta": fmeta,
        "meta": {**(meta or {}), "nok": bool(nok), "ts_ms": ts, "recipe": recipe},
        "do_full": do_full
    }
    _SAVEQ.put(("prod", payload))
    return {"thumb": str(fthumb), "full": str(ffull) if do_full else None, "meta": str(fmeta)}

# --- Skutočný zápis (worker) ---
def _do_save_golden(frame, recipe):
    if frame is None: return
    out = Path("/data") / "recipes" / recipe / "golden.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    iio.imwrite(out, frame, extension=".png")

def _do_save_validation(frame, ffull: Path, fthumb: Path):
    if frame is None: return
    ffull.parent.mkdir(parents=True, exist_ok=True)
    fthumb.parent.mkdir(parents=True, exist_ok=True)
    # thumb (JPEG)
    iio.imwrite(fthumb, frame, extension=".jpg", quality=int(CFG.get("thumb_jpeg_quality", 30)))
    # full (WebP lossless-ish, ale necháme quality z configu)
    iio.imwrite(ffull, frame, extension=".webp", quality=int(CFG.get("full_webp_quality", 95)))

def _do_save_production(frame, fthumb: Path, ffull: Path, fmeta: Path, meta: dict, do_full: bool):
    if frame is None: return
    fthumb.parent.mkdir(parents=True, exist_ok=True)
    ffull.parent.mkdir(parents=True, exist_ok=True)
    fmeta.parent.mkdir(parents=True, exist_ok=True)
    # thumb
    iio.imwrite(fthumb, frame, extension=".jpg", quality=int(CFG.get("thumb_jpeg_quality", 30)))
    # full podľa politiky
    if do_full:
        iio.imwrite(ffull, frame, extension=".webp", quality=int(CFG.get("full_webp_quality", 95)))
    # meta.json
    try:
        with open(fmeta, "w", encoding="utf-8") as f:
            json.dump(meta or {}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[save][meta][ERR]", e)

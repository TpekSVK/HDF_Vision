# app/services/storage_service.py
import os, json, time, threading, queue, math, uuid, shutil
from pathlib import Path
from datetime import datetime
import imageio.v3 as iio
import numpy as np
from typing import Any, Dict, Mapping, Sequence, Optional

from app.models.schema import RecipeV2

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
    # runtime dirs: runs/YYYYMMDD/<recipe>/{thumbs,full,meta}
    day = datetime.now().strftime("%Y%m%d")
    run_dir = base / "runs" / day / recipe
    (run_dir / "thumbs").mkdir(parents=True, exist_ok=True)
    (run_dir / "full").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    # placeholders for future artefacts
    (run_dir / "aligned").mkdir(parents=True, exist_ok=True)
    (run_dir / "overlay").mkdir(parents=True, exist_ok=True)
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


def _recipe_json_path(recipe: str, base_dir: str | Path = "/data") -> Path:
    return Path(base_dir) / "recipes" / recipe / "recipe.json"


def _multi_view_config_path(recipe: str, base_dir: str | Path = "/data") -> Path:
    return Path(base_dir) / "recipes" / recipe / "multi_view.json"


def _step_dir(recipe: str, step_id: str, base_dir: str | Path = "/data") -> Path:
    safe_id = str(step_id or "").strip() or "step"
    return Path(base_dir) / "recipes" / recipe / "steps" / safe_id


def load_recipe_config(recipe: str, *, base_dir: str | Path = "/data") -> RecipeV2:
    """Load recipe configuration including tool pipeline."""

    path = _recipe_json_path(recipe, base_dir)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return RecipeV2.from_dict(data)

    return RecipeV2()


def save_recipe_config(recipe: str, data: RecipeV2, *, base_dir: str | Path = "/data") -> Path:
    """Persist recipe configuration with normalized structure."""

    path = _recipe_json_path(recipe, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def load_multi_view_config(recipe: str, *, base_dir: str | Path = "/data") -> Dict[str, Any]:
    """Load multi-view configuration for the given recipe."""

    path = _multi_view_config_path(recipe, base_dir)
    if not path.exists():
        return {"aggregation": "AND", "steps": []}

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"aggregation": "AND", "steps": []}

    aggregation = str(data.get("aggregation", "AND")).upper()
    if aggregation not in {"AND", "OR", "WEIGHTED"}:
        aggregation = "AND"

    normalized_steps: list[Dict[str, Any]] = []
    for index, raw in enumerate(data.get("steps", []) or []):
        if not isinstance(raw, dict):
            continue
        step_id = str(raw.get("id") or raw.get("step_id") or f"step-{index + 1}").strip()
        if not step_id:
            step_id = f"step-{index + 1}"
        name = str(raw.get("name") or step_id)
        pose_enabled = bool(raw.get("pose_enabled", True))
        settle_ms = raw.get("settle_ms")
        try:
            settle_ms = None if settle_ms is None else max(0, int(settle_ms))
        except Exception:
            settle_ms = None
        camera_profile = raw.get("camera_profile") or {}
        if not isinstance(camera_profile, dict):
            camera_profile = {}
        order = raw.get("order")
        try:
            order_val = int(order)
        except Exception:
            order_val = index
        normalized_steps.append(
            {
                "id": step_id,
                "name": name,
                "order": order_val,
                "pose_enabled": pose_enabled,
                "settle_ms": settle_ms,
                "camera_profile": camera_profile,
            }
        )

    normalized_steps.sort(key=lambda entry: entry.get("order", 0))
    for idx, step in enumerate(normalized_steps):
        step["order"] = idx

    return {"aggregation": aggregation, "steps": normalized_steps}


def save_multi_view_config(
    recipe: str,
    data: Mapping[str, Any],
    *,
    base_dir: str | Path = "/data",
) -> Dict[str, Any]:
    """Persist the multi-view configuration and return the normalized payload."""

    normalized = load_multi_view_config(recipe, base_dir=base_dir)
    aggregation = str(data.get("aggregation", normalized.get("aggregation", "AND"))).upper()
    if aggregation not in {"AND", "OR", "WEIGHTED"}:
        aggregation = "AND"

    raw_steps = list(data.get("steps", []))
    steps: list[Dict[str, Any]] = []
    for index, raw in enumerate(raw_steps):
        if isinstance(raw, dict):
            steps.append(dict(raw))

    # Normalization ensures deterministic ordering and required fields.
    normalized = {"aggregation": aggregation, "steps": []}
    for idx, entry in enumerate(steps):
        step_id = str(entry.get("id") or entry.get("step_id") or f"step-{idx + 1}").strip()
        if not step_id:
            step_id = f"step-{idx + 1}"
        name = str(entry.get("name") or step_id)
        pose_enabled = bool(entry.get("pose_enabled", True))
        settle_ms = entry.get("settle_ms")
        try:
            settle_ms = None if settle_ms is None else max(0, int(settle_ms))
        except Exception:
            settle_ms = None
        camera_profile = entry.get("camera_profile") or {}
        if not isinstance(camera_profile, dict):
            camera_profile = {}
        normalized["steps"].append(
            {
                "id": step_id,
                "name": name,
                "order": idx,
                "pose_enabled": pose_enabled,
                "settle_ms": settle_ms,
                "camera_profile": camera_profile,
            }
        )

    path = _multi_view_config_path(recipe, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(normalized, fh, ensure_ascii=False, indent=2)
    return normalized


def load_multi_view_step_assets(
    recipe: str,
    step_id: str,
    *,
    base_dir: str | Path = "/data",
) -> Dict[str, Any]:
    """Load persisted assets for a multi-view step (golden, regions, limits)."""

    step_path = _step_dir(recipe, step_id, base_dir)
    assets: Dict[str, Any] = {"regions": [], "limits": {}}

    golden_path = step_path / "golden.png"
    if golden_path.exists():
        try:
            image = iio.imread(golden_path)
            if image.ndim == 3:
                image = image[:, :, 0]
            if image.dtype != np.uint8:
                image = np.clip(image, 0, 255).astype(np.uint8)
            assets["golden"] = image
        except Exception:
            assets["golden"] = None
    else:
        assets["golden"] = None

    regions_path = step_path / "regions.json"
    if regions_path.exists():
        try:
            with open(regions_path, "r", encoding="utf-8") as fh:
                assets["regions"] = list(json.load(fh) or [])
        except Exception:
            assets["regions"] = []

    limits_path = step_path / "limits.json"
    if limits_path.exists():
        try:
            with open(limits_path, "r", encoding="utf-8") as fh:
                raw_limits = json.load(fh)
            if isinstance(raw_limits, dict):
                assets["limits"] = raw_limits
            else:
                assets["limits"] = {}
        except Exception:
            assets["limits"] = {}

    return assets


def save_multi_view_step_assets(
    recipe: str,
    step_id: str,
    *,
    golden: Optional[np.ndarray] = None,
    regions: Optional[Sequence[Mapping[str, Any]]] = None,
    limits: Optional[Mapping[str, Any]] = None,
    base_dir: str | Path = "/data",
) -> Dict[str, Any]:
    """Persist assets for a multi-view step and return normalized payload."""

    step_path = _step_dir(recipe, step_id, base_dir)
    step_path.mkdir(parents=True, exist_ok=True)

    if golden is not None:
        try:
            arr = _to_u8(np.asarray(golden))
            iio.imwrite(step_path / "golden.png", arr)
        except Exception:
            pass

    if regions is not None:
        with open(step_path / "regions.json", "w", encoding="utf-8") as fh:
            json.dump([dict(r) for r in regions], fh, ensure_ascii=False, indent=2)

    if limits is not None:
        with open(step_path / "limits.json", "w", encoding="utf-8") as fh:
            json.dump(dict(limits), fh, ensure_ascii=False, indent=2)

    return load_multi_view_step_assets(recipe, step_id, base_dir=base_dir)


def delete_multi_view_step_assets(
    recipe: str,
    step_id: str,
    *,
    base_dir: str | Path = "/data",
) -> None:
    """Remove persisted assets for the selected step."""

    step_path = _step_dir(recipe, step_id, base_dir)
    if step_path.exists():
        shutil.rmtree(step_path, ignore_errors=True)


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

def save_production_result(
    frame_u8,
    meta: dict,
    recipe_name: str,
    store_full_nok: bool,
    nok: bool,
    *,
    step_frames: Sequence[np.ndarray] | None = None,
    step_meta: Sequence[Mapping[str, Any]] | None = None,
):
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
    step_entries: list[dict[str, str | None]] = []
    if step_frames or step_meta:
        steps_dir = run_dir / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        frames_list = list(step_frames or [])
        meta_list = list(step_meta or [])
        step_count = max(len(frames_list), len(meta_list))
        for index in range(step_count):
            frame_entry = frames_list[index] if index < len(frames_list) else None
            meta_entry: Mapping[str, Any] | None = meta_list[index] if index < len(meta_list) else None
            step_label = f"step_{index + 1:02d}"
            thumb_path = steps_dir / f"{step_label}.webp"
            meta_path = steps_dir / f"{step_label}.json"
            if frame_entry is not None:
                img = _to_u8(frame_entry)
                if img is not None:
                    iio.imwrite(thumb_path, img, extension=".webp")
            step_payload: Dict[str, Any] = {}
            if meta_entry:
                step_payload.update({
                    "step_id": meta_entry.get("step_id"),
                    "name": meta_entry.get("name"),
                    "verdict": meta_entry.get("verdict") or meta_entry.get("status"),
                    "metrics": meta_entry.get("metrics", {}),
                })
            if not step_payload:
                step_payload = {
                    "step_id": step_label,
                    "name": step_label,
                    "verdict": None,
                    "metrics": {},
                }
            with open(meta_path, "w", encoding="utf-8") as fh:
                json.dump(step_payload, fh, ensure_ascii=False, indent=2)
            step_entries.append({"thumb": str(thumb_path), "meta": str(meta_path)})

    result_payload = {"thumb": str(fthumb), "full": str(ffull) if do_full else None, "meta": str(fmeta)}
    if step_entries:
        result_payload["steps"] = step_entries
    return result_payload

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

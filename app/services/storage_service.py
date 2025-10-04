# app/services/storage_service.py
import os
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import cv2
import imageio.v3 as iio

# Základná štruktúra: /data/<customer>/<project>/<part>/<version>/
# Pre D1 stačí pracovať v /data/runs/YYYYMMDD/...
BASE_DIR = Path(os.environ.get("HDF_DATA_DIR", "/data")).resolve()

def _ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def _today_run_dir():
    d = datetime.now().strftime("%Y%m%d")
    p = BASE_DIR / "runs" / d
    _ensure_dir(p)
    return p

def _to_u8_gray(img):
    """Zaručí uint8 grayscale pre zápis JPEG/WebP/PNG (thumbnaily, náhľady)."""
    if img is None:
        return img
    a = img
    # farebné -> gray
    if a.ndim == 3 and a.shape[2] == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    # 16-bit -> 8-bit
    if a.dtype == np.uint16:
        maxv = int(a.max())
        if maxv <= 0:
            a = np.zeros_like(a, dtype=np.uint8)
        elif maxv <= 4095:
            a = (a >> 4).astype(np.uint8)
        else:
            a = cv2.convertScaleAbs(a, alpha=255.0/65535.0)
    elif a.dtype != np.uint8:
        a = cv2.normalize(a, None, 0, 255, cv2.NORM_MINMAX)
        a = a.astype(np.uint8)
    return a

def _timestamp_ms():
    return int(time.time() * 1000)

def save_production_result(frame,
                           meta: dict,
                           recipe_name: str,
                           store_full_nok: bool = False,
                           nok: bool = False):
    """
    Uloží produkčný výsledok:
      - JSONL s metadátami
      - thumbnail (JPG, 25-35% kvalita)
      - voliteľne full NOK (lossless WebP alebo 16-bit PNG, ak vstup bol 16-bit)
    """
    run_dir = _today_run_dir()
    ts = _timestamp_ms()

    # cesty
    fjsonl = run_dir / f"{ts}_{recipe_name}.jsonl"
    fthumb = run_dir / f"{ts}_{recipe_name}_thumb.jpg"
    ffull  = run_dir / f"{ts}_{recipe_name}_full"

    # thumbnail (resize na rozumnú šírku, napr. 640px, pri zachovaní pomeru)
    h, w = frame.shape[:2]
    tgt_w = 640
    scale = tgt_w / max(1, w)
    tgt_h = max(1, int(h * scale))
    thumb = cv2.resize(frame, (tgt_w, tgt_h), interpolation=cv2.INTER_AREA)
    thumb = _to_u8_gray(thumb)

    # zápis thumbnailu
    iio.imwrite(fthumb, thumb, extension=".jpg", quality=30)

    # zápis full (iba ak NOK a požadované)
    if store_full_nok and nok:
        if frame.dtype == np.uint16:
            # zachovaj 16-bit – PNG
            iio.imwrite(str(ffull) + ".png", frame, extension=".png")
        else:
            # lossless WebP ak je uint8
            fwebp = str(ffull) + ".webp"
            iio.imwrite(fwebp, _to_u8_gray(frame), extension=".webp", quality=100, lossless=True)

    # JSONL log
    record = {
        "ts": ts,
        "recipe": recipe_name,
        "nok": bool(nok),
        "meta": meta or {},
        "files": {
            "thumb": str(fthumb),
            "full": (str(ffull) + ".png") if (store_full_nok and nok and frame.dtype == np.uint16) else (
                (str(ffull) + ".webp") if (store_full_nok and nok) else None
            )
        },
        "shape": [int(h), int(w)],
        "dtype": str(frame.dtype),
    }
    with open(fjsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"thumb": str(fthumb), "jsonl": str(fjsonl), "full": record["files"]["full"]}


def save_validation_image(img, ok: bool, recipe_name: str):
    """
    Ukladanie validačných snímok počas SETUP/validácie.
    - OK ide do validation/ok/
    - NOK ide do validation/nok/
    """
    base = BASE_DIR / "validation" / ("ok" if ok else "nok")
    _ensure_dir(base)
    ts = _timestamp_ms()
    fthumb = base / f"{ts}_{recipe_name}_thumb.jpg"
    ffull  = base / f"{ts}_{recipe_name}_full"

    # thumbnail
    h, w = img.shape[:2]
    tgt_w = 640
    scale = tgt_w / max(1, w)
    tgt_h = max(1, int(h * scale))
    thumb = cv2.resize(img, (tgt_w, tgt_h), interpolation=cv2.INTER_AREA)
    thumb = _to_u8_gray(thumb)
    iio.imwrite(fthumb, thumb, extension=".jpg", quality=35)

    # full – ak 16-bit, uložíme PNG; inak lossless WebP
    if img.dtype == np.uint16:
        iio.imwrite(str(ffull) + ".png", img, extension=".png")
        full_path = str(ffull) + ".png"
    else:
        iio.imwrite(str(ffull) + ".webp", _to_u8_gray(img), extension=".webp", quality=100, lossless=True)
        full_path = str(ffull) + ".webp"

    return {"thumb": str(fthumb), "full": full_path}


def save_golden(img, recipe_name: str):
    """
    Uloží golden referenciu do recipes/<recipe_name>/golden.png (PNG kvôli 16-bit podpore).
    """
    dst_dir = BASE_DIR / "recipes" / recipe_name
    _ensure_dir(dst_dir)
    fp = dst_dir / "golden.png"
    # ak je 16-bit, uložíme 16-bit PNG; inak konvertujeme na uint8 gray
    if img.dtype == np.uint16:
        iio.imwrite(fp, img, extension=".png")
    else:
        iio.imwrite(fp, _to_u8_gray(img), extension=".png")
    return str(fp)

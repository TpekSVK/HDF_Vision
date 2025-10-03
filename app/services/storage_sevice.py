import os, time, json, uuid, shutil
from datetime import datetime
import imageio.v3 as iio
import cv2
import numpy as np

BASE = "/data"  # v Dockeri mountované
# Štruktúra: Customer/Project/Part/Version – zatiaľ placeholder "default"
CUSTOMER="DefaultCustomer"; PROJECT="DefaultProject"; PART="DefaultPart"; VERSION="v1"

def _p(*xs): return os.path.join(BASE, CUSTOMER, PROJECT, PART, VERSION, *xs)

def ensure_dirs():
    for d in ["recipes", "validation/ok", "validation/nok", "runs"]:
        os.makedirs(_p(d), exist_ok=True)

def save_golden(frame_gray8, recipe_name:str):
    ensure_dirs()
    path = _p(f"recipes/{recipe_name}")
    os.makedirs(path, exist_ok=True)
    golden_png = os.path.join(path, "golden.png")
    # uloženie bezstratovo
    iio.imwrite(golden_png, frame_gray8, extension=".png")
    return golden_png

def save_validation(frame_gray8, ok:bool, recipe_name:str):
    ensure_dirs()
    sub = "ok" if ok else "nok"
    t = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    # WebP lossless; fallback JPEG
    fname_webp = _p(f"validation/{sub}/{recipe_name}_{t}.webp")
    try:
        iio.imwrite(fname_webp, frame_gray8, extension=".webp", plugin="pillow", quality=100, lossless=True, method=6)
        return fname_webp
    except Exception:
        fname_jpg = _p(f"validation/{sub}/{recipe_name}_{t}.jpg")
        iio.imwrite(fname_jpg, frame_gray8, extension=".jpg", quality=90)
        return fname_jpg

def save_production_result(frame_gray8, result_meta:dict, recipe_name:str, store_full_nok:bool=False, nok:bool=False):
    ensure_dirs()
    day = datetime.utcnow().strftime("%Y%m%d")
    run_dir = _p(f"runs/{day}")
    os.makedirs(run_dir, exist_ok=True)

    # thumbnail (25–35%) JPEG
    thumb = cv2.resize(frame_gray8, (0,0), fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
    fthumb = os.path.join(run_dir, f"{recipe_name}_{int(time.time()*1000)}_thumb.jpg")
    iio.imwrite(fthumb, thumb, extension=".jpg", quality=30)

    # meta-log (JSON lines)
    flog = os.path.join(run_dir, f"{recipe_name}.jsonl")
    with open(flog, "a", encoding="utf-8") as f:
        f.write(json.dumps(result_meta, ensure_ascii=False) + "\n")

    full_path = None
    if nok and store_full_nok:
        # ulož plný snímok NOK
        full_path = os.path.join(run_dir, f"{recipe_name}_{int(time.time()*1000)}_nok.webp")
        iio.imwrite(full_path, frame_gray8, extension=".webp", plugin="pillow", quality=100, lossless=True, method=6)

    return {"thumb": fthumb, "log": flog, "full_nok": full_path}

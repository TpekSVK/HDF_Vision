from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import imageio.v3 as iio
import numpy as np


@dataclass(slots=True)
class PresenceV2Model:
    median: np.ndarray
    mad: np.ndarray
    stats: dict[str, Any]


def _as_gray_u8(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    if arr.dtype != np.uint8:
        arr = cv2.convertScaleAbs(arr)
    return arr


def compute_roi_hash(roi: tuple[int, int, int, int] | None, ignore_mask: np.ndarray | None) -> str:
    payload: dict[str, Any] = {"roi": tuple(roi) if roi is not None else None}
    if ignore_mask is not None:
        mask = np.asarray(ignore_mask)
        if mask.ndim == 3:
            mask = mask[:, :, 0]
        payload["mask_shape"] = tuple(mask.shape)
        payload["mask_sum"] = int(np.sum(mask > 0))
        payload["mask_sha1"] = hashlib.sha1(mask.astype(np.uint8, copy=False).tobytes()).hexdigest()
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def resolve_assets_dir(base_dir: str | Path, recipe_name: str, view_id: str, tool_identity: str) -> Path:
    root = Path(base_dir) / "recipes" / str(recipe_name)
    return root / "tool_assets" / str(view_id or "view_1") / str(tool_identity) / "presence_absence_v2"


def ensure_assets_dirs(base_dir: Path) -> dict[str, Path]:
    ok_dir = base_dir / "ok"
    nok_dir = base_dir / "nok"
    model_dir = base_dir / "model"
    thumbs_dir = model_dir / "thumbs"
    for item in (ok_dir, nok_dir, model_dir, thumbs_dir):
        item.mkdir(parents=True, exist_ok=True)
    return {"ok": ok_dir, "nok": nok_dir, "model": model_dir, "thumbs": thumbs_dir}


def save_sample(sample: np.ndarray, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    path = target_dir / f"sample_{stamp}.png"
    iio.imwrite(path, _as_gray_u8(sample))
    return path


def load_samples(sample_dir: Path) -> list[np.ndarray]:
    samples: list[np.ndarray] = []
    if not sample_dir.exists():
        return samples
    for path in sorted(sample_dir.glob("*.png")):
        try:
            samples.append(_as_gray_u8(iio.imread(path)))
        except Exception:
            continue
    return samples


def build_model(ok_samples: list[np.ndarray], *, polarity: str, eps: float = 1.0) -> tuple[np.ndarray, np.ndarray, dict[str, float], list[str]]:
    stack = np.stack([_as_gray_u8(sample).astype(np.float32) for sample in ok_samples], axis=0)
    median = np.median(stack, axis=0)
    mad = np.median(np.abs(stack - median[None, :, :]), axis=0) + float(eps)

    scores: list[float] = []
    areas: list[float] = []
    for sample in stack:
        metrics = evaluate_sample(sample, median, mad, polarity=polarity, score_threshold=3.0, total_area_threshold=0.0, min_blob_area=1.0)
        scores.append(float(metrics["anomaly_score"]))
        areas.append(float(metrics["anomaly_area"]))

    score_med = float(np.median(scores)) if scores else 0.0
    score_mad = float(np.median(np.abs(np.asarray(scores) - score_med))) if scores else 0.0
    area_med = float(np.median(areas)) if areas else 0.0
    area_mad = float(np.median(np.abs(np.asarray(areas) - area_med))) if areas else 0.0

    recommended = {
        "score_threshold": max(2.5, score_med + 3.0 * max(score_mad, 0.5)),
        "total_area_threshold": max(1.0, area_med + 3.0 * max(area_mad, 1.0)),
        "min_blob_area": max(5.0, (area_med + 1.0) * 0.1),
    }
    warnings: list[str] = []
    if score_mad > 1.5:
        warnings.append("OK dataset má vysokú variabilitu anomálneho skóre.")
    if area_mad > 50:
        warnings.append("OK dataset má vysokú variabilitu anomálnej plochy.")

    return median.astype(np.float32), mad.astype(np.float32), recommended, warnings


def evaluate_sample(
    sample: np.ndarray,
    median: np.ndarray,
    mad: np.ndarray,
    *,
    polarity: str,
    score_threshold: float,
    total_area_threshold: float,
    min_blob_area: float,
) -> dict[str, Any]:
    image = _as_gray_u8(sample).astype(np.float32)
    diff_raw = image - median.astype(np.float32)
    mode = str(polarity or "any").strip().lower()
    if mode == "darker_only":
        diff = np.maximum(median - image, 0.0)
    elif mode == "brighter_only":
        diff = np.maximum(image - median, 0.0)
    else:
        diff = np.abs(diff_raw)

    robust = diff / np.maximum(mad.astype(np.float32), 1.0)
    binary = (robust >= float(score_threshold)).astype(np.uint8) * 255
    kernel = np.ones((3, 3), dtype=np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    filtered = np.zeros_like(binary)
    blob_count = 0
    for idx in range(1, int(n_labels)):
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area >= float(min_blob_area):
            filtered[labels == idx] = 255
            blob_count += 1

    anomaly_area = float(np.count_nonzero(filtered))
    anomaly_score = float(np.max(robust)) if robust.size else 0.0
    max_dev = float(np.max(diff)) if diff.size else 0.0
    mean_dev = float(np.mean(diff)) if diff.size else 0.0

    status = "ok" if anomaly_area <= float(total_area_threshold) else "nok"
    overlay = cv2.cvtColor(_as_gray_u8(sample), cv2.COLOR_GRAY2BGR)
    overlay[filtered > 0] = (0, 0, 255)

    return {
        "status": status,
        "anomaly_score": anomaly_score,
        "anomaly_area": anomaly_area,
        "blob_count": int(blob_count),
        "max_deviation": max_dev,
        "mean_deviation": mean_dev,
        "binary_mask": filtered,
        "diff_map": np.clip(robust * 32.0, 0, 255).astype(np.uint8),
        "overlay": overlay,
    }


def save_model(model_dir: Path, median: np.ndarray, mad: np.ndarray, stats: dict[str, Any]) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    iio.imwrite(model_dir / "median.png", np.clip(median, 0, 255).astype(np.uint8))
    mad_norm = np.clip((mad / max(float(np.max(mad)), 1.0)) * 255.0, 0, 255).astype(np.uint8)
    iio.imwrite(model_dir / "mad.png", mad_norm)
    np.save(model_dir / "mad.npy", mad.astype(np.float32))
    with (model_dir / "stats.json").open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def load_model(model_dir: Path) -> PresenceV2Model | None:
    median_path = model_dir / "median.png"
    stats_path = model_dir / "stats.json"
    if not median_path.exists() or not stats_path.exists():
        return None
    try:
        median = _as_gray_u8(iio.imread(median_path)).astype(np.float32)
        mad_npy = model_dir / "mad.npy"
        if mad_npy.exists():
            mad = np.load(mad_npy).astype(np.float32)
        else:
            mad = _as_gray_u8(iio.imread(model_dir / "mad.png")).astype(np.float32)
        with stats_path.open("r", encoding="utf-8") as f:
            stats = json.load(f)
    except Exception:
        return None
    return PresenceV2Model(median=median, mad=mad, stats=stats)

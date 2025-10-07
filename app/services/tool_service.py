# app/services/tool_service.py
import json
from pathlib import Path
import imageio.v3 as iio
import numpy as np

from app.models.schema import RecipeDefinition
from app.services.compare_service import analyze

DEFAULT_THRESHOLDS = {
    "ssim_min": 0.92,
    "diff_thresh": 15,
    "min_blob_area": 20,
    "max_total_area": 2000,
    "max_blob_count": 10,
}

class ToolService:
    def __init__(self, base_dir="/data"):
        self.base = Path(base_dir)
        self.recipe = "default"
        self.golden = None            # np.ndarray uint8
        self.regions = None           # list[dict]
        self.thresholds = DEFAULT_THRESHOLDS.copy()
        self.pose_enabled = True

    def load_recipe(self, name: str):
        self.recipe = name
        rdir = self.base / "recipes" / name
        gfp = rdir / "golden.png"
        rfp = rdir / "regions.json"
        if not gfp.exists() or not rfp.exists():
            raise FileNotFoundError(f"Recept {name} nie je kompletný (chýba golden alebo regions.json)")

        g = iio.imread(gfp)
        if g.ndim == 3:
            g = g[:, :, 0]
        if g.dtype != np.uint8:
            # ak by bol 16-bit, znormalizuj na uint8
            g_f = g.astype(np.float32)
            g_max = float(g_f.max())
            if g_max > 0:
                g = (g_f * (255.0 / g_max)).astype(np.uint8)
            else:
                g = np.zeros_like(g_f, dtype=np.uint8)
        with open(rfp, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("regions.json nemá očakávanú štruktúru.")
        recipe = RecipeDefinition.from_dict(data)

        self.golden = g
        self.regions = [r.to_dict() for r in recipe.regions]
        self.pose_enabled = bool(recipe.pose_enabled)

        # voliteľne načítaj thresholds.json ak existuje
        tfp = rdir / "thresholds.json"
        if tfp.exists():
            with open(tfp, "r", encoding="utf-8") as f:
                th = json.load(f)
            self.thresholds.update(th)

    def save_thresholds(self):
        rdir = self.base / "recipes" / self.recipe
        rdir.mkdir(parents=True, exist_ok=True)
        with open(rdir / "thresholds.json", "w", encoding="utf-8") as f:
            json.dump(self.thresholds, f, ensure_ascii=False, indent=2)

    def evaluate(self, frame_u8):
        if self.golden is None or self.regions is None:
            raise RuntimeError("Recept nie je načítaný.")
        return analyze(
            self.golden,
            self.regions,
            frame_u8,
            self.thresholds,
            pose_enabled=self.pose_enabled,
        )

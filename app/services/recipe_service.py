# app/services/recipe_service.py
from pathlib import Path
import json
import imageio.v3 as iio
import numpy as np

from app.models.regions import Region
from app.models.schema import RecipeDefinition
from app.services.db_service import DbService
from app.services.tool_service import ToolService, DEFAULT_THRESHOLDS

class RecipeService:
    def __init__(self, base_dir="/data", db: DbService | None = None):
        self.base = Path(base_dir)
        self.db = db or DbService()
        self.tool = ToolService(base_dir=base_dir)
        self.tool.pose_enabled = True

    def list(self) -> list[str]:
        # DB je master
        return self.db.list_recipes()

    def load(self, name: str):
        # ensure v DB + load súborov
        rid = self.db.ensure_recipe(name)
        self.tool.load_recipe(name)
        # ak v DB chýbajú thresholds, doplň z toolu
        th = self.db.get_thresholds(rid)
        if not th:
            self.db.set_thresholds(rid, self.tool.thresholds)
        else:
            # sync do toolu (DB má prioritu)
            self.tool.thresholds.update(th)
            self.tool.save_thresholds()

    def create(self, name: str):
        rid = self.db.ensure_recipe(name)
        # inicializuj thresholds default
        self.db.set_thresholds(rid, DEFAULT_THRESHOLDS)
        # priprav priečinky
        p = self.base / "recipes" / name
        p.mkdir(parents=True, exist_ok=True)
        self.tool.pose_enabled = True
        # nechaj usera cez Wizard uložiť golden/regions
        return name

    def save_recipe_data(self, name: str, pose_enabled: bool, regions: list[dict | Region]):
        recipe_dir = self.base / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        regions_list = [r if isinstance(r, Region) else Region.from_dict(r) for r in regions]
        recipe = RecipeDefinition(pose_enabled=pose_enabled, regions=regions_list)
        with open(recipe_dir / "regions.json", "w", encoding="utf-8") as f:
            json.dump(recipe.to_dict(), f, ensure_ascii=False, indent=2)
        if getattr(self.tool, "recipe", None) == name:
            self.tool.pose_enabled = pose_enabled
            self.tool.regions = [r.to_dict() for r in regions_list]

    def rename(self, old: str, new: str):
        # premenuj v DB
        self.db.rename_recipe(old, new)
        # premenuj priečinok na FS (golden/regions)
        oldp = self.base / "recipes" / old
        newp = self.base / "recipes" / new
        if oldp.exists():
            oldp.rename(newp)

    def delete(self, name: str):
        # zmaž v DB (cascade thresholds, results)
        self.db.delete_recipe(name)
        # zmaž FS priečinok
        p = self.base / "recipes" / name
        if p.exists():
            import shutil
            shutil.rmtree(p, ignore_errors=True)

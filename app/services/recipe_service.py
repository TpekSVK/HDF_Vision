# app/services/recipe_service.py
from pathlib import Path
import json
from typing import Iterable, List, Sequence

from app.models.schema import RecipeData, RecipeV2, Tool
from app.services.db_service import DbService
from app.services.tool_service import ToolService, DEFAULT_THRESHOLDS
from app.services.storage_service import load_recipe_config, save_recipe_config

class RecipeService:
    def __init__(self, base_dir="/data", db: DbService | None = None):
        self.base = Path(base_dir)
        self.db = db or DbService(self.base / "HDF_Vision.db")
        self.tool = ToolService(base_dir=base_dir)
        self._draft_tools: dict[str, List[Tool]] = {}

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
        # create empty recipe.json with empty tools
        recipe = RecipeV2(pose_enabled=True, regions=[], tools=[])
        self._save_recipe_config(name, recipe)
        # nechaj usera cez Wizard uložiť golden/regions
        return name

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

    def save_regions(self, name: str, recipe: RecipeData):
        recipe_dir = self.base / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        with open(recipe_dir / "regions.json", "w", encoding="utf-8") as f:
            json.dump(recipe.to_dict(), f, ensure_ascii=False, indent=2)
        if getattr(self.tool, "recipe", None) == name:
            self.tool.regions = recipe.regions
            self.tool.pose_enabled = recipe.pose_enabled
        # sync to recipe.json structure
        recipe_v2 = self._load_recipe_config(name)
        recipe_v2.pose_enabled = recipe.pose_enabled
        recipe_v2.regions = list(recipe.regions)
        self._save_recipe_config(name, recipe_v2)

    # --- tools ---
    def load_tools(self, name: str, *, use_draft: bool = False) -> List[Tool]:
        if use_draft:
            return self.get_draft_tools(name)

        tools = self._get_persisted_tools(name)
        self._draft_tools[name] = [tool.copy() for tool in tools]
        return [tool.copy() for tool in tools]

    def save_tools(self, name: str, tools: Sequence[Tool | dict]) -> List[Tool]:
        recipe = self._load_recipe_config(name)
        recipe.tools = self._coerce_tools(tools)
        normalized = self._save_recipe_config(name, recipe)
        self._draft_tools[name] = [tool.copy() for tool in normalized.tools]
        return [tool.copy() for tool in normalized.tools]

    # --- draft tool management ---
    def get_draft_tools(self, name: str) -> List[Tool]:
        draft = self._ensure_draft_tools(name)
        return [tool.copy() for tool in draft]

    def add_tool(self, recipe_id: str, tool: Tool | dict) -> List[Tool]:
        draft = self._ensure_draft_tools(recipe_id)
        draft.append(Tool.from_dict(tool))
        self._normalize_draft_orders(draft)
        return [tool.copy() for tool in draft]

    def remove_tool(self, recipe_id: str, tool_index: int) -> List[Tool]:
        draft = self._ensure_draft_tools(recipe_id)
        if 0 <= tool_index < len(draft):
            del draft[tool_index]
            self._normalize_draft_orders(draft)
        return [tool.copy() for tool in draft]

    def reorder_tools(self, recipe_id: str, new_order: Sequence[int]) -> List[Tool]:
        draft = self._ensure_draft_tools(recipe_id)
        if len(new_order) != len(draft):
            raise ValueError("new_order must match number of tools")
        if sorted(new_order) != list(range(len(draft))):
            raise ValueError("new_order must be a permutation of current indices")
        reordered = [draft[idx] for idx in new_order]
        draft[:] = reordered
        self._normalize_draft_orders(draft)
        return [tool.copy() for tool in draft]

    # --- internals ---
    def _coerce_tools(self, tools: Sequence[Tool | dict]) -> List[Tool]:
        coerced: List[Tool] = []
        for tool in tools:
            if isinstance(tool, Tool):
                coerced.append(tool.copy())
            elif isinstance(tool, dict):
                coerced.append(Tool.from_dict(tool))
            else:
                raise TypeError(f"Unsupported tool entry: {type(tool)!r}")
        return coerced

    def _get_persisted_tools(self, name: str) -> List[Tool]:
        recipe = self._load_recipe_config(name)
        return [tool.copy() for tool in self._sort_tools(recipe.tools)]

    def _sort_tools(self, tools: Iterable[Tool]) -> List[Tool]:
        return [tool.copy() for tool in sorted(tools, key=lambda t: (t.order, t.name))]

    def _normalize_tools(self, tools: Iterable[Tool]) -> List[Tool]:
        sorted_tools = self._sort_tools(tools)
        return [tool.with_order(idx) for idx, tool in enumerate(sorted_tools)]

    def _ensure_draft_tools(self, name: str) -> List[Tool]:
        draft = self._draft_tools.get(name)
        if draft is None:
            draft = self._get_persisted_tools(name)
            self._draft_tools[name] = [tool.copy() for tool in draft]
            draft = self._draft_tools[name]
        return draft

    def _normalize_draft_orders(self, draft: List[Tool]) -> None:
        for idx, tool in enumerate(draft):
            tool.order = idx

    def _load_recipe_config(self, name: str) -> RecipeV2:
        recipe = load_recipe_config(name, base_dir=self.base)
        # ensure persisted if legacy fallback created
        self._ensure_recipe_file(name, recipe)
        return recipe

    def _save_recipe_config(self, name: str, recipe: RecipeV2) -> RecipeV2:
        recipe_copy = recipe.copy()
        recipe_copy.tools = self._normalize_tools(recipe_copy.tools)
        save_recipe_config(name, recipe_copy, base_dir=self.base)
        return recipe_copy

    def _ensure_recipe_file(self, name: str, recipe: RecipeV2) -> None:
        path = self.base / "recipes" / name / "recipe.json"
        if not path.exists():
            self._save_recipe_config(name, recipe)

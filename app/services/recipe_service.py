# app/services/recipe_service.py
from pathlib import Path
import json
from typing import Any, Iterable, List, Mapping, Sequence

import numpy as np

from app.models.schema import RecipeData, RecipeV2, Tool
from app.services.db_service import DbService
from app.services.tool_service import ToolService, DEFAULT_THRESHOLDS
from app.services.storage_service import (
    delete_multi_view_step_assets,
    load_multi_view_config,
    load_multi_view_step_assets,
    load_recipe_config,
    save_multi_view_config,
    save_multi_view_step_assets,
    save_recipe_config,
)

class RecipeService:
    def __init__(self, base_dir="/data", db: DbService | None = None):
        self.base = Path(base_dir)
        self.db = db or DbService(self.base / "HDF_Vision.db")
        self.tool = ToolService(base_dir=base_dir)
        self._draft_tools: dict[str, List[Tool]] = {}
        self._locator_autosort: dict[str, bool] = {}
        self._multi_view_cache: dict[str, dict[str, Any]] = {}

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
        self.db.mark_recipe_draft_updated(name)

    def get_locator_failure_policy(self, name: str) -> str:
        recipe = self._load_recipe_config(name)
        return getattr(recipe, "on_locator_failure", "continue_without_alignment")

    def set_locator_failure_policy(self, name: str, policy: str) -> str:
        normalized = "fail" if str(policy or "").strip().lower() == "fail" else "continue_without_alignment"
        recipe = self._load_recipe_config(name)
        recipe.on_locator_failure = normalized
        self._save_recipe_config(name, recipe)
        self.db.mark_recipe_draft_updated(name)
        return normalized

    # --- multi-view ---
    def load_multi_view_config(self, name: str) -> dict[str, Any]:
        config = load_multi_view_config(name, base_dir=self.base)
        self._multi_view_cache[name] = config
        return {"aggregation": config.get("aggregation", "AND"), "steps": list(config.get("steps", []))}

    def save_multi_view_config(self, name: str, data: Mapping[str, Any]) -> dict[str, Any]:
        normalized = save_multi_view_config(name, data, base_dir=self.base)
        self._multi_view_cache[name] = normalized
        self.db.mark_recipe_draft_updated(name)
        return {"aggregation": normalized.get("aggregation", "AND"), "steps": list(normalized.get("steps", []))}

    def has_multi_view(self, name: str) -> bool:
        cached = self._multi_view_cache.get(name)
        if cached is None:
            cached = load_multi_view_config(name, base_dir=self.base)
            self._multi_view_cache[name] = cached
        return bool(cached.get("steps"))

    def load_multi_view_step_assets(self, name: str, step_id: str) -> dict[str, Any]:
        return load_multi_view_step_assets(name, step_id, base_dir=self.base)

    def save_multi_view_step_assets(
        self,
        name: str,
        step_id: str,
        *,
        golden: np.ndarray | None = None,
        regions: Sequence[Mapping[str, Any]] | None = None,
        limits: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        assets = save_multi_view_step_assets(
            name,
            step_id,
            golden=golden,
            regions=regions,
            limits=limits,
            base_dir=self.base,
        )
        self.db.mark_recipe_draft_updated(name)
        return assets

    def delete_multi_view_step(self, name: str, step_id: str) -> None:
        delete_multi_view_step_assets(name, step_id, base_dir=self.base)
        config = load_multi_view_config(name, base_dir=self.base)
        filtered = [step for step in config.get("steps", []) if step.get("id") != step_id]
        config["steps"] = filtered
        save_multi_view_config(name, config, base_dir=self.base)
        self._multi_view_cache[name] = config
        self.db.mark_recipe_draft_updated(name)

    # --- tools ---
    def load_tools(self, name: str, *, use_draft: bool = True) -> List[Tool]:
        if use_draft:
            tools = self._get_persisted_tools(name)
            self._draft_tools[name] = [tool.copy() for tool in tools]
            return [tool.copy() for tool in tools]

        return [tool.copy() for tool in self.get_published_tools(name)]

    def save_tools(self, name: str, tools: Sequence[Tool | dict]) -> tuple[List[Tool], bool]:
        recipe = self._load_recipe_config(name)
        recipe.tools = self._coerce_tools(tools)
        normalized = self._save_recipe_config(name, recipe)
        autosorted = self._locator_autosort.pop(name, False)
        self._draft_tools[name] = [tool.copy() for tool in normalized.tools]
        self.db.mark_recipe_draft_updated(name)
        return [tool.copy() for tool in normalized.tools], autosorted

    def publish_recipe(self, name: str) -> tuple[List[Tool], bool]:
        recipe = self._load_recipe_config(name)
        publish_copy = recipe.copy()
        normalized_tools, autosorted = self._normalize_tools(publish_copy.tools)
        publish_copy.tools = normalized_tools
        self._save_published_recipe_config(name, publish_copy)
        self.db.mark_recipe_published(name)
        self._draft_tools[name] = [tool.copy() for tool in publish_copy.tools]
        return [tool.copy() for tool in publish_copy.tools], autosorted

    def get_published_tools(self, name: str) -> List[Tool]:
        recipe = self._load_published_recipe_config(name)
        return [tool.copy() for tool in self._sort_tools(recipe.tools)]

    def has_unpublished_changes(self, name: str) -> bool:
        state = self.db.recipe_publish_state(name)
        draft_ts = state.get("draft_updated_at")
        published_ts = state.get("published_at")
        if draft_ts is None:
            return False
        if published_ts is None:
            return True
        return str(draft_ts) > str(published_ts)

    def publish_state(self, name: str) -> dict[str, str | None | bool]:
        state = self.db.recipe_publish_state(name)
        return {
            "draft_updated_at": state.get("draft_updated_at"),
            "published_at": state.get("published_at"),
            "has_unpublished_changes": self.has_unpublished_changes(name),
        }

    def mark_draft_updated(self, name: str) -> None:
        self.db.mark_recipe_draft_updated(name)

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

    def update_tool(self, recipe_id: str, tool_index: int, tool: Tool | dict) -> List[Tool]:
        draft = self._ensure_draft_tools(recipe_id)
        if tool_index < 0 or tool_index >= len(draft):
            raise IndexError("tool_index out of range")
        draft[tool_index] = Tool.from_dict(tool)
        self._normalize_draft_orders(draft)
        return [entry.copy() for entry in draft]

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

    def _normalize_tools(self, tools: Iterable[Tool]) -> tuple[List[Tool], bool]:
        sorted_tools = self._sort_tools(tools)
        locators: List[Tool] = []
        analyzers: List[Tool] = []
        for tool in sorted_tools:
            (locators if tool.type.startswith("locator.") else analyzers).append(tool)

        enforced_order = locators + analyzers
        autosorted = enforced_order != sorted_tools
        normalized = [tool.with_order(idx) for idx, tool in enumerate(enforced_order)]
        return normalized, autosorted

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
        normalized_tools, autosorted = self._normalize_tools(recipe_copy.tools)
        recipe_copy.tools = normalized_tools
        self._locator_autosort[name] = autosorted
        save_recipe_config(name, recipe_copy, base_dir=self.base)
        return recipe_copy

    def _ensure_recipe_file(self, name: str, recipe: RecipeV2) -> None:
        path = self.base / "recipes" / name / "recipe.json"
        if not path.exists():
            self._save_recipe_config(name, recipe)

    def _published_recipe_path(self, name: str) -> Path:
        return self.base / "recipes" / name / "recipe.published.json"

    def _load_published_recipe_config(self, name: str) -> RecipeV2:
        path = self._published_recipe_path(name)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return RecipeV2.from_dict(data)
        return self._load_recipe_config(name)

    def _save_published_recipe_config(self, name: str, recipe: RecipeV2) -> None:
        path = self._published_recipe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recipe.to_dict(), f, ensure_ascii=False, indent=2)
